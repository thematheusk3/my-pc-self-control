// Mídia (MPRIS): utilidades compartilhadas pela Visão geral ("No PC agora") e por Controles > Áudio.
import { ic } from "./icons.js";
import { esc } from "./ui.js";

export const STATUS = { playing: ["good", "Tocando"], paused: ["", "Pausado"], stopped: ["", "Parado"] };

export const mmss = s => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(s % 60).padStart(2, "0")}`; };

export function artSrc(p) {
  if (!p.art) return null;
  if (/^https?:\/\//.test(p.art)) return p.art;
  if (p.art.startsWith("file://")) return `/api/media/art?player=${encodeURIComponent(p.id)}&v=${encodeURIComponent(p.art.slice(-40))}`;
  return null;
}

/** Posição estimada agora: a amostra é de `p.at`; se está tocando, soma o tempo desde então. */
export function livePos(p, ts) {
  return Math.min(p.length, (p.position || 0) + (p.status === "playing" ? Math.max(0, ts - p.at) * (p.rate || 1) : 0));
}

/** Botões anterior, tocar/pausar, próxima (e ±10 s com seek=true). Os cliques vão para controlMedia(). */
export function mediaButtons(p, { seek = false } = {}) {
  const pid = esc(p.id), playing = p.status === "playing";
  const b = (op, ico, title, extra = "") => `<button class="btn btn-sm" data-op="${op}" data-player="${pid}" ${extra} title="${title}" aria-label="${title}">${ico}</button>`;
  return [
    p.can.previous && b("previous", ic("skip-back"), "Anterior"),
    seek && p.can.seek && b("seek", "−10s", "Voltar 10 s", `data-offset="-10"`),
    p.can.play_pause && b("play_pause", ic(playing ? "pause" : "play"), playing ? "Pausar" : "Tocar"),
    seek && p.can.seek && b("seek", "+10s", "Avançar 10 s", `data-offset="10"`),
    p.can.next && b("next", ic("skip-forward"), "Próxima"),
  ].filter(Boolean).join("");
}

/** Trata o clique num botão gerado por mediaButtons(). Devolve o estado novo da mídia (ou lança). */
export function controlMedia(app, btn) {
  const d = btn.dataset;
  return app.api("/api/media", { method: "POST", body: { player: d.player, op: d.op, offset: d.offset != null ? Number(d.offset) : undefined } });
}
