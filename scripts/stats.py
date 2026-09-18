# -*- coding: utf-8 -*-
"""
chem-research-loop / stats.py
=============================
统计显著性检验与多重比较校正。

为什么需要这一层
----------------
V2 的空白探测用启发式公式 `期望 = freq(a) × freq(b) / N`。它有两个硬伤：

  1. **没有显著性判断** —— 观测共现比期望少 3 篇，是真实信号还是随机波动？
     在 100 篇语料里那可能只是噪声，在 10000 篇里才是强信号。
     点估计回答不了这个问题。
  2. **多重比较问题** —— 一次要检验几十个概念组合。即便单个组合的
     假阳性率控制在 5%，检验 40 个组合后出现至少一次假阳性的概率是
     1-(1-0.05)^40 ≈ 87%。这是探索性研究最常见的翻车点，审稿人一抓一个准。

形式化
------
把「共现不足」定义为 2×2 列联表的单尾检验（原假设：两概念相互独立）：

                含 b      不含 b        合计
      含 a       k        f_a - k       f_a
      不含 a    f_b - k   N-f_a-f_b+k   N - f_a
      合计      f_b       N - f_b       N

检验方向为**左尾**：观测共现 k 是否显著小于独立假设下的期望。
样本量适中时用 Fisher 精确检验（超几何分布），过大时退回正态近似。

零第三方依赖，仅用标准库 math。
"""

from __future__ import annotations

import math
from math import erf, exp, lgamma, log, sqrt

# 超过该语料规模改用正态近似（精确检验需枚举阶乘，规模过大时性价比低）
EXACT_MAX_N = 3000


# --------------------------------------------------------------------------
# 底层：对数域组合数（避免大数溢出）
# --------------------------------------------------------------------------

def log_comb(n: int, k: int) -> float:
    """log C(n, k)，用 lgamma 计算以避免大数溢出。

    C(2000, 1000) 约为 10^600，远超 float 上限（约 10^308），
    因此必须走对数域，而不是先算组合数再取对数。
    """
    if k < 0 or k > n or n < 0:
        return float("-inf")
    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def _logsumexp(logs: list[float]) -> float:
    """数值稳定的 log(sum(exp(x)))。"""
    vals = [x for x in logs if x != float("-inf")]
    if not vals:
        return float("-inf")
    m = max(vals)
    return m + log(sum(exp(x - m) for x in vals))


# --------------------------------------------------------------------------
# Fisher 精确检验（超几何分布左尾）
# --------------------------------------------------------------------------

def hypergeom_logpmf(i: int, n_total: int, f_a: int, f_b: int) -> float:
    """超几何分布概率质量函数的对数。

    P(X = i) = C(f_a, i) × C(N-f_a, f_b-i) / C(N, f_b)
    """
    if i < max(0, f_b - (n_total - f_a)) or i > min(f_a, f_b):
        return float("-inf")
    return (log_comb(f_a, i)
            + log_comb(n_total - f_a, f_b - i)
            - log_comb(n_total, f_b))


def fisher_left_tail_p(k: int, n_total: int, f_a: int, f_b: int) -> float:
    """Fisher 精确检验左尾 p 值：P(X <= k)。

    用于检验「共现显著不足」。全在对数域累加，避免中间值溢出。
    """
    if n_total <= 0 or f_a <= 0 or f_b <= 0:
        return 1.0
    lo = max(0, f_b - (n_total - f_a))
    hi = min(k, f_a, f_b)
    if hi < lo:
        return 0.0
    logs = [hypergeom_logpmf(i, n_total, f_a, f_b) for i in range(lo, hi + 1)]
    lp = _logsumexp(logs)
    if lp == float("-inf"):
        return 0.0
    return min(1.0, max(0.0, exp(lp)))


def normal_approx_left_p(k: int, n_total: int, f_a: int, f_b: int) -> float:
    """正态近似左尾 p 值（带连续性修正），用于超大语料。"""
    if n_total <= 1:
        return 1.0
    expected = (f_a * f_b) / n_total
    var = expected * (1 - f_a / n_total) * (n_total - f_b) / (n_total - 1)
    if var <= 0:
        return 1.0
    z = (k + 0.5 - expected) / sqrt(var)
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def depletion_pvalue(k: int, n_total: int, f_a: int, f_b: int) -> float:
    """共现不足的显著性 p 值：自动在精确检验与正态近似间切换。"""
    if n_total <= EXACT_MAX_N:
        return fisher_left_tail_p(k, n_total, f_a, f_b)
    return normal_approx_left_p(k, n_total, f_a, f_b)


# --------------------------------------------------------------------------
# 效应量
# --------------------------------------------------------------------------

