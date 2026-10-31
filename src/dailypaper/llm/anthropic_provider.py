"""Claude（Anthropic 官方 SDK），默认的 provider。

- 不发送 temperature（当前 Claude 模型已不接受采样参数）。
- 用 ``output_config.effort`` 控制思考深度：筛选用 low，精读用 high。
- system prompt 打上 cache_control：同一批筛选请求共享系统提示，命中缓存可省钱。
- 默认开启服务端 refusal fallback（``fallbacks: "default"``），可在 llm.yaml 的 provider options 关闭。
- 用流式请求 + get_final_message()，避免长输出触发 HTTP 超时。
- 角色设置 ``web_search: true`` 时附带服务端联网搜索工具（精读的"后续研究调研"用）；
  搜索结果块不进入正文，只保留模型最终写出的文字。
"""

from __future__ import annotations

import os

import anthropic

from ..core.registry import register
from .base import Cancelled, LLMError, LLMProvider, LLMResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"
MAX_CONTINUATIONS = 3  # 服务端工具可能返回 pause_turn，最多续写几次


@register("llm", "anthropic")
class AnthropicProvider(LLMProvider):
    def __init__(self, spec, role) -> None:
        super().__init__(spec, role)
        # 没有 key 时 SDK 不会立刻报错，而是在第一次请求时失败；这里提前检查，给出明确提示。
        # 只有设置了 ANTHROPIC_AUTH_TOKEN（例如 ant auth login 的凭据）时才允许不填 key。
        key = self.api_key(required=not os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        kwargs: dict = {"max_retries": 2}
        if key:
            kwargs["api_key"] = key
        if spec.base_url:
            kwargs["base_url"] = spec.base_url
        self.client = anthropic.Anthropic(**kwargs)
        # Haiku 没有服务端 fallback，只对其他模型开启
        self.use_fallbacks = bool(spec.options.get("fallbacks", True)) and "haiku" not in role.model

    def _request(self, params: dict, on_delta=None):
        ctx = (self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **params) if self.use_fallbacks
               else self.client.messages.stream(**params))
        with ctx as stream:
            if on_delta:
                for event in stream:
                    if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                        on_delta(event.delta.text)
            return stream.get_final_message()

    def stream(self, system: str, user: str, on_delta, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        return self.complete(system, user, tag=tag, json_mode=json_mode, on_delta=on_delta)

    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False, on_delta=None) -> LLMResponse:
        if json_mode:
            system += "\n\n只输出一个合法的 JSON 对象，不要输出任何其他文字。"
        messages: list[dict] = [{"role": "user", "content": user}]
        params: dict = {
            "model": self.role.model,
            "max_tokens": self.role.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if self.role.effort:
            params["output_config"] = {"effort": self.role.effort}
        if self.role.web_search and not json_mode:
            params["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": self.role.web_search_max_uses}]
        params.update(self.role.options)

        text_parts: list[str] = []
        input_tokens = output_tokens = 0
        try:
            for _ in range(MAX_CONTINUATIONS + 1):
                msg = self._request(params, on_delta)
                input_tokens += msg.usage.input_tokens
                output_tokens += msg.usage.output_tokens
                if msg.stop_reason == "refusal":
                    raise LLMError(f"Claude 拒绝了这个请求（{tag}）")
                text_parts += [b.text for b in msg.content if b.type == "text"]
                if msg.stop_reason != "pause_turn":
                    break
                # 服务端工具中途暂停：把已有内容作为 assistant 回合续上，再请求一次
                messages.append({"role": "assistant", "content": msg.content})
        except Cancelled:
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMError(f"Claude API 认证失败，请检查 {self.spec.api_key_env}: {e}") from e
        except (anthropic.BadRequestError, anthropic.NotFoundError) as e:
            raise LLMError(f"Claude API 请求无效（模型名或参数）: {e}") from e
        # RateLimitError / 5xx / 连接错误交给 LLMClient 重试（见 is_transient）

        return LLMResponse(
            text="".join(text_parts),
            model=msg.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=msg.stop_reason or "",
        )

    def is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code >= 500
        return super().is_transient(exc)
