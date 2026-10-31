"""顶会专栏：venue_fill 从论文池补足、venue_mark 写回、read venue:<key>、IndexEntry 会议字段、venues 配置接口。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dailypaper.core.io import read_json
from dailypaper.core.models import Paper
from dailypaper.core.pipeline import Pipeline
from dailypaper.integrations.venues.pool import VenuePool, paper_key


def _fill_pool(cfg, n: int = 6) -> VenuePool:
    pool = VenuePool(cfg)
    papers = [
        Paper(id=f"v{i}", title=f"Conformal prediction for LLM agents {i}", abstract="selective prediction with coverage guarantees for language model agents",
              url_abs=f"https://aclanthology.org/2025.acl-long.{i}/", url_pdf=f"https://aclanthology.org/2025.acl-long.{i}.pdf")
        for i in range(n)
    ] + [Paper(id="x", title="Protein folding dynamics", abstract="molecular simulation of proteins")]  # 关键词不命中
    pool.upsert(papers, "acl", 2025, "acl_anthology")
    pool.log_sync("acl", 2025, len(papers))
    for v in cfg.venues.venues:
        pool.log_sync(v.id, 2026, 0)
        pool.log_sync(v.id, 2025, 0)
    return pool


def _daily_with_fill(cfg, total=4, arxiv_top=1):
    stages = cfg.pipeline.pipelines["daily"]
    for s in stages:
        if s.id == "screen":
            s.params["top_k"] = arxiv_top
    from dailypaper.core.config import StageSpec

    idx = next(i for i, s in enumerate(stages) if s.id == "screen")
    stages.insert(idx + 1, StageSpec(id="venue_fill", use="ranker.venue_fill", **{"with": {"total": total, "min_score": 0, "auto_sync": False}}))
    stages.append(StageSpec(id="venue_mark", use="publisher.venue_mark"))


def test_venue_fill_tops_up_and_marks(ctx, cfg):
    pool = _fill_pool(cfg)
    _daily_with_fill(cfg, total=4, arxiv_top=1)
    items = Pipeline.from_config(ctx).run()
    venue = [c for c in items if c.paper.source == "venue"]
    read = [c for c in items if c.note]
    assert len(read) == 4 and sum(1 for c in read if c.paper.source == "venue") == 3
    assert all(c.meta.get("filled_from") == "venue" and c.selected for c in venue)
    assert ctx.notes["venue_fill"]["n"] == 3 and ctx.notes["venue_fill"]["pool_candidates"] == 6
    # 筛选结果写回池子；读过的标记 read；seen 记了 key
    st = pool.stats()
    assert st["read"] == 3 and st["screened"] >= 3
    seen = set(read_json(cfg.data_dir / "seen.json"))
    assert all(c.id in seen for c in venue)
    # 索引里带会议字段，JSON 路径里的斜杠安全
    index = read_json(cfg.content_dir / "api" / "index.json")["papers"]
    ve = [e for e in index if e["source"] == "venue"]
    assert len(ve) == 3 and ve[0]["venue"] == "acl" and ve[0]["ccf"] == "A" and ve[0]["venue_year"] == 2025 and ve[0]["filled_from"] == "venue"
    from dailypaper.core.models import file_id

    assert (cfg.content_dir / "api" / "papers" / f"{file_id(ve[0]['id'])}.json").exists()
    daily = read_json(cfg.content_dir / "api" / "daily" / "2026-10-28.json")
    assert daily["notes"]["venue_fill"]["n"] == 3
    # 第二次运行：已读的不再入选（池里只剩 3 篇未读）
    ctx2 = ctx
    items2 = Pipeline.from_config(ctx2).run()
    assert sum(1 for c in items2 if c.paper.source == "venue" and c.note) <= 3


def test_venue_fill_skips_when_enough(ctx, cfg):
    _fill_pool(cfg)
    _daily_with_fill(cfg, total=2, arxiv_top=2)
    items = Pipeline.from_config(ctx).run()
    assert not [c for c in items if c.paper.source == "venue"]


def test_read_venue_key(ctx, cfg):
    pool = _fill_pool(cfg, n=1)
    key = paper_key(Paper(id="v0", title="Conformal prediction for LLM agents 0"))
    assert pool.get(key) is not None
    cfg.pipeline.pipelines["read"] = [
        {"id": "fetch", "use": "source.ids"},
        {"id": "screen", "use": "ranker.llm_screen", "with": {"force_select": True}},
        {"id": "fulltext", "use": "enricher.fulltext", "with": {"loaders": ["abstract"]}},
        {"id": "read", "use": "reader.deep_read", "with": {"workers": 1}},
        {"id": "render", "use": "renderer.markdown", "with": {"digest": False}},
        {"id": "site_index", "use": "renderer.site_index"},
        {"id": "venue_mark", "use": "publisher.venue_mark"},
    ]
    from dailypaper.core.config import StageSpec

    cfg.pipeline.pipelines["read"] = [StageSpec.model_validate(s) for s in cfg.pipeline.pipelines["read"]]
    ctx.pipeline = "read"
    ctx.inputs["ids"] = [f"venue:{key}"]
    items = Pipeline.from_config(ctx).run()
    assert len(items) == 1 and items[0].note and items[0].paper.source == "venue"
    assert pool.stats()["read"] == 1


def test_venues_config_api(cfg, project):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("ruamel.yaml")
    from fastapi.testclient import TestClient

    from dailypaper.app.server import create_app

    del fastapi
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    H = {"X-DailyPaper": "1"}
    v = client.get("/api/config/venues").json()
    assert v["editable"] and len(v["venues"]) == 24 and v["column"]["total"] == 6
    body = {"years_back": 2, "auto_sync_days": 10, "column": {"total": 5, "arxiv_max": 2, "min_score": 7, "screen_budget": 30},
            "venues": [{"id": x["id"], "enabled": x["id"] != "eacl"} for x in v["venues"]]}
    r = client.put("/api/config/venues", json=body, headers=H)
    assert r.status_code == 200, r.text
    text = (cfg.root / "config" / "venues.yaml").read_text(encoding="utf-8")
    assert "# 顶会论文池" in text and "enabled: false" in text and cfg.venues.column.total == 5 and cfg.venues.years_back == 2
    pipe = yaml.safe_load((cfg.root / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    screen = next(s for s in pipe["pipelines"]["daily"] if s["use"] == "ranker.llm_screen")
    assert screen["with"]["top_k"] == 2
    body["column"]["total"] = 99
    assert client.put("/api/config/venues", json=body, headers=H).status_code == 400
    st = client.get("/api/venues/status").json()
    assert "pool" in st and st["venues"][0]["badge"]
    assert Path(st["pool"]["path"]).name == "venues.sqlite"
