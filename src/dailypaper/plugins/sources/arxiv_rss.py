"""arXiv 每日公告 RSS（rss.arxiv.org）：一次请求拿到当天公布的所有新论文，默认的数据源。

与 arXiv API 相比：只覆盖"今天公布"的论文（周末没有公告），但只需一个请求，不容易被限流。
补抓历史日期请改用 source.arxiv（API）。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from pydantic import BaseModel, Field

from ...core.interfaces import Source
from ...core.models import ARXIV_ID_RE, Candidate, Paper
from ...core.registry import register
from .arxiv import USER_AGENT

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}
RSS_URL = "https://rss.arxiv.org/atom/{cats}"
_ABSTRACT_RE = re.compile(r"Abstract:\s*(.*)", re.S)


def _t(el: ET.Element | None) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def parse_rss(xml_text: str, announce_types: set[str] | None = None) -> list[Paper]:
    root = ET.fromstring(xml_text)
    out: list[Paper] = []
    for e in root.findall("atom:entry", NS):
        atype = _t(e.find("arxiv:announce_type", NS))
        if announce_types and atype not in announce_types:
            continue
        m = ARXIV_ID_RE.match(_t(e.find("atom:id", NS)).rsplit(":", 1)[-1])
        if not m:
            continue
        pid, version = m.group(1), m.group(2) or ""
        summary = (e.find("atom:summary", NS).text or "") if e.find("atom:summary", NS) is not None else ""
        am = _ABSTRACT_RE.search(summary)
        abstract = " ".join((am.group(1) if am else summary).split())
        authors = [a.strip() for a in _t(e.find("dc:creator", NS)).split(",") if a.strip()]
        cats = [c.get("term", "") for c in e.findall("atom:category", NS)]
        pub = _t(e.find("atom:published", NS))
        try:
            published = datetime.fromisoformat(pub) if pub else None
        except ValueError:
            published = None
        out.append(
            Paper(
                id=pid,
                version=version,
                title=_t(e.find("atom:title", NS)),
                abstract=abstract,
                authors=authors,
                categories=cats,
                primary_category=cats[0] if cats else "",
                published=published,
                url_abs=f"https://arxiv.org/abs/{pid}",
                url_pdf=f"https://arxiv.org/pdf/{pid}{version}",
                url_html=f"https://arxiv.org/html/{pid}{version}",
                extra={"announce_type": atype},
            )
        )
    return out


@register("source", "arxiv_rss")
class ArxivRssSource(Source):
    class Params(BaseModel):
        categories: list[str] = Field(default_factory=lambda: ["cs.CL", "cs.LG", "cs.AI", "cs.MA", "stat.ML"])
        announce_types: list[str] = Field(default_factory=lambda: ["new", "cross"])  # 不含 replace（旧论文的新版本）
        timeout: float = 60

    def run(self, items: list[Candidate]) -> list[Candidate]:
        url = RSS_URL.format(cats="+".join(self.p.categories))
        r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.p.timeout, follow_redirects=True)
        r.raise_for_status()
        papers = parse_rss(r.text, set(self.p.announce_types))
        have = {c.id for c in items}
        new = [Candidate(paper=p) for p in papers if p.id not in have]
        self.log.info("arXiv RSS 返回 %d 篇（%s）", len(new), "/".join(self.p.announce_types))
        return items + new
