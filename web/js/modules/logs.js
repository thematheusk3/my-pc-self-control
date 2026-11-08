// Aba Logs: busca no journal do sistema (filtros, boots anteriores, ao vivo) e investigação a partir de uma linha.
import { ic } from "../icons.js";
import { $, $$, esc, toast } from "../ui.js";
import { openInvestigate } from "./incidents.js";

let app, root, rows = [], live = false, liveTimer = null, loading = false, more = false;
const f = { grep: "", priority: "4", boot: "0", unit: "", user_unit: "", kernel: false, range: "" };
const PRIO = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"];
const PRIO_PT = ["Emergência", "Alerta", "Crítico", "Erro", "Aviso", "Nota", "Info", "Debug"];
const RANGES = [["", "Tudo do boot"], ["900", "Últimos 15 min"], ["3600", "Última hora"], ["86400", "Últimas 24 h"]];

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div class="card logs-filters">
      <div class="toolbar" style="margin:0">
        <input class="search" type="search" placeholder="Buscar (texto ou regex)…" aria-label="Buscar no log" data-f="grep">
        <select class="search" data-f="priority" aria-label="Prioridade">
          <option value="">Todas as prioridades</option><option value="3">Erros e piores</option>
          <option value="4" selected>Avisos e piores</option><option value="5">Notas e piores</option><option value="6">Info e piores</option></select>
        <select class="search" data-f="boot" aria-label="Boot"><option value="0">Boot atual</option></select>
        <select class="search" data-f="range" aria-label="Período">${RANGES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      </div>
      <div class="toolbar" style="margin:8px 0 0">
        <input class="search" data-f="unit" placeholder="Unidade do sistema (ex.: sshd.service)" aria-label="Unidade" list="logs-units">
        <datalist id="logs-units"></datalist>
        <label class="check"><input type="checkbox" data-f="kernel"> Só kernel</label>
        <label class="check"><input type="checkbox" data-live> Ao vivo</label>
        <span style="flex:1"></span>
        <button class="btn btn-sm" data-act="search">Buscar</button>
      </div>
      <div class="logs-active" data-active></div>
    </div>
    <div class="card logs-box"><ol class="logs" data-rows><li class="empty">Carregando…</li></ol>
      <div class="toolbar logs-more" style="margin:8px 0 0"><button class="btn btn-sm" data-act="older" hidden>Carregar mais antigas</button>
        <span class="muted" data-count></span></div></div>`;

  const apply = () => { readFilters(); search(); };
  $("[data-f=grep]", el).addEventListener("keydown", e => { if (e.key === "Enter") apply(); });
  $("[data-f=unit]", el).addEventListener("keydown", e => { if (e.key === "Enter") apply(); });
  for (const s of $$("select[data-f], input[type=checkbox][data-f]", el)) s.addEventListener("change", apply);
  $("[data-live]", el).addEventListener("change", e => setLive(e.target.checked));
  el.addEventListener("click", e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "search") apply();
    if (act === "older") older();
    if (act === "clear-unit") { f.unit = f.user_unit = ""; $("[data-f=unit]", el).value = ""; search(); }
    const li = e.target.closest("[data-i]");
    if (li) openEntry(rows[Number(li.dataset.i)]);
  });
  loadBoots();
}

function readFilters() {
  f.grep = $("[data-f=grep]", root).value.trim();
  f.priority = $("[data-f=priority]", root).value;
  f.boot = $("[data-f=boot]", root).value;
  f.range = $("[data-f=range]", root).value;
  f.kernel = $("[data-f=kernel]", root).checked;
  const u = $("[data-f=unit]", root).value.trim();
  if (u !== (f.unit || f.user_unit)) { f.unit = u; f.user_unit = ""; }
}

function params(extra = {}) {
  const p = new URLSearchParams();
  for (const k of ["grep", "priority", "unit", "user_unit"]) if (f[k]) p.set(k, f[k]);
  if (f.kernel) p.set("kernel", "1");
  if (f.boot !== "all") p.set("boot", f.boot || "0");
  if (f.range) p.set("since", String(Math.floor(Date.now() / 1000 - Number(f.range))));
  p.set("limit", "300");
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p.toString();
}

async function search() {
  if (loading) return;
  loading = true;
  $("[data-rows]", root).innerHTML = `<li class="empty">Buscando…</li>`;
  renderActive();
  try {
    const r = await app.api(`/api/logs?${params()}`);
    rows = r.items; more = r.more;
    render(true);
  } catch (e) { $("[data-rows]", root).innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
  loading = false;
}

async function older() {
  if (!rows.length || loading) return;
  loading = true;
  try {
    const r = await app.api(`/api/logs?${params({ before: rows[0].cursor })}`);
    rows = [...r.items, ...rows]; more = r.more;
    render(false);
  } catch (e) { toast(e.message, true); }
  loading = false;
}

async function newer() {
  if (loading || !rows.length || document.hidden) return;
  try {
    const p = new URLSearchParams(params({ after: rows.at(-1).cursor }));
    p.delete("since");
    const r = await app.api(`/api/logs?${p}`);
    if (r.items.length) {
      const box = $(".logs-box", root);
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      rows = [...rows, ...r.items].slice(-3000);
      render(atEnd);
    }
  } catch { /* tenta de novo no próximo ciclo */ }
}

function setLive(on) {
  live = on;
  clearInterval(liveTimer);
  if (on) liveTimer = setInterval(newer, 2000);
}

function render(scrollEnd) {
  const ol = $("[data-rows]", root);
  $("[data-act=older]", root).hidden = !more;
  $("[data-count]", root).textContent = rows.length ? `${rows.length} linhas` : "";
  if (!rows.length) { ol.innerHTML = `<li class="empty">Nenhuma linha com esses filtros.</li>`; return; }
  let lastDay = "";
  ol.innerHTML = rows.map((m, i) => {
    const d = new Date(m.ts * 1000), day = d.toLocaleDateString("pt-BR");
    const sep = day !== lastDay ? `<li class="logs-day">${day}</li>` : "";
    lastDay = day;
    return `${sep}<li data-i="${i}" class="lg p${m.prio}"><time>${d.toLocaleTimeString("pt-BR")}</time>
      <span class="lg-who" title="${esc(m.unit || "")}">${esc(m.ident)}</span><span class="lg-msg">${esc(m.msg.split("\n")[0])}</span></li>`;
  }).join("");
  if (scrollEnd) { const box = $(".logs-box", root); box.scrollTop = box.scrollHeight; }
}

function renderActive() {
  const u = f.unit || f.user_unit;
  $("[data-active]", root).innerHTML = u ? `<span class="chip">${f.user_unit ? "Serviço do usuário" : "Unidade"}: ${esc(u)}
    <button class="chip-x" data-act="clear-unit" aria-label="Remover filtro">×</button></span>` : "";
}

async function loadBoots() {
  try {
    const r = await app.api("/api/logs/boots");
    const sel = $("[data-f=boot]", root);
    sel.innerHTML = r.boots.map(b => `<option value="${b.index === 0 ? "0" : esc(b.id)}">${b.index === 0 ? "Boot atual" : `Boot ${b.index}`} · ${new Date(b.first * 1000).toLocaleString("pt-BR")}${b.index === 0 ? "" : ` → ${new Date(b.last * 1000).toLocaleTimeString("pt-BR")}`}</option>`).join("")
      + `<option value="all">Todos os boots</option>`;
    sel.value = f.boot === "all" || r.boots.some(b => b.id === f.boot) ? f.boot : "0";
  } catch { /* fica só o boot atual */ }
  try {
    const s = await app.api("/api/services?scope=system");
    $("#logs-units").innerHTML = s.units.map(u => `<option value="${esc(u.unit)}">`).join("");
  } catch { /* sem sugestões */ }
}

async function openEntry(m) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>${esc(m.ident)}</h2><div class="muted">${new Date(m.ts * 1000).toLocaleString("pt-BR")} · ${PRIO_PT[m.prio] || m.prio}${m.unit ? ` · ${esc(m.unit)}` : ""}</div>
    <pre class="inc-pre">${esc(m.msg)}</pre><p class="muted">Carregando campos…</p>`;
  if (!dlg.open) dlg.showModal();
  let full = null;
  try { full = await app.api(`/api/logs/entry?cursor=${encodeURIComponent(m.cursor)}`); } catch { /* mostra só o básico */ }
  const fields = full?.fields || {};
  const keys = Object.keys(fields).filter(k => !["MESSAGE", "__CURSOR"].includes(k)).sort();
  dlg.innerHTML = `<h2>${esc(m.ident)}</h2>
    <div class="muted">${new Date(m.ts * 1000).toLocaleString("pt-BR")} · <span class="lg-prio p${m.prio}">${PRIO_PT[m.prio] || m.prio}</span>${m.unit ? ` · ${esc(m.unit)}` : ""}</div>
    <pre class="inc-pre">${esc(m.msg)}</pre>
    ${keys.length ? `<details><summary class="stat-label">Todos os campos (${keys.length})</summary><dl class="kv logs-kv">${keys.map(k =>
      `<dt>${esc(k)}</dt><dd class="mono">${esc(String(fields[k]).slice(0, 600))}</dd>`).join("")}</dl></details>` : ""}
    <div class="sheet-actions">
      ${app.info?.features?.incidents ? `<button class="btn btn-primary full" data-inv>Investigar este momento</button>` : ""}
      ${m.unit ? `<button class="btn" data-unit>Só esta unidade</button>` : ""}
      <button class="btn" data-ctx>Ver ao redor</button>
      <button class="btn" data-copy>Copiar linha</button>
      <button class="btn full" data-close>Fechar</button></div>`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-inv]")?.addEventListener("click", () => { dlg.close(); openInvestigate(m.ts, `${m.ident}: ${m.msg.split("\n")[0].slice(0, 200)}`); });
  dlg.querySelector("[data-unit]")?.addEventListener("click", () => {
    dlg.close();
    const user = fields._SYSTEMD_USER_UNIT && m.unit === fields._SYSTEMD_USER_UNIT;
    f.unit = user ? "" : m.unit; f.user_unit = user ? m.unit : "";
    $("[data-f=unit]", root).value = m.unit;
    search();
  });
  dlg.querySelector("[data-ctx]").onclick = () => {
    // tudo (todas as prioridades) 2 min antes e depois da linha
    dlg.close();
    f.grep = ""; f.unit = ""; f.user_unit = ""; f.kernel = false; f.priority = ""; f.range = "";
    $("[data-f=grep]", root).value = ""; $("[data-f=unit]", root).value = ""; $("[data-f=kernel]", root).checked = false;
    $("[data-f=priority]", root).value = ""; $("[data-f=range]", root).value = "";
    around(m.ts);
  };
  dlg.querySelector("[data-copy]").onclick = async () => {
    const text = `${new Date(m.ts * 1000).toLocaleString("pt-BR")} ${m.ident}${m.unit ? ` (${m.unit})` : ""}: ${m.msg}`;
    try { await navigator.clipboard.writeText(text); toast("Linha copiada."); } catch { toast("O navegador não deixou copiar (precisa de HTTPS).", true); }
  };
}

