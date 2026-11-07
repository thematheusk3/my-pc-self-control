import { ic } from "../icons.js";
import { $, bytes, esc, pct } from "../ui.js";
import { openProcessSheet } from "../procsheet.js";
import { exitedView, suspiciousView } from "../procviews.js";

let app, root, timer = null, procs = [], limit = 150;
let view = "live", views = {};   // live | exited | susp
const state = { q: "", sort: "cpu", dir: -1, kernel: false, user: "" };

const COLS = [
  { k: "name", label: "Processo" },
  { k: "pid", label: "PID", r: true },
  { k: "user", label: "Usuário" },
  { k: "cpu", label: "CPU", r: true },
  { k: "mem", label: "Memória", r: true },
  { k: "gpu", label: "GPU", r: true },
  { k: "cmd", label: "Comando" },
];

function mount(el, a) {
  app = a; root = el;
  const hist = !!app.info?.features?.exited;
  el.innerHTML = `
    ${hist ? `<div class="seg proc-views" role="tablist" aria-label="Visão" data-views>
      <button data-v="live" aria-pressed="true">Em execução</button>
      <button data-v="exited" aria-pressed="false">Encerrados</button>
      <button data-v="susp" aria-pressed="false">Suspeitos</button>
    </div>` : ""}
    <div data-view="live">
    <div class="toolbar">
      <input class="search" type="search" placeholder="Buscar nome, PID ou comando…" aria-label="Buscar processos" data-q>
      <select class="search" data-user aria-label="Filtrar por usuário"><option value="">Todos os usuários</option></select>
      <select class="search mobile-only" data-sort aria-label="Ordenar por">
        <option value="cpu">Ordenar: CPU</option><option value="mem">Ordenar: Memória</option>
        <option value="gpu">Ordenar: GPU</option><option value="name">Ordenar: Nome</option><option value="pid">Ordenar: PID</option>
      </select>
      <label class="check"><input type="checkbox" data-kernel> Threads do kernel</label>
    </div>
    <div class="proc-summary" data-summary></div>
    <div class="card table-wrap desktop-only"><table class="ptable"><thead><tr>
      ${COLS.map(c => `<th data-k="${c.k}" class="${c.r ? "r" : ""}${c.k === "cmd" ? " cmdh" : ""}">${c.label}</th>`).join("")}
    </tr></thead><tbody data-tbody></tbody></table></div>
    <div class="card table-wrap mobile-only"><ul class="plist" data-list></ul></div>
    <div style="text-align:center;margin-top:12px"><button class="btn btn-sm" data-more hidden>Mostrar mais</button></div>
    </div>
    <div data-view="exited" hidden></div>
    <div data-view="susp" hidden></div>`;
  if (hist) {
    views.exited = exitedView(app, $("[data-view=exited]", el));
    views.susp = suspiciousView(app, $("[data-view=susp]", el));
    $("[data-views]", el).addEventListener("click", e => {
      const b = e.target.closest("button[data-v]");
      if (b) location.hash = b.dataset.v === "live" ? "processes" : `processes:${b.dataset.v}`;
    });
  }

  $("[data-view=live] [data-q]", el).addEventListener("input", e => { state.q = e.target.value.toLowerCase(); render(); });
  $("[data-view=live] [data-user]", el).addEventListener("change", e => { state.user = e.target.value; render(); });
  $("[data-view=live] [data-kernel]", el).addEventListener("change", e => { state.kernel = e.target.checked; render(); });
  $("[data-view=live] [data-sort]", el).addEventListener("change", e => { setSort(e.target.value, true); });
  el.querySelectorAll("[data-view=live] th[data-k]").forEach(th => th.addEventListener("click", () => {
    if (th.dataset.k === "cmd") return;
    setSort(th.dataset.k, state.sort !== th.dataset.k);
  }));
  $("[data-view=live] [data-more]", el).addEventListener("click", () => { limit += 150; render(); });
  const open = e => {
    const row = e.target.closest("[data-pid]");
    if (!row) return;
    const p = procs.find(p => p.pid === Number(row.dataset.pid));
    if (p) openProcessSheet(app, p, refresh);
  };
  $("[data-view=live] [data-tbody]", el).addEventListener("click", open);
  $("[data-view=live] [data-list]", el).addEventListener("click", open);
  document.addEventListener("visibilitychange", () => (document.hidden ? hideAll() : root.classList.contains("active") && showCur()));
}

