"""MCP 服务器（stdio，JSON-RPC 2.0，不依赖第三方 SDK）：在 Claude Desktop / Claude Code 里直接查你的论文库。

启动：``dailypaper mcp``。Claude Code 里添加：
    claude mcp add dailypaper -- <项目>/.venv/Scripts/dailypaper.exe --root <项目> mcp

工具：
- ``list_papers``    精读过的论文（按日期 / 方向 / 关键词过滤）
- ``get_note``       某篇的精读笔记全文
- ``pool_search``    在顶会论文池里全文检索（本地，不花钱）
- ``ai_search``      AI 检索（扩展关键词 + 重排，会调用模型，约 $0.01）
- ``ask_paper``      就某篇论文提问（依据全文 + 笔记，会调用模型）
日志只写 stderr，stdout 只用于协议消息。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from .. import __version__
from ..core.config import AppConfig

log = logging.getLogger("dailypaper.mcp")
PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "list_papers", "description": "列出 DailyPaper 精读过的论文（标题、日期、方向、一句话总结）。可按日期、方向或关键词过滤。",
     "inputSchema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD，只看这一天"},
         "topic": {"type": "string", "description": "研究方向 id"},
         "q": {"type": "string", "description": "标题 / 摘要里的关键词"},
         "limit": {"type": "integer", "default": 20}}}},
    {"name": "get_note", "description": "读取某篇论文的精读笔记全文（Markdown）。paper_id 来自 list_papers。",
     "inputSchema": {"type": "object", "properties": {"paper_id": {"type": "string"}}, "required": ["paper_id"]}},
    {"name": "pool_search", "description": "在本地顶会论文池（CCF A/B、CORE A*/A 会议近两年录用论文）里按关键词全文检索，不调用模型。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "英文关键词，空格分隔"},
         "venues": {"type": "array", "items": {"type": "string"}, "description": "会议 id，如 acl、neurips"},
         "years": {"type": "array", "items": {"type": "integer"}},
         "limit": {"type": "integer", "default": 20}}, "required": ["query"]}},
    {"name": "ai_search", "description": "AI 检索：用自然语言描述要找的论文，结合研究方向重排并给出理由。会调用模型（约 $0.01）。",
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string"}, "scope": {"type": "string", "enum": ["pool", "web", "all"], "default": "pool"},
         "n": {"type": "integer", "default": 15}}, "required": ["q"]}},
    {"name": "ask_paper", "description": "就一篇精读过的论文提问，依据 PDF 全文和精读笔记回答，标出原文页码。会调用模型。",
     "inputSchema": {"type": "object", "properties": {"paper_id": {"type": "string"}, "question": {"type": "string"}},
                     "required": ["paper_id", "question"]}},
]


def _text(obj: Any) -> dict:
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)}]}


def call_tool(cfg: AppConfig, name: str, args: dict) -> dict:
    from ..integrations.site.contract import SiteContract

    if name == "list_papers":
        rows = SiteContract(cfg).load_index()
        if args.get("date"):
            rows = [e for e in rows if e.date == args["date"]]
        if args.get("topic"):
            rows = [e for e in rows if args["topic"] in e.topics]
        if args.get("q"):
            q = str(args["q"]).lower()
            rows = [e for e in rows if q in (e.title + " " + e.abstract).lower()]
        rows.sort(key=lambda e: (e.date, e.score), reverse=True)
        return _text([{"id": e.id, "title": e.title, "date": e.date, "topics": e.topics, "tldr": e.tldr,
                       "venue": f"{e.venue_name} {e.venue_year}".strip() or "arXiv"} for e in rows[: int(args.get("limit") or 20)]])
    if name == "get_note":
        from ..core.io import read_markdown

        e = next((x for x in SiteContract(cfg).load_index() if x.id == args.get("paper_id")), None)
        if not e or not e.note_path or not (cfg.content_dir / e.note_path).exists():
            return {**_text(f"没有这篇的笔记：{args.get('paper_id')}"), "isError": True}
        return _text(read_markdown(cfg.content_dir / e.note_path)[1])
    if name == "pool_search":
        from ..integrations.venues.pool import VenuePool

        terms = " OR ".join(f'"{w}"' for w in str(args.get("query", "")).split() if w.strip('"'))
        pool = VenuePool(cfg)
        try:
            rows = pool.search_fts(terms, venues=args.get("venues") or None, years=args.get("years") or None,
                                   limit=int(args.get("limit") or 20))
        finally:
            pool.close()
        return _text([{"key": r["paper"].id, "title": r["paper"].title, "venue": r["paper"].extra.get("venue_name"),
                       "year": r["paper"].extra.get("year"), "url": r["paper"].url_abs, "abstract": r["paper"].abstract[:500]}
                      for r in rows])
    if name == "ai_search":
        from ..integrations.search.service import search

        res = search(cfg, str(args.get("q", "")), scope=str(args.get("scope") or "pool"), n=int(args.get("n") or 15))
        return _text({"intent": res["intent"], "results": [{"title": r["title"], "score": r["score"], "reason": r["reason"],
                      "venue": f"{r.get('venue_name') or ''} {r.get('year') or ''}".strip(), "url": r.get("url_abs")}
                      for r in res["results"]], "usd": res["usd"]})
    if name == "ask_paper":
        from ..integrations.ask import ask

        item = ask(cfg, str(args["paper_id"]), str(args["question"]))
        return _text(item["a"] + f"\n\n（模型 {item['model']}，约 ${item['usd']:.3f}）")
    return {**_text(f"未知工具 {name}"), "isError": True}


def handle(cfg: AppConfig, msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # 通知（如 notifications/initialized）不回复
        return None
    try:
        if method == "initialize":
            result: Any = {"protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL,
                           "capabilities": {"tools": {}}, "serverInfo": {"name": "dailypaper", "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                result = call_tool(cfg, p.get("name", ""), p.get("arguments") or {})
            except Exception as e:  # noqa: BLE001 - 工具出错作为结果返回给模型
                result = {**_text(f"{type(e).__name__}: {e}"), "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def serve_stdio(cfg: AppConfig) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    for raw in sys.stdin.buffer:  # 按字节读写，避免 Windows 控制台编码把中文弄乱
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(cfg, msg)
        if reply is not None:
            sys.stdout.buffer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
