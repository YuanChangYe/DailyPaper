"""命令行入口：``dailypaper <command>``。`dailypaper -h` 查看全部命令。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from .core import registry
from .core.config import AppConfig, find_root, load_config
from .core.context import RunContext
from .core.env import load_dotenv  # 也从 cli 导出：from dailypaper.cli import load_dotenv 仍可用
from .core.pipeline import Pipeline, PipelineError

log = logging.getLogger("dailypaper")




def setup_logging(verbose: bool, log_file: str | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        # 定时任务用：程序自己以 UTF-8 追加写日志，不依赖外层 shell 的编码
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S" if log_file else "%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def say(msg: str) -> None:
    """面向用户的结果行：同时进终端和日志文件。"""
    logging.getLogger("dailypaper.result").info(msg)


def make_ctx(cfg: AppConfig, args, pipeline: str, **inputs) -> RunContext:
    run_date = date.fromisoformat(args.date) if getattr(args, "date", None) else date.today()
    return RunContext(
        config=cfg,
        run_date=run_date,
        pipeline=pipeline,
        dry_run=getattr(args, "dry_run", False),
        provider_override=getattr(args, "provider", None),
        inputs=inputs,
    )


def apply_toggles(cfg: AppConfig, pipeline: str, enable: list[str], disable: list[str]) -> None:
    specs = cfg.pipeline.pipelines.get(pipeline, [])
    known = {s.id for s in specs}
    for sid in [*enable, *disable]:
        if sid not in known:
            raise PipelineError(f"流水线 {pipeline} 中没有 stage {sid}；可用: {sorted(known)}")
    for s in specs:
        if s.id in enable:
            s.enabled = True
        if s.id in disable:
            s.enabled = False


def summarize(items, cfg: AppConfig | None = None) -> None:
    if cfg is not None:
        from .llm.usage import budget_line

        say(budget_line(cfg))
    read = [c for c in items if c.note]
    say(f"完成：候选 {len(items)} 篇，精读 {len(read)} 篇。")
    for c in read:
        say(f"  [{c.note.value_score}/5] {c.paper.title[:80]} → {c.note_path}")
    errs = [(c.id, e) for c in items for e in c.errors if c.selected]
    for pid, e in errs:
        say(f"  ! {pid}: {e}")


# ------------------------------------------------------------------ commands


def cmd_run(cfg: AppConfig, args) -> int:
    if getattr(args, "scheduled", False):
        from .app.schedule import is_paused, paused_until

        if is_paused(cfg):
            say(f"定时运行已暂停到 {paused_until(cfg)}，今天跳过（不调用模型）。在网页「设置 → 运行与费用」或 dailypaper schedule on 恢复。")
            return 0
    apply_toggles(cfg, args.pipeline, args.enable, args.disable)
    ctx = make_ctx(cfg, args, args.pipeline)
    items = Pipeline.from_config(ctx).run(start_from=args.from_stage, stop_after=args.until)
    summarize(items, cfg)
    return 0


def cmd_schedule(cfg: AppConfig, args) -> int:
    from .app import schedule as sch

    if args.action == "time":
        if not args.value:
            print("用法：dailypaper schedule time 14:30")
            return 2
        sch.set_time(args.value)
        print(f"运行时间改为每天 {args.value}")
    elif args.action != "status":
        if args.action == "pause" and not args.value:
            print("用法：dailypaper schedule pause 2026-11-20")
            return 2
        for m in sch.apply(cfg, args.action, until=args.value):
            print(m)
    v = sch.view(cfg, refresh=True)
    label = {"on": "开启", "pause": f"暂停到 {v.get('paused_until')}", "off": "关闭", "unregistered": "未注册"}[v["mode"]]
    print(f"定时运行：{label}" + (f" · 每天 {v.get('time')} · 下次 {v.get('next_run') or '—'} · 上次 {v.get('last_run') or '—'}（结果 {v.get('last_result')}）" if v.get("registered") else ""))
    if not v["supported"]:
        print("（只有 Windows 支持；其他系统请用 cron 调用 dailypaper run --scheduled）")
    return 0


def cmd_read(cfg: AppConfig, args) -> int:
    apply_toggles(cfg, "read", args.enable, args.disable)
    ctx = make_ctx(cfg, args, "read", ids=args.ids, prompt_only=args.prompt_only, quick=args.quick)
    items = Pipeline.from_config(ctx).run()
    if args.prompt_only:
        print(f"prompt 已写入 {cfg.cache_dir / 'prompts'}")
    else:
        summarize(items, cfg)
    return 0


def cmd_check(cfg: AppConfig, args) -> int:
    """运行前自检：配置能否加载、各 LLM 角色的 key 是否存在、插件和模板是否齐全。缺 key 时返回 1。"""
    from .prompting.selector import LensCatalog

    problems: list[str] = []
    print(f"项目目录: {cfg.root}")
    print(f"输出目录: 笔记 {cfg.content_dir} · 状态 {cfg.data_dir} · 缓存 {cfg.cache_dir}")
    pipelines = ", ".join(cfg.pipeline.pipelines) or "（无）"
    print(f"✓ 配置已加载：{len(cfg.topics)} 个研究方向，流水线 {pipelines}")

    provider_override = getattr(args, "provider", None)
    for role_name, role in cfg.llm.roles.items():
        pname = provider_override or role.provider
        spec = cfg.llm.providers.get(pname)
        if spec is None and pname != "fake":
            problems.append(f"llm.yaml：角色 {role_name} 使用的 provider {pname} 不存在")
            continue
        env = spec.api_key_env if spec else None
        if env and not os.environ.get(env) and not (spec.type == "anthropic" and os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            problems.append(
                f"角色 {role_name}（{pname} / {role.model}）需要环境变量 {env}，但它为空。"
                f"本地请写入 .env；GitHub Actions 请在 Settings → Secrets and variables → Actions 添加同名 Secret"
            )
        else:
            print(f"✓ 角色 {role_name}: {pname} / {role.model or '（fake）'}")

    for name in ("deep_read", "screen"):
        for part in ("system", "user"):
            if not (cfg.path("prompts") / "base" / f"{name}.{part}.md.j2").exists():
                problems.append(f"缺少模板 prompts/base/{name}.{part}.md.j2")
    if not cfg.note_schema.sections:
        problems.append("prompts/schema.yaml 缺失或没有 sections，精读提示词无法生成")
    else:
        print(f"✓ 笔记结构 v{cfg.note_schema.version}：{len(cfg.note_schema.sections)} 节")
    cat = LensCatalog(cfg)
    ids = cat.available_ids()
    missing = [i for i in cat.adapters if i not in ids]
    print(f"✓ lens 可用 {len(ids)} 个" + (f"，不可用 {len(missing)} 个（{', '.join(missing)}）将被跳过" if missing else ""))

    for p in problems:
        print(f"✗ {p}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error::{p}")
    return 1 if problems else 0


def cmd_covers(cfg: AppConfig, args) -> int:
    """扫描封面素材目录，生成网页用的三档尺寸和清单。"""
    from .integrations.covers import build_covers, source_dir

    src = source_dir(cfg)
    if not src.exists():
        src.mkdir(parents=True, exist_ok=True)
        print(f"已创建素材目录 {src}，把照片放进去再运行一次。")
        return 0
    r = build_covers(cfg, force=args.force)
    print(f"素材 {src}：共 {r['count']} 张，本次生成 {r['built']}，未变化 {r['skipped']}，清理 {r['removed']} 个旧文件 → {r['output_dir']}")
    for e in r["errors"]:
        print(f"  ! {e['source']}: {e['error']}")
    return 0


def cmd_venues(cfg: AppConfig, args) -> int:
    """顶会论文池：sync 同步 / status 状态。"""
    from .integrations.venues.pool import VenuePool
    from .integrations.venues.sync import sync_venues

    pool = VenuePool(cfg)
    if args.action == "status":
        st = pool.stats()
        print(f"论文池 {st['path']}：共 {st['total']} 篇，已筛选 {st['screened']}，已精读 {st['read']}，上次同步 {st['last_sync'] or '从未'}")
        for v in cfg.venues.venues:
            rows = st["venues"].get(v.id, [])
            flag = "" if v.enabled else "（已停用）"
            desc = "、".join(f"{r['year']}: {r['count']} 篇" for r in rows) or "未同步"
            print(f"  {v.badge:34s} {desc}{flag}")
        return 0
    results = sync_venues(cfg, venues=args.venue or None, years=args.year or None, force=args.force, pool=pool, progress=lambda m: print("  " + m))
    done = [r for r in results if not r.get("skipped")]
    failed = [r for r in done if not r["ok"]]
    print(f"同步完成：{len(done)} 项，新增 {sum(r['new'] for r in done)} 篇，跳过 {len(results) - len(done)} 项（最近同步过），失败 {len(failed)} 项")
    for r in failed:
        print(f"  ! {r['venue']} {r['year']}: {r.get('error', '')}")
    return 1 if failed and not done[len(failed):] else 0


def cmd_list_plugins(cfg: AppConfig, args) -> int:
    for kind, names in registry.available().items():
        print(f"{kind:16s} {', '.join(names)}")
    return 0


def cmd_list_lenses(cfg: AppConfig, args) -> int:
    from .prompting.selector import LensCatalog

    cat = LensCatalog(cfg)
    for lid, adapter in cat.adapters.items():
        ok = "✓" if adapter.available() else "✗"
        print(f"{ok} {lid:28s} [{adapter.spec.adapter}] p={adapter.spec.priority:<3d} {adapter.title()}")
    print("\n✗ = 不可用（skill 未同步或文件缺失）。规则见 config/lenses.yaml。")
    return 0


def cmd_sync_skills(cfg: AppConfig, args) -> int:
    from .prompting.skills_sync import sync_skills

    names = args.only or sorted({Path(s.path).name for s in cfg.lenses.catalog if s.adapter.startswith("skill_") and s.path})
    src = Path(args.source).expanduser()
    result = sync_skills(src, cfg.skills_dir, names)
    for name, n in result.items():
        print(f"{name}: {n} 个文件 → {cfg.skills_dir / name}")
    return 0 if result else 1


def cmd_app(cfg: AppConfig, args) -> int:
    from .app.server import serve

    serve(cfg, host=args.host or cfg.settings.app.host, port=args.port or cfg.settings.app.port, open_browser=not args.no_browser)
    return 0


def cmd_mcp(cfg: AppConfig, args) -> int:
    from .app.mcp_server import serve_stdio

    serve_stdio(cfg)
    return 0


def cmd_search(cfg: AppConfig, args) -> int:
    from .integrations.search.service import search

    res = search(cfg, " ".join(args.q), scope=args.scope, venues=args.venue or None, ccf=args.ccf or None, n=args.n,
                 provider_override=args.provider)
    print(f"{res['intent']}  （候选 {res['candidates']} 篇，约 ${res['usd']:.3f}）")
    for i, r in enumerate(res["results"], 1):
        where = f"{r.get('venue_name') or r['source']} {r.get('year') or ''}".strip()
        print(f"{i:2}. [{r['score']:.0f}] {r['title']}  ·  {where}\n     {r['reason']}\n     {r.get('url_abs') or ''}")
    return 0


def cmd_review(cfg: AppConfig, args) -> int:
    from .integrations.review import ReviewStore
    from .integrations.zotero.service import ZoteroService, import_approved

    store = ReviewStore(cfg)
    for pid in args.approve:
        store.decide(pid, "approved", collection=args.collection or "")
    for pid in args.reject:
        store.decide(pid, "rejected")
    for pid in args.later:
        store.decide(pid, "later")
    if args.approve:
        for line in import_approved(cfg, ZoteroService.from_config(cfg)):
            print(line)
    items = store.items(status=args.status)
    print(f"{len(items)} 篇（{args.status or '全部'}）")
    for it in items:
        e, d = it.entry, it.decision
        print(f"[{d.status:8s}] {e.id}  {'★' * e.value_score:5s} {e.title[:70]}\n           {e.tldr[:100]}")
    return 0


def cmd_zotero_sync(cfg: AppConfig, args) -> int:
    from .integrations.zotero.service import ZoteroService, collect_obsidian_approvals, import_approved

    n = collect_obsidian_approvals(cfg)
    if n:
        print(f"从 Obsidian 笔记收集到 {n} 个入库请求")
    for line in import_approved(cfg, ZoteroService.from_config(cfg)):
        print(line)
    return 0


def cmd_zotero_profile(cfg: AppConfig, args) -> int:
    from .integrations.zotero.service import ZoteroService

    stats = ZoteroService.from_config(cfg).export_profile()
    print(f"已导出：{stats}")
    return 0


def cmd_zotero_check(cfg: AppConfig, args) -> int:
    from .integrations.zotero.service import ZoteroService

    for line in ZoteroService.from_config(cfg).diagnose():
        print(line)
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dailypaper", description="arXiv 每日论文筛选与精读")
    p.add_argument("--root", help="项目根目录（默认自动查找含 config/pipeline.yaml 的目录）")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--log-file", metavar="FILE", help="同时把日志追加写入该文件（UTF-8），供定时任务使用")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, date_arg=True):
        if date_arg:
            sp.add_argument("--date", help="运行日期 YYYY-MM-DD（默认今天）")
        sp.add_argument("--provider", help="所有 LLM 角色改用该 provider，例如 fake")
        sp.add_argument("--dry-run", action="store_true", help="跳过所有 publisher（不写队列、不提交、不推送）")
        sp.add_argument("--enable", action="append", default=[], metavar="STAGE", help="启用某个 stage")
        sp.add_argument("--disable", action="append", default=[], metavar="STAGE", help="禁用某个 stage")

    sp = sub.add_parser("run", help="运行一条流水线（默认 daily）")
    sp.add_argument("--pipeline", default="daily")
    sp.add_argument("--from", dest="from_stage", metavar="STAGE", help="从某个 stage 开始（读取上一步的检查点）")
    sp.add_argument("--until", metavar="STAGE", help="运行到某个 stage 为止")
    sp.add_argument("--scheduled", action="store_true", help="由定时任务调用：处于「暂停到某天」期间直接跳过")
    common(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("schedule", help="定时运行：status 查看 / on 开启 / off 关闭 / pause YYYY-MM-DD 暂停到某天 / time HH:MM 改时间")
    sp.add_argument("action", choices=["status", "on", "off", "pause", "time"])
    sp.add_argument("value", nargs="?", help="pause 的日期或 time 的时间")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("read", help="精读指定论文：arXiv ID 或 Zotero 条目 key")
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--prompt-only", action="store_true", help="只生成 prompt（写到 .cache/prompts/），不调用模型")
    sp.add_argument("--quick", action="store_true", help="快速概览：只写前几节，用便宜的模型（约 $0.01、十几秒）")
    common(sp)
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("app", help="启动本地助手（审核页面 + Zotero 联动）")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_app)

    sp = sub.add_parser("mcp", help="以 MCP 服务器方式运行（stdio），在 Claude Desktop / Claude Code 里查论文库")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("search", help='AI 检索：dailypaper search "LLM agent 的拒答" --scope web')
    sp.add_argument("q", nargs="+")
    sp.add_argument("--scope", choices=["pool", "web", "all"], default="pool")
    sp.add_argument("--venue", action="append", help="只在这些会议里找（可重复）")
    sp.add_argument("--ccf", action="append", help="CCF 等级 A / B（可重复）")
    sp.add_argument("-n", type=int, default=20)
    sp.add_argument("--provider", help="例如 fake，不花钱试跑")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("review", help="命令行审核：列出队列，或批准/拒绝")
    sp.add_argument("--status", default="pending", help="pending / approved / later / rejected / imported / failed，逗号分隔；空字符串=全部")
    sp.add_argument("--approve", nargs="*", default=[], metavar="ID")
    sp.add_argument("--reject", nargs="*", default=[], metavar="ID")
    sp.add_argument("--later", nargs="*", default=[], metavar="ID")
    sp.add_argument("--collection", help="批准时的目标 Zotero 分类")
    sp.set_defaults(func=cmd_review)

    sub.add_parser("zotero-sync", help="把已批准（含 Obsidian 中勾选的）论文写入 Zotero").set_defaults(func=cmd_zotero_sync)
    sub.add_parser("zotero-profile", help="从 Zotero 导出文库索引与兴趣画像到 data/").set_defaults(func=cmd_zotero_profile)
    sub.add_parser("zotero-check", help="检查 Zotero 本地 API / connector / Better BibTeX 是否可用").set_defaults(func=cmd_zotero_check)

    sp = sub.add_parser("sync-skills", help="把本机 Claude skill 复制到仓库 skills/ 目录")
    sp.add_argument("--source", default=str(Path.home() / ".claude" / "skills"))
    sp.add_argument("--only", nargs="*", help="skill 名称；默认同步 lenses.yaml 中引用的全部 skill")
    sp.set_defaults(func=cmd_sync_skills)

    sp = sub.add_parser("check", help="运行前自检：配置、API key、模板、lens")
    sp.add_argument("--provider", help="按该 provider 检查（例如 fake）")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("venues", help="顶会论文池：sync 同步近两年录用论文，status 查看")
    sp.add_argument("action", choices=["sync", "status"])
    sp.add_argument("--venue", action="append", help="只同步这些会议 id（可重复）")
    sp.add_argument("--year", action="append", type=int, help="只同步这些年份（可重复）")
    sp.add_argument("--force", action="store_true", help="忽略 auto_sync_days，全部重新拉取")
    sp.set_defaults(func=cmd_venues)
    sp = sub.add_parser("covers", help="扫描首页封面素材目录（输出目录旁的 covers/），生成尺寸与清单")
    sp.add_argument("--force", action="store_true", help="全部重新生成")
    sp.set_defaults(func=cmd_covers)
    sub.add_parser("list-plugins", help="列出已注册的插件").set_defaults(func=cmd_list_plugins)
    sub.add_parser("list-lenses", help="列出精读 lens 及可用状态").set_defaults(func=cmd_list_lenses)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose, args.log_file)
    if args.log_file:
        log.info("==== dailypaper %s", " ".join(argv if argv is not None else sys.argv[1:]))
    root = Path(args.root).resolve() if args.root else find_root()
    load_dotenv(root / ".env")  # 先读 .env，配置里的 ${ENV} 才能取到值
    cfg = load_config(root)
    try:
        return args.func(cfg, args)
    except PipelineError as e:
        log.error("%s", e)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error::{e}")  # 让错误出现在 Actions 页面的 Annotations 中
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
