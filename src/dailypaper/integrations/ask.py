"""论文问答：网页论文页「问答」标签的后端。

上下文（按优先级）：
1. PDF 全文（``pdf_to_text``，按页标 ``[p.N]``，去掉参考文献），缓存为 ``.cache/fulltext/<slug>.pages.md``；
2. 精读时保存的全文 ``.cache/fulltext/<slug>.md``（按章节，没有页码）；
3. 摘要。
再加上精读笔记、这篇论文所属的研究方向、课题档案。全文放在 system prompt 里：Claude 给 system 打了
cache_control，同一篇论文接着问时全文按缓存价计费；DeepSeek 自动缓存。

问答记录存在 ``<数据目录>/qa/<file_id>.json``，属于个人数据，不发布到网站。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import date, datetime

from ..core.config import AppConfig
from ..core.io import read_json, read_markdown, write_json, write_text
from ..core.models import Paper, file_id
from .research_profile import load_profile_text

log = logging.getLogger("dailypaper.ask")

MAX_CHARS = 90000  # 与精读的 FulltextEnricher 一致，约 2.5 万 token
HISTORY_TURNS = 4
_LOCK = threading.Lock()


class AskError(ValueError):
    pass


class BudgetExceeded(AskError):
    pass


def role_for(cfg: AppConfig, deep: bool) -> str:
    if deep:
        return "read"
    return "ask" if "ask" in cfg.llm.roles else "screen"


def check_budget(cfg: AppConfig) -> None:
    from ..llm.usage import month_summary

    budget = cfg.llm.monthly_budget_usd
    if budget and month_summary(cfg)["usd"] >= budget:
        raise BudgetExceeded(f"本月花费已达预算 ${budget:.0f}，问答暂停。可在「设置 → 运行与费用」调高预算。")


# ---------------------------------------------------------------- 上下文
def paper_context(cfg: AppConfig, paper_id: str, fetch_pdf: bool = True) -> dict:
    from ..app.papers import ensure_pdf, index_entry

    entry = index_entry(cfg, paper_id)
    if entry is None:
        raise AskError(f"索引里没有这篇论文：{paper_id}")
    paper = Paper(id=paper_id, title=entry.title, abstract=entry.abstract, authors=entry.authors, url_pdf=entry.url_pdf)
    ft_dir = cfg.cache_dir / "fulltext"
    pages_md = ft_dir / f"{paper.slug}.pages.md"
    text, grounding = "", ""
    if pages_md.exists():
        text, grounding = pages_md.read_text(encoding="utf-8"), "pdf"
    elif fetch_pdf:
        try:
            from ..plugins.fulltext.loaders import pdf_to_text

            text = pdf_to_text(ensure_pdf(cfg, paper_id))
            if len(text) > 500:
                write_text(pages_md, text)
                grounding = "pdf"
        except Exception as e:  # noqa: BLE001 - 没有 PDF / 没装 pymupdf：退回已有全文
            log.info("%s 取 PDF 全文失败，退回精读时的全文：%s", paper_id, e)
            text = ""
    if not grounding:
        plain = ft_dir / f"{paper.slug}.md"
        if plain.exists():
            text, grounding = plain.read_text(encoding="utf-8"), "fulltext"
        else:
            text, grounding = entry.abstract or "", "abstract"
    pages = text.count("[p.") if grounding == "pdf" else 0
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[……后文因长度限制截断……]"
    note = ""
    if entry.note_path and (cfg.content_dir / entry.note_path).exists():
        _, note = read_markdown(cfg.content_dir / entry.note_path)
    topics = [t for t in cfg.topics if t.id in (entry.topics or [])]
    return {"paper": paper, "entry": entry, "fulltext": text, "grounding": grounding, "pages": pages,
            "chars": len(text), "note": note, "focus_topics": topics}


def context_summary(cfg: AppConfig, ctx: dict) -> dict:
    roles = cfg.llm.roles
    quick, deep = roles.get(role_for(cfg, False)), roles.get("read")
    web_ok = bool(quick and cfg.llm.providers.get(quick.provider) and cfg.llm.providers[quick.provider].type == "anthropic")
    return {
        "grounding": ctx["grounding"], "pages": ctx["pages"], "chars": ctx["chars"], "has_note": bool(ctx["note"]),
        "model": f"{quick.provider}:{quick.model}" if quick else "", "deep_model": f"{deep.provider}:{deep.model}" if deep else "",
        "web_ok": web_ok, "has_profile": bool(load_profile_text(cfg)),
        "suggestions": suggestions(ctx),
    }


GENERIC_QUESTIONS = ["用一句话说清楚这篇的贡献", "实验设置有什么漏洞或不公平的比较？", "复现需要准备什么（数据、算力、代码）？", "这篇对我的课题有什么用？"]


def suggestions(ctx: dict) -> list[str]:
    out: list[str] = []
    for t in ctx["focus_topics"]:
        out += [q for q in t.lens_questions if q not in out][:2]
    return (out + [q for q in GENERIC_QUESTIONS if q not in out])[:6]


# ---------------------------------------------------------------- 记录
def qa_path(cfg: AppConfig, paper_id: str):
    return cfg.data_dir / "qa" / f"{file_id(paper_id)}.json"


def load_history(cfg: AppConfig, paper_id: str) -> list[dict]:
    data = read_json(qa_path(cfg, paper_id), None) or {}
    return list(data.get("items") or [])


def _save(cfg: AppConfig, paper_id: str, items: list[dict]) -> None:
    write_json(qa_path(cfg, paper_id), {"paper_id": paper_id, "items": items})


def delete_item(cfg: AppConfig, paper_id: str, item_id: str) -> bool:
    with _LOCK:
        items = load_history(cfg, paper_id)
        kept = [i for i in items if i.get("id") != item_id]
        if len(kept) == len(items):
            return False
        _save(cfg, paper_id, kept)
        return True


# ---------------------------------------------------------------- 提问
def ask(cfg: AppConfig, paper_id: str, question: str, *, quote: str = "", deep: bool = False, web: bool = False,
        on_delta=None, provider_override: str | None = None) -> dict:
    from ..core.context import RunContext
    from ..llm import get_llm
    from ..llm.usage import cost_of
    from ..prompting.composer import PromptComposer

    question = (question or "").strip()
    if not question:
        raise AskError("问题是空的")
    if len(question) > 4000 or len(quote) > 6000:
        raise AskError("问题或引文太长")
    check_budget(cfg)
    c = paper_context(cfg, paper_id)
    history = [{"q": h.get("q", ""), "a": h.get("a", "")} for h in load_history(cfg, paper_id)[-HISTORY_TURNS:]]
    system, user = PromptComposer(cfg).render(
        "ask", paper=c["paper"], fulltext=c["fulltext"], grounding=c["grounding"], note=c["note"],
        focus_topics=c["focus_topics"], profile=load_profile_text(cfg), language=cfg.settings.language,
        question=question, quote=quote.strip(), history=history,
    )
    role = role_for(cfg, deep)
    rctx = RunContext(config=cfg, run_date=date.today(), pipeline="ask", provider_override=provider_override)
    llm = get_llm(rctx, role, web_search=True) if web else get_llm(rctx, role)
    t0 = time.time()
    resp = llm.stream(system, user, on_delta, tag="ask") if on_delta else llm.complete(system, user, tag="ask")
    usd = 0.0 if resp.cached else (cost_of(cfg, llm.provider.role.model, resp.input_tokens, resp.output_tokens) or 0.0)
    item = {
        "id": uuid.uuid4().hex[:10], "q": question, "quote": quote.strip(), "a": resp.text.strip(),
        "model": llm.model, "usd": round(usd, 5), "deep": deep, "web": web, "grounding": c["grounding"],
        "seconds": round(time.time() - t0, 1), "cached": resp.cached,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        items = load_history(cfg, paper_id)
        items.append(item)
        _save(cfg, paper_id, items)
    return item
