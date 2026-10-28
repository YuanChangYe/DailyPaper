"""配置加载。

配置全部放在 ``<root>/config/*.yaml``：

- ``pipeline.yaml``  流水线：各 stage 用哪个插件、参数
- ``topics.yaml``    研究方向
- ``llm.yaml``       LLM provider 与按角色（screen / read）的模型
- ``lenses.yaml``    精读 lens 目录与选择规则
- ``settings.yaml``  路径、语言、Zotero / Obsidian / 网站 / 本地助手

YAML 字符串中可以写 ``${ENV}`` 或 ``${ENV:-默认值}`` 引用环境变量。
API key 只从环境变量读取，不写进配置文件。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(2) or "")

        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _expand_env(data)


# ---------------------------------------------------------------- pipeline


class StageSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    use: str  # "<kind>.<name>"
    params: dict = Field(default_factory=dict, alias="with")
    enabled: bool = True
    on_error: Literal["fail", "skip"] = "fail"


class PipelineConfig(BaseModel):
    pipelines: dict[str, list[StageSpec]] = Field(default_factory=dict)


# ---------------------------------------------------------------- topics


class Topic(BaseModel):
    id: str
    name: str
    description: str = ""
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list)  # 强关键词：命中一个即可
    context: list[str] = Field(default_factory=list)  # 语境词：非空时需同时命中至少一个
    exclude: list[str] = Field(default_factory=list)
    lens_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- llm


class ProviderSpec(BaseModel):
    type: str  # 对应 registry 中的 llm.<type>
    base_url: str | None = None
    api_key_env: str | None = None
    options: dict = Field(default_factory=dict)


class LLMRole(BaseModel):
    provider: str
    model: str = ""
    temperature: float | None = None  # Claude 已不接受该参数，会被忽略
    max_tokens: int = 4000
    effort: str | None = None  # Claude: low / medium / high / xhigh / max
    json_mode: bool = False
    options: dict = Field(default_factory=dict)


class LLMConfig(BaseModel):
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    roles: dict[str, LLMRole] = Field(default_factory=dict)
    cache: bool = True
    max_retries: int = 3
    pricing: dict[str, list[float]] = Field(default_factory=dict)  # 模型 → [输入, 输出] 美元 / 百万 token
    monthly_budget_usd: float | None = None
    budget_warn_ratio: float = 0.8


# ---------------------------------------------------------------- lenses


class LensSpec(BaseModel):
    id: str
    adapter: str  # 对应 registry 中的 lens_adapter.<name>
    path: str | None = None
    title: str = ""
    description: str = ""
    priority: int = 50
    sections: list[int] = Field(default_factory=list)  # 作用于 base 框架的哪几节
    options: dict = Field(default_factory=dict)


class LensRule(BaseModel):
    when: dict = Field(default_factory=dict)
    use: list[str] = Field(default_factory=list)


class LLMSelectConfig(BaseModel):
    enabled: bool = True
    max_extra: int = 2


class LensConfig(BaseModel):
    budget_chars: int = 24000
    max_lenses: int = 5
    llm_select: LLMSelectConfig = Field(default_factory=LLMSelectConfig)
    catalog: list[LensSpec] = Field(default_factory=list)
    rules: list[LensRule] = Field(default_factory=list)


# ---------------------------------------------------------------- settings


class Paths(BaseModel):
    content_dir: str = "docs"
    data_dir: str = "data"
    cache_dir: str = ".cache"
    skills_dir: str = "skills"


class ZoteroConfig(BaseModel):
    reader: str = "local_api"  # local_api | web_api | none
    writer: str = "connector"  # connector | web_api | none
    default_collection: str = "DailyPaper/{topic_name}"
    attach_pdf: bool = True
    add_note: bool = True
    tags: list[str] = Field(default_factory=lambda: ["dailypaper"])
    local_api: dict = Field(default_factory=lambda: {"base_url": "http://127.0.0.1:23119"})
    web_api: dict = Field(default_factory=dict)
    profile: dict = Field(default_factory=dict)


class ObsidianConfig(BaseModel):
    feedback: bool = True
    vault_path: str | None = None


class SiteConfig(BaseModel):
    title: str = "DailyPaper"
    base_url: str = ""
    adapter: str = "mkdocs"
    adapter_options: dict = Field(default_factory=dict)


class LocalAppConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    auto_git: bool = False


class Settings(BaseModel):
    language: str = "zh"
    timezone: str = "Asia/Shanghai"
    paths: Paths = Field(default_factory=Paths)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    app: LocalAppConfig = Field(default_factory=LocalAppConfig)


# ---------------------------------------------------------------- root


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    settings: Settings = Field(default_factory=Settings)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    topics: list[Topic] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    lenses: LensConfig = Field(default_factory=LensConfig)

    # 便捷路径
    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def content_dir(self) -> Path:
        return self.path(self.settings.paths.content_dir)

    @property
    def data_dir(self) -> Path:
        return self.path(self.settings.paths.data_dir)

    @property
    def cache_dir(self) -> Path:
        return self.path(self.settings.paths.cache_dir)

    @property
    def skills_dir(self) -> Path:
        return self.path(self.settings.paths.skills_dir)

    def topic(self, topic_id: str) -> Topic | None:
        return next((t for t in self.topics if t.id == topic_id), None)


def find_root(start: Path | None = None) -> Path:
    """优先用环境变量 DAILYPAPER_HOME，否则从当前目录向上找含 config/pipeline.yaml 的目录。"""
    env = os.environ.get("DAILYPAPER_HOME")
    if env:
        return Path(env).resolve()
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / "config" / "pipeline.yaml").exists():
            return d
    return cur


def load_config(root: Path | str | None = None) -> AppConfig:
    root = Path(root).resolve() if root else find_root()
    cfg_dir = root / "config"
    pipeline_raw = read_yaml(cfg_dir / "pipeline.yaml")
    topics_raw = read_yaml(cfg_dir / "topics.yaml")
    return AppConfig(
        root=root,
        settings=Settings.model_validate(read_yaml(cfg_dir / "settings.yaml")),
        pipeline=PipelineConfig.model_validate(pipeline_raw),
        topics=[Topic.model_validate(t) for t in topics_raw.get("topics", [])],
        llm=LLMConfig.model_validate(read_yaml(cfg_dir / "llm.yaml")),
        lenses=LensConfig.model_validate(read_yaml(cfg_dir / "lenses.yaml")),
    )
