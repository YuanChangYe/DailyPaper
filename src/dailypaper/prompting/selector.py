"""为每篇论文挑选 lens：规则匹配（lenses.yaml）+ 筛选阶段 LLM 建议，按优先级和数量上限截断。

规则写法（lenses.yaml → rules）：

    - when: {always: true}                 # 总是使用
      use: ["topic:*", "type:*"]          # topic:* = 该论文主要方向的 topic lens；type:* = type_<paper_type>
    - when: {topic: [conformal_selective]} # 主要方向中包含其一
      use: [nature-paper-card]
    - when: {paper_type: [theory]}
      use: [type_theory]
    - when: {min_score: 7}                 # 最终排序分 ≥ 7
      use: [ccf-idea-reviewer]
    - when: {min_novelty: 2}
      use: [...]
"""

from __future__ import annotations

import logging

from ..core import registry
from ..core.config import AppConfig, LensRule, LensSpec
from ..core.models import Candidate
from .lens import Lens, LensAdapter, LensContext

log = logging.getLogger("dailypaper.lens.selector")

TOPIC_PRIORITY = 80  # 研究方向的镜头问题最贴近用户需求，默认优先保留


class LensCatalog:
    """lens 目录：lenses.yaml 中声明的 lens + 每个方向自动生成的 topic lens。"""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        specs: list[LensSpec] = list(cfg.lenses.catalog)
        declared = {s.id for s in specs}
        for t in cfg.topics:
            tid = f"topic:{t.id}"
            if tid not in declared:
                specs.append(
                    LensSpec(id=tid, adapter="topic", priority=TOPIC_PRIORITY, sections=[1, 5, 8], options={"topic": t.id})
                )
        self.adapters: dict[str, LensAdapter] = {}
        for spec in specs:
            try:
                cls = registry.get("lens_adapter", spec.adapter)
            except KeyError as e:
                log.warning("lens %s: %s", spec.id, e)
                continue
            self.adapters[spec.id] = cls(cfg, spec)

    def available_ids(self) -> list[str]:
        return [i for i, a in self.adapters.items() if a.available()]

    def describe(self, include_topics: bool = False) -> dict[str, str]:
        """供筛选 prompt 使用的 {id: 描述}（默认不含 topic lens，它们由规则自动选择）。"""
        return {
            i: (a.description() or a.title())
            for i, a in self.adapters.items()
            if a.available() and (include_topics or not i.startswith("topic:"))
        }


def _rule_matches(rule: LensRule, cand: Candidate, topics: list[str]) -> bool:
    w = rule.when
    if not w or w.get("always"):
        return True
    ok = True
    if "topic" in w:
        ok &= bool(set(w["topic"]) & set(topics))
    if "paper_type" in w:
        ok &= bool(cand.screen) and cand.screen.paper_type in w["paper_type"]
    if "min_score" in w:
        ok &= cand.score >= float(w["min_score"])
    if "min_novelty" in w:
        ok &= bool(cand.screen) and cand.screen.novelty >= int(w["min_novelty"])
    if "source" in w:
        ok &= cand.paper.source in w["source"]
    return ok


def select_lenses(catalog: LensCatalog, cand: Candidate) -> list[Lens]:
    cfg = catalog.cfg.lenses
    topics = cand.main_topics()
    lctx = LensContext(paper=cand.paper, screen=cand.screen, topics=topics, score=cand.score)

    ids: list[str] = []
    for rule in cfg.rules:
        if not _rule_matches(rule, cand, topics):
            continue
        for lid in rule.use:
            if lid == "topic:*":
                ids += [f"topic:{t}" for t in topics]
            elif lid == "type:*":
                ids.append(f"type_{lctx.paper_type}")
            else:
                ids.append(lid)

    if cfg.llm_select.enabled and cand.screen:
        extra = [i for i in cand.screen.suggested_lenses if i not in ids and not i.startswith("topic:")]
        ids += extra[: cfg.llm_select.max_extra]

    lenses: list[Lens] = []
    seen: set[str] = set()
    for lid in ids:
        if lid in seen:
            continue
        seen.add(lid)
        adapter = catalog.adapters.get(lid)
        if adapter is None or not adapter.available():
            if not lid.startswith("type_"):
                log.debug("lens %s 不存在或不可用，跳过", lid)
            continue
        lens = adapter.build(lctx)
        if lens:
            lenses.append(lens)

    lenses.sort(key=lambda x: -x.priority)
    topic_lenses = [x for x in lenses if x.id.startswith("topic:")]
    others = [x for x in lenses if not x.id.startswith("topic:")][: cfg.max_lenses]
    return sorted(topic_lenses + others, key=lambda x: -x.priority)
