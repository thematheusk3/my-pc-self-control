// O palco do Mural: gerenciador de estado da tela cheia. Decide a cena (Início, Jogo, Música) a partir do que acontece no
// PC, aplica a paleta viva e entrega os eventos ao motor (mural-events.js). A aba de configurações fica em modules/mural.js.
import { $, $$, bytes, duration, esc } from "./ui.js";
import { ic } from "./icons.js";
import { artSrc, controlMedia, livePos, mediaButtons, mmss } from "./media.js";
import { Spark } from "./spark.js";
import { EventDeck, fromAlert } from "./mural-events.js";
import { paletteFrom, skyPalette } from "./mural-palette.js";
import { AURORA, BACKDROPS, Backdrop } from "./mural-backdrop.js";
import { currentPalette } from "./theme.js";

const MODES = [["auto", "Automático", "arrow-clockwise"], ["home", "Início", "house"], ["game", "Jogo", "game-controller"], ["music", "Música", "music-note"]];
const IDLE = 4000;              // ms até esconder os controles
const CONF_EVERY = 5 * 60000;   // clima e preferências
const SHIFT_EVERY = 60000;      // desloca o conteúdo para não marcar telas OLED
const SPLASH_SECONDS = 6;
const COVER_MINUTES = 2;        // tempo de cada capa no fundo "Capas dos jogos"

let app, el, deck, conf = null, scene = null, off = null, timers = [], idleTimer = null, lastSeq = null, wake = null;
let fpsSpark = null, lastGame = undefined, lastTrack = undefined, paletteKey = null, bgLayer = 0;
let onExit = () => {};
let backdrop = null, covers = null, coverAt = 0, minutes = 0;

const artUrl = (key, kind) => `/api/mural/art/${encodeURIComponent(key)}${kind ? `?kind=${kind}` : ""}`;
const deg = n => (n == null ? "—" : `${Math.round(n)}°`);

