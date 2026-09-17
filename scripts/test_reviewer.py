# -*- coding: utf-8 -*-
"""
chem-research-loop / test_reviewer.py
=====================================
用真实运行产物验证审稿门禁。

不是模拟数据 —— 直接加载上一轮 e2e-standard 的产物，
让审稿人独立复核，检验门禁能否真正发现（或确认）问题。

用法：python test_reviewer.py <产物目录>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from domains import resolve_domain  # noqa: E402
from reviewer import Reviewer  # noqa: E402
from sources import Paper  # noqa: E402


def load(outdir: str):
    def j(name):
        with open(os.path.join(outdir, name), encoding="utf-8") as fh:
            return json.load(fh)

    raw = j("papers.json")
    fields = set(Paper.__dataclass_fields__)
    papers = [Paper(**{k: v for k, v in d.items() if k in fields}) for d in raw]
    return papers, j("analysis.json"), j("gaps_verified.json"), j("run_log.json")


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else "../output/e2e-standard"
    papers, analysis, props, log = load(outdir)

    domain = resolve_domain(log.get("domain", "materials"))
    topic = log.get("topic", "")
    depth = log.get("depth", "standard")

    ledgers = log.get("stages", {}).get("scan", {}).get("ledgers", []) or [{}]
    ledger = ledgers[0]

    round_logs = []
    for r in log.get("stages", {}).get("iterate", {}).get("round_logs", []) or []:
        r = dict(r)
        r["domain_anchor"] = log["stages"]["iterate"].get("domain_anchor")
        round_logs.append(r)

    print("=" * 70)
    print(f"  审稿人独立复核 · {topic}（{domain.name_zh}）")
    print(f"  产物目录：{outdir}")
    print(f"  语料 {len(papers)} 篇 / 结论 {len(props)} 条")
    print("=" * 70)

    rv = Reviewer(verbose=True)
    rv.bind_domain(domain)

    print("\n--- 第一组：语料层 ---")
    rv.review_corpus(papers, ledger, topic, depth)

    print("\n--- 第二组：分析层 ---")
    rv.review_analysis(analysis, papers, domain)

    print("\n--- 第三组：命题层 ---")
    rv.review_propositions(props, round_logs)

    print("\n--- 确定性证据门禁（独立重算，不接受上游自述）---")
    gate = rv.evidence_gate(papers, analysis, props)

    v = rv.verdict()
    print("\n" + "=" * 70)
    print(f"  证据门禁：{'通过' if gate['passed'] else '未通过'}")
    print(f"  检查项 {v['passed_checks']}/{v['total_checks']} 通过"
          f"｜阻断未通过 {len(v['blocking_failed'])} 项"
          f"｜告警 {len(v['warnings'])} 项")
    print(f"  裁决：{'通过，允许交付' if v['passed'] else '不通过，禁止交付'}")
    print("=" * 70)

    report = rv.report()
    out = os.path.join(outdir, "review_report.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"\n审稿报告已写出：{out}")

    return 0 if v["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
