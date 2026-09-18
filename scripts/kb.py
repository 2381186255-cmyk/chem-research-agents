# -*- coding: utf-8 -*-
"""
chem-research-loop / kb.py
==========================
结构化知识库 + 断点状态管理。

仿 Vibe Math V3 的「论文式知识库」范式：把研究过程沉淀为可复用资产，
而不是跑完即弃的中间产物。

  Problems/   研究问题清单（问题 + 依赖 + 来源）
  Progress/   研究日志（append-only JSONL，断点续跑依据）
  Propos/     命题库（每条假设及其验证状态）
  Methods/    方法库（可复用的分析框架与判据）
  Verified/   可信结论（通过审稿门禁）
  Rejected/   已推翻结论（防止后续重复踩坑）
  Corpus/     语料快照（可回溯）
  Reports/    最终交付报告

三条设计原则
------------
1. **原子写入**：先写临时文件再替换，避免中断留下半截损坏文件。
2. **state.json 是断点续跑的唯一真相来源**：任何阶段完成都必须先落盘再继续。
3. **知识可积累**：Rejected/ 记录被推翻的假设，下次同类研究不再走弯路 ——
   这是「方法论沉淀」最直接的体现。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def slugify(text: str, maxlen: int = 40) -> str:
    """把研究主题转成安全的目录名（保留中文，其余非字母数字转连字符）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (text or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:maxlen] or "task"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写同目录临时文件，再 replace 覆盖目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".swp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


