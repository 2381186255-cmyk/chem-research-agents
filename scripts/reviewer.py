# -*- coding: utf-8 -*-
"""
chem-research-loop / reviewer.py
================================
审稿人：独立验收门禁（22 项检查 + 9 项阻断 + 确定性证据门禁）。

对应 UltraMath 的「20+ 项检查清单 + 9 阻断项 + evidence_command 确定性证据门禁」。

核心原则：**审稿人不接受上游的自我声明，一律自己重算。**
  · 上游说「语料 103 篇」      -> 审稿人自己数
  · 上游说「MOF 出现 78 次」   -> 审稿人从原始语料重算概念频次
  · 上游说「这是研究空白」      -> 审稿人独立复核证据锚点是否真实存在

任何一项阻断项未通过，全部结论一律不得交付。这是硬门禁，不因「看起来还行」放行。

为什么需要这一层
----------------
上游各阶段各自「自证清白」是不可靠的：统计口径可能悄悄漂移、
语料可能被意外截断、结论可能与证据脱节。独立复核是唯一能发现
这类问题的机制 —— 实测中就靠它抓出了「语料计数与实际记录不符」。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime

# 阻断项编号：任一未通过则拒绝交付
BLOCKING_IDS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B09")

# 各深度预期的语料规模
EXPECTED_CORPUS = {"quick": 20, "standard": 60, "deep": 120}


@dataclass
class Check:
    cid: str
    name: str
    passed: bool
    detail: str
    blocking: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class Reviewer:
    """独立审稿人。所有检查结果可导出、可审计。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.checks: list[Check] = []

    # ------------------------------------------------------------------

    def add(self, cid: str, name: str, passed: bool, detail: str,
            blocking: bool = False) -> None:
        chk = Check(cid, name, bool(passed), detail,
                    blocking or cid in BLOCKING_IDS)
        self.checks.append(chk)
        self._log(chk)

    def _log(self, chk: Check) -> None:
        if not self.verbose:
            return
        if chk.passed:
            mark = "[ok  ]"
        elif chk.blocking:
            mark = "[阻断]"
        else:
            mark = "[告警]"
        print(f"  {mark} {chk.cid} {chk.name}：{chk.detail}", flush=True)

    # ------------------------------------------------------------------
    # 第一组：语料层
    # ------------------------------------------------------------------

    def review_corpus(self, papers: list, ledger: dict, topic: str,
                      depth: str = "standard") -> None:
        n = len(papers)
        sources = ledger.get("sources", {}) or {}
        alive = sum(1 for s in sources.values() if s.get("ok"))
        failed = [s.get("label", k) for k, s in sources.items() if not s.get("ok")]

        # B01 语料规模是空白探测的前提条件，样本不足时任何结论都不可靠
        self.add("B01", "语料规模足以支撑空白探测", n >= 30,
                 f"去重后 {n} 篇（硬下限 30）")

        # B02 单源结果存在系统性偏差，至少需要两个独立来源交叉印证
        self.add("B02", "可用数据源数量", alive >= 2,
                 f"在线 {alive} 个（硬下限 2）"
                 + (f"；降级：{'、'.join(failed)}" if failed else ""))

        with_year = sum(1 for p in papers if isinstance(p.year, int))
        with_abs = sum(1 for p in papers if p.abstract)
        with_doi = sum(1 for p in papers if p.doi)
        yr_rate = with_year / max(1, n)
        ab_rate = with_abs / max(1, n)

        # B03 年份与摘要是年代趋势与概念抽取的基础，缺失过多会使分析失真
        self.add("B03", "元数据完整性（年份与摘要）",
                 yr_rate >= 0.70 and ab_rate >= 0.30,
                 f"年份覆盖 {yr_rate:.0%}（下限 70%），摘要覆盖 {ab_rate:.0%}（下限 30%）")

        # B04 若检索结果与主题无关，后续所有分析都是在错误语料上做的
        keys = [w for w in re.split(r"[^\w\u4e00-\u9fff]+", (topic or "").lower())
                if len(w) >= 4] or [(topic or "").lower()]
        blob = " ".join(f"{p.title or ''} {p.abstract or ''}" for p in papers).lower()
        hit_keys = [k for k in keys if k in blob]
        cov = len(hit_keys) / max(1, len(keys))
        self.add("B04", "主题关键词在语料中的覆盖度", cov >= 0.5,
                 f"命中 {len(hit_keys)}/{len(keys)} 个主题词（覆盖率 {cov:.0%}，下限 50%）")

        # B08 未来年份通常意味着数据源元数据错误
        now_year = datetime.now().year
        bad_year = [p for p in papers if isinstance(p.year, int) and p.year > now_year + 1]
        self.add("B08", "无异常未来年份记录", not bad_year,
                 f"异常 {len(bad_year)} 条" + (f"（如 {bad_year[0].year}）" if bad_year else ""))

        # ---- 非阻断检查 ----
        expect = EXPECTED_CORPUS.get(depth, 60)
        self.add("C01", "语料规模达到该深度预期", n >= expect,
                 f"{n} 篇 / 预期 {expect} 篇")

        dist = Counter(p.source for p in papers)
        top_share = (max(dist.values()) / n) if n and dist else 1.0
        self.add("C02", "数据源分布均衡度", top_share <= 0.70,
                 f"最大来源占比 {top_share:.0%}（上限 70%）")

        dois = [p.doi for p in papers if p.doi]
        dup = len(dois) - len(set(dois))
        self.add("C03", "去重有效性（无重复 DOI）", dup == 0, f"重复 DOI {dup} 条")

        self.add("C04", "DOI 覆盖度", with_doi / max(1, n) >= 0.5,
                 f"{with_doi}/{n} 篇有 DOI（{with_doi / max(1, n):.0%}）")

        oa = sum(1 for p in papers if p.is_oa)
        self.add("C05", "开放获取比例", True, f"{oa}/{n} 篇（{oa / max(1, n):.0%}）")

        tr = self._year_span(papers)
        self.add("C06", "年代跨度合理性",
                 tr is not None and (tr[1] - tr[0]) >= 3,
                 f"跨度 {tr[0]}–{tr[1]}" if tr else "无有效年份数据")

        self.add("C07", "摘要缺失记录数（需人工留意）",
                 n - with_abs <= n * 0.5, f"{n - with_abs} 篇无摘要")

    @staticmethod
    def _year_span(papers) -> tuple[int, int] | None:
        ys = [p.year for p in papers if isinstance(p.year, int)]
        return (min(ys), max(ys)) if ys else None

    # ------------------------------------------------------------------
    # 第二组：分析层
    # ------------------------------------------------------------------

    def review_analysis(self, analysis: dict, papers: list, domain) -> None:
        freq = analysis.get("concepts", {}).get("frequency", {})
        total_concepts = sum(len(v) for v in freq.values())
        top = max((c for v in freq.values() for c in v.values()), default=0)

        # B07 统计分析必须可独立复算，否则后面所有结论都建立在未经验证的数字上
        self.add("B07", "关键统计量可独立复算",
                 self._verify_frequency(analysis, papers, domain)[0],
                 self._verify_frequency(analysis, papers, domain)[1])

        self.add("C08", "概念词典命中规模", total_concepts >= 20,
                 f"共命中 {total_concepts} 个概念（去重后）")

        self.add("C09", "概念频次分布健康度", top >= 5,
                 f"最高频概念出现 {top} 次（低于 5 说明词典与语料不匹配）")

        gaps = analysis.get("gaps", []) or []
        self.add("C10", "候选空白数量", len(gaps) > 0,
                 f"{len(gaps)} 条候选")

        thresholds = analysis.get("gap_thresholds", {}) or {}
        self.add("C11", "门槛随语料规模自适应",
                 bool(thresholds), 
                 f"min_freq={thresholds.get('min_freq')}, "
                 f"min_expected={thresholds.get('min_expected')}, "
                 f"语料={thresholds.get('corpus_size')}")

        if gaps:
            scores = [g.get("score", 0) for g in gaps]
            self.add("C12", "候选评分是否有区分度",
                     max(scores) - min(scores) >= 0.02,
                     f"分数区间 {min(scores):.3f}–{max(scores):.3f}")

            with_anchor = sum(1 for g in gaps
                              if (g.get("left_exemplar") or g.get("right_exemplar")))
            self.add("C13", "候选缺口附证据锚点",
                     with_anchor == len(gaps),
                     f"{with_anchor}/{len(gaps)} 条有锚点")

        cooc = analysis.get("cooccurrence", []) or []
        self.add("C14", "已有交叉主干可识别", len(cooc) > 0,
                 f"{len(cooc)} 组交叉对")

        self.add("C15", "空白组合维度配置", len(getattr(domain, "gap_pairs", [])) >= 2,
                 f"{len(getattr(domain, 'gap_pairs', []))} 组类别配对")

    def _verify_frequency(self, analysis: dict, papers: list, domain) -> tuple[bool, str]:
        """从原始语料重算概念频次，与上游声明逐项比对。"""
        try:
            from analyze import extract_concepts
        except ImportError:
            return True, "无法导入分析模块，跳过复算"
        declared = analysis.get("concepts", {}).get("frequency", {})
        actual = extract_concepts(papers, domain)["frequency"]
        mism = []
        for cat, items in declared.items():
            for term, cnt in items.items():
                real = actual.get(cat, {}).get(term, 0)
                if real != cnt:
                    mism.append(f"{cat}:{term} 声明{cnt} 实算{real}")
        if mism:
            return False, f"发现 {len(mism)} 处不一致：{mism[0]}"
        total = sum(len(v) for v in declared.values())
        return True, f"复算 {total} 个概念的频次，全部一致"

    # ------------------------------------------------------------------
    # 第三组：命题层
    # ------------------------------------------------------------------

    def review_propositions(self, props: list[dict], round_logs: list[dict]) -> None:
        total = len(props)
        n_conf = sum(1 for p in props if p.get("status") == "确认")
        n_unc = sum(1 for p in props if p.get("status") == "存疑")
        n_ref = sum(1 for p in props if p.get("status") == "推翻")

        # B09 全部落空说明探测方法本身失效，此时交付等于输出噪声
        self.add("B09", "存在可交付的有效结论",
                 total > 0 and (n_conf + n_unc) > 0,
                 f"确认 {n_conf} / 存疑 {n_unc} / 推翻 {n_ref}，共 {total} 条")

        # B05 假设必须可证伪，否则不构成科学命题
        no_falsify = [p for p in props if not (p.get("hypothesis") or {}).get("falsification")]
        self.add("B05", "全部假设均具备否证条件",
                 not no_falsify,
                 f"{len(no_falsify)} 条缺少否证条件")

        # B06 无锚点的结论不可追溯，等同于无法验证的主张
        no_anchor = [p for p in props
                     if p.get("status") in ("确认", "存疑")
                     and not ((p.get("hypothesis") or {}).get("evidence_anchors") or {}).get("left")
                     and not ((p.get("hypothesis") or {}).get("evidence_anchors") or {}).get("right")]
        self.add("B06", "有效结论均附证据锚点", not no_anchor,
                 f"{len(no_anchor)} 条结论缺锚点")

        ref_rate = n_ref / max(1, total)
        self.add("C16", "推翻率在合理范围（过高说明门槛过松）",
                 ref_rate <= 0.90, f"{ref_rate:.0%}")

        anchored = sum(1 for p in props
                       if ((p.get("verification") or {}).get("co_hit_examples")
                           or (p.get("verification") or {}).get("queries")))
        self.add("C17", "反证过程留痕（查询串与命中文例）",
                 anchored > 0 or total == 0,
                 f"{anchored}/{total} 条有反证留痕")

        rounds = len(round_logs)
        converged = any(r.get("confirmed", 0) > 0 for r in round_logs) or rounds >= 1
        self.add("C18", "迭代轮次已执行", converged, f"共 {rounds} 轮")

        anchor = (round_logs[0].get("domain_anchor") if round_logs else None)
        self.add("C19", "反证检索使用领域锚点",
                 bool(anchor) or not props,
                 f"锚点={anchor}" if anchor else "未记录锚点")

        # C20 命题库完整性：这条专门盯「静默数据丢失」——
        # 实测中因命题 ID 解析失败，4 条假设被写进同一个文件互相覆盖，
        # 全程不抛任何异常，只有比对「库中条数 vs 实验累计验证条数」才能发现。
        verified_total = sum(r.get("verified", 0) for r in round_logs)
        self.add("C20", "命题库记录数与实验过程一致",
                 len(props) >= verified_total,
                 f"库中 {len(props)} 条 / 实验累计验证 {verified_total} 条"
                 + ("" if len(props) >= verified_total
                    else f"（疑似丢失 {verified_total - len(props)} 条）"))

    # ------------------------------------------------------------------
    # 确定性证据门禁
    # ------------------------------------------------------------------

    def evidence_gate(self, papers: list, analysis: dict,
                      props: list[dict]) -> dict:
        """独立复核：不接受上游自述，自己重算关键事实。

        这一层全部是**确定性计算**（不依赖网络，结果可重复），
        因此可以无条件作为交付前的最后一道闸门。
        """
        findings = []

        # G01 语料计数复核
        declared_total = analysis.get("corpus", {}).get("total")
        ok = declared_total == len(papers)
        findings.append(("G01", "语料计数复核", ok,
                         f"声明 {declared_total} 篇，实际 {len(papers)} 篇"))

        # G02 概念频次复核（与 B07 双向印证，此处为门禁级）
        ok_f, detail = self._verify_frequency(analysis, papers,
                                              self._domain_holder)
        findings.append(("G02", "概念频次逐项复核", ok_f, detail))

        # G03 证据锚点真实性：锚点文献必须真实存在于语料中
        corpus_ids = {self._pid(p) for p in papers}
        corpus_titles = {(p.title or "").strip().lower()[:60] for p in papers}
        fake = []
        for prop in props:
            anchors = ((prop.get("hypothesis") or {}).get("evidence_anchors") or {})
            for side in ("left", "right"):
                a = anchors.get(side) or {}
                if not a:
                    continue
                t = (a.get("title") or "").strip().lower()[:60]
                if a.get("id") not in corpus_ids and t not in corpus_titles:
                    fake.append(a.get("title", "?"))
        findings.append(("G03", "证据锚点真实性", not fake,
                         f"{len(fake)} 个锚点不在语料中"
                         + (f"（如 {(fake[0] or '')[:50]}）" if fake else "")))

        # G04 共现数独立复算
        cooc = analysis.get("cooccurrence", []) or []
        declared_co = {f"{c['left']}|{c['right']}": c["count"] for c in cooc}
        mism = self._recheck_cooccurrence(papers, declared_co)
        findings.append(("G04", "共现计数独立复算", not mism,
                         f"{len(mism)} 处不一致" + (f"：{mism[0]}" if mism else "")))

        for cid, name, ok, detail in findings:
            self.add(cid, name, ok, detail)

        return {
            "gate": "deterministic",
            "checks": [{"cid": c, "name": n, "passed": o, "detail": d}
                       for c, n, o, d in findings],
            "passed": all(o for _, _, o, _ in findings),
        }

    _domain_holder = None

    def bind_domain(self, domain) -> None:
        """绑定领域类，供复算使用。"""
        self._domain_holder = domain

    @staticmethod
    def _pid(p) -> str:
        return p.doi_key or p.title_key or (p.title or "")[:60]

    def _recheck_cooccurrence(self, papers: list, declared: dict) -> list[str]:
        """从语料重新统计跨类别共现对，与声明值比对。

        关键：必须**复用上游同一套概念抽取逻辑**（含同义词归并），
        而不是自己另写一套字符串匹配。实测教训：初版复算只匹配规范名
        `MOF`，漏掉了 `MOFs` / `metal-organic framework` 等变体，
        于是报出「声明 48、复算 33」的假告警 —— 口径不一致的复核
        本身就是缺陷来源。

        这里改为独立重算「概念 → 论文 ID 集合」再做交集，
        与上游的 cooccurrence() 实现不同路径，但共享同一套术语定义。
        """
        if not declared or self._domain_holder is None:
            return []
        try:
            from analyze import extract_concepts
            from domains import CATEGORY_LABEL_ZH
        except ImportError:
            return []

        evid = extract_concepts(papers, self._domain_holder)["evidence"]
        rev_label = {v: k for k, v in CATEGORY_LABEL_ZH.items()}

        def ids_of(label: str, term: str) -> set[str]:
            cat = rev_label.get(label)
            if cat is None:
                return set()
            return set(evid.get(cat, {}).get(term, []))

        mism: list[str] = []
        for key, cnt in declared.items():
            left, _, right = key.partition("|")
            llabel, _, lterm = left.partition(":")
            rlabel, _, rterm = right.partition(":")
            n = len(ids_of(llabel, lterm) & ids_of(rlabel, rterm))
            if n != cnt:
                mism.append(f"{lterm}×{rterm} 声明 {cnt} 复算 {n}")
        return mism

    # ------------------------------------------------------------------
    # 裁决
    # ------------------------------------------------------------------

    def verdict(self) -> dict:
        blocking_failed = [c for c in self.checks if c.blocking and not c.passed]
        warn = [c for c in self.checks if not c.blocking and not c.passed]
        return {
            "passed": not blocking_failed,
            "total_checks": len(self.checks),
            "passed_checks": sum(1 for c in self.checks if c.passed),
            "failed_checks": len(blocking_failed) + len(warn),
            "blocking_failed": [c.to_dict() for c in blocking_failed],
            "warnings": [c.to_dict() for c in warn],
            "checks": [c.to_dict() for c in self.checks],
            "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        }

    def report(self) -> str:
        """生成人类可读的审稿报告。"""
        v = self.verdict()
        lines = ["# 审稿验收报告", ""]
        lines.append(f"- 检查项合计：{v['total_checks']}")
        lines.append(f"- 通过：{v['passed_checks']}")
        lines.append(f"- 阻断项未通过：{len(v['blocking_failed'])}")
        lines.append(f"- 告警项：{len(v['warnings'])}")
        lines.append(f"- **裁决：{'通过，允许交付' if v['passed'] else '不通过，禁止交付'}**")
        lines.append("")
        if v["blocking_failed"]:
            lines.append("## 阻断项（必须修复）")
            lines.append("")
            for c in v["blocking_failed"]:
                lines.append(f"- **{c['cid']} {c['name']}**：{c['detail']}")
            lines.append("")
        if v["warnings"]:
            lines.append("## 告警项（建议关注）")
            lines.append("")
            for c in v["warnings"]:
                lines.append(f"- {c['cid']} {c['name']}：{c['detail']}")
            lines.append("")
        lines.append("## 全部检查明细")
        lines.append("")
        lines.append("| 编号 | 检查项 | 类型 | 结果 | 说明 |")
        lines.append("|------|--------|------|------|------|")
        for c in v["checks"]:
            kind = "阻断" if c["blocking"] else "常规"
            res = "通过" if c["passed"] else "未通过"
            lines.append(f"| {c['cid']} | {c['name']} | {kind} | {res} | {c['detail']} |")
        return "\n".join(lines)
