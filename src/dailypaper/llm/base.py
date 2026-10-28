"""LLM provider 接口。新增一家模型 = 写一个子类并 ``@register("llm", "<type>")``。"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from pydantic import BaseModel

from ..core.config import LLMRole, ProviderSpec


class LLMError(RuntimeError):
    pass


class LLMResponse(BaseModel):
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    stop_reason: str = ""


class LLMProvider(ABC):
    def __init__(self, spec: ProviderSpec, role: LLMRole) -> None:
        self.spec = spec
        self.role = role

    def api_key(self, required: bool = True) -> str | None:
        env = self.spec.api_key_env
        key = os.environ.get(env) if env else None
        if required and env and not key:
            raise LLMError(
                f"缺少环境变量 {env}。本地运行请先设置它；GitHub Actions 中请在仓库 Secrets 里添加 {env}。"
            )
        return key

    @abstractmethod
    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        """一次单轮调用。tag 用于日志和 fake provider 区分任务（screen / read / ...）。"""

    def is_transient(self, exc: Exception) -> bool:
        """是否值得重试：网络抖动、超时、限流、服务端错误。其他异常（配置、认证、参数）立即失败。"""
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError))
