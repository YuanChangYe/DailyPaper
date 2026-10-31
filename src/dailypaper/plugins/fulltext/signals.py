"""外部信号（精读完成后查一次，只作为小字辅助信息，不参与排序）。

- OpenAlex：作者机构、被引数、作者 id（用于「关注的作者」按 id 匹配）。新 arXiv 论文按 DOI 10.48550/arXiv.<id> 查。
- Hugging Face 每日论文榜：在榜 = 社区关注度高。
- 冷门好文：筛选给的最高方向分 ≥ hidden_gem_score，且被引 0、不在 HF 榜上。正好和"按热度推送"反过来。

都写进 paper.extra，再由 site_index 写进 api/index.json。网络失败只跳过，不影响精读。
"""

from __future__ import annotations

import re
from datetime import date

import httpx
from pydantic import BaseModel

from ...core.interfaces import Enricher
from ...core.io import read_json, write_json
from ...core.models import Candidate
from ...core.registry import register

UA = {"User-Agent": "DailyPaper/0.1 (personal research digest)"}


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


@register("enricher", "signals")
class SignalsEnricher(Enricher):
    class Params(BaseModel):
        openalex: bool = True
        hf_daily: bool = True
        hidden_gem_score: float = 8.0

    def _openalex(self, client: httpx.Client, c: Candidate) -> dict | None:
        p = c.paper
        sel = "id,title,cited_by_count,authorships"
        doi = p.doi or (f"10.48550/arXiv.{p.id}" if p.is_arxiv else "")
        if not doi and p.extra.get("arxiv_id"):
            doi = f"10.48550/arXiv.{p.extra['arxiv_id']}"
        if doi:
            r = client.get(f"https://api.openalex.org/works/doi:{doi}", params={"select": sel})
            if r.status_code == 200:
                return r.json()
        r = client.get("https://api.openalex.org/works", params={"search": p.title, "per-page": 3, "select": sel})
        if r.status_code == 200:
            for w in r.json().get("results", []):
                if _norm(w.get("title", "")) == _norm(p.title):
                    return w
        return None

    def _hf_ids(self, client: httpx.Client, day: date) -> set[str]:
        path = self.config.cache_dir / "hf" / f"{day.isoformat()}.json"
        ids = read_json(path, None)
        if ids is None:
            r = client.get("https://huggingface.co/api/daily_papers", params={"date": day.isoformat()})
            ids = [str((x.get("paper") or {}).get("id", "")) for x in r.json()] if r.status_code == 200 else []
            write_json(path, ids)
        return set(ids)

    def run(self, items: list[Candidate]) -> list[Candidate]:
        todo = [c for c in self.selected(items) if c.note]
        if not todo:
            return items
        with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
            hf: set[str] = set()
            if self.p.hf_daily:
                try:
                    hf = self._hf_ids(client, self.ctx.run_date)
                except Exception as e:  # noqa: BLE001
                    self.log.info("HF 每日论文榜读取失败：%s", e)
            for c in todo:
                x = c.paper.extra
                if self.p.openalex:
                    try:
                        w = self._openalex(client, c)
                    except Exception as e:  # noqa: BLE001
                        self.log.info("%s OpenAlex 查询失败：%s", c.id, e)
                        w = None
                    if w:
                        insts: list[str] = []
                        for a in w.get("authorships") or []:
                            for inst in a.get("institutions") or []:
                                name = inst.get("display_name")
                                if name and name not in insts:
                                    insts.append(name)
                        x["affiliations"] = insts[:6]
                        x["cited_by"] = int(w.get("cited_by_count") or 0)
                        x["openalex_authors"] = [str((a.get("author") or {}).get("id", "")).rsplit("/", 1)[-1]
                                                 for a in w.get("authorships") or []]
                aid = c.paper.id if c.paper.is_arxiv else str(x.get("arxiv_id") or "")
                x["hf_trending"] = bool(aid and aid in hf)
                best = max((c.screen.topic_scores.values() if c.screen else []), default=0)
                x["hidden_gem"] = bool(best >= self.p.hidden_gem_score and not x.get("cited_by") and not x["hf_trending"])
        return items
