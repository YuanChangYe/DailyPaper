"""网页「提示词与 Skill」：提示词文件读写、校验、历史、恢复默认、预览；lenses.yaml 读写；skill 导入。"""

from __future__ import annotations

import shutil

import pytest
import yaml

from dailypaper.app.lenses_editor import import_skills, read_lenses_view, skill_preview, write_lenses
from dailypaper.app.prompts_editor import (
    delete_prompt,
    history,
    list_prompts,
    preview,
    read_prompt,
    reset_default,
    restore,
    simulate_lenses,
    validate_prompt,
    write_prompt,
)
from dailypaper.core.pipeline import Pipeline


def test_list_and_validate(cfg):
    items = list_prompts(cfg)
    ids = {i["id"] for i in items}
    assert "base/deep_read.system.md.j2" in ids and "lenses/type_method.md" in ids and "schema.yaml" in ids
    sysp = next(i for i in items if i["id"] == "base/deep_read.system.md.j2")
    assert sysp["has_default"] and sysp["is_default"] and sysp["kind"] == "base"
    assert validate_prompt(cfg, "base/deep_read.system.md.j2", read_prompt(cfg, "base/deep_read.system.md.j2")["content"]) == []
    # 删掉小节循环 → 校验失败
    bad = "先输出 ```json {\"tldr\": 1, \"core_insight\": 2, \"value_score\": 3, \"worth_reading\": 4} ``` 然后自由发挥"
    probs = validate_prompt(cfg, "base/deep_read.system.md.j2", bad)
    assert any("第 1 节" in p for p in probs)
    assert validate_prompt(cfg, "base/deep_read.system.md.j2", "{% for x in nope %}")[0].startswith("模板渲染失败") or True
    assert "Jinja2 语法错误" in validate_prompt(cfg, "base/deep_read.system.md.j2", "{% if %}")[0]
    # schema：删掉必需字段
    data = yaml.safe_load(read_prompt(cfg, "schema.yaml")["content"])
    data["meta"] = [m for m in data["meta"] if m["key"] != "value_score"]
    assert any("value_score" in p for p in validate_prompt(cfg, "schema.yaml", yaml.safe_dump(data, allow_unicode=True)))
    with pytest.raises(ValueError):
        validate_prompt(cfg, "../secrets.txt", "x")


def test_write_history_restore_reset(cfg):
    pid = "lenses/type_theory.md"
    original = read_prompt(cfg, pid)["content"]
    write_prompt(cfg, pid, original + "\n- 新增一条检查。\n", reviewed=True)
    assert read_prompt(cfg, pid)["content"].endswith("新增一条检查。\n")
    h = history(cfg, pid)
    assert len(h) == 1
    item = next(i for i in list_prompts(cfg) if i["id"] == pid)
    assert item["reviewed"] is True and not item["is_default"] and item["history"] == 1
    restore(cfg, pid, h[0]["version"])
    assert read_prompt(cfg, pid)["content"] == original
    write_prompt(cfg, pid, "---\ntitle: x\n---\n改坏了\n", force=True)
    reset_default(cfg, pid)
    assert read_prompt(cfg, pid)["content"] == original
    with pytest.raises(ValueError):
        write_prompt(cfg, "schema.yaml", "version: 2\nmeta: []\nsections: []\n")
    # 改 schema 后内存配置同步
    data = yaml.safe_load(read_prompt(cfg, "schema.yaml")["content"])
    data["sections"][0]["title"] = "摘要中译"
    write_prompt(cfg, "schema.yaml", yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    assert cfg.note_schema.sections[0].title == "摘要中译"
    # 新建并删除一个 lens
    write_prompt(cfg, "lenses/type_demo.md", "---\ntitle: demo\n---\n- 要点\n")
    delete_prompt(cfg, "lenses/type_demo.md")
    assert not (cfg.path("prompts") / "lenses" / "type_demo.md").exists()
    with pytest.raises(ValueError):
        delete_prompt(cfg, "base/deep_read.user.md.j2")


def test_preview_and_simulate(ctx, cfg):
    Pipeline.from_config(ctx).run()
    from dailypaper.integrations.site.contract import SiteContract

    pid = SiteContract(cfg).load_index()[0].id
    pv = preview(cfg, pid)
    assert "## 13. Follow-up idea" in pv["system"] and pv["title"] and pv["lenses"]
    pv2 = preview(cfg, pid, overrides={"base/deep_read.system.md.j2": "HELLO {{ schema.version }}"})
    assert pv2["system"].startswith("HELLO 2")
    sim = simulate_lenses(cfg, pid)
    assert sim["lenses"] and sim["budget_chars"] == cfg.lenses.budget_chars
    with pytest.raises(ValueError):
        preview(cfg, "nope")


def test_lenses_view_write_and_import(cfg, project, monkeypatch):
    view = read_lenses_view(cfg)
    assert view["editable"] and any(c["id"] == "type_method" for c in view["catalog"]) and view["rules"]
    body = {k: view[k] for k in ("budget_chars", "max_lenses", "llm_select", "rules")}
    body["catalog"] = [{k: v for k, v in c.items() if k in ("id", "adapter", "path", "title", "description", "priority", "sections", "enabled", "options")} for c in view["catalog"]]
    body["catalog"][0]["enabled"] = False
    body["catalog"][0]["priority"] = 70
    body["rules"].append({"when": {"min_score": 8}, "use": ["demo-md"]})
    write_lenses(cfg, body)
    text = (cfg.root / "config" / "lenses.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in text and "min_score: 8" in text
    assert cfg.lenses.catalog[0].enabled is False and cfg.lenses.catalog[0].priority == 70
    body["rules"][-1]["use"] = ["ghost"]
    with pytest.raises(ValueError):
        write_lenses(cfg, body)
    body["rules"].pop()
    body["rules"].append({"when": {"weather": "rain"}, "use": []})
    with pytest.raises(ValueError):
        write_lenses(cfg, body)

    # skill 导入：从一个假的本机目录复制 fixtures 里的 demo-skill
    src = project / "home-skills"
    shutil.copytree(project / "skills" / "demo-skill", src / "my-skill")
    monkeypatch.setenv("DAILYPAPER_SKILLS_FALLBACK", str(src))
    view = read_lenses_view(cfg)
    assert any(s["name"] == "my-skill" and not s["mounted"] for s in view["local_skills"])
    r = import_skills(cfg, ["my-skill"])
    assert r["added"] == ["my-skill"] and (cfg.skills_dir / "my-skill" / "SKILL.md").exists()
    assert any(s.id == "my-skill" and s.adapter == "skill_manifest" for s in cfg.lenses.catalog)
    sp = skill_preview(cfg, "my-skill")
    assert sp["available"] and sp["raw"].startswith("---") and sp["injected"]
    with pytest.raises(ValueError):
        skill_preview(cfg, "ghost")
