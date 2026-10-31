"""OpenAI 兼容的 /chat/completions 接口：DeepSeek、Gemini（OpenAI 兼容端点）、OpenRouter、硅基流动等。"""

from __future__ import annotations

import json

import httpx

from ..core.registry import register
from .base import LLMError, LLMProvider, LLMResponse


@register("llm", "openai_compat")
class OpenAICompatProvider(LLMProvider):
    def _body(self, system: str, user: str, json_mode: bool) -> dict:
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
        return body

    def stream(self, system: str, user: str, on_delta, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        if not self.spec.base_url:
            raise LLMError("openai_compat provider 需要配置 base_url")
        body = self._body(system, user, json_mode) | {"stream": True, "stream_options": {"include_usage": True}}
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        url = self.spec.base_url.rstrip("/") + "/chat/completions"
        timeout = float(self.spec.options.get("timeout", 300))
        parts: list[str] = []
        usage: dict = {}
        model, finish = self.role.model, ""
        with httpx.stream("POST", url, json=body, headers=headers, timeout=timeout) as r:
            if r.status_code in (400, 401, 403, 404):
                raise LLMError(f"{url} 返回 {r.status_code}: {r.read().decode('utf-8', 'replace')[:500]}")
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                model = chunk.get("model") or model
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        on_delta(piece)
                    finish = choice.get("finish_reason") or finish
        text = "".join(parts)
        return LLMResponse(
            text=text,
            model=model,
            # 少数服务流式时不返回用量：按字符粗估，保证记账不为 0
            input_tokens=usage.get("prompt_tokens") or (len(system) + len(user)) // 3,
            output_tokens=usage.get("completion_tokens") or len(text) // 2,
            stop_reason=finish,
        )

    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        if not self.spec.base_url:
            raise LLMError("openai_compat provider 需要配置 base_url")
        body = self._body(system, user, json_mode)
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
