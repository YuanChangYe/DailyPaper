"""网页「提示词与 Skill」用：读写 prompts/ 下的文件，带程序校验、LLM 审核、版本历史、恢复默认、预览。

可编辑的文件（id 就是相对 prompts/ 的路径）：
- ``base/*.j2``      精读与筛选的 Jinja2 模板
- ``lenses/*.md``    论文类型 lens 与阅读 skill lens（Markdown，可带 frontmatter）
- ``schema.yaml``    笔记结构（元信息字段与各节标题）

保存前的程序校验保证输出仍能被网页渲染：模板能渲染、精读模板仍要求 JSON 元信息和 schema 里的全部小节；
schema 的元信息字段名不能少（程序和网页依赖它们）。每次保存把旧版本放进 ``prompts/.history/``。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml as pyyaml

from ..core.config import AppConfig, NoteSchema, load_schema
from ..core.io import split_frontmatter
from ..core.models import Candidate, Paper, ScreenResult

HISTORY_KEEP = 20
REQUIRED_META_KEYS = ("tldr", "core_insight", "value_score", "worth_reading")  # 程序与网页依赖的字段名
_SAFE_ID = re.compile(r"^(base/[A-Za-z0-9_.-]+\.j2|lenses/[A-Za-z0-9_-]+\.md|schema\.yaml)$")


def prompts_dir(cfg: AppConfig) -> Path:
    return cfg.path("prompts")


def _check_id(pid: str) -> str:
    if not _SAFE_ID.match(pid):
        raise ValueError(f"不允许的文件：{pid}")
    return pid


def kind_of(pid: str) -> str:
    return "schema" if pid == "schema.yaml" else "base" if pid.startswith("base/") else "lens"


def _history_dir(cfg: AppConfig) -> Path:
    return prompts_dir(cfg) / ".history"


def _hist_prefix(pid: str) -> str:
    return pid.replace("/", "__")


def _meta_path(cfg: AppConfig) -> Path:
    return _history_dir(cfg) / "meta.json"


def _read_meta(cfg: AppConfig) -> dict:
    p = _meta_path(cfg)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _write_meta(cfg: AppConfig, data: dict) -> None:
    _history_dir(cfg).mkdir(parents=True, exist_ok=True)
    _meta_path(cfg).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_path(cfg: AppConfig, pid: str) -> Path:
    return prompts_dir(cfg) / "defaults" / pid


def _title_of(cfg: AppConfig, pid: str, text: str) -> tuple[str, str]:
    k = kind_of(pid)
    if k == "schema":
        return "笔记结构（schema.yaml）", "元信息字段与 13 节标题、每节的要求；精读提示词按它生成"
    if k == "base":
        names = {"deep_read.system.md.j2": ("精读 · 系统提示", "全局规则与输出格式；各节标题来自 schema.yaml"),
                 "deep_read.user.md.j2": ("精读 · 用户消息", "论文元数据与全文的排版"),
                 "screen.system.md.j2": ("筛选 · 系统提示", "摘要级筛选的任务说明与打分标准"),
                 "screen.user.md.j2": ("筛选 · 用户消息", "一批论文的摘要如何交给模型"),
                 "ask.system.md.j2": ("问答 · 系统提示", "论文页问答：回答规则；全文、笔记、研究方向放在这里"),
                 "ask.user.md.j2": ("问答 · 用户消息", "问题、选中的引文和之前几轮问答"),
                 "search_expand.system.md.j2": ("检索 · 扩展关键词", "把问题改写成英文关键词和 arXiv 查询式"),
                 "search_expand.user.md.j2": ("检索 · 扩展关键词（用户消息）", "问题本身"),
                 "search_rerank.system.md.j2": ("检索 · 重排打分", "按和研究方向的相关度给候选论文打分、写理由"),
                 "search_rerank.user.md.j2": ("检索 · 重排打分（用户消息）", "候选论文 JSON"),
                 "search_report.system.md.j2": ("检索 · 深入综述", "方法分组、对比表、研究空白、follow-up idea"),
                 "search_report.user.md.j2": ("检索 · 深入综述（用户消息）", "检索问题与论文列表")}
        return names.get(pid.split("/")[-1], (pid, ""))
    fm, _ = split_frontmatter(text)
    return str(fm.get("title") or pid.split("/")[-1]), str(fm.get("description") or "")


def list_prompts(cfg: AppConfig) -> list[dict]:
    root = prompts_dir(cfg)
    meta = _read_meta(cfg)
    out = []
    files = sorted(root.glob("base/*.j2")) + sorted(root.glob("lenses/*.md")) + [root / "schema.yaml"]
    lens_ids = {s.path: s for s in cfg.lenses.catalog if s.path}
    for f in files:
        if not f.exists():
            continue
        pid = f.relative_to(root).as_posix()
        text = f.read_text(encoding="utf-8")
        title, desc = _title_of(cfg, pid, text)
        dp = default_path(cfg, pid)
        spec = lens_ids.get(f"prompts/{pid}")
        out.append({
            "id": pid, "kind": kind_of(pid), "title": title, "description": desc, "size": len(text),
            "has_default": dp.exists(), "is_default": dp.exists() and dp.read_text(encoding="utf-8") == text,
            "history": len(list(_history_dir(cfg).glob(f"{_hist_prefix(pid)}.*"))) if _history_dir(cfg).exists() else 0,
            "reviewed": (meta.get(pid) or {}).get("reviewed"),
            "saved_at": (meta.get(pid) or {}).get("saved_at"),
            "lens_id": spec.id if spec else None,
            "lens_enabled": spec.enabled if spec else None,
        })
    return out


def read_prompt(cfg: AppConfig, pid: str) -> dict:
    _check_id(pid)
    f = prompts_dir(cfg) / pid
    if not f.exists():
        raise FileNotFoundError(pid)
    text = f.read_text(encoding="utf-8")
    dp = default_path(cfg, pid)
    title, desc = _title_of(cfg, pid, text)
    return {"id": pid, "kind": kind_of(pid), "title": title, "description": desc, "content": text,
            "default": dp.read_text(encoding="utf-8") if dp.exists() else None, "history": history(cfg, pid)}


# ---------------------------------------------------------------- 程序校验


def _sample_vars(cfg: AppConfig, schema: NoteSchema | None = None) -> dict[str, Any]:
    paper = Paper(id="0000.00000", title="Sample Paper", abstract="An abstract.", authors=["A", "B"], categories=["cs.CL"])
    return {
        "topics": cfg.topics, "language": cfg.settings.language, "lenses": [],
        "schema": schema or cfg.note_schema, "include_deep": True, "web_search": False,
        "paper": paper, "screen": ScreenResult(primary_topic="x", paper_type="method", reason="r"),
        "topics_hit": ["x"], "fulltext": "BODY", "fulltext_loader": "abstract", "grounding": "source-limited",
        "papers": [{"id": paper.id, "title": paper.title, "abstract": paper.abstract}], "lens_catalog": {},
        "batch": [paper], "candidates": [Candidate(paper=paper)],
        # 问答与检索模板用到的变量
        "profile": "", "note": "", "focus_topics": cfg.topics[:1], "question": "Q?", "quote": "", "history": [],
        "query": "Q", "intent": "", "payload": "{}", "count": 1,
    }


def validate_prompt(cfg: AppConfig, pid: str, content: str) -> list[str]:
    """返回问题列表；空表示通过。"""
    _check_id(pid)
    k = kind_of(pid)
    problems: list[str] = []
    if not content.strip():
        return ["内容为空"]
    if k == "schema":
        try:
            data = pyyaml.safe_load(content) or {}
            schema = NoteSchema.model_validate(data)
        except Exception as e:  # noqa: BLE001
            return [f"YAML 或结构不合法：{e}"]
        keys = [m.key for m in schema.meta]
        for req in REQUIRED_META_KEYS:
            if req not in keys:
                problems.append(f"meta 缺少字段 {req}（程序和网页依赖它，标签 label 可以改，key 不能删）")
        if not schema.sections:
            problems.append("sections 为空")
        ids = [s.id for s in schema.sections]
        titles = [s.title.strip() for s in schema.sections]
        if len(set(ids)) != len(ids):
            problems.append("sections 的 id 重复")
        if len(set(titles)) != len(titles):
            problems.append("sections 的 title 重复，网页目录无法区分")
        if any(not t for t in titles):
            problems.append("有小节没有 title")
        return problems
    if k == "lens":
        try:
            fm, body = split_frontmatter(content)
        except Exception as e:  # noqa: BLE001
            return [f"frontmatter 解析失败：{e}"]
        if not body.strip():
            problems.append("正文为空")
        if not fm.get("title"):
            problems.append("建议在 frontmatter 写 title（网页和提示词里显示的名称）")
        return problems
    # base 模板：能渲染；精读系统提示要包含 JSON 元信息与全部小节
    from jinja2 import DictLoader, Environment, StrictUndefined, TemplateSyntaxError

    env = Environment(loader=DictLoader({"t": content}), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    try:
        rendered = env.get_template("t").render(**_sample_vars(cfg))
    except TemplateSyntaxError as e:
        return [f"Jinja2 语法错误（第 {e.lineno} 行）：{e.message}"]
    except Exception as e:  # noqa: BLE001
        return [f"模板渲染失败：{e}"]
    if pid.endswith("deep_read.system.md.j2"):
        if "```json" not in rendered:
            problems.append("精读提示词必须要求先输出 ```json 元信息块，否则网页列表没有一句话总结和价值评分")
        for key in REQUIRED_META_KEYS:
            if f'"{key}"' not in rendered:
                problems.append(f"JSON 元信息块缺少字段 {key}")
        for n, s in cfg.note_schema.numbered(True):
            if f"## {n}. {s.title}" not in rendered:
                problems.append(f"没有输出第 {n} 节「{s.title}」的标题（模板应按 schema.sections 循环生成）")
    if pid.endswith("screen.system.md.j2") and "json" not in rendered.lower():
        problems.append("筛选提示词需要要求 JSON 输出（排序阶段按 JSON 解析）")
    return problems


# ---------------------------------------------------------------- 保存 / 历史 / 恢复


def history(cfg: AppConfig, pid: str) -> list[dict]:
    d = _history_dir(cfg)
    if not d.exists():
        return []
    items = []
    for f in sorted(d.glob(f"{_hist_prefix(pid)}.*"), reverse=True):
        ts = f.suffix.lstrip(".")
        try:
            when = datetime.strptime(ts, "%Y%m%d%H%M%S").isoformat(timespec="seconds")
        except ValueError:
            when = ts
        items.append({"version": ts, "saved_at": when, "size": f.stat().st_size})
    return items


def _backup(cfg: AppConfig, pid: str) -> None:
    f = prompts_dir(cfg) / pid
    if not f.exists():
        return
    d = _history_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    target = d / f"{_hist_prefix(pid)}.{ts}"
    if target.exists():  # 同一秒内多次保存
        target = d / f"{_hist_prefix(pid)}.{ts}{datetime.now().microsecond:06d}"
    shutil.copy2(f, target)
    old = sorted(d.glob(f"{_hist_prefix(pid)}.*"))
    for stale in old[:-HISTORY_KEEP]:
        stale.unlink(missing_ok=True)


def _after_write(cfg: AppConfig, pid: str) -> None:
    if pid == "schema.yaml":
        cfg.note_schema = load_schema(cfg.root)


def write_prompt(cfg: AppConfig, pid: str, content: str, reviewed: bool | None = None, force: bool = False) -> list[str]:
    _check_id(pid)
    problems = validate_prompt(cfg, pid, content)
    hard = [p for p in problems if not p.startswith("建议")]
    if hard and not force:
        raise ValueError("；".join(hard))
    f = prompts_dir(cfg) / pid
    f.parent.mkdir(parents=True, exist_ok=True)
    _backup(cfg, pid)
    f.write_text(content.replace("\r\n", "\n"), encoding="utf-8", newline="\n")  # 保持 LF，避免 Windows 上产生整文件 diff
    meta = _read_meta(cfg)
    meta[pid] = {"saved_at": datetime.now().isoformat(timespec="seconds"), "reviewed": reviewed}
    _write_meta(cfg, meta)
    _after_write(cfg, pid)
    msgs = [f"已保存 prompts/{pid}"]
    if problems and force:
        msgs.append("（忽略了校验提示）")
    return msgs


def restore(cfg: AppConfig, pid: str, version: str) -> list[str]:
    _check_id(pid)
    src = _history_dir(cfg) / f"{_hist_prefix(pid)}.{version}"
    if not src.exists() or not re.match(r"^[0-9]+$", version):
        raise ValueError("没有这个历史版本")
    return write_prompt(cfg, pid, src.read_text(encoding="utf-8"), reviewed=None, force=True)[:1] + [f"已恢复到 {version}"]


def reset_default(cfg: AppConfig, pid: str) -> list[str]:
    _check_id(pid)
    dp = default_path(cfg, pid)
    if not dp.exists():
        raise ValueError("这个文件没有内置默认版本")
    return write_prompt(cfg, pid, dp.read_text(encoding="utf-8"), reviewed=True, force=True)[:1] + ["已恢复默认"]


def delete_prompt(cfg: AppConfig, pid: str) -> list[str]:
    _check_id(pid)
    if kind_of(pid) != "lens":
        raise ValueError("只能删除 lenses/ 下的文件")
    f = prompts_dir(cfg) / pid
    if not f.exists():
        raise ValueError("文件不存在")
    _backup(cfg, pid)
    f.unlink()
    msgs = [f"已删除 prompts/{pid}（历史里还有备份）"]
    stale = [s.id for s in cfg.lenses.catalog if s.path == f"prompts/{pid}"]
    if stale:
        from .lenses_editor import read_lenses_view, write_lenses

        view = read_lenses_view(cfg)
        body = {k: view[k] for k in ("budget_chars", "max_lenses", "llm_select")}
        body["catalog"] = [{**{k: v for k, v in c.items() if k in ("id", "adapter", "path", "priority", "sections", "enabled", "options")},
                            "title": c["title_raw"], "description": c["description_raw"]} for c in view["catalog"] if c["id"] not in stale]
        body["rules"] = [{"when": r["when"], "use": [u for u in r["use"] if u not in stale]} for r in view["rules"]]
        write_lenses(cfg, body)
        msgs.append(f"已从 lenses.yaml 移除 {', '.join(stale)}")
    return msgs


# ---------------------------------------------------------------- LLM 审核

REVIEW_SYSTEM = """你是提示词审核员。用户修改了一个论文精读系统里的提示词或配置文件，你要检查改动后程序和网页是否还能正常工作，并给出改进建议。
只输出一个 JSON 对象：{"ok": true/false, "issues": ["会导致功能失效的问题……"], "suggestions": ["可选的改进建议……"]}。
issues 只写会破坏程序约定的硬性问题；措辞、风格类意见放 suggestions。没有问题时 issues 为空数组。用中文。"""

CHECKLISTS = {
    "base": """检查清单（精读 / 筛选模板，Jinja2）：
