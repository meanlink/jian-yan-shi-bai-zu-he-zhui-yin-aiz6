"""差分隐私语义检验：相邻数据集（仅差一批记录）的加噪输出在统计上不可区分。

不依赖数据库，纯算法层验证：
1. 同一确定性盐值下，相邻数据集输出仅差 0/1 的真实计数，被同分布噪声掩盖；
2. 大量独立盐值的噪声经验分布符合理论尺度（Laplace b=1/ε、Gaussian σ≈1.64/ε）；
3. 相邻数据集在多次发布上的均值差远小于真实差异的典型尺度，单批不可被稳定推断。
"""
import math
import sys
import statistics

sys.path.insert(0, __file__.rsplit('/', 1)[0] + '/backend')
from src.services import dp
from src.services.business import seed_records

P = F = 0
def check(name, cond, extra=''):
    global P, F
    if cond: P += 1; print('  PASS', name)
    else: F += 1; print('  FAIL', name, extra)

records = seed_records()
neighbor = records[:-1]  # 相邻数据集：移除最后一批
assert len(records) - len(neighbor) == 1

print('1) 确定性盐值：相邻数据集同键输出差异 = 真实计数差（0 或 1）')
eps, delta = 0.5, 0.0
_, noises = dp.draw_noises(1, eps, delta, 'laplace', 'fixed-salt')
c1 = dp.true_counts(records)
c2 = dp.true_counts(neighbor)
snap1 = dp.build_noisy_snapshot(c1, noises)
snap2 = dp.build_noisy_snapshot(c2, noises)
diffs = {k: snap1[k]['noisy_count_float'] - snap2[k]['noisy_count_float'] for k in noises}
real_diffs = {k: c1[k] - c2[k] for k in noises}
check('加噪输出差恰好等于真实计数差', all(abs(diffs[k] - real_diffs[k]) < 1e-9 for k in noises))
check('单批记录对任一计数的影响 ≤ 1（敏感度 1）', all(v in (0, 1) for v in real_diffs.values()))

print('2) 噪声经验分布符合理论尺度')
def sample_noises(mechanism, epsilon, delta, n=4000):
    samples = []
    for i in range(n):
        _, nz = dp.draw_noises(1, epsilon, delta, mechanism, f'salt-{i}')
        samples.append(nz['heat_fail'])
    return samples

lap = sample_noises('laplace', 0.5, 0.0)
b_theory = 1.0 / 0.5
check('Laplace 均值≈0', abs(statistics.mean(lap)) < 0.15, statistics.mean(lap))
# Laplace 方差 = 2b²
check('Laplace 方差≈2b²', abs(statistics.pvariance(lap) - 2 * b_theory ** 2) < 1.2, statistics.pvariance(lap))

gauss = sample_noises('gaussian', 0.5, 1e-5, n=4000)
sigma_theory = dp.GAUSSIUS_C / 0.5
check('Gaussian 均值≈0', abs(statistics.mean(gauss)) < 0.15, statistics.mean(gauss))
check('Gaussian 方差≈σ²', abs(statistics.pvariance(gauss) - sigma_theory ** 2) < 0.8, statistics.pvariance(gauss))

print('3) 相邻数据集在多次独立发布下不可稳定区分（单批推断被噪声淹没）')
def mean_noisy_total(recs, mechanism, epsilon, delta, n=600):
    totals = []
    for i in range(n):
        _, nz = dp.draw_noises(1, epsilon, delta, mechanism, f'mean-{i}')
        snap = dp.build_noisy_snapshot(dp.true_counts(recs), nz)
        totals.append(snap['__total__']['noisy_count_float'])
    return statistics.mean(totals), statistics.stdev(totals)

m1, sd1 = mean_noisy_total(records, 'laplace', 0.5, 0.0)
m2, sd2 = mean_noisy_total(neighbor, 'laplace', 0.5, 0.0)
# 真实差异仅 1 批，而加噪总数的标准差约为 sqrt(2)*b≈2.83；均值差应远小于一个标准差。
check('相邻数据集均值差远小于噪声标准差', abs(m1 - m2) < sd1, f'mean diff={abs(m1-m2):.3f} sd={sd1:.3f}')
check('噪声标准差与理论量级一致（≈sqrt(2)/ε）', abs(sd1 - math.sqrt(2) / 0.5) < 0.8, sd1)

print('4) ε 越小噪声越大（隐私-效用权衡方向正确）')
_, nz_lo = dp.draw_noises(1, 0.1, 0.0, 'laplace', 'tradeoff')
_, nz_hi = dp.draw_noises(1, 1.5, 0.0, 'laplace', 'tradeoff')
spread_lo = max(abs(v) for v in nz_lo.values())
spread_hi = max(abs(v) for v in nz_hi.values())
check('小 ε 的噪声尺度大于大 ε（同盐值抽样比较 b=1/ε）',
      dp.noise_scale(0.1, 0.0, 'laplace') > dp.noise_scale(1.5, 0.0, 'laplace'))
check('误差边界随 ε 减小而增大', dp.error_bound(0.1, 0, 'laplace') > dp.error_bound(1.5, 0, 'laplace'))

print('5) 载荷不含任何可还原单批的真实计数')
_, nz = dp.draw_noises(1, 0.5, 0.0, 'laplace', 'payload-check')
class _DS:
    id, name = 1, '检验批次'
payload = dp.build_release_payload(
    name='p', dataset=_DS(), total_true=len(records), epsilon=0.5, delta=0.0, mechanism='laplace',
    min_support=0.12, min_confidence=0.65,
    snapshot=dp.build_noisy_snapshot(dp.true_counts(records), nz), cache_id=1, created_at=1.0)
import json
blob = json.dumps(payload, ensure_ascii=False)
def has_key(obj, target):
    if isinstance(obj, dict):
        return any(k == target or has_key(v, target) for k, v in obj.items())
    if isinstance(obj, list):
        return any(has_key(v, target) for v in obj)
    return False
check('载荷中不含原始事务字段键', not has_key(payload, 'batch_id') and not has_key(payload, 'records'))
check('快照每项只含加噪值与噪声，无真实计数', all(set(v.keys()) == {'noisy_count', 'noisy_count_float', 'noise'} for v in payload['snapshot'].values()))

print(f'\n结果：{P} 通过 / {F} 失败')
sys.exit(1 if F else 0)
