/* DailyPaper 天气动态封面。
 *
 * 给首页封面（.hero-stage）叠加天气效果：晴（暖色调 + 光束）、多云（飘动的云）、雾（加浓雾气）、
 * 雨（斜向雨丝 + 玻璃上的水珠，水珠里是倒立缩小的照片，会慢慢滑落并合并）、雪、雷暴（大雨 + 闪电）、夜晚（星空）。
 *
 * 用法：DPWeather.start(stageEl, {scene, night, intensity, wind, motion, tintOnly})；离开页面时 DPWeather.stop()。
 *   scene      clear | cloudy | fog | rain | snow | storm
 *   night      true 时叠加夜色（晴夜有星空）
 *   intensity  0–1，雨雪的密度
 *   wind       -1–1，雨丝和雪的倾斜方向与程度（正数向右）
 *   motion     false 时只画一帧静态画面（尊重「减少动态效果」）
 *   tintOnly   true 时只改色调，不画任何粒子
 * 只用 Canvas 2D，没有外部依赖。封面滚出屏幕或切到别的标签页时自动暂停。
 */
(function () {
  "use strict";
  let st = null;
  const rand = (a, b) => a + Math.random() * (b - a);
  const TAU = Math.PI * 2;

  function layer(cls, tag) {
    const e = document.createElement(tag || "div");
    e.className = cls;
    e.setAttribute("aria-hidden", "true");
    return e;
  }

  /* ------------------------------------------------------------ 照片映射：屏幕坐标 → 图片像素（水珠折射用） */
  function photoMap(img, w, h) {
    if (!img || !img.complete || !img.naturalWidth) return null;
    const nw = img.naturalWidth, nh = img.naturalHeight;
    const scale = Math.max(w / nw, h / nh);
    const pos = (getComputedStyle(img).objectPosition || "50% 50%").split(/\s+/);
    const pct = (v, d) => {
      if (!v) return d;
      if (v.endsWith("%")) return parseFloat(v) / 100;
      return { left: 0, top: 0, center: 0.5, right: 1, bottom: 1 }[v] ?? d;
    };
    const px = pct(pos[0], 0.5), py = pct(pos[1], 0.5);
    return { img, scale, ox: (w - nw * scale) * px, oy: (h - nh * scale) * py };
  }

  /* ------------------------------------------------------------ 云和雾的贴图（离屏画布，画一次反复用） */
  function cloudSprite(w, h, color, blobs) {
    const c = document.createElement("canvas");
    c.width = Math.max(2, Math.round(w)); c.height = Math.max(2, Math.round(h));
    const g = c.getContext("2d");
    for (let i = 0; i < blobs; i++) {
      const x = rand(0.2, 0.8) * w, y = rand(0.35, 0.7) * h, r = rand(0.18, 0.34) * w;
      const grad = g.createRadialGradient(x, y, 0, x, y, r);
      grad.addColorStop(0, `rgba(${color},.55)`);
      grad.addColorStop(0.55, `rgba(${color},.22)`);
      grad.addColorStop(1, `rgba(${color},0)`);
      g.fillStyle = grad;
      g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
    }
    return c;
  }

  /* ------------------------------------------------------------ 初始化各类粒子 */
  function build() {
    const { w, h, o } = st;
    const area = (w * h) / 1e6;  // 以百万像素计
    const k = o.intensity;
    st.rain = []; st.snow = []; st.stars = []; st.clouds = []; st.drops = []; st.shooting = null;
    const rainy = o.scene === "rain" || o.scene === "storm";
    if (rainy) {
      const n = Math.round(area * 220 * (0.35 + k));
      for (let i = 0; i < n; i++) st.rain.push(newStreak(true));
      const nd = Math.round(area * 110 * (0.5 + k));
      for (let i = 0; i < nd; i++) st.drops.push(newDrop(rand(0, w), rand(0, h)));
    }
    if (o.scene === "snow") {
      const n = Math.round(area * 150 * (0.35 + k));
      for (let i = 0; i < n; i++) st.snow.push(newFlake(true));
    }
    if (o.night && (o.scene === "clear" || o.scene === "cloudy")) {
      const n = Math.round(area * (o.scene === "clear" ? 170 : 60));
      for (let i = 0; i < n; i++) st.stars.push({ x: rand(0, w), y: rand(0, h * 0.62), r: rand(0.35, 1.3), a: rand(0.35, 0.95), sp: rand(0.6, 2.2), ph: rand(0, TAU) });
    }
    const cloudy = { cloudy: [6, "236,240,244"], rain: [4, "150,160,170"], storm: [5, "70,76,90"], fog: [5, "238,244,246"], snow: [3, "232,238,244"] }[o.scene];
    if (cloudy) {
      const [n, color] = cloudy;
      const col = o.night ? "120,130,150" : color;
      for (let i = 0; i < n; i++) {
        const fog = o.scene === "fog";
        const sw = w * rand(0.45, 0.8), sh = h * (fog ? rand(0.25, 0.4) : rand(0.22, 0.38));
        st.clouds.push({
          sprite: cloudSprite(sw, sh, col, fog ? 7 : 6),
          x: rand(-sw, w), y: fog ? rand(h * 0.45, h * 0.85) - sh / 2 : rand(-sh * 0.4, h * 0.38),
          v: rand(0.06, 0.2) * (o.wind >= 0 ? 1 : -1) * (1 + Math.abs(o.wind)),
          a: fog ? rand(0.45, 0.75) : o.scene === "storm" ? rand(0.55, 0.8) : rand(0.35, 0.6),
        });
      }
    }
  }

  function newStreak(anywhere) {
    const { w, h } = st;
    const near = Math.random() < 0.45;
    return { x: rand(-w * 0.2, w * 1.1), y: anywhere ? rand(-h, h) : rand(-h * 0.3, -10), l: near ? rand(16, 28) : rand(8, 15), v: near ? rand(15, 22) : rand(9, 13), near };
  }

  function newFlake(anywhere) {
    const { w, h } = st;
    const r = Math.random() < 0.15 ? rand(2.4, 3.6) : rand(0.9, 2.2);
    return { x: rand(-20, w + 20), y: anywhere ? rand(-h, h) : rand(-30, -5), r, v: 0.25 + r * rand(0.22, 0.38), ph: rand(0, TAU), f: rand(0.4, 1.1), amp: rand(0.3, 1.2) };
  }

  function newDrop(x, y) {
    const p = Math.random();
    const r = p < 0.72 ? rand(1.6, 3.8) : p < 0.95 ? rand(4, 7.5) : rand(8, 12);
    return { x, y, r, vy: 0, slide: false, wob: rand(0, TAU), born: performance.now() };
  }

  /* ------------------------------------------------------------ 每帧更新 */
  function update(dt, t) {
    const { w, h, o } = st;
    const lean = o.wind * 0.45;
    for (const d of st.rain) {
      d.y += d.v * dt * (0.8 + o.intensity * 0.4);
      d.x += lean * d.v * dt;
      if (d.y > h + 30 || d.x < -w * 0.3 || d.x > w * 1.3) Object.assign(d, newStreak(false));
    }
    for (const f of st.snow) {
      f.y += f.v * dt;
      f.x += (Math.sin(t / 1000 * f.f + f.ph) * f.amp * 0.35 + o.wind * 0.8) * dt;
      if (f.y > h + 10 || f.x < -30 || f.x > w + 30) Object.assign(f, newFlake(false));
    }
    for (const c of st.clouds) {
      c.x += c.v * dt;
      const sw = c.sprite.width;
      if (c.v > 0 && c.x > w + 20) c.x = -sw - 20;
      if (c.v < 0 && c.x < -sw - 20) c.x = w + 20;
    }
    if (st.drops.length) updateDrops(dt);
    if (st.stars.length && o.scene === "clear") {
      if (!st.shooting && Math.random() < 0.0012 * dt) st.shooting = { x: rand(w * 0.2, w), y: rand(0, h * 0.3), vx: -rand(9, 14), vy: rand(3, 5), life: 1 };
      if (st.shooting) {
        const s = st.shooting;
        s.x += s.vx * dt; s.y += s.vy * dt; s.life -= 0.025 * dt;
        if (s.life <= 0) st.shooting = null;
      }
    }
  }

  function updateDrops(dt) {
    const { w, h, o } = st;
    const drops = st.drops;
    const max = Math.round(((w * h) / 1e6) * 180 * (0.5 + o.intensity));
    // 新水珠落到玻璃上
    if (Math.random() < (0.35 + o.intensity) * 0.9 * dt) drops.push(newDrop(rand(0, w), rand(0, h)));
    // 大水珠开始下滑
    for (const d of drops) {
      if (!d.slide && d.r > 6.5 && Math.random() < 0.004 * dt * (0.5 + o.intensity)) d.slide = true;
      if (!d.slide) continue;
      d.vy = Math.min(4.2, d.vy + 0.045 * dt);
      const oy = d.y;
      d.y += d.vy * dt;
      d.wob += 0.08 * dt;
      d.x += Math.sin(d.wob) * 0.35 * dt + o.wind * 0.25 * dt;
      // 滑过的地方留下一串小水珠
      if (Math.floor(oy / 13) !== Math.floor(d.y / 13) && Math.random() < 0.55 && drops.length < max * 1.2) {
        const tr = newDrop(d.x + rand(-1.5, 1.5), oy);
        tr.r = rand(0.9, 1.8);
        drops.push(tr);
        d.r = Math.max(4.5, d.r - 0.05);
      }
    }
    // 下滑的水珠吞并路上的小水珠
    for (const d of drops) {
      if (!d.slide) continue;
      for (const e of drops) {
        if (e === d || e.dead || e.slide || e.r > d.r) continue;
        const dx = e.x - d.x, dy = e.y - d.y;
        if (dx * dx + dy * dy < (d.r + e.r * 0.6) ** 2 && e.y > d.y - d.r) {
          d.r = Math.min(13, Math.sqrt(d.r * d.r + e.r * e.r * 0.45));
          e.dead = true;
        }
      }
    }
    st.drops = drops.filter((d) => !d.dead && d.y - d.r < h + 20 && d.x > -20 && d.x < w + 20);
    if (st.drops.length > max) st.drops.splice(0, st.drops.length - max);
  }

  /* ------------------------------------------------------------ 绘制 */
  function drawParticles(t) {
    const { ctx, w, h, o, dpr } = st;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // 星空
    for (const s of st.stars) {
      const a = Math.max(0, Math.min(1, s.a + Math.sin(t / 1000 * s.sp + s.ph) * 0.3));
      ctx.fillStyle = `rgba(255,255,255,${a.toFixed(3)})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, TAU); ctx.fill();
    }
    if (st.shooting) {
      const s = st.shooting;
      const g = ctx.createLinearGradient(s.x, s.y, s.x - s.vx * 8, s.y - s.vy * 8);
      g.addColorStop(0, `rgba(255,255,255,${(0.9 * s.life).toFixed(3)})`);
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.strokeStyle = g; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.x - s.vx * 8, s.y - s.vy * 8); ctx.stroke();
    }
    // 云和雾
    for (const c of st.clouds) {
      ctx.globalAlpha = c.a;
      ctx.drawImage(c.sprite, c.x, c.y);
    }
    ctx.globalAlpha = 1;
    // 雨丝：远处细淡，近处粗亮
    if (st.rain.length) {
      const lean = o.wind * 0.45;
      for (const near of [false, true]) {
        ctx.strokeStyle = near ? "rgba(220,234,244,.5)" : "rgba(210,226,238,.22)";
        ctx.lineWidth = near ? 1.15 : 0.8;
        ctx.beginPath();
        for (const d of st.rain) {
          if (d.near !== near) continue;
          ctx.moveTo(d.x, d.y);
          ctx.lineTo(d.x - lean * d.l, d.y - d.l);
        }
        ctx.stroke();
      }
    }
    // 雪
    if (st.snow.length) {
      ctx.fillStyle = "rgba(255,255,255,.88)";
      ctx.beginPath();
      for (const f of st.snow) { ctx.moveTo(f.x + f.r, f.y); ctx.arc(f.x, f.y, f.r, 0, TAU); }
      ctx.fill();
    }
  }

  function drawDrops() {
    const { gctx, w, h, dpr, stage } = st;
    gctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    gctx.clearRect(0, 0, w, h);
    if (!st.drops.length) return;
    const m = photoMap(stage.querySelector(".hero-photo img"), w, h);
    for (const d of st.drops) drawDrop(gctx, d, m);
  }

  function drawDrop(g, d, m) {
    const r = d.r, ry = r * (d.slide ? 1.25 : 1.1);
    const x = d.x, y = d.y;
    if (r < 1.3) {  // 太小的只画一个淡淡的点
      g.fillStyle = "rgba(200,215,226,.22)";
      g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
      return;
    }
    // 水珠在玻璃上投下的一点阴影
    g.fillStyle = "rgba(0,0,0,.2)";
    g.beginPath(); g.ellipse(x + r * 0.12, y + ry * 0.22, r * 1.02, ry * 1.02, 0, 0, TAU); g.fill();
    g.save();
    g.beginPath(); g.ellipse(x, y, r, ry, 0, 0, TAU); g.clip();
    if (m) {
      // 水珠像一枚小透镜：里面是上下颠倒、缩小的后方景物
      const k = 3.2;  // 透镜视场：越大看到的范围越广
      const sw = (r * 2 * k) / m.scale, sh = (ry * 2 * k) / m.scale;
      const cx = (x - m.ox) / m.scale, cy = (y - m.oy) / m.scale;
      g.translate(x, y); g.scale(1, -1);
      try { g.drawImage(m.img, cx - sw / 2, cy - sh / 2, sw, sh, -r, -ry, r * 2, ry * 2); } catch { /* 图片还没准备好 */ }
      g.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      g.fillStyle = "rgba(16,30,42,.3)";  // 和封面的雨天色调一致，不至于比周围亮一截
      g.fillRect(x - r, y - ry, r * 2, ry * 2);
    } else {
      g.fillStyle = "rgba(220,232,240,.18)";
      g.fillRect(x - r, y - ry, r * 2, ry * 2);
    }
    g.restore();
    // 边缘暗、左上高光，让水珠有厚度
    const grad = g.createRadialGradient(x - r * 0.25, y - ry * 0.3, r * 0.1, x, y, Math.max(r, ry) * 1.05);
    grad.addColorStop(0, "rgba(255,255,255,.16)");
    grad.addColorStop(0.3, "rgba(255,255,255,0)");
    grad.addColorStop(0.72, "rgba(0,0,0,0)");
    grad.addColorStop(1, "rgba(0,0,0,.5)");
    g.fillStyle = grad;
    g.beginPath(); g.ellipse(x, y, r, ry, 0, 0, TAU); g.fill();
    g.fillStyle = "rgba(255,255,255,.42)";
    g.beginPath(); g.ellipse(x - r * 0.3, y - ry * 0.36, r * 0.17, ry * 0.11, -0.5, 0, TAU); g.fill();
  }

  /* ------------------------------------------------------------ 闪电 */
  function scheduleFlash() {
    if (!st || st.o.scene !== "storm" || !st.o.motion) return;
    st.flashTimer = setTimeout(() => {
      if (!st) return;
      if (st.running) {
        st.flash.classList.remove("on");
        void st.flash.offsetWidth;  // 重新触发动画
        st.flash.classList.add("on");
      }
      scheduleFlash();
    }, rand(4500, 12000) * (1.4 - st.o.intensity * 0.5));
  }

  /* ------------------------------------------------------------ 循环、尺寸、可见性 */
  function resize() {
    if (!st) return;
    const r = st.stage.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    if (w === st.w && h === st.h) return;
    st.w = w; st.h = h;
    st.dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    for (const c of [st.canvas, st.glass]) {
      c.width = Math.round(w * st.dpr); c.height = Math.round(h * st.dpr);
    }
    build();
    if (!st.o.motion) renderStatic();
  }

  function frame(t) {
    if (!st || !st.running) return;
    const dt = Math.min(3, (t - (st.last || t)) / 16.67 || 1);
    st.last = t;
    update(dt, t);
    drawParticles(t);
    st.tick = (st.tick + 1) % 2;
    if (st.drops.length && st.tick === 0) drawDrops();  // 水珠 30fps 足够
    st.raf = requestAnimationFrame(frame);
  }

  function renderStatic() {
    const t = performance.now();
    for (let i = 0; i < 30; i++) update(1, t + i * 16);  // 让水珠和雨丝先散开
    for (const d of st.drops) d.slide = false;
    drawParticles(t);
    drawDrops();
  }

  function setRunning(on) {
    if (!st) return;
    const want = on && st.o.motion && !document.hidden && st.inView;
    if (want === st.running) return;
    st.running = want;
    if (want) { st.last = 0; st.raf = requestAnimationFrame(frame); }
    else if (st.raf) cancelAnimationFrame(st.raf);
  }

  function onVisibility() { setRunning(true); }

  function start(stage, opts) {
    stop();
    if (!stage) return;
    const o = Object.assign({ scene: "clear", night: false, intensity: 0.6, wind: 0, motion: true, tintOnly: false }, opts || {});
    o.intensity = Math.max(0, Math.min(1, Number(o.intensity) || 0));
    o.wind = Math.max(-1, Math.min(1, Number(o.wind) || 0));
    stage.dataset.wx = o.scene;
    if (o.night) stage.dataset.wxNight = "1"; else delete stage.dataset.wxNight;
    const photo = stage.querySelector(".hero-photo");
    const tint = layer("wx-tint"), rays = layer("wx-rays"), flash = layer("wx-flash");
    const canvas = layer("wx-canvas", "canvas"), glass = layer("wx-canvas wx-glass", "canvas");
    (photo || stage.firstChild).after(tint, rays);
    const inner = stage.querySelector(".hero-inner");
    for (const e of [canvas, glass, flash]) inner ? stage.insertBefore(e, inner) : stage.append(e);
    st = { stage, o, tint, rays, flash, canvas, glass, ctx: canvas.getContext("2d"), gctx: glass.getContext("2d"),
           w: 0, h: 0, dpr: 1, tick: 0, running: false, inView: true, last: 0 };
    if (o.tintOnly) { canvas.hidden = true; glass.hidden = true; return; }
    resize();
    st.ro = new ResizeObserver(resize); st.ro.observe(stage);
    st.io = new IntersectionObserver((es) => { st.inView = es[0].isIntersecting; setRunning(true); }, { threshold: 0.02 });
    st.io.observe(stage);
    document.addEventListener("visibilitychange", onVisibility);
    const img = stage.querySelector(".hero-photo img");
    if (img && !img.complete) img.addEventListener("load", () => st && !st.o.motion && renderStatic(), { once: true });
    if (o.motion) { setRunning(true); scheduleFlash(); } else renderStatic();
  }

  function stop() {
    if (!st) return;
    setRunning(false);
    clearTimeout(st.flashTimer);
    st.ro?.disconnect(); st.io?.disconnect();
    document.removeEventListener("visibilitychange", onVisibility);
    for (const e of [st.tint, st.rays, st.flash, st.canvas, st.glass]) e.remove();
    delete st.stage.dataset.wx; delete st.stage.dataset.wxNight;
    st = null;
  }

  window.DPWeather = { start, stop, get active() { return !!st; } };
})();
