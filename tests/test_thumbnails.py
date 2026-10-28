from __future__ import annotations

import pytest

from dailypaper.core.config import StageSpec
from dailypaper.core.io import read_json
from dailypaper.core.pipeline import Pipeline


def test_thumbnails_render_first_page(ctx, cfg, monkeypatch, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    # 造一个两页 PDF，并让加载器直接返回它（不联网）
    pdf = tmp_path / "paper.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    page.insert_text((40, 80), "Conformal Abstention for Agents", fontsize=18)
    doc.new_page()
    doc.save(pdf)
    doc.close()
    from dailypaper.plugins.fulltext import loaders

    monkeypatch.setattr(loaders.PdfLoader, "pdf_path", lambda self, paper: pdf)

    items = Pipeline.from_config(ctx).run()  # 生成索引
    read_ids = [c.id for c in items if c.note]
    Pipeline(ctx, [StageSpec(id="t", use="renderer.thumbnails", params={"width": 200})]).run(items)
    for pid in read_ids:
        jpg = cfg.content_dir / "api" / "thumbs" / f"{pid}.jpg"
        assert jpg.exists() and jpg.read_bytes()[:2] == b"\xff\xd8"  # JPEG
    # 索引里带站点信息，供静态网站读取 GitHub 链接
    assert read_json(cfg.content_dir / "api" / "index.json")["site"]["repo_url"].startswith("https://github.com/")


def test_thumbnails_skip_without_pdf(ctx, cfg, monkeypatch):
    pytest.importorskip("pymupdf")
    from dailypaper.plugins.fulltext import loaders

    monkeypatch.setattr(loaders.PdfLoader, "pdf_path", lambda self, paper: None)
    items = Pipeline.from_config(ctx).run()
    Pipeline(ctx, [StageSpec(id="t", use="renderer.thumbnails")]).run(items)
    assert not list((cfg.content_dir / "api" / "thumbs").glob("*.jpg"))
