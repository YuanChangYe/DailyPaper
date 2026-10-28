"""Claude（Anthropic 官方 SDK），默认的 provider。

- 不发送 temperature（当前 Claude 模型已不接受采样参数）。
- 用 ``output_config.effort`` 控制思考深度：筛选用 low，精读用 high。
- system prompt 打上 cache_control：同一批筛选请求共享系统提示，命中缓存可省钱。
- 默认开启服务端 refusal fallback（``fallbacks: "default"``），可在 llm.yaml 的 provider options 关闭。
- 用流式请求 + get_final_message()，避免长输出触发 HTTP 超时。
"""

from __future__ import annotations

import os

import anthropic

from ..core.registry import register
from .base import LLMError, LLMProvider, LLMResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"


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

    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        if json_mode:
            system += "\n\n只输出一个合法的 JSON 对象，不要输出任何其他文字。"
        params: dict = {
            "model": self.role.model,
            "max_tokens": self.role.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if self.role.effort:
            params["output_config"] = {"effort": self.role.effort}
        params.update(self.role.options)
        try:
            if self.use_fallbacks:
                with self.client.beta.messages.stream(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                ) as stream:
                    msg = stream.get_final_message()
            else:
                with self.client.messages.stream(**params) as stream:
                    msg = stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMError(f"Claude API 认证失败，请检查 {self.spec.api_key_env}: {e}") from e
        except (anthropic.BadRequestError, anthropic.NotFoundError) as e:
            raise LLMError(f"Claude API 请求无效（模型名或参数）: {e}") from e
        # RateLimitError / 5xx / 连接错误交给 LLMClient 重试（见 is_transient）

        if msg.stop_reason == "refusal":
            raise LLMError(f"Claude 拒绝了这个请求（{tag}）")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return LLMResponse(
            text=text,
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            stop_reason=msg.stop_reason or "",
        )

    def is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code >= 500
        return super().is_transient(exc)
