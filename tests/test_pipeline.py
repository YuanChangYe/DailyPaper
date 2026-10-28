from __future__ import annotations

import pytest

from dailypaper.core import registry
from dailypaper.core.config import StageSpec
from dailypaper.core.interfaces import Filter
from dailypaper.core.io import read_json, read_markdown
from dailypaper.core.pipeline import Pipeline, PipelineError


def test_builtin_plugins_registered():
    kinds = registry.available()
    for kind, name in [
        ("source", "arxiv_rss"), ("source", "arxiv"), ("filter", "keyword"), ("ranker", "llm_screen"),
        ("enricher", "fulltext"), ("reader", "deep_read"), ("renderer", "markdown"), ("publisher", "review_queue"),
        ("llm", "fake"), ("lens_adapter", "skill_manifest"), ("zotero_writer", "connector"), ("site_adapter", "mkdocs"),
    ]:
        assert name in kinds[kind], f"{kind}.{name} 未注册"


def test_unknown_plugin_has_helpful_error():
    with pytest.raises(registry.PluginNotFound, match="已注册"):
        registry.get("filter.nope")


def test_daily_end_to_end_with_fake_llm(ctx, cfg):
    items = Pipeline.from_config(ctx).run()
    # 关键词预筛去掉了 CV 和排序算法两篇
    ids = {c.id for c in items}
    assert "2610.00004" not in ids and "2610.00005" not in ids
    read = [c for c in items if c.note]
    assert len(read) == 2
    for c in read:
        fm, body = read_markdown(cfg.content_dir / c.note_path)
        assert fm["paper_id"] == c.id and fm["status"] == "unread"
        assert "## 2. Core insight" in body
        assert "demo-skill" in fm["lenses"]
    assert (cfg.content_dir / "daily" / "2026-10-28.md").exists()
    index = read_json(cfg.content_dir / "api" / "index.json")
    assert {p["id"] for p in index["papers"]} == {c.id for c in read}
    assert (cfg.content_dir / "feed.xml").read_text(encoding="utf-8").startswith("<?xml")
    assert len(read_json(cfg.data_dir / "review_queue.json")) == 2
    # 所有新抓取的论文（包括被预筛掉的）都记为已处理
    assert len(read_json(cfg.data_dir / "seen.json")) == 5

    # 第二次运行：全部已处理过
    again = Pipeline.from_config(ctx).run()
    assert again == []


def test_resume_from_checkpoint(ctx):
    Pipeline.from_config(ctx).run(stop_after="screen")
    items = Pipeline.from_config(ctx).run(start_from="fulltext")
    assert sum(1 for c in items if c.note) == 2


def test_disabled_stage_is_skipped(ctx, cfg):
    for s in cfg.pipeline.pipelines["daily"]:
        if s.id == "prefilter":
            s.enabled = False
    items = Pipeline.from_config(ctx).run(stop_after="screen")
    assert len(items) == 5  # 没有预筛，全部进入筛选


def test_dry_run_skips_publishers(ctx, cfg):
    ctx.dry_run = True
    Pipeline.from_config(ctx).run()
    assert not (cfg.data_dir / "review_queue.json").exists()
    assert not (cfg.data_dir / "seen.json").exists()


def test_custom_plugin_can_be_added(ctx):
    @registry.register("filter", "only_first")
    class OnlyFirst(Filter):
        def run(self, items):
            return items[:1]

    specs = [StageSpec(id="fetch", use="source.file", params={"path": "fixtures/papers.json"}),
             StageSpec(id="first", use="filter.only_first")]
    assert len(Pipeline(ctx, specs).run()) == 1


def test_bad_params_rejected(ctx):
    with pytest.raises(PipelineError, match="参数错误"):
        Pipeline(ctx, [StageSpec(id="x", use="filter.keyword", params={"no_such_param": 1})])
