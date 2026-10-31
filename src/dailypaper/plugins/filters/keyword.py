"""关键词预筛（本地正则，不花钱）。

规则（每个方向独立判断，任一方向通过即保留）：
- 命中至少一个 ``keywords``（强关键词）；
- 若该方向配置了 ``context``（语境词，例如 LLM / agent），还需命中至少一个语境词；
- 命中 ``exclude`` 则该方向不通过。

通过的候选按命中数排序，最多保留 ``max_candidates`` 篇，控制后续 LLM 成本。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ...core.config import Topic
from ...core.interfaces import Filter
from ...core.models import Candidate
from ...core.registry import register


def compile_terms(terms: list[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for t in terms:
        body = r"[\s\-]+".join(re.escape(w) for w in t.split())
        left = r"\b" if t[:1].isalnum() else ""
        right = r"" if t.endswith("*") else (r"\b" if t[-1:].isalnum() else "")
        body = body.replace(r"\*", r"\w*")
        out.append((t, re.compile(left + body + right, re.I)))
    return out


class TopicMatcher:
    def __init__(self, topic: Topic) -> None:
        self.topic = topic
        self.keywords = compile_terms(topic.keywords)
        self.context = compile_terms(topic.context)
        self.exclude = compile_terms(topic.exclude)

    def match(self, text: str) -> list[str]:
        if any(p.search(text) for _, p in self.exclude):
            return []
        hits = [t for t, p in self.keywords if p.search(text)]
        if hits and self.context and not any(p.search(text) for _, p in self.context):
            return []
        return hits


@register("filter", "keyword")
class KeywordFilter(Filter):
    class Params(BaseModel):
        max_candidates: int = 80
        title_weight: float = 2.0  # 标题命中比摘要命中更重要

    def run(self, items: list[Candidate]) -> list[Candidate]:
        from ...integrations.follow import load_follow, paper_matches

        matchers = [TopicMatcher(t) for t in self.config.topics]
        follow = load_follow(self.config)
        kept: list[tuple[float, Candidate]] = []
        for c in items:
            text = f"{c.paper.title}\n{c.paper.abstract}"
            hits = {m.topic.id: h for m in matchers if (h := m.match(text))}
            followed = paper_matches(follow, c.paper) if follow["authors"] or follow["institutions"] else []
            if followed:  # 关注的作者 / 机构：不看关键词，直接进入筛选
                c.paper.extra["followed"] = followed
            if not hits and not followed:
                continue
            title_hits = sum(1 for m in matchers for _, p in m.keywords if p.search(c.paper.title))
            score = sum(len(h) * self.config.topic(t).weight for t, h in hits.items()) + self.p.title_weight * title_hits
            c.keyword_hits = hits
            c.score_parts["keyword"] = round(score, 2)
            kept.append((score, c))
        kept.sort(key=lambda x: -x[0])
        return [c for _, c in kept[: self.p.max_candidates]]
