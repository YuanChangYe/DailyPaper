/* 「检索」页（#/search）：用自然语言找论文。范围：我的顶会池（本地全文检索）/ 全网（arXiv + OpenAlex）/ 两者。
   模型扩展关键词、结合研究方向重排，每篇一句理由；可以精读、快速看看、导出 BibTeX、生成深入综述。
   index.html 里 VIEWS.search 调用 window.DPSearch.view / after，ctx 是页面的工具函数。 */
(function () {
  "use strict";
  const CSS = `
.srch{display:grid;grid-template-columns:240px minmax(0,1fr);gap:28px;align-items:start}
.srch-hist{position:sticky;top:96px;max-height:calc(100vh - 120px);overflow:auto}
.srch-hist h4{margin:0 0 8px;font-size:13px;color:var(--muted);font-weight:600;letter-spacing:.04em}
.srch-hist button{display:block;width:100%;text-align:left;border:0;background:none;color:var(--body);font:inherit;font-size:13px;line-height:1.45;padding:7px 10px;border-radius:10px;cursor:pointer}
.srch-hist button:hover{background:var(--glass);color:var(--ink)}
.srch-hist button small{display:block;color:var(--muted);font-size:11.5px;margin-top:2px}
.srch-form{padding:16px;border-radius:var(--r-md);border:1px solid var(--line);background:var(--panel)}
.srch-form textarea{width:100%;min-height:64px;resize:vertical;border:0;outline:none;background:transparent;color:var(--ink);font:inherit;font-size:16px;line-height:1.55}
.srch-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-top:10px;font-size:13px;color:var(--muted)}
.srch-row .grow{flex:1}
.srch-chips{display:flex;flex-wrap:wrap;gap:6px}
.srch-chips button{border:1px solid var(--line);background:none;color:var(--muted);font:inherit;font-size:12.5px;padding:4px 10px;border-radius:999px;cursor:pointer}
.srch-chips button[aria-pressed="true"]{border-color:var(--accent);color:var(--ink);background:var(--glass)}
.srch-more{margin-top:10px;font-size:13px;color:var(--muted)}
.srch-more summary{cursor:pointer;width:fit-content}
.srch-more .lab{display:block;margin:10px 0 6px;font-size:12px;letter-spacing:.04em}
.srch-status{margin:16px 2px;font-size:13.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.srch-status b{color:var(--ink)}.srch-status .grow{flex:1}
.srch-item{display:grid;grid-template-columns:auto minmax(0,1fr);gap:12px;padding:16px 18px;border-radius:var(--r-md);border:1px solid var(--line);background:var(--glass);margin-bottom:10px}
.srch-item h3{font-size:16.5px;line-height:1.4;margin:0}.srch-item h3 a:hover{color:var(--accent)}
.srch-item .badges{display:flex;flex-wrap:wrap;gap:6px;margin:7px 0 0;font-size:12px;color:var(--muted);align-items:center}
.srch-item .why{margin:8px 0 0;font-size:14px;color:var(--body);line-height:1.6}
.srch-item .rel{display:inline-flex;align-items:center;gap:6px;font-variant-numeric:tabular-nums}
.srch-item .rel i{display:inline-block;width:56px;height:5px;border-radius:3px;background:var(--line);overflow:hidden;position:relative}
.srch-item .rel i::after{content:"";position:absolute;inset:0 auto 0 0;width:var(--w);background:var(--accent);border-radius:3px}
.srch-item details{margin-top:8px;font-size:13.5px;color:var(--muted)}.srch-item details summary{cursor:pointer;width:fit-content}
.srch-item details p{margin:6px 0 0;line-height:1.65}
.srch-item .acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.srch-item input[type=checkbox]{margin-top:4px;accent-color:var(--accent);width:16px;height:16px}
.srch-report{margin-top:18px;padding:20px 22px;border-radius:var(--r-md);border:1px solid var(--accent);background:var(--glass)}
.srch-empty{padding:28px 6px;color:var(--muted);font-size:14px;line-height:1.8}
.srch-item .badges .chip{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.srch-status,.srch-hist,.srch-empty{border-radius:var(--r-md);background:var(--panel);padding:10px 14px}
.srch-hist{padding:12px 8px}.srch-status:empty{display:none}
@media (max-width:900px){.srch{grid-template-columns:minmax(0,1fr)}.srch-hist{position:static;max-height:none;order:2}}`;
  let styled = false;
  const style = () => { if (styled) return; styled = true; const el = document.createElement("style"); el.textContent = CSS; document.head.append(el); };
  const SRC = { pool: "顶会池", arxiv: "arXiv", openalex: "OpenAlex" };
  const SCOPES = [["pool", "我的顶会池"], ["web", "全网"], ["all", "两者"]];
  const LEVELS = [["ccf:A", "CCF-A"], ["ccf:B", "CCF-B"], ["core:A*", "CORE A*"], ["core:A", "CORE A"]];
  let last = null;  // 当前结果

  function view(params, ctx) {
    style();
    const q = params.get("q") || "";
    if (!ctx.isLocal) {
      return `<div class="page-head"><h1>AI 检索</h1><p>用一句话描述要找的论文，在你的顶会论文池或全网（arXiv、OpenAlex）里找，按和你研究方向的相关度排序。</p></div>
        <div class="notice info">检索需要本地助手（会调用你配置的模型，每次约 $0.01）。在电脑上启动 DailyPaper 后用 <span class="mono">http://127.0.0.1:8765/#/search</span> 打开。</div>`;
    }
    const scope = params.get("scope") || "pool";
    return `<div class="page-head"><h1>AI 检索</h1><p>用一句话描述要找的论文。模型会扩展关键词，在论文池或全网里找，再按和你研究方向的相关度重排，每篇写一句理由。</p></div>
    <div class="srch">
      <aside class="srch-hist" id="srch-hist"><h4>检索历史</h4><div class="srch-empty" style="padding:6px 10px">还没有</div></aside>
      <div>
        <form class="srch-form" id="srch-form">
          <label class="sr" for="srch-q">要找什么</label>
          <textarea id="srch-q" placeholder="例如：LLM agent 什么时候应该拒答，有没有 selective prediction 的理论保证？">${ctx.esc(q)}</textarea>
          <div class="srch-row">
            <div class="srch-chips" data-scope>${SCOPES.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${v === scope}">${t}</button>`).join("")}</div>
            <span class="grow"></span>
            <label>条数 <select id="srch-n">${[10, 20, 30].map((n) => `<option ${n === 20 ? "selected" : ""}>${n}</option>`).join("")}</select></label>
            <button class="btn primary" type="submit" id="srch-go">检索</button>
          </div>
          <details class="srch-more" id="srch-more"><summary>筛选会议、年份和等级（只对顶会池生效）</summary>
            <span class="lab">等级</span><div class="srch-chips" data-levels>${LEVELS.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="false">${t}</button>`).join("")}</div>
            <span class="lab">会议</span><div class="srch-chips" data-venues>正在读取…</div>
            <span class="lab">年份</span><div class="srch-chips" data-years></div>
          </details>
        </form>
        <div class="srch-status" id="srch-status"></div>
        <div id="srch-results"><div class="srch-empty">顶会池在本地检索，只有扩展关键词和重排会调用模型，每次约 $0.01；全网会实时请求 arXiv 和 OpenAlex，慢一些（10–30 秒）。同一个问题 24 小时内再搜不花钱。</div></div>
        <div id="srch-report"></div>
      </div>
    </div>`;
  }

  async function after(params, ctx) {
    if (!ctx.isLocal) return;
    const form = document.getElementById("srch-form"), ta = document.getElementById("srch-q");
    form.addEventListener("click", (e) => {
      const b = e.target.closest(".srch-chips button"); if (!b) return;
      const box = b.parentElement;
      if (box.hasAttribute("data-scope")) box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      else b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true"));
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); run(ctx); });
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); run(ctx); } });
    document.getElementById("srch-hist").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-hq]"); if (!b) return;
      ta.value = b.dataset.hq;
      form.querySelectorAll("[data-scope] button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === b.dataset.hs)));
      if (b.dataset.hkey) {  // 24 小时内的结果直接打开，不花钱
        try {
          const res = await ctx.getJSON(`/api/search/result?key=${encodeURIComponent(b.dataset.hkey)}`);
          history.replaceState(null, "", `#/search?q=${encodeURIComponent(res.q)}&scope=${res.scope}&key=${res.key}`);
          fillForm(res); show(res, ctx); return;
        } catch { /* 过期：重新检索 */ }
      }
      run(ctx);
    });
    document.getElementById("srch-results").addEventListener("click", (e) => onResultAction(e, ctx));
    loadVenues(ctx); loadHistory(ctx);
    if (params.get("key")) {
      try { const res = await ctx.getJSON(`/api/search/result?key=${encodeURIComponent(params.get("key"))}`); fillForm(res); show(res, ctx); }
      catch { if (params.get("q")) run(ctx); }  // 结果过期：按地址里的问题重新检索
    } else if (params.get("q")) run(ctx);
  }

  // 用保存的结果回填表单：问题、范围、会议 / 年份 / 等级筛选
  function fillForm(res) {
    document.getElementById("srch-q").value = res.q || "";
    const f = res.filters || {};
    const lv = [...(f.ccf || []).map((x) => "ccf:" + x), ...(f.core || []).map((x) => "core:" + x)];
    const mark = (sel, vals) => document.querySelectorAll(`${sel} button`).forEach((b) => b.setAttribute("aria-pressed", String(vals.includes(b.dataset.v))));
    mark("[data-scope]", [res.scope || "pool"]);
    mark("[data-levels]", lv);
    mark("[data-venues]", f.venues || []);
    mark("[data-years]", (f.years || []).map(String));
    if (lv.length || (f.venues || []).length || (f.years || []).length) document.getElementById("srch-more").open = true;
  }

  async function loadVenues(ctx) {
    const box = document.querySelector("[data-venues]"), yrs = document.querySelector("[data-years]");
    try {
      const st = await ctx.getJSON("/api/config/venues");
      const on = st.venues.filter((v) => v.enabled);
      box.innerHTML = on.map((v) => `<button type="button" data-v="${ctx.esc(v.id)}" aria-pressed="false">${ctx.esc(v.name)}</button>`).join("") || "（论文池里还没有会议）";
      const y = new Date().getFullYear();
      yrs.innerHTML = [y, y - 1, y - 2].map((x) => `<button type="button" data-v="${x}" aria-pressed="false">${x}</button>`).join("");
    } catch { box.textContent = "读取会议列表失败"; }
  }

  async function loadHistory(ctx) {
    const el = document.getElementById("srch-hist");
    try {
      const { items } = await ctx.getJSON("/api/search/history");
      if (!items.length) return;
      el.innerHTML = `<h4>检索历史</h4>` + items.slice(0, 30).map((h) => `<button type="button" data-hq="${ctx.esc(h.q)}" data-hs="${ctx.esc(h.scope)}" data-hkey="${ctx.esc(h.key || "")}" title="${ctx.esc((h.top || []).join("\n"))}">${ctx.esc(h.q)}<small>${ctx.esc({ pool: "顶会池", web: "全网", all: "两者" }[h.scope] || h.scope)} · ${h.n} 篇 · ${ctx.esc(String(h.at || "").slice(5, 16).replace("T", " "))}</small></button>`).join("");
    } catch { /* 旧版本地助手 */ }
  }

  const picked = (sel) => [...document.querySelectorAll(`${sel} button[aria-pressed="true"]`)].map((b) => b.dataset.v);

  async function run(ctx, refresh = false) {
    const q = document.getElementById("srch-q").value.trim(); if (!q) return;
    const scope = picked("[data-scope]")[0] || "pool";
    const levels = picked("[data-levels]");
    const body = { q, scope, n: Number(document.getElementById("srch-n").value), refresh,
      venues: picked("[data-venues]"), years: picked("[data-years]").map(Number),
      ccf: levels.filter((x) => x.startsWith("ccf:")).map((x) => x.slice(4)), core: levels.filter((x) => x.startsWith("core:")).map((x) => x.slice(5)) };
    const go = document.getElementById("srch-go"), status = document.getElementById("srch-status");
    go.disabled = true; document.getElementById("srch-report").innerHTML = "";
    const t0 = Date.now();
    const tick = setInterval(() => (status.innerHTML = `<span>${scope === "pool" ? "在论文池里找" : "在 arXiv 和 OpenAlex 上找"}、按你的方向重排…</span><span>${Math.round((Date.now() - t0) / 1000)}s</span>`), 300);
    try {
      const res = await ctx.send("/api/search", body);
      history.replaceState(null, "", `#/search?q=${encodeURIComponent(q)}&scope=${scope}&key=${res.key}`);
      show(res, ctx); loadHistory(ctx);
    } catch (e) {
      status.innerHTML = `<span style="color:var(--bad)">${ctx.esc(e.status === 404 || e.status === 405 ? "本地助手是旧版本，还没有检索功能：请重启本地助手。" : e.message)}</span>`;
    } finally { clearInterval(tick); go.disabled = false; }
  }

  function show(res, ctx) {
    last = res;
    const c = res.counts || {};
    const where = [c.pool ? `顶会池 ${c.pool}` : "", c.arxiv ? `arXiv ${c.arxiv}` : "", c.openalex ? `OpenAlex ${c.openalex}` : ""].filter(Boolean).join(" · ");
    document.getElementById("srch-status").innerHTML = `<span><b>${res.results.length}</b> 篇相关（召回 ${ctx.esc(where || "0")}，去重后 ${res.candidates} 篇）</span>
      <span>${res.cached ? "缓存结果，未花钱" : `约 $${(res.usd || 0).toFixed(3)} · ${res.seconds}s`}</span>${res.intent ? `<span title="模型理解的检索意图">「${ctx.esc(res.intent)}」</span>` : ""}
      <span class="grow"></span>
      ${res.results.length ? `<button class="btn sm" type="button" data-sact="bib">导出 BibTeX</button><button class="btn sm primary" type="button" data-sact="report">生成深入综述</button>` : ""}
      ${res.cached ? `<button class="btn sm ghost" type="button" data-sact="refresh">重新检索</button>` : ""}
      ${(res.notes || []).map((n) => `<span style="flex-basis:100%;color:var(--warn)">${ctx.esc(n)}</span>`).join("")}`;
    const status = document.getElementById("srch-status");
    status.onclick = (e) => {
      const b = e.target.closest("[data-sact]"); if (!b) return;
      if (b.dataset.sact === "bib") bib(ctx);
      else if (b.dataset.sact === "report") report(ctx, b);
      else run(ctx, true);
    };
    const el = document.getElementById("srch-results");
    if (!res.results.length) {
      el.innerHTML = `<div class="srch-empty">没有找到相关的论文。可以换个说法、去掉会议或年份筛选，或者把范围换成「全网」。${(c.pool || 0) === 0 && res.scope !== "web" ? "<br>论文池里没有命中：确认在 设置 → 研究方向 里同步过论文池。" : ""}</div>`;
      return;
    }
    el.innerHTML = res.results.map((r) => item(r, ctx)).join("");
  }

  function item(r, ctx) {
    const lvl = [r.ccf ? "CCF-" + r.ccf : "", r.core ? "CORE " + r.core : ""].filter(Boolean).join(" · ");
    const venue = [r.venue_name, r.year].filter(Boolean).join(" ");
    const pct = Math.max(0, Math.min(100, (r.score || 0) * 10));
    return `<article class="srch-item" data-rid="${ctx.esc(r.id)}">
      <input type="checkbox" data-bibpick="${ctx.esc(r.id)}" aria-label="选中导出 BibTeX" checked>
      <div>
        <h3>${r.url_abs ? `<a href="${ctx.esc(r.url_abs)}" target="_blank" rel="noopener noreferrer">${ctx.esc(r.title)}</a>` : ctx.esc(r.title)}</h3>
        <div class="badges"><span class="rel" title="相关度 ${r.score}/10"><i style="--w:${pct}%"></i>${(r.score || 0).toFixed(0)}</span>
          <span class="chip src">${SRC[r.source] || r.source}</span>${venue ? `<span class="chip venue">${ctx.esc(venue)}${lvl ? `<small>${ctx.esc(lvl)}</small>` : ""}</span>` : ""}
          ${r.followed?.length ? `<span class="chip follow" title="关注：${ctx.esc(r.followed.join("、"))}">关注</span>` : ""}
          ${r.note_id ? `<a class="chip" href="#/paper/${encodeURIComponent(r.note_id)}">已有笔记 →</a>` : ""}
          <span>${ctx.esc((r.authors || []).slice(0, 4).join(", "))}${(r.authors || []).length > 4 ? " 等" : ""}</span></div>
        ${r.reason ? `<p class="why">${ctx.esc(r.reason)}</p>` : ""}
        ${r.abstract ? `<details><summary>摘要</summary><p>${ctx.esc(r.abstract)}</p></details>` : ""}
        <div class="acts">
          ${r.note_id ? "" : r.readable ? `<button class="btn sm primary" type="button" data-ract="read">精读</button><button class="btn sm" type="button" data-ract="quick" title="只写前几节，用便宜的模型，约 $0.01">快速看看</button>` : `<span style="font-size:12px;color:var(--muted)">这篇没有 arXiv 版本，暂时不能在这里精读</span>`}
          ${r.url_pdf ? `<a class="btn sm ghost" href="${ctx.esc(r.url_pdf)}" target="_blank" rel="noopener noreferrer">PDF ↗</a>` : ""}
        </div>
      </div>
    </article>`;
  }

  function onResultAction(e, ctx) {
    const b = e.target.closest("[data-ract]"); if (!b) return;
    const id = b.closest("[data-rid]").dataset.rid;
    const r = last?.results.find((x) => x.id === id); if (!r) return;
    const rid = r.source === "pool" ? r.id : r.arxiv_id || r.id;
    ctx.startRun("read", [rid], { quick: b.dataset.ract === "quick" });
  }

  function bib(ctx) {
    if (!last) return;
    const ids = [...document.querySelectorAll("[data-bibpick]:checked")].map((x) => x.dataset.bibpick);
    if (!ids.length) { ctx.toast("先勾选要导出的论文", "err"); return; }
    location.href = `/api/search/bibtex?key=${encodeURIComponent(last.key)}&ids=${encodeURIComponent(ids.join(","))}`;
  }

  async function report(ctx, btn) {
    if (!last) return;
    const n = Math.min(25, last.results.length);
    let est = ""; try { const r = await ctx.getJSON(`/api/estimate?kind=report`); if (r.usd != null) est = `，大约 $${r.usd.toFixed(2)}`; } catch { /* 忽略 */ }
    if (!confirm(`用精读模型对前 ${n} 篇写一份深入综述（方法分组、对比表、研究空白、follow-up idea）？\n会调用模型${est}，需要一两分钟。`)) return;
    btn.disabled = true;
    const box = document.getElementById("srch-report");
    box.innerHTML = `<div class="srch-report"><p style="color:var(--muted);margin:0">正在写综述…（${n} 篇）</p></div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const r = await ctx.send("/api/search/report", { key: last.key, n });
      const md = r.markdown.replace(/^---[\s\S]*?\n---\n/, "");
      box.innerHTML = `<div class="srch-report"><p style="font-size:12.5px;color:var(--muted);margin:0 0 12px">已保存到 <span class="mono">${ctx.esc(r.path)}</span>${r.obsidian ? `，并复制到 Obsidian（${ctx.esc(r.obsidian)}）` : ""} · ${ctx.esc(r.model)} · 约 $${(r.usd || 0).toFixed(2)}</p><div class="prose">${ctx.renderMarkdown(md)}</div></div>`;
    } catch (e) {
      box.innerHTML = `<div class="notice">${ctx.esc(e.message)}</div>`;
    } finally { btn.disabled = false; }
  }

  window.DPSearch = { view, after };
})();
