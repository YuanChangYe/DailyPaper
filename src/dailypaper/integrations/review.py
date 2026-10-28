"""审核队列：精读完成的论文等你决定是否放进 Zotero。

为避免云端和本地同时改同一个文件导致 git 冲突，拆成两个文件、各由一方写入：
- ``data/review_queue.json``      云端流水线追加（每篇论文的摘要信息），本地只读
- ``data/review_decisions.json``  本地助手写入（你的决定和入库结果），云端只读（用于排序反馈）
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..core.config import AppConfig
from ..core.io import read_json, write_json

Status = Literal["pending", "approved", "later", "rejected", "imported", "failed"]


class QueueEntry(BaseModel):
    id: str
    title: str
    date: str
    authors: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    paper_type: str = ""
    score: float = 0.0
    value_score: int = 0
    tldr: str = ""
    core_insight: str = ""
    worth_reading: str = ""
    note_path: str = ""
    url_abs: str = ""
    url_pdf: str = ""
    doi: str = ""
    abstract: str = ""
    source: str = "arxiv"


class Decision(BaseModel):
    status: Status = "pending"
    collection: str = ""
    decided_at: str = ""
    zotero_key: str = ""
    citekey: str = ""
    error: str = ""


class ReviewItem(BaseModel):
    entry: QueueEntry
    decision: Decision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewStore:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.queue_path: Path = cfg.data_dir / "review_queue.json"
        self.decisions_path: Path = cfg.data_dir / "review_decisions.json"

    # ---------------------------------------------------------- 读
    def entries(self) -> list[QueueEntry]:
        return [QueueEntry.model_validate(e) for e in read_json(self.queue_path, []) or []]

    def decisions(self) -> dict[str, Decision]:
        raw = read_json(self.decisions_path, {}) or {}
        return {k: Decision.model_validate(v) for k, v in raw.items()}

    def items(self, status: str | None = None) -> list[ReviewItem]:
        decisions = self.decisions()
        out = [ReviewItem(entry=e, decision=decisions.get(e.id, Decision())) for e in self.entries()]
        if status:
            wanted = set(status.split(","))
            out = [i for i in out if i.decision.status in wanted]
        return sorted(out, key=lambda i: (i.entry.date, i.entry.score), reverse=True)

    def get(self, paper_id: str) -> ReviewItem | None:
        return next((i for i in self.items() if i.entry.id == paper_id), None)

    # ---------------------------------------------------------- 写（云端）
    def append(self, entries: list[QueueEntry]) -> int:
        current = read_json(self.queue_path, []) or []
        have = {e["id"] for e in current}
        added = [e.model_dump() for e in entries if e.id not in have]
        if added:
            write_json(self.queue_path, current + added)
        return len(added)

    # ---------------------------------------------------------- 写（本地）
    def decide(self, paper_id: str, status: Status, **fields) -> Decision:
        raw = read_json(self.decisions_path, {}) or {}
        d = Decision.model_validate(raw.get(paper_id, {}))
        d = d.model_copy(update={"status": status, "decided_at": _now(), **fields})
        raw[paper_id] = d.model_dump()
        write_json(self.decisions_path, raw)
        return d
