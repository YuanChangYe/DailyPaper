"""OpenAI 兼容的 /chat/completions 接口：DeepSeek、Gemini（OpenAI 兼容端点）、OpenRouter、硅基流动等。"""

from __future__ import annotations

import httpx

from ..core.registry import register
from .base import LLMError, LLMProvider, LLMResponse


@register("llm", "openai_compat")
class OpenAICompatProvider(LLMProvider):
    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        if not self.spec.base_url:
            raise LLMError("openai_compat provider 需要配置 base_url")
        body: dict = {
            "model": self.role.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": self.role.max_tokens,
        }
        if self.role.temperature is not None:
            body["temperature"] = self.role.temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.role.options)
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        url = self.spec.base_url.rstrip("/") + "/chat/completions"
        timeout = float(self.spec.options.get("timeout", 300))
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
        if r.status_code in (400, 401, 403, 404):
            raise LLMError(f"{url} 返回 {r.status_code}: {r.text[:500]}")
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return LLMResponse(
            text=choice["message"].get("content") or "",
            model=data.get("model", self.role.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            stop_reason=choice.get("finish_reason") or "",
        )
