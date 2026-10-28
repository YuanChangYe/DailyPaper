"""Markdown 渲染：单篇精读笔记 + 每日 digest。

同一份 Markdown 同时是 MkDocs 网站的源文件和 Obsidian 的笔记：
- frontmatter 带 tags / topics / rating / status / zotero_approve 等字段，Obsidian 可直接筛选
- 重新生成某篇笔记时，保留你在 Obsidian 里改过的字段（PRESERVED_FIELDS），只更新正文
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from pydantic import BaseModel

from ...core.interfaces import Renderer
from ...core.io import join_frontmatter, read_markdown, write_text
from ...core.models import Candidate
from ...core.registry import register

PRESERVED_FIELDS = ("status", "rating", "zotero_approve", "zotero_key", "citekey", "zotero_link", "my_tags", "notes")

TOPIC_PAGE_DIR = "topics"


def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n) if n else "—"


def rel_link(from_path: str, to_path: str) -> str:
    """内容目录内两个文件之间的相对链接（正斜杠，兼容 MkDocs 与 Obsidian）。"""
    return posixpath.relpath(to_path, posixpath.dirname(from_path) or ".")


@register("renderer", "markdown")
class MarkdownRenderer(Renderer):
    class Params(BaseModel):
        digest: bool = True  # read 流水线不生成每日 digest
        also_threshold: float = 5.0  # 未精读但分数达到该值的论文列入"也值得关注"
        also_limit: int = 15

    # ------------------------------------------------------------ 单篇
    def note_rel_path(self, c: Candidate) -> str:
        d = self.ctx.run_date
        return f"papers/{d:%Y}/{d:%m}/{c.paper.slug}.md"

    def topic_names(self, ids: list[str]) -> list[str]:
        return [(self.config.topic(t).name if self.config.topic(t) else t) for t in ids]

    def render_note(self, c: Candidate) -> tuple[dict, str]:
        p, n, s = c.paper, c.note, c.screen
        topics = c.main_topics()
        fm = {
            "title": p.title,
            "paper_id": p.id,
            "source": p.source,
            "authors": p.authors[:20],
            "date": self.ctx.run_date.isoformat(),
            "published": p.published.date().isoformat() if p.published else None,
            "topics": topics,
            "tags": ["dailypaper", *topics, *([s.paper_type] if s else [])],
            "paper_type": s.paper_type if s else "",
            "value_score": n.value_score,
            "score": c.score,
            "tldr": n.tldr,
            "lenses": n.lenses,
            "model": n.model,
            "grounding": n.grounding,
            "url_abs": p.url_abs,
            "url_pdf": p.url_pdf,
            "status": "unread",
            "rating": None,
            "zotero_approve": False,
            "zotero_key": None,
            "citekey": None,
        }
        meta_bits = [
            f"**作者** {', '.join(p.authors[:6])}{' 等' if len(p.authors) > 6 else ''}",
            f"**来源** [{p.id}{p.version}]({p.url_abs})" + (f" · [PDF]({p.url_pdf})" if p.url_pdf else ""),
            f"**方向** {' / '.join(self.topic_names(topics)) or '—'}",
            f"**类型** {s.paper_type if s else '—'}",
            f"**价值** {stars(n.value_score)}",
        ]
        body = [
            f"# {p.title}",
            "> " + " · ".join(meta_bits),
            f"> **TL;DR** {n.tldr}" if n.tldr else "",
            f"> **Core insight** {n.core_insight}" if n.core_insight else "",
            f"> **读不读** {n.worth_reading}" if n.worth_reading else "",
            n.markdown,
            "---",
            f"<details><summary>原文摘要</summary>\n\n{p.abstract}\n\n</details>",
            f"<small>精读模型 {n.model} · 全文来源 {c.fulltext.loader if c.fulltext else 'abstract'}（{n.grounding}）"
            f" · lens: {', '.join(n.lenses) or '无'} · 筛选理由：{s.reason if s else '—'}</small>",
        ]
        return fm, "\n\n".join(x for x in body if x)

    def write_note(self, c: Candidate) -> str:
        rel = self.note_rel_path(c)
        # 同一篇论文以前写过（可能在别的日期目录）：沿用原路径，保留用户字段
        existing = next(iter(self.config.content_dir.glob(f"papers/*/*/{c.paper.slug}.md")), None)
        if existing is not None:
            rel = existing.relative_to(self.config.content_dir).as_posix()
        fm, body = self.render_note(c)
        path = self.config.content_dir / rel
        if path.exists():
            old_fm, _ = read_markdown(path)
            for k in PRESERVED_FIELDS:
                if old_fm.get(k) not in (None, ""):
                    fm[k] = old_fm[k]
            fm["date"] = old_fm.get("date", fm["date"])
        write_text(path, join_frontmatter(fm, body))
        return rel

    # ------------------------------------------------------------ digest
    def write_digest(self, items: list[Candidate]) -> str:
        d = self.ctx.run_date.isoformat()
        rel = f"daily/{d}.md"
        read = [c for c in items if c.note]
        also = [c for c in items if not c.note and c.score >= self.p.also_threshold][: self.p.also_limit]
        lines = [
            f"# {d} 论文日报",
            f"今日候选 {len(items)} 篇（关键词预筛后），精读 {len(read)} 篇。",
            "## 精读",
        ]
        if not read:
            lines.append("今天没有达到精读阈值的论文。")
        for i, c in enumerate(read, 1):
            n = c.note
            link = rel_link(rel, c.note_path)
            lines += [
                f"### {i}. [{c.paper.title}]({link})",
                f"{stars(n.value_score)} · {' / '.join(self.topic_names(c.main_topics()))} · "
                f"{c.screen.paper_type if c.screen else ''} · 排序分 {c.score}",
                f"- **TL;DR** {n.tldr}",
                f"- **Core insight** {n.core_insight}",
                f"- **读不读** {n.worth_reading}",
            ]
        if also:
            lines += ["## 也值得关注", "| 分数 | 论文 | 方向 | 理由 |", "|---:|---|---|---|"]
            for c in also:
                reason = (c.screen.reason if c.screen else "").replace("|", "/")
                lines.append(
                    f"| {c.score} | [{c.paper.title.replace('|', '/')}]({c.paper.url_abs}) | "
                    f"{' / '.join(self.topic_names(c.main_topics()))} | {reason} |"
                )
        fm = {"title": f"{d} 论文日报", "date": d, "tags": ["dailypaper", "daily"]}
        write_text(self.config.content_dir / rel, join_frontmatter(fm, "\n\n".join(lines)))
        return rel

    def run(self, items: list[Candidate]) -> list[Candidate]:
        for c in items:
            if c.note:
                c.note_path = self.write_note(c)
        if self.p.digest:
            rel = self.write_digest(items)
            self.ctx.inputs["digest_path"] = rel
            self.log.info("日报: %s", Path(self.config.content_dir, rel))
        return items
