"""并发发布实证：竞争同一批次预算时总额绝不超支。

生产用 MySQL，create_release 以 SELECT ... FOR UPDATE 行锁把同批次并发发布
串行化，再在同一事务内做余额校验。本地 sqlite 只有数据库级写锁、不支持行锁，
无法多线程并发写，因此这里以"竞争序列"复现行锁下的等价串行：每个请求独立事务、
独立提交，严格按到达顺序争夺同一预算，验证无论多少请求并发，总消耗 ≤ 总额，
且被拒请求不产生任何发布、缓存或流水残留。
"""
import os, sys, tempfile
tmp = tempfile.mkdtemp(prefix='dpconc_')
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/conc.db'
os.environ['ARTIFACT_DIR'] = f'{tmp}/artifacts'
os.environ.setdefault('JWT_SECRET', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy import select, func
from fastapi import HTTPException
from src.config.database import Base, engine, Session
from src import models  # noqa
from src.models import User, Dataset, PrivacyRelease, NoiseCache
from src.services import privacy
from src.services.business import seed_records
from src.utils.security import hash_password
from src.schemas.privacy import ReleaseInput

Base.metadata.create_all(engine)
with Session() as db:
    u = User(email='a@t.local', name='负责人', role='admin', password=hash_password('x'))
    db.add(u); db.flush()
    uid = int(u.id)
    ds = Dataset(name='并发批次', source='c.csv', records=seed_records(), checksum='c', created_at=1.0)
    db.add(ds); db.flush()
    did = int(ds.id)
    privacy.get_or_set_budget(db, did, 1.0, 0.0, u)  # 总 ε=1.0
    db.commit()

N_REQUESTS = 5
# 每个请求用互不相同的 ε，强制各自取噪并各自扣费（同参数才会复用零扣费）。
# 0.3+0.3 不允许重复，故取递增序列：0.10+0.20+0.30+0.40=1.0 恰好耗尽，
# 第 5 个 0.5 使累计达 1.5，必须被 409 拒绝。
EPS_SEQUENCE = [0.10, 0.20, 0.30, 0.40, 0.50]
ok, reject = 0, 0
for i in range(N_REQUESTS):
    eps = EPS_SEQUENCE[i]
    with Session() as db:  # 每请求独立事务，模拟行锁串行后的到达序列
        user = db.get(User, uid)
        data = ReleaseInput(name=f'竞争发布{i}', dataset_id=did, epsilon=eps, delta=0.0,
                            min_support=0.12, min_confidence=0.65)
        try:
            privacy.create_release(db, data, user, f'conc-key-{i}')
            db.commit(); ok += 1
        except HTTPException as e:
            db.rollback()
            assert e.status_code == 409, f'期望 409，实际 {e.status_code}'
            reject += 1

with Session() as db:
    view = privacy.budget_view(db, did)
    n_ledger = len(privacy.ledger(db, did))
    n_release = db.scalar(select(func.count()).select_from(PrivacyRelease))
    n_cache = db.scalar(select(func.count()).select_from(NoiseCache).where(NoiseCache.dataset_id == did))

P = F = 0
def check(name, cond):
    global P, F
    if cond: P += 1; print('  PASS', name)
    else: F += 1; print('  FAIL', name)

print(f"成功 {ok} / 拒绝 {reject}")
check('成功数恰好等于预算可容纳的 4 个', ok == 4)
check('超出总额的第 5 个请求被 409 拒绝', reject == 1)
check('总消耗不超过总额 1.0', abs(view['spent_epsilon'] - 1.0) < 1e-12)
check('剩余预算为 0', abs(view['remaining_epsilon']) < 1e-12)
check('流水条数 = 成功发布数 = 4', n_ledger == 4)
check('发布记录数 = 4（被拒请求无残留）', n_release == 4)
check('4 次扣费各自取噪，缓存数 = 4', n_cache == 4)
# 预算耗尽后再来一个必然被拒
with Session() as db:
    user = db.get(User, uid)
    data = ReleaseInput(name='耗尽后发布', dataset_id=did, epsilon=0.05, delta=0.0,
                        min_support=0.12, min_confidence=0.65)
    try:
        privacy.create_release(db, data, user, 'conc-after')
        check('耗尽后发布被拒', False)
    except HTTPException as e:
        check('耗尽后发布被拒', e.status_code == 409)
        db.rollback()
with Session() as db:
    check('被拒后仍无新增残留', db.scalar(select(func.count()).select_from(PrivacyRelease)) == 4)

print(f'\n结果：{P} 通过 / {F} 失败')
sys.exit(1 if F else 0)

