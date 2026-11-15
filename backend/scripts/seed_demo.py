import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, PrivacyBudget, NoiseCache, PrivacyRelease, PrivacyLedger
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import dp, privacy
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员'),('partner','何杉 · 合作方代表')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        admin=db.scalar(select(User).where(User.role=='admin'))
        # 差分隐私演示：负责人设定批次预算，并预先发布一条合作方可读的加噪统计。
        budget=db.scalar(select(PrivacyBudget).where(PrivacyBudget.dataset_id==d.id))
        if not budget:
            now=time.time()
            budget=PrivacyBudget(dataset_id=d.id,total_epsilon=4.0,total_delta=1e-4,created_by=admin.id,created_at=now,updated_at=now)
            db.add(budget);db.flush()
            epsilon,delta=dp.validate_privacy_params(0.5,0.0)
            mechanism=dp.mechanism_for(delta)
            import secrets
            salt=secrets.token_hex(8)
            seed_int,noises=dp.draw_noises(d.id,epsilon,delta,mechanism,salt)
            cache=NoiseCache(dataset_id=d.id,epsilon=epsilon,delta=delta,mechanism=mechanism,salt=salt,rng_seed=seed_int,noises=noises,created_by=admin.id,created_at=now)
            db.add(cache);db.flush()
            release=PrivacyRelease(
                name='合作方月度共败概览 · 差分隐私版',dataset_id=d.id,budget_id=budget.id,cache_id=cache.id,
                idempotency_key='seed-dp-demo-release-0001',epsilon=epsilon,delta=delta,mechanism=mechanism,
                min_support=0.12,min_confidence=0.65,status='reserved',created_by=admin.id,created_at=now,
            )
            db.add(release);db.flush()
            privacy._publish_reserved(db,release)
            db.add(PrivacyLedger(budget_id=budget.id,release_id=release.id,cache_id=cache.id,kind='charge',
                                 epsilon_spend=epsilon,delta_spend=delta,actor=admin.email,
                                 note='演示数据：差分隐私发布扣费',created_at=time.time()))
            db.add(Audit(actor='seed',action='privacy.release.publish',detail={'id':release.id,'epsilon':epsilon},at=time.time()))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,PrivacyBudget,NoiseCache,PrivacyRelease,PrivacyLedger]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
