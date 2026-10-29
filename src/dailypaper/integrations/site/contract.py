"""站点数据契约：任何网站都可以只依赖这些文件来展示 DailyPaper 的内容。

内容目录（默认 docs/）下：
- ``papers/YYYY/MM/<id>.md``     单篇精读笔记（YAML frontmatter + Markdown）
- ``daily/YYYY-MM-DD.md``        每日 digest
- ``api/index.json``             所有已精读论文的索引（IndexEntry 列表，按日期、分数降序）
- ``api/daily/YYYY-MM-DD.json``  某天的 digest 数据
- ``api/papers/<id>.json``       单篇的完整数据（含精读 Markdown）
- ``feed.xml``                   RSS 2.0（最近 50 篇）

字段只增不删，保证向后兼容；``schema_version`` 变化时会在 README 说明。
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from ... import __version__
from ...core.config import AppConfig
from ...core.io import read_json, write_json, write_text
from ...core.models import Candidate

SCHEMA_VERSION = 1


class IndexEntry(BaseModel):
    id: str
    title: str
    date: str
    authors: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    paper_type: str = ""
    value_score: int = 0
    score: float = 0.0
    tldr: str = ""
    core_insight: str = ""
    abstract: str = ""  # 截断的原文摘要，供网页搜索
    note_path: str = ""  # 相对内容目录，正斜杠
    url_abs: str = ""
    url_pdf: str = ""
    source: str = "arxiv"


def entry_from_candidate(c: Candidate, date: str) -> IndexEntry:
    n = c.note
    return IndexEntry(
        id=c.id,
        title=c.paper.title,
        date=date,
        authors=c.paper.authors[:20],
        topics=c.main_topics(),
        paper_type=c.screen.paper_type if c.screen else "",
        value_score=n.value_score if n else 0,
        score=c.score,
        tldr=n.tldr if n else "",
        core_insight=n.core_insight if n else "",
        abstract=c.paper.abstract[:800],
        note_path=c.note_path,
        url_abs=c.paper.url_abs,
        url_pdf=c.paper.url_pdf,
        source=c.paper.source,
    )


class SiteContract:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.root: Path = cfg.content_dir
        self.api = self.root / "api"

    def load_index(self) -> list[IndexEntry]:
        data = read_json(self.api / "index.json", {}) or {}
        return [IndexEntry.model_validate(e) for e in data.get("papers", [])]

    def update_index(self, entries: list[IndexEntry]) -> list[IndexEntry]:
        by_id = {e.id: e for e in self.load_index()}
        for e in entries:
            by_id[e.id] = e
        for e in by_id.values():  # 旧索引没有摘要字段时，从单篇 JSON 补上
            if not e.abstract:
                data = read_json(self.api / "papers" / f"{e.id.replace('/', '_')}.json", {}) or {}
                e.abstract = str(data.get("abstract", ""))[:800]
        merged = sorted(by_id.values(), key=lambda e: (e.date, e.score), reverse=True)
        write_json(
            self.api / "index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "site": {
                    "title": self.cfg.settings.site.title,
                    "base_url": self.cfg.settings.site.base_url,
                    "repo_url": self.cfg.settings.site.repo_url,
                    "schema": self.cfg.note_schema.to_public(),
                    "version": __version__,
                },
                "papers": [e.model_dump() for e in merged],
            },
        )
        return merged

    def write_daily(self, date: str, selected: list[IndexEntry], others: list[dict]) -> None:
        write_json(
            self.api / "daily" / f"{date}.json",
            {"schema_version": SCHEMA_VERSION, "date": date, "papers": [e.model_dump() for e in selected], "also": others},
        )

    def write_paper(self, entry: IndexEntry, markdown: str, extra: dict) -> None:
        write_json(
            self.api / "papers" / f"{entry.id.replace('/', '_')}.json",
            {"schema_version": SCHEMA_VERSION, **entry.model_dump(), "markdown": markdown, **extra},
        )

    def write_feed(self, index: list[IndexEntry], limit: int = 50) -> None:
        site = self.cfg.settings.site
        base = site.base_url.rstrip("/")
        items = []
        for e in index[:limit]:
            page = e.note_path[:-3] + "/" if e.note_path.endswith(".md") else e.note_path
            link = f"{base}/{page}" if base else e.url_abs
            try:
                pub = format_datetime(datetime.fromisoformat(e.date).replace(tzinfo=timezone.utc))
            except ValueError:
                pub = ""
            items.append(
                "<item>"
                f"<title>{escape(e.title)}</title><link>{escape(link)}</link><guid isPermaLink=\"false\">{escape(e.id)}</guid>"
                f"<pubDate>{pub}</pubDate><description>{escape(e.tldr or e.core_insight)}</description>"
                + "".join(f"<category>{escape(t)}</category>" for t in e.topics)
                + "</item>"
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{escape(site.title)}</title><link>{escape(base or 'https://arxiv.org')}</link>"
            f"<description>{escape(site.title)} 每日论文精读</description>" + "".join(items) + "</channel></rss>\n"
        )
        write_text(self.root / "feed.xml", xml)