1. 精读系统提示是否仍要求模型先输出一个 ```json 代码块，且字段名正好是 tldr、core_insight、value_score、worth_reading（这些字段名被程序和网页读取，不能改名或删除；显示标签可以改）。
2. 是否仍按 schema.sections 循环输出 "## 编号. 标题" 的二级标题，且告诉模型标题文字保持不变、不要增删合并小节（网页目录和校验依赖这些标题）。
3. 是否删掉了证据纪律（原文出处标记、区分作者说法与推断）或"不编造"的要求。
4. 是否引入了与网页渲染冲突的格式要求：一级标题、HTML、非 Markdown 输出、把 JSON 放在正文之后等。
5. 是否引用了模板里不存在的变量（可用变量：topics、language、lenses、schema、include_deep、web_search、paper、screen、topics_hit、fulltext、fulltext_loader、grounding）。
6. 筛选模板是否仍要求 JSON 输出，且字段与原版一致。""",
    "lens": """检查清单（lens，Markdown）：
1. 内容是否只是分析要点和检查清单，而没有要求改变输出结构（新增小节、改标题、输出文件、调用脚本或工具）。
2. 是否与证据纪律冲突（例如要求凭印象判断 novelty、省略出处）。
3. 篇幅是否合理（建议 3000 字符以内，过长会被预算截断）。
4. frontmatter 是否有 title 和 description。""",
    "schema": """检查清单（schema.yaml，笔记结构）：
1. meta 里必须保留 key 为 tldr、core_insight、value_score、worth_reading 的四个字段（label 可改）。
2. sections 的 id 和 title 唯一、非空；deep: true 的节应放在最后、连续。
3. 每节的 hint 是否清楚地说明了要写什么；是否有两节内容重复。
4. 整体是否仍符合"先重建思路、再看方法与证据、最后站在审稿人和后来者的角度"的阅读逻辑。""",
}


def llm_review(cfg: AppConfig, pid: str, content: str, provider_override: str | None = None) -> dict:
    from datetime import date

    from ..core.context import RunContext
    from ..llm import get_llm
    from ..llm.client import extract_json

    _check_id(pid)
    k = kind_of(pid)
    ctx = RunContext(config=cfg, run_date=date.today(), pipeline="review", provider_override=provider_override)
    llm = get_llm(ctx, "screen")
    user = f"{CHECKLISTS[k]}\n\n当前 schema.yaml 的小节标题：{[s.title for s in cfg.note_schema.sections]}\n\n待审核文件 prompts/{pid} 的内容：\n<file>\n{content}\n</file>"
    resp = llm.complete(REVIEW_SYSTEM, user, tag="prompt_review", json_mode=True)
    try:
        data = extract_json(resp.text)
    except Exception:  # noqa: BLE001
        data = {"ok": False, "issues": ["模型没有返回合法的 JSON"], "suggestions": [resp.text[:500]]}
    return {"ok": bool(data.get("ok", False)) and not data.get("issues"), "issues": list(data.get("issues") or []),
            "suggestions": list(data.get("suggestions") or []), "model": llm.model}


# ---------------------------------------------------------------- 预览与 lens 模拟


def candidate_from_index(cfg: AppConfig, paper_id: str) -> Candidate:
    """用站点索引里的论文造一个 Candidate（方向、类型、分数），全文用缓存，没有就用摘要。"""
    from ..core.models import FulltextInfo
    from ..integrations.site.contract import SiteContract

    e = next((x for x in SiteContract(cfg).load_index() if x.id == paper_id), None)
    if e is None:
        raise ValueError(f"索引里没有 {paper_id}")
    paper = Paper(id=e.id, source=e.source, title=e.title, abstract=e.abstract, authors=e.authors, url_abs=e.url_abs, url_pdf=e.url_pdf)
    screen = ScreenResult(topic_scores={t: 10.0 - i for i, t in enumerate(e.topics)}, primary_topic=e.topics[0] if e.topics else "",
                          paper_type=e.paper_type or "method", novelty=2, reason="（预览）")
    c = Candidate(paper=paper, screen=screen, score=e.score, selected=True)
    ft = cfg.cache_dir / "fulltext" / f"{paper.slug}.md"
    if ft.exists():
        c.fulltext = FulltextInfo(path=str(ft), loader="cache", grounding="structure-grounded", chars=ft.stat().st_size)
    return c


def preview(cfg: AppConfig, paper_id: str, overrides: dict[str, str] | None = None, max_user_chars: int = 30000) -> dict:
    from jinja2 import ChoiceLoader, DictLoader

    from ..prompting.composer import PromptComposer
    from ..prompting.selector import LensCatalog, select_lenses

    c = candidate_from_index(cfg, paper_id)
    composer = PromptComposer(cfg)
    schema = cfg.note_schema
    if overrides:
        tpl = {k: v for k, v in overrides.items() if k.startswith("base/")}
        if tpl:
            composer.env.loader = ChoiceLoader([DictLoader(tpl), composer.env.loader])
        if "schema.yaml" in overrides:
            schema = NoteSchema.model_validate(pyyaml.safe_load(overrides["schema.yaml"]) or {})
    lenses = select_lenses(LensCatalog(cfg), c)
    fulltext = Path(c.fulltext.path).read_text(encoding="utf-8") if c.fulltext else f"[Abstract]\n{c.paper.abstract}"
    role = cfg.llm.roles.get("read")
    pv = cfg.llm.providers.get(role.provider) if role else None
    p = composer.compose("deep_read", lenses, paper=c.paper, screen=c.screen, topics_hit=c.main_topics(), fulltext=fulltext,
                         fulltext_loader=c.fulltext.loader if c.fulltext else "abstract",
                         grounding=c.fulltext.grounding if c.fulltext else "source-limited",
                         schema=schema, include_deep=True, web_search=bool(role and role.web_search and pv and pv.type == "anthropic"))
    user = p.user if len(p.user) <= max_user_chars else p.user[:max_user_chars] + f"\n\n[……预览截断，共 {len(p.user)} 字符]"
    return {"paper_id": paper_id, "title": c.paper.title, "system": p.system, "user": user, "lenses": p.lenses, "dropped": p.dropped_lenses,
            "chars": {"system": len(p.system), "user": len(p.user)}, "fulltext": c.fulltext.loader if c.fulltext else "abstract"}


def simulate_lenses(cfg: AppConfig, paper_id: str) -> dict:
    from ..prompting.composer import PromptComposer
    from ..prompting.selector import LensCatalog, select_lenses

    c = candidate_from_index(cfg, paper_id)
    lenses = select_lenses(LensCatalog(cfg), c)
    kept, dropped = PromptComposer(cfg).fit_budget(lenses, cfg.lenses.budget_chars)
    return {"paper_id": paper_id, "title": c.paper.title, "topics": c.main_topics(), "paper_type": c.screen.paper_type, "score": c.score,
            "lenses": [{"id": x.id, "title": x.title, "priority": x.priority, "chars": len(x.content), "sections": x.sections,
                        "truncated": x.content.endswith("（因篇幅截断）")} for x in kept],
            "dropped": dropped, "budget_chars": cfg.lenses.budget_chars}
