"""OpenReview API v2（NeurIPS / ICML / ICLR 等）：``content.venueid`` 等于会议 id 的 note 就是录用论文。

2026 年起 OpenReview 对匿名请求要求人机验证（403 ChallengeRequiredError），需要用账号登录：
在 .env 里写 ``OPENREVIEW_USERNAME`` 和 ``OPENREVIEW_PASSWORD``（注册免费）。没有账号时抛 VenueUnavailable，
同步会改用 venues.yaml 里该会议的 ``fallback`` 适配器（如 openalex）。

venues.yaml 参数 ``venueid``：例如 ``ICLR.cc/{y}/Conference``。
"""

from __future__ import annotations

import os

import httpx

from ....core.config import VenueSpec
from ....core.models import Paper
from ....core.registry import register
from .base import VenueAdapter, VenueUnavailable, log

API = "https://api2.openreview.net"
PAGE = 1000


def _val(content: dict, key: str, default=""):
    v = content.get(key)
    if isinstance(v, dict):
        v = v.get("value", default)
    return default if v is None else v


def note_to_paper(note: dict, venue_id: str, year: int) -> Paper | None:
    c = note.get("content") or {}
    title = str(_val(c, "title")).strip()
    if not title:
        return None
    nid = note.get("id", "")
    authors = [str(a) for a in (_val(c, "authors", []) or [])]
    pdf = _val(c, "pdf")
    return Paper(
        id=f"openreview:{nid}", source="venue", title=title, abstract=str(_val(c, "abstract")).strip(), authors=authors,
        url_abs=f"https://openreview.net/forum?id={nid}", url_pdf=f"https://openreview.net/pdf?id={nid}" if pdf else "",
        extra={"venue": venue_id, "year": year, "openreview_id": nid, "venue_label": str(_val(c, "venue"))},
    )


@register("venue_adapter", "openreview")
class OpenReviewAdapter(VenueAdapter):
    name = "openreview"

    def _token(self, client: httpx.Client) -> str | None:
        user, pwd = os.environ.get("OPENREVIEW_USERNAME"), os.environ.get("OPENREVIEW_PASSWORD")
        if not (user and pwd):
            return None
        r = client.post(f"{API}/login", json={"id": user, "password": pwd})
        if r.status_code != 200:
            raise VenueUnavailable(f"OpenReview 登录失败（HTTP {r.status_code}）：检查 OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD")
        return r.json().get("token")

    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        venueid = str(spec.param("venueid", "")).replace("{y}", str(year))
        if not venueid:
            raise VenueUnavailable(f"{spec.id}: venues.yaml 里没有 venueid")
        out: list[Paper] = []
        with self.client(Accept="application/json") as client:
            token = self._token(client)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            offset = 0
            while True:
                r = self.get_retry(client, f"{API}/notes", params={"content.venueid": venueid, "limit": PAGE, "offset": offset}, headers=headers)
                if r.status_code == 403 and "Challenge" in r.text:
                    raise VenueUnavailable(
                        "OpenReview 要求登录（匿名请求被人机验证拦截）。在 .env 里填 OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD，"
                        "或给该会议配置 fallback: openalex")
                if r.status_code != 200:
                    raise VenueUnavailable(f"{venueid}: HTTP {r.status_code} {r.text[:120]}")
                data = r.json()
                notes = data.get("notes") or []
                out += [p for p in (note_to_paper(n, spec.id, year) for n in notes) if p]
                if len(notes) < PAGE:
                    break
                offset += PAGE
        log.info("OpenReview %s：%d 篇", venueid, len(out))
        return out
