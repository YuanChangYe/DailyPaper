"""阅读趋势：按日 / 按方向统计，以及用 LLM 生成一段时间的阅读总结（缓存在 data/trends/）。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ..core.config import AppConfig
from ..core.context import RunContext
from ..core.io import read_markdown, write_text
from ..llm import get_llm
from .site.contract import IndexEntry, SiteContract


def window(cfg: AppConfig, days: int, end: date | None = None) -> tuple[date, date, list[IndexEntry]]:
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    entries = [e for e in SiteContract(cfg).load_index() if start.isoformat() <= e.date <= end.isoformat()]
    return start, end, entries


def stats(cfg: AppConfig, days: int = 7, end: date | None = None) -> dict:
    start, end, entries = window(cfg, days, end)
    per_day: dict[str, int] = defaultdict(int)
    per_topic: Counter = Counter()
    per_type: Counter = Counter()
    values: Counter = Counter()
    for e in entries:
        per_day[e.date] += 1
        per_topic.update(e.topics)
        if e.paper_type:
            per_type[e.paper_type] += 1
        values[e.value_score] += 1
    day_series = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    topics = {t.id: t.name for t in cfg.topics}
    top = sorted(entries, key=lambda e: (e.value_score, e.score), reverse=True)[:5]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "total": len(entries),
        "per_day": [{"date": d, "count": per_day.get(d, 0)} for d in day_series],
        "per_topic": [{"id": t, "name": topics.get(t, t), "count": n} for t, n in per_topic.most_common()],
        "per_type": [{"type": t, "count": n} for t, n in per_type.most_common()],
        "values": {str(k): v for k, v in sorted(values.items())},
        "top": [e.model_dump() for e in top],
        "avg_value": round(sum(e.value_score for e in entries) / len(entries), 2) if entries else 0,
    }


def summary_path(cfg: AppConfig, start: date, end: date):
    return cfg.data_dir / "trends" / f"{start.isoformat()}_{end.isoformat()}.md"


def cached_summary(cfg: AppConfig, days: int = 7, end: date | None = None) -> str | None:
    start, end, _ = window(cfg, days, end)
    p = summary_path(cfg, start, end)
    return p.read_text(encoding="utf-8") if p.exists() else None


SYSTEM = """你是研究者的阅读助理。根据一段时间内精读过的论文列表（标题、方向、价值评分、TL;DR、核心 insight），写一份简短的阅读趋势总结，
用中文，Markdown 格式，300–500 字，分四个小标题：
## 这段时间读了什么（按方向归纳，不要逐篇罗列）
## 共同的趋势与分歧（方法、问题设定、评测上的共性，以及互相矛盾的结论）
## 最值得回看的 2–3 篇（标题 + 一句理由）
## 下一步可以追的问题（2–3 条，具体可做）
只依据给定内容，不要编造。术语保留英文。"""


def generate_summary(ctx: RunContext, days: int = 7, end: date | None = None, force: bool = False) -> str:
    cfg = ctx.config
    start, end, entries = window(cfg, days, end)
    path = summary_path(cfg, start, end)
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")
    if not entries:
        raise ValueError("这段时间没有精读过的论文")
    lines = []
    for e in sorted(entries, key=lambda e: (e.date, -e.value_score)):
        extra = ""
        note = cfg.content_dir / e.note_path if e.note_path else None
        if note and note.exists():
            _, body = read_markdown(note)
            for sec in ("## 9.", "## 7."):
                if sec in body:
                    extra = body.split(sec, 1)[1].split("\n## ", 1)[0][:600]
                    break
        topics = ", ".join(e.topics)
        lines.append(f"- [{e.date}] {e.title}（方向: {topics}；类型: {e.paper_type}；价值: {e.value_score}/5）\n  TL;DR: {e.tldr}\n  Insight: {e.core_insight}\n  {extra.strip()}")
    user = f"时间范围：{start} 至 {end}，共 {len(entries)} 篇。\n\n" + "\n".join(lines)
    text = get_llm(ctx, "screen").complete(SYSTEM, user, tag="trends").text.strip()
    header = f"<!-- generated {date.today().isoformat()} for {start}..{end} -->\n"
    write_text(path, header + text + "\n")
    return header + text + "\n"
