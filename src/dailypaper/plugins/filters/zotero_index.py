"""跳过已经在 Zotero 文库中的论文（依据本地助手导出的 data/zotero_index.json）。"""

from __future__ import annotations

from ...core.interfaces import Filter
from ...core.models import Candidate
from ...core.registry import register
from ...integrations.zotero.profile import LibraryIndex


@register("filter", "zotero_index")
class ZoteroIndexFilter(Filter):
    def run(self, items: list[Candidate]) -> list[Candidate]:
        index = LibraryIndex.load(self.config)
        if index is None:
            return items  # 还没导出过索引：不过滤
        kept = [c for c in items if not index.contains(c.paper)]
        if len(kept) < len(items):
            self.log.info("跳过 %d 篇已在 Zotero 中的论文", len(items) - len(kept))
        return kept
