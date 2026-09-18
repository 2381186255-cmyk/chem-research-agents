# -*- coding: utf-8 -*-
"""
chem-research-loop / visualize.py
=================================
可视化师：把分析结果渲染成一份自包含的交互式 HTML。

设计约束
--------
1. **零 CDN、零外部依赖** —— 全部图形用内联 SVG 直接生成，双击即可打开，
   断网也能看。这与项目「零第三方依赖」的调性一致。
2. **纯静态交互** —— 悬停提示用 SVG 原生 <title>（浏览器原生支持），
   不引入任何 JavaScript 框架。
3. **确定性布局** —— 力导向算法固定随机种子，同一份数据每次渲染结果一致，
   便于版本对比与复现。

三张图
------
  · 概念网络图：节点按四个语义类别分簇着色，边的粗细表示共现强度
  · 空白热力图：横纵为跨类别概念，颜色表示提升度（暖色=共现不足=候选空白）
  · 年代趋势图：逐年文献量，并叠加开放获取占比
"""

from __future__ import annotations

import html
import math
import random

from domains import CATEGORY_LABEL_ZH, CONCEPT_CATEGORIES

# 四个语义类别的配色（与 light 主题协调）
CATEGORY_COLOR = {
    "material": "#185FA5",
    "method": "#534AB7",
    "application": "#3B6D11",
    "metric": "#BA7517",
}

# 提升度色阶：暖色表示共现不足（候选空白），冷色表示已被充分研究
LIFT_SCALE = [
    (0.0, "#F0997B", "完全无共现"),
    (0.5, "#FAC775", "共现稀疏"),
    (1.0, "#FAEEDA", "略低于期望"),
    (1.5, "#C0DD97", "接近期望"),
    (99.0, "#97C459", "共现密集"),
]

NET_W, NET_H = 720, 520


# --------------------------------------------------------------------------
# 力导向布局
# --------------------------------------------------------------------------

def force_layout(nodes: list[dict], edges: list[tuple[int, int, float]],
                 width: int, height: int, iterations: int = 220,
                 seed: int = 42) -> list[tuple[float, float]]:
    """简化力导向布局：斥力 + 引力 + 向心力。

    固定随机种子保证确定性 —— 同一份数据每次渲染位置一致，
    这样不同批次的结果图才能放在一起对比。
    """
    rng = random.Random(seed)
    n = len(nodes)
    if n == 0:
        return []

    cats = list(dict.fromkeys(nd["cat"] for nd in nodes))
    centers: dict[str, tuple[float, float]] = {}
    for i, c in enumerate(cats):
        ang = 2 * math.pi * i / max(1, len(cats))
        centers[c] = (width / 2 + math.cos(ang) * width * 0.24,
                      height / 2 + math.sin(ang) * height * 0.24)

    pos = [[centers[nd["cat"]][0] + rng.uniform(-28, 28),
            centers[nd["cat"]][1] + rng.uniform(-28, 28)] for nd in nodes]

    k_rep, k_att, k_center, max_step = 5200.0, 0.012, 0.006, 14.0
    pad = 34

    for _ in range(iterations):
        disp = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                dx, dy = pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]
                d2 = dx * dx + dy * dy
                if d2 < 1e-6:
                    dx, dy, d2 = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                d = math.sqrt(d2)
                f = k_rep / d2
                ux, uy = dx / d, dy / d
                disp[i][0] += ux * f
                disp[i][1] += uy * f
                disp[j][0] -= ux * f
                disp[j][1] -= uy * f

        for (i, j, w) in edges:
            dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
            d = max(1.0, math.sqrt(dx * dx + dy * dy))
            f = k_att * w * d
            ux, uy = dx / d, dy / d
            disp[i][0] += ux * f
            disp[i][1] += uy * f
            disp[j][0] -= ux * f
            disp[j][1] -= uy * f

        for i in range(n):
            disp[i][0] += (width / 2 - pos[i][0]) * k_center
            disp[i][1] += (height / 2 - pos[i][1]) * k_center

        for i in range(n):
            dx, dy = disp[i]
            d = math.sqrt(dx * dx + dy * dy)
            if d > max_step:
                dx, dy = dx * max_step / d, dy * max_step / d
            pos[i][0] = max(pad, min(width - pad, pos[i][0] + dx))
            pos[i][1] = max(pad, min(height - pad, pos[i][1] + dy))

    return [(p[0], p[1]) for p in pos]


# --------------------------------------------------------------------------
# 图一：概念网络
# --------------------------------------------------------------------------

