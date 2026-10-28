"""自带的两类 lens：Markdown 文件 lens 与研究方向 lens。"""

from __future__ import annotations

from ...core.registry import register
from ..lens import LensAdapter, LensContext, read_text, strip_frontmatter


@register("lens_adapter", "markdown")
class MarkdownLens(LensAdapter):
    """prompts/lenses/*.md。文件可带 frontmatter（title / description），正文即指引内容。"""

    def _load(self) -> tuple[dict, str]:
        path = self.resolve_path()
        if path is None:
            raise FileNotFoundError(self.spec.path)
        return strip_frontmatter(read_text(path))

    def title(self) -> str:
        if self.spec.title:
            return self.spec.title
        try:
            return self._load()[0].get("title", self.spec.id)
        except FileNotFoundError:
            return self.spec.id

    def description(self) -> str:
        if self.spec.description:
            return self.spec.description
        try:
            return self._load()[0].get("description", "")
        except FileNotFoundError:
            return ""

    def render(self, lctx: LensContext) -> str:
        return self._load()[1]


@register("lens_adapter", "topic")
class TopicLens(LensAdapter):
    """由 topics.yaml 的 lens_questions 生成；options.topic 指定方向 id。"""

    def _topic(self):
        return self.cfg.topic(self.opts.get("topic", ""))

    def available(self) -> bool:
        t = self._topic()
        return bool(t and t.lens_questions)

    def title(self) -> str:
        t = self._topic()
        return f"研究方向 · {t.name}" if t else self.spec.id

    def description(self) -> str:
        t = self._topic()
        return f"{t.name} 方向的镜头问题" if t else ""

    def render(self, lctx: LensContext) -> str:
        t = self._topic()
        if not t:
            return ""
        qs = "\n".join(f"- {q}" for q in t.lens_questions)
        return f"这篇论文与「{t.name}」相关。精读时逐条回答下列问题，并把答案融入对应小节：\n{qs}"
