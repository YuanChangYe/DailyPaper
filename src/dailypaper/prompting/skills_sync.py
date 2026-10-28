"""把本机的 Claude skill 复制到仓库的 skills/ 目录，供 GitHub Actions 中的精读使用。

只复制文本类文件（md / yaml / json / txt），不复制脚本和测试；
manifest.yaml 中引用的同级 skill 文件（如 ../nature-shared/core/x.md）会一并复制。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import yaml

log = logging.getLogger("dailypaper.skills")

TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt"}
SKIP_DIRS = {"scripts", "tests", "evals", "agents", "__pycache__", ".git"}


def _copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if f.is_dir() or f.suffix.lower() not in TEXT_SUFFIXES or set(rel.parts) & SKIP_DIRS:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


def _sibling_refs(skill_dir: Path) -> list[str]:
    manifest = skill_dir / "manifest.yaml"
    if not manifest.exists():
        return []
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    refs = list(data.get("always_load", []))
    return [r for r in refs if r.startswith("../")]


def sync_skills(source_root: Path, dest_root: Path, names: list[str]) -> dict[str, int]:
    """返回 {skill 名: 复制的文件数}。"""
    result: dict[str, int] = {}
    for name in names:
        src = source_root / name
        if not (src / "SKILL.md").exists():
            log.warning("跳过 %s：%s 下没有 SKILL.md", name, src)
            continue
        dst = dest_root / name
        if dst.exists():
            shutil.rmtree(dst)
        result[name] = _copy_tree(src, dst)
        for ref in _sibling_refs(src):
            ref_src = (src / ref).resolve()
            ref_dst = (dst / ref).resolve()
            if ref_src.exists():
                ref_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ref_src, ref_dst)
                result[name] += 1
            else:
                log.warning("%s 引用的 %s 不存在", name, ref)
    return result