function clockTick() {
  const now = new Date();
  $$("[data-time]", el).forEach(n => { n.textContent = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); });
  $$("[data-date]", el).forEach(n => { n.textContent = now.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" }); });
}

// ---------- paleta e fundo ----------
function applyPalette(p) {
  const root = document.documentElement.style;
  el.style.setProperty("--m-deep", p.deep);
  el.style.setProperty("--m-mid", p.mid);
  el.style.setProperty("--m-accent", p.accent);
  root.setProperty("--mural-accent", p.accent);      // o gráfico lê a cor do documento
}

async function paletteOf(src) {
  if (paletteKey === src) return;
  paletteKey = src;
  const p = await paletteFrom(src);
  if (p && paletteKey === src) applyPalette(p);
}

function homeView(kind) {
  if (kind === "aurora") return { palette: AURORA };
  if (kind === "board") return { palette: currentPalette() };
  const palette = skyPalette(conf.weather);
  return { palette, phase: palette.phase, weather: conf.weather };
}

async function loadCovers() {
  covers = [];                   // evita pedir duas vezes
  try {
    const { games } = await app.api("/api/mural/games");
    covers = games.filter(g => g.steam).map(g => g.key).sort(() => Math.random() - .5);
  } catch { /* fica com o céu */ }
  if (scene === "home") paintHome();
}

/** Fundo do Início: desenhado ou imagem. Sem capa ou sem imagem disponível, volta ao céu. */
function paintHome() {
  let kind = conf.background;
  if (kind === "games" && covers === null) loadCovers();
  if ((kind === "games" && !covers?.length) || (kind === "photo" && !conf.background_v)) kind = "sky";
  const src = kind === "games" ? artUrl(covers[coverAt % covers.length], "hero")
    : kind === "photo" ? `/api/mural/background?v=${conf.background_v}` : null;
  el.dataset.backdrop = kind;
  if (src) {
    backdrop.hide();
    setBackground(src);
    paletteOf(src);
    return;
  }
  setBackground(null);
  paletteKey = null;
  const view = homeView(kind);
  if (view.phase) el.dataset.sky = view.phase;
  applyPalette(view.palette);
  backdrop.show(kind, view);
}

function coverFailed() {
  if (scene !== "home" || el.dataset.backdrop !== "games") return;
  covers.splice(coverAt % covers.length, 1);        // jogo sem arte de fundo: sai do rodízio
  paintHome();
}

function everyMinute() {
  if (scene !== "home") return;
  if (conf.background === "games" && covers?.length && ++minutes % COVER_MINUTES === 0) coverAt++;
  paintHome();                                       // o céu muda ao longo do dia
}

/** Troca de arte com fade: a imagem nova carrega na camada escondida e só então aparece. */
function setBackground(src) {
  const layers = $$(".mural-bg", el), cur = layers[bgLayer];
  if (!src) { layers.forEach(l => { l.classList.remove("on"); l.removeAttribute("src"); }); return; }
  if (cur.getAttribute("src") === src) return;
  const next = layers[1 - bgLayer];
  next.onload = () => { next.classList.add("on"); cur.classList.remove("on"); bgLayer = 1 - bgLayer; };
  next.onerror = () => { cur.classList.remove("on"); coverFailed(); };
  next.src = src;
}

// ---------- blocos ----------
function weatherMini() {
  const w = conf?.weather;
  return w ? `<span class="mural-chip">${ic(w.icon)} ${deg(w.temp)} <span>${esc(conf.location?.name || "")}</span></span>` : "";
}

function weatherBlock() {
  if (!conf.location) return `<button class="btn btn-primary" data-pick-city>Escolher cidade para ver o clima</button>`;
  const w = conf.weather;
  if (!w) return `<p class="mural-dim">${esc(conf.weather_error || "Buscando o clima…")}</p>`;
  const [today, ...rest] = w.days;
  return `<section class="mural-glass mural-weather">
      <div class="mural-w-now">
        <div class="mural-temp">${ic(w.icon)}<b>${deg(w.temp)}</b></div>
        <div><p class="mural-w-city">${esc(conf.location.name)}</p><p class="mural-dim">${esc(w.label)}</p></div>
      </div>
      <dl class="mural-w-facts">
        ${today ? `<div><dt>Máx.</dt><dd>${deg(today.max)}</dd></div><div><dt>Mín.</dt><dd>${deg(today.min)}</dd></div>` : ""}
        <div><dt>Sensação</dt><dd>${deg(w.feels)}</dd></div><div><dt>Umidade</dt><dd>${w.humidity ?? "—"}%</dd></div>
        ${today?.rain != null ? `<div><dt>Chuva</dt><dd>${today.rain}%</dd></div>` : ""}
      </dl>
      <ul class="mural-days">${rest.map(d => `<li>
        <span>${new Date(`${d.date}T12:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}</span>
        ${ic(d.icon)}<b>${deg(d.max)}</b><span class="mural-dim">${deg(d.min)}</span></li>`).join("")}</ul>
    </section>`;
}

/** Anel de 0 a 100: mostra o nível de relance, sem precisar ler o número. */
function ring(value, label, suffix = "%") {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return `<div class="mural-ring"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="42" pathLength="100"/><circle class="on" cx="50" cy="50" r="42" pathLength="100" stroke-dasharray="${v} 100"/></svg>
    <b>${Math.round(value ?? 0)}${suffix}</b><span>${label}</span></div>`;
}

const cell = (label, value) => `<div class="mural-stat"><b>${value}</b><span>${label}</span></div>`;

// ---------- escolha da cena ----------
function pick(d) {
  const game = d.game?.game, media = d.media?.current, mode = conf.mode;
  if (mode === "game") return game ? "game" : "home";
  if (mode === "music") return media ? "music" : "home";
  if (mode === "home") return "home";
  return game ? "game" : media?.status === "playing" ? "music" : "home";
}

function emptyNote(d) {
  if (conf.mode === "game" && !d.game?.game) return "Nenhum jogo aberto agora.";
  if (conf.mode === "music" && !d.media?.current) return "Nada tocando agora.";
  return "";
}

const SCENES = {
  home: () => `<div class="mural-home">
      <div class="mural-clock"><time data-time></time><p data-date></p><p class="mural-dim" data-empty></p></div>
      <div class="mural-side" data-weather></div>
    </div>`,
  game: () => `<div class="mural-game">
      <header class="mural-top"><span class="mural-chip"><time data-time></time></span><span data-mini></span></header>
      <div class="mural-title" data-title></div>
      <section class="mural-glass mural-hud">
        <div class="mural-fps" data-fps-box hidden><div class="mural-fps-num"><b data-fps></b><span>FPS</span></div><div class="spark" data-spark></div></div>
        <div class="mural-stats" data-stats></div>
      </section>
      <div class="mural-glass mural-player" data-player hidden></div>
    </div>`,
  music: () => `<div class="mural-music">
      <header class="mural-top"><span class="mural-chip"><time data-time></time> <span data-date></span></span><span data-mini></span></header>
      <div class="mural-track">
        <img class="mural-art" data-art alt="">
        <div class="mural-info">
          <h1 data-mtitle></h1><p class="mural-artist" data-artist></p><p class="mural-dim" data-album></p>
          <div class="mural-prog" data-prog><i></i></div><p class="mural-dim num" data-mtime></p>
          <div class="mural-btns" data-btns></div>
        </div>
      </div>
    </div>`,
};

function enter(name) {
  scene = name;
  el.dataset.scene = name;
  fpsSpark = null;
  if (name !== "home") { backdrop.hide(); delete el.dataset.backdrop; }
  $("[data-bg]", el).hidden = name !== "home";
  $(".mural-scene", el).innerHTML = SCENES[name]();
  clockTick();
  paintConf();
}

function paintConf() {
  const w = $("[data-weather]", el);
  if (w) w.innerHTML = weatherBlock();
  $$("[data-mini]", el).forEach(n => { n.innerHTML = weatherMini(); });
  $$("[data-mode]", el).forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === conf.mode));
  movePill();
  if (scene === "home") paintHome();
}

/** O destaque desliza até o modo escolhido: mostra de onde veio e para onde foi. */
function movePill() {
  const b = $("[data-mode][aria-pressed='true']", el), pill = $(".mural-dock-pill", el);
  if (!b || !pill) return;
  pill.style.width = `${b.offsetWidth}px`;
  pill.style.translate = `${b.offsetLeft}px 0`;
}

function paintGame(d) {
  const g = d.game, game = g.game, o = conf.game;
  const t = $("[data-title]", el);
  if (t.dataset.key !== game.key) {
    t.dataset.key = game.key;
    const src = artUrl(game.key);
    setBackground(src);
    paletteOf(src);
    const fallback = `<h1>${esc(game.name)}</h1>`;
    t.innerHTML = game.appid ? `<img class="mural-logo" src="${artUrl(game.key, "logo")}" alt="${esc(game.name)}">` : fallback;
    const logo = $("img", t);
    if (logo) logo.onerror = () => { logo.replaceWith(Object.assign(document.createElement("h1"), { textContent: game.name })); };
    t.insertAdjacentHTML("beforeend", `<p class="mural-dim" data-since></p>`);
  }
  const since = $("[data-since]", t);
  if (since) since.textContent = o.session && game.since ? `Jogando há ${duration(Date.now() / 1000 - game.since)}` : "";

  const box = $("[data-fps-box]", el), hasFps = o.fps && g.fps != null;
  box.hidden = !hasFps;
  if (hasFps) {
    $("[data-fps]", el).textContent = Math.round(g.fps);
    fpsSpark ||= new Spark($("[data-spark]", el), { series: [{ key: "game.fps", color: "--mural-accent" }], min: 0, format: v => `${Math.round(v)} FPS` });
    fpsSpark.setData(app.history);
  }

  const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
  const cpuT = d.cpu?.temp, s = [];
  if (o.cpu && d.cpu) s.push(ring(d.cpu.total, "CPU"));
  if (o.gpu && gpu?.busy != null) s.push(ring(gpu.busy, "GPU"));
  if (o.ram && d.memory) s.push(ring(d.memory.percent, "RAM"));
  if (o.low1 && g.low1 != null) s.push(cell("1% low", Math.round(g.low1)));
  if (o.frametime && g.frametime != null) s.push(cell("Frametime", `${g.frametime.toFixed(1)} ms`));
  if (o.temps && cpuT != null) s.push(cell("CPU", `${Math.round(cpuT)} °C`));
  if (o.temps && gpu?.temp != null) s.push(cell("GPU", `${Math.round(gpu.temp)} °C`));
  if (o.vram && gpu?.vram?.total) s.push(cell("VRAM", bytes(gpu.vram.used)));
  $("[data-stats]", el).innerHTML = s.join("");

  const p = $("[data-player]", el), m = d.media?.current;
  const show = !!(o.music && m && m.status === "playing");
  p.hidden = !show;
  if (show) {
    const src = artSrc(m);
    p.innerHTML = `${src ? `<img src="${esc(src)}" alt="">` : `<span class="mural-noart">${ic("music-note")}</span>`}
      <div><b>${esc(m.title)}</b><span>${esc(m.artist || m.app)}</span></div>`;
  }
}

function paintMusic(d, ts) {
  const m = d.media.current;
  const src = artSrc(m);
  setBackground(src);
  if (src) paletteOf(src);
  const art = $("[data-art]", el);
  art.hidden = !src;
  if (src && art.getAttribute("src") !== src) { art.src = src; art.onerror = () => { art.hidden = true; }; }
  $("[data-mtitle]", el).textContent = m.title;
  $("[data-artist]", el).textContent = m.artist || m.app;
  $("[data-album]", el).textContent = m.album || "";
  const prog = $("[data-prog]", el), time = $("[data-mtime]", el);
  prog.hidden = time.hidden = !m.length;
  if (m.length) {
    const pos = livePos(m, ts);
    prog.firstElementChild.style.width = `${(pos / m.length) * 100}%`;
    time.textContent = `${mmss(pos)} / ${mmss(m.length)}`;
  }
  const btns = $("[data-btns]", el), key = `${m.id}|${m.status}|${JSON.stringify(m.can)}`;
  if (app.info.features.control && btns.dataset.key !== key) {
    btns.dataset.key = key;
    btns.innerHTML = mediaButtons(m);
  }
}

// ---------- eventos do PC ----------
async function alertEvents(a) {
  const seq = a?.seq ?? 0;
  if (lastSeq === null) { lastSeq = seq; return; }
  if (seq <= lastSeq) return;
  const from = lastSeq;
  lastSeq = seq;
  if (!conf.notifications) return;
  let list = [a];
  if (seq > from + 1) {          // o snapshot só traz o último: numa rajada, busca os demais
    try { list = (await app.api("/api/alerts")).alerts.filter(x => x.seq > from).reverse(); } catch { /* fica com o último */ }
  }
  list.forEach(x => deck.push(fromAlert(x, conf)));
}

function contextEvents(d) {
  const game = d.game?.game?.key ?? null, g = d.game?.game;
  if (lastGame !== undefined && game && game !== lastGame && conf.context) {
    deck.push({ id: `game-${game}`, kind: "takeover", level: "splash", title: g.name, eyebrow: "Agora jogando",
      art: artUrl(game, g.appid ? "cover" : undefined), seconds: SPLASH_SECONDS });
  }
  lastGame = game;

  const m = d.media?.current, track = m?.status === "playing" ? `${m.id}|${m.title}` : null;
  if (lastTrack !== undefined && track && track !== lastTrack && scene !== "music" && conf.context) {
    deck.push({ id: `track-${track}`, kind: "toast", level: "info", title: m.title, text: m.artist || m.app, icon: "music-note", art: artSrc(m), seconds: 5 });
  }
  lastTrack = track;
}

function paint(snap) {
  if (!conf) return;
  const d = snap.data, next = pick(d);
  if (next !== scene) enter(next);
  if (next === "game") paintGame(d);
  else if (next === "music") paintMusic(d, snap.ts);
  else {
    const e = $("[data-empty]", el);
    if (e) e.textContent = emptyNote(d);
  }
  contextEvents(d);
  alertEvents(d.alerts?.latest);
}

// ---------- controles ----------
function wakeUp() {
  el.classList.add("awake");
  movePill();
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (!$(".mural-bar", el).contains(document.activeElement)) el.classList.remove("awake");
  }, IDLE);
}

async function setMode(mode) {
  conf = { ...conf, mode };
  paintConf();
  if (app.snapshot) paint(app.snapshot);
  try { conf = await app.api("/api/mural", { method: "POST", body: { mode } }); } catch { /* vale nesta tela até recarregar */ }
}

async function nextBackground() {
  const list = BACKDROPS.filter(([id]) => id !== "photo" || conf.background_v);
  const [id, name] = list[(list.findIndex(([k]) => k === conf.background) + 1) % list.length];
  conf = { ...conf, background: id };
  if (scene === "home") paintHome();
  deck.push({ id: `bg-${id}`, kind: "toast", level: "info", title: name, icon: "image", seconds: 3 });
  try { conf = await app.api("/api/mural", { method: "POST", body: { background: id } }); } catch { /* vale nesta tela até recarregar */ }
}

async function refreshConf() {
  try {
    conf = await app.api("/api/mural");
    paintConf();
    if (conf.location && !conf.weather && !conf.weather_error && !el.hidden) setTimeout(refreshConf, 3000);   // o clima ainda vai chegar
  } catch { /* tenta no próximo ciclo */ }
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.();
  else el.requestFullscreen?.().catch(() => {});
}

async function keepAwake() {
  // só em HTTPS ou localhost; pelo IP da rede o navegador não oferece a API
  try { wake = await navigator.wakeLock?.request("screen"); } catch { wake = null; }
}

function build() {
  el = document.createElement("div");
  el.className = "mural";
  el.setAttribute("role", "region");
  el.setAttribute("aria-label", "Mural");
  el.hidden = true;
  el.innerHTML = `<canvas class="mural-canvas" aria-hidden="true"></canvas><img class="mural-bg" alt=""><img class="mural-bg" alt=""><div class="mural-scrim"></div>
    <div class="mural-scene"></div>
    <div class="mural-toasts" role="status" aria-live="polite"></div>
    <section class="mural-takeover" hidden></section>
    <nav class="mural-bar" aria-label="Controles do Mural">
      <div class="mural-dock-modes"><span class="mural-dock-pill"></span>
        ${MODES.map(([id, label, icon]) => `<button data-mode="${id}" aria-pressed="false" aria-label="${label}">${ic(icon)}<span>${label}</span></button>`).join("")}</div>
      <div class="mural-dock-tools">
        <button data-bg title="Trocar o fundo" aria-label="Trocar o fundo">${ic("image")}</button>
        <button data-fs title="Tela cheia" aria-label="Tela cheia">${ic("frame-corners")}</button>
        <button data-exit title="Sair do Mural" aria-label="Sair do Mural">${ic("x")}</button>
      </div>
    </nav>`;
  document.body.append(el);
  deck = new EventDeck(el);
  backdrop = new Backdrop($(".mural-canvas", el));
  document.fonts?.ready.then(movePill);             // a largura dos botões muda quando a fonte chega
  new ResizeObserver(movePill).observe($(".mural-dock-modes", el));
  el.addEventListener("pointermove", wakeUp);
  el.addEventListener("pointerdown", wakeUp);
  el.addEventListener("focusin", wakeUp);
  el.addEventListener("click", async e => {
    const mode = e.target.closest("[data-mode]");
    if (mode) return setMode(mode.dataset.mode);
    if (e.target.closest("[data-bg]")) return nextBackground();
    if (e.target.closest("[data-fs]")) return toggleFullscreen();
    if (e.target.closest("[data-exit]")) return close();
    if (e.target.closest("[data-pick-city]")) return close();
    const b = e.target.closest("button[data-op]");
    if (b) {
      b.disabled = true;
      try { await controlMedia(app, b); } catch { /* o estado seguinte mostra o que valeu */ }
      b.disabled = false;
    }
  });
}

function onKey(e) {
  if (e.key !== "Escape") return wakeUp();
  if (deck.active) return deck.next();                             // primeiro dispensa o aviso
  if (document.fullscreenElement) document.exitFullscreen?.();     // alguns navegadores entregam o Esc à página
  else close();
}

export async function open(a, { fullscreen = false, exit = () => {} } = {}) {
  app = a; onExit = exit;
  if (!el) build();
  if (!el.hidden) return;
  el.hidden = false;
  document.body.classList.add("mural-open");
  if (fullscreen) el.requestFullscreen?.().catch(() => {});
  scene = null; lastSeq = null; lastGame = undefined; lastTrack = undefined; paletteKey = null;
  await refreshConf();
  if (el.hidden) return;
  if (app.snapshot) paint(app.snapshot);
  off = app.onSnapshot(paint);
  timers = [setInterval(clockTick, 1000), setInterval(refreshConf, CONF_EVERY), setInterval(() => {
    const r = () => Math.round(Math.random() * 16 - 8);
    $(".mural-scene", el).style.translate = `${r()}px ${r()}px`;
    everyMinute();
  }, SHIFT_EVERY)];
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", keepAwake);
  keepAwake();
  wakeUp();
}

export function close() {
  if (!el || el.hidden) return;
  el.hidden = true;
  document.body.classList.remove("mural-open");
  off?.(); off = null;
  timers.forEach(clearInterval); timers = [];
  clearTimeout(idleTimer);
  deck.clear();
  backdrop.hide();
  covers = null;
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("visibilitychange", keepAwake);
  if (document.fullscreenElement) document.exitFullscreen?.();
  wake?.release?.(); wake = null;
  onExit();
}
