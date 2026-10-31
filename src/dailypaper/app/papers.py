"""论文 PDF 与全文的获取（本地助手的 PDF 阅读、出处定位、问答共用）。"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.config import AppConfig
from ..core.models import Paper

ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")


class PaperNotFound(ValueError):
    pass


def index_entry(cfg: AppConfig, paper_id: str):
    from ..integrations.site.contract import SiteContract

    return next((e for e in SiteContract(cfg).load_index() if e.id == paper_id), None)


def paper_of(cfg: AppConfig, paper_id: str) -> Paper:
    entry = index_entry(cfg, paper_id)
    if not ARXIV_RE.match(paper_id) and not (entry and entry.url_pdf):
        raise PaperNotFound("只支持 arXiv ID 或索引里带 PDF 链接的论文")
    return Paper(
        id=paper_id,
        title=entry.title if entry else "",
        abstract=entry.abstract if entry else "",
        url_pdf=(entry.url_pdf if entry else "") or f"https://arxiv.org/pdf/{paper_id}",
    )


def ensure_pdf(cfg: AppConfig, paper_id: str) -> Path:
    """返回本地缓存的 PDF（.cache/pdf/<slug>.pdf），没有就下载。失败抛 RuntimeError。"""
    import httpx

    from ..integrations.venues.openreview_auth import auth_headers

    paper = paper_of(cfg, paper_id)
    path = cfg.cache_dir / "pdf" / f"{paper.slug}.pdf"
    if path.exists():
        return path
    try:
        r = httpx.get(paper.url_pdf, timeout=120, follow_redirects=True,
                      headers={"User-Agent": "DailyPaper/0.1 (personal research digest)", **auth_headers(paper.url_pdf)})
    except httpx.HTTPError as e:
        raise RuntimeError(f"下载 PDF 失败: {e}") from e
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"没有拿到 PDF（HTTP {r.status_code}）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return path


def static_hash(static_dir: Path) -> str:
    """网页文件（index.html + assets/*.js|css）的修改时间摘要：变了就提示已打开的网页刷新。"""
    import hashlib

    h = hashlib.sha1()
    files = [static_dir / "index.html", *sorted((static_dir / "assets").glob("*.js")), *sorted((static_dir / "assets").glob("*.css"))]
    for f in files:
        if f.exists():
            h.update(f"{f.name}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()[:12]