def render_concept_network(analysis: dict, max_nodes: int = 46) -> str:
    freq = analysis.get("concepts", {}).get("frequency", {})
    cooc = analysis.get("cooccurrence", []) or []

    # 取各类别高频概念
    picked: list[dict] = []
    index: dict[str, int] = {}
    for cat in CONCEPT_CATEGORIES:
        items = sorted(freq.get(cat, {}).items(), key=lambda kv: kv[1], reverse=True)
        for term, cnt in items[:max_nodes // len(CONCEPT_CATEGORIES) + 2]:
            if cnt >= 2:
                index[f"{cat}:{term}"] = len(picked)
                picked.append({"cat": cat, "term": term, "freq": cnt})

    if not picked:
        return '<p class="empty">概念数据不足，无法绘制网络图。</p>'

    # 用交叉主干构造边
    edges: list[tuple[int, int, float]] = []
    seen_e: set[tuple[int, int]] = set()
    for c in cooc:
        lk = _key_of_label(c["left"])
        rk = _key_of_label(c["right"])
        if lk in index and rk in index:
            i, j = index[lk], index[rk]
            if i == j or (min(i, j), max(i, j)) in seen_e:
                continue
            seen_e.add((min(i, j), max(i, j)))
            edges.append((i, j, float(c["count"])))

    pos = force_layout(picked, edges, NET_W, NET_H)
    max_freq = max(nd["freq"] for nd in picked)
    max_w = max((w for _, _, w in edges), default=1.0)

    L: list[str] = []
    L.append(f'<svg viewBox="0 0 {NET_W} {NET_H}" class="chart" role="img">')
    L.append('<title>概念共现网络</title>')

    for (i, j, w) in edges:
        x1, y1 = pos[i]
        x2, y2 = pos[j]
        sw = 0.6 + 3.4 * (w / max_w)
        op = 0.22 + 0.5 * (w / max_w)
        L.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="#888780" stroke-width="{sw:.2f}" stroke-opacity="{op:.2f}">'
                 f'<title>{_esc(picked[i]["term"])} × {_esc(picked[j]["term"])}：共现 {int(w)} 篇</title></line>')

    for i, nd in enumerate(picked):
        x, y = pos[i]
        r = 6 + 13 * (nd["freq"] / max_freq)
        color = CATEGORY_COLOR.get(nd["cat"], "#5F5E5A")
        L.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" '
                 f'fill-opacity="0.82" stroke="#FFFFFF" stroke-width="1.2">'
                 f'<title>{_esc(nd["term"])}（{CATEGORY_LABEL_ZH.get(nd["cat"], nd["cat"])}）'
                 f'：{nd["freq"]} 篇</title></circle>')
        if r > 10:
            L.append(f'<text x="{x:.1f}" y="{y + r + 12:.1f}" text-anchor="middle" '
                     f'class="nlab">{_esc(nd["term"][:16])}</text>')
    L.append("</svg>")
    return "\n".join(L)


def _key_of_label(label: str) -> str:
    """把「材料体系:MOF」还原为内部键 material:MOF。"""
    zh, _, term = label.partition(":")
    rev = {v: k for k, v in CATEGORY_LABEL_ZH.items()}
    return f"{rev.get(zh, zh)}:{term}"


# --------------------------------------------------------------------------
# 图二：空白热力图
# --------------------------------------------------------------------------

