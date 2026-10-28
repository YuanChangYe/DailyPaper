"""本地助手后端（FastAPI）。``dailypaper app`` 启动，浏览器打开 http://127.0.0.1:8765 。

提供：
- ``/``            网页（static/index.html，与 GitHub Pages 上是同一份文件）
- ``/assets``      网页用到的图片等
- ``/site``        内容目录（笔记、api/*.json、feed.xml），网页从 /site/api/ 读数据
- ``/api/...``     审核、Zotero、配置读写、触发运行（接口约定见同目录的 API.md）

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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.config import AppConfig
from ..core.io import read_markdown
from ..integrations.review import ReviewStore
from ..integrations.zotero.service import ZoteroService, collect_obsidian_approvals, import_approved
from .runner import RunManager

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class DecideBody(BaseModel):
    ids: list[str]
    action: str  # approve | later | reject | pending
    collection: str = ""


class RunBody(BaseModel):
    pipeline: str = "daily"
    ids: list[str] = []
    date: str | None = None
    provider: str | None = None  # 例如 fake，用于不花钱试跑


def create_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="DailyPaper 本地助手", docs_url=None, redoc_url=None)
    store = ReviewStore(cfg)
    runner = RunManager(cfg.root, cache_dir=cfg.cache_dir)

    def zotero() -> ZoteroService:
        return ZoteroService.from_config(cfg)

    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=cfg.root, capture_output=True, text=True)
        if r.returncode != 0:
            raise HTTPException(500, f"git {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
        return r.stdout.strip()

    def inside_repo(p: Path) -> bool:
        try:
            return p.resolve().is_relative_to(cfg.root.resolve()) and (cfg.root / ".git").exists()
        except (OSError, ValueError):
            return False

    git_enabled = inside_repo(cfg.content_dir) and inside_repo(cfg.data_dir)

    def commit_and_push(message: str) -> str:
        if not git_enabled:
            return "输出目录不在 git 仓库内（DAILYPAPER_WORKSPACE），无需提交"
        git("add", "--", str(cfg.data_dir), str(cfg.content_dir))
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

    # ------------------------------------------------------------ 页面与静态内容
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    cfg.content_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    app.mount("/site", StaticFiles(directory=cfg.content_dir), name="site")

    # ------------------------------------------------------------ 元信息与审核
    @app.get("/api/meta")
    def meta():
        counts: dict[str, int] = {}
        for it in store.items():
            counts[it.decision.status] = counts.get(it.decision.status, 0) + 1
        return {
            "topics": [{"id": t.id, "name": t.name} for t in cfg.topics],
            "paper_types": ["method", "theory", "benchmark", "empirical", "survey", "position"],
            "counts": counts,
            "site_title": cfg.settings.site.title,
            "repo_url": cfg.settings.site.repo_url,
            "git_enabled": git_enabled,
            "workspace": str(cfg.content_dir.parent),
            "running": runner.running,
        }

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
            raise HTTPException(404, f"笔记文件不存在：{it.entry.note_path}")
        fm, body = read_markdown(path)
        return {"frontmatter": fm, "markdown": body}

    @app.get("/api/pdf/{paper_id}")
    def pdf(paper_id: str):
        """同源提供论文 PDF（缓存在 .cache/pdf/），供网页在分栏里直接阅读。"""
        import re

        import httpx

        from ..core.models import Paper
        from ..integrations.site.contract import SiteContract

        if not re.match(r"^\d{4}\.\d{4,5}$", paper_id):
            raise HTTPException(400, "只支持 arXiv ID")
        entry = next((e for e in SiteContract(cfg).load_index() if e.id == paper_id), None)
        paper = Paper(id=paper_id, title="", url_pdf=(entry.url_pdf if entry else "") or f"https://arxiv.org/pdf/{paper_id}")
        path = cfg.cache_dir / "pdf" / f"{paper.slug}.pdf"
        if not path.exists():
            try:
                r = httpx.get(paper.url_pdf, timeout=120, follow_redirects=True,
                              headers={"User-Agent": "DailyPaper/0.1 (personal research digest)"})
            except httpx.HTTPError as e:
                raise HTTPException(502, f"下载 PDF 失败: {e}") from e
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                raise HTTPException(502, f"arXiv 没有返回 PDF（HTTP {r.status_code}）")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return FileResponse(path, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{paper_id}.pdf"'})

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
        known = {e.id for e in store.entries()}
        unknown = [i for i in body.ids if i not in known]
        if unknown:
            raise HTTPException(404, f"不在审核队列中：{', '.join(unknown)}")
        for pid in body.ids:
            store.decide(pid, status, collection=body.collection, error="")
        messages = []
        if status == "approved":
            messages = list(import_approved(cfg, zotero()))
        if cfg.settings.app.auto_git:
            messages.append(commit_and_push(f"review: {body.action} {len(body.ids)} paper(s)"))
        return {"messages": messages or [f"已标记 {len(body.ids)} 篇为「{status}」"]}

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
        if not git_enabled:
            return {"messages": ["输出目录不在 git 仓库内，无需拉取"]}
        return {"messages": [git("pull", "--rebase", "--autostash") or "已是最新"]}

    @app.post("/api/git/push")
    def git_push():
        return {"messages": [commit_and_push("review: update decisions")]}

    # ------------------------------------------------------------ 搜索设置
    @app.get("/api/config")
    def get_config():
        from .config_editor import read_view

        return read_view(cfg)

    @app.put("/api/config")
    def put_config(body: dict):
        from .config_editor import read_view, write_config

        if not read_view(cfg)["editable"]:
            raise HTTPException(403, "配置文件不可写")
        try:
            return {"messages": write_config(cfg, body)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------ 我的文献库（Zotero 分类）
    @app.get("/api/zotero/library")
    def zotero_library():
        z = zotero()
        if not z.reader:
            raise HTTPException(503, "未配置 Zotero 读取（settings.yaml → zotero.reader）")
        ok, msg = z.reader.available()
        if not ok:
            raise HTTPException(503, msg)
        return {"collections": z.reader.collections()}

    @app.get("/api/zotero/items")
    def zotero_items(collection: str = ""):
        from ..integrations.site.contract import SiteContract

        z = zotero()
        if not z.reader:
            raise HTTPException(503, "未配置 Zotero 读取")
        items = z.reader.items_in_collection(collection or None)
        by_arxiv = {e.id: e for e in SiteContract(cfg).load_index()}
        for it in items:
            e = by_arxiv.get(it["arxiv_id"]) if it["arxiv_id"] else None
            it["paper_id"] = e.id if e else ""
            it["value_score"] = e.value_score if e else None
        return {"items": items}

    # ------------------------------------------------------------ 高亮与批注
    @app.get("/api/annotations/{paper_id}")
    def get_annotations(paper_id: str):
        from ..integrations.annotations import AnnotationStore

        return AnnotationStore(cfg).get(paper_id).model_dump()

    @app.put("/api/annotations/{paper_id}")
    def put_annotations(paper_id: str, body: dict):
        from ..integrations.annotations import Annotations, AnnotationStore

        try:
            data = Annotations.model_validate(body)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"格式不对: {e}") from e
        it = store.get(paper_id)
        return AnnotationStore(cfg).put(paper_id, data, it.entry.note_path if it else None).model_dump()

    @app.get("/api/annotations")
    def annotation_counts():
        from ..integrations.annotations import AnnotationStore

        return AnnotationStore(cfg).all_counts()

    # ------------------------------------------------------------ 趋势
    @app.get("/api/trends")
    def trends(days: int = 7, end: str | None = None):
        from datetime import date as _date

        from ..integrations.trends import cached_summary, stats

        days = max(1, min(days, 365))
        end_d = _date.fromisoformat(end) if end else None
        return {**stats(cfg, days, end_d), "summary": cached_summary(cfg, days, end_d)}

    @app.post("/api/trends/summary")
    def trends_summary(body: dict):
        from datetime import date as _date

        from ..core.context import RunContext
        from ..integrations.trends import generate_summary

        days = max(1, min(int(body.get("days", 7)), 365))
        end_d = _date.fromisoformat(body["end"]) if body.get("end") else None
        ctx = RunContext(config=cfg, run_date=_date.today(), pipeline="trends", provider_override=body.get("provider"))
        try:
            return {"summary": generate_summary(ctx, days, end_d, force=bool(body.get("force")))}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------ 退出（桌面图标启动时没有终端可关）
    @app.post("/api/shutdown")
    def shutdown():
        import os
        from threading import Timer

        Timer(0.6, lambda: os._exit(0)).start()
        return {"messages": ["本地助手正在退出，可以关闭这个页面"]}

    # ------------------------------------------------------------ 触发运行
    @app.post("/api/run")
    def run(body: RunBody):
        extra = ["--provider", body.provider] if body.provider else []
        try:
            started = runner.start(body.pipeline, body.ids, body.date, extra)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        label = f"read {' '.join(body.ids)}" if body.pipeline == "read" else body.pipeline
        return {"run_id": started, "messages": [f"已开始：{label}"]}

    @app.get("/api/run/status")
    def run_status():
        return runner.status()

    app.state.runner = runner
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