async function around(ts) {
  loading = true;
  $("[data-rows]", root).innerHTML = `<li class="empty">Buscando…</li>`;
  try {
    const p = new URLSearchParams({ since: String(Math.floor(ts - 120)), until: String(Math.ceil(ts + 120)), limit: "2000", boot: "all" });
    const r = await app.api(`/api/logs?${p}`);
    rows = r.items; more = false;
    render(false);
    const i = rows.findIndex(x => x.ts >= ts);
    root.querySelector(`[data-i="${Math.max(0, i)}"]`)?.scrollIntoView({ block: "center" });
    root.querySelector(`[data-i="${Math.max(0, i)}"]`)?.classList.add("lg-mark");
  } catch (e) { toast(e.message, true); }
  loading = false;
}

// #logs:unit=sshd.service  ou  #logs:user_unit=pipewire.service
function route(sub) {
  const m = /^(unit|user_unit)=(.+)$/.exec(sub || "");
  if (m) {
    f.unit = m[1] === "unit" ? m[2] : ""; f.user_unit = m[1] === "user_unit" ? m[2] : "";
    f.priority = ""; $("[data-f=priority]", root).value = "";
    $("[data-f=unit]", root).value = m[2];
  }
  search();
}

export default {
  id: "logs", title: "Logs", icon: ic("list"),
  enabled: a => !!a.info?.features?.logs,
  mount, route,
  show() { if (live) setLive(true); },
  hide() { clearInterval(liveTimer); },
};
