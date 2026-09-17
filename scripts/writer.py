# -*- coding: utf-8 -*-
"""
chem-research-loop / writer.py
==============================
综述作家：把知识库里的证据组装成一份正式研究报告。

对应 UltraMath 的「作家」角色 —— 只负责把已验证的材料组织成可交付文档，
不新增任何未经证据支持的说法。

报告的三条硬约束
----------------
1. **每条结论必须带置信度标签**（A 已验证 / B 待判读 / C 已排除）
2. **每条结论必须带证据锚点**（可回溯到具体文献）
3. **必须显式声明局限**，不掩盖「本次未发现空白」这类结论
"""

from __future__ import annotations

from datetime import datetime

STATUS_TO_GRADE = {
    "确认": ("A", "已验证", "本领域精查零命中，可作为重点投入方向"),
    "存疑": ("B", "待人工判读", "交叉稀疏但非零，建议先做小规模验证"),
    "推翻": ("C", "已排除", "本领域已有交叉研究，不建议作为切入点"),
}


def _norm(t: str) -> str:
    return (t or "").replace("|", "/")


def compose_report(*, topic: str, domain, depth: str, kb=None,
                   papers: list, analysis: dict, props: list[dict],
                   review: dict, run_log: dict) -> str:
    """生成完整研究报告。"""
    L: list[str] = []
    A = L.append
    st = (kb.state if kb else {}) or {}
    corpus = analysis.get("corpus", {})
    trend = analysis.get("year_trend", {})

    # ---------------- 封面与摘要 ----------------
    A(f"# 研究报告：{topic}")
    A("")
    A(f"**研究方向** {domain.name_zh}　|　**调研深度** {depth}　|　"
      f"**生成时间** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    A("")
    A("## 摘要")
    A("")
    n_conf = sum(1 for p in props if p.get("status") == "确认")
    n_unc = sum(1 for p in props if p.get("status") == "存疑")
    n_ref = sum(1 for p in props if p.get("status") == "推翻")
    span = trend.get("span")
    A(f"本次调研从 {corpus.get('total', 0)} 篇去重文献出发"
      f"（数据源：{'、'.join(corpus.get('by_source', {}).keys()) or '未知'}），"
      f"时间跨度 {f'{span[0]}–{span[1]}' if span else '未知'}，"
      f"中位年份 {trend.get('median_year', '未知')}，"
      f"近三年文献占比 {trend.get('recent_share', 0):.0%}。")
    A("")
    A(f"经过概念结构分析与**定向反证检索**双重筛选，共形成 {len(props)} 条候选切入点："
      f"其中 **{n_conf} 条通过反证验证（A 级）**、{n_unc} 条待人工判读（B 级）、"
      f"{n_ref} 条被证据推翻（C 级）。")
    A("")
    if review and not review.get("passed"):
        A("> **注意**：本次结果未通过验收门禁，结论仅供内部参考，不建议对外使用。")
        A("")
    A("---")
    A("")

    # ---------------- 一、领域概况 ----------------
    A("## 一、领域概况")
    A("")
    A("### 1.1 文献年代分布")
    A("")
    counts = trend.get("counts", {}) or {}
    if counts:
        A("| 年份 | 文献数 |")
        A("|------|--------|")
        for y, c in counts.items():
            A(f"| {y} | {c} |")
        A("")
        A(f"近三年（{trend.get('recent_window', 3)} 年窗口）文献占比 "
          f"**{trend.get('recent_share', 0):.0%}**，"
          + ("表明该领域仍处于活跃上升期。" if trend.get("recent_share", 0) >= 0.3
             else "表明该领域已趋于成熟或热点转移。"))
        A("")

    A("### 1.2 数据来源分布")
    A("")
    A("| 数据源 | 收录条数 | 占比 |")
    A("|--------|----------|------|")
    total = max(1, corpus.get("total", 1))
    for src, c in (corpus.get("by_source", {}) or {}).items():
        A(f"| {src} | {c} | {c / total:.0%} |")
    A("")
    A(f"其中含摘要 {corpus.get('with_abstract', 0)} 篇、"
      f"开放获取 {corpus.get('open_access', 0)} 篇、"
      f"带 DOI {corpus.get('with_doi', 0)} 篇。")
    A("")

    # ---------------- 二、研究脉络 ----------------
    A("## 二、研究脉络")
    A("")
    A("### 2.1 奠基性文献（按被引排序）")
    A("")
    A("| # | 年份 | 被引 | 标题 | 来源 |")
    A("|---|------|------|------|------|")
    for i, p in enumerate(analysis.get("top_papers", [])[:10], 1):
        A(f"| {i} | {p.get('year') or '—'} | {p.get('citations') if p.get('citations') is not None else '—'} "
          f"| {_norm(p.get('title'))[:88]} | {p.get('source', '')} |")
    A("")
    A("### 2.2 概念分布")
    A("")
    A("| 类别 | 高频概念（括号内为出现篇数） |")
    A("|------|------------------------------|")
    for cat, items in (analysis.get("concept_summary", {}) or {}).items():
        if items:
            top = list(items.items())[:10]
            A(f"| {cat} | " + "、".join(f"{t}({c})" for t, c in top) + " |")
    A("")
    A("### 2.3 已有的交叉主干")
    A("")
    A("下表列出语料中已被反复研究的「概念组合」，代表该领域**已经拥挤**的方向：")
    A("")
    A("| 概念 A | 概念 B | 共现篇数 |")
    A("|--------|--------|----------|")
    for c in (analysis.get("cooccurrence", []) or [])[:15]:
        A(f"| {c['left']} | {c['right']} | {c['count']} |")
    A("")

    # ---------------- 三、候选切入点 ----------------
    A("## 三、候选切入点（按置信度分级）")
    A("")
    A("每条候选均经过**定向反证检索**：主动构造精准查询去打权威数据库，"
      "用真实结果检验它是否真的是空白。")
    A("")
    A("| 等级 | 含义 | 建议行动 |")
    A("|------|------|----------|")
    for _, (g, name, action) in sorted(STATUS_TO_GRADE.items(), key=lambda kv: kv[1][0]):
        A(f"| {g} 级 {name} | {action} | — |")
    A("")

    ordered = sorted(props, key=lambda p: (STATUS_TO_GRADE.get(p.get("status"), ("Z",))[0],
                                           -(p.get("score") or 0)))
    for i, prop in enumerate(ordered, 1):
        status = prop.get("status", "存疑")
        grade, gname, _ = STATUS_TO_GRADE.get(status, ("?", "未知", ""))
        hyp = prop.get("hypothesis", {}) or {}
        ver = prop.get("verification", {}) or {}
        A(f"### 切入点 {i}　【{grade} 级 · {gname}】")
        A("")
        A(f"- **组合**：`{prop.get('left_term')}`（{prop.get('left_category')}）"
          f" × `{prop.get('right_term')}`（{prop.get('right_category')}）")
        A(f"- **语料内统计**：左侧 {prop.get('left_freq')} 篇、右侧 {prop.get('right_freq')} 篇、"
          f"实际共现 {prop.get('cooccur')} 篇、随机期望 {prop.get('expected')} 篇")
        if prop.get("score") is not None:
            A(f"- **综合评分**：{prop.get('score'):.3f}"
              f"（新颖性 {prop.get('novelty')} / 证据 {prop.get('evidence')} / "
              f"可行性 {prop.get('feasibility')} / 动量 {prop.get('momentum')}）")
        A(f"- **反证结论**：{ver.get('verdict', '未执行')}")
        if ver:
            A(f"- **反证过程**：检索 {ver.get('retrieved', 0)} 篇，"
              f"领域外滤除 {ver.get('off_domain_filtered', 0)} 篇，"
              f"本领域交叉 {ver.get('co_hits', 0)} 篇"
              f"（交叉密度 {ver.get('cross_rate', 0):.0%}）")
            q = ver.get("queries") or []
            if q:
                A(f"- **检索式**：{' ／ '.join(f'`{x}`' for x in q)}")
        if hyp.get("statement"):
            A(f"- **可检验假设**：{hyp['statement']}")
        if hyp.get("research_question"):
            A(f"- **研究问题**：{hyp['research_question']}")
        if hyp.get("falsification"):
            A(f"- **否证条件**：{hyp['falsification']}")
        anchors = hyp.get("evidence_anchors", {}) or {}
        for side, label in (("left", "左侧"), ("right", "右侧")):
            a = anchors.get(side) or {}
            if a.get("title"):
                A(f"- **{label}证据锚点**：{_norm(a['title'])[:80]}"
                  f"（{a.get('year') or '—'}，被引 {a.get('citations') if a.get('citations') is not None else '—'}）")
        for ex in (ver.get("co_hit_examples") or [])[:2]:
            A(f"  - 反证命中文例：{_norm(ex.get('title'))[:80]}"
              f"（{ex.get('year') or '—'}）")
        A("")

    # ---------------- 四、审稿验收 ----------------
    A("## 四、审稿验收结论")
    A("")
    if review:
        A(f"- 检查项合计：**{review.get('total_checks', 0)}** 项")
        A(f"- 通过：**{review.get('passed_checks', 0)}** 项")
        A(f"- 阻断项未通过：{len(review.get('blocking_failed', []))} 项")
        A(f"- 告警项：{len(review.get('warnings', []))} 项")
        A(f"- **裁决：{'通过，允许交付' if review.get('passed') else '不通过，禁止交付'}**")
        A("")
        bf = review.get("blocking_failed") or []
        if bf:
            A("**未通过的阻断项：**")
            A("")
            for c in bf:
                A(f"- {c['cid']} {c['name']}：{c['detail']}")
            A("")
        warns = review.get("warnings") or []
        if warns:
            A("**告警项：**")
            A("")
            for c in warns:
                A(f"- {c['cid']} {c['name']}：{c['detail']}")
            A("")
        A("验收采用**确定性证据门禁**：审稿人独立重算语料计数、概念频次、"
          "共现统计与证据锚点真实性，不接受上游的自我声明。")
        A("")

    # ---------------- 五、方法与可信度 ----------------
    A("## 五、方法与可信度声明")
    A("")
    A("### 5.1 工作流")
    A("")
    A("文献侦察 → 知识结构化 → 空白探测 → 假设生成 → **反证实验** → 审稿验收 → 报告产出")
    A("")
    A("### 5.2 关键方法")
    A("")
    A("- **跨源去重**：DOI 与标题双指纹联合判定，解决 arXiv 预印本无 DOI 的漏合并问题")
    A("- **同义词归并**：MOF / MOFs / metal-organic framework 归为同一概念，避免频次稀释")
    A("- **结构洞探测**：以「两概念独立时的期望共现」为基准，识别显著低共现的跨类别组合")
    A("- **定向反证**：对每条候选构造带领域锚点的精准检索，用真实结果检验其是否为假空白")
    A("- **交叉密度判据**：以比例而非绝对篇数判定，避免小样本误判")
    A("")
    A("### 5.3 局限")
    A("")
    A("1. 概念粒度以领域通用术语为主，无法识别细颗粒度命题，此类空白需人工研判补足")
    A("2. 分析仅基于标题与摘要，付费墙文献若缺摘要则贡献有限")
    A("3. 语料来自有限检索式，是领域的采样而非全样本，「零共现」需谨慎解读")
    A("4. 成熟领域通常难以产出 A 级结论，这是方法论的诚实结果，不应通过放宽标准制造结论")
    A("")

    # ---------------- 附录 ----------------
    A("## 附录：运行台账")
    A("")
    if kb:
        A(f"- 工作区：`{kb.root}`")
        A(f"- 已完成阶段：{'、'.join((st.get('stages') or {}).keys()) or '—'}")
    it = (run_log.get("stages", {}) or {}).get("iterate", {}) or {}
    A(f"- 反证领域锚点：`{it.get('domain_anchor', '—')}`")
    A(f"- 迭代轮数：{it.get('rounds_run', 0)}")
    for r in it.get("round_logs", []) or []:
        A(f"  - 第 {r.get('round')} 轮：验证 {r.get('verified')} 条 → "
          f"确认 {r.get('confirmed')} / 推翻 {r.get('refuted')} / 存疑 {r.get('uncertain')}")
    return "\n".join(L)
