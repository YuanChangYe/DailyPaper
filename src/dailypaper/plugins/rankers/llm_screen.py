"""LLM 摘要级筛选与排序。

最终排序分 = max_t(相关度_t × 方向权重_t × 反馈乘子_t) + novelty × novelty_bonus + 兴趣画像相似度 × profile_weight

- 反馈乘子来自你的审核决定和 Obsidian 评分（integrations/obsidian/feedback.py）
- 兴趣画像来自 Zotero 文库（integrations/zotero/profile.py），没有画像时该项为 0

``screen_and_score`` 是可复用的核心：给一批 Candidate 填上 ``screen`` 与 ``score``；
stage ``ranker.llm_screen`` 和顶会补足 ``ranker.venue_fill`` 都用它。
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from ...core.context import RunContext
from ...core.interfaces import Ranker
from ...core.models import Candidate, ScreenResult
from ...core.registry import register
from ...integrations.obsidian.feedback import topic_multipliers
from ...integrations.zotero.profile import load_profile
from ...llm import get_llm
from ...prompting.composer import PromptComposer
from ...prompting.selector import LensCatalog


class ScreenOptions(BaseModel):
    batch_size: int = 10
    novelty_bonus: float = 0.5
    profile_weight: float = 1.5
    use_feedback: bool = True
    max_abstract_chars: int = 2000


def _screen_batch(ctx: RunContext, llm, composer: PromptComposer, catalog: dict, batch: list[Candidate], opts: ScreenOptions) -> dict:
    papers = [{"id": c.id, "title": c.paper.title, "abstract": c.paper.abstract[: opts.max_abstract_chars]} for c in batch]
    payload = json.dumps({"topics": [t.id for t in ctx.config.topics], "lens_catalog": list(catalog), "papers": papers}, ensure_ascii=False, indent=1)
    system, user = composer.render("screen", topics=ctx.config.topics, lens_catalog=catalog, papers=papers, payload=payload)
    data = llm.complete_json(system, user, tag="screen")
    results = data.get("results", data) if isinstance(data, dict) else data
    return {str(r.get("id")): r for r in results if isinstance(r, dict)}


def screen_and_score(ctx: RunContext, items: list[Candidate], opts: ScreenOptions | None = None, *, llm=None,
                     composer: PromptComposer | None = None, catalog: dict | None = None, log: logging.Logger | None = None) -> list[str]:
    """对 items 做摘要级筛选并算出排序分（就地写入 c.screen / c.score / c.score_parts）。返回每批的失败信息。

    全部批次都失败时抛 RuntimeError（key、模型名或网络这类整体问题）。
    """
    if not items:
        return []
    opts = opts or ScreenOptions()
    log = log or logging.getLogger("dailypaper.ranker.llm_screen")
    cfg = ctx.config
    llm = llm or get_llm(ctx, "screen")
    composer = composer or PromptComposer(cfg)
    catalog = LensCatalog(cfg).describe() if catalog is None else catalog
    topic_ids = {t.id for t in cfg.topics}

    batches = [items[i : i + opts.batch_size] for i in range(0, len(items), opts.batch_size)]
    failures: list[str] = []
    for n, batch in enumerate(batches, start=1):
        try:
            results = _screen_batch(ctx, llm, composer, catalog, batch, opts)
        except Exception as e:  # noqa: BLE001 - 一批失败不影响其他批
            log.error("筛选第 %d 批失败: %s", n, e)
            failures.append(str(e))
            results = {}
        for c in batch:
            r = results.get(c.id)
            if r is None:
                # 标记为筛选失败：不会被记入 seen.json，下次运行会重新处理
                c.errors.append("screen: 模型没有返回该论文的结果")
                c.screen = ScreenResult(reason="（筛选失败）")
                continue
            scores = {k: float(v) for k, v in (r.get("topic_scores") or {}).items() if k in topic_ids}
            c.screen = ScreenResult(
                topic_scores=scores,
                primary_topic=max(scores, key=scores.get) if scores else "",
                paper_type=str(r.get("paper_type") or "method"),
                novelty=int(r.get("novelty") or 0),
                novelty_signal=str(r.get("novelty_signal") or ""),
                reason=str(r.get("reason") or ""),
                suggested_lenses=[str(x) for x in r.get("suggested_lenses") or []],
            )
    if batches and len(failures) == len(batches):
        raise RuntimeError(f"摘要筛选全部失败（{len(batches)} 批）。最后一个错误: {failures[-1]}")

    mult = topic_multipliers(cfg) if opts.use_feedback else {}
    profile = load_profile(cfg)
    from ...integrations.follow import load_follow

    follow_bonus = load_follow(cfg)["bonus"]
    for c in items:
        s = c.screen
        if not s:
            continue
        rel = max(
            (v * (cfg.topic(t).weight if cfg.topic(t) else 1.0) * mult.get(t, 1.0) for t, v in s.topic_scores.items()),
            default=0.0,
        )
        parts = {"relevance": round(rel, 2), "novelty": s.novelty * opts.novelty_bonus}
        if profile:
            parts["profile"] = round(profile.score(f"{c.paper.title} {c.paper.abstract}") * opts.profile_weight, 2)
        if c.paper.extra.get("followed"):
            parts["follow"] = follow_bonus
        c.score_parts.update(parts)
        c.score = round(sum(parts.values()), 2)
    return failures


@register("ranker", "llm_screen")
class LLMScreenRanker(Ranker):
    class Params(ScreenOptions):
        top_k: int = 5
        min_score: float = 6.0  # 低于该分数的不进入精读
        force_select: bool = False  # read 流水线：不论分数都精读

    def run(self, items: list[Candidate]) -> list[Candidate]:
        if not items:
            return items
        screen_and_score(self.ctx, items, ScreenOptions(**self.p.model_dump(include=set(ScreenOptions.model_fields))), log=self.log)
        items.sort(key=lambda c: -c.score)
        for rank, c in enumerate(items):
            c.selected = self.p.force_select or (rank < self.p.top_k and c.score >= self.p.min_score)
        return items