def render_gap_heatmap(analysis: dict, top: int = 12) -> str:
    freq = analysis.get("concepts", {}).get("frequency", {})
    cooc = analysis.get("cooccurrence", []) or []
    gaps = analysis.get("gaps", []) or []

    # 矩阵：行=左侧概念，列=右侧概念，值=共现数
    matrix: dict[tuple[str, str], int] = {}
    rowset: list[tuple[str, str]] = []
    colset: list[tuple[str, str]] = []
    for c in cooc:
        rk, ck = _key_of_label(c["left"]), _key_of_label(c["right"])
        matrix[(rk, ck)] = c["count"]
        if rk not in rowset:
            rowset.append(rk)
        if ck not in colset:
            colset.append(ck)

    # 补上被判定为空白（零共现）的组合，这才是图的核心价值
    for g in gaps:
        rk = f"{g['left_category']}:{g['left_term']}"
        ck = f"{g['right_category']}:{g['right_term']}"
        matrix.setdefault((rk, ck), g["cooccur"])
        if rk not in rowset:
            rowset.append(rk)
        if ck not in colset:
            colset.append(ck)

    # 按频次排序取前 top 个
    def _freq_of(k: str) -> int:
        cat, _, term = k.partition(":")
        return freq.get(cat, {}).get(term, 0)

    rowset = sorted(rowset, key=_freq_of, reverse=True)[:top]
    colset = sorted(colset, key=_freq_of, reverse=True)[:top]
    if not rowset or not colset:
        return '<p class="empty">热力图数据不足。</p>'

    cell = 40
    left_w = 168
    top_h = 118
    w = left_w + cell * len(colset) + 20
    h = top_h + cell * len(rowset) + 16

    L: list[str] = []
    L.append(f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">')
    L.append('<title>跨类别共现热力图</title>')

    # 列标签（旋转）
    for j, ck in enumerate(colset):
        cx = left_w + j * cell + cell / 2
        cat, _, term = ck.partition(":")
        L.append(f'<text x="{cx:.1f}" y="{top_h - 8}" transform="rotate(-38 {cx:.1f} {top_h - 8})" '
                 f'class="clab" fill="{CATEGORY_COLOR.get(cat, "#5F5E5A")}">{_esc(term[:18])}</text>')

    for i, rk in enumerate(rowset):
        y = top_h + i * cell
        cat, _, term = rk.partition(":")
        L.append(f'<text x="{left_w - 10}" y="{y + cell / 2 + 4:.1f}" text-anchor="end" '
                 f'class="rlab" fill="{CATEGORY_COLOR.get(cat, "#5F5E5A")}">{_esc(term[:20])}</text>')
        for j, ck in enumerate(colset):
            x = left_w + j * cell
            obs = matrix.get((rk, ck))
            if obs is None:
                L.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" '
                         f'fill="#F1EFE8" stroke="#FFFFFF" stroke-width="1">'
                         f'<title>无数据</title></rect>')
                continue
            exp_c = (_freq_of(rk) * _freq_of(ck)) / max(1, analysis["corpus"]["total"])
            lv = (obs / exp_c) if exp_c > 0 else 0.0
            color = LIFT_SCALE[-1][1]
            desc = LIFT_SCALE[-1][2]
            for thr, col, lbl in LIFT_SCALE:
                if lv < thr:
                    color, desc = col, lbl
                    break
            L.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" '
                     f'fill="{color}" stroke="#FFFFFF" stroke-width="1">'
                     f'<title>{_esc(term)} × {_esc(ck.partition(":")[2])}：'
                     f'实际 {obs} 篇 / 期望 {exp_c:.1f} 篇，提升度 {lv:.2f}（{desc}）</title></rect>')
            L.append(f'<text x="{x + (cell - 2) / 2}" y="{y + cell / 2 + 4:.1f}" '
                     f'text-anchor="middle" class="cellv">{obs}</text>')
    L.append("</svg>")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 图三：年代趋势
# --------------------------------------------------------------------------

def render_year_trend(analysis: dict) -> str:
    counts = (analysis.get("year_trend") or {}).get("counts", {}) or {}
    if not counts:
        return '<p class="empty">无有效年份数据。</p>'

    years = sorted(counts.keys())
    vals = [counts[y] for y in years]
    w, h = 720, 300
    pad_l, pad_b, pad_t = 48, 46, 20
    plot_w = w - pad_l - 20
    plot_h = h - pad_b - pad_t
    mx = max(vals)
    bw = plot_w / len(years)

    L: list[str] = []
    L.append(f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">')
    L.append('<title>文献年代分布</title>')
    for g in range(5):
        gy = pad_t + plot_h * g / 4
        gv = mx * (1 - g / 4)
        L.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - 20}" y2="{gy:.1f}" '
                 f'stroke="#D3D1C7" stroke-width="0.5"/>')
        L.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
                 f'class="axis">{gv:.0f}</text>')

    for i, y in enumerate(years):
        bh = plot_h * (counts[y] / mx)
        x = pad_l + i * bw
        L.append(f'<rect x="{x + bw * 0.16:.1f}" y="{pad_t + plot_h - bh:.1f}" '
                 f'width="{bw * 0.68:.1f}" height="{bh:.1f}" fill="#185FA5" '
                 f'fill-opacity="0.78" rx="2">'
                 f'<title>{y} 年：{counts[y]} 篇</title></rect>')
        if len(years) <= 18 or i % 2 == 0:
            L.append(f'<text x="{x + bw / 2:.1f}" y="{h - pad_b + 18:.1f}" '
                     f'text-anchor="middle" class="axis">{y}</text>')
    L.append("</svg>")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


