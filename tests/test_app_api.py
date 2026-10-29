"""网页用到的后端接口：静态挂载、配置读写、后台运行。"""

from __future__ import annotations

import time

import pytest
import yaml

from dailypaper.core.pipeline import Pipeline

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("ruamel.yaml")
from fastapi.testclient import TestClient  # noqa: E402

from dailypaper.app.server import create_app  # noqa: E402

H = {"X-DailyPaper": "1"}


@pytest.fixture
def client(ctx, cfg):
    Pipeline.from_config(ctx).run()  # 生成 docs/api/*.json 和审核队列
    return TestClient(create_app(cfg), base_url="http://127.0.0.1")


def test_static_mounts(client, cfg):
    assert "DailyPaper" in client.get("/").text
    idx = client.get("/site/api/index.json").json()
    assert len(idx["papers"]) == 2
    pid = idx["papers"][0]["id"]
    paper = client.get(f"/site/api/papers/{pid}.json").json()
    assert "## 1. 摘要翻译" in paper["markdown"] and paper["lenses"]
    assert client.get("/site/api/daily/2026-10-28.json").json()["papers"]
    assert client.get("/assets/hero-sunrise-640.jpg").status_code == 200
    # 网页本身也被复制进了内容目录（GitHub Pages 用）
    assert (cfg.content_dir / "index.html").exists() and (cfg.content_dir / "assets" / "hero-sunrise.jpg").exists()
    assert (cfg.content_dir / "notes.md").exists() and not (cfg.content_dir / "index.md").exists()


