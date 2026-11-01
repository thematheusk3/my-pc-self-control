// Utilidades de formatação e componentes pequenos reutilizáveis.

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function bytes(n, digits = 1) {
  if (n == null || isNaN(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : n >= 100 ? 0 : digits)} ${u[i]}`;
}
export const rate = n => (n == null ? "—" : `${bytes(n)}/s`);
export const pct = (n, d = 0) => (n == null ? "—" : `${Number(n).toFixed(d)}%`);
export const temp = n => (n == null ? "—" : `${Math.round(n)} °C`);
export const mhz = n => (n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(2)} GHz` : `${Math.round(n)} MHz`);

export function duration(s) {
  s = Math.floor(s);
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${hh}h ${m}min`;
  if (hh) return `${hh}h ${m}min`;
  return `${m}min ${s % 60}s`;
}

// Status de temperatura: sempre cor + texto (nunca só cor).
export function tempStatus(t, crit) {
  if (t == null) return null;
  const c = crit && crit > 40 ? crit : 100;
  const r = t / c;
  if (r >= 0.95) return { cls: "critical", label: "Crítico" };
  if (r >= 0.85) return { cls: "serious", label: "Quente" };
  if (r >= 0.72) return { cls: "warning", label: "Morno" };
  return { cls: "good", label: "Normal" };
}
export function tempChip(t, crit) {
  const s = tempStatus(t, crit);
  return s ? `<span class="chip ${s.cls}">${temp(t)} · ${s.label}</span>` : "";
}

export function statusChip(t, crit) {
  const s = tempStatus(t, crit);
  return s ? `<span class="chip ${s.cls}">${s.label}</span>` : "";
}

export function meter(label, value, max, text) {
  const w = max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return `<div class="meter-row"><div class="lbl"><span>${esc(label)}</span><span>${text}</span></div>
    <div class="meter" role="meter" aria-valuenow="${w.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${w}%"></i></div></div>`;
}

let toastTimer;
export function toast(msg, err = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", err);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3500);
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
