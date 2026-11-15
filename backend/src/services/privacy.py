"""差分隐私发布的持久化编排：预算锁定、幂等发布、断点恢复、产物落盘。"""
import csv
import io
import json
import os
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import select, func

from src.config.settings import ARTIFACT_DIR
from src.models import (
    Dataset, PrivacyBudget, NoiseCache, PrivacyRelease, PrivacyLedger,
)
from src.services import dp

EPSILON_TOTAL_MAX = 10.0
DELTA_TOTAL_MAX = 1e-2


def _budget_row(db, dataset_id, lock=False):
    stmt = select(PrivacyBudget).where(PrivacyBudget.dataset_id == dataset_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def get_or_set_budget(db, dataset_id, total_epsilon, total_delta, user):
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据批次不存在")
    if total_epsilon is not None:
        if not (0 < float(total_epsilon) <= EPSILON_TOTAL_MAX):
            raise HTTPException(422, f"批次总 ε 须在 0~{EPSILON_TOTAL_MAX:g} 之间")
        total_epsilon = float(total_epsilon)
    if total_delta is not None:
        if not (0 <= float(total_delta) <= DELTA_TOTAL_MAX):
            raise HTTPException(422, f"批次总 δ 须在 0~{DELTA_TOTAL_MAX:g} 之间")
        total_delta = float(total_delta)
    row = _budget_row(db, dataset_id)
    now = time.time()
    if row is None:
        if total_epsilon is None or total_delta is None:
            raise HTTPException(409, "该批次尚未设置隐私预算，请先由负责人设定总额")
        row = PrivacyBudget(
            dataset_id=dataset_id, total_epsilon=total_epsilon, total_delta=total_delta,
            created_by=user.id, created_at=now, updated_at=now,
        )
        db.add(row)
        db.flush()
        return row, True
    if total_epsilon is not None or total_delta is not None:
        spent_epsilon, spent_delta = spend_totals(db, row.id)
        new_epsilon = row.total_epsilon if total_epsilon is None else total_epsilon
        new_delta = row.total_delta if total_delta is None else total_delta
        if new_epsilon < spent_epsilon - 1e-12 or new_delta < spent_delta - 1e-12:
            raise HTTPException(409, "新总额不能低于已消耗预算，请先核对预算流水")
        row.total_epsilon = new_epsilon
        row.total_delta = new_delta
        row.updated_at = now
    return row, False


def spend_totals(db, budget_id):
    epsilon = db.scalar(
        select(func.coalesce(func.sum(PrivacyLedger.epsilon_spend), 0.0)).where(PrivacyLedger.budget_id == budget_id)
    )
    delta = db.scalar(
        select(func.coalesce(func.sum(PrivacyLedger.delta_spend), 0.0)).where(PrivacyLedger.budget_id == budget_id)
    )
    return float(epsilon), float(delta)


def budget_view(db, dataset_id):
    row = _budget_row(db, dataset_id)
    if row is None:
        return None
    spent_epsilon, spent_delta = spend_totals(db, row.id)
    return {
        "dataset_id": dataset_id,
        "total_epsilon": row.total_epsilon,
        "total_delta": row.total_delta,
        "spent_epsilon": round(spent_epsilon, 10),
        "spent_delta": round(spent_delta, 12),
        "remaining_epsilon": round(row.total_epsilon - spent_epsilon, 10),
        "remaining_delta": round(row.total_delta - spent_delta, 12),
        "updated_at": row.updated_at,
    }


def _find_cache(db, dataset_id, epsilon, delta):
    return db.scalar(
        select(NoiseCache).where(
            NoiseCache.dataset_id == dataset_id,
            NoiseCache.epsilon == epsilon,
            NoiseCache.delta == delta,
        )
    )


def _write_artifact(release, payload):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    csv_name = f"dp-release-{release.id}.csv"
    json_name = f"dp-release-{release.id}.json"
    out = io.StringIO()
    out.write("﻿")
    table = payload["table"]
    writer = csv.writer(out)

    def safe(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        text = str(v)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text

    writer.writerow([c["label"] for c in table["columns"]])
    for row in table["rows"]:
        writer.writerow([safe(row.get(c["key"], "")) for c in table["columns"]])
    with open(os.path.join(ARTIFACT_DIR, csv_name), "w", encoding="utf-8", newline="") as f:
        f.write(out.getvalue())
    with open(os.path.join(ARTIFACT_DIR, json_name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=False)
    release.artifact_filename = csv_name
    return csv_name, json_name


def artifact_paths(release):
    base = release.artifact_filename or f"dp-release-{release.id}.csv"
    csv_path = os.path.join(ARTIFACT_DIR, base)
    json_path = os.path.join(ARTIFACT_DIR, base.replace(".csv", ".json"))
    return csv_path, json_path


def _publish_reserved(db, release):
    """把 reserved 发布推进到 published；同事务写文件，扣费由调用方在同事务补写。

    本函数不写预算流水，保证重试/恢复时流水只在 create_release/_resume_reserved 落一次。
    """
    dataset = db.get(Dataset, release.dataset_id)
    cache = db.get(NoiseCache, release.cache_id)

    true_count_map = dp.true_counts(dataset.records)
    payload = dp.build_release_payload(
        name=release.name, dataset=dataset, total_true=len(dataset.records),
        epsilon=release.epsilon, delta=release.delta, mechanism=release.mechanism,
        min_support=release.min_support, min_confidence=release.min_confidence,
        snapshot=dp.build_noisy_snapshot(true_count_map, cache.noises),
        cache_id=cache.id, created_at=release.created_at,
    )
    release.payload = payload
    _write_artifact(release, payload)
    release.status = "published"
    release.published_at = time.time()
    release.error = ""
    return payload


def create_release(db, data, user, idempotency_key):
    """幂等发布主流程：重试同键直接返回原发布，绝不重复扣费或重新取噪。"""
    idem = (idempotency_key or "").strip()
    if not idem:
        raise HTTPException(422, "缺少幂等键，无法保证重试不重复扣费")
    if len(idem) > 64:
        raise HTTPException(422, "幂等键长度不能超过 64")

    existing = db.scalar(select(PrivacyRelease).where(PrivacyRelease.idempotency_key == idem))
    if existing is not None:
        if existing.status == "published":
            return existing, False
        if existing.status == "failed":
            raise HTTPException(409, "该幂等键对应发布已失败，请更换请求后重新发布")
        # reserved：中断恢复，沿用原预算预留与原噪声，不新增任何扣减。
        _resume_reserved(db, existing, user)
        return existing, False

    epsilon, delta = dp.validate_privacy_params(data.epsilon, data.delta)
    min_support, min_confidence = dp.validate_thresholds(data.min_support, data.min_confidence)
    mechanism = dp.mechanism_for(delta)

    # 行锁串行化同批次并发发布，保证总额不超支。
    budget = _budget_row(db, data.dataset_id, lock=True)
    if budget is None:
        raise HTTPException(409, "该批次尚未设置隐私预算，请先由负责人设定总额")
    spent_epsilon, spent_delta = spend_totals(db, budget.id)
    if spent_epsilon + epsilon > budget.total_epsilon + 1e-12 or \
       spent_delta + delta > budget.total_delta + 1e-12:
        raise HTTPException(
            409,
            f"预算不足：本次需要 ε={epsilon:g}/δ={delta:g}，"
            f"剩余 ε={budget.total_epsilon - spent_epsilon:g}/δ={budget.total_delta - spent_delta:g}",
        )

    cache = _find_cache(db, data.dataset_id, epsilon, delta)
    reused = cache is not None
    if not reused:
        salt = secrets.token_hex(8)
        seed, noises = dp.draw_noises(data.dataset_id, epsilon, delta, mechanism, salt)
        cache = NoiseCache(
            dataset_id=data.dataset_id, epsilon=epsilon, delta=delta, mechanism=mechanism,
            salt=salt, rng_seed=seed, noises=noises, created_by=user.id, created_at=time.time(),
        )
        db.add(cache)
        db.flush()

    now = time.time()
    release = PrivacyRelease(
        name=data.name.strip(), dataset_id=data.dataset_id, budget_id=budget.id,
        cache_id=cache.id, idempotency_key=idem, epsilon=epsilon, delta=delta,
        mechanism=mechanism, min_support=min_support, min_confidence=min_confidence,
        status="reserved", created_by=user.id, created_at=now,
    )
    db.add(release)
    db.flush()

    try:
        _publish_reserved(db, release)
        # 写流水（charge 与 reuse 区分；复用缓存零扣费）。
        db.add(PrivacyLedger(
            budget_id=budget.id, release_id=release.id, cache_id=cache.id,
            kind="reuse" if reused else "charge",
            epsilon_spend=0.0 if reused else epsilon,
            delta_spend=0.0 if reused else delta,
            actor=user.email,
            note=("复用同批次同参数已存噪声，零额外扣费" if reused else "差分隐私发布扣费"),
            created_at=time.time(),
        ))
    except Exception as exc:
        release.status = "failed"
        release.error = str(exc)[:990]
        raise
    return release, True


def _resume_reserved(db, release, user):
    """恢复中断的 reserved 发布：不重新取噪；按该噪声是否已被付过费决定补记口径。"""
    existing_ledger = db.scalar(
        select(func.count()).select_from(PrivacyLedger).where(PrivacyLedger.release_id == release.id)
    )
    _publish_reserved(db, release)
    if not existing_ledger:
        # 该噪声缓存若已被其它发布扣过费，本次恢复零扣费；否则补记一次全额扣费。
        prior_charge = db.scalar(
            select(func.count()).select_from(PrivacyLedger).where(
                PrivacyLedger.cache_id == release.cache_id,
                PrivacyLedger.kind == "charge",
            )
        )
        reused = bool(prior_charge)
        db.add(PrivacyLedger(
            budget_id=release.budget_id, cache_id=release.cache_id, release_id=release.id,
            kind="reuse" if reused else "charge",
            epsilon_spend=0.0 if reused else release.epsilon,
            delta_spend=0.0 if reused else release.delta,
            actor=user.email,
            note=("中断恢复：噪声已扣费，零额外扣减" if reused else "中断恢复后补记扣费（仅一次）"),
            created_at=time.time(),
        ))


def resume_release(db, release_id, user):
    release = db.get(PrivacyRelease, release_id)
    if release is None:
        raise HTTPException(404, "发布不存在")
    if release.status == "published":
        return release
    if release.status == "failed":
        raise HTTPException(409, "失败发布不可恢复，请重新发起")
    _budget_row(db, release.dataset_id, lock=True)
    _resume_reserved(db, release, user)
    return release


def recover_reserved(db):
    """进程启动时把遗留的 reserved 发布恢复，避免中断永久占位。"""
    recovered = 0
    rows = list(db.scalars(select(PrivacyRelease).where(PrivacyRelease.status == "reserved")))
    for release in rows:
        _resume_reserved(db, release, _SimpleActor())
        recovered += 1
    if recovered:
        db.commit()
    return recovered


class _SimpleActor:
    email = "system.recovery"


def ledger(db, dataset_id=None):
    stmt = select(PrivacyLedger).order_by(PrivacyLedger.id.desc()).limit(200)
    if dataset_id is not None:
        budgets = list(db.scalars(select(PrivacyBudget).where(PrivacyBudget.dataset_id == dataset_id)))
        ids = [b.id for b in budgets]
        if not ids:
            return []
        stmt = stmt.where(PrivacyLedger.budget_id.in_(ids))
    return [
        {
            "id": row.id, "budget_id": row.budget_id, "release_id": row.release_id,
            "cache_id": row.cache_id, "kind": row.kind,
            "epsilon_spend": row.epsilon_spend, "delta_spend": row.delta_spend,
            "actor": row.actor, "note": row.note, "created_at": row.created_at,
        }
        for row in db.scalars(stmt)
    ]
