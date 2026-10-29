"""全文加载器（registry kind = ``fulltext_loader``）与按顺序尝试它们的 enricher stage。

内置加载器（在 pipeline.yaml 的 loaders 列表里按顺序尝试，第一个成功的生效）：
- ``arxiv_html``  arXiv 的 HTML 版本（保留章节结构，公式转为 LaTeX）  → structure-grounded
- ``pdf``         下载 PDF 并用 PyMuPDF 提取，带 [p.N] 页码标记       → page-grounded
- ``local_pdf``   本地 PDF（Zotero 反向精读时 paper.extra["pdf_path"]） → page-grounded
- ``abstract``    只用摘要（兜底）                                     → source-limited
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from ...core import registry
from ...core.context import RunContext
from ...core.interfaces import Enricher
from ...core.io import write_text
from ...core.models import Candidate, FulltextInfo, Paper
from ...core.registry import register
from ..sources.arxiv import USER_AGENT

log = logging.getLogger("dailypaper.fulltext")

_REFS_RE = re.compile(r"^\s*(references|bibliography|参考文献)\s*$", re.I | re.M)


class FulltextLoader(ABC):
    grounding = "structure-grounded"

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def load(self, paper: Paper) -> str | None:
        """返回全文文本；不适用或失败时返回 None。"""


def _http_get(url: str, timeout: float = 60) -> httpx.Response:
    from ...integrations.venues.openreview_auth import auth_headers

    return httpx.get(url, headers={"User-Agent": USER_AGENT, **auth_headers(url)}, timeout=timeout, follow_redirects=True)


@register("fulltext_loader", "arxiv_lookup")
class ArxivLookupLoader(FulltextLoader):
    """非 arXiv 来源的论文（顶会论文池、Zotero）：按标题在 arXiv 上找同名版本，找到就按 arXiv HTML 读全文，并把 arXiv id 记回论文。"""

    grounding = "structure-grounded"

    def load(self, paper: Paper) -> str | None:
        if paper.source == "arxiv" or paper.url_html or not paper.title.strip():
            return None
        from urllib.parse import quote

        from ...integrations.venues.pool import normalize_title
        from ..sources.arxiv import API_URL, parse_atom

        self.grounding = "structure-grounded"
        title = " ".join(paper.title.split())[:200].replace('"', " ")
        try:
            r = _http_get(f"{API_URL}?search_query=ti:%22{quote(title)}%22&max_results=5")
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        want = normalize_title(paper.title)
        hit = next((p for p in parse_atom(r.text) if normalize_title(p.title) == want), None)
        if hit is None:
            return None
        paper.url_html = paper.url_html or hit.url_html or f"https://arxiv.org/html/{hit.id}"
        paper.url_pdf = paper.url_pdf or hit.url_pdf
        paper.extra["arxiv_id"] = hit.id
        if paper.source == "venue":
            try:
                from ...integrations.venues.pool import VenuePool

                VenuePool(self.ctx.config).set_arxiv(paper.id, hit.id, paper.url_html)
            except Exception:  # noqa: BLE001 - 记不回池子不影响读全文
                pass
        text = ArxivHtmlLoader(self.ctx).load(paper)
        if text:
            return text
        # 没有 HTML 版本时退回 arXiv 的 PDF
        self.grounding = "page-grounded"
        return PdfLoader(self.ctx).load(Paper(id=hit.id, title=hit.title, url_pdf=hit.url_pdf))


@register("fulltext_loader", "arxiv_html")
class ArxivHtmlLoader(FulltextLoader):
    grounding = "structure-grounded"

    def load(self, paper: Paper) -> str | None:
        if not paper.url_html:
            return None
        r = _http_get(paper.url_html)
        if r.status_code != 200 or "ltx_document" not in r.text:
            return None
        return html_to_text(r.text)


def html_to_text(html: str) -> str:
    """把 arXiv（LaTeXML）HTML 转成带标题层级、LaTeX 公式和表格的纯文本。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    doc = soup.select_one("article") or soup.select_one(".ltx_document") or soup.body
    if doc is None:
        return ""
    for sel in ["nav", "header", "footer", "script", "style", ".ltx_page_header", ".ltx_page_footer",
                ".ltx_bibliography", ".ltx_authors", ".ltx_dates", ".package-alerts", "button"]:
        for el in doc.select(sel):
            el.decompose()
    for m in doc.find_all("math"):
        tex = (m.get("alttext") or m.get_text(" ")).strip()
        block = m.get("display") == "block"
        m.replace_with(f" $${tex}$$ " if block else f" ${tex}$ ")
    for table in doc.select("table.ltx_tabular, table.ltx_equation, table.ltx_equationgroup"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [" ".join(td.get_text(" ").split()) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        table.replace_with("\n" + "\n".join(rows) + "\n")
    for level in range(1, 7):
        for h in doc.find_all(f"h{level}"):
            h.insert(0, "\n\n" + "#" * min(level + 1, 6) + " ")
            h.append("\n\n")
    for el in doc.select("p, figcaption, li, .ltx_para, .ltx_caption"):
        el.append("\n\n")
    text = doc.get_text("")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


@register("fulltext_loader", "pdf")
class PdfLoader(FulltextLoader):
    grounding = "page-grounded"

    def pdf_path(self, paper: Paper) -> Path | None:
        if not paper.url_pdf:
            return None
        path = self.ctx.config.cache_dir / "pdf" / f"{paper.slug}.pdf"
        if not path.exists():
            r = _http_get(paper.url_pdf, timeout=120)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return path

    def load(self, paper: Paper) -> str | None:
        path = self.pdf_path(paper)
        return pdf_to_text(path) if path else None


@register("fulltext_loader", "local_pdf")
class LocalPdfLoader(FulltextLoader):
    grounding = "page-grounded"

    def load(self, paper: Paper) -> str | None:
        p = paper.extra.get("pdf_path")
        return pdf_to_text(Path(p)) if p and Path(p).exists() else None


def pdf_to_text(path: Path) -> str:
    import pymupdf  # 可选依赖：pip install dailypaper[pdf]

    pages = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            pages.append(f"[p.{i}]\n{page.get_text('text').strip()}")
    text = "\n\n".join(pages)
    # 去掉参考文献（只在后半部分查找，避免误删正文里的 "References" 小标题）
    matches = [m for m in _REFS_RE.finditer(text) if m.start() > len(text) * 0.4]
    if matches:
        cut = matches[0].start()
        appendix = re.search(r"^\s*(appendix|supplementary)", text[cut:], re.I | re.M)
        text = text[:cut] + ("\n\n[Appendix]\n" + text[cut + appendix.start():] if appendix else "")
    return text


@register("fulltext_loader", "abstract")
class AbstractLoader(FulltextLoader):
    grounding = "source-limited"

    def load(self, paper: Paper) -> str | None:
        return f"[Abstract]\n{paper.abstract}" if paper.abstract else None


@register("enricher", "fulltext")
class FulltextEnricher(Enricher):
    class Params(BaseModel):
        loaders: list[str] = Field(default_factory=lambda: ["arxiv_html", "pdf", "abstract"])
        max_chars: int = 90000  # 约 2.5 万 token；超出部分（多为附录）截断

    def run(self, items: list[Candidate]) -> list[Candidate]:
        loaders = [(n, registry.get("fulltext_loader", n)(self.ctx)) for n in self.p.loaders]
        out_dir = self.config.cache_dir / "fulltext"
        for c in self.selected(items):
            for name, loader in loaders:
                try:
                    text = loader.load(c.paper)
                except Exception as e:  # noqa: BLE001 - 换下一个加载器
                    self.log.warning("%s 用 %s 加载失败: %s", c.id, name, e)
                    text = None
                if not text or len(text) < 200 and name != "abstract":
                    continue
                if len(text) > self.p.max_chars:
                    text = text[: self.p.max_chars] + "\n\n[……后文因长度限制截断……]"
                path = out_dir / f"{c.paper.slug}.md"
                write_text(path, text)
                c.fulltext = FulltextInfo(path=str(path), loader=name, grounding=loader.grounding, chars=len(text))
                self.log.info("%s 全文来自 %s（%d 字符）", c.id, name, len(text))
                break
            else:
                c.errors.append("fulltext: 所有加载器都失败")
        return items
