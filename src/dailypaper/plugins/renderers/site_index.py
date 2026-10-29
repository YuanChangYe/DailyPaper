"""站点契约输出（api/*.json、feed.xml）+ 由索引生成的首页与方向页。"""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from ...core.interfaces import Renderer
from ...core.io import join_frontmatter, write_text
from ...core.models import Candidate
from ...core.registry import register
from ...integrations.site.contract import IndexEntry, SiteContract, entry_from_candidate
from .markdown import TOPIC_PAGE_DIR, rel_link, stars


@register("renderer", "site_index")
class SiteIndexRenderer(Renderer):
    class Params(BaseModel):
        home_days: int = 14  # 笔记目录页列出最近多少天的日报
        feed_limit: int = 50
        web_bundle: bool = True  # 把网页 index.html 和 assets/ 复制进内容目录

    def run(self, items: list[Candidate]) -> list[Candidate]:
        site = SiteContract(self.config)
        date = self.ctx.run_date.isoformat()
        read = [c for c in items if c.note and c.note_path]
        entries = [entry_from_candidate(c, date) for c in read]
        for c, e in zip(read, entries, strict=True):
            site.write_paper(e, c.note.markdown, {"lenses": c.note.lenses, "model": c.note.model, "abstract": c.paper.abstract})
        index = site.update_index(entries)
        if (self.config.content_dir / "daily" / f"{date}.md").exists() and self.ctx.pipeline == "daily":
            others = [
                {"id": c.id, "title": c.paper.title, "score": c.score, "url_abs": c.paper.url_abs,
                 "reason": c.screen.reason if c.screen else ""}
                for c in items if not c.note and c.score > 0
            ][:30]
            site.write_daily(date, entries, others, dict(self.ctx.notes))
        site.write_feed(index, self.p.feed_limit)
        self.write_topic_pages(index)
        self.write_home(index)
        if self.p.web_bundle:
            self.copy_web_bundle()
        return items

    def copy_web_bundle(self) -> None:
        """把网页（index.html + assets/）复制进内容目录，GitHub Pages 和本地助手用同一份文件。"""
        src = Path(__file__).resolve().parents[2] / "app" / "static"
        dst = self.config.content_dir
        if not (src / "index.html").exists():
            return
        shutil.copy2(src / "index.html", dst / "index.html")
        if (src / "assets").exists():
            shutil.copytree(src / "assets", dst / "assets", dirs_exist_ok=True)
        if (src / "javascripts").exists():
            shutil.copytree(src / "javascripts", dst / "javascripts", dirs_exist_ok=True)

    def write_topic_pages(self, index: list[IndexEntry]) -> None:
        by_topic: dict[str, list[IndexEntry]] = defaultdict(list)
        for e in index:
            for t in e.topics:
                by_topic[t].append(e)
        for topic in self.config.topics:
            rel = f"{TOPIC_PAGE_DIR}/{topic.id}.md"
            rows = [f"| 日期 | 价值 | 论文 | {self.config.note_schema.label('tldr')} |", "|---|---|---|---|"]
            for e in by_topic.get(topic.id, []):
                rows.append(
                    f"| {e.date} | {stars(e.value_score)} | [{e.title.replace('|', '/')}]({rel_link(rel, e.note_path)}) | "
                    f"{e.tldr.replace('|', '/')} |"
                )
            body = f"# {topic.name}\n\n{topic.description.strip()}\n\n共 {len(by_topic.get(topic.id, []))} 篇。\n\n" + "\n".join(rows)
            write_text(self.config.content_dir / rel, join_frontmatter({"title": topic.name, "tags": ["dailypaper", "topic"]}, body))

    def write_home(self, index: list[IndexEntry]) -> None:
        """Markdown 版目录页（notes.md）。网站首页是 index.html（网页）。"""
        site_title = self.config.settings.site.title
        dailies = sorted((self.config.content_dir / "daily").glob("*.md"), reverse=True)[: self.p.home_days]
        lines = [
            f"# {site_title} · 笔记目录",
            "每天自动筛选 arXiv 新论文，围绕 insight 与 novelty 精读。",
            "## 最近的日报",
            *[f"- [{p.stem}](daily/{p.name})" for p in dailies],
            "## 研究方向",
            *[f"- [{t.name}]({TOPIC_PAGE_DIR}/{t.id}.md)" for t in self.config.topics],
            "## 高价值论文",
        ]
        top = sorted((e for e in index if e.value_score >= 4), key=lambda e: (e.value_score, e.date), reverse=True)[:15]
        lines += [f"- {stars(e.value_score)} [{e.title}]({e.note_path}) · {e.date}" for e in top] or ["暂无。"]
        lines.append("\n[RSS](feed.xml) · [JSON](api/index.json)")
        write_text(self.config.content_dir / "notes.md", "\n\n".join(lines) + "\n")
        stale = self.config.content_dir / "index.md"  # 旧版本生成的 Markdown 首页，与 index.html 冲突
        if stale.exists() and "笔记目录" not in stale.read_text(encoding="utf-8")[:200]:
            stale.unlink()
