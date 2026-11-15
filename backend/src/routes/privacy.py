import os
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse
from sqlalchemy import select
from src.config.database import get_db
from src.config.settings import ARTIFACT_DIR
from src.models import PrivacyRelease, Dataset
from src.schemas.privacy import BudgetInput, ReleaseInput
from src.middlewares.auth import current_user, admin_only, internal_only, can_write
from src.services import privacy, dp
from src.services.audit import record

router = APIRouter(prefix='/privacy')


def release_dict(r, include_payload=True):
    data = {
        'id': r.id, 'name': r.name, 'dataset_id': r.dataset_id, 'cache_id': r.cache_id,
        'epsilon': r.epsilon, 'delta': r.delta, 'mechanism': r.mechanism,
        'min_support': r.min_support, 'min_confidence': r.min_confidence,
        'status': r.status, 'created_at': r.created_at, 'published_at': r.published_at,
        'idempotency_key': r.idempotency_key, 'error': r.error,
    }
    if include_payload:
        data['payload'] = r.payload
    return data


@router.get('/budgets')
def budgets(dataset_id: int | None = None, db=Depends(get_db), u=Depends(internal_only)):
    if dataset_id is not None:
        view = privacy.budget_view(db, dataset_id)
        return [view] if view else []
    rows = []
    for d in db.scalars(select(Dataset).order_by(Dataset.id)):
        view = privacy.budget_view(db, d.id)
        if view:
            rows.append(view)
    return rows


@router.put('/budgets', status_code=200)
def set_budget(data: BudgetInput, db=Depends(get_db), u=Depends(admin_only)):
    row, created = privacy.get_or_set_budget(db, data.dataset_id, data.total_epsilon, data.total_delta, u)
    record(db, u.email, 'privacy.budget.set', {
        'dataset_id': data.dataset_id, 'total_epsilon': row.total_epsilon,
        'total_delta': row.total_delta, 'created': created,
    })
    db.commit()
    return privacy.budget_view(db, data.dataset_id)


@router.get('/releases')
def releases(dataset_id: int | None = None, db=Depends(get_db), u=Depends(current_user)):
    stmt = select(PrivacyRelease).where(PrivacyRelease.status == 'published').order_by(PrivacyRelease.id.desc())
    if dataset_id is not None:
        stmt = stmt.where(PrivacyRelease.dataset_id == dataset_id)
    if u.role != 'partner':
        # 内部账号还能看到 reserved/failed，便于中断恢复与排障。
        stmt = select(PrivacyRelease).order_by(PrivacyRelease.id.desc())
        if dataset_id is not None:
            stmt = stmt.where(PrivacyRelease.dataset_id == dataset_id)
    return [release_dict(r) for r in db.scalars(stmt.limit(200))]


@router.post('/releases', status_code=201)
def publish(data: ReleaseInput, db=Depends(get_db), u=Depends(can_write),
            idempotency_key: str = Header(default='', alias='X-Idempotency-Key')):
    try:
        release, created = privacy.create_release(db, data, u, idempotency_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    record(db, u.email, 'privacy.release.publish' if created else 'privacy.release.reuse', {
        'id': release.id, 'epsilon': release.epsilon, 'delta': release.delta,
        'cache_id': release.cache_id, 'idempotency_key': release.idempotency_key,
    })
    db.commit()
    return release_dict(release)


@router.post('/releases/{release_id}/resume')
def resume(release_id: int, db=Depends(get_db), u=Depends(can_write)):
    r = privacy.resume_release(db, release_id, u)
    record(db, u.email, 'privacy.release.resume', {'id': r.id})
    db.commit()
    return release_dict(r)


@router.get('/releases/{release_id}')
def release_detail(release_id: int, db=Depends(get_db), u=Depends(current_user)):
    r = db.get(PrivacyRelease, release_id)
    if not r:
        raise HTTPException(404, '发布不存在')
    if u.role == 'partner' and r.status != 'published':
        raise HTTPException(404, '发布不存在')
    return release_dict(r)


@router.get('/releases/{release_id}/download')
def download(release_id: int, format: str = 'csv', db=Depends(get_db), u=Depends(current_user)):
    r = db.get(PrivacyRelease, release_id)
    if not r or r.status != 'published':
        raise HTTPException(404, '已发布统计不存在')
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    csv_path, json_path = privacy.artifact_paths(r)
    path = json_path if format == 'json' else csv_path
    if not os.path.exists(path):
        raise HTTPException(404, '发布文件不存在，可在发布页执行恢复')
    filename = os.path.basename(path)
    return FileResponse(
        path, filename=filename,
        media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8',
    )


@router.get('/ledger')
def ledger(dataset_id: int | None = None, db=Depends(get_db), u=Depends(internal_only)):
    return privacy.ledger(db, dataset_id)


@router.get('/mechanisms')
def mechanisms(u=Depends(current_user)):
    return {
        'mechanisms': [
            {'key': 'laplace', 'label': 'Laplace · 纯 ε-DP（δ=0）', 'delta': 0.0},
            {'key': 'gaussian', 'label': 'Gaussian · (ε,δ)-近似 DP', 'delta_range': [1e-6, 1e-3]},
        ],
        'epsilon_per_release': [0.05, 2.0],
        'thresholds': {'min_support': [0.05, 0.5], 'min_confidence': [0.2, 0.95]},
        'error_note': '误差幅度随 ε 减小而增大；页面按 95% 置信度给出单计数误差边界。',
    }
