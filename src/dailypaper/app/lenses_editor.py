"""网页「提示词与 Skill」用：读写 config/lenses.yaml（catalog 与 rules）、导入本机 skill、查看 skill 实际注入的文本。

写回用 ruamel 保留注释；catalog 里每一项可改 enabled / priority / sections / title / description / options，
可以新增（本地 Markdown lens 或导入的 skill）和删除；rules 整体替换。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml as pyyaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..core import registry
from ..core.config import AppConfig, LensSpec, load_config
from ..core.models import Paper, ScreenResult
from ..prompting.lens import LensContext
from ..prompting.selector import LensCatalog
from .config_editor import _dump, _flow, friendly_errors

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,60}$")
WHEN_KEYS = {"always", "topic", "paper_type", "min_score", "min_novelty", "source"}
PAPER_TYPES = ["method", "theory", "benchmark", "empirical", "survey", "position"]


def skills_source(cfg: AppConfig) -> Path:
    return Path(os.environ.get("DAILYPAPER_SKILLS_FALLBACK", Path.home() / ".claude" / "skills"))


def _kind(spec: LensSpec) -> str:
    if spec.adapter.startswith("skill_"):
        return "skill"
    if spec.id.startswith("type_"):
        return "type"
    return "reading"


def read_lenses_view(cfg: AppConfig) -> dict[str, Any]:
    cat = LensCatalog(cfg)
    rules = [{"when": dict(r.when), "use": list(r.use)} for r in cfg.lenses.rules]
    items = []
    for spec in cfg.lenses.catalog:
        adapter = cat.adapters.get(spec.id)
        items.append({
            "id": spec.id, "adapter": spec.adapter, "path": spec.path, "kind": _kind(spec),
            "title": adapter.title() if adapter else (spec.title or spec.id),
            "description": adapter.description() if adapter else spec.description,
            "title_raw": spec.title, "description_raw": spec.description,  # lenses.yaml 里写的原值，保存时原样回传
            "priority": spec.priority, "sections": list(spec.sections), "enabled": spec.enabled, "options": dict(spec.options),
            "available": bool(adapter and adapter.available()),
            "used_by_rules": [i for i, r in enumerate(rules) if spec.id in r["use"]],
        })
    mounted = {Path(s.path).name for s in cfg.lenses.catalog if s.adapter.startswith("skill_") and s.path}
    src = skills_source(cfg)
    local_skills = []
    if src.exists():
        for d in sorted(src.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                local_skills.append({"name": d.name, "mounted": d.name in mounted, "has_manifest": (d / "manifest.yaml").exists(),
                                     "description": _skill_description(d)})
    path = cfg.root / "config" / "lenses.yaml"
    return {
        "editable": path.exists() and os.access(path, os.W_OK),
        "budget_chars": cfg.lenses.budget_chars, "max_lenses": cfg.lenses.max_lenses,
        "llm_select": {"enabled": cfg.lenses.llm_select.enabled, "max_extra": cfg.lenses.llm_select.max_extra},
        "catalog": items, "rules": rules,
        "topics": [{"id": t.id, "name": t.name} for t in cfg.topics], "paper_types": PAPER_TYPES,
        "sections": [{"n": n, "title": s.title} for n, s in cfg.note_schema.numbered(True)],
        "local_skills": local_skills, "skills_source": str(src), "skills_dir": str(cfg.skills_dir),
        "adapters": sorted(registry.available().get("lens_adapter", [])),
    }


def _skill_description(d: Path) -> str:
    try:
        from ..core.io import split_frontmatter

        fm, _ = split_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
        return str(fm.get("description") or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 写回


class CatalogIn(BaseModel):
    id: str
    adapter: str
    path: str | None = None
    title: str = ""
    description: str = ""
    priority: int = Field(default=50, ge=0, le=100)
    sections: list[int] = Field(default_factory=list)
    enabled: bool = True
    options: dict = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"lens id {v!r} 只能用字母、数字、_ . -，以字母开头")
        return v


class RuleIn(BaseModel):
    when: dict = Field(default_factory=dict)
    use: list[str] = Field(default_factory=list)

    @field_validator("when")
    @classmethod
    def _when(cls, v: dict) -> dict:
        bad = set(v) - WHEN_KEYS
        if bad:
            raise ValueError(f"规则条件不支持 {', '.join(sorted(bad))}（可用：{', '.join(sorted(WHEN_KEYS))}）")
        return v


class LensesBody(BaseModel):
    budget_chars: int = Field(ge=2000, le=200_000)
    max_lenses: int = Field(ge=0, le=20)
    llm_select: dict = Field(default_factory=dict)
    catalog: list[CatalogIn]
    rules: list[RuleIn]

    @field_validator("catalog")
    @classmethod
    def _unique(cls, v: list[CatalogIn]) -> list[CatalogIn]:
        ids = [c.id for c in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"lens id 重复：{', '.join(sorted(dup))}")
        return v


def write_lenses(cfg: AppConfig, body: dict) -> list[str]:
    try:
        data = LensesBody.model_validate(body)
    except ValidationError as e:
        raise ValueError(friendly_errors(e)) from e
    known_adapters = set(registry.available().get("lens_adapter", []))
    ids = {c.id for c in data.catalog}
    for c in data.catalog:
        if c.adapter not in known_adapters:
            raise ValueError(f"{c.id}: 没有 {c.adapter} 这种 adapter（可用：{', '.join(sorted(known_adapters))}）")
        if c.adapter != "topic" and not c.path:
            raise ValueError(f"{c.id}: 缺少 path")
    for i, r in enumerate(data.rules):
        for lid in r.use:
            if lid not in ids and lid not in ("topic:*", "type:*") and not lid.startswith("topic:"):
                raise ValueError(f"规则 {i + 1} 引用了不存在的 lens {lid!r}")

    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 140
    yaml.indent(mapping=2, sequence=4, offset=2)  # 保持手写文件的 "  - {...}" 缩进
    path = cfg.root / "config" / "lenses.yaml"
    doc = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()
    doc["budget_chars"] = data.budget_chars
    doc["max_lenses"] = data.max_lenses
    ls = doc.get("llm_select")
    if ls is None:
        ls = doc["llm_select"] = CommentedMap()
    ls["enabled"] = bool(data.llm_select.get("enabled", True))
    ls["max_extra"] = int(data.llm_select.get("max_extra", 1))

    cat = doc.get("catalog")
    if cat is None:
        cat = doc["catalog"] = []
    existing = {str(n.get("id")): n for n in cat if isinstance(n, dict)}
    wanted = {c.id: c for c in data.catalog}
    # 删除：从后往前删，保留其他项的注释
    for i in range(len(cat) - 1, -1, -1):
        n = cat[i]
        if isinstance(n, dict) and str(n.get("id")) not in wanted:
            del cat[i]
    for c in data.catalog:
        node = existing.get(c.id)
        if node is None:
            node = CommentedMap()
            node["id"] = c.id
            node["adapter"] = c.adapter
            if c.path:
                node["path"] = c.path
            cat.append(node)
        node["adapter"] = c.adapter
        if c.path:
            node["path"] = c.path
        for k in ("title", "description"):
            v = getattr(c, k)
            if v:
                node[k] = v
            else:
                node.pop(k, None)
        node["priority"] = c.priority
        node["sections"] = _flow(sorted(set(int(x) for x in c.sections)))
        if c.enabled:
            node.pop("enabled", None)
        else:
            node["enabled"] = False
        if c.options:
            opts = node.get("options")
            if isinstance(opts, dict):  # 原地更新，保留原来的行内写法与注释
                for k in [k for k in list(opts) if k not in c.options]:
                    del opts[k]
                for k, v in c.options.items():
                    if opts.get(k) != v:
                        opts[k] = v
            else:
                node["options"] = c.options
        else:
            node.pop("options", None)

    rules = []
    for r in data.rules:
        node = CommentedMap()
        w = CommentedMap(r.when)
        for k, v in list(w.items()):
            if isinstance(v, list):
                w[k] = _flow(v)
        w.fa.set_flow_style()
        node["when"] = w
        node["use"] = _flow(list(r.use))
        rules.append(node)
    doc["rules"] = rules
    _dump(yaml, doc, path)

    fresh = load_config(cfg.root)
    cfg.lenses = fresh.lenses
    return ["已保存 config/lenses.yaml，下次精读生效"]


# ---------------------------------------------------------------- skill 导入与预览


def import_skills(cfg: AppConfig, names: list[str]) -> dict:
    from ..prompting.skills_sync import sync_skills

    names = [n for n in names if _ID_RE.match(n)]
    if not names:
        raise ValueError("没有可导入的 skill 名")
    src = skills_source(cfg)
    if not src.exists():
        raise ValueError(f"本机没有 skill 目录 {src}")
    copied = sync_skills(src, cfg.skills_dir, names)
    if not copied:
        raise ValueError("这些目录下没有 SKILL.md")
    view = read_lenses_view(cfg)
    catalog = [{**{k: v for k, v in c.items() if k in CatalogIn.model_fields}, "title": c["title_raw"], "description": c["description_raw"]}
               for c in view["catalog"]]
    known = {c["id"] for c in catalog}
    added = []
    for name in copied:
        if name in known:
            continue
        has_manifest = (cfg.skills_dir / name / "manifest.yaml").exists()
        catalog.append({
            "id": name, "adapter": "skill_manifest" if has_manifest else "skill_markdown",
            "path": f"{cfg.settings.paths.skills_dir}/{name}", "priority": 40, "sections": [], "enabled": True,
            "options": {"include_always_load": True, "max_chars": 8000} if has_manifest else {"max_chars": 6000},
        })
        added.append(name)
    body = {"budget_chars": view["budget_chars"], "max_lenses": view["max_lenses"], "llm_select": view["llm_select"],
            "catalog": catalog, "rules": view["rules"]}
    write_lenses(cfg, body)
    return {"copied": copied, "added": added,
            "messages": [f"已复制 {sum(copied.values())} 个文件到 {cfg.skills_dir}"] + ([f"已加入 catalog：{', '.join(added)}（还没有规则引用它，去「使用规则」里添加）"] if added else [])}


def skill_preview(cfg: AppConfig, lens_id: str, paper_type: str = "method") -> dict:
    spec = next((s for s in cfg.lenses.catalog if s.id == lens_id), None)
    if spec is None:
        raise ValueError(f"目录里没有 {lens_id}")
    try:
        adapter = registry.get("lens_adapter", spec.adapter)(cfg, spec)  # 停用的也能预览
    except KeyError as e:
        raise ValueError(str(e)) from e
    raw = ""
    p = adapter.resolve_path()
    if p is not None:
        f = p / "SKILL.md" if p.is_dir() else p
        if f.exists():
            raw = f.read_text(encoding="utf-8")
    lctx = LensContext(paper=Paper(id="0000.00000", title="Sample"), screen=ScreenResult(paper_type=paper_type), topics=[t.id for t in cfg.topics][:1])
    lens = adapter.build(lctx)
    return {"id": lens_id, "title": adapter.title(), "available": adapter.available(), "path": str(p) if p else None,
            "raw": raw, "injected": lens.content if lens else "", "chars": len(lens.content) if lens else 0}


def yaml_preview(cfg: AppConfig) -> str:
    return (cfg.root / "config" / "lenses.yaml").read_text(encoding="utf-8") if (cfg.root / "config" / "lenses.yaml").exists() else pyyaml.safe_dump({})
