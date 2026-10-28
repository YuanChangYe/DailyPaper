"""按 ID 读取指定论文（``dailypaper read <id>`` 使用的 read 流水线入口）。

- arXiv ID（如 2610.01234）：从 arXiv API 取元数据
- Zotero 条目 key（如 ABCD1234 或 zotero:ABCD1234）：从本机 Zotero 取元数据和本地 PDF
"""

from __future__ import annotations

from pydantic import BaseModel

from ...core.interfaces import Source
from ...core.models import ARXIV_ID_RE, Candidate
from ...core.registry import register
from .arxiv import fetch_by_ids


@register("source", "ids")
class IdsSource(Source):
    class Params(BaseModel):
        input_key: str = "ids"  # 从 ctx.inputs 中取 ID 列表

    def run(self, items: list[Candidate]) -> list[Candidate]:
        ids: list[str] = list(self.ctx.inputs.get(self.p.input_key) or [])
        arxiv_ids: list[str] = []
        zotero_keys: list[str] = []
        for raw in ids:
            m = ARXIV_ID_RE.match(raw.removeprefix("arXiv:"))
            if m:
                arxiv_ids.append(m.group(1))  # 去掉版本号，取最新版
            else:
                zotero_keys.append(raw.removeprefix("zotero:"))
        out = list(items)
        if arxiv_ids:
            out += [Candidate(paper=p, selected=True) for p in fetch_by_ids(arxiv_ids)]
        if zotero_keys:
            from ...integrations.zotero.service import ZoteroService

            zs = ZoteroService.from_context(self.ctx)
            for key in zotero_keys:
                paper = zs.paper_from_item(key)
                out.append(Candidate(paper=paper, selected=True))
        if not out:
            self.log.warning("没有可读取的论文：%s", ids)
        return out
