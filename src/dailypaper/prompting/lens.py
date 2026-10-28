"""Lens：叠加在 base 精读框架上的一段分析指引。

lens 的来源由适配器决定（registry kind = ``lens_adapter``）：
- ``markdown``       prompts/lenses/ 下的 md 文件
- ``topic``          topics.yaml 中的 lens_questions（每个方向自动生成 ``topic:<id>``）
- ``skill_manifest`` 带 manifest.yaml 的外部 skill（如 nature-paper-card）
- ``skill_markdown`` 只有 SKILL.md 的外部 skill（如 ccf-idea-reviewer）

新增来源 = 写一个 LensAdapter 子类并 ``@register("lens_adapter", "<name>")``。
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import AppConfig, LensSpec
from ..core.models import Paper, ScreenResult

log = logging.getLogger("dailypaper.lens")

_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_HEADING_RE = re.compile(r"^(#{1,6})\s+", re.M)


@dataclass
class LensContext:
    """选择和渲染 lens 时可用的论文信息。"""

    paper: Paper
    screen: ScreenResult | None = None
    topics: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def paper_type(self) -> str:
        return self.screen.paper_type if self.screen else "method"


@dataclass
class Lens:
    id: str
    title: str
    content: str
    priority: int = 50
    sections: list[int] = field(default_factory=list)
    source: str = ""


class LensAdapter(ABC):
    def __init__(self, cfg: AppConfig, spec: LensSpec) -> None:
        self.cfg = cfg
        self.spec = spec
        self.opts = spec.options

    def resolve_path(self) -> Path | None:
        """lens 文件/目录的位置；外部 skill 在仓库里没有副本时，本地回退到 ~/.claude/skills。"""
        if not self.spec.path:
            return None
        p = self.cfg.path(self.spec.path)
        if p.exists():
            return p
        fallback = Path(os.environ.get("DAILYPAPER_SKILLS_FALLBACK", Path.home() / ".claude" / "skills"))
        alt = fallback / Path(self.spec.path).name
        return alt if alt.exists() else None

    def available(self) -> bool:
        return self.resolve_path() is not None

    def title(self) -> str:
        return self.spec.title or self.spec.id

    def description(self) -> str:
        return self.spec.description

    @abstractmethod
    def render(self, lctx: LensContext) -> str:
        ...

    def build(self, lctx: LensContext) -> Lens | None:
        try:
            content = self.render(lctx).strip()
        except FileNotFoundError as e:
            log.warning("lens %s 不可用: %s", self.spec.id, e)
            return None
        if not content:
            return None
        if self.opts.get("strip_code", True):
            content = _CODE_FENCE_RE.sub("", content)
        # 外部内容的标题降到四级以下，避免打乱 base 框架的标题层级
        content = _HEADING_RE.sub(lambda m: "#" * max(4, len(m.group(1))) + " ", content)
        max_chars = int(self.opts.get("max_chars", 0) or 0)
        if max_chars and len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n…（截断）"
        return Lens(
            id=self.spec.id,
            title=self.title(),
            content=re.sub(r"\n{3,}", "\n\n", content),
            priority=self.spec.priority,
            sections=self.spec.sections,
            source=self.spec.adapter,
        )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_frontmatter(text: str) -> tuple[dict, str]:
    from ..core.io import split_frontmatter

    return split_frontmatter(text)
