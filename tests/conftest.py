"""测试夹具：在临时目录里搭一个完整项目（真实的 prompts/、topics，测试用的 pipeline 与 lens 配置）。

全部使用 fake provider 和本地样例数据，不访问网络、不花钱。
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from dailypaper.core.config import load_config
from dailypaper.core.context import RunContext

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

TEST_PIPELINE = {
    "pipelines": {
        "daily": [
            {"id": "fetch", "use": "source.file", "with": {"path": "fixtures/papers.json"}},
            {"id": "dedupe", "use": "filter.seen"},
            {"id": "in_zotero", "use": "filter.zotero_index"},
            {"id": "prefilter", "use": "filter.keyword"},
            {"id": "screen", "use": "ranker.llm_screen", "with": {"top_k": 2, "min_score": 0}},
            {"id": "fulltext", "use": "enricher.fulltext", "with": {"loaders": ["abstract"]}},
            {"id": "read", "use": "reader.deep_read", "with": {"workers": 1}},
            {"id": "render", "use": "renderer.markdown"},
            {"id": "site_index", "use": "renderer.site_index"},
            {"id": "review_queue", "use": "publisher.review_queue"},
            {"id": "mark_seen", "use": "publisher.mark_seen"},
        ]
    }
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    for d in ("config", "prompts"):
        # 个人文件（课题档案、关注列表、局域网令牌）不带进测试
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns(".history", "__pycache__", "profile.md", "follow.yaml", "lan.json"))
    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    shutil.copytree(FIXTURES / "skills", tmp_path / "skills")
    (tmp_path / "config" / "pipeline.yaml").write_text(yaml.safe_dump(TEST_PIPELINE), encoding="utf-8")

    lenses = yaml.safe_load((tmp_path / "config" / "lenses.yaml").read_text(encoding="utf-8"))
    lenses["catalog"] = [c for c in lenses["catalog"] if not c["adapter"].startswith("skill_")] + [
        {
            "id": "demo-skill",
            "adapter": "skill_manifest",
            "path": "skills/demo-skill",
            "priority": 50,
            "options": {"exclude": ["static/core/workflow.md"], "axis_map": {"survey": "review", "method": "methods"}},
        },
        {"id": "demo-md", "adapter": "skill_markdown", "path": "skills/demo-skill", "priority": 40,
         "options": {"sections": ["Core Rule"]}},
    ]
    lenses["rules"] = [{"when": {"always": True}, "use": ["topic:*", "type:*", "demo-skill", "demo-md"]}]
    (tmp_path / "config" / "lenses.yaml").write_text(yaml.safe_dump(lenses, allow_unicode=True), encoding="utf-8")

    # 不让测试读到本机 ~/.claude/skills，也不受本机 .env 中输出目录设置的影响
    monkeypatch.setenv("DAILYPAPER_SKILLS_FALLBACK", str(tmp_path / "no-such-dir"))
    monkeypatch.delenv("DAILYPAPER_WORKSPACE", raising=False)
    return tmp_path


@pytest.fixture
def cfg(project: Path):
    return load_config(project)


@pytest.fixture
def ctx(cfg):
    return RunContext(config=cfg, run_date=date(2026, 10, 28), provider_override="fake")
