# -*- coding: utf-8 -*-
"""
chem-research-loop / sources.py
===============================
多源学术文献适配器：统一接口契约 + 自动降级链 + 跨源去重合并。

设计要点（面向对象）：
  LiteratureSource  —— 抽象基类，定义 search() 接口契约
    ├── OpenAlexSource     全学科开放学术图谱（引用网络、概念标签）
    ├── CrossrefSource     DOI 官方注册库（元数据权威）
    ├── EuropePMCSource    生命/化学/医学摘要 + 开放获取全文
    └── ArXivSource        预印本（物理化学、计算化学、材料理论）

  SourceRegistry —— 调度器，负责并行编排、健康度记账、失败降级、去重合并

零第三方依赖，仅使用 Python 标准库，保证「下载即跑」。
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

UA = "chem-research-loop/1.0 (academic-literature-retrieval; mailto:research@example.org)"
TIMEOUT = 35
_CTX = ssl.create_default_context()


class SourceError(RuntimeError):
    """数据源调用失败（网络、限流、结构异常）。用于触发降级。"""


# --------------------------------------------------------------------------
# 数据契约：统一文献记录
# --------------------------------------------------------------------------

@dataclass
class Paper:
    """跨源统一文献记录。所有适配器必须产出这个结构。"""

    source: str
    title: str
    year: int | None = None
    doi: str | None = None
    abstract: str = ""
    citations: int | None = None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    url: str = ""
    concepts: list[str] = field(default_factory=list)
    is_oa: bool = False

    @property
    def doi_key(self) -> str:
        """DOI 指纹。空字符串表示该记录没有 DOI。"""
        return (self.doi or "").lower().strip()

    @property
    def title_key(self) -> str:
        """标题指纹：去标点空格、转小写后截断。

        仅当归一化标题长度 >= 20 字符时才返回指纹，避免 'Editorial'、
        'Correction' 这类短标题造成跨文献误合并。
        """
        t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (self.title or "").lower())
        return t[:110] if len(t) >= 20 else ""

    @property
    def richness(self) -> int:
        """信息丰富度，用于多源同名记录合并时择优。"""
        return len(self.abstract or "") + (len(self.authors) * 10) + len(self.venue or "")

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# HTTP 底座
# --------------------------------------------------------------------------

def http_get(url: str, timeout: int = TIMEOUT, retries: int = 2) -> str:
    """带指数退避的 GET。

    实测 Crossref 会间歇性返回 429（同一进程内体检失败、紧接着检索又成功），
    属于典型的共享 IP 限流。因此对 429 / 503 及瞬时网络错误做退避重试，
    避免单次抖动被误判为「数据源不可用」而触发无谓降级。
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "application/json, application/atom+xml, */*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise SourceError(f"HTTP {exc.code}") from exc
        except Exception as exc:  # 超时、DNS、连接重置
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise SourceError(f"{type(exc).__name__}: {exc}") from exc
    raise SourceError(f"{type(last_exc).__name__}: {last_exc}")


def http_get_json(url: str, timeout: int = TIMEOUT) -> dict:
    raw = http_get(url, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"JSON 解析失败: {exc}") from exc


def _q(text: str) -> str:
    return urllib.parse.quote_plus(text)


def _max_opt(a, b):
    """两个可能为 None 的数值中取较大者。

    用于合并引用数：多个源对同一篇论文的引用数会有差异，
    取最大值更接近真实且代表更新的统计口径。
    """
    vals = [x for x in (a, b) if x is not None]
    return max(vals) if vals else None


def rebuild_abstract(inverted: dict | None) -> str:
    """OpenAlex 的 abstract_inverted_index 还原为正常语序摘要。"""
    if not inverted:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


# --------------------------------------------------------------------------
# 抽象基类
# --------------------------------------------------------------------------

class LiteratureSource:
    """所有文献数据源的接口契约。子类只需实现 search()。"""

    name = "base"
    label_zh = "基础源"
    priority = 100  # 数字越小优先级越高

    def search(self, query: str, limit: int = 10, year_from: int | None = None) -> list[Paper]:
        raise NotImplementedError

    def probe(self) -> bool:
        """健康探针：发一个极小请求判断源是否可达。"""
        try:
            self.search("chemistry", limit=1)
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------
# 具体适配器
# --------------------------------------------------------------------------

