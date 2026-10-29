"""OpenAlex（https://api.openalex.org）：按会议的 source id 和年份拉取论文，摘要由 abstract_inverted_index 还原。

venues.yaml 参数 ``openalex_source``：例如 S4363606243（NeurIPS）。查 id：
    https://api.openalex.org/sources?search=Neural%20Information%20Processing%20Systems
覆盖面因会议而异（NeurIPS 好，ICLR / ICML 很差），所以主要作为 fallback。
``OPENALEX_API_KEY`` 可选；没有也能用，限速更严。
"""

from __future__ import annotations

import os

from ....core.config import VenueSpec
from ....core.models import Paper
from ....core.registry import register
from ..pool import extract_arxiv_id
from .base import VenueAdapter, VenueUnavailable, log

API = "https://api.openalex.org/works"
SELECT = "id,title,doi,publication_year,authorships,abstract_inverted_index,primary_location,locations,ids"


def abstract_from_index(idx: dict | None) -> str:
    if not idx:
        return ""
    pos: list[tuple[int, str]] = []
    for word, positions in idx.items():
        pos += [(p, word) for p in positions]
    pos.sort()
    return " ".join(w for _, w in pos)


def work_to_paper(w: dict, venue_id: str, year: int) -> Paper | None:
    title = (w.get("title") or "").strip()
    if not title:
        return None
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    locs = [(loc or {}) for loc in (w.get("locations") or [])] + [w.get("primary_location") or {}]
    urls = [str(x.get("landing_page_url") or "") for x in locs] + [str(x.get("pdf_url") or "") for x in locs]
    aid = extract_arxiv_id(*urls)
    pdf = next((x.get("pdf_url") for x in locs if x.get("pdf_url")), "") or ""
    landing = (w.get("primary_location") or {}).get("landing_page_url") or (f"https://doi.org/{doi}" if doi else "")
    oaid = str(w.get("id") or "").rsplit("/", 1)[-1]
    return Paper(
        id=f"openalex:{oaid}", source="venue", title=title, abstract=abstract_from_index(w.get("abstract_inverted_index")),
        authors=[a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or []) if a.get("author")],
        url_abs=landing or "", url_pdf=pdf, doi=doi,
        extra={"venue": venue_id, "year": year, "arxiv_id": aid, "openalex_id": oaid},
    )


@register("venue_adapter", "openalex")
class OpenAlexAdapter(VenueAdapter):
    name = "openalex"

    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        source = str(spec.param("openalex_source", ""))
        if not source:
            raise VenueUnavailable(f"{spec.id}: venues.yaml 里没有 openalex_source")
        params: dict = {"filter": f"primary_location.source.id:{source},publication_year:{year}", "per-page": 200, "select": SELECT}
        key = os.environ.get("OPENALEX_API_KEY")
        if key:
            params["api_key"] = key
            params["cursor"] = "*"  # 有 key 才能用游标翻页（不限条数）
        else:
            params["page"] = 1  # 无 key：普通分页，最多 1 万条
        out: list[Paper] = []
        with self.client() as client:
            while True:
                r = self.get_retry(client, API, params=params)
                if r.status_code == 429:
                    raise VenueUnavailable("OpenAlex 限流（429）：稍后再试，或在 .env 设置 OPENALEX_API_KEY")
                if r.status_code != 200:
                    raise VenueUnavailable(f"OpenAlex {source}/{year}: HTTP {r.status_code} {r.text[:120]}")
                data = r.json()
                results = data.get("results", [])
                out += [p for p in (work_to_paper(w, spec.id, year) for w in results) if p]
                if len(results) < 200:
                    break
                if key:
                    cursor = (data.get("meta") or {}).get("next_cursor")
                    if not cursor:
                        break
                    params["cursor"] = cursor
                else:
                    params["page"] += 1
                    if params["page"] > 50:
                        log.warning("OpenAlex %s/%d 超过 1 万条，无 key 只能取前 1 万；设置 OPENALEX_API_KEY 可取全", spec.id, year)
                        break
        log.info("OpenAlex %s/%d：%d 篇", spec.id, year, len(out))
        return out
