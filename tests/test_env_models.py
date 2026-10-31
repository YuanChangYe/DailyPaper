""".env 热更新（本地助手不用重启就能读到新 key）和设置页的模型下拉列表。"""

from __future__ import annotations

import os
import time

import httpx
import pytest

from dailypaper.core.config import ProviderSpec
from dailypaper.core.env import load_dotenv
from dailypaper.llm import models as models_mod


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    t = time.time() + 1 + len(text)  # 保证 mtime 变化
    os.utime(path, (t, t))


def test_dotenv_refresh_adds_updates_removes(tmp_path, monkeypatch):
    for k in ("DP_T_A", "DP_T_B", "DP_T_REAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DP_T_REAL", "from-system")
    env = tmp_path / ".env"
    _write(env, "# c\nDP_T_A=1\nexport DP_T_REAL=from-file\n")
    load_dotenv(env)
    assert os.environ["DP_T_A"] == "1"
    assert os.environ["DP_T_REAL"] == "from-system"  # 真实环境变量不被覆盖

    _write(env, 'DP_T_A="2"\nDP_T_B=\'new\'\nDP_T_REAL=from-file\n')
    load_dotenv(env, refresh=True)
    assert os.environ["DP_T_A"] == "2" and os.environ["DP_T_B"] == "new"
    assert os.environ["DP_T_REAL"] == "from-system"

    _write(env, "DP_T_B=new\n")
    load_dotenv(env, refresh=True)
    assert "DP_T_A" not in os.environ  # 从 .env 删掉的 key 也撤掉
    assert os.environ["DP_T_B"] == "new" and os.environ["DP_T_REAL"] == "from-system"

    env.unlink()
    load_dotenv(env, refresh=True)
    assert "DP_T_B" not in os.environ and os.environ["DP_T_REAL"] == "from-system"


def test_list_models_openai_compat_filters_and_caches(cfg, monkeypatch):
    models_mod.clear_cache()
    monkeypatch.setenv("DP_T_KEY", "k")
    cfg.llm.providers["g"] = ProviderSpec(type="openai_compat", api_key_env="DP_T_KEY",
                                          base_url="https://generativelanguage.googleapis.com/v1beta/openai")
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        ids = ["models/gemini-2.5-flash", "models/gemini-3-flash-preview", "models/gemini-2.5-flash-preview-tts",
               "models/text-embedding-004", "models/gemini-flash-latest", "models/gemini-3.1-pro-preview"]
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    monkeypatch.setattr(models_mod.httpx, "get", fake_get)
    r = models_mod.list_models(cfg, "g")
    assert not r["error"]
    assert r["models"] == ["gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-latest"]
    assert calls[0][0].endswith("/openai/models") and calls[0][1]["Authorization"] == "Bearer k"
    assert models_mod.list_models(cfg, "g")["cached"] and len(calls) == 1
    models_mod.list_models(cfg, "g", refresh=True)
    assert len(calls) == 2


def test_list_models_reports_missing_key_and_unknown(cfg, monkeypatch):
    models_mod.clear_cache()
    monkeypatch.delenv("DP_T_NOKEY", raising=False)
    cfg.llm.providers["d"] = ProviderSpec(type="openai_compat", api_key_env="DP_T_NOKEY", base_url="https://x.test")
    r = models_mod.list_models(cfg, "d")
    assert r["models"] == [] and "DP_T_NOKEY" in r["error"]
    assert "没有" in models_mod.list_models(cfg, "nope")["error"]
    cfg.llm.providers["f"] = ProviderSpec(type="fake")
    assert models_mod.list_models(cfg, "f")["models"] == ["fake"]


def test_models_endpoint(cfg):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dailypaper.app.server import create_app

    cfg.llm.providers["f"] = ProviderSpec(type="fake")
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    assert client.get("/api/llm/models", params={"provider": "f"}).json()["models"] == ["fake"]
