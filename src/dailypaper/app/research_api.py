"""本地助手里「读论文、问论文、找论文」相关的接口（从 server.py 拆出来，保持主文件可读）。

- ``GET  /api/pdf-anchors/{id}``   PDF 出处定位表（§5.2 → 第 7 页），网页把笔记里的出处变成可点的跳页按钮
- ``POST /api/app/restart``        重启本地助手（网页发现后端版本旧时一键重启）
- ``/api/ask*``                    论文问答（历史、提问、流式提问、删除）
- ``/api/search*``                 AI 检索（检索、历史、深入综述、BibTeX）
- ``/api/research-profile``、``/api/follow`` 课题档案、关注的作者 / 机构
- ``/api/venues/calendar``         会议日程（即将召开、截稿）
- ``/api/lookup``                  书签小工具：按论文网址找已有笔记或论文池条目
- ``/api/estimate``                点「精读」「综述」之前的费用预估
- ``/api/lan``、``/api/notify/*``、``/api/schedule/github``  局域网模式、完成提醒、暂停云端定时任务
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from threading import Timer

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ..core.config import AppConfig

# 网页和后端约定的接口版本：网页需要的比后端提供的新，就提示「本地助手需要重启」
API_LEVEL = 2


class AskBody(BaseModel):
    paper_id: str
    question: str
    quote: str = ""
    deep: bool = False
    web: bool = False


class SearchBody(BaseModel):
    q: str
    scope: str = "pool"
    venues: list[str] = []
    years: list[int] = []
    ccf: list[str] = []
    core: list[str] = []
    n: int = 20
    refresh: bool = False


class ReportBody(BaseModel):
    key: str
    n: int = 25


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def restart_command(cfg: AppConfig, port: int) -> list[str]:
    """2 秒后用同样的参数重新启动本地助手（等当前进程退出、端口释放）。"""
    launch = [sys.executable, "-m", "dailypaper.cli", "--root", str(cfg.root), "app", "--no-browser", "--port", str(port)]
    code = "import subprocess, sys, time; time.sleep(2); subprocess.Popen(sys.argv[1:])"
    return [sys.executable, "-c", code, *launch]


def mount(app: FastAPI, cfg: AppConfig, runner=None) -> None:
    @app.get("/api/pdf-anchors/{paper_id:path}")
    def pdf_anchors(paper_id: str):
        from ..integrations.pdf_anchors import load_anchors
        from .papers import PaperNotFound, ensure_pdf

        try:
            path = ensure_pdf(cfg, paper_id)
            return load_anchors(path)
        except PaperNotFound as e:
            raise HTTPException(400, str(e)) from e
        except ImportError as e:
            raise HTTPException(501, "需要安装 pymupdf：pip install dailypaper[pdf]") from e
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e

    # ------------------------------------------------------------ 论文问答
    @app.get("/api/ask")
    def ask_history(paper_id: str, fetch_pdf: bool = True):
        from ..integrations.ask import AskError, context_summary, load_history, paper_context

        try:
            c = paper_context(cfg, paper_id, fetch_pdf=fetch_pdf)
        except AskError as e:
            raise HTTPException(404, str(e)) from e
        items = load_history(cfg, paper_id)
        return {"items": items, "context": context_summary(cfg, c), "usd_total": round(sum(i.get("usd", 0) for i in items), 4)}

    @app.post("/api/ask")
    def ask_once(body: AskBody):
        from ..integrations.ask import AskError, ask
        from ..llm.base import LLMError

        try:
            return {"item": ask(cfg, body.paper_id, body.question, quote=body.quote, deep=body.deep, web=body.web)}
        except AskError as e:
            raise HTTPException(400, str(e)) from e
        except LLMError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/ask/stream")
    def ask_stream(body: AskBody):
        """Server-Sent Events：{"delta": 文字片段} … 最后 {"done": true, "item": …} 或 {"error": …}。
        网页断开（点了取消）时停止生成，这一问不保存。"""
        from ..integrations.ask import AskError, ask
        from ..llm.base import Cancelled, LLMError

        q: queue.Queue = queue.Queue()
        stop = threading.Event()

        def on_delta(piece: str) -> None:
            if stop.is_set():
                raise Cancelled()
            q.put({"delta": piece})

        def work() -> None:
            try:
                item = ask(cfg, body.paper_id, body.question, quote=body.quote, deep=body.deep, web=body.web, on_delta=on_delta)
                q.put({"done": True, "item": item})
            except Cancelled:
                q.put({"error": "已取消"})
            except (AskError, LLMError) as e:
                q.put({"error": str(e)})
            except Exception as e:  # noqa: BLE001
                q.put({"error": f"{type(e).__name__}: {e}"})
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def gen():
            try:
                yield _sse({"start": True})
                while True:
                    msg = q.get()
                    if msg is None:
                        return
                    yield _sse(msg)
            finally:
                stop.set()  # 客户端断开：生成器被关闭，通知后台线程停止

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.delete("/api/ask")
    def ask_delete(paper_id: str, item_id: str):
        from ..integrations.ask import delete_item

        if not delete_item(cfg, paper_id, item_id):
            raise HTTPException(404, "没有这条问答")
        return {"ok": True}

    # ------------------------------------------------------------ AI 检索
    @app.post("/api/search")
    def search_ep(body: SearchBody):
        from ..integrations.ask import BudgetExceeded
        from ..integrations.search.service import SearchError, search
        from ..llm.base import LLMError

        try:
            return search(cfg, body.q, scope=body.scope, venues=body.venues, years=body.years, ccf=body.ccf, core=body.core,
                          n=body.n, refresh=body.refresh)
        except (SearchError, BudgetExceeded) as e:
            raise HTTPException(400, str(e)) from e
        except LLMError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/search/history")
    def search_history():
        from ..integrations.search.service import load_history

        return {"items": load_history(cfg)}

    @app.get("/api/search/result")
    def search_result(key: str):
        from ..integrations.search.service import load_cached

        hit = load_cached(cfg, key)
        if not hit:
            raise HTTPException(404, "这次检索的结果已过期，请重新检索")
        return {**hit, "cached": True}

    @app.post("/api/search/report")
    def search_report(body: ReportBody):
        from ..integrations.ask import BudgetExceeded
        from ..integrations.search.service import SearchError, report
        from ..llm.base import LLMError

        try:
            return report(cfg, body.key, n=body.n)
        except (SearchError, BudgetExceeded) as e:
            raise HTTPException(400, str(e)) from e
        except LLMError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/search/bibtex")
    def search_bibtex(key: str, ids: str = ""):
        from ..integrations.search.service import load_cached, to_bibtex

        hit = load_cached(cfg, key)
        if not hit:
            raise HTTPException(404, "这次检索的结果已过期，请重新检索")
        want = {x for x in ids.split(",") if x}
        items = [r for r in hit["results"] if not want or r["id"] in want]
        return PlainTextResponse(to_bibtex(items), media_type="application/x-bibtex; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="dailypaper-search.bib"'})

    # ------------------------------------------------------------ 课题档案、关注
    @app.get("/api/research-profile")
    def profile_get():
        from ..integrations.research_profile import MAX_CHARS, load_profile_text

        return {"text": load_profile_text(cfg), "max_chars": MAX_CHARS}

    @app.put("/api/research-profile")
    def profile_put(body: dict):
        from ..integrations.research_profile import load_profile_text, save_profile_text

        try:
            save_profile_text(cfg, str(body.get("text", "")))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"text": load_profile_text(cfg), "messages": ["课题档案已保存，之后的精读、问答和检索都会参考它"]}

    @app.get("/api/follow")
    def follow_get():
        from ..integrations.follow import load_follow

        return load_follow(cfg)

    @app.put("/api/follow")
    def follow_put(body: dict):
        from ..integrations.follow import save_follow

        try:
            data = save_follow(cfg, body)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, f"格式不对：{e}") from e
        return {**data, "messages": ["关注列表已保存"]}

    # ------------------------------------------------------------ 会议日程、书签小工具、费用预估
    @app.get("/api/venues/calendar")
    def venues_calendar(refresh: bool = False):
        from ..integrations.venues.calendar import calendar

        return {"items": calendar(cfg, refresh=refresh)}

    @app.get("/api/lookup")
    def lookup(url: str):
        """书签小工具：给一个论文网址（arXiv / OpenReview / ACL Anthology / PMLR / CVF），找已有笔记或论文池条目。"""
        import re

        from .papers import index_entry

        m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", url)
        if m:
            e = index_entry(cfg, m.group(1))
            return {"kind": "arxiv", "id": m.group(1), "note_id": e.id if e else "", "title": e.title if e else ""}
        from ..integrations.venues.pool import VenuePool

        stem = re.sub(r"[?#].*$", "", url).rstrip("/").removesuffix(".pdf").removesuffix(".html")
        m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([\w-]+)", url)
        pool = VenuePool(cfg)
        try:
            pat = f"%id={m.group(1)}%" if m else f"%{stem.split('://', 1)[-1]}%"
            row = pool.db.execute("SELECT key, title FROM papers WHERE url_abs LIKE ? OR url_pdf LIKE ? LIMIT 1", (pat, pat)).fetchone()
        finally:
            pool.close()
        if not row:
            return {"kind": "unknown", "id": "", "note_id": "", "title": ""}
        e = index_entry(cfg, row["key"])
        return {"kind": "venue", "id": "venue:" + row["key"], "note_id": e.id if e else "", "title": row["title"]}

    @app.get("/api/estimate")
    def estimate(kind: str = "read", n: int = 1, quick: bool = False):
        """按价格表粗估费用：精读约 3 万输入 token + 输出上限的一半；综述约 2 万输入。没有价格的模型返回 null。"""
        from ..llm.usage import cost_of

        n = max(1, min(n, 50))
        quick_role = "ask" if "ask" in cfg.llm.roles else "screen"
        role_name = {"read": quick_role if quick else "read", "report": "read", "search": quick_role, "ask": quick_role}.get(kind, "read")
        role = cfg.llm.roles.get(role_name)
        if not role:
            return {"usd": None, "model": ""}
        tin, tout = {"read": (12000 if quick else 30000, min(role.max_tokens, 6000 if quick else 12000)),
                     "report": (20000, min(role.max_tokens, 8000)), "search": (25000, 4000), "ask": (25000, 800)}.get(kind, (30000, 8000))
        one = cost_of(cfg, role.model, tin, tout)
        return {"usd": round(one * n, 3) if one is not None else None, "model": f"{role.provider}:{role.model}", "n": n}

    # ------------------------------------------------------------ 局域网模式（只能在本机修改）
    def _local_only(request: Request) -> None:
        from .lan import is_loopback

        if request.client and not is_loopback(request.client.host) and request.client.host != "testclient":
            raise HTTPException(403, "只能在电脑本机上修改局域网设置")

    @app.get("/api/lan")
    def lan_get(request: Request):
        from .lan import lan_ips, load_lan

        _local_only(request)
        lan = load_lan(cfg)
        port = request.url.port or 8765
        listening = getattr(app.state, "lan_listening", None)
        return {"enabled": lan["enabled"], "urls": [f"http://{ip}:{port}/?lan_token={lan['token']}" for ip in lan_ips()] if lan["enabled"] else [],
                "ips": lan_ips(), "needs_restart": listening is not None and listening != lan["enabled"]}

    @app.put("/api/lan")
    def lan_put(request: Request, body: dict):
        from .lan import save_lan

        _local_only(request)
        if not hasattr(app.state, "lan_listening"):
            from .lan import load_lan

            app.state.lan_listening = load_lan(cfg)["enabled"]  # 启动时的状态（决定是否监听 0.0.0.0）
        save_lan(cfg, bool(body.get("enabled")), rotate=bool(body.get("rotate")))
        msgs = ["已更换配对令牌，旧的扫码地址失效"] if body.get("rotate") else []
        msgs.append("局域网模式已打开，重启本地助手后生效" if body.get("enabled") else "局域网模式已关闭，重启本地助手后生效")
        return {**lan_get(request), "messages": msgs}

    # ------------------------------------------------------------ 完成提醒
    @app.get("/api/notify")
    def notify_status():
        from ..plugins.publishers.notify import channels

        stage = next((s for s in cfg.pipeline.pipelines.get("daily", []) if s.use == "publisher.notify"), None)
        return {"channels": channels(), "enabled": bool(stage and stage.enabled)}

    @app.post("/api/notify/test")
    def notify_test():
        from ..plugins.publishers.notify import push, windows_toast

        done = []
        if os.name == "nt":
            try:
                windows_toast("DailyPaper 测试通知", "看到这条，说明每日完成提醒能正常弹出。")
                done.append("Windows 通知")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Windows 通知失败：{e}") from e
        try:
            done += push("DailyPaper 测试通知", "看到这条，说明手机推送配置正确。")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"手机推送失败：{e}") from e
        return {"messages": [f"已发送：{'、'.join(done)}" if done else "没有可用的提醒渠道"]}

    # ------------------------------------------------------------ 暂停云端（GitHub Actions）的定时任务
    @app.get("/api/schedule/github")
    def schedule_github_get():
        """云端定时任务的暂停状态：读仓库变量 DAILYPAPER_PAUSED_UNTIL（需要已登录的 gh）。"""
        import shutil

        gh = shutil.which("gh")
        if not gh:
            return {"available": False, "value": None}
        try:
            r = subprocess.run([gh, "variable", "get", "DAILYPAPER_PAUSED_UNTIL"], cwd=cfg.root, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return {"available": False, "value": None}
        if r.returncode != 0:
            # 变量不存在也会返回非 0；没登录 / 不是 GitHub 仓库时 stderr 里有说明
            missing = "not found" in (r.stderr or "").lower() or "could not find" in (r.stderr or "").lower()
            return {"available": missing, "value": None}
        return {"available": True, "value": r.stdout.strip() or None}

    @app.post("/api/schedule/github")
    def schedule_github(body: dict):
        """把「暂停到某天」同步到仓库变量 DAILYPAPER_PAUSED_UNTIL（daily.yml 第一步检查它）。需要已登录的 gh 命令行。"""
        import shutil

        until = str(body.get("until") or "")
        gh = shutil.which("gh")
        if not gh:
            raise HTTPException(400, "没有找到 gh 命令行。可以在 GitHub 仓库 Settings → Secrets and variables → Actions → Variables "
                                     "手动添加 DAILYPAPER_PAUSED_UNTIL")
        args = [gh, "variable", "set", "DAILYPAPER_PAUSED_UNTIL", "--body", until] if until else [gh, "variable", "delete", "DAILYPAPER_PAUSED_UNTIL"]
        r = subprocess.run(args, cwd=cfg.root, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and until:
            raise HTTPException(502, f"gh 执行失败：{(r.stderr or r.stdout).strip()[:300]}")
        return {"messages": [f"云端定时任务暂停到 {until}" if until else "云端定时任务已恢复"]}

    @app.post("/api/app/restart")
    def restart(request: Request):
        if runner is not None and runner.running:
            raise HTTPException(409, "正在运行任务，等它结束再重启")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(restart_command(cfg, request.url.port or 8765), cwd=cfg.root, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Timer(0.6, lambda: os._exit(0)).start()
        return {"messages": ["本地助手正在重启，几秒后自动刷新页面"]}
