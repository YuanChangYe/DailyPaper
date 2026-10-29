from __future__ import annotations

import httpx
import pytest

from dailypaper.core.config import LLMRole, ProviderSpec
from dailypaper.llm.base import LLMError, LLMProvider, LLMResponse
from dailypaper.llm.client import LLMClient


class FlakyProvider(LLMProvider):
    """前 fail_times 次抛出指定异常，之后成功。"""

    def __init__(self, exc: Exception, fail_times: int) -> None:
        super().__init__(ProviderSpec(type="flaky"), LLMRole(provider="flaky", model="m"))
        self.exc, self.fail_times, self.calls = exc, fail_times, 0

    def complete(self, system, user, *, tag="", json_mode=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return LLMResponse(text="ok")


def _client(ctx, provider, retries=3):
    ctx.config.llm.max_retries = retries
    ctx.config.llm.cache = False
    return LLMClient(ctx, "screen", provider, "flaky")


def test_transient_error_is_retried(ctx, monkeypatch):
    monkeypatch.setattr("dailypaper.llm.client.time.sleep", lambda s: None)
    p = FlakyProvider(httpx.ConnectError("boom"), fail_times=2)
    assert _client(ctx, p).complete("s", "u").text == "ok"
    assert p.calls == 3


def test_non_transient_error_fails_immediately(ctx, monkeypatch):
    monkeypatch.setattr("dailypaper.llm.client.time.sleep", lambda s: pytest.fail("不应重试"))
    p = FlakyProvider(TypeError("Could not resolve authentication method"), fail_times=5)
    with pytest.raises(LLMError, match="TypeError"):
        _client(ctx, p).complete("s", "u")
    assert p.calls == 1


def test_anthropic_provider_requires_key(monkeypatch):
    pytest.importorskip("anthropic")
    from dailypaper.llm.anthropic_provider import AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    spec = ProviderSpec(type="anthropic", api_key_env="ANTHROPIC_API_KEY")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(spec, LLMRole(provider="claude", model="claude-haiku-5-5"))


def test_screen_failure_keeps_papers_unseen(ctx, cfg, monkeypatch):
    """筛选整体失败时流水线报错；部分失败的论文不记入 seen.json。"""
    from dailypaper.core.io import read_json
    from dailypaper.core.pipeline import Pipeline, PipelineError
    from dailypaper.plugins.rankers import llm_screen

    calls = {"n": 0}
    real = llm_screen._screen_batch

    def flaky(ctx, llm, composer, catalog, batch, opts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("batch failed")
        return real(ctx, llm, composer, catalog, batch, opts)

    monkeypatch.setattr(llm_screen, "_screen_batch", flaky)
    for s in cfg.pipeline.pipelines["daily"]:
        if s.id == "screen":
            s.params["batch_size"] = 2  # 3 篇预筛结果 → 2 批，第一批失败
    items = Pipeline.from_config(ctx).run()
    failed = {c.id for c in items if any(e.startswith("screen:") for e in c.errors)}
    assert len(failed) == 2
    seen = set(read_json(cfg.data_dir / "seen.json"))
    assert not (failed & seen) and len(seen) == 3

    monkeypatch.setattr(llm_screen, "_screen_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(PipelineError, match="全部失败"):
        Pipeline.from_config(ctx).run()
