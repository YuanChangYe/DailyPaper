"""论文首页缩略图：把 PDF 第一页渲染成 JPEG，写到 ``api/thumbs/<id>.jpg``，供网页的论文列表使用。

需要可选依赖 pymupdf；没有时跳过并记录警告。默认也会为索引里还没有缩略图的旧论文补做（有数量上限）。
"""

from __future__ import annotations

from pydantic import BaseModel

from ...core.interfaces import Renderer
from ...core.models import Candidate, Paper
from ...core.registry import register
from ...integrations.site.contract import SiteContract
from ..fulltext.loaders import PdfLoader


@register("renderer", "thumbnails")
class ThumbnailRenderer(Renderer):
    class Params(BaseModel):
        width: int = 480  # 像素宽度
        quality: int = 80
        max_per_run: int = 30  # 每次最多处理多少篇（含补做），控制 PDF 下载量
        backfill: bool = True

    def run(self, items: list[Candidate]) -> list[Candidate]:
        try:
            import pymupdf
        except ImportError:
            self.log.warning("未安装 pymupdf，跳过缩略图（pip install dailypaper[pdf]）")
            return items

        out_dir = self.config.content_dir / "api" / "thumbs"
        out_dir.mkdir(parents=True, exist_ok=True)
        papers: list[Paper] = [c.paper for c in items if c.note]
        if self.p.backfill:
            have = {p.id for p in papers}
            for e in SiteContract(self.config).load_index():
                if e.id not in have and e.url_pdf:
                    papers.append(Paper(id=e.id, title=e.title, url_pdf=e.url_pdf, source=e.source))
        loader = PdfLoader(self.ctx)
        done = 0
        for paper in papers:
            dst = out_dir / f"{paper.slug}.jpg"
            if dst.exists():
                continue
            if done >= self.p.max_per_run:
                break
            try:
                path = loader.pdf_path(paper)
                if not path:
                    continue
                with pymupdf.open(path) as doc:
                    page = doc[0]
                    zoom = self.p.width / max(page.rect.width, 1)
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                    dst.write_bytes(pix.tobytes("jpeg", jpg_quality=self.p.quality))
                done += 1
            except Exception as e:  # noqa: BLE001 - 缩略图失败不影响流水线
                self.log.warning("%s 缩略图失败: %s", paper.id, e)
        if done:
            self.log.info("生成 %d 张缩略图", done)
        return items
