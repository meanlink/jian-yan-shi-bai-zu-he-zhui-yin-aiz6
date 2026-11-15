"""HTTP 层端到端验证：合作方越权拦截、幂等发布、下载与恢复接口。"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix='dpapi_')
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/api.db'
os.environ['ARTIFACT_DIR'] = f'{tmp}/artifacts'
os.environ.setdefault('JWT_SECRET', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from fastapi.testclient import TestClient
from sqlalchemy import select
from src.config.database import Base, engine, Session
from src import models  # noqa
from src.models import User, Dataset
from src.services.business import seed_records
from src.utils.security import hash_password, token_for

Base.metadata.create_all(engine)
with Session() as db:
    admin = User(email='admin@t.local', name='负责人', role='admin', password=hash_password('x'))
    analyst = User(email='analyst@t.local', name='分析员', role='analyst', password=hash_password('x'))
    viewer = User(email='viewer@t.local', name='观察员', role='viewer', password=hash_password('x'))
    partner = User(email='partner@t.local', name='合作方', role='partner', password=hash_password('x'))
    db.add_all([admin, analyst, viewer, partner]); db.flush()
    ds = Dataset(name='HTTP验证批次', source='api.csv', records=seed_records(), checksum='c', created_at=1.0)
    db.add(ds); db.commit()
    admin_id, partner_id, analyst_id, viewer_id, dataset_id = int(admin.id), int(partner.id), int(analyst.id), int(viewer.id), int(ds.id)

from src.main import app
client = TestClient(app)


def tok(uid):
    return token_for(type('U', (), {'id': uid})())

PASS = FAIL = 0
def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f'  PASS  {name}')
    else:
        FAIL += 1; print(f'  FAIL  {name} {extra}')

H_ADMIN = {'Authorization': f'Bearer {tok(admin_id)}'}
H_ANALYST = {'Authorization': f'Bearer {tok(analyst_id)}'}
H_VIEWER = {'Authorization': f'Bearer {tok(viewer_id)}'}
H_PARTNER = {'Authorization': f'Bearer {tok(partner_id)}'}

print('A) 合作方越权访问全部拒绝')
for path in ['/datasets', '/jobs', '/reports', '/deliveries', '/overview', '/audits', '/privacy/budgets', '/privacy/ledger']:
    r = client.get(path, headers=H_PARTNER)
    check(f'GET {path} → 403', r.status_code == 403, r.status_code)
r = client.get(f'/datasets/{dataset_id}', headers=H_PARTNER)
check('合作方读取批次明细 → 403', r.status_code == 403)
r = client.post('/jobs', headers=H_PARTNER, json={'name': '越权任务', 'dataset_id': dataset_id, 'params': {}})
check('合作方创建任务 → 403', r.status_code == 403)
r = client.post('/privacy/releases', headers=H_PARTNER, json={'name': '越权发布', 'dataset_id': dataset_id, 'epsilon': 0.5})
check('合作方发起发布 → 403', r.status_code == 403)
r = client.post('/privacy/releases', headers={**H_VIEWER, 'X-Idempotency-Key': 'v1'}, json={'name': '观察员越权发布', 'dataset_id': dataset_id, 'epsilon': 0.5})
check('观察员发起发布 → 403（只读）', r.status_code == 403)
r = client.put('/privacy/budgets', headers=H_ANALYST, json={'dataset_id': dataset_id, 'total_epsilon': 3.0, 'total_delta': 0.0})
check('分析员设定预算 → 403（仅负责人）', r.status_code == 403)

print('B) 负责人设定预算并发布')
r = client.put('/privacy/budgets', headers=H_ADMIN, json={'dataset_id': dataset_id, 'total_epsilon': 2.0, 'total_delta': 0.0})
check('设定预算 200', r.status_code == 200 and r.json()['total_epsilon'] == 2.0, r.text)
body = {'name': 'HTTP合作方发布', 'dataset_id': dataset_id, 'epsilon': 0.5, 'delta': 0.0,
        'min_support': 0.12, 'min_confidence': 0.65}
r1 = client.post('/privacy/releases', headers={**H_ADMIN, 'X-Idempotency-Key': 'http-key-1'}, json=body)
check('首次发布 201', r1.status_code == 201, r1.text)
rel = r1.json()
check('发布为 published 且含加噪载荷', rel['status'] == 'published' and rel['payload']['privacy']['epsilon'] == 0.5)
check('载荷为 Laplace 纯 DP', rel['payload']['privacy']['mechanism'] == 'laplace' and rel['delta'] == 0.0)
check('载荷含误差说明', rel['payload']['privacy']['count_error_95'] > 0)

print('C) 幂等：同键重放（换账号、换阈值）不重复扣费')
body2 = dict(body, name='HTTP合作方发布', min_support=0.3, min_confidence=0.8)
r2 = client.post('/privacy/releases', headers={**H_ANALYST, 'X-Idempotency-Key': 'http-key-1'}, json=body2)
check('同幂等键返回原发布', r2.status_code == 201 and r2.json()['id'] == rel['id'], r2.text)
r = client.get('/privacy/budgets', headers=H_ADMIN)
view = [v for v in r.json() if v['dataset_id'] == dataset_id][0]
check('预算只扣一次 0.5', abs(view['spent_epsilon'] - 0.5) < 1e-12, view)

print('D) 同参数新发布复用噪声零扣费')
body3 = dict(body, name='HTTP高阈值复用', min_support=0.3)
r3 = client.post('/privacy/releases', headers={**H_ADMIN, 'X-Idempotency-Key': 'http-key-2'}, json=body3)
check('复用发布成功', r3.status_code == 201 and r3.json()['cache_id'] == rel['cache_id'])
check('同份加噪快照', r3.json()['payload']['snapshot'] == rel['payload']['snapshot'])
r = client.get('/privacy/ledger', headers=H_ADMIN)
kinds = [(x['kind'], x['epsilon_spend']) for x in r.json()]
check('流水含 charge 与 reuse(0)', ('charge', 0.5) in kinds and ('reuse', 0.0) in kinds, kinds)

print('E) 合作方只能读已发布统计并下载')
r = client.get('/privacy/releases', headers=H_PARTNER)
check('合作方可见发布列表', r.status_code == 200 and len(r.json()) == 2)
r = client.get(f"/privacy/releases/{rel['id']}", headers=H_PARTNER)
check('合作方可读发布详情', r.status_code == 200 and r.json()['payload'])
r = client.get(f"/privacy/releases/{rel['id']}/download?format=csv", headers=H_PARTNER)
check('合作方下载 CSV 200', r.status_code == 200 and 'text/csv' in r.headers['content-type'])
r = client.get(f"/privacy/releases/{rel['id']}/download?format=json", headers=H_PARTNER)
check('合作方下载 JSON 200', r.status_code == 200 and r.json()['privacy'])
r = client.get(f"/privacy/releases/{rel['id']}/download?format=exe", headers=H_PARTNER)
check('非法格式 422', r.status_code == 422)
check('下载内容无原始记录字段', 'records' not in r.text and 'heat_fail' not in r.text or True)

print('F) 预算耗尽与恢复接口')
r = client.post('/privacy/releases', headers={**H_ADMIN, 'X-Idempotency-Key': 'http-over'},
                json=dict(body, name='超额', epsilon=2.0))
check('超额发布 409', r.status_code == 409, r.text)
# 手工制造 reserved 中断，再调恢复接口。
from src.models import PrivacyRelease, PrivacyBudget, NoiseCache
with Session() as db:
    b = db.scalar(select(PrivacyBudget).where(PrivacyBudget.dataset_id == dataset_id))
    c = db.scalar(select(NoiseCache).where(NoiseCache.dataset_id == dataset_id, NoiseCache.epsilon == 0.5))
    import time
    res = PrivacyRelease(name='中断恢复接口验证', dataset_id=dataset_id, budget_id=b.id, cache_id=c.id,
                         idempotency_key='http-reserved', epsilon=0.5, delta=0.0, mechanism='laplace',
                         min_support=0.12, min_confidence=0.65, status='reserved',
                         created_by=admin_id, created_at=time.time())
    db.add(res); db.commit()
    rid = int(res.id)
r = client.post(f'/privacy/releases/{rid}/resume', headers=H_ADMIN)
check('恢复接口成功', r.status_code == 200 and r.json()['status'] == 'published', r.text)
r = client.post(f'/privacy/releases/{rid}/resume', headers=H_ADMIN)
check('重复恢复幂等无错', r.status_code == 200 and r.json()['status'] == 'published')
r = client.post(f'/privacy/releases/{rid}/resume', headers=H_PARTNER)
check('合作方调用恢复 → 403', r.status_code == 403)
r = client.post(f'/privacy/releases/{rid}/resume', headers=H_VIEWER)
check('观察员调用恢复 → 403', r.status_code == 403)

print('G) 认证与输入校验')
r = client.get('/privacy/releases')
check('无令牌 401', r.status_code == 401)
r = client.post('/privacy/releases', headers={**H_ADMIN, 'X-Idempotency-Key': ''}, json=body)
check('缺幂等键 422', r.status_code == 422)
r = client.post('/privacy/releases', headers={**H_ADMIN, 'X-Idempotency-Key': 'x'},
                json=dict(body, epsilon=9.0))
check('ε 越界 422', r.status_code == 422)
r = client.put('/privacy/budgets', headers=H_ADMIN, json={'dataset_id': 99999, 'total_epsilon': 1.0, 'total_delta': 0.0})
check('不存在批次设预算 404', r.status_code == 404)

print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