class OpenAlexSource(LiteratureSource):
    """开放学术图谱。优势：引用网络、概念标签、覆盖全学科。"""

    name = "openalex"
    label_zh = "OpenAlex 开放学术图谱"
    priority = 10
    BASE = "https://api.openalex.org/works"

    def search(self, query: str, limit: int = 10, year_from: int | None = None) -> list[Paper]:
        params = {
            "search": query,
            "per-page": str(min(limit, 200)),
            "mailto": "research@example.org",
            "select": ("id,doi,title,publication_year,cited_by_count,"
                       "primary_location,authorships,abstract_inverted_index,"
                       "concepts,topics,open_access"),
        }
        if year_from:
            params["filter"] = f"from_publication_date:{year_from}-01-01"
        url = self.BASE + "?" + urllib.parse.urlencode(params)
        data = http_get_json(url)

        out: list[Paper] = []
        for w in data.get("results", []) or []:
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            venue = ""
            loc = w.get("primary_location") or {}
            if isinstance(loc.get("source"), dict):
                venue = loc["source"].get("display_name") or ""
            authors = []
            for a in (w.get("authorships") or [])[:12]:
                nm = (a.get("author") or {}).get("display_name")
                if nm:
                    authors.append(nm)
            concepts = [c.get("display_name", "") for c in (w.get("concepts") or [])[:8] if c.get("display_name")]
            if not concepts:
                concepts = [t.get("display_name", "") for t in (w.get("topics") or [])[:8] if t.get("display_name")]
            out.append(Paper(
                source=self.name,
                title=(w.get("title") or "").strip(),
                year=w.get("publication_year"),
                doi=doi,
                abstract=rebuild_abstract(w.get("abstract_inverted_index")),
                citations=w.get("cited_by_count"),
                venue=venue,
                authors=authors,
                url=w.get("id") or "",
                concepts=concepts,
                is_oa=bool((w.get("open_access") or {}).get("is_oa")),
            ))
        return out


class CrossrefSource(LiteratureSource):
    """DOI 官方注册库。优势：元数据最权威、化学期刊覆盖完整。"""

    name = "crossref"
    label_zh = "Crossref DOI 注册库"
    priority = 20
    BASE = "https://api.crossref.org/works"

    def search(self, query: str, limit: int = 10, year_from: int | None = None) -> list[Paper]:
        params = {
            "query": query,
            "rows": str(min(limit, 100)),
            "select": "DOI,title,issued,is-referenced-by-count,container-title,author,abstract,type,URL",
            "mailto": "research@example.org",
        }
        if year_from:
            params["filter"] = f"from-pub-date:{year_from}-01-01"
        url = self.BASE + "?" + urllib.parse.urlencode(params)
        data = http_get_json(url)

        out: list[Paper] = []
        for w in ((data.get("message") or {}).get("items") or []):
            titles = w.get("title") or []
            title = (titles[0] if titles else "").strip()
            if not title:
                continue
            year = None
            parts = ((w.get("issued") or {}).get("date-parts") or [[]])
            if parts and parts[0]:
                year = parts[0][0]
            authors = []
            for a in (w.get("author") or [])[:12]:
                nm = " ".join(x for x in [a.get("given"), a.get("family")] if x).strip()
                if nm:
                    authors.append(nm)
            raw_abs = w.get("abstract") or ""
            abstract = re.sub(r"<[^>]+>", " ", raw_abs)
            abstract = re.sub(r"\s+", " ", abstract).strip()
            ct = w.get("container-title") or []
            out.append(Paper(
                source=self.name,
                title=title,
                year=year,
                doi=(w.get("DOI") or "").lower() or None,
                abstract=abstract,
                citations=w.get("is-referenced-by-count"),
                venue=(ct[0] if ct else ""),
                authors=authors,
                url=w.get("URL") or "",
            ))
        return out


