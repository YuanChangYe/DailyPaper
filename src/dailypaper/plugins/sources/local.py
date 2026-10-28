"""本地数据源：从文件读取论文（测试、离线调试，或导入别处导出的列表）。

支持 arXiv Atom XML（.xml）和 Paper 列表 JSON（.json，元素字段同 Paper 模型）。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ...core.interfaces import Source
from ...core.models import Candidate, Paper
from ...core.registry import register
from .arxiv import parse_atom


def load_papers(path: Path) -> list[Paper]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".xml":
        return parse_atom(text)
    return [Paper.model_validate(p) for p in json.loads(text)]


@register("source", "file")
class FileSource(Source):
    class Params(BaseModel):
        path: str

    def run(self, items: list[Candidate]) -> list[Candidate]:
        papers = load_papers(self.config.path(self.p.path))
        have = {c.id for c in items}
        return items + [Candidate(paper=p) for p in papers if p.id not in have]
