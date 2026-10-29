"""顶会论文池：解析（ACL bib、OpenReview、OpenAlex）、池子去重与检索、同步编排与 fallback。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dailypaper.core.models import Paper, ScreenResult
from dailypaper.core.registry import register
from dailypaper.integrations.venues.adapters.acl_anthology import parse_bib
from dailypaper.integrations.venues.adapters.base import VenueAdapter, VenueUnavailable
from dailypaper.integrations.venues.adapters.openalex import abstract_from_index, work_to_paper
from dailypaper.integrations.venues.adapters.openreview import note_to_paper
from dailypaper.integrations.venues.pool import VenuePool, extract_arxiv_id, normalize_title, paper_key
from dailypaper.integrations.venues.sync import needs_sync, sync_venues, target_years

FIX = Path(__file__).parent / "fixtures"


def test_parse_acl_bib():
    papers = parse_bib((FIX / "acl_sample.bib").read_text(encoding="utf-8"), "acl", 2025)
    assert len(papers) == 2  # @proceedings 不算
    p = papers[0]
    assert p.title == "Conformal Abstention for LLM Agents: A Study"
    assert p.authors == ["Weiqi Wang", "Limeng Cui", "Yangqiu Song"]
    assert p.abstract.startswith("We study selective prediction") and "and calibration" in p.abstract
    assert p.url_pdf == "https://aclanthology.org/2025.acl-long.1.pdf" and p.doi == "10.18653/v1/2025.acl-long.1"
    assert papers[1].extra["arxiv_id"] == "2501.01234"


def test_parse_openreview_and_openalex():
    note = {"id": "abc123", "content": {"title": {"value": "Paper T"}, "abstract": {"value": "Abs."}, "authors": {"value": ["A", "B"]}, "pdf": {"value": "/pdf/x"}, "venue": {"value": "ICLR 2026 Poster"}}}
    p = note_to_paper(note, "iclr", 2026)
    assert p.title == "Paper T" and p.url_pdf == "https://openreview.net/pdf?id=abc123" and p.extra["venue_label"] == "ICLR 2026 Poster"
    assert note_to_paper({"id": "x", "content": {}}, "iclr", 2026) is None
    assert abstract_from_index({"world": [1], "Hello": [0]}) == "Hello world"
    w = {"id": "https://openalex.org/W1", "title": "W Title", "doi": "https://doi.org/10.1/x", "publication_year": 2025,
         "authorships": [{"author": {"display_name": "Ann"}}], "abstract_inverted_index": {"b": [1], "a": [0]},
         "primary_location": {"landing_page_url": "https://arxiv.org/abs/2502.00001v2", "pdf_url": None}, "locations": []}
    q = work_to_paper(w, "neurips", 2025)
    assert q.abstract == "a b" and q.doi == "10.1/x" and q.extra["arxiv_id"] == "2502.00001" and q.authors == ["Ann"]


def test_keys_and_pool_dedup_search(cfg):
    assert normalize_title("Conformal  Prediction: for LLMs!") == "conformalpredictionforllms"
    assert extract_arxiv_id("https://doi.org/10.48550/arXiv.2410.12345") == "2410.12345"
    pool = VenuePool(cfg, path=Path(":memory:"))
    a = Paper(id="1", title="Conformal Prediction for Agents", abstract="coverage guarantees for agents", extra={"arxiv_id": "2410.00001"})
    b = Paper(id="2", title="Conformal prediction for agents", abstract="")  # 同题不同会（无 arXiv id → 标题 key）
    c = Paper(id="3", title="Another Topic Entirely", abstract="retrieval augmented generation")
    assert paper_key(a) == "arxiv:2410.00001" and paper_key(b).startswith("title:")
    assert pool.upsert([a, c], "acl", 2025, "acl_anthology") == 2
    assert pool.upsert([a, b], "emnlp", 2025) == 1  # a 重复
    assert pool.stats()["total"] == 3
    hits = pool.search_fts("conformal agents")
    assert hits and hits[0]["paper"].title.startswith("Conformal")
    assert pool.search_fts("retrieval", venues=["acl"])[0]["paper"].id == paper_key(c)
    assert pool.search_fts("retrieval", venues=["emnlp"]) == []
    assert pool.search_fts('conformal AND ("')  # 语法错误时退化成按词 OR 查询，不报错
    key = paper_key(a)
    pool.set_screened(key, ScreenResult(primary_topic="x", reason="r"), 7.5)
    sr, score = pool.screened_of(key)
    assert sr.reason == "r" and score == 7.5
    rows = pool.candidates()
    assert rows[0]["key"] == key  # 已筛选的在前
    pool.mark_read(key)
    assert all(r["key"] != key for r in pool.candidates())
    got = pool.get(key)
    assert got.source == "venue" and got.extra["venue_name"] == "ACL" and got.extra["ccf"] == "A" and got.url_html.endswith("2410.00001")


def test_sync_with_fallback(cfg, monkeypatch):
    calls = []

    @register("venue_adapter", "fake_primary")
    class Primary(VenueAdapter):
        def list_papers(self, spec, year):
            calls.append(("primary", spec.id, year))
            raise VenueUnavailable("需要登录")

    @register("venue_adapter", "fake_backup")
    class Backup(VenueAdapter):
        def list_papers(self, spec, year):
            calls.append(("backup", spec.id, year))
            return [Paper(id=f"{spec.id}-{year}", title=f"{spec.id} {year} paper", abstract="x")]

    cfg.venues.venues[0].adapter = "fake_primary"
    cfg.venues.venues[0].__pydantic_extra__["fallback"] = "fake_backup"
    for v in cfg.venues.venues[1:]:
        v.enabled = False
    cfg.venues.years_back = 1
    pool = VenuePool(cfg, path=Path(":memory:"))
    assert needs_sync(cfg, pool)
    res = sync_venues(cfg, pool=pool)
    assert len(res) == 2 and all(r["ok"] and r["adapter"] == "fake_backup" and r["new"] == 1 for r in res)
    assert pool.stats()["total"] == 2 and not needs_sync(cfg, pool)
    assert sync_venues(cfg, pool=pool)[0]["skipped"]
    assert len(target_years(cfg)) == 2
    cfg.venues.venues[0].__pydantic_extra__.pop("fallback")
    res = sync_venues(cfg, pool=pool, force=True)
    assert not res[0]["ok"] and "需要登录" in res[0]["error"]


@pytest.mark.parametrize("name", ["openreview", "acl_anthology", "openalex"])
def test_adapters_registered(name):
    from dailypaper.core import registry

    assert name in registry.available()["venue_adapter"]


def test_openreview_auth_headers(monkeypatch):
    from dailypaper.integrations.venues import openreview_auth as oa

    oa.reset()
    monkeypatch.delenv("OPENREVIEW_USERNAME", raising=False)
    monkeypatch.delenv("OPENREVIEW_PASSWORD", raising=False)
    assert oa.auth_headers("https://openreview.net/pdf?id=x") == {} and oa.credentials() is None
    monkeypatch.setenv("OPENREVIEW_USERNAME", "u@x.org")
    monkeypatch.setenv("OPENREVIEW_PASSWORD", "p")
    import httpx

    calls = {"n": 0}

    class R:
        status_code = 200
        def json(self): return {"token": "tok"}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or R())
    assert oa.auth_headers("https://openreview.net/pdf?id=x") == {"Authorization": "Bearer tok"}
    assert oa.auth_headers("https://arxiv.org/pdf/1") == {}
    oa.auth_headers("https://api2.openreview.net/notes")
    assert calls["n"] == 1  # 只登录一次
    oa.reset()


def test_arxiv_lookup_loader(cfg, monkeypatch):
    from datetime import date

    from dailypaper.core.context import RunContext
    from dailypaper.plugins.fulltext import loaders

    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2501.00001v1</id>
      <title>Conformal Prediction for LLM Agents</title><summary>abs</summary><published>2025-01-01T00:00:00Z</published>
      <link href="http://arxiv.org/pdf/2501.00001v1" title="pdf"/></entry></feed>"""

    class R:
        def __init__(self, text, status=200): self.text, self.status_code = text, status
    urls = []

    def fake_get(url, timeout=60):
        urls.append(url)
        if "export.arxiv.org" in url:
            return R(atom)
        return R("<html><div class='ltx_document'><p>Full text here.</p></div></html>")
    monkeypatch.setattr(loaders, "_http_get", fake_get)
    ctx = RunContext(config=cfg, run_date=date(2026, 10, 29), provider_override="fake")
    pool = VenuePool(cfg)
    p = Paper(id="k", source="venue", title="Conformal Prediction for LLM Agents")
    pool.upsert([p], "acl", 2025)
    p = pool.get(paper_key(p))
    text = loaders.ArxivLookupLoader(ctx).load(p)
    assert text and "Full text" in text and p.extra["arxiv_id"] == "2501.00001" and "arxiv.org/html/2501.00001" in p.url_html
    assert pool.get(p.id).extra["arxiv_id"] == "2501.00001"
    assert loaders.ArxivLookupLoader(ctx).load(Paper(id="2501.00001", source="arxiv", title="x")) is None  # arXiv 来源不查
    p2 = Paper(id="k2", source="venue", title="A Completely Different Title")
    assert loaders.ArxivLookupLoader(ctx).load(p2) is None  # 标题对不上不冒认
