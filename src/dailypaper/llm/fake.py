"""离线假模型：没有 API key 时用来跑通全流程和测试。输出是确定性的，格式与真实模型一致。"""

from __future__ import annotations

import hashlib
import json
import re

from ..core.registry import register
from .base import LLMProvider, LLMResponse


def _jitter(s: str, n: int) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16) % n


@register("llm", "fake")
class FakeProvider(LLMProvider):
    def complete(self, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> LLMResponse:
        if tag == "screen":
            text = self._screen(user)
        elif tag == "read":
            text = self._read(user)
        else:
            text = json.dumps({"ok": True}) if json_mode else "（fake 输出）"
        return LLMResponse(text=text, model="fake", input_tokens=len(system + user) // 4, output_tokens=len(text) // 4)

    @staticmethod
    def _payload(user: str) -> dict:
        m = re.search(r"```json\s*(\{.*\})\s*```", user, re.S)
        return json.loads(m.group(1)) if m else {}

    def _screen(self, user: str) -> str:
        data = self._payload(user)
        topics = data.get("topics", [])
        lenses = data.get("lens_catalog", [])
        results = []
        for p in data.get("papers", []):
            text = (p.get("title", "") + " " + p.get("abstract", "")).lower()
            scores = {}
            for t in topics:
                words = [w for w in t.split("_") if len(w) > 2]
                hit = any(w in text for w in words)
                scores[t] = float(min(10, (6 if hit else 2) + _jitter(p["id"] + t, 4)))
            results.append(
                {
                    "id": p["id"],
                    "topic_scores": scores,
                    "paper_type": ["method", "theory", "benchmark", "empirical"][_jitter(p["id"], 4)],
                    "novelty": _jitter(p["id"] + "n", 4),
                    "novelty_signal": "（fake）提出了新的问题设定",
                    "reason": "（fake）与研究方向的关键词重合",
                    "suggested_lenses": lenses[:1],
                }
            )
        return json.dumps({"results": results}, ensure_ascii=False)

    def _read(self, user: str) -> str:
        m = re.search(r"^标题[:：]\s*(.+)$", user, re.M)
        title = m.group(1).strip() if m else "Unknown"
        meta = {
            "tldr": f"（fake）{title} 的一句话总结。",
            "core_insight": "（fake）现有方法假设 X → 实际上 Y → 所以作者做了 Z。",
            "value_score": 3 + _jitter(title, 3),
            "worth_reading": "（fake）值得读第 3 节。",
        }
        sections = [
            "## 0. TL;DR", meta["tldr"],
            "## 1. Problem formulation", "原文未说明（fake）。",
            "## 2. Core insight", meta["core_insight"],
            "## 3. Novelty delta", "| 前作 | 差异 |\n|---|---|\n| A | 原文未说明 |",
            "## 4. Mechanism", "$$s(x, y) = 1 - \\hat p(y \\mid x)$$",
            "## 5. Guarantees", "原文未说明（fake）。",
            "## 6. Claim ↔ Evidence", "| Claim | Evidence | 强度 |\n|---|---|---|\n| C1 | Table 1 | 弱 |",
            "## 7. Red flags & limitations", "- （fake）",
            "## 8. Research hooks", "1. （fake）",
            "## 9. 是否值得读原文", meta["worth_reading"],
        ]
        return "```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```\n\n" + "\n\n".join(sections)
