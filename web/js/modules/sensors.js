import { $, esc, statusChip } from "../ui.js";

let app, root;
const KIND = { temp: "Temperaturas", fan: "Ventoinhas", in: "Tensões", power: "Potência", curr: "Corrente", freq: "Frequência" };

function fmt(r) {
  const d = r.kind === "in" ? 3 : r.kind === "fan" ? 0 : 1;
  return `${r.value.toFixed(d)} ${r.unit}`;
}

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid" data-grid></div>`;
  app.onSnapshot(update);
}

function update(snap) {
  if (!root.classList.contains("active")) return;
  const chips = snap.data.sensors?.chips || [];
  const grid = $("[data-grid]", root);
  if (!chips.length) { grid.innerHTML = `<div class="card empty span-all">Nenhum sensor hwmon encontrado. Instale <code>lm_sensors</code> e rode <code>sensors-detect</code>.</div>`; return; }
  const hint = snap.data.sensors.fan_hint ? `<div class="card span-all"><p class="note" style="margin:0">⚠ ${snap.data.sensors.fan_hint}</p></div>` : "";
  grid.innerHTML = hint + chips.map(c => {
    const groups = {};
    for (const r of c.readings) (groups[r.kind] ||= []).push(r);
    const body = Object.entries(groups).map(([k, rs]) => `
      <div class="stat-label" style="margin-top:10px">${KIND[k] || k}</div>
      <dl class="kv">${rs.map(r => `<dt>${esc(r.label)}</dt><dd><span class="num">${fmt(r)}</span>${r.kind === "temp" ? statusChip(r.value, r.crit) : ""}</dd>`).join("")}</dl>`).join("");
    return `<article class="card"><div class="card-head"><div><div class="card-title">${esc(c.name)}</div>
      <div class="card-sub">${esc(c.driver)}${c.device ? ` · ${esc(c.device)}` : ""}</div></div></div>${body}</article>`;
  }).join("");
}

export default { id: "sensors", title: "Sensores", icon: "🌡", mount, show: () => app.snapshot && update(app.snapshot) };
