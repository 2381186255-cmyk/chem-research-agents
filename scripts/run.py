# -*- coding: utf-8 -*-
"""
chem-research-loop / run.py
===========================
命令行入口（V2）：调用主控执行完整科研闭环。

六个角色由主控自动调度，每步完成即落盘，支持断点续跑。

用法
----
  python run.py probe                                  # 数据源健康体检
  python run.py list                                   # 列出四个研究方向
  python run.py --topic "..." --domain materials --depth standard
  python run.py --topic "..." --no-resume              # 强制从头跑
  python run.py --topic "..." --reset review write     # 重置指定阶段后重跑

断点续跑
--------
  同一主题重复执行会自动复用工作区，跳过已完成阶段，不重复消耗抓取次数。
  工作区位于 <workspace>/<主题slug>__<方向>/。

人工干预
--------
  运行期间在工作区写入 control.json：
    {"action": "pause"}     在下一个阶段边界安全停机
    {"action": "abort"}     中止流程
    {"action": "continue"}  恢复
"""

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from domains import list_domains  # noqa: E402
from orchestrator import DEPTH_PRESETS, Orchestrator  # noqa: E402
from sources import SourceRegistry  # noqa: E402


def cmd_probe() -> int:
    reg = SourceRegistry(verbose=True)
    print("=== 数据源健康体检 ===")
    for name, info in reg.probe_all().items():
        print(f"  {info['label']:<24} {info['status']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="化学自主科研闭环：文献侦察 → 知识结构化 → 空白探测 → "
                    "假设生成 → 反证实验 → 审稿门禁 → 报告产出")
    ap.add_argument("--topic", help="研究主题，中英文皆可")
    ap.add_argument("--domain", default="materials", help="研究方向（默认 materials）")
    ap.add_argument("--depth", default="standard", choices=list(DEPTH_PRESETS),
                    help="调研深度，决定语料规模与迭代轮数")
    ap.add_argument("--workspace", default="./workspace",
                    help="工作区根目录，每个主题自动建独立子目录")
    ap.add_argument("--out", default=None, help="[兼容参数] 等价于 --workspace")
    ap.add_argument("--year-from", type=int, default=None, help="只取该年之后的文献")
    ap.add_argument("--max-rounds", type=int, default=None, help="覆盖预设迭代轮数")
    ap.add_argument("--per-source", type=int, default=None, help="覆盖预设单源抓取量")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略历史进度，从头执行全部阶段")
    ap.add_argument("--reset", nargs="*", default=None,
                    help="重置指定阶段后重跑，如 --reset review write")
    ap.add_argument("--list-domains", action="store_true", help="列出全部研究方向")

    argv = sys.argv[1:]
    if argv and argv[0] == "probe":
        return cmd_probe()
    if argv and argv[0] == "list":
        print(list_domains())
        return 0

    args = ap.parse_args(argv)
    if args.list_domains:
        print(list_domains())
        return 0
    if not args.topic:
        ap.print_help()
        print()
        print(list_domains())
        return 1

    try:
        orch = Orchestrator(
            topic=args.topic, domain_key=args.domain, depth=args.depth,
            base_dir=args.out or args.workspace, year_from=args.year_from,
            resume=not args.no_resume, max_rounds=args.max_rounds,
            per_source=args.per_source,
        )
    except ValueError as exc:
        print(f"参数错误：{exc}\n\n{list_domains()}")
        return 2

    if args.reset is not None:
        stages = args.reset or ["scout", "analyze", "hypothesize",
                                "experiment", "review", "write"]
        for s in stages:
            orch.kb.reset_stage(s)
            print(f"  已重置阶段：{s}")
        orch.kb.clear_control()

    summary = orch.run()

    if summary.get("aborted"):
        return 5
    if not summary.get("review_passed", True):
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
