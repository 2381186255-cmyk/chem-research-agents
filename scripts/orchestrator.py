# -*- coding: utf-8 -*-
"""
chem-research-loop / orchestrator.py
====================================
主控：六角色编排 + 断点续跑 + 门禁卡点。

六个角色（对应 UltraMath 的专业分工范式）
----------------------------------------
  侦察员 Scout          多源检索、跨源去重、语料治理
  分析师 Analyst        概念抽取、年代趋势、共现网络、空白探测
  假设官 Hypothesizer   把缺口转成可检验假设卡（不执行验证）
  实验员 Experimenter   执行定向反证，逐条给出确认/存疑/推翻
  审稿人 Reviewer       22 项检查 + 9 阻断 + 确定性证据门禁
  作家   Writer         组装正式研究报告

主控职责
--------
  1. 顺序调度角色，每步完成即落盘（断点续跑的依据）
  2. 已完成的阶段自动跳过，避免重复消耗抓取次数
  3. 在阶段边界检查人工干预信号（pause / abort）
  4. 审稿门禁未通过则中止交付，后续阶段不再执行
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from analyze import analyze, find_gaps
from domains import resolve_domain
from kb import ResearchKB, read_json
from reviewer import Reviewer
from sources import Paper, SourceRegistry
from verify import make_hypothesis, pick_domain_anchor, status_of, verify_gap
from visualize import build_visualization
from writer import compose_report

DEPTH_PRESETS = {
    # per_source：每个检索式、每个数据源各抓多少条。
    # 语料规模是空白探测的前提 —— 实测 18 篇语料下空白候选为 0，103 篇才有信号。
    "quick": {"per_source": 6, "n_queries": 2, "max_rounds": 1, "verify_top": 3},
    "standard": {"per_source": 15, "n_queries": 3, "max_rounds": 3, "verify_top": 4},
    "deep": {"per_source": 25, "n_queries": 4, "max_rounds": 5, "verify_top": 6},
}

BANNER = "=" * 68


# ==========================================================================
# 上下文
# ==========================================================================

@dataclass
class Context:
    """贯穿全流程的共享上下文。断点恢复时可从知识库重建。"""

    kb: ResearchKB
    domain: type
    topic: str
    depth: str
    preset: dict
    year_from: int | None = None

    papers: list = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    propositions: list = field(default_factory=list)
    round_logs: list = field(default_factory=list)
    anchor: tuple = ("", "")
    review: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    run_log: dict = field(default_factory=dict)

    def restore_papers(self) -> None:
        """断点恢复：从 Corpus 快照重建 Paper 对象。"""
        raw = read_json(self.kb.root / "Corpus" / "papers.json", default=[]) or []
        fields = set(Paper.__dataclass_fields__)
        self.papers = [Paper(**{k: v for k, v in d.items() if k in fields}) for d in raw]

    def restore_analysis(self) -> None:
        self.analysis = read_json(self.kb.root / "analysis.json", default={}) or {}

    def restore_ledger(self) -> None:
        """断点恢复：重建数据源台账。

        审稿人的 B02 检查（可用数据源数量）依赖 ledger。若不恢复它，
        重跑时审稿人看到的是「在线 0 个」并直接阻断交付 —— 实测中确实
        发生了这次误阻断，机制是对的，缺的是状态恢复。
        """
        meta = read_json(self.kb.root / "Corpus" / "snapshot_meta.json",
                         default={}) or {}
        ledgers = meta.get("ledgers") or []
        if ledgers:
            self.ledger = ledgers[0]
            return
        srcs = {}
        for name, info in (meta.get("sources") or {}).items():
            online = info.get("status") == "在线"
            srcs[name] = {"label": info.get("label", name),
                          "ok": 1 if online else 0,
                          "fail": 0 if online else 1, "last_error": ""}
        self.ledger = {"sources": srcs}

    def restore_propositions(self) -> None:
        self.propositions = self.kb.load_propositions()


# ==========================================================================
# 角色基类
# ==========================================================================

class Stage:
    key = "base"
    name_zh = "基础阶段"
    role = ""

    def run(self, ctx: Context) -> dict:
        raise NotImplementedError

    def _hdr(self, n: int) -> None:
        print(f"\n{BANNER}\n  阶段 {n} · {self.name_zh}"
              f"{f'（{self.role}）' if self.role else ''}\n{BANNER}", flush=True)


# ==========================================================================
# 角色一：侦察员
# ==========================================================================

class ScoutStage(Stage):
    key = "scout"
    name_zh = "文献侦察"
    role = "侦察员"

    def run(self, ctx: Context) -> dict:
        self._hdr(1)
        reg = SourceRegistry(verbose=True)

        print("  [数据源健康体检]", flush=True)
        health = reg.probe_all()
        for name, info in health.items():
            print(f"    {info['label']:<24} {info['status']}", flush=True)
        online = [n for n, i in health.items() if i["status"] == "在线"]
        if not online:
            raise RuntimeError("所有数据源均不可达，无法继续")

        queries = ctx.domain.build_queries(ctx.topic, limit=ctx.preset["n_queries"])
        print(f"\n  [检索主题] {ctx.topic}", flush=True)
        for q in queries:
            print(f"    · {q}", flush=True)

        all_papers: list = []
        ledgers: list[dict] = []
        for q in queries:
            print(f"\n  [检索] {q}", flush=True)
            got, ledger = reg.fetch(q, per_source=ctx.preset["per_source"],
                                    year_from=ctx.year_from)
            all_papers.extend(got)
            ledgers.append(ledger)
            time.sleep(0.5)

        papers = SourceRegistry.dedupe(all_papers)
        print(f"\n  原始命中 {len(all_papers)} 条 → 跨源去重 {len(papers)} 条独一文献",
              flush=True)
        if not papers:
            raise RuntimeError("未检索到任何文献，建议更换主题或放宽年份限制")

        ctx.papers = papers
        ctx.ledger = ledgers[0] if ledgers else {}
        ctx.kb.snapshot_corpus(papers, {
            "topic": ctx.topic, "queries": queries,
            "raw_hits": len(all_papers), "unique_hits": len(papers),
            "sources": health,
            # 保存完整台账：断点恢复时审稿人要用它核对数据源可用数
            "ledgers": ledgers,
        })
        ctx.kb.write_problem("P001", ctx.topic, ctx.domain.key,
                             source="、".join(online))

        return {"queries": len(queries), "raw_hits": len(all_papers),
                "unique_hits": len(papers), "sources_online": len(online)}


# ==========================================================================
# 角色二：分析师
# ==========================================================================

class AnalystStage(Stage):
    key = "analyze"
    name_zh = "知识结构化与空白探测"
    role = "分析师"

    def run(self, ctx: Context) -> dict:
        self._hdr(2)
        result = analyze(ctx.papers, ctx.domain, top_n_papers=10, top_n_gaps=24)
        ctx.analysis = result

        tr = result["year_trend"]
        print(f"  年代跨度 {tr.get('span')}｜中位年份 {tr.get('median_year')}"
              f"｜近三年占比 {tr.get('recent_share')}", flush=True)
        print("\n  高频概念（各类别 Top 6）:", flush=True)
        for cat, items in result["concept_summary"].items():
            top = list(items.items())[:6]
            if top:
                print(f"    {cat:<12} " + "、".join(f"{t}({c})" for t, c in top), flush=True)

        gaps = result.get("gaps", [])
        print(f"\n  候选研究缺口 {len(gaps)} 条:", flush=True)
        for g in gaps[:8]:
            print(f"    [{g['score']:.3f}] {g['left_term']} × {g['right_term']}"
                  f"  共现{g['cooccur']}/期望{g['expected']}  {g['verdict']}", flush=True)

        ctx.kb.write_artifact("analysis.json", result)
        ctx.kb.record_method("共现期望判据", {
            "formula": "expected = freq(left) * freq(right) / N",
            "note": "两概念独立时的期望共现；实际值显著低于期望即为候选缺口",
        })
        ctx.kb.record_method("自适应门槛", {
            "rule": "min_freq 随语料规模缩放：<50→2，50~150→3，150~400→4，>400→5",
            "note": "固定门槛在小语料下会让空白探测直接失效",
        })

        return {"gaps_found": len(gaps), "corpus": result["corpus"]["total"],
                "thresholds": result.get("gap_thresholds")}


# ==========================================================================
# 角色三：假设官
# ==========================================================================

class HypothesizerStage(Stage):
    key = "hypothesize"
    name_zh = "假设生成"
    role = "假设官"

    def run(self, ctx: Context) -> dict:
        self._hdr(3)
        gaps = (ctx.analysis.get("gaps") or [])
        if not gaps:
            print("  无候选缺口，跳过假设生成。", flush=True)
            return {"propositions": 0}

        props = []
        for i, gap in enumerate(gaps):
            hyp = make_hypothesis(gap, ctx.domain, i)
            # 显式把 hypothesis_id 提到顶层：命题库以它为文件名，
            # 深层嵌套取值容易漏（见 kb._pid_of 的实测教训）
            prop = {**gap, "hypothesis_id": hyp["hypothesis_id"],
                    "hypothesis": hyp, "status": "pending",
                    "verification": {}, "phase": "hypothesized"}
            ctx.kb.write_proposition(prop)
            props.append(prop)
            print(f"  [{hyp['hypothesis_id']}] {gap['left_term']} × {gap['right_term']}"
                  f"（分数 {gap['score']:.3f}）", flush=True)

        ctx.propositions = props
        print(f"\n  生成 {len(props)} 条可检验假设，全部附否证条件与证据锚点。",
              flush=True)
        return {"propositions": len(props)}


# ==========================================================================
# 角色四：实验员
# ==========================================================================

class ExperimenterStage(Stage):
    key = "experiment"
    name_zh = "反证实验与迭代"
    role = "实验员"

    def run(self, ctx: Context) -> dict:
        self._hdr(4)
        if not ctx.propositions:
            print("  无命题可验证。", flush=True)
            return {"confirmed": 0, "refuted": 0, "uncertain": 0, "rounds": 0}

        anchor = pick_domain_anchor(ctx.analysis)
        ctx.anchor = anchor
        if anchor[0]:
            print(f"  领域锚点：{anchor[0]}（统一反证检索的领域边界，"
                  f"避免拿全学科文献误判本领域）", flush=True)

        verified_keys = ctx.kb.verified_keys()
        pending = list(ctx.propositions)
        counters = {"确认": 0, "推翻": 0, "存疑": 0}
        max_rounds = ctx.preset["max_rounds"]
        rounds_run = 0

        for rnd in range(1, max_rounds + 1):
            target = min(ctx.preset["verify_top"], len(pending))
            if target == 0:
                print(f"\n  第 {rnd} 轮：待验证队列已空，迭代收敛。", flush=True)
                break
            rounds_run = rnd
            print(f"\n  --- 第 {rnd} 轮：验证 {target} 条假设 ---", flush=True)

            batch = pending[:target]
            pending = pending[target:]
            round_hits = {"确认": 0, "推翻": 0, "存疑": 0}

            for prop in batch:
                key = f"{prop['left_term']}|{prop['right_term']}"
                verified_keys.add(key)
                ctx.kb.add_verified_key(key)

                hyp = prop["hypothesis"]
                print(f"\n  [{hyp['hypothesis_id']}] "
                      f"{prop['left_term']} × {prop['right_term']}", flush=True)
                print(f"      假设：{hyp['statement'][:72]}", flush=True)

                v = verify_gap(prop, ctx.domain, anchor)
                prop["verification"] = v
                prop["status"] = status_of(v)
                round_hits[prop["status"]] += 1
                counters[prop["status"]] += 1

                print(f"      反证：命中 {v['retrieved']} 篇"
                      f"（领域外滤除 {v['off_domain_filtered']} 篇），"
                      f"本领域交叉 {v['co_hits']} 篇 → {v['verdict']}", flush=True)

                if prop["status"] == "确认":
                    ctx.kb.promote_verified(prop)
                elif prop["status"] == "推翻":
                    ctx.kb.record_rejection(prop, v["verdict"])
                else:
                    ctx.kb.write_proposition(prop)
                time.sleep(0.3)

            ctx.round_logs.append({
                "round": rnd, "verified": len(batch),
                "confirmed": round_hits["确认"], "refuted": round_hits["推翻"],
                "uncertain": round_hits["存疑"], "domain_anchor": anchor[0],
            })
            print(f"\n  轮次小结：确认 {round_hits['确认']} / 推翻 {round_hits['推翻']}"
                  f" / 存疑 {round_hits['存疑']}", flush=True)

            # 第 ⑥ 步的「调整」：推翻偏多说明语料覆盖不足，放宽门槛再探一批
            if round_hits["推翻"] > round_hits["确认"] and rnd < max_rounds:
                print("  → 推翻偏多，判定语料覆盖不足；放宽门槛重新探测。", flush=True)
                relaxed = find_gaps(ctx.papers, ctx.domain, ctx.analysis,
                                    min_freq=2, min_expected=0.7, top_n=30)
                extra = []
                for g in relaxed:
                    if f"{g.left_term}|{g.right_term}" in verified_keys:
                        continue
                    d = g.to_dict()
                    h = make_hypothesis(d, ctx.domain,
                                        len(ctx.propositions) + len(extra))
                    d["hypothesis"] = h
                    d["hypothesis_id"] = h["hypothesis_id"]
                    d["status"] = "pending"
                    d["verification"] = {}
                    d["phase"] = "hypothesized"
                    ctx.kb.write_proposition(d)
                    extra.append(d)
                if extra:
                    print(f"  → 放宽后新增 {len(extra)} 条待验证缺口。", flush=True)
                pending = extra + pending
            elif counters["确认"] >= max(2, ctx.preset["verify_top"]):
                print("  → 已获得足量 A 级结论，迭代收敛。", flush=True)
                break

        # 把最终状态同步回上下文
        ctx.propositions = ctx.kb.load_propositions()
        return {
            "rounds": rounds_run,
            "confirmed": counters["确认"],
            "refuted": counters["推翻"],
            "uncertain": counters["存疑"],
            "domain_anchor": anchor[0],
            "round_logs": ctx.round_logs,
        }


# ==========================================================================
# 角色五：审稿人（门禁卡点）
# ==========================================================================

class ReviewStage(Stage):
    key = "review"
    name_zh = "审稿验收（门禁）"
    role = "审稿人"

    def run(self, ctx: Context) -> dict:
        self._hdr(5)
        rv = Reviewer(verbose=True)
        rv.bind_domain(ctx.domain)

        print("\n  [第一组] 语料层", flush=True)
        rv.review_corpus(ctx.papers, ctx.ledger, ctx.topic, ctx.depth)

        print("\n  [第二组] 分析层", flush=True)
        rv.review_analysis(ctx.analysis, ctx.papers, ctx.domain)

        print("\n  [第三组] 命题层", flush=True)
        rv.review_propositions(ctx.propositions, ctx.round_logs)

        print("\n  [确定性证据门禁] 独立重算，不接受上游自述", flush=True)
        gate = rv.evidence_gate(ctx.papers, ctx.analysis, ctx.propositions)

        verdict = rv.verdict()
        ctx.review = verdict
        ctx.kb.write_artifact("review_report.md", rv.report())
        ctx.kb.write_artifact("review_verdict.json", verdict)

        print(f"\n  证据门禁：{'通过' if gate['passed'] else '未通过'}", flush=True)
        print(f"  检查项 {verdict['passed_checks']}/{verdict['total_checks']} 通过"
              f"｜阻断未通过 {len(verdict['blocking_failed'])} 项"
              f"｜告警 {len(verdict['warnings'])} 项", flush=True)
        print(f"  >>> 裁决：{'通过，允许交付' if verdict['passed'] else '不通过，禁止交付'}",
              flush=True)

        return {"review_passed": verdict["passed"],
                "checks": verdict["total_checks"],
                "blocking_failed": len(verdict["blocking_failed"])}


# ==========================================================================
# 角色六：作家
# ==========================================================================

class WriterStage(Stage):
    key = "write"
    name_zh = "报告产出"
    role = "作家"

    def run(self, ctx: Context) -> dict:
        self._hdr(6)
        if ctx.review and not ctx.review.get("passed"):
            print("  审稿门禁未通过，按规约不产出正式报告。", flush=True)
            print("  证据包仍保留在工作区，可修复问题后重跑本阶段。", flush=True)
            return {"report": None, "blocked_by_gate": True}

        text = compose_report(
            topic=ctx.topic, domain=ctx.domain, depth=ctx.depth,
            kb=ctx.kb, papers=ctx.papers, analysis=ctx.analysis,
            props=ctx.propositions, review=ctx.review, run_log=ctx.run_log,
        )
        path = ctx.kb.write_report("research_report.md", text)
        print(f"  报告已写出：{path}", flush=True)
        print(f"  篇幅：{len(text.splitlines())} 行", flush=True)
        return {"report": str(path)}


# ==========================================================================
# 角色七：可视化师
# ==========================================================================

class VisualizeStage(Stage):
    key = "visualize"
    name_zh = "研究可视化"
    role = "可视化师"

    def run(self, ctx: Context) -> dict:
        self._hdr(7)
        if ctx.review and not ctx.review.get("passed"):
            print("  审稿门禁未通过，按规约不产出可视化。", flush=True)
            return {"skipped": True, "blocked_by_gate": True}

        text = build_visualization(ctx.analysis, ctx.topic, ctx.domain.name_zh)
        path = ctx.kb.write_report("visualization.html", text)
        print(f"  可视化已写出：{path}", flush=True)
        print(f"  内容：概念网络 / 共现热力图 / 年代趋势（自包含，可离线打开）",
              flush=True)
        return {"visualization": str(path), "bytes": len(text)}


# ==========================================================================
# 主控
# ==========================================================================

class Orchestrator:
    """科研流程主控。负责角色编排、断点续跑、门禁卡点与人工干预响应。"""

    def __init__(self, topic: str, domain_key: str, depth: str = "standard",
                 base_dir: str = "./workspace", year_from: int | None = None,
                 resume: bool = True, max_rounds: int | None = None,
                 per_source: int | None = None):
        self.domain = resolve_domain(domain_key)
        if self.domain is None:
            raise ValueError(f"未知研究方向：{domain_key}")

        preset = dict(DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"]))
        if max_rounds is not None:
            preset["max_rounds"] = max_rounds
        if per_source is not None:
            preset["per_source"] = per_source

        self.kb = ResearchKB.for_topic(base_dir, topic, self.domain.key, depth)
        self.ctx = Context(kb=self.kb, domain=self.domain, topic=topic,
                           depth=depth, preset=preset, year_from=year_from)
        self.resume = resume
        self.stages: list[Stage] = [
            ScoutStage(), AnalystStage(), HypothesizerStage(),
            ExperimenterStage(), ReviewStage(), WriterStage(), VisualizeStage(),
        ]

    def _restore(self) -> None:
        """断点恢复：重建上下文。

        要恢复的不只是「跑到哪了」，还包括各阶段产出的**上下文数据**。
        实测已两次踩坑：
          · 漏恢复 ledger      -> 审稿人看到「在线 0 个」，误判阻断交付
          · 漏恢复 round_logs  -> 审稿人看不到迭代轮次与领域锚点，误报告警
        教训：每新增一个跨阶段共享的数据，就必须同步补一条恢复逻辑。
        """
        ctx = self.ctx
        if ctx.kb.is_done("scout"):
            ctx.restore_papers()
            ctx.restore_ledger()
        if ctx.kb.is_done("analyze"):
            ctx.restore_analysis()
        if ctx.kb.is_done("hypothesize") or ctx.kb.is_done("experiment"):
            ctx.restore_propositions()
        if ctx.kb.is_done("experiment"):
            meta = ctx.kb.state["stages"].get("experiment", {}).get("meta", {}) or {}
            ctx.round_logs = meta.get("round_logs", []) or []
            anchor_term = meta.get("domain_anchor")
            if anchor_term:
                ctx.anchor = (anchor_term, "")

    def run(self) -> dict:
        ctx = self.ctx
        started = datetime.now()
        ctx.run_log = {
            "topic": ctx.topic, "domain": self.domain.key,
            "depth": ctx.depth, "preset": ctx.preset,
            "workspace": str(self.kb.root), "stages": {},
            "started_at": started.isoformat(timespec="seconds"),
        }

        print(BANNER)
        print(f"  化学自主科研闭环 · {ctx.topic}")
        print(f"  方向 {self.domain.name_zh}｜深度 {ctx.depth}｜"
              f"断点续跑 {'开启' if self.resume else '关闭'}")
        print(f"  工作区 {self.kb.root}")
        print(BANNER, flush=True)

        if self.resume:
            self._restore()
            done = [s.key for s in self.stages if self.kb.is_done(s.key)]
            if done:
                print(f"  检测到历史进度，将跳过已完成阶段：{'、'.join(done)}", flush=True)

        aborted = False
        for stage in self.stages:
            ctl = self.kb.check_control()
            if ctl == "abort":
                print(f"\n  收到人工中止信号，在「{stage.name_zh}」前安全停机。", flush=True)
                print(f"  工作区状态已保存，可稍后重跑同一命令继续。", flush=True)
                aborted = True
                break
            if ctl == "pause":
                print(f"\n  收到人工暂停信号，在「{stage.name_zh}」前安全停机。", flush=True)
                print(f"  将 control.json 改回 continue 后重跑即可继续。", flush=True)
                aborted = True
                break

            if self.resume and self.kb.is_done(stage.key):
                print(f"\n  [跳过] {stage.name_zh} —— 该阶段已完成", flush=True)
                ctx.run_log["stages"][stage.key] = self.kb.state["stages"][stage.key]["meta"]
                continue

            try:
                meta = stage.run(ctx)
            except RuntimeError as exc:
                print(f"\n  阶段「{stage.name_zh}」中止：{exc}", flush=True)
                aborted = True
                break

            ctx.run_log["stages"][stage.key] = meta
            self.kb.mark_done(stage.key, **(meta or {}))

            if stage.key == "review" and not meta.get("review_passed"):
                print("\n  >>> 审稿门禁未通过，后续「报告产出」阶段不再执行。", flush=True)
                print("  >>> 请修复阻断项后重跑（本阶段可用 reset 强制重做）。", flush=True)
                aborted = True
                break

        finished = datetime.now()
        ctx.run_log["finished_at"] = finished.isoformat(timespec="seconds")
        ctx.run_log["duration_sec"] = round((finished - started).total_seconds(), 1)
        self.kb.write_artifact("run_log.json", ctx.run_log)

        print(f"\n{BANNER}")
        if aborted:
            print(f"  流程未完整走完（{ctx.run_log['duration_sec']} 秒），工作区已保存。")
        else:
            print(f"  流程完成，耗时 {ctx.run_log['duration_sec']} 秒。")
        it = ctx.run_log["stages"].get("experiment", {}) or {}
        if it:
            print(f"  结论：确认 {it.get('confirmed', 0)} 条 / "
                  f"存疑 {it.get('uncertain', 0)} 条 / 推翻 {it.get('refuted', 0)} 条")
        print(f"  工作区：{self.kb.root}")
        print(BANNER, flush=True)

        summary = self.kb.summary()
        summary["aborted"] = aborted
        summary["duration_sec"] = ctx.run_log["duration_sec"]
        return summary
