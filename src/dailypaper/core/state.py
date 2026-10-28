"""跨运行的持久状态：已处理论文（data/seen.json）。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .io import read_json, write_json


class SeenStore:
    """记录处理过的论文 ID，避免每天重复筛选。只保留最近 keep_days 天，文件不会无限增长。"""

    def __init__(self, path: Path, keep_days: int = 90) -> None:
        self.path = path
        self.keep_days = keep_days
        self._data: dict[str, str] = read_json(path, {}) or {}

    def __contains__(self, paper_id: str) -> bool:
        return paper_id in self._data

    def __len__(self) -> int:
        return len(self._data)

    def add(self, paper_id: str, day: date) -> None:
        self._data.setdefault(paper_id, day.isoformat())

    def save(self, today: date) -> None:
        cutoff = (today - timedelta(days=self.keep_days)).isoformat()
        self._data = {k: v for k, v in self._data.items() if v >= cutoff}
        write_json(self.path, dict(sorted(self._data.items())))
