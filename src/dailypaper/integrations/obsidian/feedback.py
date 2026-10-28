"""阅读反馈回流：把你的审核决定和 Obsidian 笔记里的评分变成排序信号。

信号来源：
- data/review_decisions.json：加入 Zotero（+）、不要（−）
- 笔记 frontmatter：``rating``（1–5，你给的评分）、``zotero_approve: true``（在 Obsidian 里勾选入库）

输出每个方向的权重乘子（0.8–1.2），由 ranker.llm_screen 使用。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import AppConfig
from ...core.io import read_markdown
from ..review import ReviewStore


@dataclass
class NoteState:
    path: Path
    paper_id: str
    topics: list[str]
    rating: int | None = None
    zotero_approve: bool = False
    zotero_key: str = ""
    fm: dict = field(default_factory=dict)


def scan_notes(cfg: AppConfig) -> list[NoteState]:
    root = cfg.content_dir / "papers"
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*.md"):
        try:
            fm, _ = read_markdown(p)
        except Exception:  # noqa: BLE001 - 用户手改坏的 frontmatter 不应中断流程
            continue
        pid = str(fm.get("paper_id") or fm.get("arxiv_id") or p.stem)
        rating = fm.get("rating")
        out.append(
            NoteState(
                path=p,
                paper_id=pid,
                topics=list(fm.get("topics") or []),
                rating=int(rating) if isinstance(rating, (int, float)) else None,
                zotero_approve=bool(fm.get("zotero_approve")),
                zotero_key=str(fm.get("zotero_key") or ""),
                fm=fm,
            )
        )
    return out


def topic_multipliers(cfg: AppConfig, step: float = 0.04, lo: float = 0.8, hi: float = 1.2) -> dict[str, float]:
    votes: dict[str, float] = defaultdict(float)
    store = ReviewStore(cfg)
    topics_of = {e.id: e.topics for e in store.entries()}
    for pid, d in store.decisions().items():
        delta = {"approved": 1.0, "imported": 1.0, "rejected": -1.0}.get(d.status, 0.0)
        for t in topics_of.get(pid, []):
            votes[t] += delta
    if cfg.settings.obsidian.feedback:
        for n in scan_notes(cfg):
            if n.rating is not None:
                for t in n.topics:
                    votes[t] += (n.rating - 3) / 2  # 5 星 = +1，1 星 = −1
    return {t: max(lo, min(hi, 1.0 + step * v)) for t, v in votes.items()}
