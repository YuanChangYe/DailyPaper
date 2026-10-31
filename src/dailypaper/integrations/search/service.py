"""AI 检索。

1. ``expand``：便宜的模型把问题扩展成英文关键词、FTS 查询式和 arXiv 查询式（JSON）。
2. 召回：
   - ``pool``：本地顶会论文池（SQLite FTS5 / bm25），可按会议、年份、CCF / CORE 等级过滤；
   - ``web``：实时请求 arXiv API（覆盖 arXiv 全部历史）和 OpenAlex（覆盖非 arXiv 的会议、期刊论文）；
   - ``all``：两者都要。
   按 arXiv ID、DOI、规范化标题去重。
3. 重排：每 25 篇一批打 0–10 分并写一句理由；提示词里带研究方向和课题档案，所以排序是"对你"的相关度。
4. ``report``：对前 N 篇写深入综述（方法分组、对比表、研究空白、follow-up idea），保存成 Markdown，
   配置了 Obsidian vault 时同时复制过去。

结果缓存 24 小时（.cache/search/），检索历史存在 <数据目录>/searches.jsonl。都只在本地。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from ...core.config import AppConfig
from ...core.io import read_json, write_json, write_text
from ..research_profile import load_profile_text

log = logging.getLogger("dailypaper.search")

CACHE_HOURS = 24
POOL_LIMIT = 120
WEB_LIMIT = 50
BATCH = 25
UA = {"User-Agent": "DailyPaper/0.1 (personal research digest)"}
SCOPES = ("pool", "web", "all")


class SearchError(ValueError):
    pass


class RecallFailed(RuntimeError):
    """某个外部来源这次没拿到结果（限流、超时）；检索照常进行，结果里提示一句。"""


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:120]


def _role(cfg: AppConfig) -> str:
    return "ask" if "ask" in cfg.llm.roles else "screen"


def _ctx(cfg: AppConfig, provider_override: str | None):
    from ...core.context import RunContext

    return RunContext(config=cfg, run_date=date.today(), pipeline="search", provider_override=provider_override)


def _render(cfg: AppConfig, name: str, **kw) -> tuple[str, str]:
    from ...prompting.composer import PromptComposer

    return PromptComposer(cfg).render(name, topics=cfg.topics, profile=load_profile_text(cfg), language=cfg.settings.language, **kw)


def allowed_venues(cfg: AppConfig, venues: list[str] | None, ccf: list[str] | None, core: list[str] | None) -> list[str] | None:
    """会议多选与等级筛选的交集；都没选时返回 None（不过滤）。"""
    if not venues and not ccf and not core:
        return None
    out = []
    for v in cfg.venues.venues:
        if venues and v.id not in venues:
            continue
        if (ccf or core) and not ((ccf and v.ccf in ccf) or (core and v.core in core)):
            continue
        out.append(v.id)
    return out


# ---------------------------------------------------------------- 召回
def _fts_query(exp: dict, q: str) -> str:
    words = [w for w in (exp.get("keywords_en") or []) if isinstance(w, str) and w.strip()]
    if not words:
        words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", q)
    terms = []
    for w in words[:10]:
        w = re.sub(r'["*^():]', " ", w).strip()
        if w:
            terms.append(f'"{w}"')
    return " OR ".join(terms)


def recall_pool(cfg: AppConfig, exp: dict, q: str, venues: list[str] | None, years: list[int] | None) -> list[dict]:
    from ..venues.pool import VenuePool

    query = _fts_query(exp, q)
    if not query:
        return []
    pool = VenuePool(cfg)
    try:
        rows = pool.search_fts(query, venues=venues, years=years, limit=POOL_LIMIT)
    finally:
        pool.close()
    out = []
    for r in rows:
        p = r["paper"]
        x = p.extra
        out.append({
            "id": f"venue:{x.get('key') or p.id}", "source": "pool", "title": p.title, "abstract": p.abstract,
            "authors": p.authors[:8], "year": x.get("year"), "venue": x.get("venue"), "venue_name": x.get("venue_name", ""),
            "ccf": x.get("ccf", ""), "core": x.get("core", ""), "url_abs": p.url_abs, "url_pdf": p.url_pdf,
            "arxiv_id": x.get("arxiv_id", ""), "doi": p.doi, "readable": True,
        })
    return out


def recall_arxiv(exp: dict, q: str) -> list[dict]:
    from ...plugins.sources.arxiv import API_URL, parse_atom

    query = (exp.get("arxiv_query") or "").strip()
    if not query:
        words = [w for w in (exp.get("keywords_en") or re.findall(r"[A-Za-z][A-Za-z-]{2,}", q))][:6]
        query = " OR ".join(f'all:"{w}"' for w in words)
    if not query:
        return []
    papers = []
    for attempt in range(2):  # arXiv API 经常限流（429）：等 3 秒再试一次；仍不行就只用 OpenAlex 的结果
        try:
            r = httpx.get(API_URL, params={"search_query": query, "start": 0, "max_results": WEB_LIMIT, "sortBy": "relevance"},
                          headers=UA, timeout=15, follow_redirects=True)
            if r.status_code == 429 and attempt == 0:
                time.sleep(3)
                continue
            r.raise_for_status()
            papers = parse_atom(r.text)
            break
        except Exception as e:  # noqa: BLE001 - 一个来源失败不影响另一个
            log.warning("arXiv 检索失败：%s", e)
            raise RecallFailed("arXiv 暂时不可用（限流或超时），这次只用了 OpenAlex 的结果") from e
    return [{
        "id": p.id, "source": "arxiv", "title": p.title, "abstract": p.abstract, "authors": p.authors[:8],
        "year": p.published.year if p.published else None, "venue": "", "venue_name": "arXiv", "ccf": "", "core": "",
        "url_abs": p.url_abs, "url_pdf": p.url_pdf, "arxiv_id": p.id, "doi": p.doi, "readable": True,
    } for p in papers]


def recall_openalex(exp: dict, q: str) -> list[dict]:
    import os

    from ..venues.adapters.openalex import SELECT, work_to_paper

    words = [w for w in (exp.get("keywords_en") or []) if isinstance(w, str)][:6] or re.findall(r"[A-Za-z][A-Za-z-]{2,}", q)[:6]
    if not words:
        return []
    params: dict = {"search": " ".join(words), "per-page": WEB_LIMIT, "select": SELECT + ",publication_year,cited_by_count",
                    "filter": "type:article|preprint"}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    try:
        r = httpx.get("https://api.openalex.org/works", params=params, headers=UA, timeout=30)
        r.raise_for_status()
        works = r.json().get("results", [])
    except Exception as e:  # noqa: BLE001
        log.warning("OpenAlex 检索失败：%s", e)
        return []
    out = []
    for w in works:
        p = work_to_paper(w, "", w.get("publication_year") or 0)
        if not p:
            continue
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        aid = p.extra.get("arxiv_id") or ""
        out.append({
            "id": aid or p.id, "source": "openalex", "title": p.title, "abstract": p.abstract, "authors": p.authors[:8],
            "year": w.get("publication_year"), "venue": "", "venue_name": src, "ccf": "", "core": "",
            "url_abs": p.url_abs, "url_pdf": p.url_pdf, "arxiv_id": aid, "doi": p.doi, "readable": bool(aid),
            "cited_by": w.get("cited_by_count"),
        })
    return out


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in items:
        keys = {k for k in (f"a:{it.get('arxiv_id')}" if it.get("arxiv_id") else "", f"d:{(it.get('doi') or '').lower()}" if it.get("doi") else "",
                            f"t:{_norm_title(it.get('title', ''))}") if k and k != "t:"}
        if keys & seen:
            continue
        seen |= keys
        out.append(it)
    return out


# ---------------------------------------------------------------- 主流程
def _cache_path(cfg: AppConfig, key: str) -> Path:
    return cfg.cache_dir / "search" / f"{key}.json"


def search_key(q: str, scope: str, venues, years, ccf, core, n: int) -> str:
    raw = json.dumps([q.strip().lower(), scope, sorted(venues or []), sorted(years or []), sorted(ccf or []), sorted(core or []), n],
                     ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_cached(cfg: AppConfig, key: str) -> dict | None:
    hit = read_json(_cache_path(cfg, key), None)
    if hit and time.time() - hit.get("_ts", 0) < CACHE_HOURS * 3600:
        return hit
    return None


def search(cfg: AppConfig, q: str, *, scope: str = "pool", venues: list[str] | None = None, years: list[int] | None = None,
           ccf: list[str] | None = None, core: list[str] | None = None, n: int = 20, refresh: bool = False,
           provider_override: str | None = None) -> dict:
    from ...llm import get_llm
    from ...llm.client import extract_json
    from ..ask import check_budget
    from ..follow import mark_followed

    q = (q or "").strip()
    if not q:
        raise SearchError("请输入要找的内容")
    if scope not in SCOPES:
        raise SearchError(f"scope 只能是 {', '.join(SCOPES)}")
    n = max(5, min(int(n or 20), 50))
    key = search_key(q, scope, venues, years, ccf, core, n)
    if not refresh and (hit := load_cached(cfg, key)):
        return {**hit, "cached": True}
    check_budget(cfg)
    t0 = time.time()
    ctx = _ctx(cfg, provider_override)
    llm = get_llm(ctx, _role(cfg))
    usd = [0.0]
    lock = threading.Lock()

    def charge(resp) -> None:
        from ...llm.usage import cost_of

        if not resp.cached:
            with lock:  # 重排是多线程的
                usd[0] += cost_of(cfg, llm.provider.role.model, resp.input_tokens, resp.output_tokens) or 0.0

    system, user = _render(cfg, "search_expand", query=q)
    resp = llm.complete(system, user, tag="search_expand", json_mode=llm.provider.role.json_mode)
    charge(resp)
    try:
        exp = extract_json(resp.text)
    except Exception:  # noqa: BLE001 - 扩展失败就直接用原问题的英文词
        exp = {}
    exp = exp if isinstance(exp, dict) else {}

    allow = allowed_venues(cfg, venues, ccf, core)
    cands: list[dict] = []
    counts = {"pool": 0, "arxiv": 0, "openalex": 0}
    if scope in ("pool", "all") and allow != []:
        got = recall_pool(cfg, exp, q, allow, years)
        counts["pool"] = len(got)
        cands += got
    notes: list[str] = []
    if scope in ("web", "all"):
        from concurrent.futures import ThreadPoolExecutor

        def safe(fn):
            try:
                return fn(exp, q)
            except RecallFailed as e:
                notes.append(str(e))
                return []

        with ThreadPoolExecutor(max_workers=2) as ex:  # 两个来源同时请求，总耗时取较慢的那个
            fa, fo = ex.submit(safe, recall_arxiv), ex.submit(safe, recall_openalex)
            a, o = fa.result(), fo.result()
        counts["arxiv"], counts["openalex"] = len(a), len(o)
        cands += a + o
    cands = dedupe(cands)

    scored: dict[int, tuple[float, str]] = {}

    def rerank(start: int) -> None:
        batch = [{"i": start + j, "title": c["title"], "abstract": (c["abstract"] or "")[:700],
                  "venue": f"{c.get('venue_name') or ''} {c.get('year') or ''}".strip()} for j, c in enumerate(cands[start:start + BATCH])]
        system, user = _render(cfg, "search_rerank", query=q, intent=exp.get("intent", ""),
                               payload=json.dumps({"question": q, "candidates": batch}, ensure_ascii=False))
        resp = llm.complete(system, user, tag="search_rerank", json_mode=llm.provider.role.json_mode)
        charge(resp)
        try:
            data = extract_json(resp.text)
        except Exception:  # noqa: BLE001 - 这一批解析失败就当没打分
            return
        for r in (data.get("results") if isinstance(data, dict) else data) or []:
            try:
                scored[int(r["i"])] = (float(r.get("score", 0)), str(r.get("reason", "")))
            except (KeyError, TypeError, ValueError):
                continue

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:  # 几批并行打分，120 篇从 40 秒降到 10 秒左右
        list(pool.map(rerank, range(0, len(cands), BATCH)))
    for i, c in enumerate(cands):
        c["score"], c["reason"] = scored.get(i, (0.0, ""))
    cands.sort(key=lambda c: -c["score"])
    results = [c for c in cands if c["score"] > 0][:n]
    in_index = _index_lookup(cfg)
    for c in results:
        c["note_id"] = in_index.get(f"a:{c.get('arxiv_id')}") or in_index.get(f"t:{_norm_title(c['title'])}") or ""
    mark_followed(cfg, results)
    out = {
        "key": key, "q": q, "scope": scope, "filters": {"venues": venues or [], "years": years or [], "ccf": ccf or [], "core": core or []},
        "intent": exp.get("intent", ""), "keywords": exp.get("keywords_en", []), "counts": counts, "candidates": len(cands),
        "notes": notes,
        "results": results, "usd": round(usd[0], 5), "seconds": round(time.time() - t0, 1), "model": llm.model,
        "at": datetime.now().isoformat(timespec="seconds"), "_ts": time.time(),
    }
    write_json(_cache_path(cfg, key), out)
    _append_history(cfg, out)
    return out


def _index_lookup(cfg: AppConfig) -> dict[str, str]:
    """已经精读过的论文：检索结果里显示「已有笔记」。"""
    from ..site.contract import SiteContract

    out = {}
    try:
        for e in SiteContract(cfg).load_index():
            out[f"t:{_norm_title(e.title)}"] = e.id
            if re.match(r"^\d{4}\.\d{4,5}$", e.id):
                out[f"a:{e.id}"] = e.id
    except Exception:  # noqa: BLE001
        pass
    return out


def history_path(cfg: AppConfig) -> Path:
    return cfg.data_dir / "searches.jsonl"


def _append_history(cfg: AppConfig, res: dict) -> None:
    line = {"at": res["at"], "key": res["key"], "q": res["q"], "scope": res["scope"], "filters": res["filters"],
            "n": len(res["results"]), "top": [r["title"] for r in res["results"][:5]]}
    p = history_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_history(cfg: AppConfig, limit: int = 50) -> list[dict]:
    p = history_path(cfg)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    seen: set[str] = set()
    out = []
    for r in reversed(rows):  # 同一个问题只留最近一次
        k = r.get("q", "").strip().lower() + "|" + r.get("scope", "")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out[:limit]


# ---------------------------------------------------------------- 深入综述
def reports_dir(cfg: AppConfig) -> Path:
    return cfg.data_dir / "reports"


def report(cfg: AppConfig, key: str, n: int = 25, provider_override: str | None = None) -> dict:
    from ...llm import get_llm
    from ...llm.usage import cost_of
    from ..ask import check_budget

    res = read_json(_cache_path(cfg, key), None)
    if not res:
        raise SearchError("这次检索的结果已过期，请重新检索")
    items = res["results"][: max(5, min(n, 40))]
    if not items:
        raise SearchError("没有可以综述的结果")
    check_budget(cfg)
    papers = "\n\n".join(
        f"[{i}] {it['title']}（{it.get('venue_name') or it['source']} {it.get('year') or ''}）\n{(it.get('abstract') or '')[:1500]}"
        for i, it in enumerate(items, 1))
    system, user = _render(cfg, "search_report", query=res["q"], papers=papers, count=len(items))
    llm = get_llm(_ctx(cfg, provider_override), "read")
    t0 = time.time()
    resp = llm.complete(system, user, tag="report")
    refs = "\n".join(f"{i}. {it['title']} · {it.get('venue_name') or it['source']} {it.get('year') or ''} · {it.get('url_abs') or ''}"
                     for i, it in enumerate(items, 1))
    md = (f"---\ntitle: \"检索综述：{res['q']}\"\ndate: {date.today().isoformat()}\ntype: search-report\n---\n\n"
          f"# 检索综述：{res['q']}\n\n> 范围：{res['scope']} · {len(items)} 篇 · 模型 {llm.model}\n\n{resp.text.strip()}\n\n## 参考论文\n\n{refs}\n")
    slug = re.sub(r"[^\w一-鿿-]+", "-", res["q"])[:40].strip("-") or key
    path = reports_dir(cfg) / f"search-{date.today().isoformat()}-{slug}.md"
    write_text(path, md)
    vault = cfg.settings.obsidian.vault_path
    copied = ""
    if vault:
        try:
            dst = Path(vault) / "reports" / path.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst)
            copied = str(dst)
        except OSError as e:
            log.warning("复制综述到 Obsidian 失败：%s", e)
    usd = 0.0 if resp.cached else (cost_of(cfg, llm.provider.role.model, resp.input_tokens, resp.output_tokens) or 0.0)
    return {"markdown": md, "path": str(path), "obsidian": copied, "usd": round(usd, 5), "seconds": round(time.time() - t0, 1),
            "model": llm.model}


def to_bibtex(items: list[dict]) -> str:
    out = []
    for it in items:
        first = (it.get("authors") or ["anon"])[0].split()[-1] if it.get("authors") else "anon"
        word = (re.findall(r"[A-Za-z]{4,}", it.get("title", "")) or ["paper"])[0].lower()
        key = re.sub(r"[^A-Za-z0-9]", "", f"{first}{it.get('year') or ''}{word}")
        fields = {"title": it.get("title", ""), "author": " and ".join(it.get("authors") or []), "year": it.get("year") or "",
                  "booktitle" if it.get("venue_name") and it["source"] != "arxiv" else "journal": it.get("venue_name") or "arXiv",
                  "doi": it.get("doi") or "", "url": it.get("url_abs") or "", "eprint": it.get("arxiv_id") or ""}
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
        kind = "inproceedings" if it["source"] == "pool" else "article"
        out.append(f"@{kind}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + "\n"
