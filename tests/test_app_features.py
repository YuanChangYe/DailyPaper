"""网页新增功能的后端：高亮批注、趋势统计与总结、文献库在 Zotero 不可用时的表现。"""

from __future__ import annotations

import pytest

from dailypaper.core.io import read_markdown
from dailypaper.core.pipeline import Pipeline

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from dailypaper.app.server import create_app  # noqa: E402

H = {"X-DailyPaper": "1"}


@pytest.fixture
def client(ctx, cfg):
    Pipeline.from_config(ctx).run()
    cfg.settings.zotero.reader = "none"
    return TestClient(create_app(cfg), base_url="http://127.0.0.1")


def test_annotations_roundtrip_and_frontmatter(client, cfg):
    pid = client.get("/site/api/index.json").json()["papers"][0]["id"]
    assert client.get(f"/api/annotations/{pid}").json() == {"highlights": [], "note": "", "updated_at": ""}
    body = {"highlights": [{"id": "h1", "text": "Core insight", "color": "green", "comment": "重点", "section": "2"}], "note": "我的想法"}
    r = client.put(f"/api/annotations/{pid}", json=body, headers=H)
    assert r.status_code == 200 and r.json()["highlights"][0]["created_at"]
    got = client.get(f"/api/annotations/{pid}").json()
    assert got["note"] == "我的想法" and got["highlights"][0]["color"] == "green"
    note_path = next(e for e in client.get("/site/api/index.json").json()["papers"] if e["id"] == pid)["note_path"]
    fm, _ = read_markdown(cfg.content_dir / note_path)
    assert fm["notes"] == "我的想法" and fm["highlights"] == 1
    assert client.get("/api/annotations").json()[pid] == 2
    assert client.put(f"/api/annotations/{pid}", json={"highlights": [{"id": "x", "text": ""}]}, headers=H).status_code == 400


def test_trends_stats_and_summary(client, cfg):
    t = client.get("/api/trends?days=7").json()
    assert t["total"] == 2 and len(t["per_day"]) == 7 and sum(d["count"] for d in t["per_day"]) == 2
    assert t["per_topic"] and t["top"] and t["summary"] is None
    r = client.post("/api/trends/summary", json={"days": 7, "provider": "fake"}, headers=H)
    assert r.status_code == 200 and r.json()["summary"].strip()
    assert client.get("/api/trends?days=7").json()["summary"]  # 已缓存
    assert (cfg.data_dir / "trends").glob("*.md")
    assert client.post("/api/trends/summary", json={"days": 7, "end": "2020-01-01", "provider": "fake"}, headers=H).status_code == 400


def test_zotero_library_without_reader(client):
    assert client.get("/api/zotero/library").status_code == 503
    assert client.get("/api/zotero/items").status_code == 503


def test_covers_build_and_manifest(client, cfg):
    import shutil

    from dailypaper.integrations.covers import build_covers, covers_view, set_cover_meta, slugify, source_dir

    pytest.importorskip("pymupdf")
    assert slugify("梅里雪山 日照金山.jpg") and slugify("My Photo (1).JPG").startswith("my-photo-1-")
    src = source_dir(cfg)
    src.mkdir(parents=True)
    from pathlib import Path

    sample = Path(__file__).resolve().parents[1] / "src" / "dailypaper" / "app" / "static" / "assets" / "hero-sunrise-640.jpg"
    shutil.copy(sample, src / "随便 起个名字.jpg")
    shutil.copy(sample, src / "second.png")
    r = build_covers(cfg)
    assert r["count"] == 2 and r["built"] == 2
    out = cfg.content_dir / "assets" / "covers"
    assert (out / "covers.json").exists() and len(list(out.glob("cover-*-640.jpg"))) == 2
    assert build_covers(cfg)["skipped"] == 2  # 第二次不重做
    (src / "second.png").unlink()
    assert build_covers(cfg)["removed"] == 3  # 三个尺寸一起清掉
    view = covers_view(cfg)
    assert view["sources"] == 1 and view["covers"][0]["credit"] == "随便 起个名字"
    set_cover_meta(cfg, view["covers"][0]["id"], credit="某年某地", position="center 30%")
    assert covers_view(cfg)["covers"][0]["position"] == "center 30%"
    assert build_covers(cfg)["skipped"] == 1 and covers_view(cfg)["covers"][0]["credit"] == "某年某地"  # 重扫保留署名
    api = client.get("/api/covers").json()
    assert api["covers"][0]["files"]["640"].startswith("cover-")
    assert client.get("/site/assets/covers/covers.json").status_code == 200
    assert client.put(f"/api/covers/{api['covers'][0]['id']}", json={"credit": "x"}, headers=H).json()["credit"] == "x"
    assert client.post("/api/covers/build", json={}, headers=H).json()["count"] == 1
