"""把 base 框架模板、选中的 lens 和论文内容组装成最终 prompt。

模板在 ``prompts/base/``（Jinja2），可以直接编辑而不改代码：
- ``<name>.system.md.j2``  系统提示（任务、输出结构、lens 指引）
- ``<name>.user.md.j2``    用户消息（论文内容）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..core.config import AppConfig
from .lens import Lens

log = logging.getLogger("dailypaper.composer")


@dataclass
class ComposedPrompt:
    system: str
    user: str
    lenses: list[str]
    dropped_lenses: list[str]


class PromptComposer:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.env = Environment(
            loader=FileSystemLoader(str(cfg.path("prompts"))),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def fit_budget(self, lenses: list[Lens], budget: int) -> tuple[list[Lens], list[str]]:
        """按优先级装入 lens：超出预算时截断最后一个（剩余空间足够时）或整体丢弃。"""
        kept: list[Lens] = []
        dropped: list[str] = []
        used = 0
        for lens in sorted(lenses, key=lambda x: -x.priority):
            size = len(lens.content)
            if used + size <= budget:
                kept.append(lens)
                used += size
                continue
            room = budget - used
            if room >= 1500:
                cut = lens.content[:room].rsplit("\n", 1)[0] + "\n…（因篇幅截断）"
                kept.append(Lens(lens.id, lens.title, cut, lens.priority, lens.sections, lens.source))
                used = budget
            else:
                dropped.append(lens.id)
        if dropped:
            log.info("lens 超出预算被丢弃: %s", dropped)
        return kept, dropped

    def render(self, name: str, **variables) -> tuple[str, str]:
        system = self.env.get_template(f"base/{name}.system.md.j2").render(**variables)
        user = self.env.get_template(f"base/{name}.user.md.j2").render(**variables)
        return system.strip(), user.strip()

    def compose(self, name: str, lenses: list[Lens], **variables) -> ComposedPrompt:
        kept, dropped = self.fit_budget(lenses, self.cfg.lenses.budget_chars)
        variables.setdefault("schema", self.cfg.note_schema)
        variables.setdefault("include_deep", True)
        variables.setdefault("web_search", False)
        system, user = self.render(
            name,
            lenses=kept,
            topics=self.cfg.topics,
            language=self.cfg.settings.language,
            **variables,
        )
        return ComposedPrompt(system=system, user=user, lenses=[x.id for x in kept], dropped_lenses=dropped)
