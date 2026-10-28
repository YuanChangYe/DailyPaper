"""外部阅读 skill 适配器：把 Claude skill 的分析指引转成 lens。

skill 原本是给 agent 用的（会读文件、跑脚本、按流程交互）。这里只抽取其中"怎么分析"的文本，
不注入脚本、工具调用和输出文件约定；输出结构始终由 base 框架决定。

``skill_manifest``（适合 nature-paper-card 这类带 manifest.yaml 的 skill）options：
    include_always_load: true          读取 manifest.always_load 中的文件
    exclude: [static/core/workflow.md] 排除的文件（相对 skill 目录）
    axis: paper_type                   用论文类型选择 manifest.axes 中的片段
    axis_map: {method: methods, ...}   本项目的 paper_type → skill 的 axis 取值
    references: [references/xx.md]     额外加入的参考文件（manifest 的 on_demand 条件是自然语言，由配置决定）

``skill_markdown``（适合只有 SKILL.md 的 skill）options：
    sections: ["Core Rule"]            只保留这些二级标题下的内容；省略则保留全文
    references: [references/rubric.md] 额外加入的参考文件

两者共用：max_chars（单个 lens 上限）、strip_code（去掉代码块，默认 true）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from ...core.registry import register
from ..lens import LensAdapter, LensContext, read_text, strip_frontmatter

log = logging.getLogger("dailypaper.lens.skill")

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _section_blocks(body: str) -> dict[str, str]:
    """按二级标题切分 Markdown，返回 {标题: 内容}。"""
    out: dict[str, str] = {}
    matches = list(_H2_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[m.group(1).strip()] = body[m.end() : end].strip()
    return out


class _SkillBase(LensAdapter):
    def skill_dir(self) -> Path:
        p = self.resolve_path()
        if p is None:
            raise FileNotFoundError(f"找不到 skill 目录 {self.spec.path}（运行 dailypaper sync-skills 同步）")
        return p

    def _skill_meta(self) -> dict:
        try:
            fm, _ = strip_frontmatter(read_text(self.skill_dir() / "SKILL.md"))
            return fm
        except FileNotFoundError:
            return {}

    def title(self) -> str:
        return self.spec.title or f"Skill · {self._skill_meta().get('name', self.spec.id)}"

    def description(self) -> str:
        return self.spec.description or str(self._skill_meta().get("description", ""))[:300]

    def _resolve(self, rel: str) -> Path:
        """skill 内的相对路径；``../other-skill/x.md`` 解析到同级 skill 目录。"""
        return (self.skill_dir() / rel).resolve()

    def _files_text(self, rels: list[str]) -> list[str]:
        parts = []
        for rel in rels:
            path = self._resolve(rel)
            if not path.exists():
                log.warning("skill %s 缺少文件 %s，已跳过", self.spec.id, rel)
                continue
            _, body = strip_frontmatter(read_text(path))
            parts.append(f"#### {path.stem}\n{body.strip()}")
        return parts


@register("lens_adapter", "skill_manifest")
class SkillManifestLens(_SkillBase):
    def render(self, lctx: LensContext) -> str:
        manifest_path = self.skill_dir() / "manifest.yaml"
        manifest = yaml.safe_load(read_text(manifest_path)) or {}
        exclude = set(self.opts.get("exclude", []))
        files: list[str] = []
        if self.opts.get("include_always_load", True):
            files += [f for f in manifest.get("always_load", []) if f not in exclude]

        axis_name = self.opts.get("axis", "paper_type")
        axis = (manifest.get("axes") or {}).get(axis_name)
        if axis:
            value = (self.opts.get("axis_map") or {}).get(lctx.paper_type, lctx.paper_type)
            values = axis.get("values", {})
            frag = values.get(value) or values.get(axis.get("default", ""))
            if frag:
                files.append(frag)

        files += [f for f in self.opts.get("references", []) if f not in files]
        return "\n\n".join(self._files_text(files))


@register("lens_adapter", "skill_markdown")
class SkillMarkdownLens(_SkillBase):
    def render(self, lctx: LensContext) -> str:
        _, body = strip_frontmatter(read_text(self.skill_dir() / "SKILL.md"))
        wanted = self.opts.get("sections")
        if wanted:
            blocks = _section_blocks(body)
            picked = [f"#### {h}\n{blocks[h]}" for h in wanted if h in blocks]
            missing = [h for h in wanted if h not in blocks]
            if missing:
                log.warning("skill %s 中没有这些小节: %s", self.spec.id, missing)
            body = "\n\n".join(picked)
        parts = [body.strip()] + self._files_text(self.opts.get("references", []))
        return "\n\n".join(p for p in parts if p)
