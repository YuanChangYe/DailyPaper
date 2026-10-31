"""探索位：每周一次，从「筛过但没入选」的论文里挑 1 篇新颖性高、但不在你主方向上的，标「探索」。

只按研究方向推送容易越看越窄（信息茧房）。默认关闭：pipeline.yaml 里 explore 的 enabled 改成 true。
"""

from __future__ import annotations

from pydantic import BaseModel

from ...core.interfaces import Ranker
from ...core.models import Candidate
from ...core.registry import register


@register("ranker", "explore")
class ExploreRanker(Ranker):
    class Params(BaseModel):
        weekday: int = 0  # 0 = 周一 … 6 = 周日；-1 = 每天
        max_topic_score: float = 6.0  # 最高方向分低于这个值，才算"不在主方向上"
        min_novelty: int = 2  # 0..3

    def run(self, items: list[Candidate]) -> list[Candidate]:
        if self.p.weekday >= 0 and self.ctx.run_date.weekday() != self.p.weekday:
            return items
        pool = [c for c in items if not c.selected and c.screen and c.screen.novelty >= self.p.min_novelty
                and max(c.screen.topic_scores.values(), default=0) < self.p.max_topic_score]
        if not pool:
            self.log.info("探索位：今天没有合适的论文")
            return items
        pick = max(pool, key=lambda c: (c.screen.novelty, c.score))
        pick.selected = True
        pick.meta["explore"] = True
        self.ctx.notes["explore"] = {"id": pick.id, "title": pick.paper.title}
        self.log.info("探索位：%s %s", pick.id, pick.paper.title[:60])
        return items
