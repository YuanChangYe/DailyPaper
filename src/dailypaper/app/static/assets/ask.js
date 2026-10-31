/* 论文页「问答」标签：就这篇论文提问，回答依据 PDF 全文 + 精读笔记 + 研究方向，流式显示，出处可点跳到 PDF。
   index.html 通过 window.DPAsk.mount(panel, ctx) 调用；ctx 里是页面的工具函数（renderMarkdown、send、toast……）。 */
(function () {
  "use strict";
  const CSS = `
.ask-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;padding:12px 16px;border-radius:var(--r-md);border:1px solid var(--line);background:var(--glass);font-size:13px;color:var(--muted);margin-bottom:14px}
.ask-head b{color:var(--ink);font-weight:600}.ask-head .grow{flex:1}
.ask-sugs{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.ask-sugs button{border:1px solid var(--line);background:var(--glass);color:var(--body);font:inherit;font-size:13px;padding:6px 12px;border-radius:999px;cursor:pointer;text-align:left}
.ask-sugs button:hover{border-color:var(--accent);color:var(--ink)}
.ask-list{display:flex;flex-direction:column;gap:14px;padding-bottom:12px}
.ask-head,.ask-empty{background:var(--panel)}.ask-empty{border-radius:var(--r-md)}
.ask-item{padding:16px 18px;border-radius:var(--r-md);border:1px solid var(--line);background:var(--glass)}
.ask-item .q{font-weight:600;color:var(--ink);font-size:15px;line-height:1.5;white-space:pre-wrap}
.ask-item .quote{margin:8px 0 0;padding:6px 10px;border-left:3px solid var(--accent);color:var(--muted);font-size:13px;white-space:pre-wrap;max-height:7.5em;overflow:auto}
.ask-item .a{margin-top:10px;font-size:15px}
.ask-item .a.prose>*:first-child{margin-top:0}.ask-item .a.prose>*:last-child{margin-bottom:0}
.ask-item .meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-top:12px;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
.ask-item .meta .grow{flex:1}
.ask-item .meta button{border:0;background:none;color:var(--muted);font:inherit;font-size:12px;cursor:pointer;padding:2px 4px;border-radius:6px}
.ask-item .meta button:hover{color:var(--ink);background:var(--glass)}
.ask-item.pending .a{color:var(--muted)}
.ask-item .err{color:var(--bad);margin-top:8px;font-size:13px}
.ask-box{position:sticky;bottom:12px;z-index:5;margin-top:18px;padding:10px;border-radius:16px;border:1px solid var(--line-2);background:var(--panel);box-shadow:0 14px 40px rgba(0,0,0,.25);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
.ask-box .qbar{display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;padding:6px 10px;border-left:3px solid var(--accent);background:var(--glass);border-radius:6px;font-size:12.5px;color:var(--muted)}
.ask-box .qbar span{flex:1;min-width:0;max-height:4.5em;overflow:auto;white-space:pre-wrap}
.ask-box .qbar button{border:0;background:none;color:var(--muted);cursor:pointer;font-size:16px;line-height:1}
.ask-box textarea{width:100%;min-height:52px;max-height:200px;resize:vertical;border:0;outline:none;background:transparent;color:var(--ink);font:inherit;font-size:15px;line-height:1.55}
.ask-box .row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-top:6px;font-size:12.5px;color:var(--muted)}
.ask-box .row label{display:inline-flex;align-items:center;gap:5px;cursor:pointer}
.ask-box .row .grow{flex:1}
.ask-box .row label.off{opacity:.45;cursor:not-allowed}
.ask-empty{padding:18px;color:var(--muted);font-size:14px;line-height:1.7}
.ask-cursor::after{content:"▍";animation:askblink 1s steps(1) infinite;color:var(--accent)}
@keyframes askblink{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){.ask-cursor::after{animation:none}}`;
  let styled = false;
  const S = {};  // 每篇论文一份：{items, context, busy, ctrl}

  function style() {
    if (styled) return; styled = true;
    const el = document.createElement("style"); el.textContent = CSS; document.head.append(el);
  }
  const money = (x) => (x ? (x < 0.01 ? "<$0.01" : "$" + x.toFixed(2)) : "$0");
  const GROUND = { pdf: "PDF 全文", fulltext: "精读时的全文", abstract: "只有摘要" };

  function mount(panel, ctx) {
    style();
    if (panel.dataset.mounted === ctx.id && panel.firstElementChild) {
      if (ctx.quote) setQuote(panel, ctx.quote);
      panel.querySelector("textarea")?.focus({ preventScroll: true });
      return;
    }
    panel.dataset.mounted = ctx.id;
    if (!ctx.isLocal) {
      panel.innerHTML = `<div class="notice info">问答需要本地助手（会调用你配置的模型）。在电脑上双击 DailyPaper 图标启动后，用 <span class="mono">http://127.0.0.1:8765</span> 打开即可。</div>`;
      return;
    }
    const st = (S[ctx.id] ||= { items: [], context: null, busy: false, ctrl: null });
    panel.innerHTML = `
      <div class="ask-head" data-head><span>正在读取论文全文…</span></div>
      <div class="ask-sugs" data-sugs></div>
      <div class="ask-list" data-list></div>
      <form class="ask-box" data-box>
        <div class="qbar" data-qbar hidden><span></span><button type="button" title="去掉引文" aria-label="去掉引文">×</button></div>
        <textarea placeholder="就这篇论文提问… Enter 发送，Shift+Enter 换行。在笔记里选中文字可以「问 AI」。" aria-label="问题"></textarea>
        <div class="row">
          <label title="改用精读模型回答：更慢、更贵、更深入"><input type="checkbox" data-deep> 深入</label>
          <label data-weblabel title="允许模型联网搜索（只有 Claude 支持，每次多约 $0.01–0.05）"><input type="checkbox" data-web> 联网</label>
          <span class="grow" data-hint></span>
          <button class="btn sm ghost" type="button" data-cancel hidden>取消</button>
          <button class="btn sm primary" type="submit" data-send>发送</button>
        </div>
      </form>`;
    const box = panel.querySelector("[data-box]"), ta = box.querySelector("textarea");
    box.addEventListener("submit", (e) => { e.preventDefault(); submit(panel, ctx); });
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(panel, ctx); } });
    box.querySelector("[data-qbar] button").addEventListener("click", () => setQuote(panel, ""));
    box.querySelector("[data-cancel]").addEventListener("click", () => st.ctrl?.abort());
    panel.querySelector("[data-sugs]").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b || st.busy) return;
      ta.value = b.dataset.q; submit(panel, ctx);
    });
    panel.querySelector("[data-list]").addEventListener("click", (e) => onItemAction(e, panel, ctx));
    if (ctx.quote) setQuote(panel, ctx.quote);
    load(panel, ctx);
  }

  async function load(panel, ctx) {
    const st = S[ctx.id];
    try {
      const r = await ctx.getJSON(`/api/ask?paper_id=${encodeURIComponent(ctx.id)}`);
      st.items = r.items || []; st.context = r.context;
    } catch (e) {
      panel.querySelector("[data-head]").innerHTML = e.status === 404
        ? `<span>本地助手是旧版本，还没有问答功能。</span><button class="btn sm" type="button" data-restart-app>重启本地助手</button>`
        : `<span>读取失败：${ctx.esc(e.message)}</span>`;
      return;
    }
    paintHead(panel, ctx); paintSugs(panel, ctx); paintList(panel, ctx);
  }

  function paintHead(panel, ctx) {
    const st = S[ctx.id], c = st.context; if (!c) return;
    const usd = st.items.reduce((s, i) => s + (i.usd || 0), 0);
    const ground = `${GROUND[c.grounding] || c.grounding}${c.pages ? ` ${c.pages} 页` : ""}${c.has_note ? " + 精读笔记" : ""}${c.has_profile ? " + 课题档案" : ""}`;
    panel.querySelector("[data-head]").innerHTML = `<span>依据 <b>${ctx.esc(ground)}</b></span><span>模型 <b class="mono">${ctx.esc(c.model)}</b></span>
      <span class="grow"></span><span>本篇已问 ${st.items.length} 次 · ${money(usd)}</span>`;
    const web = panel.querySelector("[data-weblabel]");
    if (!c.web_ok) { web.classList.add("off"); web.querySelector("input").disabled = true; web.title = "联网搜索只有 Claude 支持；当前问答模型不是 Claude"; }
    panel.querySelector("[data-deep]").parentElement.title = `改用精读模型 ${c.deep_model} 回答：更慢、更贵、更深入`;
    if (c.grounding === "abstract") panel.querySelector("[data-hint]").textContent = "这篇只拿到了摘要，回答依据有限";
  }

  function paintSugs(panel, ctx) {
    const c = S[ctx.id].context; const el = panel.querySelector("[data-sugs]");
    el.innerHTML = (c?.suggestions || []).map((q) => `<button type="button" data-q="${ctx.esc(q)}">${ctx.esc(q)}</button>`).join("");
  }

  function itemHTML(it, ctx) {
    const flags = [it.deep ? "深入" : "", it.web ? "联网" : "", it.cached ? "缓存" : ""].filter(Boolean).join(" · ");
    return `<article class="ask-item" data-item="${ctx.esc(it.id)}">
      <div class="q">${ctx.esc(it.q)}</div>
      ${it.quote ? `<div class="quote">${ctx.esc(it.quote)}</div>` : ""}
      <div class="a prose">${ctx.renderMarkdown(it.a || "")}</div>
      <div class="meta"><span class="mono">${ctx.esc(it.model || "")}</span><span>${money(it.usd)}${it.seconds ? ` · ${it.seconds}s` : ""}${flags ? " · " + flags : ""}</span>
        <span>${ctx.esc(String(it.at || "").replace("T", " ").slice(5, 16))}</span><span class="grow"></span>
        <button type="button" data-act="copy">复制</button><button type="button" data-act="note">存入我的笔记</button>
        ${it.deep ? "" : `<button type="button" data-act="deep">用精读模型重答</button>`}<button type="button" data-act="del">删除</button></div>
    </article>`;
  }

  function paintList(panel, ctx) {
    const st = S[ctx.id], list = panel.querySelector("[data-list]");
    list.innerHTML = st.items.length ? st.items.map((it) => itemHTML(it, ctx)).join("")
      : `<div class="ask-empty">还没有问过这篇。可以点上面的建议问题，或者直接输入。回答会标出原文页码，点页码就在右边 PDF 里跳过去。</div>`;
    ctx.linkSources(ctx.id, list);
  }

  function setQuote(panel, text) {
    const bar = panel.querySelector("[data-qbar]"); if (!bar) return;
    bar.hidden = !text; bar.querySelector("span").textContent = text || ""; bar.dataset.quote = text || "";
    if (text) panel.querySelector("textarea").focus({ preventScroll: true });
  }

  async function submit(panel, ctx, override) {
    const st = S[ctx.id]; if (st.busy) return;
    const ta = panel.querySelector("textarea"), bar = panel.querySelector("[data-qbar]");
    const q = override?.q ?? ta.value.trim(); if (!q) { ta.focus(); return; }
    const body = { paper_id: ctx.id, question: q, quote: override?.quote ?? (bar.dataset.quote || ""),
      deep: override?.deep ?? panel.querySelector("[data-deep]").checked, web: panel.querySelector("[data-web]").checked };
    st.busy = true; st.ctrl = new AbortController();
    const send = panel.querySelector("[data-send]"), cancel = panel.querySelector("[data-cancel]"), hint = panel.querySelector("[data-hint]");
    send.disabled = true; cancel.hidden = false;
    const list = panel.querySelector("[data-list]");
    list.querySelector(".ask-empty")?.remove();
    const card = document.createElement("article"); card.className = "ask-item pending";
    card.innerHTML = `<div class="q">${ctx.esc(q)}</div>${body.quote ? `<div class="quote">${ctx.esc(body.quote)}</div>` : ""}<div class="a prose ask-cursor">正在读论文…</div>`;
    list.append(card); card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (!override) { ta.value = ""; setQuote(panel, ""); }
    const t0 = Date.now(); const timer = setInterval(() => (hint.textContent = `${Math.round((Date.now() - t0) / 1000)}s`), 500);
    let text = "", raf = 0;
    const paint = () => { raf = 0; card.querySelector(".a").innerHTML = ctx.renderMarkdown(text); };
    try {
      const r = await fetch("/api/ask/stream", { method: "POST", signal: st.ctrl.signal, cache: "no-store",
        headers: { "Content-Type": "application/json", "X-DailyPaper": "1" }, body: JSON.stringify(body) });
      if (!r.ok) {
        let msg = `${r.status}`; try { msg = (await r.json()).detail || msg; } catch { /* 非 JSON */ }
        if (r.status === 404 || r.status === 405) msg = "本地助手是旧版本，还没有问答功能：请重启本地助手。";
        throw new Error(msg);
      }
      const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", item = null;
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
          if (!line.startsWith("data:")) continue;
          const msg = JSON.parse(line.slice(5));
          if (msg.delta) { text += msg.delta; if (!raf) raf = setTimeout(paint, 60); }  // 不用 rAF：页面在后台时 rAF 不触发
          else if (msg.error) throw new Error(msg.error);
          else if (msg.done) item = msg.item;
        }
      }
      if (!item) throw new Error("连接中断，没有收到完整回答");
      st.items.push(item);
      const tmp = document.createElement("div"); tmp.innerHTML = itemHTML(item, ctx);
      const done = tmp.firstElementChild; card.replaceWith(done); ctx.linkSources(ctx.id, done);
      paintHead(panel, ctx);
    } catch (e) {
      card.classList.remove("pending"); card.querySelector(".a").classList.remove("ask-cursor");
      if (raf) clearTimeout(raf);
      if (text) paint();
      const msg = e.name === "AbortError" ? "已取消（这一问不保存）" : e.message;
      card.insertAdjacentHTML("beforeend", `<div class="err">${ctx.esc(msg)}</div>`);
      if (!override) ta.value = q;
    } finally {
      clearInterval(timer); hint.textContent = "";
      st.busy = false; st.ctrl = null; send.disabled = false; cancel.hidden = true;
    }
  }

  async function onItemAction(e, panel, ctx) {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const card = b.closest("[data-item]"), st = S[ctx.id];
    const it = st.items.find((x) => x.id === card.dataset.item); if (!it) return;
    if (b.dataset.act === "copy") {
      try { await navigator.clipboard.writeText(`问：${it.q}\n\n${it.a}`); ctx.toast("已复制", "ok"); } catch { ctx.toast("复制失败", "err"); }
    } else if (b.dataset.act === "note") {
      // 追加到「我的笔记」文本框并触发保存（批注面板负责写回笔记 frontmatter，Obsidian 里能看到）
      const ta = document.getElementById("my-note");
      if (!ta) { ctx.toast("找不到「我的笔记」", "err"); return; }
      ta.value = `${ta.value.trim()}${ta.value.trim() ? "\n\n" : ""}【问答】${it.q}\n${it.a}`.trim();
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      ctx.toast("已存入我的笔记", "ok");
    } else if (b.dataset.act === "deep") {
      submit(panel, ctx, { q: it.q, quote: it.quote, deep: true });
    } else if (b.dataset.act === "del") {
      if (!confirm("删除这条问答？")) return;
      try {
        await fetch(`/api/ask?paper_id=${encodeURIComponent(ctx.id)}&item_id=${encodeURIComponent(it.id)}`, { method: "DELETE", headers: { "X-DailyPaper": "1" } });
        st.items = st.items.filter((x) => x.id !== it.id); paintList(panel, ctx); paintHead(panel, ctx);
      } catch (err) { ctx.toast(err.message, "err"); }
    }
  }

  window.DPAsk = { mount };
})();
