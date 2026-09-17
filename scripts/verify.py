# -*- coding: utf-8 -*-
"""
chem-research-loop / verify.py
=============================
反证实验模块：把候选缺口转成可检验假设，并主动证伪它们。

这一层是整个工作流的**真值来源**。统计层的「语料内零共现」只是候选信号，
必须经过定向检索反证才能称为结论。

三条实测校准的关键设计
----------------------
1. **必须带领域锚点**：不加锚点时查 `catalysis selectivity` 会拉回全学科
   通用文献，把本领域的真实空白全部误判为「已被研究」（实测 8/8 全军覆没）。
2. **判定用交叉密度而非绝对篇数**：20 篇命中里 2 篇交叉（10%）与
   5 篇命中里 2 篇交叉（40%）含义完全不同。
3. **假设句式按类别组合生成**：早期版本对模板所有占位符填同一个词，
   产出「把 gas separation 应用于 gas separation」这种无意义句子。
"""

from __future__ import annotations

import time

from analyze import judge_verification, paper_id
from domains import CATEGORY_LABEL_ZH
from sources import SourceRegistry

VERIFY_SOURCES = ("openalex", "crossref", "europepmc")


# --------------------------------------------------------------------------
# 假设句式：按左右两侧的语义类别选择，杜绝占位符错配
# --------------------------------------------------------------------------

PAIR_FRAME: dict[tuple[str, str], tuple[str, str]] = {
    ("material", "application"): (
        "把「{L}」用于「{R}」",
        "该材料体系能否在目标应用场景中达到工程可用水平，其瓶颈在哪里"),
    ("application", "material"): (
        "用「{R}」承载「{L}」",
        "该材料是否为该应用场景的合适载体，其选择性与稳定性如何"),
    ("method", "application"): (
        "将「{L}」应用于「{R}」",
        "该方法的引入能否在目标应用中带来可测量的增益"),
    ("application", "method"): (
        "为「{L}」引入「{R}」方法",
        "该方法能否突破目标应用场景的现有瓶颈"),
    ("material", "method"): (
        "以「{L}」结合「{R}」",
        "该材料在该方法下的适配性与结果可再现性如何"),
    ("method", "material"): (
        "以「{L}」处理或表征「{R}」",
        "该方法能否揭示该材料体系中此前未被观测到的性质"),
    ("application", "metric"): (
        "在「{R}」这一指标维度上重新审视「{L}」",
        "现有报道是否系统性低估了该指标维度上的差异"),
    ("method", "metric"): (
        "考察「{L}」对「{R}」的影响规律",
        "该方法的哪些参数主导了该指标的波动"),
    ("material", "metric"): (
        "围绕「{R}」指标优化「{L}」",
        "该材料体系的指标上限受什么因素制约"),
    ("metric", "material"): (
        "从「{L}」出发反向筛选材料体系",
        "哪些材料体系在该指标上仍有未开发的余量"),
    ("metric", "method"): (
        "以「{L}」为判据筛选「{R}」方案",
        "哪些方法方案在该指标上表现最优、代价最小"),
    ("metric", "application"): (
        "以「{L}」为准绳评估「{R}」场景的可行性",
        "该应用场景的真正短板是否落在该指标上"),
    ("material", "material"): (
        "「{L}」与「{R}」的复合或杂化",
        "两者复合后能否产生单一组分不具备的协同效应"),
    ("method", "method"): (
        "「{L}」与「{R}」的联用",
        "两种方法的联用能否同时获得各自优势并互相补偿短板"),
}


def make_hypothesis(gap: dict, domain, idx: int) -> dict:
    """把缺口转成结构化假设卡（含研究问题、判据与所需数据）。"""
    lcat, rcat = gap["left_category"], gap["right_category"]
    lzh, rzh = CATEGORY_LABEL_ZH[lcat], CATEGORY_LABEL_ZH[rcat]
    lt, rt = gap["left_term"], gap["right_term"]

    frame = PAIR_FRAME.get((lcat, rcat))
    if frame:
        action, question = frame[0].format(L=lt, R=rt), frame[1]
    else:
        action = f"「{lt}」与「{rt}」的结合"
        question = "该组合是否具备实质可行性"

    statement = (
        f"{action} —— 二者分属{lzh}与{rzh}；在现有语料中交叉出现仅 "
        f"{gap['cooccur']} 篇，低于随机期望的 {gap['expected']} 篇，"
        f"提示该组合尚未被系统研究。"
    )

    return {
        "hypothesis_id": f"H{idx + 1:02d}",
        "derived_from_gap": f"{lcat}:{lt} × {rcat}:{rt}",
        "statement": statement,
        "research_question": question,
        "gap_score": gap["score"],
        "gap_verdict": gap["verdict"],
        "testable_by": "定向文献反证检索 + 文献数据统计对比",
        "required_data": [
            f"「{lt}」（{lzh}）相关文献的量化指标",
            f"「{rt}」（{rzh}）相关文献的量化指标",
            "两者交叉报道的存在性与数量证据",
        ],
        "falsification": (
            "若定向检索能返回至少 3 篇实质性交叉研究且交叉密度不低于 10%，"
            "则本假设不成立；若精查仍零命中，则空白成立、假设被支持。"
        ),
        "evidence_anchors": {
            "left": gap.get("left_exemplar", {}),
            "right": gap.get("right_exemplar", {}),
        },
    }


