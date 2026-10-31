"""PMLR（Proceedings of Machine Learning Research）：ICML、COLT、AISTATS、UAI 等。

每卷有一个完整的 BibTeX（含 abstract）：https://proceedings.mlr.press/v267/assets/bib/bibliography.bib
venues.yaml 参数 ``pmlr_volumes``：年份 → 卷号，例如 {2025: 267, 2024: 235}。新一年的卷号出来后补一行。
"""

from __future__ import annotations

import re

from ....core.config import VenueSpec
from ....core.models import Paper
from ....core.registry import register
from ..pool import extract_arxiv_id
from .acl_anthology import _ENTRY_RE, _fields
from .base import VenueAdapter, VenueUnavailable, log

BIB_URL = "https://proceedings.mlr.press/v{vol}/assets/bib/bibliography.bib"


def parse_pmlr_bib(text: str, venue_id: str, year: int, vol: int) -> list[Paper]:
    papers: list[Paper] = []
    for m in _ENTRY_RE.finditer(text):
        if m.group(1).lower() != "inproceedings":
            continue
        key, f = m.group(2), _fields(m.group(3))
        title = f.get("title", "")
        if not title or (re.search(r"\bpreface\b", title, re.I) and not f.get("abstract")):  # 卷首的 Preface 不是论文
            continue
        authors = [" ".join(reversed([x.strip() for x in a.split(",")])) if "," in a else a.strip()
                   for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
        url = f.get("url", "") or f"https://proceedings.mlr.press/v{vol}/{key}.html"
        pdf = f.get("pdf", "") or url.replace(".html", "/" + key + ".pdf")
        papers.append(Paper(
            id=f"pmlr-v{vol}-{key}", source="venue", title=title, abstract=f.get("abstract", ""), authors=authors,
            url_abs=url, url_pdf=pdf, extra={"venue": venue_id, "year": year, "arxiv_id": extract_arxiv_id(f.get("note", ""))},
        ))
    return papers


@register("venue_adapter", "pmlr")
class PmlrAdapter(VenueAdapter):
    name = "pmlr"

    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        vols = {int(k): int(v) for k, v in (spec.param("pmlr_volumes", {}) or {}).items()}
        vol = vols.get(year)
        if not vol:
            log.info("PMLR：%s 没有配置 %d 年的卷号（pmlr_volumes），跳过", spec.id, year)
            return []
        with self.client() as client:
            r = self.get_retry(client, BIB_URL.format(vol=vol))
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise VenueUnavailable(f"PMLR v{vol}: HTTP {r.status_code}")
        papers = parse_pmlr_bib(r.text, spec.id, year, vol)
        log.info("PMLR %s v%d：%d 篇", spec.id, vol, len(papers))
        return papers