def expected_count(n_total: int, f_a: int, f_b: int) -> float:
    """独立假设下的期望共现次数。"""
    if n_total <= 0:
        return 0.0
    return (f_a * f_b) / n_total


def lift(k: int, n_total: int, f_a: int, f_b: int) -> float:
    """提升度：观测共现 / 期望共现。小于 1 表示共现不足。"""
    exp_c = expected_count(n_total, f_a, f_b)
    if exp_c <= 0:
        return 0.0
    return k / exp_c


def odds_ratio(k: int, n_total: int, f_a: int, f_b: int) -> float:
    """优势比 (a·d)/(b·c)。小于 1 表示负相关，即共现不足。"""
    a = k
    b = f_a - k
    c = f_b - k
    d = n_total - f_a - f_b + k
    if b <= 0 or c <= 0:
        return float("inf") if a > 0 else 1.0
    denom = b * c
    if denom == 0:
        return float("inf")
    return (a * d) / denom


# --------------------------------------------------------------------------
# 多重比较校正
# --------------------------------------------------------------------------

def benjamini_hochberg(
    pvals: list[float], alpha: float = 0.05
) -> tuple[list[bool], list[float]]:
    """Benjamini-Hochberg 错误发现率（FDR）校正。

    为什么用 FDR 而不是 Bonferroni：空白探测是**探索性研究**，
    目的是产生值得跟进的候选，而不是下最终定论。Bonferroni 过于保守，
    会几乎筛掉全部信号；FDR 允许一定比例的假阳性，更适合这个场景 ——
    反正后面还有定向反证这一步兜底。

    返回 (rejected, qvals)：
      rejected[i] —— 第 i 个原假设是否在 FDR=alpha 下被拒绝（即显著）
      qvals[i]    —— 校正后的 q 值
    """
    m = len(pvals)
    if m == 0:
        return [], []

    order = sorted(range(m), key=lambda i: pvals[i])

    # q 值：从最大 p 值往回取累积最小值，保证单调不减
    qvals = [1.0] * m
    running = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        q = pvals[i] * m / (rank + 1)
        running = min(running, q)
        qvals[i] = min(1.0, running)

    # 拒绝域：取满足 p(rank) <= rank/m × alpha 的最大 rank
    max_sig_rank = -1
    for rank in range(m):
        i = order[rank]
        if pvals[i] <= ((rank + 1) / m) * alpha:
            max_sig_rank = rank

    rejected = [False] * m
    for rank in range(max_sig_rank + 1):
        rejected[order[rank]] = True

    return rejected, qvals


def significance_label(q: float, alpha: float = 0.05) -> str:
    """把 q 值翻译成人类可读的显著性标签。"""
    if q is None:
        return "未检验"
    if q < 0.01:
        return "极显著（q<0.01）"
    if q < alpha:
        return f"显著（q<{alpha:g}）"
    if q < 0.10:
        return "边缘（0.05≤q<0.10）"
    return "不显著"


def summarize_tests(pvals: list[float], alpha: float = 0.05) -> dict:
    """一批检验的汇总，供报告与门禁使用。"""
    if not pvals:
        return {"total": 0, "significant": 0, "alpha": alpha,
                "min_p": None, "min_q": None}
    rejected, qvals = benjamini_hochberg(pvals, alpha)
    return {
        "total": len(pvals),
        "alpha": alpha,
        "significant": sum(1 for r in rejected if r),
        "min_p": round(min(pvals), 6),
        "min_q": round(min(qvals), 6),
        "max_q": round(max(qvals), 6),
    }


if __name__ == "__main__":
    # 手工可验算的示例：N=8, f_a=4, f_b=4 的超几何分布
    # P(X=0)=1/70, P(X=1)=16/70, P(X=2)=36/70, P(X=3)=16/70, P(X=4)=1/70
    print("=== Fisher 左尾检验自检（N=8, f_a=4, f_b=4）===")
    for k in range(5):
        p = fisher_left_tail_p(k, 8, 4, 4)
        manual = sum(math.comb(4, i) * math.comb(4, 4 - i) for i in range(k + 1)) / math.comb(8, 4)
        print(f"  k={k}  p={p:.6f}  手工={manual:.6f}  一致={abs(p - manual) < 1e-9}")

    print("\n=== 多重比较校正自检 ===")
    ps = [0.001, 0.008, 0.02, 0.04, 0.30, 0.60]
    rej, qs = benjamini_hochberg(ps, 0.05)
    for p, r, q in zip(ps, rej, qs):
        print(f"  p={p:<6} q={q:.4f}  显著={r}")
    print(f"\n  汇总：{summarize_tests(ps)}")
