// Fundos desenhados do Início do Mural. Cada um usa só a paleta { deep, mid, accent }, então acompanha o céu ou o tema.
// Roda a 24 quadros por segundo no máximo, em resolução limitada, porque o Mural fica aberto por horas. Com "reduzir
// movimento", desenha um quadro só.
import { celestial } from "./mural-palette.js";

export const BACKDROPS = [["sky", "Céu da cidade"], ["ridge", "Serra"], ["board", "Placa de circuito"], ["aurora", "Aurora"],
  ["games", "Capas dos jogos"], ["photo", "Sua imagem"]];
export const AURORA = { deep: "#050d16", mid: "#123f4d", accent: "#86f2c8" };

const FRAME = 1000 / 24;
const MAX_WIDTH = 1920;

function seeded(seed) {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

function mix(a, b, t) {
  const n = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const [x, y] = [n(a), n(b)];
  return `#${x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

const rgba = (hex, alpha) => `${hex}${Math.round(alpha * 255).toString(16).padStart(2, "0")}`;

function offscreen(w, h, paint) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h));
  paint(c.getContext("2d"), c.width, c.height);
  return c;
}

/** Nuvem macia em branco: um desenho só, esticado e transparente. */
const puff = offscreen(512, 200, (g, w, h) => {
  const r = seeded(77);
  for (let i = 0; i < 34; i++) {
    const u = r(), x = w * (.22 + .56 * u), y = h * (.6 - .22 * Math.sin(u * Math.PI) * r()), s = h * (.17 + .15 * Math.sin(u * Math.PI));
    const blob = g.createRadialGradient(x, y, 0, x, y, s);
    blob.addColorStop(0, "#ffffff70"); blob.addColorStop(.55, "#ffffff38"); blob.addColorStop(1, "#ffffff00");
    g.fillStyle = blob;
    g.fillRect(x - s, y - s, s * 2, s * 2);
  }
});

function skyFill(g, w, h, p) {
  const f = g.createLinearGradient(0, 0, 0, h);
  f.addColorStop(0, p.deep); f.addColorStop(.62, mix(p.deep, p.mid, .6)); f.addColorStop(1, p.mid);
  g.fillStyle = f;
  g.fillRect(0, 0, w, h);
}

function glow(g, x, y, radius, color, alpha) {
  const f = g.createRadialGradient(x, y, 0, x, y, radius);
  f.addColorStop(0, rgba(color, alpha)); f.addColorStop(1, rgba(color, 0));
  const mode = g.globalCompositeOperation;
  g.globalCompositeOperation = "screen";       // clareia o céu: misturar amarelo com azul daria um verde sujo
  g.fillStyle = f;
  g.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  g.globalCompositeOperation = mode;
}

// ---------- céu ----------
function sky(g, w, h) {
  const r = seeded(11), m = Math.min(w, h);
  const stars = Array.from({ length: 150 }, () => ({ x: r() * w, y: r() * h * .72, s: .5 + r() * 1.3, p: r() * 6.3 }));
  const clouds = Array.from({ length: 9 }, () => ({ x: r(), y: .1 + r() * .34, s: .45 + r() * .7, v: .004 + r() * .007 }));
  const drops = Array.from({ length: 110 }, () => ({ x: r(), y: r(), v: .7 + r() * .6, l: .03 + r() * .03 }));

  return (t, { palette: p, phase, weather }) => {
    skyFill(g, w, h, p);
    const icon = weather?.icon || "";
    const closed = /cloud($|-rain|-lightning|-snow|-fog)/.test(icon);

    const dark = { night: 1, dawn: .4, dusk: .4, day: 0 }[phase] * (closed ? .15 : 1);
    if (dark) {
      g.fillStyle = "#ffffff";
      for (const s of stars) {
        g.globalAlpha = dark * (.45 + .4 * Math.sin(t * .8 + s.p));
        g.fillRect(s.x, s.y, s.s, s.s);
      }
      g.globalAlpha = 1;
    }

    const c = celestial(weather);
    const x = w * (.12 + .76 * c.t), y = h * (.8 - .58 * Math.sin(Math.PI * c.t));
    const veil = closed ? .3 : 1;
    if (c.body === "sun") {
      glow(g, x, y, m * .62, mix(p.accent, "#ffffff", .2), .5 * veil);
      if (!closed) {
        g.fillStyle = mix(p.accent, "#ffffff", .55);
        g.beginPath(); g.arc(x, y, m * .05, 0, 7); g.fill();
      }
    } else {
      glow(g, x, y, m * .34, "#cdd8ff", .26 * veil);
      if (!closed) {
        g.fillStyle = "#e6ebf7";
        g.beginPath(); g.arc(x, y, m * .042, 0, 7); g.fill();
        g.fillStyle = "#b9c3d8";            // mares da lua
        for (const [dx, dy, s] of [[-.32, -.2, .3], [.22, .1, .22], [-.08, .4, .18]]) {
          g.beginPath(); g.arc(x + dx * m * .042, y + dy * m * .042, s * m * .042, 0, 7); g.fill();
        }
      }
    }

    const count = closed ? clouds.length : /sun|moon/.test(icon) && icon.startsWith("cloud") ? 5 : 2;
    const night = phase === "night" ? .4 : 1;
    clouds.slice(0, count).forEach(cl => {
      const width = w * .46 * cl.s, cx = ((cl.x + t * cl.v) % 1.5) - .4;
      g.globalAlpha = (closed ? .7 : .55) * night;
      g.drawImage(puff, cx * w, cl.y * h, width, width * .38);
    });
    g.globalAlpha = 1;

    if (/rain|lightning/.test(icon)) {
      g.strokeStyle = "#dbe9ff40"; g.lineWidth = 1.2;
      g.beginPath();
      for (const d of drops) {
        const dy = (d.y + t * d.v) % 1, dx = (d.x - t * d.v * .08 + 2) % 1;
        g.moveTo(dx * w, dy * h);
        g.lineTo((dx - d.l * .18) * w, (dy + d.l) * h);
      }
      g.stroke();
    } else if (icon === "cloud-snow") {
      g.fillStyle = "#ffffffb0";
      for (const d of drops) {
        const dy = (d.y + t * d.v * .09) % 1, dx = (d.x + Math.sin(t * .4 + d.y * 9) * .012 + 1) % 1;
        g.beginPath(); g.arc(dx * w, dy * h, 1 + d.v, 0, 7); g.fill();
      }
    }
  };
}

// ---------- serra ----------
function ridgeLine(w, h, base, amp, r) {
  const a = [r() * 6, r() * 6, r() * 6], pts = [];
  for (let x = 0; x <= w + 8; x += 8) {
    const u = x / w;
    const y = Math.sin(u * 3.1 + a[0]) * .6 + Math.sin(u * 7.7 + a[1]) * .3 + Math.sin(u * 19 + a[2]) * .12 + (r() - .5) * .07;
    pts.push([x, h * base - y * h * amp]);
  }
  return pts;
}

function ridge(g, w, h, view) {
  const p = view.palette, m = Math.min(w, h), r = seeded(23), LAYERS = 5;
  const sunX = w * .66, sunY = h * .52;
  const base = offscreen(w, h, (b) => {
    skyFill(b, w, h, p);
    glow(b, sunX, sunY, m * .8, mix(p.accent, "#ffffff", .15), .55);
    b.fillStyle = mix(p.accent, "#ffffff", .4);
    b.beginPath(); b.arc(sunX, sunY, m * .11, 0, 7); b.fill();
  });
  const layers = Array.from({ length: LAYERS }, (_, i) => {
    const k = i / (LAYERS - 1), line = ridgeLine(w, h, .5 + k * .36, .11 - k * .05, r);
    const near = mix(p.deep, "#000000", .35), far = mix(p.mid, p.accent, .16);
    return offscreen(w, h, b => {
      const f = b.createLinearGradient(0, h * (.36 + k * .36), 0, h);
      f.addColorStop(0, mix(far, near, k)); f.addColorStop(1, mix(far, near, Math.min(1, k + .3)));
      b.fillStyle = f;
      b.beginPath(); b.moveTo(0, h);
      line.forEach(([x, y]) => b.lineTo(x, y));
      b.lineTo(w, h); b.closePath(); b.fill();
    });
  });
  return t => {
    g.drawImage(base, 0, 0);
    layers.forEach((layer, i) => {
      g.drawImage(layer, 0, 0);
      if (i === LAYERS - 1) return;           // névoa entre as serras, deslizando devagar
      const width = w * 1.5, x = ((t * (.006 + i * .002) + i * .37) % 1.5 - .75) * w;
      g.globalAlpha = .16 - i * .02;
      g.drawImage(puff, x, h * (.5 + i * .09), width, h * .2);
      g.globalAlpha = 1;
    });
  };
}

// ---------- placa de circuito ----------
const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];

function routeTraces(cols, rows, r) {
  const used = new Set(), traces = [];
  const free = (x, y) => x >= 0 && y >= 0 && x <= cols && y <= rows && !used.has(`${x},${y}`);
  for (let tries = 0; tries < 90; tries++) {
    const dir0 = Math.floor(r() * 8);
    const schedule = [];                     // o mesmo trajeto para todas as trilhas do barramento: ficam paralelas
    let d = dir0;
    for (let i = 0, n = 2 + Math.floor(r() * 5); i < n; i++) {
      schedule.push([d, 3 + Math.floor(r() * 9)]);
      d = (d + (r() < .5 ? 1 : 7)) % 8;
    }
    const sx = Math.floor(r() * cols), sy = Math.floor(r() * rows);
    const [px, py] = DIRS[(dir0 + 2) % 8], lanes = 1 + Math.floor(r() * 5);
    for (let lane = 0; lane < lanes; lane++) {
      let x = sx + px * lane * 2, y = sy + py * lane * 2;
      if (!free(x, y)) continue;
      const pts = [[x, y]];
      used.add(`${x},${y}`);
      route: for (const [dirI, len] of schedule) {
        const [dx, dy] = DIRS[dirI];
        for (let i = 0; i < len; i++) {
          if (!free(x + dx, y + dy)) break route;
          x += dx; y += dy;
          used.add(`${x},${y}`);
          pts.push([x, y]);
        }
      }
      if (pts.length > 6) traces.push(pts);
    }
  }
  return traces;
}

function board(g, w, h, view) {
  const p = view.palette, r = seeded(5), u = Math.max(6, Math.min(w, h) / 30);
  const traces = routeTraces(Math.ceil(w / u), Math.ceil(h / u), r);
  const copper = mix(p.mid, p.accent, .14), pad = rgba(p.accent, .62);

  const base = offscreen(w, h, b => {
    const f = b.createLinearGradient(0, 0, w, h);
    f.addColorStop(0, mix(p.deep, p.mid, .5)); f.addColorStop(1, p.deep);
    b.fillStyle = f;
    b.fillRect(0, 0, w, h);
    b.lineWidth = u * .16; b.lineJoin = "round"; b.lineCap = "round"; b.strokeStyle = copper;
    for (const t of traces) {
      b.beginPath();
      t.forEach(([x, y], i) => (i ? b.lineTo(x * u, y * u) : b.moveTo(x * u, y * u)));
      b.stroke();
    }
    for (const t of traces) {
      for (const [x, y] of [t[0], t[t.length - 1]]) {
        b.fillStyle = pad;
        b.beginPath(); b.arc(x * u, y * u, u * .3, 0, 7); b.fill();
        b.fillStyle = p.deep;
        b.beginPath(); b.arc(x * u, y * u, u * .13, 0, 7); b.fill();
      }
    }
    // dois componentes, para ler como placa e não como um padrão qualquer
    for (const [cx, cy, cw, ch] of view.bare ? [] : [[.62, .14, 7, 7], [.14, .16, 9, 4]]) {
      const x = Math.round(w * cx / u) * u, y = Math.round(h * cy / u) * u, bw = cw * u, bh = ch * u;
      b.fillStyle = pad;
      for (let i = .5; i < cw; i++) { b.fillRect(x + i * u - u * .14, y - u * .4, u * .28, bh + u * .8); }
      for (let i = .5; i < ch; i++) { b.fillRect(x - u * .4, y + i * u - u * .14, bw + u * .8, u * .28); }
      b.fillStyle = mix(p.deep, "#000000", .5);
      b.fillRect(x, y, bw, bh);
      b.strokeStyle = rgba("#ffffff", .1); b.lineWidth = 1;
      b.strokeRect(x + .5, y + .5, bw - 1, bh - 1);
      b.fillStyle = rgba("#ffffff", .28);
      b.beginPath(); b.arc(x + u * .7, y + u * .7, u * .16, 0, 7); b.fill();
    }
  });

  const pulses = traces.length ? Array.from({ length: 18 }, () => ({ t: traces[Math.floor(r() * traces.length)], at: r(), v: .05 + r() * .09 })) : [];
  const point = (tr, i) => {
    const a = Math.floor(i), f = i - a, A = tr[Math.max(0, Math.min(tr.length - 1, a))], B = tr[Math.max(0, Math.min(tr.length - 1, a + 1))];
    return [(A[0] + (B[0] - A[0]) * f) * u, (A[1] + (B[1] - A[1]) * f) * u];
  };
  return time => {
    g.drawImage(base, 0, 0);
    g.globalCompositeOperation = "lighter";
    for (const q of pulses) {
      const head = ((q.at + time * q.v) % 1.25) * (q.t.length - 1);     // 25% do ciclo apagado: os pulsos não andam em fila
      for (let k = 0; k < 11; k++) {
        const i = head - k * .5;
        if (i < 0 || i > q.t.length - 1) continue;
        const [x, y] = point(q.t, i);
        g.fillStyle = rgba(p.accent, (1 - k / 11) ** 2 * .9);
        g.beginPath(); g.arc(x, y, u * .12 * (1 - k / 16), 0, 7); g.fill();
      }
      if (head <= q.t.length - 1) { const [x, y] = point(q.t, head); glow(g, x, y, u * .9, p.accent, .4); }
    }
    g.globalCompositeOperation = "source-over";
  };
}

// ---------- aurora ----------
/** Desenhada em baixa resolução: ampliada pelo navegador, fica macia sem pagar por um desfoque a cada quadro. */
function aurora(g, w, h, view) {
  const p = view.palette;
  const curtain = color => offscreen(2, 128, (b, bw, bh) => {
    const f = b.createLinearGradient(0, 0, 0, bh);
    f.addColorStop(0, rgba(color, 0)); f.addColorStop(.72, rgba(color, .7)); f.addColorStop(.9, rgba(color, 1)); f.addColorStop(1, rgba(color, 0));
    b.fillStyle = f;
    b.fillRect(0, 0, bw, bh);
  });
  const bands = (view.bands || [p.accent, "#58c6e8", "#a678f0"]).map(curtain);
  return t => {
    const f = g.createLinearGradient(0, 0, 0, h);
    f.addColorStop(0, p.deep); f.addColorStop(1, mix(p.deep, p.mid, .55));
    g.fillStyle = f;
    g.fillRect(0, 0, w, h);
    g.globalCompositeOperation = "lighter";
    bands.forEach((band, i) => {
      for (let x = 0; x < w; x += 2) {
        const u = x / w * 620;
        const y = h * (.34 + i * .07 + .1 * Math.sin(u * .008 + t * .09 + i * 1.7) + .05 * Math.sin(u * .023 - t * .15 + i));
        const tall = h * (.42 + .16 * Math.sin(u * .013 + t * .11 + i));
        g.globalAlpha = (.12 + .5 * Math.sin(u * .017 + t * .19 + i * 2) ** 2) * (i ? .55 : .8);
        g.drawImage(band, x, y - tall, 2, tall);
      }
    });
    g.globalAlpha = 1;
    g.globalCompositeOperation = "source-over";
  };
}

const RENDERERS = { sky, ridge, board, aurora };
const SCALE = { sky: 1, ridge: 1, board: 1, aurora: .25 };

/** Desenha um quadro numa miniatura: as opções da aba Mural mostram o fundo de verdade. */
export function thumb(canvas, kind, view) {
  const g = canvas.getContext("2d");
  RENDERERS[kind](g, canvas.width, canvas.height, view)(40, view);
}

export class Backdrop {
  constructor(canvas) {
    this.c = canvas;
    this.g = canvas.getContext("2d");
    this.kind = null; this.key = null; this.draw = null; this.raf = null; this.last = 0;
    new ResizeObserver(() => { if (this.kind) this.build(); }).observe(canvas);
  }

  show(kind, view) {
    const key = `${kind}|${view.palette.deep}|${view.palette.mid}|${view.palette.accent}`;
    this.view = view;
    if (key !== this.key) { this.kind = kind; this.key = key; this.build(); }
    this.c.classList.add("on");
    this.frame(performance.now());
    this.run();
  }

  hide() {
    cancelAnimationFrame(this.raf);
    this.raf = null; this.kind = null; this.key = null;
    this.c.classList.remove("on");
  }

  build() {
    const box = this.c.getBoundingClientRect();
    if (!box.width) return;
    const fit = Math.min(devicePixelRatio || 1, 1.5, MAX_WIDTH / box.width) * SCALE[this.kind];
    this.c.width = Math.round(box.width * fit);
    this.c.height = Math.round(box.height * fit);
    this.draw = RENDERERS[this.kind](this.g, this.c.width, this.c.height, this.view);
    this.frame(performance.now());
  }

  frame(now) {
    this.draw?.(now / 1000, this.view);
    this.last = now;
  }

  run() {
    if (this.raf || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const loop = now => {
      this.raf = requestAnimationFrame(loop);
      if (now - this.last >= FRAME) this.frame(now);
    };
    this.raf = requestAnimationFrame(loop);
  }
}