def test_config_roundtrip_preserves_comments(client, cfg):
    view = client.get("/api/config").json()
    assert view["editable"] and view["daily"]["top_k"] == 2
    assert [t["id"] for t in view["topics"]][:2] == ["llm_uq", "agent_uq"]

    body = {"topics": view["topics"], "daily": view["daily"]}
    body["topics"][0]["name"] = "改过的名字"
    body["topics"][0]["keywords"].append("new keyword")
    body["topics"].append({"id": "new_topic", "name": "新方向", "weight": 1.0, "description": "d",
                           "keywords": ["x"], "context": [], "exclude": [], "lens_questions": ["q1"]})
    body["daily"].update({"top_k": 3, "min_score": 7.5, "categories": ["cs.CL"], "max_candidates": 40})
    r = client.put("/api/config", json=body, headers=H)
    assert r.status_code == 200, r.text

    topics_text = (cfg.root / "config" / "topics.yaml").read_text(encoding="utf-8")
    assert topics_text.startswith("# 研究方向")  # 文件头注释保留
    data = yaml.safe_load(topics_text)["topics"]
    assert data[0]["name"] == "改过的名字" and "new keyword" in data[0]["keywords"]
    assert data[-1]["id"] == "new_topic" and data[-1]["lens_questions"] == ["q1"]
    pipe = yaml.safe_load((cfg.root / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    stages = {s["id"]: s for s in pipe["pipelines"]["daily"]}
    assert stages["screen"]["with"]["top_k"] == 3 and stages["screen"]["with"]["min_score"] == 7.5
    assert stages["fetch"]["with"]["categories"] == ["cs.CL"] and stages["prefilter"]["with"]["max_candidates"] == 40
    # 内存中的配置同步更新
    assert cfg.topic("new_topic") is not None and cfg.topic("llm_uq").name == "改过的名字"
    assert client.get("/api/meta").json()["topics"][-1]["id"] == "new_topic"


def test_config_validation_errors(client):
    view = client.get("/api/config").json()
    body = {"topics": view["topics"], "daily": view["daily"]}
    body["topics"][1]["id"] = body["topics"][0]["id"]
    r = client.put("/api/config", json=body, headers=H)
    assert r.status_code == 400 and "重复" in r.json()["detail"]
    body = {"topics": view["topics"][:1], "daily": {**view["daily"], "top_k": 0}}
    assert client.put("/api/config", json=body, headers=H).status_code == 400
    assert client.put("/api/config", json=body).status_code == 403  # 缺请求头


def test_run_endpoint_runs_pipeline_in_background(client, cfg):
    assert client.post("/api/run", json={"pipeline": "read", "ids": ["not-an-id"]}, headers=H).status_code == 400
    r = client.post("/api/run", json={"pipeline": "daily", "provider": "fake"}, headers=H)
    assert r.status_code == 200, r.text
    runner = client.app.state.runner
    if runner.running:
        assert client.post("/api/run", json={"pipeline": "daily", "provider": "fake"}, headers=H).status_code == 409
    for _ in range(120):
        s = client.get("/api/run/status").json()
        if not s["running"]:
            break
        time.sleep(0.5)
    assert not s["running"] and s["ok"] is True, "\n".join(s["log_tail"])
    assert any("完成" in line for line in s["log_tail"])
    assert s["pipeline"] == "daily" and s["started_at"] and s["finished_at"]


def test_llm_config_roundtrip_keeps_comments(client, cfg):
    view = client.get("/api/config/llm").json()
    assert view["editable"] and "screen" in view["roles"] and "read" in view["roles"]
    assert all("key_set" in p for p in view["providers"])  # 只报告是否存在，不回显 key
    body = {k: view[k] for k in ("roles", "monthly_budget_usd", "budget_warn_ratio", "pricing", "cache")}
    body["roles"]["read"]["model"] = "claude-opus-5-5"
    body["roles"]["read"]["effort"] = "max"
    body["roles"]["read"]["web_search"] = False
    body["monthly_budget_usd"] = 42
    body["pricing"]["my-model"] = [1, 2]
    r = client.put("/api/config/llm", json=body, headers=H)
    assert r.status_code == 200, r.text
    text = (cfg.root / "config" / "llm.yaml").read_text(encoding="utf-8")
    assert "# LLM 配置" in text and "claude-opus-5-5" in text and "my-model" in text
    assert cfg.llm.roles["read"].model == "claude-opus-5-5" and cfg.llm.monthly_budget_usd == 42
    assert cfg.llm.roles["read"].web_search is False
    body["roles"]["read"]["effort"] = "ultra"
    assert client.put("/api/config/llm", json=body, headers=H).status_code == 400
    body["roles"]["read"]["effort"] = None
    body["roles"]["read"]["provider"] = "nope"
    assert "不存在" in client.put("/api/config/llm", json=body, headers=H).json()["detail"]


def test_usage_storage_and_export(client, cfg):
    u = client.get("/api/usage").json()
    assert len(u["months"]) == 6 and u["months"][-1]["month"] == u["month"] and "models" in u

    (cfg.cache_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (cfg.cache_dir / "prompts" / "x.md").write_text("hello", encoding="utf-8")
    st = client.get("/api/storage").json()
    pr = next(c for c in st["cache"] if c["kind"] == "prompts")
    assert pr["files"] == 1 and pr["bytes"] == 5 and st["notes"]["files"] == 2
    r = client.post("/api/storage/clear", json={"kind": "prompts"}, headers=H).json()
    assert r["files"] == 1 and not any((cfg.cache_dir / "prompts").iterdir())
    assert client.post("/api/storage/clear", json={"kind": "data"}, headers=H).status_code == 400

    pid = client.get("/site/api/index.json").json()["papers"][0]["id"]
    client.put(f"/api/annotations/{pid}", json={"highlights": [{"id": "h1", "text": "重点", "color": "green", "comment": "好"}], "note": "想法"}, headers=H)
    ex = client.get("/api/export/annotations.json").json()
    assert ex["papers"][0]["id"] == pid and ex["papers"][0]["title"] and ex["papers"][0]["highlights"][0]["text"] == "重点"
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export/notes.zip").content))
    names = z.namelist()
    assert "annotations.json" in names and sum(n.endswith(".md") for n in names) == 2
    md = next(n for n in names if n.endswith(f"{pid}.md"))
    assert "## 我的高亮与批注" in z.read(md).decode("utf-8")
