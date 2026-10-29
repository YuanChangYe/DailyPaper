"""ACL Anthology：按卷下载官方 BibTeX（含 abstract），例如 https://aclanthology.org/volumes/2025.acl-long.bib。

venues.yaml 参数 ``volumes``：卷名模板列表，``{y}`` 替换成年份；404 的卷视为该年没有，跳过。
"""

from __future__ import annotations

import re

from ....core.config import VenueSpec
from ....core.models import Paper
from ....core.registry import register
from ..pool import extract_arxiv_id
from .base import VenueAdapter, VenueUnavailable, log

VOLUME_URL = "https://aclanthology.org/volumes/{vol}.bib"
_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)
_FIELD_RE = re.compile(r"^\s*(\w+)\s*=\s*(.*?),?\s*$", re.S)


def _unbrace(s: str) -> str:
    s = s.strip()
    while s and s[0] in "{\"" and s[-1] in "}\"":
        s = s[1:-1].strip()
    s = re.sub(r"\{([^{}]*)\}", r"\1", s)  # {A}ssociation → Association
    s = re.sub(r"\{([^{}]*)\}", r"\1", s)
    return " ".join(s.split())


def _fields(body: str) -> dict[str, str]:
    """把一个 entry 的 body 拆成字段：字段值可能跨行，以 ``key = {...}`` 或 ``key = "..."`` 形式。"""
    out: dict[str, str] = {}
    i = 0
    n = len(body)
    while i < n:
        m = re.compile(r"\s*(\w+)\s*=\s*").match(body, i)
        if not m:
            break
        key = m.group(1).lower()
        j = m.end()
        if j >= n:
            break
        if body[j] in "{\"":
            open_ch = body[j]
            close_ch = "}" if open_ch == "{" else "\""
            depth = 0
            k = j if open_ch == "{" else j + 1  # 引号值从开引号之后开始找闭引号
            while k < n:
                ch = body[k]
                if open_ch == "{":
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                elif ch == close_ch and body[k - 1] != "\\":
                    break
                k += 1
            out[key] = _unbrace(body[j : k + 1])
            i = k + 1
        else:
            k = body.find(",", j)
            k = n if k < 0 else k
            out[key] = body[j:k].strip()
            i = k
        comma = body.find(",", i)
        i = comma + 1 if comma >= 0 else n
    return out


def parse_bib(text: str, venue_id: str, year: int) -> list[Paper]:
    papers: list[Paper] = []
    for m in _ENTRY_RE.finditer(text):
        etype, _key, body = m.group(1).lower(), m.group(2), m.group(3)
        if etype != "inproceedings":
            continue
        f = _fields(body)
        title = f.get("title", "")
        if not title:
            continue
        url = f.get("url", "")
        authors = [" ".join(reversed([x.strip() for x in a.split(",")])) if "," in a else a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
        aid = extract_arxiv_id(f.get("note", ""), f.get("eprint", ""))
        papers.append(
            Paper(
                id=url.rstrip("/").rsplit("/", 1)[-1] or title[:40], source="venue", title=title, abstract=f.get("abstract", ""), authors=authors,
                url_abs=url, url_pdf=(url.rstrip("/") + ".pdf") if url else "", doi=f.get("doi", ""),
                extra={"venue": venue_id, "year": year, "arxiv_id": aid, "pages": f.get("pages", "")},
            )
        )
    return papers


@register("venue_adapter", "acl_anthology")
class AclAnthologyAdapter(VenueAdapter):
    name = "acl_anthology"

    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        volumes = [v.replace("{y}", str(year)) for v in spec.param("volumes", [])]
        if not volumes:
            raise VenueUnavailable(f"{spec.id}: venues.yaml 里没有 volumes")
        out: list[Paper] = []
        self.partial = False
        with self.client() as client:
            for vol in volumes:
                url = VOLUME_URL.format(vol=vol)
                try:
                    r = self.get_retry(client, url)
                except VenueUnavailable as e:
                    log.warning("ACL Anthology %s 暂时拿不到，下次再试：%s", vol, e)
                    self.partial = True
                    continue
                if r.status_code == 404:
                    log.info("ACL Anthology 没有 %s（%d 年没有这一卷）", vol, year)
                    continue
                if r.status_code != 200:
                    log.warning("ACL Anthology %s HTTP %d，下次再试", vol, r.status_code)
                    self.partial = True
                    continue
                papers = parse_bib(r.text, spec.id, year)
                log.info("ACL Anthology %s：%d 篇", vol, len(papers))
                out += papers
        return out