class ResearchKB:
    """结构化研究知识库。

    典型用法：
        kb = ResearchKB.for_topic("/path/to/workspace", "MOF CO2 capture", "materials")
        kb.mark_done("scout", unique_hits=103)
        if not kb.is_done("analyze"):
            ...
    """

    DIRS = (
        "Problems", "Progress", "Propos", "Methods",
        "Verified", "Rejected", "Corpus", "Reports",
    )

    def __init__(self, root: str | os.PathLike, task_id: str | None = None):
        self.root = Path(root)
        self.task_id = task_id or slugify(self.root.name)
        for d in self.DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self._state: dict | None = None

    # ------------------------------------------------------------------
    # 构造入口
    # ------------------------------------------------------------------

    @classmethod
    def for_topic(cls, base_dir: str | os.PathLike, topic: str,
                  domain: str, depth: str) -> "ResearchKB":
        """按主题生成独立工作区（多任务并行隔离）。"""
        task_id = f"{slugify(topic, 32)}__{domain}"
        kb = cls(Path(base_dir) / task_id, task_id=task_id)
        st = kb.state
        if not st.get("topic"):
            st.update({
                "task_id": task_id,
                "topic": topic,
                "domain": domain,
                "depth": depth,
                "created_at": now(),
            })
            kb.save_state()
        return kb

    # ------------------------------------------------------------------
    # 断点状态
    # ------------------------------------------------------------------

    @property
    def state(self) -> dict:
        if self._state is None:
            self._state = read_json(self.state_path, default={}) or {}
            self._state.setdefault("stages", {})
            self._state.setdefault("verified_keys", [])
        return self._state

    def save_state(self) -> None:
        self._state["updated_at"] = now()
        atomic_write_json(self.state_path, self.state)

    def is_done(self, stage: str) -> bool:
        return bool(self.state["stages"].get(stage, {}).get("done"))

    def mark_done(self, stage: str, **meta) -> None:
        self.state["stages"][stage] = {"done": True, "at": now(), "meta": meta}
        self.state["current_stage"] = stage
        self.save_state()
        self.log_progress("stage_done", {"stage": stage, **meta})

    # 各阶段对应的产物路径，reset 时一并清理
    STAGE_ARTIFACTS = {
        "scout": ["Corpus"],
        "analyze": ["analysis.json"],
        "hypothesize": ["Propos"],
        "experiment": ["Verified", "Rejected"],
        "review": ["review_report.md", "review_verdict.json"],
        "write": ["Reports/research_report.md"],
        "visualize": ["Reports/visualization.html"],
    }

    def reset_stage(self, stage: str, purge: bool = True) -> None:
        """重置某个阶段（人工干预用）。

        purge=True 时同时清空该阶段的产物目录 —— 这一点很关键：实测中
        只清 state 不清产物，上一轮遗留的陈旧文件会混入新一轮，导致命题库
        出现幽灵记录（4 条新假设 + 1 条旧残留 = 5 条），且该类问题不会抛异常。
        """
        if purge:
            for item in self.STAGE_ARTIFACTS.get(stage, []):
                p = self.root / item
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    elif p.exists():
                        p.unlink()
                except OSError:
                    pass
        self.state["stages"].pop(stage, None)
        self.save_state()

    def add_verified_key(self, key: str) -> None:
        if key not in self.state["verified_keys"]:
            self.state["verified_keys"].append(key)
            self.save_state()

    def verified_keys(self) -> set[str]:
        return set(self.state.get("verified_keys", []))

    # ------------------------------------------------------------------
    # 人工干预通道
    # ------------------------------------------------------------------

    @property
    def control_path(self) -> Path:
        return self.root / "control.json"

    def check_control(self) -> str:
        """读取人工干预信号：continue / pause / abort。

        对应 UltraMath 的「人工/自动干预」能力：长任务运行期间，
        用户在 control.json 里写 {"action": "pause"} 即可在下一个
        检查点安全停机，而不必强杀进程。
        """
        ctl = read_json(self.control_path, default={}) or {}
        action = (ctl.get("action") or "continue").strip().lower()
        return action if action in ("continue", "pause", "abort") else "continue"

    def set_control(self, action: str, note: str = "") -> None:
        atomic_write_json(self.control_path, {"action": action, "note": note, "at": now()})

    def clear_control(self) -> None:
        if self.control_path.exists():
            self.control_path.unlink()

    # ------------------------------------------------------------------
    # 研究日志（append-only）
    # ------------------------------------------------------------------

    def log_progress(self, event: str, detail: dict | None = None) -> None:
        record = {"at": now(), "event": event, "detail": detail or {}}
        path = self.root / "Progress" / "research_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 问题清单
    # ------------------------------------------------------------------

    def write_problem(self, pid: str, topic: str, domain: str,
                      depends_on: list[str] | None = None,
                      source: str = "") -> None:
        atomic_write_json(self.root / "Problems" / f"{pid}.json", {
            "id": pid,
            "topic": topic,
            "domain": domain,
            "depends_on": depends_on or [],
            "source": source,
            "created_at": now(),
        })

    # ------------------------------------------------------------------
    # 命题库
    # ------------------------------------------------------------------

    @staticmethod
    def _pid_of(prop: dict) -> str:
        """解析命题的稳定 ID。

        命题结构为 `{**gap字段, "hypothesis": {...}}` —— ID 位于 hypothesis
        内部而非顶层，因此必须多级回退查找。

        实测教训：早期版本只查顶层 `hypothesis_id`，取不到就退化为 "P000"，
        导致 4 条假设全部写入同一个 P000.json 互相覆盖，最终只剩最后 1 条。
        这类「静默数据丢失」不会抛异常，只能靠命题库条数校验发现。
        """
        return (prop.get("hypothesis_id")
                or (prop.get("hypothesis") or {}).get("hypothesis_id")
                or prop.get("id")
                or "P000")

    def write_proposition(self, prop: dict) -> None:
        pid = self._pid_of(prop)
        prop = dict(prop)
        prop.setdefault("created_at", now())
        prop["status"] = prop.get("status") or "pending"
        atomic_write_json(self.root / "Propos" / f"{pid}.json", prop)

    def load_propositions(self, status: str | None = None) -> list[dict]:
        out = []
        for p in sorted((self.root / "Propos").glob("*.json")):
            data = read_json(p, default={}) or {}
            if status is None or data.get("status") == status:
                out.append(data)
        return out

    def promote_verified(self, prop: dict) -> None:
        """把通过门禁的命题提升到 Verified/（绝对可信层）。"""
        pid = self._pid_of(prop)
        atomic_write_json(self.root / "Verified" / f"{pid}.json", {
            **prop, "promoted_at": now(), "trust_level": "verified",
        })
        prop["status"] = "verified"
        self.write_proposition(prop)

    def record_rejection(self, prop: dict, reason: str) -> None:
        """把被推翻的命题归档到 Rejected/，供后续研究避坑。"""
        pid = self._pid_of(prop)
        atomic_write_json(self.root / "Rejected" / f"{pid}.json", {
            **prop, "rejected_at": now(), "reject_reason": reason,
        })
        prop["status"] = "rejected"
        self.write_proposition(prop)

    # ------------------------------------------------------------------
    # 方法库（方法论沉淀）
    # ------------------------------------------------------------------

    def record_method(self, name: str, data: dict) -> None:
        """沉淀一个可复用的方法/判据，对应 Vibe Math 的 Method Keeper。"""
        path = self.root / "Methods" / f"{slugify(name)}.json"
        existing = read_json(path, default=None)
        if existing:
            existing.setdefault("occurrences", 0)
            existing["occurrences"] += 1
            existing["last_seen"] = now()
            atomic_write_json(path, existing)
        else:
            atomic_write_json(path, {
                "name": name, "created_at": now(), "last_seen": now(),
                "occurrences": 1, **data,
            })

    # ------------------------------------------------------------------
    # 语料与方法结果
    # ------------------------------------------------------------------

    def snapshot_corpus(self, papers: list, extra: dict | None = None) -> None:
        atomic_write_json(self.root / "Corpus" / "papers.json",
                          [p.to_dict() if hasattr(p, "to_dict") else p for p in papers])
        if extra:
            atomic_write_json(self.root / "Corpus" / "snapshot_meta.json", extra)

    def write_artifact(self, name: str, obj) -> Path:
        """写任意阶段产物到工作区根目录。"""
        path = self.root / name
        if isinstance(obj, (dict, list)):
            atomic_write_json(path, obj)
        else:
            atomic_write_text(path, str(obj))
        return path

    def write_report(self, filename: str, text: str) -> Path:
        path = self.root / "Reports" / filename
        atomic_write_text(path, text)
        return path

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        st = self.state
        return {
            "task_id": self.task_id,
            "topic": st.get("topic"),
            "domain": st.get("domain"),
            "depth": st.get("depth"),
            "root": str(self.root),
            "stages_done": [k for k, v in st["stages"].items() if v.get("done")],
            "propositions": len(self.load_propositions()),
            "verified": len(list((self.root / "Verified").glob("*.json"))),
            "rejected": len(list((self.root / "Rejected").glob("*.json"))),
            "methods": len(list((self.root / "Methods").glob("*.json"))),
            "created_at": st.get("created_at"),
            "updated_at": st.get("updated_at"),
        }


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    kb = ResearchKB.for_topic(base, "demo task", "materials", "quick")
    kb.mark_done("scout", unique_hits=103)
    kb.write_proposition({"hypothesis_id": "H01", "statement": "示例假设"})
    kb.record_method("共现期望判据", {"formula": "freq(a)*freq(b)/N"})
    print(json.dumps(kb.summary(), ensure_ascii=False, indent=2))
