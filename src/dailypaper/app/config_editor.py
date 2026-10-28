"""网页「搜索设置」的读写：topics.yaml 的 topics，以及 pipeline.yaml 中 daily 流水线的几个参数。

写回时用 ruamel.yaml 做 round-trip，保留文件里的注释和顺序；保存前先用 pydantic 校验，错误信息为中文。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..core.config import AppConfig, Topic, load_config

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class DailyParams(BaseModel):
    categories: list[str] = Field(min_length=1)
    max_candidates: int = Field(ge=5, le=1000)
    top_k: int = Field(ge=1, le=50)
    min_score: float = Field(ge=0, le=10)

    @field_validator("categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        out = []
        for c in v:
            c = c.strip()
            if not re.match(r"^[a-zA-Z-]+(\.[A-Za-z-]+)?$", c):
                raise ValueError(f"arXiv 分类格式不对：{c!r}（例如 cs.CL、stat.ML）")
            out.append(c)
        return out


class TopicIn(BaseModel):
    id: str
    name: str = Field(min_length=1)
    weight: float = Field(ge=0.1, le=5)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    lens_questions: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"方向 id {v!r} 只能用小写字母、数字和下划线，且以字母开头")
        return v

    @field_validator("keywords", "context", "exclude", "lens_questions")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


class ConfigBody(BaseModel):
    topics: list[TopicIn] = Field(min_length=1)
    daily: DailyParams

    @field_validator("topics")
    @classmethod
    def _unique(cls, v: list[TopicIn]) -> list[TopicIn]:
        ids = [t.id for t in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"方向 id 重复：{', '.join(sorted(dup))}")
        return v


def _stage(cfg: AppConfig, prefix: str):
    return next((s for s in cfg.pipeline.pipelines.get("daily", []) if s.use.startswith(prefix)), None)


def read_view(cfg: AppConfig) -> dict[str, Any]:
    src, kw, rk = _stage(cfg, "source."), _stage(cfg, "filter.keyword"), _stage(cfg, "ranker.llm_screen")
    daily = {
        "categories": list((src.params if src else {}).get("categories") or ["cs.CL", "cs.LG", "cs.AI", "cs.MA", "stat.ML"]),
        "max_candidates": int((kw.params if kw else {}).get("max_candidates", 80)),
        "top_k": int((rk.params if rk else {}).get("top_k", 5)),
        "min_score": float((rk.params if rk else {}).get("min_score", 6.0)),
    }
    files = [cfg.root / "config" / "topics.yaml", cfg.root / "config" / "pipeline.yaml"]
    editable = all(f.exists() and os.access(f, os.W_OK) for f in files)
    return {"editable": editable, "topics": [t.model_dump() for t in cfg.topics], "daily": daily}


def friendly_errors(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"] if x != "topics" or True)
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "；".join(parts)


def write_config(cfg: AppConfig, body: dict) -> list[str]:
    """校验并写回两个 YAML 文件；成功后让内存中的配置同步更新。返回提示信息。"""
    try:
        data = ConfigBody.model_validate(body)
    except ValidationError as e:
        raise ValueError(friendly_errors(e)) from e
    # 用正式的 Topic 模型再过一遍，保证与运行时一致
    topics = [Topic.model_validate(t.model_dump()) for t in data.topics]

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    cfg_dir = cfg.root / "config"

    topics_path = cfg_dir / "topics.yaml"
    doc = yaml.load(topics_path.read_text(encoding="utf-8")) or {}
    doc["topics"] = [_topic_node(t) for t in topics]
    _dump(yaml, doc, topics_path)

    pipe_path = cfg_dir / "pipeline.yaml"
    pdoc = yaml.load(pipe_path.read_text(encoding="utf-8")) or {}
    stages = (pdoc.get("pipelines") or {}).get("daily") or []
    for st in stages:
        use = str(st.get("use", ""))
        params = st.setdefault("with", {})
        if use.startswith("source."):
            params["categories"] = list(data.daily.categories)
        elif use == "filter.keyword":
            params["max_candidates"] = data.daily.max_candidates
        elif use == "ranker.llm_screen":
            params["top_k"] = data.daily.top_k
            params["min_score"] = data.daily.min_score
    _dump(yaml, pdoc, pipe_path)

    fresh = load_config(cfg.root)  # 重新加载以验证文件可读，并同步到内存
    cfg.topics = fresh.topics
    cfg.pipeline = fresh.pipeline
    return ["已保存 config/topics.yaml 与 config/pipeline.yaml，下次运行生效"]


def _topic_node(t: Topic) -> dict:
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.scalarstring import FoldedScalarString

    node = CommentedMap()
    node["id"] = t.id
    node["name"] = t.name
    node["weight"] = t.weight
    node["description"] = FoldedScalarString(" ".join(t.description.split()))
    node["keywords"] = list(t.keywords)
    node["context"] = list(t.context)
    node["exclude"] = list(t.exclude)
    node["lens_questions"] = list(t.lens_questions)
    return node


def _dump(yaml, doc, path: Path) -> None:
    from io import StringIO

    buf = StringIO()
    yaml.dump(doc, buf)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)
