// Motor de eventos do Mural: decide como cada acontecimento aparece, por quanto tempo e quem tem prioridade.
//   toast     aviso no canto, não interrompe (vários ao mesmo tempo, os mais recentes ficam)
//   takeover  ocupa a tela inteira por um tempo e devolve à cena anterior; maior prioridade passa na frente
import { esc } from "./ui.js";
import { noteIcon } from "./icons.js";

export const PRIORITY = { splash: 1, info: 2, warning: 3, critical: 4 };
const TOAST_SECONDS = { info: 5, warning: 8, critical: 12 };
const TAKEOVER_SECONDS = 20;
const MAX_TOASTS = 3;

/** Transforma um alerta (regra do painel ou webhook) em evento do Mural. */
export function fromAlert(a, { takeover }) {
  const full = a.display === "fullscreen" || (a.display !== "toast" && a.level === "critical" && takeover);
  return {
    id: `alert-${a.seq}`, kind: full ? "takeover" : "toast", level: a.level || "info", title: a.title, text: a.text,
    icon: a.icon, source: a.source, seconds: a.duration || (full ? TAKEOVER_SECONDS : TOAST_SECONDS[a.level] || 5),
  };
}

export class EventDeck {
  constructor(stage) {
    this.stage = stage;
    this.toasts = stage.querySelector(".mural-toasts");
    this.layer = stage.querySelector(".mural-takeover");
    this.queue = [];
    this.active = null;
    this.timer = null;
    this.layer.addEventListener("click", e => { if (e.target.closest("[data-dismiss]")) this.next(); });
  }

  push(ev) {
    if (ev.kind === "takeover") this.takeover(ev);
    else this.toast(ev);
  }

  toast(ev) {
    const n = document.createElement("div");
    n.className = `mural-toast ${ev.level}`;
    n.innerHTML = `<span class="mural-toast-ico">${ev.art ? `<img src="${esc(ev.art)}" alt="">` : noteIcon(ev.icon)}</span>
      <div><b>${esc(ev.title)}</b>${ev.text ? `<p>${esc(ev.text)}</p>` : ""}${ev.source ? `<small>${esc(ev.source)}</small>` : ""}</div>`;
    this.toasts.prepend(n);
    while (this.toasts.children.length > MAX_TOASTS) this.toasts.lastElementChild.remove();
    setTimeout(() => n.remove(), ev.seconds * 1000);
  }

  takeover(ev) {
    const rank = e => PRIORITY[e.level] ?? 1;
    if (this.active && rank(ev) <= rank(this.active)) {
      if (rank(ev) > 1) this.queue.push(ev);                // um splash só vale na hora: não entra na fila
      this.queue.sort((a, b) => rank(b) - rank(a));
      return;
    }
    if (this.active && rank(this.active) > 1) this.queue.unshift(this.active);   // volta depois do mais urgente
    this.show(ev);
  }

  show(ev) {
    this.active = ev;
    clearTimeout(this.timer);
    const l = this.layer;
    l.className = `mural-takeover ${ev.level}`;
    l.hidden = false;
    l.setAttribute("role", ev.level === "critical" ? "alert" : "status");
    l.innerHTML = `${ev.art ? `<img class="mural-take-art" src="${esc(ev.art)}" alt="">` : `<span class="mural-take-ico">${noteIcon(ev.icon)}</span>`}
      ${ev.eyebrow ? `<p class="mural-take-kicker">${esc(ev.eyebrow)}</p>` : ""}
      <h2>${esc(ev.title)}</h2>${ev.text ? `<p class="mural-take-text">${esc(ev.text)}</p>` : ""}
      ${ev.source ? `<p class="mural-take-src">${esc(ev.source)}</p>` : ""}
      <button class="btn" data-dismiss>Dispensar</button>
      <div class="mural-take-time" style="animation-duration:${ev.seconds}s"></div>`;
    this.timer = setTimeout(() => this.next(), ev.seconds * 1000);
  }

  next() {
    clearTimeout(this.timer);
    this.active = null;
    const ev = this.queue.shift();
    if (ev) this.show(ev);
    else this.layer.hidden = true;
  }

  clear() {
    clearTimeout(this.timer);
    this.queue = [];
    this.active = null;
    this.layer.hidden = true;
    this.toasts.replaceChildren();
  }
}
