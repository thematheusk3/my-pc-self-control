// Sparkline em canvas com crosshair + tooltip (mouse e toque).
import { cssVar } from "./ui.js";

export class Spark {
  /**
   * @param {HTMLElement} el     contêiner .spark
   * @param {object} opts        { series: [{key, color, label}], min, max, format }
   */
  constructor(el, opts) {
    this.el = el;
    this.opts = opts;
    this.canvas = document.createElement("canvas");
    this.tip = document.createElement("div");
    this.tip.className = "spark-tip";
    this.tip.hidden = true;
    el.append(this.canvas, this.tip);
    this.hoverX = null;
    this.data = {};
    const move = e => { const r = this.canvas.getBoundingClientRect(); this.hoverX = e.clientX - r.left; this.draw(); };
    const leave = () => { this.hoverX = null; this.tip.hidden = true; this.draw(); };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerdown", move);
    el.addEventListener("pointerleave", leave);
    el.addEventListener("pointercancel", leave);
    new ResizeObserver(() => this.draw()).observe(el);
  }

  setData(hist) { this.data = hist; this.draw(); }

  draw() {
    const { canvas, el, opts } = this;
    const dpr = window.devicePixelRatio || 1;
    const W = el.clientWidth, H = el.clientHeight;
    if (!W || !H) return;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    const now = Date.now() / 1000;
    const span = opts.window || 300;
    const t0 = now - span;
    const all = opts.series.flatMap(s => (this.data[s.key] || []).map(p => p[1]));
    let min = opts.min ?? Math.min(0, ...all);
    let max = opts.max ?? Math.max(1, ...all) * 1.1;
    if (opts.minMax != null) max = Math.max(max, opts.minMax);
    if (max === min) max = min + 1;
    const pad = 3;
    const x = t => ((t - t0) / span) * W;
    const y = v => H - pad - ((v - min) / (max - min)) * (H - pad * 2);

    // grade recessiva: só linha de base e meio
    g.strokeStyle = cssVar("--border");
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(0, H - 0.5); g.lineTo(W, H - 0.5);
    g.setLineDash([2, 4]); g.moveTo(0, Math.round(H / 2) + 0.5); g.lineTo(W, Math.round(H / 2) + 0.5);
    g.stroke(); g.setLineDash([]);

    for (const s of opts.series) {
      const pts = (this.data[s.key] || []).filter(p => p[0] >= t0);
      if (pts.length < 2) continue;
      const color = cssVar(s.color || "--series-1");
      g.beginPath();
      pts.forEach((p, i) => (i ? g.lineTo(x(p[0]), y(p[1])) : g.moveTo(x(p[0]), y(p[1]))));
      g.strokeStyle = color; g.lineWidth = 2; g.lineJoin = "round"; g.lineCap = "round";
      g.stroke();
      if (opts.series.length === 1) {
        g.lineTo(x(pts.at(-1)[0]), H); g.lineTo(x(pts[0][0]), H); g.closePath();
        const grad = g.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, color + "40"); grad.addColorStop(1, color + "00");
        g.fillStyle = grad; g.fill();
      }
    }

    // hover
    if (this.hoverX == null) return;
    const th = t0 + (this.hoverX / W) * span;
    const rows = [];
    let px = null;
    for (const s of opts.series) {
      const pts = this.data[s.key] || [];
      if (!pts.length) continue;
      let best = pts[0];
      for (const p of pts) if (Math.abs(p[0] - th) < Math.abs(best[0] - th)) best = p;
      if (best[0] < t0) continue;
      px ??= x(best[0]);
      rows.push({ s, v: best[1], t: best[0] });
      g.beginPath();
      g.arc(x(best[0]), y(best[1]), 4, 0, Math.PI * 2);
      g.fillStyle = cssVar(s.color || "--series-1"); g.fill();
      g.lineWidth = 2; g.strokeStyle = cssVar("--surface-1"); g.stroke();
    }
    if (px == null) { this.tip.hidden = true; return; }
    g.strokeStyle = cssVar("--text-muted"); g.lineWidth = 1;
    g.beginPath(); g.moveTo(Math.round(px) + 0.5, 0); g.lineTo(Math.round(px) + 0.5, H); g.stroke();
    const ago = Math.max(0, Math.round(now - rows[0].t));
    const fmt = opts.format || (v => v);
    this.tip.innerHTML = rows.map(r => `${opts.series.length > 1 ? r.s.label + ": " : ""}<b>${fmt(r.v)}</b>`).join(" · ")
      + ` <span class="muted">${ago ? `há ${ago}s` : "agora"}</span>`;
    this.tip.hidden = false;
    const tw = this.tip.offsetWidth;
    this.tip.style.left = `${Math.max(tw / 2, Math.min(W - tw / 2, px))}px`;
  }
}
