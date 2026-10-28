"""去重：跳过以前处理过的论文。配合 publisher.mark_seen 在运行成功后写回 data/seen.json。"""

from __future__ import annotations

from pydantic import BaseModel

from ...core.interfaces import Filter, Publisher
from ...core.io import read_json, write_json
from ...core.models import Candidate
from ...core.registry import register
from ...core.state import SeenStore

NEW_IDS_FILE = "new_ids.json"


def seen_store(cfg, keep_days: int = 90) -> SeenStore:
    return SeenStore(cfg.data_dir / "seen.json", keep_days=keep_days)


@register("filter", "seen")
class SeenFilter(Filter):
    def run(self, items: list[Candidate]) -> list[Candidate]:
        store = seen_store(self.config)
        fresh = [c for c in items if c.id not in store]
        # 记下本次的新论文，等流水线跑完再由 mark_seen 写入（中途失败则下次重新处理）
        write_json(self.ctx.run_dir / NEW_IDS_FILE, [c.id for c in fresh])
        return fresh


@register("publisher", "mark_seen")
class MarkSeen(Publisher):
    class Params(BaseModel):
        keep_days: int = 90

    def run(self, items: list[Candidate]) -> list[Candidate]:
        store = seen_store(self.config, self.p.keep_days)
        ids = read_json(self.ctx.run_dir / NEW_IDS_FILE, None)
        for pid in ids if ids is not None else [c.id for c in items]:
            store.add(pid, self.ctx.run_date)
        store.save(self.ctx.run_date)
        self.log.info("seen.json 共 %d 条", len(store))
        return items
