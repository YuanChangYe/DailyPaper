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
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    cfg_dir = cfg.root / "config"

    topics_path = cfg_dir / "topics.yaml"
    doc = yaml.load(topics_path.read_text(encoding="utf-8")) or {}
    doc["topics"] = [_topic_node(t) for t in topics]
    _dump(yaml, doc, topics_path)

    pipe_path = cfg_dir / "pipeline.yaml"
    pyaml = YAML()
    pyaml.preserve_quotes = True
    pyaml.width = 160
    pyaml.indent(mapping=2, sequence=4, offset=2)
    pdoc = pyaml.load(pipe_path.read_text(encoding="utf-8")) or {}
    stages = (pdoc.get("pipelines") or {}).get("daily") or []
    for st in stages:
        use = str(st.get("use", ""))
        if not (use.startswith("source.") or use in ("filter.keyword", "ranker.llm_screen")):
            continue  # 其他 stage 原样保留，不加空的 with
        params = st.setdefault("with", CommentedMap())
        if use.startswith("source."):
            params["categories"] = _flow(list(data.daily.categories))
        elif use == "filter.keyword":
            params["max_candidates"] = data.daily.max_candidates
        elif use == "ranker.llm_screen":
            params["top_k"] = data.daily.top_k
            params["min_score"] = data.daily.min_score
    _dump(pyaml, pdoc, pipe_path)

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


def _flow(items: list):
    """行内写法的列表（[a, b, c]），与手写的配置文件保持一致。"""
    from ruamel.yaml.comments import CommentedSeq

    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def _dump(yaml, doc, path: Path) -> None:
    from io import StringIO

    buf = StringIO()
    yaml.dump(doc, buf)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 运行与费用（llm.yaml）

EFFORTS = ("low", "medium", "high", "xhigh", "max")


class RoleIn(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str | None = None
    max_tokens: int = Field(ge=256, le=200_000)
    web_search: bool = False
    web_search_max_uses: int = Field(default=8, ge=1, le=30)

    @field_validator("effort")
    @classmethod
    def _effort(cls, v: str | None) -> str | None:
        v = (v or "").strip() or None
        if v and v not in EFFORTS:
            raise ValueError(f"effort 只能是 {', '.join(EFFORTS)} 或留空")
        return v


class LLMBody(BaseModel):
    roles: dict[str, RoleIn]
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    budget_warn_ratio: float = Field(default=0.8, ge=0.1, le=1.0)
    pricing: dict[str, list[float]] = Field(default_factory=dict)
    cache: bool = True

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: dict[str, RoleIn]) -> dict[str, RoleIn]:
        missing = {"screen", "read"} - set(v)
        if missing:
            raise ValueError(f"缺少角色 {', '.join(sorted(missing))}")
        return v

    @field_validator("pricing")
    @classmethod
    def _pricing(cls, v: dict[str, list[float]]) -> dict[str, list[float]]:
        out = {}
        for model, price in v.items():
            model = model.strip()
            if not model:
                continue
            if len(price) != 2 or any(p < 0 for p in price):
                raise ValueError(f"价格 {model} 需要两个非负数：[输入, 输出] 美元 / 百万 token")
            out[model] = [float(price[0]), float(price[1])]
        return out


def read_llm_view(cfg: AppConfig) -> dict[str, Any]:
    """网页「运行与费用」用：provider 列表（只报告 key 是否存在，不回显）、角色、预算、价格。"""
    providers = []
    for name, spec in cfg.llm.providers.items():
        env = spec.api_key_env
        key_set = bool(env and os.environ.get(env)) or (spec.type == "anthropic" and bool(os.environ.get("ANTHROPIC_AUTH_TOKEN")))
        providers.append({"name": name, "type": spec.type, "key_env": env, "key_set": key_set if env else None, "base_url": spec.base_url})
    roles = {
        name: {"provider": r.provider, "model": r.model, "effort": r.effort, "max_tokens": r.max_tokens,
               "web_search": r.web_search, "web_search_max_uses": r.web_search_max_uses}
        for name, r in cfg.llm.roles.items()
    }
    path = cfg.root / "config" / "llm.yaml"
    return {
        "editable": path.exists() and os.access(path, os.W_OK),
        "providers": providers,
        "roles": roles,
        "monthly_budget_usd": cfg.llm.monthly_budget_usd,
        "budget_warn_ratio": cfg.llm.budget_warn_ratio,
        "pricing": cfg.llm.pricing,
        "cache": cfg.llm.cache,
        "efforts": list(EFFORTS),
    }


def write_llm_config(cfg: AppConfig, body: dict) -> list[str]:
    try:
        data = LLMBody.model_validate(body)
    except ValidationError as e:
        raise ValueError(friendly_errors(e)) from e
    for name, role in data.roles.items():
        if role.provider not in cfg.llm.providers:
            raise ValueError(f"角色 {name} 使用的 provider {role.provider!r} 不存在（可选：{', '.join(cfg.llm.providers)}）")

    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    path = cfg.root / "config" / "llm.yaml"
    doc = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()
    roles = doc.setdefault("roles", CommentedMap())
    for name, role in data.roles.items():
        node = roles.get(name)
        if node is None:
            node = roles[name] = CommentedMap()
        node["provider"] = role.provider
        node["model"] = role.model
        node["max_tokens"] = role.max_tokens
        if role.effort:
            node["effort"] = role.effort
        else:
            node.pop("effort", None)
        if role.web_search or "web_search" in node:
            node["web_search"] = role.web_search
            node["web_search_max_uses"] = role.web_search_max_uses
    if data.monthly_budget_usd is None:
        doc.pop("monthly_budget_usd", None)
    else:
        b = data.monthly_budget_usd
        doc["monthly_budget_usd"] = int(b) if float(b).is_integer() else b
    doc["budget_warn_ratio"] = data.budget_warn_ratio
    doc["cache"] = data.cache
    pricing = doc.get("pricing")
    if pricing is None:
        pricing = doc["pricing"] = CommentedMap()
    for k in [k for k in list(pricing) if k not in data.pricing]:
        del pricing[k]
    for model, price in data.pricing.items():
        pricing[model] = _flow(list(price))
    _dump(yaml, doc, path)

    fresh = load_config(cfg.root)
    cfg.llm = fresh.llm
    return ["已保存 config/llm.yaml，下次运行生效"]
