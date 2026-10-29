"""顶会专栏 / 补足：arXiv 当天入选不足 total 篇时，从顶会论文池里挑最相关、还没读过的论文补上。

放在 daily 流水线的 screen 之后：
    - {id: venue_fill, use: ranker.venue_fill}

逻辑：
1. n = 已入选（selected）的篇数，want = total − n；want ≤ 0 直接返回。
2. 距上次同步超过 auto_sync_days 时先增量同步论文池（失败只记日志）。
3. 从池里取未读、未 seen 的论文，用 topics.yaml 的关键词预筛；已筛选过的用缓存分数，
   未筛选的最多 screen_budget 篇交给 LLM 筛选（结果写回池子，下次不再花钱）。
4. 按分数取前 want 篇且 ≥ min_score，作为 selected 候选并入列表，meta.filled_from = "venue"。
参数默认来自 config/venues.yaml 的 column，可在 pipeline.yaml 的 with 里覆盖。
"""

from __future__ import annotations

from pydantic import BaseModel

from ...core.interfaces import Ranker
from ...core.models import Candidate
from ...core.registry import register
from ...integrations.venues.pool import VenuePool
from ...integrations.venues.sync import needs_sync, sync_venues
from ..filters.keyword import TopicMatcher
from ..filters.seen import seen_store
from .llm_screen import ScreenOptions, screen_and_score


@register("ranker", "venue_fill")
class VenueFill(Ranker):
    class Params(BaseModel):
        total: int | None = None
        min_score: float | None = None
        screen_budget: int | None = None
        auto_sync: bool = True
        pool_limit: int = 0  # 一次最多从池里取多少条做关键词预筛；0 = 全部（几万条的正则预筛只要几秒）
        venues: list[str] | None = None  # 只用这些会议；默认全部启用的

    def run(self, items: list[Candidate]) -> list[Candidate]:
        col = self.config.venues.column
        total = self.p.total if self.p.total is not None else col.total
        min_score = self.p.min_score if self.p.min_score is not None else col.min_score
        budget = self.p.screen_budget if self.p.screen_budget is not None else col.screen_budget
        n_have = sum(1 for c in items if c.selected)
        want = total - n_have
        if want <= 0 or not self.config.venues.enabled():
            self.log.info("已入选 %d 篇，不需要从顶会池补足", n_have)
            return items

        pool = VenuePool(self.config)
        if self.p.auto_sync and needs_sync(self.config, pool):
            self.log.info("论文池超过 %d 天没同步，先增量同步", self.config.venues.auto_sync_days)
            try:
                res = sync_venues(self.config, pool=pool)
                self.log.info("同步完成：新增 %d 篇", sum(r.get("new", 0) for r in res))
            except Exception as e:  # noqa: BLE001 - 同步失败不影响补足
                self.log.warning("论文池同步失败，用现有数据：%s", e)

        seen = seen_store(self.config)
        venues = self.p.venues or [v.id for v in self.config.venues.enabled()]
        matchers = {t.id: TopicMatcher(t) for t in self.config.topics}
        have_ids = {c.id for c in items} | {f"arxiv:{c.id}" for c in items}
        cands: list[Candidate] = []
        for row in pool.candidates(venues=venues, limit=self.p.pool_limit):
            paper = pool.to_paper(row)
            aid = paper.extra.get("arxiv_id") or ""
            if paper.id in have_ids or paper.id in seen or (aid and aid in seen):
                continue
            text = f"{paper.title}\n{paper.abstract}"
            hits = {tid: h for tid, m in matchers.items() if (h := m.match(text))}
            if not hits:
                continue
            c = Candidate(paper=paper, keyword_hits=hits, meta={"filled_from": "venue"})
            screen, score = pool.screened_of(paper.id)
            if screen is not None:
                c.screen, c.score = screen, float(score or 0)
            cands.append(c)
        if not cands:
            self.log.info("顶会池里没有命中关键词的未读论文")
            self.ctx.notes["venue_fill"] = {"n": 0, "pool_candidates": 0, "screened": 0}
            return items

        unscreened = [c for c in cands if c.screen is None][:budget]
        if unscreened:
            self.log.info("顶会池候选 %d 篇，本次筛选 %d 篇（预算 %d）", len(cands), len(unscreened), budget)
            try:
                screen_and_score(self.ctx, unscreened, ScreenOptions(), log=self.log)
            except RuntimeError as e:
                self.log.error("顶会池筛选失败：%s", e)
                unscreened = []
            for c in unscreened:
                if c.screen and not any(e.startswith("screen:") for e in c.errors):
                    pool.set_screened(c.id, c.screen, c.score)

        scored = sorted((c for c in cands if c.screen is not None and c.score >= min_score), key=lambda c: -c.score)
        picked = scored[:want]
        for c in picked:
            c.selected = True
        self.ctx.notes["venue_fill"] = {"n": len(picked), "pool_candidates": len(cands), "screened": len(unscreened),
                                        "ids": [c.id for c in picked], "arxiv_selected": n_have}
        self.log.info("从顶会池补足 %d 篇（目标 %d，arXiv 已有 %d）", len(picked), total, n_have)
        return items + picked
