"""arXiv 数据源：按分类和提交日期从 arXiv API 拉取新论文。

arXiv API 要求请求间隔约 3 秒；每天几个分类合计一两千篇，约十几次请求、一分钟左右。
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from ...core.interfaces import Source
from ...core.models import ARXIV_ID_RE, Candidate, Paper
from ...core.registry import register

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "DailyPaper/0.1 (personal research digest)"


def _text(el: ET.Element | None) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def _dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def parse_atom(xml_text: str) -> list[Paper]:
    """把 arXiv API 返回的 Atom XML 解析成 Paper 列表。"""
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", NS):
        raw_id = _text(entry.find("atom:id", NS)).rsplit("/abs/", 1)[-1]
        m = ARXIV_ID_RE.match(raw_id)
        if not m:  # API 出错时会返回一个 id 为错误页的 entry
            continue
        pid, version = m.group(1), m.group(2) or ""
        pdf = next(
            (ln.get("href", "") for ln in entry.findall("atom:link", NS) if ln.get("title") == "pdf"),
            f"https://arxiv.org/pdf/{pid}{version}",
        )
        primary = entry.find("arxiv:primary_category", NS)
        papers.append(
            Paper(
                id=pid,
                version=version,
                title=_text(entry.find("atom:title", NS)),
                abstract=_text(entry.find("atom:summary", NS)),
                authors=[_text(a.find("atom:name", NS)) for a in entry.findall("atom:author", NS)],
                categories=[c.get("term", "") for c in entry.findall("atom:category", NS)],
                primary_category=primary.get("term", "") if primary is not None else "",
                published=_dt(_text(entry.find("atom:published", NS))),
                updated=_dt(_text(entry.find("atom:updated", NS))),
                doi=_text(entry.find("arxiv:doi", NS)),
                comment=_text(entry.find("arxiv:comment", NS)),
                url_abs=f"https://arxiv.org/abs/{pid}",
                url_pdf=pdf.replace("http://", "https://"),
                url_html=f"https://arxiv.org/html/{pid}{version}",
            )
        )
    return papers


def fetch_by_ids(ids: list[str], timeout: float = 60) -> list[Paper]:
    """按 arXiv ID 精确获取（read 命令用）。"""
    r = httpx.get(
        API_URL,
        params={"id_list": ",".join(ids), "max_results": len(ids)},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    return parse_atom(r.text)


@register("source", "arxiv")
class ArxivSource(Source):
    class Params(BaseModel):
        categories: list[str] = Field(default_factory=lambda: ["cs.CL", "cs.LG", "cs.AI", "cs.MA", "stat.ML"])
        lookback_days: int = 3  # 覆盖周末；重复的由 filter.seen 去掉
        page_size: int = 200
        max_results: int = 3000
        delay_seconds: float = 3.0
        timeout: float = 60
        retries: int = 4
        backoff_seconds: float = 20  # 被限流（429/503）时第 n 次重试等待 n × 该值

    def _query(self) -> str:
        end = self.ctx.run_date
        start = end - timedelta(days=self.p.lookback_days)
        cats = " OR ".join(f"cat:{c}" for c in self.p.categories)
        return f"({cats}) AND submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]"

    def _get_page(self, client: httpx.Client, query: str, start: int) -> list[Paper]:
        params = {
            "search_query": query,
            "start": start,
            "max_results": self.p.page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        for attempt in range(1, self.p.retries + 1):
            try:
                r = client.get(API_URL, params=params)
                r.raise_for_status()
                return parse_atom(r.text)
            except (httpx.HTTPError, ET.ParseError) as e:
                wait = self.p.backoff_seconds * attempt
                if isinstance(e, httpx.HTTPStatusError):
                    retry_after = e.response.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                self.log.warning("arXiv 第 %d 次请求失败，%ds 后重试: %s", attempt, wait, e)
                time.sleep(wait)
        raise RuntimeError("arXiv API 多次请求失败（可能被限流，稍后再试，或改用 source.arxiv_rss）")

    def run(self, items: list[Candidate]) -> list[Candidate]:
        query = self._query()
        self.log.info("arXiv 查询: %s", query)
        seen: set[str] = {c.id for c in items}
        new: list[Candidate] = []
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=self.p.timeout, follow_redirects=True) as client:
            start = 0
            while start < self.p.max_results:
                papers = self._get_page(client, query, start)
                for p in papers:
                    if p.id not in seen:
                        seen.add(p.id)
                        new.append(Candidate(paper=p))
                if len(papers) < self.p.page_size:
                    break
                start += self.p.page_size
                time.sleep(self.p.delay_seconds)
        self.log.info("arXiv 返回 %d 篇新论文", len(new))
        return items + new
