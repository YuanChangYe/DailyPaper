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
            text = self._read(system, user)
        elif tag == "ask":
            q = re.search(r"^问题[:：]\s*(.+)$", user, re.M)
            text = f"（fake）关于「{q.group(1).strip() if q else '这个问题'}」：论文的做法见 [p.1]【§2】。〔分析〕这是离线假模型的回答。"
        elif tag == "search_expand":
            q = re.search(r"^问题[:：]\s*(.+)$", user, re.M)
            words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", q.group(1) if q else user)[:6] or ["language", "model"]
            text = json.dumps({"intent": "（fake）检索意图", "keywords_en": words,
                               "fts": " OR ".join(words), "arxiv_query": " OR ".join(f'all:"{w}"' for w in words)})
        elif tag == "search_rerank":
            data = self._payload(user)
            text = json.dumps({"results": [{"i": c["i"], "score": float(9 - _jitter(c.get("title", "") + str(c["i"]), 6)),
                                            "reason": "（fake）标题与问题相关"} for c in data.get("candidates", [])]}, ensure_ascii=False)
        elif tag == "report":
            text = "## 方法分组\n\n（fake）[1] 与 [2] 属于同一类。\n\n## 研究空白\n\n（fake）〔分析〕还没有人做 X。\n\n## Follow-up idea\n\n（fake）把 [1] 用到 Y 上。"
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

    # 系统提示里"输出格式"之后的 "## N. 标题" 就是框架要求的各节；假模型照着输出，所以不依赖 schema 的具体内容
    _SECTION_RE = re.compile(r"^## (\d+\. .+?)\s*$", re.M)
    _FILLERS = ["原文未说明（fake）。", "$$s(x, y) = 1 - \\hat p(y \\mid x)$$", "| A | B |\n|---|---|\n| 1 | 原文未说明 |", "1. （fake）"]

    def _read(self, system: str, user: str) -> str:
        m = re.search(r"^标题[:：]\s*(.+)$", user, re.M)
        title = m.group(1).strip() if m else "Unknown"
        meta = {
            "tldr": f"（fake）{title} 的一句话总结。",
            "core_insight": "（fake）现有方法假设 X，实际上 Y，所以作者做了 Z。",
            "value_score": 3 + _jitter(title, 3),
            "worth_reading": "（fake）快速了解即可。",
        }
        body = system.split("## 输出格式", 1)[-1].split("# 补充分析指引", 1)[0]
        titles = self._SECTION_RE.findall(body) or ["1. 摘要翻译", "2. Take-aways"]
        parts = []
        for i, t in enumerate(titles):
            parts += [f"## {t}", self._FILLERS[i % len(self._FILLERS)]]
        return "```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```\n\n" + "\n\n".join(parts)
