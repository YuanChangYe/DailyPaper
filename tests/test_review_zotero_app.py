from __future__ import annotations

import pytest

from dailypaper.core.io import read_markdown, update_frontmatter
from dailypaper.core.pipeline import Pipeline
from dailypaper.core.registry import register
from dailypaper.integrations.obsidian.feedback import topic_multipliers
from dailypaper.integrations.review import ReviewStore
from dailypaper.integrations.zotero.base import AddResult, ZoteroRef, ZoteroWriter
from dailypaper.integrations.zotero.citekey import make_citekey
from dailypaper.integrations.zotero.profile import InterestProfile, LibraryIndex, export_library
from dailypaper.integrations.zotero.service import ZoteroService, collect_obsidian_approvals, import_approved

ADDED: list = []


@register("zotero_writer", "memory")
class MemoryWriter(ZoteroWriter):
    def available(self):
        return True, "ok"

    def add(self, req):
        ADDED.append(req)
        return AddResult(True, "ok", ref=ZoteroRef(key=f"K{len(ADDED)}"), collection_used=req.collection)


@pytest.fixture
def ran(ctx, cfg):
    Pipeline.from_config(ctx).run()
    cfg.settings.zotero.reader = "none"
    cfg.settings.zotero.writer = "memory"
    ADDED.clear()
    return cfg


def test_approve_imports_and_writes_back_citekey(ran):
    store = ReviewStore(ran)
    first = store.items("pending")[0]
    store.decide(first.entry.id, "approved", collection="DailyPaper")
    msgs = list(import_approved(ran, ZoteroService(ran)))
    assert msgs and msgs[0].startswith("✓")
    assert ADDED[0].collection == "DailyPaper" and "dailypaper" in ADDED[0].tags
    assert "DailyPaper 精读" in ADDED[0].note_html
    d = store.decisions()[first.entry.id]
    assert d.status == "imported" and d.zotero_key == "K1" and d.citekey
    fm, _ = read_markdown(ran.content_dir / first.entry.note_path)
    assert fm["zotero_key"] == "K1" and fm["citekey"] == d.citekey and fm["zotero_link"].endswith("K1")


def test_obsidian_checkbox_triggers_import(ran):
    item = ReviewStore(ran).items("pending")[0]
    update_frontmatter(ran.content_dir / item.entry.note_path, zotero_approve=True)
    assert collect_obsidian_approvals(ran) == 1
    list(import_approved(ran, ZoteroService(ran)))
    assert ReviewStore(ran).decisions()[item.entry.id].status == "imported"
    assert collect_obsidian_approvals(ran) == 0  # 不重复入库


def test_feedback_multipliers(ran):
    store = ReviewStore(ran)
    items = store.items("pending")
    store.decide(items[0].entry.id, "rejected")
    mult = topic_multipliers(ran)
    assert all(v < 1.0 for t, v in mult.items() if t in items[0].entry.topics)


def test_user_fields_preserved_on_rerender(ran, ctx):
    item = ReviewStore(ran).items("pending")[0]
    path = ran.content_dir / item.entry.note_path
    update_frontmatter(path, rating=5, status="read")
    from dailypaper.core.config import StageSpec

    Pipeline(ctx, [StageSpec(id="f", use="source.file", params={"path": "fixtures/papers.json"}),
                   StageSpec(id="s", use="ranker.llm_screen", params={"force_select": True}),
                   StageSpec(id="r", use="reader.deep_read"),
                   StageSpec(id="m", use="renderer.markdown", params={"digest": False})]).run()
    fm, _ = read_markdown(path)
    assert fm["rating"] == 5 and fm["status"] == "read"


def test_library_index_and_profile(cfg):
    items = [
        {"title": "Conformal Risk Control", "abstractNote": "conformal risk control for language models", "url": "https://arxiv.org/abs/2208.02814"},
        {"title": "Semantic Entropy", "abstractNote": "semantic entropy uncertainty language models", "DOI": "10.1/ABC"},
    ]
    export_library(cfg, items)
    idx = LibraryIndex.load(cfg)
    from dailypaper.core.models import Paper

    assert idx.contains(Paper(id="2208.02814", title="x"))
    assert idx.contains(Paper(id="9999.00001", title="semantic   ENTROPY"))
    prof = InterestProfile.build([i["title"] + " " + i["abstractNote"] for i in items])
    assert prof.score("conformal risk control for agents") > prof.score("gpu sorting kernels")


def test_make_citekey():
    assert make_citekey(["Ayush Mittal"], "2026", "Calibrated for Which Policy?") == "mittal2026calibrated"
    assert make_citekey([], "", "On the Theory") == "anontheory"


def test_app_endpoints_and_guard(ran):
    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    from fastapi.testclient import TestClient

    from dailypaper.app.server import create_app

    client = TestClient(create_app(ran), base_url="http://127.0.0.1")
    items = client.get("/api/items", params={"status": "pending"}).json()
    assert len(items) == 2
    pid = items[0]["entry"]["id"]
    assert "## 0. TL;DR" in client.get(f"/api/note/{pid}").json()["markdown"]
    assert client.post("/api/decide", json={"ids": [pid], "action": "reject"}).status_code == 403  # 缺请求头
    r = client.post("/api/decide", json={"ids": [pid], "action": "reject"}, headers={"X-DailyPaper": "1"})
    assert r.status_code == 200
    assert ReviewStore(ran).decisions()[pid].status == "rejected"
    evil = TestClient(create_app(ran), base_url="http://evil.example.com")
    assert evil.get("/api/items").status_code == 403
