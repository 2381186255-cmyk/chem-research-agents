# -*- coding: utf-8 -*-
"""
chem-research-loop / analyze.py
===============================
结构化分析引擎：把文献清单变成可推理的证据底座。

职责（第 ②③ 阶段的可计算部分）：
  1. 概念抽取    —— 用领域词典从标题/摘要中抽出四类概念
  2. 时间趋势    —— 年代分布、近三年动量
  3. 核心文献    —— 按引用数排序，识别奠基性工作
  4. 共现网络    —— 概念对共现矩阵
  5. 空白探测    —— 跨类别未共现组合（结构洞），带多维评分

设计原则：所有结论必须可回溯到具体文献。
每个 gap 候选都附带证据锚点（两侧概念各自的代表文献），
避免「看起来像空白」实为「数据不足」的误判。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

from domains import CATEGORY_LABEL_ZH, CONCEPT_CATEGORIES, ChemistryDomain
from stats import (
    benjamini_hochberg,
    depletion_pvalue,
    lift,
    odds_ratio,
    significance_label,
    summarize_tests,
)

# 空白评分权重（合计 1.0）
W_NOVELTY = 0.40
W_EVIDENCE = 0.25
W_FEASIBILITY = 0.20
W_MOMENTUM = 0.15


# --------------------------------------------------------------------------
# 1. 概念抽取
# --------------------------------------------------------------------------

def paper_id(p) -> str:
    """文献稳定标识，用于证据锚点回溯。"""
    return p.doi_key or p.title_key or (p.title or "")[:60]


def extract_concepts(papers: list, domain: type[ChemistryDomain]) -> dict:
    """从语料中抽取概念，返回每篇论文命中的四类术语。

    返回结构：
      {
        "by_paper": {paper_id: {"material": [...], "method": [...], ...}},
        "frequency": {"material": {term: 命中论文数}, ...},
        "evidence":  {"material": {term: [paper_id, ...]}, ...},
      }
    """
    index = domain.term_index()
    by_paper: dict[str, dict[str, list[str]]] = {}
    frequency: dict[str, Counter] = {c: Counter() for c in CONCEPT_CATEGORIES}
    evidence: dict[str, dict[str, list[str]]] = {c: defaultdict(list) for c in CONCEPT_CATEGORIES}

    for p in papers:
        text = f"{p.title or ''} {p.abstract or ''}"
        pid = paper_id(p)
        hits: dict[str, list[str]] = {}
        for cat in CONCEPT_CATEGORIES:
            found: list[str] = []
            for canonical, patterns in index.get(cat, []):
                # 任一书写变体命中即认定该概念出现（同义词归并）
                if any(pat.search(text) for pat in patterns):
                    found.append(canonical)
                    frequency[cat][canonical] += 1
                    evidence[cat][canonical].append(pid)
            hits[cat] = list(dict.fromkeys(found))
        by_paper[pid] = hits

    return {
        "by_paper": by_paper,
        "frequency": {c: dict(frequency[c]) for c in CONCEPT_CATEGORIES},
        "evidence": {c: dict(evidence[c]) for c in CONCEPT_CATEGORIES},
    }


# --------------------------------------------------------------------------
# 2. 时间趋势
# --------------------------------------------------------------------------

def year_trend(papers: list, recent_window: int = 3, now_year: int | None = None) -> dict:
    """年代分布与近三年动量。动量 = 近 N 年文献量 / 总文献量。"""
    now_year = now_year or datetime.now().year
    years = [p.year for p in papers if isinstance(p.year, int)]
    if not years:
        return {"counts": {}, "span": None, "recent_share": 0.0, "median_year": None}

    counts = Counter(years)
    recent = sum(1 for y in years if y >= now_year - recent_window + 1)
    return {
        "counts": dict(sorted(counts.items())),
        "span": [min(years), max(years)],
        "median_year": sorted(years)[len(years) // 2],
        "recent_window": recent_window,
        "recent_count": recent,
        "recent_share": round(recent / len(years), 3),
        "undated": sum(1 for p in papers if not isinstance(p.year, int)),
    }


# --------------------------------------------------------------------------
# 3. 核心文献
# --------------------------------------------------------------------------

def top_papers(papers: list, n: int = 10) -> list[dict]:
    """按引用数挑选奠基性文献。无引用数据的排后。"""
    ranked = sorted(papers, key=lambda p: (p.citations if p.citations is not None else -1), reverse=True)
    out = []
    for p in ranked[:n]:
        out.append({
            "id": paper_id(p),
            "title": p.title,
            "year": p.year,
            "citations": p.citations,
            "venue": p.venue,
            "doi": p.doi,
            "source": p.source,
            "url": p.url,
        })
    return out


# --------------------------------------------------------------------------
# 4 & 5. 共现网络与空白探测
# --------------------------------------------------------------------------

@dataclass
class GapCandidate:
    """一个候选研究空白：跨类别的低频共现组合。"""

    left_category: str
    left_term: str
    right_category: str
    right_term: str
    left_freq: int
    right_freq: int
    cooccur: int
    expected: float
    novelty: float
    evidence: float
    feasibility: float
    momentum: float
    score: float
    verdict: str
    # ---- 统计显著性（V3 新增）----
    # p_value: 共现不足的单尾检验 p 值
    # q_value: Benjamini-Hochberg 校正后的 q 值（已修正多重比较）
    # odds_ratio / lift: 效应量，小于 1 表示共现不足
    # significant: q < 0.05，即统计上确实「共现显著不足」
    p_value: float = 1.0
    q_value: float = 1.0
    odds_ratio: float = 1.0
    lift_value: float = 1.0
    significant: bool = False
    left_exemplar: dict = field(default_factory=dict)
    right_exemplar: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _exemplar(evidence_ids: list[str], paper_map: dict, top_map: dict) -> dict:
    """取该概念的代表文献（优先引用数最高者）作为证据锚点。"""
    if not evidence_ids:
        return {}
    best = None
    for pid in evidence_ids:
        p = paper_map.get(pid)
        if p is None:
            continue
        c = p.citations if p.citations is not None else -1
        if best is None or c > (best.citations if best.citations is not None else -1):
            best = p
    if best is None:
        return {}
    return {
        "id": paper_id(best),
        "title": best.title,
        "year": best.year,
        "citations": best.citations,
        "doi": best.doi,
    }


def judge_verification(
    co_hits: int,
    retrieved: int,
    refute_min_hits: int = 3,
    refute_min_rate: float = 0.10,
) -> tuple[str, bool | None]:
    """反证判定（纯函数，便于离线测试三条分支）。

    判定规则：
      co_hits == 0                                    -> 确认（空白成立）
      co_hits >= refute_min_hits 且 密度 >= min_rate  -> 推翻（假空白）
      其他                                             -> 存疑（需人工判读）

    用交叉密度而非绝对篇数：20 篇命中里 2 篇交叉（10%）与 5 篇命中里
    2 篇交叉（40%），含义完全不同，绝对数会抹掉这个差别。
    """
    rate = co_hits / max(1, retrieved)
    if co_hits == 0:
        return "确认：本领域精查零命中，空白成立", True
    if co_hits >= refute_min_hits and rate >= refute_min_rate:
        return (f"推翻：本领域已有 {co_hits} 篇交叉研究（占命中 {rate:.0%}），"
                f"交叉密集，属假空白"), False
    return (f"存疑：本领域仅 {co_hits} 篇零星交叉（占命中 {rate:.0%}），"
            f"交叉稀疏但非零，需人工判读"), None


def adaptive_min_freq(n_corpus: int) -> int:
    """语料规模自适应的概念频次门槛。

    固定的绝对门槛在小语料下会直接让空白探测失效：实测 18 篇语料时，
    几乎所有概念频次都是 1，用门槛 3 过滤后空白候选为 0 条。
    因此门槛随语料规模缩放，同时保留绝对下限 2（出现 1 次不足以构成趋势）。
    """
    if n_corpus < 50:
        return 2
    if n_corpus < 150:
        return 3
    if n_corpus < 400:
        return 4
    return 5


def adaptive_min_expected(n_corpus: int) -> float:
    """语料规模自适应的期望共现门槛。

    期望共现 = freq(左) × freq(右) / N。小语料下这个值天然很小，
    若坚持要求期望 >= 1，绝大多数组合会被判为「语料不足」而丢弃。
    """
    if n_corpus < 50:
        return 0.6
    return 1.0


def find_gaps(
    papers: list,
    domain: type[ChemistryDomain],
    analysis: dict,
    min_freq: int | None = None,
    min_expected: float | None = None,
    top_n: int = 12,
    now_year: int | None = None,
    recent_window: int = 3,
    alpha: float = 0.05,
    stats_out: dict | None = None,
) -> list[GapCandidate]:
    """探测跨类别未共现组合（结构洞式研究空白）。

    算法：
      对 domain.gap_pairs 指定的类别组合（如 material×application），
      遍历两侧高频概念构成的所有配对，统计其真实共现次数，
      并与「若两概念相互独立时的期望共现次数」比较：

          期望共现 = freq(左) × freq(右) / N        （N = 有效语料量）

      若真实共现远低于期望甚至为 0，说明「这两件事各自都有人做，
      但没人把它们放在一起」—— 这就是一个结构性空白。

    三重防误判：
      · freq 门槛：两侧概念各自至少出现 min_freq 次，排除「本身没人研究」
      · 期望门槛：期望共现至少 min_expected，排除「语料太小导致必然为 0」
      · 证据锚点：每个 gap 附带两侧代表文献，供人工/Agent 复核
    """
    now_year = now_year or datetime.now().year
    paper_map = {paper_id(p): p for p in papers}
    n_corpus = max(1, len(papers))
    if min_freq is None:
        min_freq = adaptive_min_freq(n_corpus)
    if min_expected is None:
        min_expected = adaptive_min_expected(n_corpus)

    # 每类只取频次最高的若干概念，避免组合爆炸（4 组 pair × 18 × 18 已足够）
    max_terms_per_cat = 18

    freq = analysis["concepts"]["frequency"]
    evid = analysis["concepts"]["evidence"]
    by_paper = analysis["concepts"]["by_paper"]

    # 逐类别统计每个术语出现的论文集合
    term_docs: dict[str, dict[str, set[str]]] = {}
    for cat in CONCEPT_CATEGORIES:
        term_docs[cat] = {t: set(ids) for t, ids in evid.get(cat, {}).items()}

    # 各术语的年代动量
    def momentum_of(ids: set[str]) -> float:
        yrs = [paper_map[i].year for i in ids
               if i in paper_map and isinstance(paper_map[i].year, int)]
        if not yrs:
            return 0.0
        recent = sum(1 for y in yrs if y >= now_year - recent_window + 1)
        return round(recent / len(yrs), 3)

    # ---- 第一阶段：枚举候选，计算评分与检验统计量（此时不做显著性判断）----
    raw: list[dict] = []
    for left_cat, right_cat in domain.gap_pairs:
        left_ranked = sorted(freq.get(left_cat, {}).items(), key=lambda kv: kv[1], reverse=True)
        right_ranked = sorted(freq.get(right_cat, {}).items(), key=lambda kv: kv[1], reverse=True)
        left_terms = [t for t, c in left_ranked if c >= min_freq][:max_terms_per_cat]
        right_terms = [t for t, c in right_ranked if c >= min_freq][:max_terms_per_cat]

        for lt in left_terms:
            for rt in right_terms:
                if lt == rt:
                    continue
                lf = freq[left_cat][lt]
                rf = freq[right_cat][rt]
                expected = (lf * rf) / n_corpus
                if expected < min_expected:
                    continue  # 语料不足，无法判定为空白
                co = len(term_docs[left_cat][lt] & term_docs[right_cat][rt])

                novelty = round(1.0 - min(1.0, co / expected), 3)
                if co > 0 and novelty < 0.5:
                    continue  # 共现已接近或超过期望，不是空白

                evidence_score = round(min(1.0, expected / 2.0), 3)
                feasibility = round(min(1.0, (lf + rf) / 12.0), 3)
                momentum = round((momentum_of(term_docs[left_cat][lt])
                                  + momentum_of(term_docs[right_cat][rt])) / 2, 3)

                score = round(
                    W_NOVELTY * novelty + W_EVIDENCE * evidence_score
                    + W_FEASIBILITY * feasibility + W_MOMENTUM * momentum, 4)

                # 措辞上明确标注「待反证」：语料内的零共现只是候选信号，
                # 未经定向检索反证前不能称为空白。实测中相当比例的
                # 语料内零共现，在精查后被发现是本领域已有研究（采样偏差）。
                if co == 0:
                    base_verdict = "语料内零共现"
                elif novelty >= 0.75:
                    base_verdict = "共现显著低于期望"
                else:
                    base_verdict = "存在少量交叉"

                raw.append({
                    "left_category": left_cat, "left_term": lt,
                    "right_category": right_cat, "right_term": rt,
                    "left_freq": lf, "right_freq": rf, "cooccur": co,
                    "expected": round(expected, 2),
                    "novelty": novelty, "evidence": evidence_score,
                    "feasibility": feasibility, "momentum": momentum,
                    "score": score,
                    "p_value": round(depletion_pvalue(co, n_corpus, lf, rf), 6),
                    "odds_ratio": round(odds_ratio(co, n_corpus, lf, rf), 4),
                    "lift_value": round(lift(co, n_corpus, lf, rf), 4),
                    "_base_verdict": base_verdict,
                    "left_exemplar": _exemplar(evid[left_cat][lt], paper_map, {}),
                    "right_exemplar": _exemplar(evid[right_cat][rt], paper_map, {}),
                })

    # ---- 第二阶段：批量多重比较校正 ----
    # 关键：必须把全部候选的 p 值放在一起统一做 FDR 校正。若逐个单独判断
    # 「p < 0.05 即显著」，检验 40 个组合后假阳性概率高达 87%
    # （1 - 0.95^40），这是探索性研究最容易翻车的地方。
    pvals = [r["p_value"] for r in raw]
    rejected, qvals = benjamini_hochberg(pvals, alpha=alpha)
    for i, r in enumerate(raw):
        r["q_value"] = round(qvals[i], 6)
        r["significant"] = bool(rejected[i])
        r["verdict"] = (f"{r.pop('_base_verdict')}｜"
                        f"{significance_label(qvals[i], alpha)}（待反证）")

    if stats_out is not None:
        stats_out.update(summarize_tests(pvals, alpha))

    candidates = [GapCandidate(**r) for r in raw]
    # 统计显著的优先，其余按综合分排序
    candidates.sort(key=lambda g: (g.significant, g.score), reverse=True)
    return candidates[:top_n]


def cooccurrence(
    papers: list,
    domain: type[ChemistryDomain],
    analysis: dict,
    top_n: int = 15,
) -> list[dict]:
    """跨类别概念共现对，用于勾勒领域已有的「方法—应用」主干。"""
    evid = analysis["concepts"]["evidence"]
    pairs = []
    for left_cat, right_cat in domain.gap_pairs:
        for lt, lids in evid.get(left_cat, {}).items():
            lset = set(lids)
            for rt, rids in evid.get(right_cat, {}).items():
                inter = len(lset & set(rids))
                if inter > 0:
                    pairs.append({
                        "left": f"{CATEGORY_LABEL_ZH[left_cat]}:{lt}",
                        "right": f"{CATEGORY_LABEL_ZH[right_cat]}:{rt}",
                        "count": inter,
                    })
    pairs.sort(key=lambda x: x["count"], reverse=True)
    return pairs[:top_n]


# --------------------------------------------------------------------------
# 汇总入口
# --------------------------------------------------------------------------

def analyze(papers: list, domain: type[ChemistryDomain], top_n_papers: int = 10,
            top_n_gaps: int = 12) -> dict:
    """对语料做完整结构化分析，产出下游可推理的证据底座。"""
    concepts = extract_concepts(papers, domain)
    base = {
        "corpus": {
            "total": len(papers),
            "with_abstract": sum(1 for p in papers if p.abstract),
            "with_doi": sum(1 for p in papers if p.doi),
            "open_access": sum(1 for p in papers if p.is_oa),
            "by_source": dict(Counter(p.source for p in papers)),
        },
        "year_trend": year_trend(papers),
        "top_papers": top_papers(papers, top_n_papers),
        "concepts": concepts,
    }
    base["concept_summary"] = {
        cat: dict(sorted(concepts["frequency"].get(cat, {}).items(),
                         key=lambda kv: kv[1], reverse=True)[:20])
        for cat in CONCEPT_CATEGORIES
    }
    base["cooccurrence"] = cooccurrence(papers, domain, base)
    base["gap_thresholds"] = {
        "corpus_size": len(papers),
        "min_freq": adaptive_min_freq(len(papers)),
        "min_expected": adaptive_min_expected(len(papers)),
    }
    # stats_out 收集的是**全部候选**的检验汇总（而非 top_n 截断后的子集），
    # 这样统计汇总才反映真实的检验次数，多重比较校正才有意义。
    gap_stats: dict = {}
    gaps = find_gaps(papers, domain, base, top_n=top_n_gaps, stats_out=gap_stats)
    base["gap_stats"] = gap_stats
    base["gaps"] = [g.to_dict() for g in gaps]
    return base


if __name__ == "__main__":
    print("analyze.py 需要配合语料运行，请使用 run.py 执行完整闭环。")
