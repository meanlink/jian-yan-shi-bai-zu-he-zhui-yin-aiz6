"""差分隐私统计与发布口径。

设计要点：
- 每个数据批次共享一份隐私预算，所有发布通过组合定理累计 ε/δ。
- 同批次 + 同 (epsilon, delta) 只取一次噪声并持久化；换账号、换筛选阈值均复用。
- 阈值筛选与所有指标都从同一份加噪统计生成，不再触碰真实计数。
- Laplace 机制提供纯 ε-DP（delta=0）；Gaussian 机制提供 (ε,δ)-近似 DP。
"""
import hashlib
import math
from itertools import combinations

import numpy as np

from src.services.business import ITEMS, NAMES

MAX_ITEMSET_SIZE = 3
# 单项敏感度 1：相邻数据集仅相差一批记录，任一计数至多变化 1。
COUNT_SENSITIVITY = 1.0
# (ε,δ)-Gaussian 机制的经典噪声尺度（Dwork & Roth, 定理 A.1）。
GAUSSIUS_C = math.sqrt(2.0 * math.log(1.25))
# 加噪计数允许的最小值，避免负计数污染比例口径。
COUNT_FLOOR = 0.0


def stat_keys():
    """所有需要加噪的统计键：总批次数 + 最多 3 项的全部项集计数。"""
    keys = ["__total__"]
    for size in range(1, MAX_ITEMSET_SIZE + 1):
        for combo in combinations(ITEMS, size):
            keys.append("|".join(combo))
    return keys


def true_counts(records):
    """从原始批次事务计算真实计数（仅在取噪瞬间使用，绝不外发）。"""
    transactions = []
    for row in records:
        transactions.append(frozenset(k for k in ITEMS if float(row[k]) == 1))
    total = len(transactions)
    counts = {"__total__": total}
    for size in range(1, MAX_ITEMSET_SIZE + 1):
        for combo in combinations(ITEMS, size):
            itemset = frozenset(combo)
            counts["|".join(combo)] = sum(itemset <= t for t in transactions)
    return counts


def validate_privacy_params(epsilon, delta):
    if not isinstance(epsilon, (int, float)) or not math.isfinite(epsilon):
        raise ValueError("隐私预算 ε 须为有限数值。")
    if not (0.05 <= float(epsilon) <= 2.0):
        raise ValueError("单次 ε 须在 0.05~2.0 之间；更大额度请由负责人分拆发布。")
    if not isinstance(delta, (int, float)) or not math.isfinite(delta):
        raise ValueError("δ 须为有限数值。")
    delta = float(delta)
    epsilon = float(epsilon)
    if not (delta == 0.0 or 1e-6 <= delta <= 1e-3):
        raise ValueError("δ 只能为 0（纯 ε-DP，Laplace）或 1e-6~1e-3（近似 DP，Gaussian）。")
    return epsilon, delta


def validate_thresholds(min_support, min_confidence):
    try:
        min_support = float(min_support)
        min_confidence = float(min_confidence)
    except (ValueError, TypeError) as exc:
        raise ValueError("筛选阈值须为有效比例。") from exc
    if not 0.05 <= min_support <= 0.5 or not 0.2 <= min_confidence <= 0.95:
        raise ValueError("最小支持度须 0.05~0.5，最小置信度须 0.2~0.95。")
    return min_support, min_confidence


def mechanism_for(delta):
    return "laplace" if float(delta) == 0.0 else "gaussian"


def noise_scale(epsilon, delta, mechanism):
    if mechanism == "laplace":
        return COUNT_SENSITIVITY / epsilon
    return GAUSSIUS_C * COUNT_SENSITIVITY / epsilon


def deterministic_seed(dataset_id, epsilon, delta, salt):
    """确定性种子：同批次同参数同盐值必然生成同一组噪声。"""
    digest = hashlib.sha256(
        f"dp-release|{dataset_id}|{epsilon:.10f}|{delta:.10f}|{salt}".encode()
    ).hexdigest()
    return int(digest[:15], 16)


