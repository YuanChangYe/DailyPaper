from __future__ import annotations

from pathlib import Path

from dailypaper.core.models import Candidate, Paper, ScreenResult
from dailypaper.llm.client import extract_json
from dailypaper.plugins.filters.keyword import TopicMatcher
from dailypaper.plugins.readers.deep_read import parse_note
from dailypaper.plugins.sources.arxiv_rss import parse_rss
from dailypaper.prompting.composer import PromptComposer
from dailypaper.prompting.lens import Lens
from dailypaper.prompting.selector import LensCatalog, select_lenses

FIX = Path(__file__).parent / "fixtures"


def test_usage_cost_and_budget(cfg, monkeypatch, caplog):
    from datetime import date

    from dailypaper.llm import usage

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    cfg.llm.monthly_budget_usd = 1.0
    usage.record(cfg, "claude-sonnet-5-5", 20_000, 10_000, date.today())  # 0.04 + 0.10
    usage.record(cfg, "unknown-model", 1000, 1000, date.today())  # 无价格：只计次数
    s = usage.month_summary(cfg)
    assert s["calls"] == 2 and abs(s["usd"] - 0.14) < 1e-6
    cfg.llm.monthly_budget_usd = 0.1
    with caplog.at_level("WARNING"):
        assert "预算" in usage.budget_line(cfg)
    assert any("已达预算" in r.message for r in caplog.records)


def test_parse_rss_filters_announce_type():
    papers = parse_rss((FIX / "rss_sample.xml").read_text(encoding="utf-8"), {"new", "cross"})
    assert [p.id for p in papers] == ["2610.11111"]
    p = papers[0]
    assert p.version == "v1" and p.authors == ["Alice Zhang", "Bob Li"]
    assert p.abstract == "We study conformal prediction for LLM agents."


def test_keyword_matcher_requires_context(cfg):
    llm_uq = TopicMatcher(cfg.topic("llm_uq"))
    assert llm_uq.match("Calibration of LLM confidence") != []
    assert llm_uq.match("Calibration of ResNet image classifiers") == []  # 缺语境词
    cp = TopicMatcher(cfg.topic("conformal_selective"))
    assert "conformal" in cp.match("Conformal prediction sets for regression")


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('blah {"a": [1, 2]} tail') == {"a": [1, 2]}


def test_parse_note_splits_meta_and_body():
    meta, body = parse_note('```json\n{"tldr": "x", "value_score": 4}\n```\n\n## 0. TL;DR\nhi')
    assert meta["value_score"] == 4 and body.startswith("## 0. TL;DR")


def _cand(paper_type="method", score=8.0):
    return Candidate(
        paper=Paper(id="2610.1", title="T"),
        screen=ScreenResult(topic_scores={"conformal_selective": 9}, paper_type=paper_type, novelty=2),
        score=score,
    )


def test_skill_manifest_lens_follows_axis_and_excludes(cfg):
    cat = LensCatalog(cfg)
    lenses = {lens.id: lens for lens in select_lenses(cat, _cand("method"))}
    demo = lenses["demo-skill"].content
    assert "PRINCIPLES_MARKER" in demo and "METHODS_MARKER" in demo and "LEDGER_MARKER" in demo
    assert "WORKFLOW_MARKER" not in demo  # 被 exclude
    survey = {lens.id: lens for lens in select_lenses(cat, _cand("survey"))}["demo-skill"].content
    assert "REVIEW_MARKER" in survey and "METHODS_MARKER" not in survey
    assert "topic:conformal_selective" in lenses and "type_method" in lenses


def test_skill_markdown_lens_keeps_sections_and_strips_code(cfg):
    lens = {x.id: x for x in select_lenses(LensCatalog(cfg), _cand())}["demo-md"]
    assert "separate author claims" in lens.content
    assert "audit.py" not in lens.content and "prepare.py" not in lens.content


def test_composer_budget_drops_low_priority(cfg):
    comp = PromptComposer(cfg)
    lenses = [Lens("a", "A", "x" * 900, priority=90), Lens("b", "B", "y" * 900, priority=10)]
    kept, dropped = comp.fit_budget(lenses, budget=1000)
    assert [x.id for x in kept] == ["a"] and dropped == ["b"]


def test_deep_read_prompt_contains_framework_and_lenses(cfg):
    lenses = select_lenses(LensCatalog(cfg), _cand())
    p = PromptComposer(cfg).compose("deep_read", lenses, paper=_cand().paper, screen=_cand().screen,
                                    topics_hit=["conformal_selective"], fulltext="BODY",
                                    fulltext_loader="abstract", grounding="source-limited")
    assert "## 8. Research hooks" in p.system and "Lens：" in p.system
    assert "<paper>\nBODY\n</paper>" in p.user and "仅有摘要" in p.user