_CSS = """
:root{--bg:#ffffff;--fg:#2C2C2A;--muted:#5F5E5A;--line:#D3D1C7;--card:#F1EFE8}
*{box-sizing:border-box}
body{margin:0;padding:32px 40px 56px;background:var(--bg);color:var(--fg);
 font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
 font-size:14px;line-height:1.7}
h1{font-size:22px;font-weight:500;margin:0 0 6px}
h2{font-size:16px;font-weight:500;margin:0 0 14px;padding-bottom:8px;
 border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;margin:0 0 24px}
section{margin:38px 0 0}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 8px}
.card{background:var(--card);border-radius:10px;padding:14px 18px;min-width:132px}
.card .n{font-size:22px;font-weight:500;line-height:1.2}
.card .l{color:var(--muted);font-size:12px;margin-top:2px}
.chart{width:100%;height:auto;display:block}
.nlab{font-size:11px;fill:var(--muted)}
.clab{font-size:11px;text-anchor:start}
.rlab{font-size:12px}
.cellv{font-size:11px;fill:#2C2C2A;fill-opacity:.62}
.axis{font-size:11px;fill:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:14px;
 color:var(--muted);font-size:12px}
.legend .i{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:14px;border-radius:3px;display:inline-block}
.empty{color:var(--muted);font-size:13px;padding:18px 0}
.note{color:var(--muted);font-size:12px;margin-top:10px}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
 color:var(--muted);font-size:12px}
"""


def build_visualization(analysis: dict, topic: str, domain_name: str) -> str:
    """生成完整的自包含 HTML 可视化页面。"""
    corpus = analysis.get("corpus", {}) or {}
    stats = analysis.get("gap_stats", {}) or {}
    trend = analysis.get("year_trend", {}) or {}

    cards = [
        (corpus.get("total", 0), "去重文献"),
        (sum(1 for v in (trend.get("counts") or {}).values()), "有年份记录"),
        (len(analysis.get("gaps", []) or []), "候选空白"),
        (stats.get("significant", "-"), "统计显著"),
        (stats.get("total", "-"), "检验组合数"),
    ]
    card_html = "\n".join(
        f'<div class="card"><div class="n">{_esc(v)}</div><div class="l">{_esc(l)}</div></div>'
        for v, l in cards)

    cat_legend = "\n".join(
        f'<span class="i"><span class="sw" style="background:{CATEGORY_COLOR[c]}"></span>'
        f'{_esc(CATEGORY_LABEL_ZH.get(c, c))}</span>'
        for c in CONCEPT_CATEGORIES)

    lift_legend = "\n".join(
        f'<span class="i"><span class="sw" style="background:{col}"></span>{_esc(lbl)}</span>'
        for thr, col, lbl in LIFT_SCALE)

    gap_note = ""
    if stats:
        gap_note = (f'<p class="note">统计口径：共检验 {stats.get("total", 0)} 个概念组合，'
                    f'采用 Benjamini-Hochberg 错误发现率（FDR）校正，'
                    f'显著性水平 α={stats.get("alpha", 0.05)}；'
                    f'其中 {stats.get("significant", 0)} 个组合在校正后仍显著共现不足。</p>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(topic)} · 研究可视化</title>
<style>{_CSS}</style>
</head>
<body>
<h1>{_esc(topic)}</h1>
<p class="sub">研究方向：{_esc(domain_name)}　|　由 chem-research-agents 自动生成</p>

<div class="cards">
{card_html}
</div>

<section>
<h2>概念共现网络</h2>
{render_concept_network(analysis)}
<div class="legend">{cat_legend}</div>
<p class="note">节点大小表示概念出现频次，边的粗细表示跨类别共现强度；鼠标悬停可查看详情。</p>
</section>

<section>
<h2>跨类别共现热力图</h2>
{render_gap_heatmap(analysis)}
<div class="legend">{lift_legend}</div>
<p class="note">格子内数字为实际共现篇数，颜色为提升度（实际 ÷ 期望）。暖色区域即统计意义上的候选空白——两者各自都有研究，却很少被放在一起。</p>
{gap_note}
</section>

<section>
<h2>文献年代分布</h2>
{render_year_trend(analysis)}
<p class="note">近三年占比 {_esc(trend.get('recent_share', 0))}，中位年份 {_esc(trend.get('median_year', '-'))}。</p>
</section>

<footer>本文件为自包含静态页面，不依赖任何外部资源，可离线打开。</footer>
</body>
</html>
"""


if __name__ == "__main__":
    print("visualize.py 由主控调用，需传入分析结果。")
