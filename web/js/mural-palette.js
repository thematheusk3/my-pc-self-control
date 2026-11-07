// As cores vivas do Mural: extraídas da arte do jogo ou da capa, ou do céu da cidade no Início.
// Toda paleta tem o mesmo formato: { deep, mid, accent }, sempre escura o bastante para o texto claro.

const cache = new Map();

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
  if (!d) return [0, 0, l];
  const s = d / (1 - Math.abs(2 * l - 1));
  const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}

function hslToHex(h, s, l) {
  const f = n => {
    const k = (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

function luminance(hex) {
  const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(x => (x <= .03928 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4));
  return .2126 * c[0] + .7152 * c[1] + .0722 * c[2];
}

const contrast = (a, b) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + .05) / (y + .05);
};

/** Agrupa os pixels por matiz e pesa pela saturação: um detalhe vivo vence um fundo grande e apagado. */
export function pickColors(pixels) {
  const bins = Array.from({ length: 24 }, () => ({ w: 0, h: 0, s: 0 }));
  for (let i = 0; i < pixels.length; i += 4) {
    if (pixels[i + 3] < 128) continue;
    const [h, s, l] = rgbToHsl(pixels[i], pixels[i + 1], pixels[i + 2]);
    if (l < .1 || l > .93 || s < .12) continue;
    const w = s ** 1.5 * (1 - Math.abs(2 * l - 1));
    const bin = bins[Math.floor(h / 15) % 24];
    bin.w += w; bin.h += h * w; bin.s += s * w;
  }
  const ranked = bins.filter(b => b.w).sort((a, b) => b.w - a.w).map(b => ({ h: b.h / b.w, s: b.s / b.w, w: b.w }));
  if (!ranked.length) return null;
  const first = ranked[0];
  const far = (a, b) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) >= 35;
  const second = ranked.find(c => far(c.h, first.h) && c.w >= first.w * .12) || first;
  return { first, second };
}

/** Monta a paleta garantindo leitura: fundo escuro e destaque com contraste de 4,5:1 sobre ele. */
export function buildPalette({ first, second }) {
  const deep = hslToHex(first.h, Math.min(first.s, .55), .09);
  const mid = hslToHex(second.h, Math.min(second.s, .6), .2);
  let l = .62, accent = hslToHex(first.h, Math.max(first.s, .6), l);
  while (contrast(accent, deep) < 4.5 && l < .9) { l += .03; accent = hslToHex(first.h, Math.max(first.s, .6), l); }
  return { deep, mid, accent };
}

/** Lê a imagem pelo canvas. Devolve null se ela não tiver cor ou se o servidor dela não permitir a leitura. */
export function paletteFrom(src) {
  if (!src) return Promise.resolve(null);
  if (cache.has(src)) return Promise.resolve(cache.get(src));
  return new Promise(resolve => {
    const img = new Image();
    if (/^https?:/.test(src) && !src.startsWith(location.origin)) img.crossOrigin = "anonymous";
    img.onerror = () => resolve(null);
    img.onload = () => {
      let out = null;
      try {
        const c = document.createElement("canvas");
        c.width = c.height = 48;
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(img, 0, 0, 48, 48);
        const picked = pickColors(g.getImageData(0, 0, 48, 48).data);
        out = picked ? buildPalette(picked) : null;
      } catch { /* imagem de outra origem sem permissão: fica a paleta anterior */ }
      if (cache.size > 40) cache.clear();
      cache.set(src, out);
      resolve(out);
    };
    img.src = src;
  });
}

// ---------- céu do Início ----------
const SKY = {
  dawn: { deep: "#1d1638", mid: "#8a4a3a", accent: "#ffc98a" },
  day: { deep: "#0c2c47", mid: "#1b6a8c", accent: "#ffd978" },
  dusk: { deep: "#1e1233", mid: "#8e3550", accent: "#ffb27a" },
  night: { deep: "#060c1f", mid: "#14234a", accent: "#bccbff" },
};
const COVERED = {
  dawn: { deep: "#1c1a2b", mid: "#4f4150", accent: "#e9c7a6" },
  day: { deep: "#14222c", mid: "#37505f", accent: "#b6dcf5" },
  dusk: { deep: "#1a1626", mid: "#4c3a4d", accent: "#e6bba7" },
  night: { deep: "#090d18", mid: "#1b2436", accent: "#b4c0d8" },
};
const TWILIGHT = 40 * 60000;

export function skyPhase(now, sunrise, sunset) {
  if (!sunrise || !sunset) { const h = now.getHours(); return h >= 6 && h < 18 ? "day" : "night"; }
  const t = now.getTime(), up = new Date(sunrise).getTime(), down = new Date(sunset).getTime();
  if (Math.abs(t - up) <= TWILIGHT) return "dawn";
  if (Math.abs(t - down) <= TWILIGHT) return "dusk";
  return t > up && t < down ? "day" : "night";
}

/** Sol de dia e lua de noite, com t de 0 (nasce) a 1 (se põe) no percurso de cada um. */
export function celestial(weather, now = new Date()) {
  const today = weather?.days?.[0], DAY = 86400000;
  const up = today?.sunrise ? new Date(today.sunrise).getTime() : new Date(now).setHours(6, 0, 0, 0);
  const down = today?.sunset ? new Date(today.sunset).getTime() : new Date(now).setHours(18, 0, 0, 0);
  const t = now.getTime();
  if (t >= up && t <= down) return { body: "sun", t: (t - up) / (down - up) };
  const since = ((t - down) % DAY + DAY) % DAY;
  return { body: "moon", t: Math.min(1, since / (DAY - (down - up))) };
}

/** Céu de agora na cidade: muda com o nascer e o pôr do sol, e fica acinzentado com chuva ou tempo fechado. */
export function skyPalette(weather, now = new Date()) {
  const today = weather?.days?.[0];
  const phase = skyPhase(now, today?.sunrise, today?.sunset);
  const closed = weather && /cloud($|-rain|-lightning|-snow|-fog)/.test(weather.icon);
  return { ...(closed ? COVERED : SKY)[phase], phase };
}