def draw_noises(dataset_id, epsilon, delta, mechanism, salt):
    """一次性为全部统计键取噪，键序固定保证可复现。"""
    seed = deterministic_seed(dataset_id, epsilon, delta, salt)
    rng = np.random.default_rng(seed)
    scale = noise_scale(epsilon, delta, mechanism)
    keys = stat_keys()
    if mechanism == "laplace":
        values = rng.laplace(0.0, scale, size=len(keys))
    else:
        values = rng.normal(0.0, scale, size=len(keys))
    return seed, {key: float(value) for key, value in zip(keys, values)}


def error_bound(epsilon, delta, mechanism, confidence=0.95):
    """单条加噪计数的误差幅度（置信度 95%），用于页面误差说明。"""
    scale = noise_scale(epsilon, delta, mechanism)
    if mechanism == "laplace":
        # |Lap(0,b)| 的分位数：b*ln(1/(1-p))
        return scale * math.log(1.0 / (1.0 - confidence))
    # 正态分位数 z≈1.959964
    return 1.959964 * scale


def _clamped_noisy_count(raw):
    return max(COUNT_FLOOR, raw)


def build_noisy_snapshot(true_count_map, noises):
    """真实计数 + 已存噪声 → 加噪统计快照（浮点与整数展示值同时保存）。"""
    snapshot = {}
    for key in stat_keys():
        raw = float(true_count_map[key]) + float(noises[key])
        clamped = _clamped_noisy_count(raw)
        snapshot[key] = {
            "noisy_count": round(clamped),
            "noisy_count_float": round(clamped, 4),
            "noise": round(float(noises[key]), 4),
        }
    return snapshot


def _support(snapshot, key, total_float):
    if total_float <= 0:
        return 0.0
    return snapshot[key]["noisy_count_float"] / total_float