class EuropePMCSource(LiteratureSource):
    """Europe PMC。PubMed 的完整镜像，且提供开放获取全文与化学交叉文献。
    在 NCBI 直连不可达的网络环境下，这是 PubMed 的首选替代。"""

    name = "europepmc"
    label_zh = "Europe PMC 全文索引"
    priority = 30
    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def search(self, query: str, limit: int = 10, year_from: int | None = None) -> list[Paper]:
        q = query
        if year_from:
            q = f'({query}) AND (FIRST_PDATE:[{year_from}-01-01 TO 2100-01-01])'
        params = {
            "query": q,
            "format": "json",
            "pageSize": str(min(limit, 100)),
            "resultType": "core",
        }
        url = self.BASE + "?" + urllib.parse.urlencode(params)
        data = http_get_json(url)

        out: list[Paper] = []
        for w in ((data.get("resultList") or {}).get("result") or []):
            title = (w.get("title") or "").strip()
            title = re.sub(r"<[^>]+>", "", title).strip()
            if not title:
                continue
            try:
                year = int(w.get("pubYear")) if w.get("pubYear") else None
            except (TypeError, ValueError):
                year = None
            authors = [a.strip() for a in (w.get("authorString") or "").rstrip(".").split(",") if a.strip()]
            abstract = re.sub(r"<[^>]+>", " ", w.get("abstractText") or "")
            abstract = re.sub(r"\s+", " ", abstract).strip()
            out.append(Paper(
                source=self.name,
                title=title,
                year=year,
                doi=(w.get("doi") or "").lower() or None,
                abstract=abstract,
                citations=w.get("citedByCount"),
                venue=w.get("journalTitle") or "",
                authors=authors,
                url=(f"https://europepmc.org/article/{w.get('source', 'MED')}/{w.get('id')}"
                     if w.get("id") else ""),
                is_oa=(w.get("isOpenAccess") == "Y"),
            ))
        return out


class ArXivSource(LiteratureSource):
    """arXiv 预印本。物理化学、计算化学、材料理论的前沿先行指标。"""

    name = "arxiv"
    label_zh = "arXiv 预印本"
    priority = 40
    BASE = "https://export.arxiv.org/api/query"
    NS = {"a": "http://www.w3.org/2005/Atom"}

    def search(self, query: str, limit: int = 10, year_from: int | None = None) -> list[Paper]:
        safe = re.sub(r"[^\w\s\-]", " ", query).strip()
        params = {
            "search_query": f"all:{safe}",
            "max_results": str(min(limit, 100)),
            "sortBy": "relevance",
        }
        url = self.BASE + "?" + urllib.parse.urlencode(params)
        raw = http_get(url)

        out: list[Paper] = []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise SourceError(f"XML 解析失败: {exc}") from exc

        for entry in root.findall("a:entry", self.NS):
            title = re.sub(r"\s+", " ", (entry.findtext("a:title", "", self.NS) or "")).strip()
            if not title:
                continue
            summary = re.sub(r"\s+", " ", (entry.findtext("a:summary", "", self.NS) or "")).strip()
            published = entry.findtext("a:published", "", self.NS) or ""
            year = None
            if len(published) >= 4 and published[:4].isdigit():
                year = int(published[:4])
            authors = []
            for a in entry.findall("a:author", self.NS)[:12]:
                nm = a.findtext("a:name", "", self.NS)
                if nm:
                    authors.append(nm)
            link = ""
            for lk in entry.findall("a:link", self.NS):
                if lk.get("rel") == "alternate":
                    link = lk.get("href") or ""
                    break
            doi = entry.findtext("{http://arxiv.org/schemas/atom}doi")
            out.append(Paper(
                source=self.name,
                title=title,
                year=year,
                doi=(doi or "").lower() or None,
                abstract=summary,
                citations=None,
                venue="arXiv preprint",
                authors=authors,
                url=link,
                is_oa=True,
            ))
        return out


# --------------------------------------------------------------------------
# 调度器：降级链 + 去重合并
# --------------------------------------------------------------------------

DEFAULT_SOURCES = [OpenAlexSource, CrossrefSource, EuropePMCSource, ArXivSource]


