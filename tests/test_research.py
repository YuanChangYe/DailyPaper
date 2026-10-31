"""论文页问答、AI 检索、出处定位、关注、局域网、MCP、会议日程与新数据源（全部离线：fake 模型 + 本地样例）。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from dailypaper.core.config import LLMRole
from dailypaper.core.models import Paper
from dailypaper.core.pipeline import Pipeline

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from dailypaper.app.server import create_app  # noqa: E402

H = {"X-DailyPaper": "1"}


@pytest.fixture
def ran(ctx, cfg, monkeypatch):
    """跑一遍 fake 流水线生成索引；所有模型角色改用 fake；不下载 PDF。"""
    Pipeline.from_config(ctx).run()
    for name in ("screen", "read", "ask"):
        cfg.llm.roles[name] = LLMRole(provider="fake", model="fake", max_tokens=2000)
    import dailypaper.app.papers as papers

    def no_pdf(cfg, pid):
        raise RuntimeError("测试不下载 PDF")

    monkeypatch.setattr(papers, "ensure_pdf", no_pdf)
    return cfg


def first_id(cfg) -> str:
    from dailypaper.integrations.site.contract import SiteContract

    return SiteContract(cfg).load_index()[0].id


# ---------------------------------------------------------------- 出处定位
def test_resolve_refs():
    from dailypaper.integrations.pdf_anchors import resolve

    a = {"§5": 6, "§5.2": 7, "Table 2": 10, "Figure 3": 8, "§A": 14}
    assert resolve("§5.2", a) == 7 and resolve("§5.3", a) == 6  # 找不到小节时退到上一级
    assert resolve("Table 2", a) == 10 and resolve("Fig. 3", a) == 8 and resolve("p.4", a) == 4
    assert resolve("附录 A", a) == 14 and resolve("Eq. 4", a) is None


def test_build_anchors_from_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from dailypaper.integrations.pdf_anchors import build_anchors, load_anchors

    doc = pymupdf.open()
    for lines in [["1", "Introduction", "Some text."], ["2", "Method", "2.1", "Policy shift"],
                  ["Table 1: Results on MATH.", "1.0", "Axis label"], ["Appendix A", "Proofs"]]:
        page = doc.new_page()
        for j, line in enumerate(lines):
            page.insert_text((72, 72 + 20 * j), line)
    doc.set_toc([[1, "Introduction", 1], [1, "Method", 2], [2, "Policy shift", 2]])
    path = tmp_path / "x.pdf"
    doc.save(path)
    data = build_anchors(path)
    assert data["pages"] == 4
    a = data["anchors"]
    assert a["§1"] == 1 and a["§2"] == 2 and a["§2.1"] == 2 and a["Table 1"] == 3 and a["§A"] == 4
    assert "§1.0" not in a  # 坐标刻度不是小节
    assert load_anchors(path)["anchors"] == a and path.with_suffix(".anchors.json").exists()


# ---------------------------------------------------------------- 问答
def test_ask_saves_history_and_uses_fallback_context(ran):
    from dailypaper.integrations.ask import ask, delete_item, load_history, paper_context

    pid = first_id(ran)
    c = paper_context(ran, pid)
    assert c["grounding"] in ("fulltext", "abstract") and c["note"]  # 没有 PDF：退回精读时的全文或摘要
    item = ask(ran, pid, "实验设置有什么漏洞？", quote="一段引文")
    assert "实验设置有什么漏洞" in item["a"] and item["quote"] == "一段引文" and item["model"].startswith("fake")
    hist = load_history(ran, pid)
    assert [h["id"] for h in hist] == [item["id"]]
    assert delete_item(ran, pid, item["id"]) and load_history(ran, pid) == []


def test_ask_budget_and_validation(ran, monkeypatch):
    from dailypaper.integrations import ask as ask_mod

    pid = first_id(ran)
    with pytest.raises(ask_mod.AskError):
        ask_mod.ask(ran, pid, "  ")
    ran.llm.monthly_budget_usd = 1.0
    monkeypatch.setattr("dailypaper.llm.usage.month_summary", lambda cfg, month=None: {"usd": 5.0, "calls": 1})
    with pytest.raises(ask_mod.BudgetExceeded):
        ask_mod.ask(ran, pid, "问题")


def test_ask_stream_endpoint(ran):
    client = TestClient(create_app(ran), base_url="http://127.0.0.1")
    pid = first_id(ran)
    r = client.get("/api/ask", params={"paper_id": pid})
    assert r.status_code == 200 and r.json()["context"]["suggestions"]
    with client.stream("POST", "/api/ask/stream", json={"paper_id": pid, "question": "贡献是什么？"}, headers=H) as resp:
        events = [json.loads(line[5:]) for line in resp.iter_lines() if line.startswith("data:")]
    assert events[0] == {"start": True} and any("delta" in e for e in events)
    done = events[-1]
    assert done["done"] and "贡献是什么" in done["item"]["a"]
    assert len(client.get("/api/ask", params={"paper_id": pid}).json()["items"]) == 1
    assert client.post("/api/ask/stream", json={"paper_id": pid, "question": "x"}).status_code == 403  # 缺请求头


# ---------------------------------------------------------------- 检索
def test_search_web_rerank_report_bibtex(ran, monkeypatch):
    from dailypaper.integrations.search import service

    fake = [{"id": f"2610.0000{i}", "source": "arxiv", "title": f"Selective prediction paper {i}", "abstract": "abs",
             "authors": ["A B"], "year": 2026, "venue_name": "arXiv", "ccf": "", "core": "", "url_abs": "u", "url_pdf": "",
             "arxiv_id": f"2610.0000{i}", "doi": "", "readable": True} for i in range(3)]
    monkeypatch.setattr(service, "recall_arxiv", lambda exp, q: fake[:2])
    monkeypatch.setattr(service, "recall_openalex", lambda exp, q: [dict(fake[1], source="openalex"), fake[2]])  # 第二篇重复
    res = service.search(ran, "selective prediction for agents", scope="web", n=10)
    assert res["candidates"] == 3 and len(res["results"]) == 3
    assert all(r["reason"] for r in res["results"]) and res["results"][0]["score"] >= res["results"][-1]["score"]
    again = service.search(ran, "selective prediction for agents", scope="web", n=10)
    assert again["cached"]  # 24 小时内同一问题不再调用模型
    assert service.load_history(ran)[0]["q"] == "selective prediction for agents"
    rep = service.report(ran, res["key"], n=3)
    assert "参考论文" in rep["markdown"] and (ran.data_dir / "reports").exists()
    bib = service.to_bibtex(res["results"])
    assert bib.count("@article") == 3 and "eprint = {2610.00001}" in bib


def test_search_pool_with_level_filter(ran):
    from dailypaper.integrations.search import service
    from dailypaper.integrations.venues.pool import VenuePool

    pool = VenuePool(ran)
    pool.upsert([Paper(id="x1", source="venue", title="Conformal abstention for language agents", abstract="selective prediction")], "acl", 2025)
    pool.upsert([Paper(id="x2", source="venue", title="Conformal prediction for vision", abstract="selective")], "iclr", 2025)
    pool.close()
    res = service.search(ran, "conformal selective", scope="pool", ccf=["A"], n=10)
    titles = [r["title"] for r in res["results"]]
    assert titles and all("language agents" in t for t in titles)  # ICLR 不是 CCF-A，被等级筛掉
    assert res["results"][0]["id"].startswith("venue:") and res["results"][0]["venue_name"] == "ACL"


def test_runner_accepts_venue_ids(tmp_path):
    from dailypaper.app.runner import _VENUE_RE

    assert _VENUE_RE.match("venue:title:abc123") and _VENUE_RE.match("venue:doi:10.18653/v1/x")
    assert not _VENUE_RE.match("venue:x; rm -rf /")


# ---------------------------------------------------------------- 关注、课题档案、外部信号
def test_follow_and_keyword_filter(cfg, ctx):
    from dailypaper.core.models import Candidate
    from dailypaper.integrations.follow import load_follow, matches, save_follow
    from dailypaper.plugins.filters.keyword import KeywordFilter

    save_follow(cfg, {"authors": [{"name": "Chelsea Finn"}, {"name": "Y. LeCun", "openalex": "A1"}], "institutions": ["Stanford"], "bonus": 2})
    f = load_follow(cfg)
    assert f["bonus"] == 2 and matches(f, ["chelsea  finn"]) == ["Chelsea Finn"]
    assert matches(f, ["Someone"], author_ids=["A1"]) == ["Y. LeCun"] and matches(f, [], ["Stanford University"]) == ["Stanford"]
    off_topic = Candidate(paper=Paper(id="2610.1", title="Robot grasping", abstract="no keywords here", authors=["Chelsea Finn"]))
    kept = KeywordFilter(ctx).run([off_topic])
    assert kept and kept[0].paper.extra["followed"] == ["Chelsea Finn"]  # 关注的作者不被关键词预筛掉


def test_profile_api_and_prompt(ran):
    from dailypaper.prompting.composer import PromptComposer

    client = TestClient(create_app(ran), base_url="http://127.0.0.1")
    r = client.put("/api/research-profile", json={"text": "我在做 agent 的拒答"}, headers=H)
    assert r.status_code == 200 and client.get("/api/research-profile").json()["text"] == "我在做 agent 的拒答"
    assert client.put("/api/research-profile", json={"text": "x" * 5000}, headers=H).status_code == 400
    from dailypaper.app.prompts_editor import _sample_vars

    v = _sample_vars(ran) | {"profile": "我在做 agent 的拒答"}
    system, _ = PromptComposer(ran).render("deep_read", **v)
    assert "课题档案" in system and "我在做 agent 的拒答" in system


def test_new_prompts_validate(cfg):
    from dailypaper.app.prompts_editor import list_prompts, validate_prompt

    ids = {p["id"]: p["title"] for p in list_prompts(cfg)}
    for pid in ("base/ask.system.md.j2", "base/search_rerank.system.md.j2", "base/search_report.system.md.j2"):
        assert pid in ids and "·" in ids[pid]
        assert validate_prompt(cfg, pid, (cfg.path("prompts") / pid).read_text(encoding="utf-8")) == []


def test_index_entry_signals(ctx, cfg):
    from dailypaper.integrations.site.contract import SiteContract

    items = Pipeline.from_config(ctx).run()
    c = next(c for c in items if c.note)
    assert c.screen.reason
    e = next(e for e in SiteContract(cfg).load_index() if e.id == c.id)
    assert e.reason == c.screen.reason and e.depth == "full" and e.hidden_gem is False


def test_explore_ranker(ctx):
    from dailypaper.core.models import Candidate, ScreenResult
    from dailypaper.plugins.rankers.explore import ExploreRanker

    ctx.run_date = date(2026, 10, 26)  # 周一
    a = Candidate(paper=Paper(id="a", title="a"), screen=ScreenResult(topic_scores={"t": 4}, novelty=3), score=3)
    b = Candidate(paper=Paper(id="b", title="b"), screen=ScreenResult(topic_scores={"t": 9}, novelty=3), score=9)
    ExploreRanker(ctx).run([a, b])
    assert a.selected and a.meta["explore"] and not b.selected
    ctx.run_date = date(2026, 10, 27)
    c = Candidate(paper=Paper(id="c", title="c"), screen=ScreenResult(topic_scores={"t": 4}, novelty=3))
    ExploreRanker(ctx).run([c])
    assert not c.selected  # 不是设定的星期几


# ---------------------------------------------------------------- 局域网、MCP、用量
def test_lan_guard(cfg):
    from dailypaper.app.lan import load_lan, save_lan

    app = create_app(cfg)
    remote = TestClient(app, base_url="http://192.168.1.20:8765", client=("192.168.1.30", 5000))
    assert remote.get("/api/meta").status_code == 403  # 局域网模式没开
    lan = save_lan(cfg, True)
    assert remote.get("/api/meta").status_code == 403  # 没带令牌
    r = remote.get("/api/meta", params={"lan_token": lan["token"]})
    assert r.status_code == 200 and "dp_lan" in r.headers.get("set-cookie", "")
    assert remote.get("/api/meta").status_code == 200  # 之后靠 Cookie
    public = TestClient(app, base_url="http://192.168.1.20:8765", client=("8.8.8.8", 5000))
    assert public.get("/api/meta", params={"lan_token": lan["token"]}).status_code == 403  # 不是局域网地址
    assert remote.put("/api/lan", json={"enabled": False}, headers=H).status_code == 403  # 只能在本机改
    save_lan(cfg, True, rotate=True)
    assert load_lan(cfg)["token"] != lan["token"]


def test_mcp_protocol(ctx, cfg):
    from dailypaper.app.mcp_server import handle

    Pipeline.from_config(ctx).run()
    init = handle(cfg, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "dailypaper"
    assert handle(cfg, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = handle(cfg, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} >= {"list_papers", "get_note", "pool_search", "ai_search", "ask_paper"}
    out = handle(cfg, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_papers", "arguments": {}}})
    rows = json.loads(out["result"]["content"][0]["text"])
    note = handle(cfg, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_note", "arguments": {"paper_id": rows[0]["id"]}}})
    assert "## 1." in note["result"]["content"][0]["text"]
    assert handle(cfg, {"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601


def test_usage_by_feature(cfg):
    from dailypaper.llm import usage

    cfg.llm.pricing["m"] = [1.0, 2.0]
    usage.record(cfg, "m", 1_000_000, 0, feature="ask")
    usage.record(cfg, "m", 0, 1_000_000, feature="read")
    f = {x["id"]: x for x in usage.history(cfg)["features"]}
    assert f["ask"]["usd"] == 1.0 and f["read"]["usd"] == 2.0 and f["ask"]["label"] == "问答"
    assert usage.feature_of("screen", "search_rerank") == "search" and usage.feature_of("read", "read") == "read"


# ---------------------------------------------------------------- 会议日程与新数据源（解析用离线样例）
def test_calendar_parse():
    from dailypaper.integrations.venues.calendar import _from_ccfddl, _start_date

    assert _start_date("February 16-23, 2027", 2027) == date(2027, 2, 16)
    data = [{"title": "X", "confs": [
        {"year": 2020, "date": "May 1-3, 2020", "place": "Old", "timeline": [{"deadline": "2019-12-01 23:59:59"}]},
        {"year": 2099, "date": "June 5-9, 2099", "place": "Mars", "link": "l", "timeline": [{"deadline": "2098-12-01 23:59:59"}]}]}]
    got = _from_ccfddl(data)
    assert got["place"] == "Mars" and got["date"] == "2099-06-05" and got["deadline"] == "2098-12-01"


def test_pmlr_and_cvf_parsers():
    from dailypaper.integrations.venues.adapters.cvf import parse_listing
    from dailypaper.integrations.venues.adapters.pmlr import parse_pmlr_bib

    bib = """@InProceedings{pmlr-v291-pref25a,
  title = {Conference on Learning Theory 2025: Preface},
  author = {Haghtalab, Nika and Moitra, Ankur},
  pdf = {https://x/pref.pdf}
}

@InProceedings{pmlr-v291-smith25a,
  title = {Sharp Bounds for {Selective} Prediction},
  author = {Smith, Jane and Doe, John},
  abstract = {We prove sharp bounds.},
  pdf = {https://x/smith.pdf},
  url = {https://proceedings.mlr.press/v291/smith25a.html}
}
"""
    ps = parse_pmlr_bib(bib, "colt", 2025, 291)
    assert len(ps) == 1 and ps[0].title == "Sharp Bounds for Selective Prediction" and ps[0].authors == ["Jane Smith", "John Doe"]
    assert ps[0].abstract == "We prove sharp bounds." and ps[0].url_pdf == "https://x/smith.pdf"
    html = """<dt class="ptitle"><br><a href="/content/ICCV2025/html/A_Paper_ICCV_2025_paper.html">A &amp; B Paper</a></dt>
<dd><input type="hidden" name="query_author" value="Ann Lee"><input type="hidden" name="query_author" value="Bo Li"></dd>
<dd>[<a href="/content/ICCV2025/papers/A_Paper_ICCV_2025_paper.pdf">pdf</a>]</dd>"""
    cs = parse_listing(html, "iccv", 2025)
    assert len(cs) == 1 and cs[0].title == "A & B Paper" and cs[0].authors == ["Ann Lee", "Bo Li"]
    assert cs[0].url_pdf.endswith("_paper.pdf") and cs[0].url_abs.startswith("https://openaccess.thecvf.com/content/")


def test_notify_status_and_estimate(cfg):
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    n = client.get("/api/notify").json()
    assert set(n["channels"]) == {"toast", "ntfy", "serverchan"} and n["enabled"] is False  # 测试流水线里没有 notify
    cfg.llm.pricing["claude-sonnet-5-5"] = [2.0, 10.0]
    e = client.get("/api/estimate", params={"kind": "read", "n": 2}).json()
    assert e["n"] == 2 and (e["usd"] is None or e["usd"] > 0)
