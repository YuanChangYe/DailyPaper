"""全文精读：base 框架 + 自适应 lens → LLM → ReadingNote。

``ctx.inputs["prompt_only"]`` 为真时只把组装好的 prompt 写到 .cache/prompts/，不调用模型
（``dailypaper read <id> --prompt-only``，用于调 prompt 和 lens）。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from ...core.interfaces import Reader
from ...core.io import write_text
from ...core.models import Candidate, ReadingNote
from ...core.registry import register
from ...llm import get_llm
from ...llm.client import extract_json
from ...prompting.composer import PromptComposer
from ...prompting.selector import LensCatalog, select_lenses

_META_RE = re.compile(r"^\s*```json\s*(\{.*?\})\s*```\s*", re.S)


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


@register("reader", "deep_read")
class DeepReader(Reader):
    class Params(BaseModel):
        template: str = "deep_read"
        workers: int = 2

    def _read_one(self, c: Candidate, composer: PromptComposer, catalog: LensCatalog, llm) -> None:
        fulltext = Path(c.fulltext.path).read_text(encoding="utf-8") if c.fulltext else f"[Abstract]\n{c.paper.abstract}"
        lenses = select_lenses(catalog, c)
        prompt = composer.compose(
            self.p.template,
            lenses,
            paper=c.paper,
            screen=c.screen,
            topics_hit=c.main_topics(),
            fulltext=fulltext,
            fulltext_loader=c.fulltext.loader if c.fulltext else "abstract",
            grounding=c.fulltext.grounding if c.fulltext else "source-limited",
        )
        if self.ctx.inputs.get("prompt_only"):
            path = self.config.cache_dir / "prompts" / f"{c.paper.slug}.md"
            write_text(path, f"<!-- lenses: {prompt.lenses} -->\n# SYSTEM\n\n{prompt.system}\n\n# USER\n\n{prompt.user}\n")
            self.log.info("prompt 已写入 %s（lens: %s）", path, ", ".join(prompt.lenses))
            return
        resp = llm.complete(prompt.system, prompt.user, tag="read")
        meta, body = parse_note(resp.text)
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
        )
        if resp.stop_reason in ("max_tokens", "length"):
            c.errors.append("deep_read: 输出达到 max_tokens 上限，笔记可能不完整")

    def run(self, items: list[Candidate]) -> list[Candidate]:
        todo = self.selected(items)
        if not todo:
            return items
        composer = PromptComposer(self.config)
        catalog = LensCatalog(self.config)
        llm = None if self.ctx.inputs.get("prompt_only") else get_llm(self.ctx, "read")

        def work(c: Candidate) -> None:
            try:
                self._read_one(c, composer, catalog, llm)
            except Exception as e:  # noqa: BLE001 - 单篇失败不影响其他论文
                self.log.error("精读 %s 失败: %s", c.id, e)
                c.errors.append(f"deep_read: {e}")

        with ThreadPoolExecutor(max_workers=max(1, self.p.workers)) as pool:
            list(pool.map(work, todo))
        return items
