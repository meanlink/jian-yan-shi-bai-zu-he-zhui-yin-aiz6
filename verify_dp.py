"""差分隐私发布的独立验证脚本（不依赖 Docker/MySQL/Redis）。

运行：python3 verify_dp.py
覆盖：
1. 噪声确定性：同批次同参数同盐值生成完全相同噪声；不同盐值不同。
2. 复用不重新取噪：换账号、换阈值均命中同一 NoiseCache。
3. 预算扣减与并发不超支：行锁 + 余额校验，超额 409。
4. 幂等重试：同幂等键重试不重复扣费、不另取噪。
5. 中断恢复：reserved 发布可恢复，流水只记一次。
6. 载荷不含真实计数；阈值筛选与指标全部来自加噪快照。
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix='dpverify_')
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['ARTIFACT_DIR'] = f'{tmp}/artifacts'
os.environ.setdefault('JWT_SECRET', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy import select, func
from src.config.database import Base, engine, Session
from src import models  # noqa: F401 注册模型
from src.models import User, Dataset, PrivacyBudget, NoiseCache, PrivacyRelease, PrivacyLedger
from src.services import dp, privacy
from src.services.business import seed_records
from src.utils.security import hash_password
from src.schemas.privacy import BudgetInput, ReleaseInput
import json


def json_safe(obj):
    return json.loads(json.dumps(obj, default=str, ensure_ascii=False))

Base.metadata.create_all(engine)

PASS, FAIL = 0, 0
def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name} {extra}')

with Session() as db:
    admin = User(email='admin@t.local', name='负责人', role='admin', password=hash_password('x'))
    other = User(email='analyst@t.local', name='分析员', role='analyst', password=hash_password('x'))
    db.add_all([admin, other]); db.flush()
    rows = seed_records()
    ds = Dataset(name='验证批次', source='verify.csv', records=rows, checksum='c', created_at=1.0)
    db.add(ds); db.flush()
    dataset_id = int(ds.id)
    admin_id = int(admin.id)
    other_id = int(other.id)
    db.commit()

print('1) 噪声确定性与机制选择')
with Session() as db:
    eps, delta = dp.validate_privacy_params(0.5, 0.0)
    check('delta=0 选择 Laplace', dp.mechanism_for(0.0) == 'laplace')
    eps2, delta2 = dp.validate_privacy_params(0.5, 1e-5)
    check('delta>0 选择 Gaussian', dp.mechanism_for(delta2) == 'gaussian')
    s1, n1 = dp.draw_noises(dataset_id, 0.5, 0.0, 'laplace', 'salt-a')
    s2, n2 = dp.draw_noises(dataset_id, 0.5, 0.0, 'laplace', 'salt-a')
    check('同盐值种子与噪声完全一致', s1 == s2 and n1 == n2)
    s3, n3 = dp.draw_noises(dataset_id, 0.5, 0.0, 'laplace', 'salt-b')
    check('不同盐值噪声不同', n1 != n3 and s1 != s3)
    s4, n4 = dp.draw_noises(dataset_id, 0.8, 0.0, 'laplace', 'salt-a')
    check('不同 ε 噪声不同', n1 != n4)
    check('噪声键覆盖总数+全部1~3项集', len(n1) == len(dp.stat_keys()) and '__total__' in n1)
    # 统计分布健全性
    import statistics
    vals = list(n1.values())
    check('Laplace 噪声均值接近 0', abs(statistics.mean(vals)) < 0.5)
    try:
        dp.validate_privacy_params(0.01, 0.0)
        check('ε 越界被拒绝', False)
    except ValueError:
        check('ε 越界被拒绝', True)
    try:
        dp.validate_privacy_params(0.5, 5e-2)
        check('δ 非法区间被拒绝', False)
    except ValueError:
        check('δ 非法区间被拒绝', True)

print('2) 设定预算')
with Session() as db:
    u = db.get(User, admin_id)
    d_id = dataset_id
    privacy.get_or_set_budget(db, d_id, 4.0, 1e-4, u)
    db.commit()
    view = privacy.budget_view(db, d_id)
    check('初始预算全额可用', view['spent_epsilon'] == 0.0 and view['total_epsilon'] == 4.0)
    try:
        privacy.get_or_set_budget(db, d_id, 0.0, 1e-4, u)
        check('总额须为正数', False)
    except Exception as e:
        check('总额须为正数', '422' in repr(e))
        db.rollback()
    # 独立小预算批次，专供超额与并发余额校验。
    ds2 = Dataset(name='超额验证批次', source='verify2.csv', records=rows, checksum='c2', created_at=2.0)
    db.add(ds2); db.flush()
    small_id = int(ds2.id)
    privacy.get_or_set_budget(db, small_id, 0.3, 0.0, u)
    db.commit()
    check('小预算批次建立', privacy.budget_view(db, small_id)['total_epsilon'] == 0.3)

print('3) 首次发布：取噪、扣费、产物')
with Session() as db:
    u = db.get(User, admin_id)
    data = ReleaseInput(name='发布A', dataset_id=dataset_id, epsilon=0.5, delta=0.0,
                        min_support=0.12, min_confidence=0.65)
    r1, created = privacy.create_release(db, data, u, 'key-0001')
    db.commit()
    check('首次发布为新建', created and r1.status == 'published')
    check('缓存行数=1', db.scalar(select(func.count()).select_from(NoiseCache)) == 1)
    ledger_rows = db.scalars(select(PrivacyLedger)).all()
    check('流水扣费 ε=0.5', len(ledger_rows) == 1 and abs(ledger_rows[0].epsilon_spend - 0.5) < 1e-12)
    check('CSV/JSON 产物落盘', all(os.path.exists(p) for p in privacy.artifact_paths(r1)))
    payload = r1.payload
    check('载荷不含原始 records', 'records' not in json_safe(payload))
    check('载荷快照不含真实计数', all('true_count' not in v for v in payload['snapshot'].values()))
    check('阈值写入载荷', payload['thresholds'] == {'min_support': 0.12, 'min_confidence': 0.65})
    check('规则计数与加噪口径一致', len(payload['table']['rows']) == payload['metrics'][3]['value'])
    cache_id = r1.cache_id

print('4) 幂等重试：同键不重复扣费、不另取噪')
with Session() as db:
    u = db.get(User, other_id)  # 换账号
    data = ReleaseInput(name='发布A', dataset_id=dataset_id, epsilon=0.5, delta=0.0,
                        min_support=0.3, min_confidence=0.8)  # 同时换阈值
    r2, created = privacy.create_release(db, data, u, 'key-0001')
    db.commit()
    check('同幂等键返回原发布', not created and r2.id == r1.id)
    check('仍只有 1 个缓存', db.scalar(select(func.count()).select_from(NoiseCache)) == 1)
    check('仍只有 1 条流水', db.scalar(select(func.count()).select_from(PrivacyLedger)) == 1)
    view = privacy.budget_view(db, dataset_id)
    check('累计消耗仍为 0.5', abs(view['spent_epsilon'] - 0.5) < 1e-12)

print('5) 同批次同参数换阈值的新发布：复用噪声零扣费')
with Session() as db:
    u = db.get(User, other_id)
    data = ReleaseInput(name='发布B · 高阈值', dataset_id=dataset_id, epsilon=0.5, delta=0.0,
                        min_support=0.3, min_confidence=0.8)
    r3, created = privacy.create_release(db, data, u, 'key-0002')
    db.commit()
    check('新发布建立', created and r3.status == 'published')
    check('复用同一缓存', r3.cache_id == cache_id)
    check('同份噪声：快照完全相同', r3.payload['snapshot'] == r1.payload['snapshot'])
    check('阈值更高时规则数不增', len(r3.payload['table']['rows']) <= len(r1.payload['table']['rows']))
    ledger_rows = db.scalars(select(PrivacyLedger).order_by(PrivacyLedger.id)).all()
    check('新增 reuse 零扣费流水', ledger_rows[-1].kind == 'reuse' and ledger_rows[-1].epsilon_spend == 0.0)
    view = privacy.budget_view(db, dataset_id)
    check('累计消耗仍为 0.5（复用不扣费）', abs(view['spent_epsilon'] - 0.5) < 1e-12)

print('6) 新 ε 发布：重新取噪并扣费')
with Session() as db:
    u = db.get(User, admin_id)
    data = ReleaseInput(name='发布C', dataset_id=dataset_id, epsilon=1.0, delta=1e-5,
                        min_support=0.12, min_confidence=0.65)
    r4, created = privacy.create_release(db, data, u, 'key-0003')
    db.commit()
    check('Gaussian 发布成功', created and r4.mechanism == 'gaussian')
    check('生成第二个缓存', db.scalar(select(func.count()).select_from(NoiseCache)) == 2)
    view = privacy.budget_view(db, dataset_id)
    check('累计 ε=1.5、δ=1e-5', abs(view['spent_epsilon'] - 1.5) < 1e-12 and abs(view['spent_delta'] - 1e-5) < 1e-15)

print('7) 超额不超支（小预算批次）')
with Session() as db:
    u = db.get(User, admin_id)
    # 先消耗 0.2，剩余 0.1。
    ok = ReleaseInput(name='小预算首发布', dataset_id=small_id, epsilon=0.2, delta=0.0,
                      min_support=0.12, min_confidence=0.65)
    r_ok, _ = privacy.create_release(db, ok, u, 'small-key-1')
    db.commit()
    check('小预算首发布成功', r_ok.status == 'published')
with Session() as db:
    u = db.get(User, admin_id)
    big = ReleaseInput(name='小预算超额发布', dataset_id=small_id, epsilon=0.2, delta=0.0,
                       min_support=0.12, min_confidence=0.65)
    try:
        privacy.create_release(db, big, u, 'small-key-over')
        check('超额发布被 409 拒绝', False)
    except Exception as e:
        check('超额发布被 409 拒绝', '409' in repr(e))
        db.rollback()
    view = privacy.budget_view(db, small_id)
    check('拒绝后余额不变（仍耗 0.2）', abs(view['spent_epsilon'] - 0.2) < 1e-12)
    check('拒绝后无残留发布', db.scalar(select(func.count()).select_from(PrivacyRelease).where(PrivacyRelease.idempotency_key == 'small-key-over')) == 0)
    check('拒绝后无残留缓存', db.scalar(select(func.count()).select_from(NoiseCache).where(NoiseCache.dataset_id == small_id)) == 1)
    # 已消耗之后把总额下调到消耗线之下，必须被拒绝。
    try:
        privacy.get_or_set_budget(db, small_id, 0.1, 0.0, u)
        check('总额低于已消耗被 409 拒绝', False)
    except Exception as e:
        check('总额低于已消耗被 409 拒绝', '409' in repr(e))
        db.rollback()
with Session() as db:
    # 行锁路径本身在 MySQL 上为 SELECT ... FOR UPDATE；sqlite 退化为普通查询，
    # 但余额校验在同一事务内完成，顺序并发请求同样无法越过剩余额度。
    u = db.get(User, admin_id)
    exact = ReleaseInput(name='恰好耗尽预算', dataset_id=small_id, epsilon=0.1, delta=0.0,
                         min_support=0.12, min_confidence=0.65)
    r_exact, _ = privacy.create_release(db, exact, u, 'small-key-2')
    db.commit()
    check('恰好等于剩余额度可以发布', r_exact.status == 'published')
    view = privacy.budget_view(db, small_id)
    check('预算恰好耗尽、余额为 0', abs(view['remaining_epsilon']) < 1e-12)

print('8) 中断恢复：reserved 可恢复且只扣一次（覆盖新噪声与已付费噪声两条路径）')
import time as _t
with Session() as db:
    u = db.get(User, admin_id)
    budget_row = db.scalar(select(PrivacyBudget).where(PrivacyBudget.dataset_id == dataset_id))
    # 8a 全新 ε=0.8 的缓存（从未被任何发布付费），恢复时应补记全额 charge。
    seed_new, noises_new = dp.draw_noises(dataset_id, 0.8, 0.0, 'laplace', 'resume-salt-new')
    cache_new = NoiseCache(dataset_id=dataset_id, epsilon=0.8, delta=0.0, mechanism='laplace',
                           salt='resume-salt-new', rng_seed=seed_new, noises=noises_new,
                           created_by=u.id, created_at=_t.time())
    db.add(cache_new); db.flush()
    res_new = PrivacyRelease(name='中断发布·新噪声', dataset_id=dataset_id, budget_id=budget_row.id,
                             cache_id=cache_new.id, idempotency_key='key-reserved-new', epsilon=0.8, delta=0.0,
                             mechanism='laplace', min_support=0.12, min_confidence=0.65,
                             status='reserved', created_by=u.id, created_at=_t.time())
    # 8b 复用已被发布 A 付过费的缓存（cache_id, ε=0.5），恢复时应零扣费 reuse。
    res_paid = PrivacyRelease(name='中断发布·已付费噪声', dataset_id=dataset_id, budget_id=budget_row.id,
                              cache_id=cache_id, idempotency_key='key-reserved-paid', epsilon=0.5, delta=0.0,
                              mechanism='laplace', min_support=0.12, min_confidence=0.65,
                              status='reserved', created_by=u.id, created_at=_t.time())
    db.add_all([res_new, res_paid]); db.commit()
    rid_new, rid_paid = res_new.id, res_paid.id
with Session() as db:
    recovered = privacy.recover_reserved(db)
    check('启动恢复处理了 2 条 reserved', recovered == 2)
with Session() as db:
    res_new = db.get(PrivacyRelease, rid_new)
    res_paid = db.get(PrivacyRelease, rid_paid)
    check('两个恢复后均为 published', res_new.status == 'published' and res_paid.status == 'published')
    led_new = db.scalars(select(PrivacyLedger).where(PrivacyLedger.release_id == rid_new)).all()
    led_paid = db.scalars(select(PrivacyLedger).where(PrivacyLedger.release_id == rid_paid)).all()
    check('新噪声恢复补记 1 条全额 charge(0.8)', len(led_new) == 1 and led_new[0].kind == 'charge' and abs(led_new[0].epsilon_spend - 0.8) < 1e-12)
    check('已付费噪声恢复仅记 1 条零扣费 reuse', len(led_paid) == 1 and led_paid[0].kind == 'reuse' and led_paid[0].epsilon_spend == 0.0)
    # 再恢复一次（幂等）
    n2 = privacy.recover_reserved(db)
    check('无遗留 reserved 时恢复为 0', n2 == 0)
    check('重复恢复不产生第二条流水',
          db.scalar(select(func.count()).select_from(PrivacyLedger).where(PrivacyLedger.release_id == rid_new)) == 1
          and db.scalar(select(func.count()).select_from(PrivacyLedger).where(PrivacyLedger.release_id == rid_paid)) == 1)

print('9) 最终账目核对')
with Session() as db:
    view = privacy.budget_view(db, dataset_id)
    print(f"    总 ε={view['total_epsilon']} 已耗 ε={view['spent_epsilon']} 剩余 ε={view['remaining_epsilon']}")
    print(f"    总 δ={view['total_delta']} 已耗 δ={view['spent_delta']} 剩余 δ={view['remaining_delta']}")
    # A(0.5) + C(1.0) + 新噪声恢复(0.8) = 2.3；发布 B 与已付费噪声恢复均为 reuse 零扣费。
    check('总消耗 = 各 charge 之和（reuse 为 0）', abs(view['spent_epsilon'] - 2.3) < 1e-12)
    check('剩余预算非负', view['remaining_epsilon'] >= 0 and view['remaining_delta'] >= 0)
    led = privacy.ledger(db, dataset_id)
    check('流水可按批次查询', len(led) >= 4)

print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
