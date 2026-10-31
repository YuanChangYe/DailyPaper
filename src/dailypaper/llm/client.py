"""LLM 客户端：在 provider 外面包一层磁盘缓存、重试、JSON 解析和用量日志。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from ..core import registry
from ..core.context import RunContext
from ..core.io import read_json, write_json
from . import usage
from .base import Cancelled, LLMError, LLMProvider, LLMResponse

log = logging.getLogger("dailypaper.llm")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def extract_json(text: str) -> Any:
    """从模型输出中取出 JSON：优先 ```json 代码块，其次第一个 { 到最后一个 }。"""
    m = _JSON_FENCE_RE.search(text)
    candidates = [m.group(1)] if m else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise LLMError("模型输出中没有合法的 JSON")


class LLMClient:
    def __init__(self, ctx: RunContext, role_name: str, provider: LLMProvider, provider_name: str) -> None:
        self.ctx = ctx
        self.role_name = role_name
        self.provider = provider
        self.provider_name = provider_name
        self.cfg = ctx.config.llm
        self.cache_dir = ctx.config.cache_dir / "llm"

    @property
    def model(self) -> str:
        return f"{self.provider_name}:{self.provider.role.model}"

    def _cache_key(self, system: str, user: str, json_mode: bool) -> str:
        r = self.provider.role
        payload = json.dumps(
            [self.provider_name, r.model, r.max_tokens, r.effort, r.temperature, json_mode, system, user],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        return self._call(system, user, tag=tag, json_mode=json_mode)

    def stream(self, system: str, user: str, on_delta, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        """边生成边回调 on_delta(文字片段)。命中缓存时一次性回调全文。on_delta 抛 Cancelled 可中止。"""
        return self._call(system, user, tag=tag, json_mode=json_mode, on_delta=on_delta)

    def _call(self, system: str, user: str, *, tag: str, json_mode: bool, on_delta=None) -> LLMResponse:
        use_cache = self.cfg.cache and self.provider_name != "fake"
        key = self._cache_key(system, user, json_mode)
        cache_path = self.cache_dir / key[:2] / f"{key}.json"
        if use_cache and cache_path.exists():
            resp = LLMResponse.model_validate(read_json(cache_path))
            resp.cached = True
            if on_delta:
                on_delta(resp.text)
            return resp

        last: Exception | None = None
        sent: list[str] = []  # 流式时已经交给调用方的文字

        def relay(piece: str) -> None:
            sent.append(piece)
            on_delta(piece)

        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                if on_delta:
                    resp = self.provider.stream(system, user, relay, tag=tag, json_mode=json_mode)
                else:
                    resp = self.provider.complete(system, user, tag=tag, json_mode=json_mode)
                break
            except (LLMError, Cancelled):
                raise  # 配置类错误（缺 key 等）和用户取消不重试
            except Exception as e:  # noqa: BLE001
                if sent:  # 已经输出了一部分，重试会让网页上的文字重复
                    raise LLMError(f"LLM 输出中断（{self.model}）: {type(e).__name__}: {e}") from e
                if not self.provider.is_transient(e):
                    raise LLMError(f"LLM 调用失败（{self.model}）: {type(e).__name__}: {e}") from e
                last = e
                wait = min(2**attempt, 30)
                log.warning("LLM 调用失败（%s，第 %d 次），%ds 后重试: %s", tag, attempt, wait, e)
                time.sleep(wait)
        else:
            raise LLMError(f"LLM 调用连续失败 {self.cfg.max_retries} 次: {last}") from last

        if use_cache:
            write_json(cache_path, resp.model_dump())
        self._log_usage(tag, resp)
        return resp

    def complete_json(self, system: str, user: str, *, tag: str = "") -> Any:
        """要求 JSON 输出；解析失败时带上错误提示重试一次。"""
        resp = self.complete(system, user, tag=tag, json_mode=self.provider.role.json_mode)
        try:
            return extract_json(resp.text)
        except LLMError:
            fix = user + "\n\n（上一次输出不是合法 JSON。请只输出一个 JSON 对象，不要任何其他文字。）"
            resp = self.complete(system, fix, tag=tag, json_mode=self.provider.role.json_mode)
            return extract_json(resp.text)

    def _log_usage(self, tag: str, resp: LLMResponse) -> None:
        line = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "role": self.role_name,
                "tag": tag,
                "model": self.model,
                "in": resp.input_tokens,
                "out": resp.output_tokens,
            },
            ensure_ascii=False,
        )
        with (self.ctx.run_dir / "usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.provider_name != "fake":
            usage.record(self.ctx.config, self.provider.role.model, resp.input_tokens, resp.output_tokens, self.ctx.run_date,
                         feature=usage.feature_of(self.role_name, tag))


def get_llm(ctx: RunContext, role_name: str, **overrides: Any) -> LLMClient:
    """按 llm.yaml 中的角色创建客户端；--provider 覆盖时所有角色都改用该 provider。

    overrides 临时改角色参数（例如 web_search=True），不写回配置。"""

    def factory() -> LLMClient:
        cfg = ctx.config.llm
        role = cfg.roles.get(role_name) or cfg.roles.get("default")
        if role is None:
            raise LLMError(f"llm.yaml 中没有角色 {role_name}（也没有 default）")
        provider_name = ctx.provider_override or role.provider
        if ctx.provider_override:
            role = role.model_copy(update={"provider": provider_name})
        if overrides:
            role = role.model_copy(update=overrides)
        spec = cfg.providers.get(provider_name)
        if spec is None:
            if provider_name != "fake":
                raise LLMError(f"llm.yaml 中没有 provider {provider_name}")
            from ..core.config import ProviderSpec

            spec = ProviderSpec(type="fake")
        cls = registry.get("llm", spec.type)
        return LLMClient(ctx, role_name, cls(spec, role), provider_name)

    key = f"llm:{role_name}" + (":" + json.dumps(overrides, sort_keys=True) if overrides else "")
    return ctx.service(key, factory)
