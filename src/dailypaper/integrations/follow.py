"""关注的作者 / 机构（config/follow.yaml，个人数据，默认不提交）。

    authors:
      - name: Yann LeCun
        openalex: A5001226970     # 可选；有 id 时按 id 匹配，避免同名
      - Chelsea Finn              # 也可以只写名字（精确全名匹配，不区分大小写）
    institutions: [Stanford University, Google DeepMind]
    bonus: 1.5                    # 每日排序时的加分（加在最终排序分上）

每日流水线：命中的论文加分并显示「关注」徽章，并且不会被关键词预筛掉。检索：结果里标出来，可只看关注的作者。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..core.config import AppConfig
from ..core.models import Paper


def follow_path(cfg: AppConfig) -> Path:
    return cfg.path("config") / "follow.yaml"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.\-]", " ", name or "")).strip().lower()


def load_follow(cfg: AppConfig) -> dict:
    p = follow_path(cfg)
    data = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}
    authors = []
    for a in data.get("authors") or []:
        if isinstance(a, str):
            authors.append({"name": a, "openalex": ""})
        elif isinstance(a, dict) and a.get("name"):
            authors.append({"name": str(a["name"]), "openalex": str(a.get("openalex") or "")})
    return {"authors": authors, "institutions": [str(x) for x in data.get("institutions") or [] if x],
            "bonus": float(data.get("bonus", 1.5))}


def save_follow(cfg: AppConfig, data: dict) -> dict:
    authors = []
    for a in data.get("authors") or []:
        name = (a.get("name") if isinstance(a, dict) else a) or ""
        name = str(name).strip()
        if not name:
            continue
        oa = str(a.get("openalex") or "").strip() if isinstance(a, dict) else ""
        authors.append({"name": name, "openalex": oa} if oa else name)
    out = {"authors": authors, "institutions": [s.strip() for s in data.get("institutions") or [] if str(s).strip()],
           "bonus": float(data.get("bonus", 1.5))}
    p = follow_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = "# 关注的作者 / 机构（网页「设置 → 研究方向」编辑）。个人数据，默认不提交。\n"
    p.write_text(head + yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    return load_follow(cfg)


def matches(follow: dict, authors: list[str], institutions: list[str] | None = None, author_ids: list[str] | None = None) -> list[str]:
    """返回命中的关注对象名字（作者或机构）。"""
    names = {_norm(a) for a in authors or []}
    ids = set(author_ids or [])
    hit = [a["name"] for a in follow["authors"] if (a["openalex"] and a["openalex"] in ids) or _norm(a["name"]) in names]
    insts = [_norm(i) for i in institutions or []]
    hit += [i for i in follow["institutions"] if any(_norm(i) in x for x in insts)]
    return hit


def paper_matches(follow: dict, paper: Paper) -> list[str]:
    x = paper.extra or {}
    return matches(follow, paper.authors, x.get("affiliations"), x.get("openalex_authors"))


def mark_followed(cfg: AppConfig, items: list[dict]) -> None:
    follow = load_follow(cfg)
    if not follow["authors"] and not follow["institutions"]:
        return
    for it in items:
        hit = matches(follow, it.get("authors") or [])
        if hit:
            it["followed"] = hit
