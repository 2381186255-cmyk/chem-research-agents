# -*- coding: utf-8 -*-
"""
chem-research-loop / selftest.py
================================
离线单元测试：验证去重合并、指纹阈值、降级链三项核心逻辑。
不发起任何网络请求，可反复运行，结果确定。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources import (  # noqa: E402
    LiteratureSource,
    Paper,
    SourceError,
    SourceRegistry,
)
from analyze import (  # noqa: E402
    adaptive_min_expected,
    adaptive_min_freq,
    judge_verification,
)
from domains import MaterialsChemistry  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    tag = "[PASS]" if cond else "[FAIL]"
    tail = f"  -> {detail}" if detail else ""
    print(f"  {tag} {name}{tail}")


# --------------------------------------------------------------------------
# 用例 1：指纹阈值
# --------------------------------------------------------------------------

def test_fingerprints() -> None:
    print("\n[用例 1] 指纹生成与阈值保护")

    long_title = Paper(source="t", title="Metal-Organic Framework for CO2 Capture.")
    plain_title = Paper(source="t", title="Metal Organic Framework for CO2 Capture")
    check(
        "标点与连字符差异不影响标题指纹",
        long_title.title_key == plain_title.title_key and long_title.title_key != "",
        f"{long_title.title_key[:40]}...",
    )

    short = Paper(source="t", title="Editorial")
    check("短标题不生成指纹（防误合并）", short.title_key == "", f"实得 '{short.title_key}'")

    doi_p = Paper(source="t", title="X", doi="10.1000/ABC")
    check("DOI 指纹统一转小写", doi_p.doi_key == "10.1000/abc", doi_p.doi_key)

    nodoi = Paper(source="t", title="Y")
    check("无 DOI 时指纹为空串", nodoi.doi_key == "", repr(nodoi.doi_key))


# --------------------------------------------------------------------------
# 用例 2：跨源去重与信息合并
# --------------------------------------------------------------------------

def test_dedupe() -> None:
    print("\n[用例 2] 跨源去重：双指纹联合判定")

    papers = [
        Paper(source="openalex", title="Metal Organic Framework for CO2 Capture",
              doi="10.1000/xyz", citations=100, abstract="short"),
        # arXiv 无 DOI，标题仅连字符与句点不同 -> 应靠标题指纹合并
        Paper(source="arxiv", title="Metal-Organic Framework for CO2 Capture.",
              doi=None, abstract="A" * 400),
        # 同 DOI 的另一条元数据 -> 应靠 DOI 指纹并入同一组（传递性）
        Paper(source="crossref", title="Metal Organic Framework for CO2 Capture",
              doi="10.1000/xyz", citations=120),
        Paper(source="x", title="Editorial", doi=None),
        Paper(source="y", title="Editorial", doi=None),
    ]
    out = SourceRegistry.dedupe(papers)
    check("5 条原始记录合并为 3 条", len(out) == 3, f"实得 {len(out)} 条")

    merged = [p for p in out if (p.title or "").startswith("Metal")][0]
    check("合并后保留最长摘要", len(merged.abstract) == 400, f"实得 {len(merged.abstract)}")
    check("合并后保留最高引用数", merged.citations == 120, f"实得 {merged.citations}")
    check("同 DOI 记录确实被合并（无重复 DOI）",
          len({p.doi_key for p in out if p.doi_key}) == len([p for p in out if p.doi_key]))
    check("两条短标题 Editorial 未被误合并",
          len([p for p in out if p.title == "Editorial"]) == 2)

    empty = SourceRegistry.dedupe([Paper(source="t", title=""), Paper(source="t", title="")])
    check("空标题记录被安全丢弃", len(empty) == 0, f"实得 {len(empty)}")


# --------------------------------------------------------------------------
# 用例 3：降级链容错
# --------------------------------------------------------------------------

class BrokenSource(LiteratureSource):
    name = "broken"
    label_zh = "模拟故障源"
    priority = 1

    def search(self, query, limit=10, year_from=None):
        raise SourceError("模拟网络故障")


class ExplodingSource(LiteratureSource):
    """模拟适配器代码缺陷（非 SourceError），验证整体不被拖垮。"""

    name = "exploding"
    label_zh = "模拟代码缺陷源"
    priority = 2

    def search(self, query, limit=10, year_from=None):
        raise ValueError("适配器内部 bug")


def test_degradation() -> None:
    print("\n[用例 3] 降级链：单源故障不拖垮整体")

    reg = SourceRegistry(source_classes=[BrokenSource, ExplodingSource], verbose=False)
    papers, ledger = reg.fetch("anything", per_source=1)

    check("全源故障时返回空列表而非抛异常", papers == [], f"实得 {len(papers)} 条")
    check("台账正确记录失败次数", ledger["sources"]["broken"]["fail"] == 1)
    check("非 SourceError 缺陷同样被计入失败", ledger["sources"]["exploding"]["fail"] == 1)
    check("失败源保留错误原因便于排查",
          "模拟网络故障" in ledger["sources"]["broken"]["last_error"])

    health = reg.probe_all()
    check("体检报告标记故障源为不可用", "不可用" in health["broken"]["status"])


# --------------------------------------------------------------------------
# 用例 4：反证判定三分支
# --------------------------------------------------------------------------

def test_verification_judgement() -> None:
    print("\n[用例 4] 反证判定三分支（含密度判据边界）")

    v, s = judge_verification(0, 20)
    check("零交叉 -> 确认空白成立", s is True and "确认" in v, v[:26])

    v, s = judge_verification(7, 15)
    check("7/15（47%）-> 推翻", s is False and "推翻" in v, v[:26])

    v, s = judge_verification(1, 17)
    check("1/17（6%）-> 存疑", s is None and "存疑" in v, v[:26])

    v, s = judge_verification(2, 20)
    check("2/20（篇数不足）-> 存疑", s is None, v[:26])

    v, s = judge_verification(3, 20)
    check("3/20（篇数与密度均达标）-> 推翻", s is False, v[:26])

    v, s = judge_verification(5, 100)
    check("5/100（密度仅 5%）-> 存疑而非推翻", s is None, v[:26])

    v, s = judge_verification(0, 0)
    check("零命中零交叉不触发除零", s is True)


# --------------------------------------------------------------------------
# 用例 5：自适应门槛
# --------------------------------------------------------------------------

def test_adaptive_thresholds() -> None:
    print("\n[用例 5] 语料规模自适应门槛")

    check("18 篇小语料频次门槛降至 2", adaptive_min_freq(18) == 2, str(adaptive_min_freq(18)))
    check("103 篇标准语料门槛为 3", adaptive_min_freq(103) == 3, str(adaptive_min_freq(103)))
    check("500 篇大语料门槛为 5", adaptive_min_freq(500) == 5, str(adaptive_min_freq(500)))
    check("小语料期望门槛放宽至 0.6", adaptive_min_expected(18) == 0.6)
    check("大语料期望门槛为 1.0", adaptive_min_expected(103) == 1.0)


# --------------------------------------------------------------------------
# 用例 6：同义词归并
# --------------------------------------------------------------------------

def _hit(cat: str, term: str, text: str) -> bool:
    idx = MaterialsChemistry.term_index()
    for t, pats in idx.get(cat, []):
        if t == term:
            return any(p.search(text) for p in pats)
    return False


def test_synonym_merging() -> None:
    print("\n[用例 6] 同义词归并（防止概念频次被人为稀释）")

    check("缩写 MOFs 归并到 MOF", _hit("material", "MOF", "The MOFs show high uptake"))
    check("全称 metal-organic framework 归并到 MOF",
          _hit("material", "MOF", "This metal-organic framework is stable"))
    check("连字符与空格变体均命中",
          _hit("material", "MOF", "metal organic framework composite"))
    check("carbon capture 归并到 CO2 capture",
          _hit("application", "CO2 capture", "for carbon capture applications"))
    check("DFT 归并到 density functional theory",
          _hit("method", "density functional theory", "using DFT calculations"))
    check("不误匹配更长词（MOFxyz）",
          not _hit("material", "MOF", "the MOFxyz compound was synthesized"))


# --------------------------------------------------------------------------
# 用例 7-9：V2 新增模块（知识库 / 断点 / 门禁）
# --------------------------------------------------------------------------

def test_kb_pid_resolution() -> None:
    """回归测试：命题 ID 解析失败曾导致 4 条假设被覆盖成 1 条（静默数据丢失）。"""
    print("\n[用例 7] 知识库命题 ID 解析（静默数据丢失回归）")
    import shutil
    import tempfile

    from kb import ResearchKB

    tmp = tempfile.mkdtemp()
    try:
        kb = ResearchKB(tmp, "t")

        check("顶层 hypothesis_id 优先解析",
              kb._pid_of({"hypothesis_id": "H01"}) == "H01")
        # 这是曾经出问题的形态：ID 只存在于嵌套的 hypothesis 里
        check("嵌套 hypothesis.hypothesis_id 可回退解析",
              kb._pid_of({"hypothesis": {"hypothesis_id": "H02"}}) == "H02")
        check("id 字段可回退解析", kb._pid_of({"id": "P9"}) == "P9")
        check("全部缺失时返回占位 ID", kb._pid_of({}) == "P000")

        for i in range(1, 5):
            kb.write_proposition({
                "hypothesis": {"hypothesis_id": f"H{i:02d}"},
                "status": "pending",
            })
        n = len(kb.load_propositions())
        check("4 条不同命题落成 4 个独立文件", n == 4, f"实得 {n} 条")

        kb.promote_verified({"hypothesis": {"hypothesis_id": "H01"}})
        check("提升到 Verified 层时 ID 解析正确",
              (kb.root / "Verified" / "H01.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_checkpoint_state() -> None:
    print("\n[用例 8] 断点状态与人工干预信号")
    import shutil
    import tempfile

    from kb import ResearchKB

    tmp = tempfile.mkdtemp()
    try:
        kb = ResearchKB(tmp, "t")

        check("未执行阶段 is_done 为假", not kb.is_done("scout"))
        kb.mark_done("scout", unique_hits=103)
        check("标记完成后 is_done 为真", kb.is_done("scout"))
        check("完成信息被记录", kb.state["stages"]["scout"]["meta"]["unique_hits"] == 103)

        # 断点恢复：新实例读同一目录应看到相同状态
        kb2 = ResearchKB(tmp, "t")
        check("新实例可恢复断点状态", kb2.is_done("scout"))

        kb2.reset_stage("scout", purge=False)
        check("重置后 is_done 为假", not kb2.is_done("scout"))

        kb2.set_control("pause")
        check("人工干预信号可读取", kb2.check_control() == "pause")
        kb2.set_control("bogus-action")
        check("非法信号安全回退为 continue", kb2.check_control() == "continue")

        kb2.add_verified_key("A|B")
        kb2.add_verified_key("A|B")
        check("已验证组合去重累积", len(kb2.verified_keys()) == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reviewer_gate() -> None:
    print("\n[用例 9] 审稿门禁判定")
    from reviewer import Reviewer

    rv = Reviewer(verbose=False)
    rv.add("B01", "模拟阻断项", False, "人为置为失败")
    v = rv.verdict()
    check("阻断项失败时整体裁决不通过", v["passed"] is False)
    check("阻断项被正确归类", len(v["blocking_failed"]) == 1)
    check("阻断项在明细中标记为 blocking",
          any(c["blocking"] for c in v["checks"]))

    rv2 = Reviewer(verbose=False)
    rv2.add("C99", "模拟常规项", False, "人为置为告警")
    v2 = rv2.verdict()
    check("仅常规项失败不阻断交付", v2["passed"] is True)
    check("常规项计入告警而非阻断",
          len(v2["warnings"]) == 1 and not v2["blocking_failed"])

    rv3 = Reviewer(verbose=False)
    rv3.add("B01", "全部通过的阻断项", True, "正常")
    check("阻断项通过时裁决放行", rv3.verdict()["passed"] is True)
    check("审稿报告可渲染", "裁决" in rv3.report())


def test_statistical_tests() -> None:
    """统计显著性检验：用手工可验算的算例锁定正确性。"""
    print("\n[用例 10] 统计显著性检验与多重比较校正")
    from stats import (benjamini_hochberg, depletion_pvalue, lift,
                       odds_ratio, significance_label, summarize_tests)

    # N=8, f_a=4, f_b=4 的超几何分布可手工验算：
    #   P(X=0)=1/70, P(X=1)=16/70, P(X=2)=36/70
    #   => P(X<=0)=1/70, P(X<=1)=17/70, P(X<=2)=53/70
    p0 = depletion_pvalue(0, 8, 4, 4)
    check("Fisher 左尾 P(X<=0) = 1/70", abs(p0 - 1 / 70) < 1e-9, f"{p0:.6f}")
    p1 = depletion_pvalue(1, 8, 4, 4)
    check("Fisher 左尾 P(X<=1) = 17/70", abs(p1 - 17 / 70) < 1e-9, f"{p1:.6f}")
    p2 = depletion_pvalue(2, 8, 4, 4)
    check("Fisher 左尾 P(X<=2) = 53/70", abs(p2 - 53 / 70) < 1e-9, f"{p2:.6f}")
    check("共现达到上限时 p 接近 1（不可能是共现不足）",
          depletion_pvalue(4, 8, 4, 4) > 0.99)

    # 效应量
    check("零共现的 lift 为 0", lift(0, 100, 20, 20) == 0.0)
    check("观测等于期望时 lift 为 1", abs(lift(4, 100, 20, 20) - 1.0) < 1e-9)
    check("零共现的优势比小于 1（负相关）", odds_ratio(0, 100, 20, 20) < 1)

    # FDR 校正：m=6, alpha=0.05 的临界值为 rank/6 × 0.05
    ps = [0.001, 0.008, 0.02, 0.04, 0.30, 0.60]
    rej, qs = benjamini_hochberg(ps, 0.05)
    check("FDR 校正：前 3 个显著（p=0.04 已超临界值）",
          sum(rej) == 3, f"实得 {sum(rej)}")
    check("q 值随 p 值单调不减",
          all(qs[i] <= qs[i + 1] + 1e-12 for i in range(len(qs) - 1)))
    check("q 值不小于对应 p 值", all(qs[i] >= ps[i] - 1e-12 for i in range(len(ps))))

    check("多个相同小 p 值全部显著",
          sum(benjamini_hochberg([0.001] * 10, 0.05)[0]) == 10)
    check("全是大 p 值则无一显著",
          sum(benjamini_hochberg([0.5, 0.6, 0.7], 0.05)[0]) == 0)
    check("空输入安全返回", benjamini_hochberg([]) == ([], []))

    check("显著性标签：极显著", "极显著" in significance_label(0.005))
    check("显著性标签：不显著", "不显著" in significance_label(0.9))

    s = summarize_tests(ps)
    check("检验汇总计数正确",
          s["total"] == 6 and s["significant"] == 3, str(s["significant"]))

    # 数值稳定性：超大语料必须走正态近似，且结果仍在 [0,1]
    big_p = depletion_pvalue(50, 5000, 800, 600)
    check("大语料不溢出且落在 [0,1]", 0.0 <= big_p <= 1.0, f"{big_p:.6f}")
    check("退化输入（零频次）不崩溃", depletion_pvalue(0, 10, 0, 5) == 1.0)


if __name__ == "__main__":
    print("=" * 62)
    print("chem-research-loop 离线单元测试")
    print("=" * 62)
    test_fingerprints()
    test_dedupe()
    test_degradation()
    test_verification_judgement()
    test_adaptive_thresholds()
    test_synonym_merging()
    test_kb_pid_resolution()
    test_checkpoint_state()
    test_reviewer_gate()
    test_statistical_tests()
    print("\n" + "=" * 62)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for f in FAILED:
            print(f"  未通过：{f}")
        sys.exit(1)
    print("全部通过")