# --------------------------------------------------------------------------
# 领域锚点
# --------------------------------------------------------------------------

def pick_domain_anchor(analysis: dict) -> tuple[str, str]:
    """从语料中挑出最能代表本领域的锚点概念，返回 (术语, 类别)。

    锚点用于统一反证检索的领域边界，避免拿全学科文献误判本领域空白。
    """
    freq = analysis.get("concepts", {}).get("frequency", {})
    cands: list[tuple[str, str, int]] = []
    for cat in ("material", "application", "method"):
        for term, c in freq.get(cat, {}).items():
            cands.append((term, cat, c))
    if not cands:
        return "", ""
    cands.sort(key=lambda x: x[2], reverse=True)
    term, cat, _ = cands[0]
    return term, cat


# --------------------------------------------------------------------------
# 反证检索
# --------------------------------------------------------------------------

def build_verify_queries(gap: dict, anchor: str = "") -> list[str]:
    """构造精准检索串。宁窄勿宽：过宽的查询会把不相关文献拉进来。"""
    lt, rt = gap["left_term"], gap["right_term"]
    if anchor and anchor not in (lt, rt):
        return [f'"{anchor}" "{lt}" "{rt}"', f"{anchor} {lt} {rt}"]
    return [f'"{lt}" "{rt}"', f"{lt} {rt}"]


def patterns_for(index: dict, category: str, term: str) -> list:
    """取出某概念的全部书写变体正则（同义词归并后的结果）。"""
    for t, pats in index.get(category, []):
        if t == term:
            return pats
    return []


def verify_gap(gap: dict, domain, anchor: tuple[str, str] = ("", ""),
               per_source: int = 5) -> dict:
    """对单条缺口执行反证检索，判定它是真空白还是假空白。

    只统计「属于本领域（含锚点）且同时含左右两侧术语」的文献，
    确保比较基准与统计层一致。
    """
    anchor_term, anchor_cat = anchor
    reg = SourceRegistry(verbose=False)
    reg.sources = [s for s in reg.sources if s.name in VERIFY_SOURCES]

    seen: dict[str, object] = {}
    queries_tried: list[str] = []

    for q in build_verify_queries(gap, anchor_term):
        queries_tried.append(q)
        try:
            papers, _ = reg.fetch(q, per_source=per_source)
        except Exception:
            continue
        for p in papers:
            seen[paper_id(p)] = p
        time.sleep(0.4)

    index = domain.term_index()
    left_pats = patterns_for(index, gap["left_category"], gap["left_term"])
    right_pats = patterns_for(index, gap["right_category"], gap["right_term"])
    anchor_pats = patterns_for(index, anchor_cat, anchor_term) if anchor_term else []
    anchor_required = bool(anchor_pats) and anchor_term not in (gap["left_term"], gap["right_term"])

    co_hits: list[dict] = []
    off_domain = 0
    for p in seen.values():
        text = f"{p.title or ''} {p.abstract or ''}"
        if anchor_required and not any(pat.search(text) for pat in anchor_pats):
            off_domain += 1
            continue
        if any(pat.search(text) for pat in left_pats) and any(pat.search(text) for pat in right_pats):
            co_hits.append({
                "title": p.title, "year": p.year,
                "citations": p.citations, "doi": p.doi, "url": p.url,
            })

    co_hits.sort(key=lambda x: (x.get("citations") or -1), reverse=True)
    verdict, supported = judge_verification(len(co_hits), len(seen))

    return {
        "verified": True,
        "domain_anchor": anchor_term,
        "queries": queries_tried,
        "retrieved": len(seen),
        "off_domain_filtered": off_domain,
        "co_hits": len(co_hits),
        "cross_rate": round(len(co_hits) / max(1, len(seen)), 3),
        "co_hit_examples": co_hits[:3],
        "verdict": verdict,
        "supported": supported,
    }


def status_of(verification: dict) -> str:
    """把反证结果映射为结论状态标签。"""
    s = verification.get("supported")
    if s is True:
        return "确认"
    if s is False:
        return "推翻"
    return "存疑"
