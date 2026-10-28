"""本地助手后端（FastAPI）。``dailypaper app`` 启动，浏览器打开 http://127.0.0.1:8765 。

安全：只监听本机；所有写操作要求自定义请求头 ``X-DailyPaper: 1``（跨站页面无法在不经预检的情况下附带它，
而本服务不开放 CORS），并校验 Host，防止其他网站借你的浏览器调用本地接口。
"""

from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path
from threading import Timer

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.config import AppConfig
from ..core.io import read_markdown
from ..integrations.review import ReviewStore
from ..integrations.zotero.service import ZoteroService, collect_obsidian_approvals, import_approved

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class DecideBody(BaseModel):
    ids: list[str]
    action: str  # approve | later | reject | pending
    collection: str = ""


def create_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="DailyPaper 本地助手", docs_url=None, redoc_url=None)
    store = ReviewStore(cfg)

    def zotero() -> ZoteroService:
        return ZoteroService.from_config(cfg)

    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=cfg.root, capture_output=True, text=True)
        if r.returncode != 0:
            raise HTTPException(500, f"git {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
        return r.stdout.strip()

    def commit_and_push(message: str) -> str:
        git("add", "--", cfg.settings.paths.data_dir, cfg.settings.paths.content_dir)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=cfg.root).returncode == 0:
            return "没有需要提交的变化"
        git("commit", "-m", message)
        git("push")
        return "已提交并推送"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.method != "GET" and request.headers.get("x-dailypaper") != "1":
            return JSONResponse({"detail": "missing X-DailyPaper header"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/items")
    def items(status: str = ""):
        return [{"entry": i.entry.model_dump(), "decision": i.decision.model_dump()} for i in store.items(status or None)]

    @app.get("/api/note/{paper_id}")
    def note(paper_id: str):
        it = store.get(paper_id)
        if not it or not it.entry.note_path:
            raise HTTPException(404, "没有这篇笔记")
        path = cfg.content_dir / it.entry.note_path
        if not path.exists():
            raise HTTPException(404, f"笔记文件不存在：{it.entry.note_path}（可能需要先 git pull）")
        fm, body = read_markdown(path)
        return {"frontmatter": fm, "markdown": body}

    @app.get("/api/zotero/status")
    def zotero_status():
        z = zotero()
        ok, msg = z.writer.available() if z.writer else (False, "未配置写入后端")
        return {
            "ok": ok,
            "message": msg,
            "collections": z.writer.collection_paths() if ok else [],
            "default_collection": cfg.settings.zotero.default_collection,
            "auto_git": cfg.settings.app.auto_git,
        }

    @app.post("/api/decide")
    def decide(body: DecideBody):
        status = {"approve": "approved", "later": "later", "reject": "rejected", "pending": "pending"}.get(body.action)
        if not status:
            raise HTTPException(400, f"未知操作 {body.action}")
        for pid in body.ids:
            store.decide(pid, status, collection=body.collection, error="")
        messages = []
        if status == "approved":
            messages = list(import_approved(cfg, zotero()))
        if cfg.settings.app.auto_git:
            messages.append(commit_and_push(f"review: {body.action} {len(body.ids)} paper(s)"))
        return {"messages": messages}

    @app.post("/api/sync")
    def sync():
        n = collect_obsidian_approvals(cfg)
        messages = ([f"从 Obsidian 收集到 {n} 个入库请求"] if n else []) + list(import_approved(cfg, zotero()))
        return {"messages": messages or ["没有待入库的论文"]}

    @app.post("/api/profile")
    def profile():
        return {"messages": [f"已从 Zotero 导出文库索引与兴趣画像：{zotero().export_profile()}"]}

    @app.post("/api/git/pull")
    def git_pull():
        return {"messages": [git("pull", "--rebase", "--autostash") or "已是最新"]}

    @app.post("/api/git/push")
    def git_push():
        return {"messages": [commit_and_push("review: update decisions")]}

    return app


def serve(cfg: AppConfig, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    if host not in ALLOWED_HOSTS:
        raise SystemExit("本地助手只允许监听 127.0.0.1 / localhost")
    url = f"http://{host}:{port}"
    print(f"DailyPaper 本地助手: {url}  （Ctrl+C 退出）")
    if open_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
