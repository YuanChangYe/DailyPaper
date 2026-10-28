"""Zotero 文库索引与兴趣画像（纯 Python，无需 numpy）。

本地助手从 Zotero 读取文库后导出两个文件（提交到 git，云端运行也能用）：
- ``data/zotero_index.json``      已有文献的 arXiv ID / DOI / 规范化标题，用于去重
- ``data/interest_profile.json``  文库标题+摘要的加权词表，用于个性化排序
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ...core.config import AppConfig
from ...core.io import read_json, write_json
from ...core.models import Paper

_WORD_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")
_STOP = set(
    """the and for with from that this are was were been have has had not but can our their its into over under
    than then also such these those using use used based via between within without about across more most less
    paper propose proposed approach method methods results show shows new novel model models data task tasks
    which while where when what how both each other only one two three first second large high low however
    we us they them it is be an as at by on of in to or if do does done study work framework""".split()
)


def tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class InterestProfile:
    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        top = sorted(weights.values(), reverse=True)[:30]
        self.norm = sum(top) or 1.0

    @classmethod
    def build(cls, docs: list[str], top_terms: int = 400) -> InterestProfile:
        df: Counter = Counter()
        tf: Counter = Counter()
        for d in docs:
            ts = tokens(d)
            tf.update(ts)
            df.update(set(ts))
        n = max(len(docs), 1)
        scored = {w: tf[w] * math.log(1 + n / df[w]) for w in tf if df[w] >= 2 or n < 20}
        best = dict(sorted(scored.items(), key=lambda kv: -kv[1])[:top_terms])
        mx = max(best.values(), default=1.0)
        return cls({w: round(v / mx, 4) for w, v in best.items()})

    def score(self, text: str) -> float:
        """0..1：文本与兴趣画像的重合程度。"""
        s = sum(self.weights.get(w, 0.0) for w in set(tokens(text)))
        return min(1.0, s / self.norm)


def load_profile(cfg: AppConfig) -> InterestProfile | None:
    data = read_json(cfg.data_dir / "interest_profile.json", None)
    return InterestProfile(data["weights"]) if data and data.get("weights") else None


class LibraryIndex:
    def __init__(self, arxiv_ids: set[str], dois: set[str], titles: set[str]) -> None:
        self.arxiv_ids, self.dois, self.titles = arxiv_ids, dois, titles

    @classmethod
    def load(cls, cfg: AppConfig) -> LibraryIndex | None:
        data = read_json(cfg.data_dir / "zotero_index.json", None)
        if not data:
            return None
        return cls(set(data.get("arxiv_ids", [])), set(data.get("dois", [])), set(data.get("titles", [])))

    def contains(self, paper: Paper) -> bool:
        return (
            paper.id in self.arxiv_ids
            or (paper.doi and paper.doi.lower() in self.dois)
            or norm_title(paper.title) in self.titles
        )


def export_library(cfg: AppConfig, items: list[dict]) -> dict:
    """items 为 Zotero 条目的 data 字典列表。写出索引和兴趣画像，返回统计。"""
    arxiv_re = re.compile(r"(\d{4}\.\d{4,5})")
    ids, dois, titles, docs = set(), set(), set(), []
    for it in items:
        title = it.get("title", "")
        if not title:
            continue
        titles.add(norm_title(title))
        if it.get("DOI"):
            dois.add(it["DOI"].lower())
        for field in ("url", "extra", "archiveID", "number"):
            m = arxiv_re.search(str(it.get(field, "")))
            if m:
                ids.add(m.group(1))
                break
        docs.append(f"{title}. {it.get('abstractNote', '')}")
    prof_cfg = cfg.settings.zotero.profile
    profile = InterestProfile.build(docs, int(prof_cfg.get("top_terms", 400)))
    write_json(
        cfg.data_dir / "zotero_index.json",
        {"arxiv_ids": sorted(ids), "dois": sorted(dois), "titles": sorted(titles)},
    )
    write_json(cfg.data_dir / "interest_profile.json", {"n_docs": len(docs), "weights": profile.weights})
    return {"items": len(docs), "arxiv_ids": len(ids), "terms": len(profile.weights)}