// sub-visão pela URL: #processes, #processes:exited, #processes:susp
function route(sub, active) {
  const v = views[sub] ? sub : "live";
  if (v === view) return;
  if (active) setView(v); else applyView(v);
}

function applyView(v) {
  view = v;
  root.querySelectorAll("[data-views] button").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === v));
  root.querySelectorAll("[data-view]").forEach(d => { d.hidden = d.dataset.view !== v; });
}

function setView(v) {
  hideAll();
  applyView(v);
  showCur();
}
function showCur() { view === "live" ? start() : views[view].start(); }
function hideAll() { stop(); Object.values(views).forEach(x => x.stop()); }

function setSort(k, fresh) {
  if (fresh) { state.sort = k; state.dir = ["name", "user", "pid"].includes(k) ? 1 : -1; }
  else state.dir *= -1;
  $("[data-view=live] [data-sort]", root).value = k;
  render();
}

async function refresh() {
  try {
    const r = await app.api("/api/processes");
    procs = r.processes;
    render();
  } catch (e) { /* conexão caiu; tenta no próximo ciclo */ }
}

function start() { stop(); refresh(); timer = setInterval(refresh, (app.info.process_interval || 2) * 1000); }
function stop() { clearInterval(timer); timer = null; }

function render() {
  const users = [...new Set(procs.map(p => p.user))].sort();
  const sel = $("[data-view=live] [data-user]", root);
  if (sel.options.length - 1 !== users.length) {
    sel.innerHTML = `<option value="">Todos os usuários</option>` + users.map(u => `<option ${u === state.user ? "selected" : ""}>${esc(u)}</option>`).join("");
  }
  const q = state.q;
  let list = procs.filter(p =>
    (state.kernel || !p.kernel) && (!state.user || p.user === state.user) &&
    (!q || p.name.toLowerCase().includes(q) || String(p.pid).startsWith(q) || p.cmd.toLowerCase().includes(q)));
  const k = state.sort, dir = state.dir;
  list.sort((a, b) => {
    const x = a[k] ?? -1, y = b[k] ?? -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir || a.pid - b.pid;
  });
  const total = list.length;
  const cpuSum = list.reduce((s, p) => s + p.cpu, 0), memSum = list.reduce((s, p) => s + p.mem, 0);
  $("[data-view=live] [data-summary]", root).textContent = `${total} processos · CPU ${pct(cpuSum, 0)} · Memória ${bytes(memSum)}`;
  list = list.slice(0, limit);
  $("[data-view=live] [data-more]", root).hidden = total <= limit;

  root.querySelectorAll("[data-view=live] th[data-k]").forEach(th => {
    if (th.dataset.k === k) th.setAttribute("aria-sort", dir < 0 ? "descending" : "ascending");
    else th.removeAttribute("aria-sort");
  });

  const maxCpu = Math.max(1, ...list.map(p => p.cpu));
  const bar = (v, max, txt) => `<span class="bar-cell"><span class="mini"><i style="width:${Math.min(100, (v / max) * 100)}%"></i></span>${txt}</span>`;
  $("[data-view=live] [data-tbody]", root).innerHTML = list.map(p => `<tr data-pid="${p.pid}">
      <td class="pname">${esc(p.name)}</td><td class="r">${p.pid}</td><td>${esc(p.user)}</td>
      <td class="r">${bar(p.cpu, maxCpu, pct(p.cpu, 1))}</td>
      <td class="r">${bytes(p.mem)}</td>
      <td class="r">${p.gpu ? pct(p.gpu, 1) : p.gpu_mem ? `<span class="muted">${bytes(p.gpu_mem)}</span>` : `<span class="muted">—</span>`}</td>
      <td class="cmd" title="${esc(p.cmd)}">${esc(p.cmd)}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">Nenhum processo encontrado.</td></tr>`;

  $("[data-view=live] [data-list]", root).innerHTML = list.map(p => `<li data-pid="${p.pid}">
      <span class="pname">${esc(p.name)}</span>
      <span class="vals"><b>${pct(p.cpu, 1)}</b></span>
      <span class="meta">${p.pid} · ${esc(p.user)}${p.gpu ? ` · GPU ${pct(p.gpu, 0)}` : ""}</span>
      <span class="vals">${bytes(p.mem)}</span></li>`).join("") || `<li class="empty">Nenhum processo encontrado.</li>`;
}

export default { id: "processes", title: "Processos", icon: ic("rows"), mount, route, show: showCur, hide: hideAll };
