"""CVF Open Access：CVPR、ICCV（WACV 也可以）。

列表页 https://openaccess.thecvf.com/CVPR2025?day=all 有全部论文的标题、作者和 PDF 链接；摘要在每篇的详情页上。
venues.yaml 参数：
- ``cvf_conf``：CVPR / ICCV；
- ``cvf_abstracts``：是否逐篇抓摘要（默认 true，3000 篇约 5–10 分钟，4 个并发；false 时只有标题，检索效果差一些）。
ICCV 只在奇数年举办，偶数年列表页 404 时跳过。
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor

from ....core.config import VenueSpec
from ....core.models import Paper
from ....core.registry import register
from .base import VenueAdapter, VenueUnavailable, log

BASE = "https://openaccess.thecvf.com"
_DT_RE = re.compile(r'<dt class="ptitle">.*?<a href="([^"]+)">(.*?)</a>\s*</dt>(.*?)(?=<dt class="ptitle">|\Z)', re.S)
_AUTH_RE = re.compile(r'name="query_author" value="([^"]+)"')
_PDF_RE = re.compile(r'href="([^"]+_paper\.pdf)"')
_ABS_RE = re.compile(r'<div id="abstract"[^>]*>(.*?)</div>', re.S)


def parse_listing(text: str, venue_id: str, year: int) -> list[Paper]:
    out = []
    for href, title, rest in _DT_RE.findall(text):
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        if not title:
            continue
        pdf = _PDF_RE.search(rest)
        slug = href.rsplit("/", 1)[-1].removesuffix(".html")
        out.append(Paper(
            id=f"cvf-{slug}"[:120], source="venue", title=title, authors=[html.unescape(a) for a in _AUTH_RE.findall(rest)],
            url_abs=BASE + href, url_pdf=BASE + pdf.group(1) if pdf else "", extra={"venue": venue_id, "year": year},
        ))
    return out


@register("venue_adapter", "cvf")
class CvfAdapter(VenueAdapter):
    name = "cvf"

    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        conf = str(spec.param("cvf_conf", spec.name)).upper()
        with self.client() as client:
            r = self.get_retry(client, f"{BASE}/{conf}{year}?day=all")
            if r.status_code == 404:
                log.info("CVF 没有 %s%d", conf, year)
                return []
            if r.status_code != 200:
                raise VenueUnavailable(f"CVF {conf}{year}: HTTP {r.status_code}")
            papers = parse_listing(r.text, spec.id, year)
            if spec.param("cvf_abstracts", True) and papers:
                def fetch(p: Paper) -> None:
                    try:
                        rr = self.get_retry(client, p.url_abs, tries=2)
                        m = _ABS_RE.search(rr.text) if rr.status_code == 200 else None
                        if m:
                            p.abstract = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).split())
                    except VenueUnavailable:
                        self.partial = True

                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(fetch, papers))
        log.info("CVF %s%d：%d 篇", conf, year, len(papers))
        return papers
