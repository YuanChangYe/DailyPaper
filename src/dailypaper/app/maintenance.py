"""网页「数据与隐私」用的本机操作：目录占用与清理、在资源管理器中打开目录、导出批注与笔记。

只动缓存目录（.cache/ 下的几个子目录），不动 data/（审核结果、批注、用量）和笔记。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from ..core.config import AppConfig
from ..core.io import read_json, read_markdown
from ..core.models import file_id

# kind → (说明, 相对 cache_dir 的子目录)
CACHE_KINDS: dict[str, tuple[str, str]] = {
    "llm": ("模型调用缓存（相同 prompt 不再花钱；清除后重跑会重新计费）", "llm"),
    "fulltext": ("论文全文文本（精读时重新下载）", "fulltext"),
    "pdf": ("论文 PDF（分栏阅读与缩略图用，按需重新下载）", "pdf"),
    "runs": ("运行检查点（中断后可以接着跑；清除后旧的运行不能续跑）", "runs"),
    "prompts": ("只生成提示词时的输出（调试用）", "prompts"),
}

OPENABLE = {"workspace", "content", "data", "cache", "config", "covers"}


def dir_size(path: Path) -> tuple[int, int]:
    """(字节数, 文件数)。目录不存在时为 (0, 0)。"""
    total = files = 0
    if not path.exists():
        return 0, 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def storage_view(cfg: AppConfig) -> dict:
    items = []
    for kind, (label, sub) in CACHE_KINDS.items():
        p = cfg.cache_dir / sub
        b, n = dir_size(p)
        items.append({"kind": kind, "label": label, "path": str(p), "bytes": b, "files": n})
    notes_b, notes_n = dir_size(cfg.content_dir / "papers")
    anno_b, anno_n = dir_size(cfg.data_dir / "annotations")
    return {
        "workspace": str(cfg.content_dir.parent),
        "dirs": {"content": str(cfg.content_dir), "data": str(cfg.data_dir), "cache": str(cfg.cache_dir), "config": str(cfg.root / "config")},
        "cache": items,
        "notes": {"bytes": notes_b, "files": notes_n},
        "annotations": {"bytes": anno_b, "files": anno_n},
    }


def clear_cache(cfg: AppConfig, kind: str) -> dict:
    if kind not in CACHE_KINDS:
        raise ValueError(f"未知的缓存类型 {kind!r}")
    p = cfg.cache_dir / CACHE_KINDS[kind][1]
    before, n = dir_size(p)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return {"kind": kind, "freed": before, "files": n}


def open_folder(cfg: AppConfig, which: str) -> str:
    if which not in OPENABLE:
        raise ValueError(f"不能打开 {which!r}")
    path = {
        "workspace": cfg.content_dir.parent,
        "content": cfg.content_dir,
        "data": cfg.data_dir,
        "cache": cfg.cache_dir,
        "config": cfg.root / "config",
        "covers": cfg.content_dir.parent / "covers",
    }[which]
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - 本机打开目录
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return str(path)


def _annotation_files(cfg: AppConfig) -> list[Path]:
    d = cfg.data_dir / "annotations"
    return sorted(d.glob("*.json")) if d.exists() else []


def export_annotations(cfg: AppConfig) -> dict:
    """所有论文的高亮与批注，附标题和笔记路径。"""
    from ..integrations.site.contract import SiteContract

    index = {e.id: e for e in SiteContract(cfg).load_index()}
    papers = []
    for p in _annotation_files(cfg):
        data = read_json(p, {}) or {}
        pid = p.stem
        e = index.get(pid) or index.get(pid.replace("_", "/"))
        papers.append({
            "id": e.id if e else pid,
            "title": e.title if e else "",
            "note_path": e.note_path if e else "",
            "url_abs": e.url_abs if e else "",
            "highlights": data.get("highlights", []),
            "note": data.get("note", ""),
            "updated_at": data.get("updated_at", ""),
        })
    return {"exported_from": str(cfg.data_dir / "annotations"), "papers": papers}


def _annotation_section(data: dict) -> str:
    hls = data.get("highlights", [])
    note = (data.get("note") or "").strip()
    if not hls and not note:
        return ""
    lines = ["", "---", "", "## 我的高亮与批注", ""]
    for h in hls:
        sec = f"（{h['section']}）" if h.get("section") else ""
        lines.append(f"- {h.get('color', 'yellow')} · {sec}「{h.get('text', '').strip()}」" + (f" — {h['comment'].strip()}" if h.get("comment") else ""))
    if note:
        lines += ["", "### 笔记", "", note]
    return "\n".join(lines) + "\n"


def export_notes_zip(cfg: AppConfig) -> bytes:
    """所有精读笔记（Markdown）打包，带批注的在末尾附上高亮与批注；另附 annotations.json。"""
    annos = {p.stem: read_json(p, {}) or {} for p in _annotation_files(cfg)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        papers_dir = cfg.content_dir / "papers"
        for md in sorted(papers_dir.rglob("*.md")) if papers_dir.exists() else []:
            fm, body = read_markdown(md)
            pid = file_id(str(fm.get("paper_id") or md.stem))
            text = md.read_text(encoding="utf-8")
            extra = _annotation_section(annos.get(pid, {}))
            z.writestr(md.relative_to(cfg.content_dir).as_posix(), text.rstrip("\n") + "\n" + extra)
        z.writestr("annotations.json", json.dumps(export_annotations(cfg), ensure_ascii=False, indent=2))
    return buf.getvalue()
