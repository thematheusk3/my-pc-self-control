// Núcleo: conexão em tempo real (SSE), histórico local, roteamento de abas.
import { $, h, toast } from "./ui.js";
import { MODULES } from "./modules/index.js";

const HISTORY_WINDOW = 300; // segundos

export const app = {
  info: null,
  snapshot: null,
  history: {},          // chave -> [[t, v], ...]
  listeners: new Set(),
  current: null,

  async api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401 && path !== "/api/auth/password") { location.href = "/login"; throw new Error("não autenticado"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  },

  onSnapshot(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },

  pushSnapshot(snap) {
    this.snapshot = snap;
    const t = snap.ts, cut = t - HISTORY_WINDOW;
    for (const [k, v] of Object.entries(snap.series || {})) {
      const arr = (this.history[k] ||= []);
      if (!arr.length || arr.at(-1)[0] < t) arr.push([t, v]);
      while (arr.length && arr[0][0] < cut) arr.shift();
    }
    updateBell(snap.data.alerts);
    for (const fn of this.listeners) {
      try { fn(snap); } catch (e) { console.error(e); }
    }
  },
};

// sino de alertas: contador + notificação do navegador (se permitida) para alertas novos
let lastAlertSeq = null;
function updateBell(al) {
  const bell = $("#bell");
  if (!al) return;
  bell.hidden = false;
  const badge = bell.querySelector(".badge");
  badge.hidden = !al.unread;
  badge.textContent = al.unread > 99 ? "99+" : al.unread;
  const seq = al.latest?.seq ?? 0;
  if (lastAlertSeq !== null && seq > lastAlertSeq && al.latest && !al.latest.read &&
      "Notification" in window && Notification.permission === "granted") {
    try { new Notification(al.latest.title, { body: al.latest.text, tag: `alert-${seq}` }); } catch { /* */ }
  }
  lastAlertSeq = seq;
}

function setLive(state) {
  const el = $("#live");
  el.className = `live ${state}`;
  el.querySelector(".live-label").textContent = { on: "Ao vivo", off: "Reconectando", "": "Conectando" }[state];
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => setLive("on");
  es.onmessage = e => app.pushSnapshot(JSON.parse(e.data));
  es.onerror = () => {
    setLive("off");
    // EventSource reconecta sozinho; checa se a sessão expirou
    app.api("/api/info").catch(() => {});
  };
}

function setupTheme() {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-btn").addEventListener("click", () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const cur = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    if (app.snapshot) app.pushSnapshot(app.snapshot); // redesenha canvases
  });
}

function setupRouter() {
  const tabs = $("#tabs"), views = $("#views");
  const mods = MODULES.filter(m => !m.enabled || m.enabled(app));
  for (const m of mods) {
    const btn = h(`<button class="tab" data-id="${m.id}"><span class="ico" aria-hidden="true">${m.icon}</span><span>${m.title}</span></button>`);
    btn.addEventListener("click", () => { location.hash = m.id; });
    tabs.append(btn);
    const view = h(`<section class="view" id="view-${m.id}" aria-label="${m.title}"></section>`);
    views.append(view);
    m.mount(view, app);
  }
  const go = () => {
    const [id, sub] = (location.hash.slice(1) || mods[0].id).split(":");   // ex.: #processes:susp
    const mod = mods.find(m => m.id === id) || mods[0];
    for (const b of tabs.children) {
      if (b.dataset.id === mod.id) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    }
    for (const v of views.children) v.classList.toggle("active", v.id === `view-${mod.id}`);
    if (app.current !== mod) { app.current?.hide?.(); app.current = mod; mod.route?.(sub); mod.show?.(); }
    else mod.route?.(sub, true);
    window.scrollTo(0, 0);
  };
  window.addEventListener("hashchange", go);
  go();
}

async function main() {
  setupTheme();
  try {
    app.info = await app.api("/api/info");
    const [hist, snap] = await Promise.all([app.api("/api/history"), app.api("/api/snapshot")]);
    app.history = hist;
    const sys = app.info.info.system || {};
    $("#host").textContent = sys.hostname || "—";
    $("#host-sub").textContent = [sys.cpu_model, sys.os].filter(Boolean).join(" · ");
    document.title = `${sys.hostname} · Self Monitoring`;
    setupRouter();
    app.pushSnapshot(snap);
    connect();
  } catch (e) {
    toast(`Erro ao conectar: ${e.message}`, true);
  }
}

main();
