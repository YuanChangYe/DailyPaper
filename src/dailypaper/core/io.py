"""文件读写小工具：原子写入 JSON/文本、Markdown frontmatter 读写、JSONL 检查点。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def write_jsonl(path: Path, models: Iterable[BaseModel]) -> None:
    lines = [m.model_dump_json() for m in models]
    _atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))


def read_jsonl(path: Path, model: type[BaseModel]) -> list:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(model.model_validate_json(line))
    return out


# ---------------------------------------------------------------- frontmatter

_FM_DELIM = "---"


def split_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, 正文)。没有 frontmatter 时返回空 dict。"""
    if not text.startswith(_FM_DELIM):
        return {}, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIM:
            fm = yaml.safe_load("\n".join(lines[1:i])) or {}
            body = "\n".join(lines[i + 1 :])
            return (fm if isinstance(fm, dict) else {}), body.lstrip("\n")
    return {}, text


def join_frontmatter(fm: dict, body: str) -> str:
    dumped = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=1000).strip()
    return f"{_FM_DELIM}\n{dumped}\n{_FM_DELIM}\n\n{body.lstrip()}"


def read_markdown(path: Path) -> tuple[dict, str]:
    return split_frontmatter(path.read_text(encoding="utf-8"))


def update_frontmatter(path: Path, **fields: Any) -> None:
    """只改 frontmatter 中给定的字段，正文原样保留（用于 citekey 回写等）。"""
    fm, body = read_markdown(path)
    fm.update(fields)
    write_text(path, join_frontmatter(fm, body))
