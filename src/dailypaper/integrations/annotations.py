"""网页上的高亮与批注，按论文存在 ``data/annotations/<id>.json``；自由笔记同时写回笔记 frontmatter 的 ``notes``
字段（重新生成笔记时会保留，Obsidian 里也能看到）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..core.config import AppConfig
from ..core.io import read_json, update_frontmatter, write_json


class Highlight(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=2000)
    color: str = "yellow"  # yellow | green | pink | blue
    comment: str = ""
    section: str = ""  # 所在小节标题，便于回看
    created_at: str = ""


class Annotations(BaseModel):
    highlights: list[Highlight] = Field(default_factory=list)
    note: str = ""  # 自由笔记（Markdown）
    updated_at: str = ""


class AnnotationStore:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.dir: Path = cfg.data_dir / "annotations"

    def _path(self, paper_id: str) -> Path:
        return self.dir / f"{paper_id.replace('/', '_')}.json"

    def get(self, paper_id: str) -> Annotations:
        return Annotations.model_validate(read_json(self._path(paper_id), {}) or {})

    def put(self, paper_id: str, data: Annotations, note_path: str | None = None) -> Annotations:
        data.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for h in data.highlights:
            h.created_at = h.created_at or data.updated_at
        write_json(self._path(paper_id), data.model_dump())
        if note_path:
            path = self.cfg.content_dir / note_path
            if path.exists():
                update_frontmatter(path, notes=data.note or None, highlights=len(data.highlights) or None)
        return data

    def all_counts(self) -> dict[str, int]:
        out = {}
        for p in self.dir.glob("*.json"):
            d = read_json(p, {}) or {}
            out[p.stem] = len(d.get("highlights", [])) + (1 if d.get("note") else 0)
        return out
