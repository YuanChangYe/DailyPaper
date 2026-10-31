"""全文精读：base 框架 + 自适应 lens → LLM → ReadingNote。

笔记结构来自 prompts/schema.yaml（``cfg.note_schema``）：提示词按它生成各节标题，模型输出后再按它校验
标题是否齐全、顺序是否正确、JSON 元信息是否完整；问题记入 ``Candidate.errors`` 并写进笔记 frontmatter。

``ctx.inputs["prompt_only"]`` 为真时只把组装好的 prompt 写到 .cache/prompts/，不调用模型
（``dailypaper read <id> --prompt-only``，用于调 prompt 和 lens）。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel

from ...core.config import NoteSchema
from ...core.interfaces import Reader
from ...core.io import write_text
from ...core.models import Candidate, ReadingNote
from ...core.registry import register
from ...integrations.research_profile import load_profile_text
from ...llm import get_llm
from ...llm.client import extract_json
from ...prompting.composer import PromptComposer
from ...prompting.selector import LensCatalog, select_lenses

_META_RE = re.compile(r"^\s*```json\s*(\{.*?\})\s*```\s*", re.S)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_NUM_PREFIX_RE = re.compile(r"^\s*\d+\s*[.、．)]\s*")


def parse_note(text: str) -> tuple[dict, str]:
    """拆出开头的 ```json 元信息块和后面的 Markdown 正文。"""
    m = _META_RE.match(text)
    if m:
        try:
            return extract_json(m.group(0)), text[m.end():].strip()
        except Exception:  # noqa: BLE001
            pass
    try:
        return extract_json(text.split("\n## ", 1)[0]), re.sub(r"^.*?(?=^## )", "", text, count=1, flags=re.S | re.M)
    except Exception:  # noqa: BLE001
        return {}, text.strip()


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", "", _NUM_PREFIX_RE.sub("", s)).lower()


def _same_title(a: str, b: str) -> bool:
    """标题是否一致：允许个别字符差异（模型偶尔写繁体字或漏一个字）。"""
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.8


def note_headings(markdown: str) -> list[str]:
    """正文里的二级标题（去掉编号）。"""
    return [_NUM_PREFIX_RE.sub("", h).strip() for h in _H2_RE.findall(markdown)]


def validate_note(markdown: str, meta: dict, schema: NoteSchema, include_deep: bool = True) -> list[str]:
    """按 schema 检查笔记：必需标题齐全且顺序正确、必需元信息字段存在且类型合法。返回问题列表（空 = 合格）。"""
    problems: list[str] = []
    for m in schema.meta:
        if not m.required:
            continue
        v = meta.get(m.key)
        if v in (None, ""):
            problems.append(f"元信息缺少 {m.key}（{m.label}）")
        elif m.type == "int":
            try:
                iv = int(v)
            except (TypeError, ValueError):
                problems.append(f"元信息 {m.key} 不是整数：{v!r}")
            else:
                if (m.min is not None and iv < m.min) or (m.max is not None and iv > m.max):
                    problems.append(f"元信息 {m.key} 超出范围 {m.min}–{m.max}：{iv}")
    found = [_norm_title(h) for h in note_headings(markdown)]
    expected = [(n, s) for n, s in schema.numbered(include_deep) if s.required]
    positions: list[int] = []
    for n, s in expected:
        key = _norm_title(s.title)
        idx = next((i for i, h in enumerate(found) if _same_title(h, key)), -1)
        if idx < 0:
            problems.append(f"缺少第 {n} 节「{s.title}」")
        else:
            positions.append(idx)
    if positions != sorted(positions):
        problems.append("小节顺序与框架不一致")
    return problems


@register("reader", "deep_read")
class DeepReader(Reader):
    class Params(BaseModel):
        template: str = "deep_read"
        workers: int = 2
        deep_min_score: float = 0.0  # 排序分低于此值的论文只生成非 deep 的小节（省钱）；0 = 全部生成

    def _read_one(self, c: Candidate, composer: PromptComposer, catalog: LensCatalog, llm) -> None:
        fulltext = Path(c.fulltext.path).read_text(encoding="utf-8") if c.fulltext else f"[Abstract]\n{c.paper.abstract}"
        lenses = select_lenses(catalog, c)
        include_deep = not self.p.deep_min_score or c.score >= self.p.deep_min_score
        quick = bool(self.ctx.inputs.get("quick"))
        if quick:  # 快速概览：只写非 deep 的小节，只给前 3 万字（摘要、引言、方法），用便宜的模型
            include_deep = False
            fulltext = fulltext[:30000] + ("\n\n[……快速概览只读了前面部分……]" if len(fulltext) > 30000 else "")
            lenses = []
        role = self.config.llm.roles.get("read")
        provider = self.config.llm.providers.get(role.provider) if role else None
        web_search = bool(role and role.web_search and provider and provider.type == "anthropic") and not self.ctx.provider_override
        prompt = composer.compose(
            self.p.template,
            lenses,
            paper=c.paper,
            screen=c.screen,
            topics_hit=c.main_topics(),
            fulltext=fulltext,
            fulltext_loader=c.fulltext.loader if c.fulltext else "abstract",
            grounding=c.fulltext.grounding if c.fulltext else "source-limited",
            include_deep=include_deep,
            web_search=web_search and not quick,
            profile=load_profile_text(self.config),
        )
        if self.ctx.inputs.get("prompt_only"):
            path = self.config.cache_dir / "prompts" / f"{c.paper.slug}.md"
            write_text(path, f"<!-- lenses: {prompt.lenses} -->\n# SYSTEM\n\n{prompt.system}\n\n# USER\n\n{prompt.user}\n")
            self.log.info("prompt 已写入 %s（lens: %s）", path, ", ".join(prompt.lenses))
            return
        resp = llm.complete(prompt.system, prompt.user, tag="read")
        meta, body = parse_note(resp.text)
        schema = self.config.note_schema
        problems = validate_note(body, meta, schema, include_deep)
        try:
            value = int(meta.get("value_score") or 0)
        except (TypeError, ValueError):
            value = 0
        c.note = ReadingNote(
            markdown=body,
            tldr=str(meta.get("tldr", "")),
            core_insight=str(meta.get("core_insight", "")),
            value_score=max(0, min(5, value)),
            worth_reading=str(meta.get("worth_reading", "")),
            lenses=prompt.lenses,
            model=llm.model,
            grounding=c.fulltext.grounding if c.fulltext else "source-limited",
            schema_version=schema.version,
            schema_ok=not problems,
            sections=note_headings(body),
            depth="quick" if quick else "full",
        )
        if resp.stop_reason in ("max_tokens", "length"):
            c.errors.append("deep_read: 输出达到 max_tokens 上限，笔记可能不完整")
        for p in problems:
            self.log.warning("精读 %s 结构不合格: %s", c.id, p)
            c.errors.append(f"deep_read: {p}")

    def run(self, items: list[Candidate]) -> list[Candidate]:
        todo = self.selected(items)
        if not todo:
            return items
        composer = PromptComposer(self.config)
        catalog = LensCatalog(self.config)
        quick_role = "ask" if "ask" in self.config.llm.roles else "screen"
        role = quick_role if self.ctx.inputs.get("quick") else "read"
        llm = None if self.ctx.inputs.get("prompt_only") else get_llm(self.ctx, role)

        def work(c: Candidate) -> None:
            try:
                self._read_one(c, composer, catalog, llm)
            except Exception as e:  # noqa: BLE001 - 单篇失败不影响其他论文
                self.log.error("精读 %s 失败: %s", c.id, e)
                c.errors.append(f"deep_read: {e}")

        with ThreadPoolExecutor(max_workers=max(1, self.p.workers)) as pool:
            list(pool.map(work, todo))
        return items