def build_release_payload(*, name, dataset, total_true, epsilon, delta, mechanism,
                          min_support, min_confidence, snapshot, cache_id, created_at):
    """从同一份加噪快照推导全部指标与阈值筛选结果，真实计数不进入载荷。"""
    total_float = snapshot["__total__"]["noisy_count_float"]
    total_display = snapshot["__total__"]["noisy_count"]

    support_float = {}
    for size in range(1, MAX_ITEMSET_SIZE + 1):
        for combo in combinations(ITEMS, size):
            key = "|".join(combo)
            support_float[key] = _support(snapshot, key, total_float)

    # 频繁项集筛选也以加噪支持度为准。
    frequent = {key for key, value in support_float.items() if value >= min_support}
    rules = []
    for size in range(2, MAX_ITEMSET_SIZE + 1):
        for combo in combinations(ITEMS, size):
            joint_key = "|".join(combo)
            if joint_key not in frequent:
                continue
            joint_support = support_float[joint_key]
            itemset = list(combo)
            for length in range(1, len(itemset)):
                for subset in combinations(sorted(itemset), length):
                    ant_key = "|".join(subset)
                    cons_items = [k for k in itemset if k not in subset]
                    cons_key = "|".join(sorted(cons_items))
                    if ant_key not in frequent or cons_key not in frequent:
                        continue
                    ant_support = support_float[ant_key]
                    cons_support = support_float[cons_key]
                    if ant_support <= 0:
                        continue
                    confidence = joint_support / ant_support
                    lift = confidence / cons_support if cons_support > 0 else 0.0
                    if confidence >= min_confidence and lift > 1.0 + 1e-12:
                        rules.append({
                            "antecedent": " + ".join(NAMES[k] for k in subset),
                            "consequent": " + ".join(NAMES[k] for k in sorted(cons_items)),
                            "noisy_count": snapshot[joint_key]["noisy_count"],
                            "support_pct": round(joint_support * 100, 2),
                            "confidence_pct": round(confidence * 100, 2),
                            "lift": round(lift, 4),
                        })
    rules.sort(key=lambda row: (-row["lift"], -row["support_pct"], row["antecedent"], row["consequent"]))
    for rank, rule in enumerate(rules, 1):
        rule["rule_id"] = f"DP-R{rank:02}"

    itemset_rows = []
    for size in range(1, MAX_ITEMSET_SIZE + 1):
        for combo in combinations(ITEMS, size):
            key = "|".join(combo)
            itemset_rows.append({
                "itemset": " + ".join(NAMES[k] for k in combo),
                "size": size,
                "noisy_count": snapshot[key]["noisy_count"],
                "support_pct": round(support_float[key] * 100, 2),
                "kept_frequent": key in frequent,
            })

    bound = error_bound(epsilon, delta, mechanism)
    guarantee = (
        f"本发布满足纯 ε-差分隐私（ε={epsilon:g}，δ=0）：任一批次记录的存在与否，"
        "都无法从加噪计数中被确定性推断。"
        if mechanism == "laplace"
        else f"本发布满足 (ε,δ)-近似差分隐私（ε={epsilon:g}，δ={delta:g}）：除概率不超过 δ 的小事件外，"
        "任一批次记录的存在与否都无法从加噪计数中被确定性推断。"
    )
    composition_note = (
        "同一数据批次的多次发布按顺序组合累计隐私预算：总消耗为各次 ε 之和、δ 之和；"
        "复用同一组已存噪声的发布不额外扣费。"
    )
    return {
        "release_name": name,
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "privacy": {
            "epsilon": epsilon,
            "delta": delta,
            "mechanism": mechanism,
            "cache_id": cache_id,
            "guarantee": guarantee,
            "composition_note": composition_note,
            "count_error_95": round(bound, 4),
            "noise_scale": round(noise_scale(epsilon, delta, mechanism), 4),
        },
        "thresholds": {"min_support": min_support, "min_confidence": min_confidence},
        "summary": (
            f"加噪口径下共 {total_display} 批复检事务，"
            f"按支持度≥{min_support:g}、置信度≥{min_confidence:g} 筛出 {len(rules)} 条提升度大于 1 的规则。"
        ),
        "metrics": [
            {"label": "加噪批次总数", "value": total_display},
            {"label": "加噪单项失败（均值）", "value": round(
                sum(snapshot["|".join([k])]["noisy_count"] for k in ITEMS) / len(ITEMS), 1)},
            {"label": "频繁项集（加噪筛选）", "value": len(frequent)},
            {"label": "筛选后规则", "value": len(rules)},
            {"label": "单计数95%误差幅度", "value": round(bound, 2)},
        ],
        "table": {
            "columns": [
                {"key": "rule_id", "label": "规则编号"},
                {"key": "antecedent", "label": "前项A"},
                {"key": "consequent", "label": "后项B"},
                {"key": "noisy_count", "label": "加噪共同失败批次"},
                {"key": "support_pct", "label": "加噪支持度(%)"},
                {"key": "confidence_pct", "label": "加噪置信度(%)"},
                {"key": "lift", "label": "加噪提升度"},
            ],
            "rows": rules,
        },
        "itemsets": itemset_rows,
        "snapshot": snapshot,
        "true_total_records": total_true,
        "generated_at": created_at,
        "method": (
            "对总批次与最多 3 项的全部项集计数各加一次校准噪声（敏感度 1）；"
            "支持度、置信度、提升度与阈值筛选全部由同一份加噪统计推导，"
            "发布载荷不含任何真实计数。"
        ),
        "limitations": [
            "加噪结果存在随机误差，小计数项的相对误差更大，只适用于合作方层面的总体判断。",
            "δ>0 的 Gaussian 机制保留极小概率的失效事件；Laplace 机制无该失效概率。",
            "共败关联不等于共同根因；本结果不能反推任何单一批次的真实标签。",
        ],
    }
