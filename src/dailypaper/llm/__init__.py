"""LLM 层：统一的 provider 接口 + 带缓存/重试/用量日志的客户端。

插件只需要 ``get_llm(ctx, "screen")`` / ``get_llm(ctx, "read")``，不关心背后是哪家模型。
"""

from .base import LLMError, LLMProvider, LLMResponse
from .client import LLMClient, get_llm

__all__ = ["LLMClient", "LLMError", "LLMProvider", "LLMResponse", "get_llm"]
