"""在各个 stage 之间流转的数据模型。

一个 ``Candidate`` 走完整条流水线，沿途逐步填充字段：
关键词命中 → 筛选结果 → 全文 → 精读笔记 → 渲染路径。
所有 stage 的输入输出都是 ``list[Candidate]``，因此任意 stage 都可以增删或重排；
模型都是普通 pydantic，可以落盘为 JSONL，再读回来断点续跑。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Paper(BaseModel):
    """一篇论文的元数据（与来源无关）。"""

    id: str  # arXiv 论文为不带版本号的 ID，例如 2610.01234；其他来源自定义（如 zotero:ABCD1234）
    source: str = "arxiv"
    version: str = ""  # 例如 "v2"
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    primary_category: str = ""
    published: datetime | None = None
    updated: datetime | None = None
    doi: str = ""
    comment: str = ""
    url_abs: str = ""
    url_pdf: str = ""
    url_html: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_arxiv(self) -> bool:
        return self.source == "arxiv" and bool(ARXIV_ID_RE.match(self.id))

    @property
    def slug(self) -> str:
        """可安全用作文件名的 ID。"""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.id)


class ScreenResult(BaseModel):
    """筛选阶段（只看标题和摘要）的结论。"""

    topic_scores: dict[str, float] = Field(default_factory=dict)  # topic id -> 0..10
    primary_topic: str = ""
    paper_type: str = "method"  # method / theory / benchmark / empirical / survey / position
    novelty: int = 0  # 0..3
    novelty_signal: str = ""
    reason: str = ""
    suggested_lenses: list[str] = Field(default_factory=list)


class FulltextInfo(BaseModel):
    """全文缓存的位置和来源；正文本身存在 .cache/fulltext/，不进 JSONL 以免检查点过大。"""

    path: str
    loader: str
    grounding: str = "structure-grounded"  # page-grounded | structure-grounded | source-limited
    chars: int = 0


class ReadingNote(BaseModel):
    """精读阶段的产物。"""

    markdown: str  # base 框架各节的正文
    tldr: str = ""
    core_insight: str = ""
    value_score: int = 0  # 1..5，对你研究的价值
    worth_reading: str = ""
    lenses: list[str] = Field(default_factory=list)  # 实际使用的 lens
    model: str = ""
    grounding: str = ""
    schema_version: int = 0  # 生成时用的 prompts/schema.yaml 版本；0 = 旧笔记
    schema_ok: bool = True  # 标题、元信息是否通过 schema 校验
    sections: list[str] = Field(default_factory=list)  # 正文实际出现的二级标题（去编号）
    created_at: datetime = Field(default_factory=now_utc)


class Candidate(BaseModel):
    """流水线中的单个处理单元。"""

    paper: Paper
    keyword_hits: dict[str, list[str]] = Field(default_factory=dict)
    screen: ScreenResult | None = None
    score: float = 0.0  # 最终排序分
    score_parts: dict[str, float] = Field(default_factory=dict)
    selected: bool = False  # 是否进入全文精读
    fulltext: FulltextInfo | None = None
    note: ReadingNote | None = None
    note_path: str = ""  # 相对内容目录的路径，例如 papers/2026/10/2610.01234.md
    errors: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.paper.id

    def main_topics(self, threshold: float = 5.0, limit: int = 3) -> list[str]:
        """相关度达到阈值的方向，按分数降序。"""
        if self.screen and self.screen.topic_scores:
            ranked = sorted(self.screen.topic_scores.items(), key=lambda kv: -kv[1])
            hits = [t for t, s in ranked if s >= threshold][:limit]
            if hits:
                return hits
            if self.screen.primary_topic:
                return [self.screen.primary_topic]
        return list(self.keyword_hits)[:limit]


class RunInfo(BaseModel):
    """一次运行的上下文摘要，写入 .cache/runs/<date>/run.json。"""

    pipeline: str
    run_date: date
    started_at: datetime = Field(default_factory=now_utc)
    finished_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
