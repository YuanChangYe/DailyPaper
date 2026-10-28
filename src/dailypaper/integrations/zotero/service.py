"""Zotero 联动的统一入口：组合读/写后端，提供入库、去重索引、兴趣画像、反向精读、诊断。"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterator

from ...core import registry
from ...core.config import AppConfig
from ...core.context import RunContext
from ...core.io import read_markdown, update_frontmatter
from ...core.models import Paper
from ..obsidian.feedback import scan_notes
from ..review import ReviewItem, ReviewStore
from .base import AddRequest, ZoteroReader, ZoteroRef, ZoteroWriter
from .citekey import bbt_citekey, make_citekey
from .profile import export_library

log = logging.getLogger("dailypaper.zotero")

_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def markdown_to_html(md: str) -> str:
    try:
        import markdown  # mkdocs 的依赖，通常已安装

        return markdown.markdown(md, extensions=["tables", "fenced_code"])
    except ImportError:
        return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in md.split("\n\n"))


class ZoteroService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        z = cfg.settings.zotero
        self.reader: ZoteroReader | None = None if z.reader == "none" else registry.get("zotero_reader", z.reader)(cfg)
        self.writer: ZoteroWriter | None = None if z.writer == "none" else registry.get("zotero_writer", z.writer)(cfg)

    @classmethod
    def from_config(cls, cfg: AppConfig) -> ZoteroService:
        return cls(cfg)

    @classmethod
    def from_context(cls, ctx: RunContext) -> ZoteroService:
        return ctx.service("zotero", lambda: cls(ctx.config))

    # ------------------------------------------------------------ 诊断
    def diagnose(self) -> list[str]:
        z = self.cfg.settings.zotero
        lines = []
        if self.reader:
            ok, msg = self.reader.available()
            lines.append(f"{'✓' if ok else '✗'} 读取（{z.reader}）: {msg}")
        if self.writer:
            ok, msg = self.writer.available()
            lines.append(f"{'✓' if ok else '✗'} 写入（{z.writer}）: {msg}")
            paths = self.writer.collection_paths() if ok else []
            if paths:
                lines.append(f"  可选分类 {len(paths)} 个，例如: {', '.join(paths[:8])}")
                if not any(p.lower().startswith("dailypaper") for p in paths):
                    lines.append("  提示：Zotero 中还没有 DailyPaper 分类；建议新建一个，审核通过的论文会默认放进去")
        base = z.local_api.get("base_url", "http://127.0.0.1:23119")
        bbt = _bbt_alive(base)
        lines.append(f"{'✓' if bbt else '–'} Better BibTeX: {'可用' if bbt else '未安装（citekey 将按 姓+年份+标题词 生成）'}")
        return lines

    # ------------------------------------------------------------ 读
    def export_profile(self) -> dict:
        if not self.reader:
            raise RuntimeError("zotero.reader 为 none，无法读取文库")
        prof = self.cfg.settings.zotero.profile
        items = self.reader.iter_items(prof.get("collections") or None, int(prof.get("max_items", 3000)))
        return export_library(self.cfg, items)

    def paper_from_item(self, key: str) -> Paper:
        if not self.reader:
            raise RuntimeError("zotero.reader 为 none，无法读取条目")
        d = self.reader.get_item(key)
        m = _ARXIV_RE.search(" ".join(str(d.get(k, "")) for k in ("url", "extra", "archiveID", "number")))
        authors = [
            (c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}").strip()
            for c in d.get("creators", [])
            if c.get("creatorType", "author") == "author"
        ]
        pdf = self.reader.pdf_path(key)
        common = dict(title=d.get("title", ""), abstract=d.get("abstractNote", ""), authors=authors, doi=d.get("DOI", ""),
                      extra={"zotero_key": key, **({"pdf_path": pdf} if pdf else {})})
        if m:
            aid = m.group(1)
            return Paper(id=aid, source="arxiv", url_abs=f"https://arxiv.org/abs/{aid}", url_pdf=f"https://arxiv.org/pdf/{aid}",
                         url_html=f"https://arxiv.org/html/{aid}", **common)
        return Paper(id=f"zotero-{key}", source="zotero", url_abs=d.get("url", "") or f"zotero://select/library/items/{key}", **common)

    # ------------------------------------------------------------ 写
    def import_item(self, item: ReviewItem, collection: str | None = None) -> dict:
        """把一篇审核通过的论文写入 Zotero，回写 citekey 到 Obsidian 笔记。返回要记入 Decision 的字段。"""
        if not self.writer:
            raise RuntimeError("zotero.writer 为 none")
        ok, msg = self.writer.available()
        if not ok:
            raise RuntimeError(msg)
        e = item.entry
        z = self.cfg.settings.zotero
        topic = self.cfg.topic(e.topics[0]) if e.topics else None
        coll = collection if collection is not None else (item.decision.collection or z.default_collection.format(
            topic_id=topic.id if topic else "", topic_name=topic.name if topic else ""
        ).strip("/"))
        note_html = ""
        if z.add_note and e.note_path:
            path = self.cfg.content_dir / e.note_path
            if path.exists():
                _, body = read_markdown(path)
                note_html = f"<h1>DailyPaper 精读：{html.escape(e.title)}</h1>" + markdown_to_html(body)
        arxiv_id = e.id if e.source == "arxiv" else ""
        req = AddRequest(
            title=e.title,
            authors=e.authors,
            abstract=e.abstract,
            date=e.date,
            url=e.url_abs,
            pdf_url=e.url_pdf,
            arxiv_id=arxiv_id,
            doi=e.doi,
            collection=coll,
            tags=[*z.tags, *e.topics, f"value-{e.value_score}"],
            note_html=note_html,
            extra=f"arXiv: {arxiv_id}" if arxiv_id else "",
        )
        if self.reader:
            existing = self.reader.find(e.title, arxiv_id, e.doi)
            if existing:
                return self._finish(item, existing, "已在 Zotero 中，跳过写入", coll)
        res = self.writer.add(req)
        if not res.ok:
            raise RuntimeError(res.message)
        ref = res.ref
        if ref is None and hasattr(self.writer, "find_after_save"):
            ref = self.writer.find_after_save(req)
        msg = res.message + ("" if res.pdf_attached in (None, True) else "（PDF 未能附加）")
        return self._finish(item, ref, msg, res.collection_used or coll)

    def _finish(self, item: ReviewItem, ref: ZoteroRef | None, msg: str, coll: str) -> dict:
        e = item.entry
        base = self.cfg.settings.zotero.local_api.get("base_url", "http://127.0.0.1:23119")
        citekey = (bbt_citekey(base, ref.key) if ref else None) or make_citekey(e.authors, e.date[:4], e.title)
        if e.note_path:
            path = self.cfg.content_dir / e.note_path
            if path.exists():
                update_frontmatter(
                    path,
                    zotero_key=ref.key if ref else None,
                    zotero_link=ref.select_link if ref else None,
                    citekey=citekey,
                    zotero_approve=True,
                )
        return {"zotero_key": ref.key if ref else "", "citekey": citekey, "collection": coll, "error": "", "message": msg}


def _bbt_alive(base: str) -> bool:
    import httpx

    try:
        r = httpx.post(f"{base.rstrip('/')}/better-bibtex/json-rpc",
                       json={"jsonrpc": "2.0", "method": "api.ready", "params": [], "id": 1}, timeout=5)
        return r.status_code == 200 and "result" in r.text
    except Exception:  # noqa: BLE001
        return False


def import_approved(cfg: AppConfig, service: ZoteroService) -> Iterator[str]:
    """把所有状态为 approved 的论文写入 Zotero，逐条产出结果说明。"""
    store = ReviewStore(cfg)
    for item in store.items(status="approved"):
        try:
            fields = service.import_item(item)
            message = fields.pop("message")
            store.decide(item.entry.id, "imported", **fields)
            yield f"✓ {item.entry.id} {message}（citekey: {fields['citekey']}）"
        except Exception as e:  # noqa: BLE001
            store.decide(item.entry.id, "approved", error=str(e))
            yield f"✗ {item.entry.id} 入库失败，保持为已批准，稍后可重试: {e}"


def collect_obsidian_approvals(cfg: AppConfig) -> int:
    """Obsidian 中把 zotero_approve 设为 true 但还没入库的笔记 → 标记为已批准。"""
    store = ReviewStore(cfg)
    decisions = store.decisions()
    queued = {e.id for e in store.entries()}
    n = 0
    for note in scan_notes(cfg):
        if note.zotero_approve and not note.zotero_key and note.paper_id in queued:
            if decisions.get(note.paper_id) is None or decisions[note.paper_id].status in ("pending", "later"):
                store.decide(note.paper_id, "approved")
                n += 1
    return n