class SourceRegistry:
    """多源调度器。

    职责：
      1. 按优先级依次调用各数据源，单源失败自动降级（记录健康台账）
      2. 跨源去重，同篇论文保留信息最丰富的版本并合并概念标签
      3. 输出统一 Paper 列表供下游阶段消费
    """

    def __init__(self, source_classes=None, verbose: bool = True):
        classes = source_classes or DEFAULT_SOURCES
        self.sources: list[LiteratureSource] = [c() for c in classes]
        self.sources.sort(key=lambda s: s.priority)
        self.verbose = verbose
        self.health: dict[str, dict] = {
            s.name: {"label": s.label_zh, "ok": 0, "fail": 0, "last_error": ""}
            for s in self.sources
        }

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    def fetch(
        self,
        query: str,
        per_source: int = 12,
        year_from: int | None = None,
    ) -> tuple[list[Paper], dict]:
        """向所有源检索同一 query，返回（去重后文献, 运行台账）。"""
        collected: list[Paper] = []
        for src in self.sources:
            h = self.health[src.name]
            try:
                got = src.search(query, limit=per_source, year_from=year_from)
                h["ok"] += 1
                h["last_error"] = ""
                collected.extend(got)
                self._log(f"  [ok]   {src.label_zh:<22} 命中 {len(got)} 条")
            except SourceError as exc:
                h["fail"] += 1
                h["last_error"] = str(exc)
                self._log(f"  [降级] {src.label_zh:<22} 失败：{exc}")
            except Exception as exc:  # 适配器自身缺陷不应拖垮整体
                h["fail"] += 1
                h["last_error"] = f"{type(exc).__name__}: {exc}"
                self._log(f"  [降级] {src.label_zh:<22} 异常：{exc}")

        merged = self.dedupe(collected)
        ledger = {
            "query": query,
            "per_source": per_source,
            "raw_hits": len(collected),
            "unique_hits": len(merged),
            "duplicates_removed": len(collected) - len(merged),
            "sources": self.health,
        }
        return merged, ledger

    @staticmethod
    def dedupe(papers: list[Paper]) -> list[Paper]:
        """跨源去重：DOI 与标题双指纹联合判定，同篇记录合并取最丰富版本。

        为什么需要双指纹：同一篇论文在 arXiv 上常常没有 DOI，而在 OpenAlex
        或 Crossref 上有。若只按「有 DOI 用 DOI，否则用标题」的单键判定，
        这类记录永远无法被识别为重复。因此两个索引都要查，并且被合并记录
        的指纹也要一并登记，保证多源传递性合并（A~B 靠 DOI，B~C 靠标题）。
        """
        result: list[Paper] = []
        doi_index: dict[str, int] = {}
        ttl_index: dict[str, int] = {}

        for p in papers:
            if not p.title:
                continue
            idx = None
            if p.doi_key and p.doi_key in doi_index:
                idx = doi_index[p.doi_key]
            if idx is None and p.title_key and p.title_key in ttl_index:
                idx = ttl_index[p.title_key]

            if idx is None:
                result.append(p)
                idx = len(result) - 1
                cur = p
            else:
                cur = result[idx]
                if p.richness > cur.richness:
                    p.abstract = p.abstract or cur.abstract
                    p.citations = _max_opt(p.citations, cur.citations)
                    p.year = p.year or cur.year
                    p.concepts = list(dict.fromkeys((p.concepts or []) + (cur.concepts or [])))
                    result[idx] = p
                    cur = p
                else:
                    if not cur.abstract and p.abstract:
                        cur.abstract = p.abstract
                    cur.citations = _max_opt(cur.citations, p.citations)
                    cur.year = cur.year or p.year
                    cur.concepts = list(dict.fromkeys((cur.concepts or []) + (p.concepts or [])))

            for cand in (cur, p):
                if cand.doi_key:
                    doi_index[cand.doi_key] = idx
                if cand.title_key:
                    ttl_index[cand.title_key] = idx

        return result

    def probe_all(self) -> dict:
        """健康体检：逐个探针，用于运行前的连通性确认。

        必须捕获宽泛异常：适配器自身的代码缺陷（非网络问题）同样应被
        标记为不可用，而不是让整个体检流程中断。
        """
        result = {}
        for src in self.sources:
            try:
                src.search("chemistry", limit=1)
                result[src.name] = {"label": src.label_zh, "status": "在线"}
            except SourceError as exc:
                result[src.name] = {"label": src.label_zh, "status": f"不可用（{exc}）"}
            except Exception as exc:
                result[src.name] = {
                    "label": src.label_zh,
                    "status": f"异常（{type(exc).__name__}: {exc}）",
                }
        return result


if __name__ == "__main__":
    import sys

    reg = SourceRegistry()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print("=== 数据源健康体检 ===")
        for name, info in reg.probe_all().items():
            print(f"  {info['label']:<24} {info['status']}")
    else:
        q = " ".join(sys.argv[1:]) or "metal-organic framework CO2 capture"
        print(f"=== 检索：{q} ===")
        papers, ledger = reg.fetch(q, per_source=5)
        for p in papers[:10]:
            print(f"  - [{(p.year or '----')}] {(p.title or '')[:70]}  (引 {p.citations})")
        print(json.dumps(ledger, ensure_ascii=False, indent=2))
