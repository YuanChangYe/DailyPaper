"""顶会论文读完后写回论文池（read_at），并把它的 arXiv id 也记入 seen.json，避免同一篇再从 arXiv 读一遍。"""

from __future__ import annotations

from ...core.interfaces import Publisher
from ...core.models import Candidate
from ...core.registry import register
from ...integrations.venues.pool import VenuePool
from ..filters.seen import seen_store


@register("publisher", "venue_mark")
class VenueMark(Publisher):
    def run(self, items: list[Candidate]) -> list[Candidate]:
        venue = [c for c in items if c.paper.source == "venue"]
        if not venue:
            return items
        pool = VenuePool(self.config)
        store = seen_store(self.config)
        n = 0
        for c in venue:
            if c.note:
                pool.mark_read(c.id)
                n += 1
            store.add(c.id, self.ctx.run_date)
            aid = c.paper.extra.get("arxiv_id")
            if aid:
                store.add(aid, self.ctx.run_date)
        pool.mark_seen([c.id for c in venue])
        store.save(self.ctx.run_date)
        self.log.info("顶会论文：%d 篇标记为已读，%d 篇记入 seen", n, len(venue))
        return items
