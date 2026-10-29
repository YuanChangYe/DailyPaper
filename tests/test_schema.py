"""笔记结构（prompts/schema.yaml）：加载、提示词生成、输出校验、lens 停用。"""

from __future__ import annotations

from dailypaper.core.config import LensSpec, NoteSchema, load_schema
from dailypaper.plugins.readers.deep_read import note_headings, parse_note, validate_note
from dailypaper.prompting.composer import PromptComposer
from dailypaper.prompting.selector import LensCatalog


def _fake_note(schema: NoteSchema, include_deep=True, drop: str | None = None, swap=False) -> str:
    secs = [s for _, s in schema.numbered(include_deep) if s.id != drop]
    if swap:
        secs[0], secs[1] = secs[1], secs[0]
    body = "\n\n".join(f"## {i}. {s.title}\n正文" for i, s in enumerate(secs, 1))
    return '```json\n{"tldr": "t", "core_insight": "c", "value_score": 4, "worth_reading": "值得 follow"}\n```\n\n' + body


def test_schema_loads_13_sections(cfg):
    schema = cfg.note_schema
    assert schema.version == 2 and len(schema.sections) == 13
    assert [s.id for s in schema.sections if s.deep] == [
        "fragile_assumption", "min_reproduction", "counterexample", "follow_up_survey", "follow_up_idea"]
    assert [n for n, _ in schema.numbered(include_deep=False)] == list(range(1, 9))
    assert schema.label("tldr") == "一句话总结" and schema.label("nope") == "nope"
    pub = schema.to_public()
    assert pub["meta"][0]["aliases"] == ["TL;DR"] and "TL;DR" in pub["legacy_section_titles"]


def test_load_schema_missing_file_gives_empty(tmp_path):
    assert load_schema(tmp_path).sections == []


def test_validate_note_ok_missing_and_order(cfg):
    schema = cfg.note_schema
    meta, body = parse_note(_fake_note(schema))
    assert validate_note(body, meta, schema) == []
    assert note_headings(body)[0] == "摘要翻译"

    meta, body = parse_note(_fake_note(schema, drop="math"))
    assert any("核心数学推导" in p for p in validate_note(body, meta, schema))

    # 个别字符写错（如繁体字）不算缺节
    meta, body = parse_note(_fake_note(schema).replace("核心直觉", "核心直覺"))
    assert validate_note(body, meta, schema) == []

    meta, body = parse_note(_fake_note(schema, swap=True))
    assert "小节顺序与框架不一致" in validate_note(body, meta, schema)

    # 只生成前 8 节时，deep 节缺失不算错
    meta, body = parse_note(_fake_note(schema, include_deep=False))
    assert validate_note(body, meta, schema, include_deep=False) == []
    assert len(validate_note(body, meta, schema, include_deep=True)) == 5

    bad_meta = {"tldr": "t", "core_insight": "c", "value_score": 9}
    problems = validate_note(body, bad_meta, schema, include_deep=False)
    assert any("worth_reading" in p for p in problems) and any("超出范围" in p for p in problems)


def test_prompt_follows_schema_and_deep_toggle(cfg):
    comp = PromptComposer(cfg)
    kw = dict(paper=None, screen=None, topics_hit=[], fulltext="X", fulltext_loader="abstract", grounding="source-limited")
    # user 模板用到 paper，这里只渲染 system
    sys_full = comp.env.get_template("base/deep_read.system.md.j2").render(
        topics=cfg.topics, language="zh", lenses=[], schema=cfg.note_schema, include_deep=True, web_search=True)
    sys_short = comp.env.get_template("base/deep_read.system.md.j2").render(
        topics=cfg.topics, language="zh", lenses=[], schema=cfg.note_schema, include_deep=False, web_search=False)
    assert "## 13. Follow-up idea" in sys_full and "你有联网搜索工具" in sys_full
    assert "## 13." not in sys_short and "## 8. Take-aways" in sys_short and "未联网检索" in sys_short
    assert '"worth_reading"' in sys_full and "`tldr`（一句话总结）" in sys_full
    del kw


def test_disabled_lens_is_skipped(cfg):
    cfg.lenses.catalog.append(LensSpec(id="off", adapter="markdown", path="prompts/lenses/type_method.md", enabled=False))
    cat = LensCatalog(cfg)
    assert "off" in cat.disabled and "off" not in cat.adapters
    assert "evidence_discipline" in cat.adapters and "idea_review" in cat.adapters
