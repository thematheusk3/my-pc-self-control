// Aba Serviços: unidades do systemd (sistema e do usuário), estado e consumo ao vivo, logs e ações.
import { ic } from "../icons.js";
import { $, $$, bytes, esc, toast } from "../ui.js";
import { openInvestigate } from "./incidents.js";

let app, root, data = null, scope = "system", filter = "running", sort = "mem", q = "", timer = null, busy = false;
const SORTS = { mem: ["Memória", u => -(u.mem || 0)], cpu: ["CPU", u => -(u.cpu || 0)], name: ["Nome", u => u.unit],
  restarts: ["Reinícios", u => -(u.restarts || 0)], since: ["Mais recentes", u => -(u.since || 0)] };
const STATE = { running: ["good", "Rodando"], exited: ["", "Concluído"], dead: ["", "Parado"], failed: ["critical", "Falhou"],
  waiting: ["", "Esperando"], "auto-restart": ["warning", "Reiniciando"], activating: ["warning", "Iniciando"], reloading: ["warning", "Recarregando"] };

function ago(t) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`;
  return `${Math.floor(s / 86400)} d`;
}
const stateChip = u => {
  const [c, l] = u.active === "failed" ? STATE.failed : STATE[u.sub] || ["", u.sub || u.active];
  return `<span class="chip ${c}">${esc(l)}</span>`;
};

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div class="toolbar">
      <input class="search" type="search" placeholder="Buscar serviço…" aria-label="Buscar serviço" data-q>
      <div class="seg" role="group" aria-label="Escopo" data-scope>
        <button data-v="system" aria-pressed="true">Sistema</button><button data-v="user" aria-pressed="false">Usuário</button></div>
      <div class="seg" role="group" aria-label="Filtro" data-filter>
        <button data-v="running" aria-pressed="true">Rodando</button><button data-v="failed" aria-pressed="false">Com falha</button>
        <button data-v="all" aria-pressed="false">Todos</button></div>
      <select class="search" data-sort aria-label="Ordenar">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select>
    </div>
    <div class="svc-sum" data-sum></div>
    <ul class="plist card svc-list" data-list><li class="empty">Carregando…</li></ul>
    <p class="note">CPU é a média desde a última atualização (a cada 3 s) e memória é a do cgroup do serviço (inclui os processos filhos).
      Serviços do usuário são os da sua sessão (Plasma, PipeWire, apps abertos pelo menu).</p>`;
  $("[data-q]", el).addEventListener("input", e => { q = e.target.value.toLowerCase(); render(); });
  const seg = (sel, fn) => $(sel, el).addEventListener("click", e => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    $$(`${sel} button`, el).forEach(x => x.setAttribute("aria-pressed", x === b));
    fn(b.dataset.v);
  });
  seg("[data-scope]", v => { scope = v; data = null; render(); load(); });
  seg("[data-filter]", v => { filter = v; render(); });
  $("[data-sort]", el).addEventListener("change", e => { sort = e.target.value; render(); });
  $("[data-list]", el).addEventListener("click", e => {
    const li = e.target.closest("[data-unit]");
    if (li) openUnit(li.dataset.unit);
  });
}

async function load() {
  if (busy) return;
  busy = true;
  try { data = await app.api(`/api/services?scope=${scope}`); render(); }
  catch (e) { $("[data-list]", root).innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
  busy = false;
}

function render() {
  const list = $("[data-list]", root);
  if (!data) { list.innerHTML = `<li class="empty">Carregando…</li>`; $("[data-sum]", root).innerHTML = ""; return; }
  const c = data.counts;
  const mem = data.units.reduce((s, u) => s + (u.active === "active" && u.mem ? u.mem : 0), 0);
  $("[data-sum]", root).innerHTML = `<span class="chip good">${c.running} rodando</span>
    ${c.failed ? `<span class="chip critical">${c.failed} com falha</span>` : `<span class="chip">nenhum com falha</span>`}
    <span class="chip">${c.total} carregados</span><span class="chip">${bytes(mem)} em serviços</span>`;
  let us = data.units.filter(u => filter === "all" || (filter === "running" ? u.sub === "running" : u.active === "failed"));
  if (q) us = us.filter(u => u.unit.toLowerCase().includes(q) || (u.description || "").toLowerCase().includes(q));
  const key = SORTS[sort][1];
  us.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
  if (!us.length) { list.innerHTML = `<li class="empty">${filter === "failed" ? "Nenhum serviço com falha." : "Nada encontrado."}</li>`; return; }
  list.innerHTML = us.slice(0, 400).map(u => `
    <li data-unit="${esc(u.unit)}" class="${u.active === "failed" ? "svc-failed" : ""}">
      <div style="min-width:0"><div class="pname">${esc(u.unit.replace(/\.service$/, ""))} ${stateChip(u)}</div>
        <div class="meta">${esc(u.description || "")}</div></div>
      <div class="vals"><b>${u.mem != null ? bytes(u.mem) : "—"}</b>
        <div class="meta">${u.cpu != null ? `CPU ${u.cpu.toFixed(1)}%` : ""}${u.tasks != null ? ` · ${u.tasks} tarefas` : ""}
          ${u.restarts ? ` · <span class="svc-warn">${u.restarts} reinícios</span>` : ""}</div>
        <div class="meta">${u.since ? `${u.sub === "running" ? "há" : "desde há"} ${ago(u.since)}` : ""}</div></div>
    </li>`).join("");
}

// ---------- detalhe ----------
async function openUnit(unit) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>${esc(unit)}</h2><p class="muted">Carregando…</p>`;
  if (!dlg.open) dlg.showModal();
  let d;
  try { d = await app.api(`/api/services/${encodeURIComponent(unit)}?scope=${scope}`); }
  catch (e) { dlg.innerHTML = `<h2>${esc(unit)}</h2><p>${esc(e.message)}</p><div class="sheet-actions"><button class="btn full" data-close>Fechar</button></div>`; dlg.querySelector("[data-close]").onclick = () => dlg.close(); return; }
  const p = d.props || {};
  const u = data?.units.find(x => x.unit === unit) || {};
  const can = app.info?.features?.services_control;
  const ts = v => { const m = /^@(\d+)/.exec(v || ""); return m ? new Date(m[1] * 1000).toLocaleString("pt-BR") : "—"; };
  dlg.innerHTML = `
    <h2>${esc(unit)}</h2>
    <div class="muted">${esc(p.Description || "")}</div>
    <dl class="kv" style="margin-top:10px">
      <dt>Estado</dt><dd>${stateChip(u.unit ? u : { active: p.ActiveState, sub: p.SubState })} ${esc(p.ActiveState)}/${esc(p.SubState)}</dd>
      <dt>Na inicialização</dt><dd>${esc(p.UnitFileState || "—")}</dd>
      <dt>Desde</dt><dd>${ts(p.ActiveState === "active" ? p.ActiveEnterTimestamp : p.InactiveEnterTimestamp)}</dd>
      ${u.mem != null ? `<dt>Memória</dt><dd>${bytes(u.mem)}${p.MemoryPeak && Number(p.MemoryPeak) < 2 ** 62 ? ` · pico ${bytes(Number(p.MemoryPeak))}` : ""}</dd>` : ""}
      ${u.cpu != null ? `<dt>CPU</dt><dd>${u.cpu.toFixed(1)}% · total ${Math.round(u.cpu_total || 0)} s</dd>` : ""}
      ${p.MainPID && p.MainPID !== "0" ? `<dt>PID principal</dt><dd>${esc(p.MainPID)}</dd>` : ""}
      <dt>Reinícios</dt><dd>${esc(p.NRestarts || "0")} · política: ${esc(p.Restart || "—")}</dd>
      <dt>Último resultado</dt><dd>${esc(p.Result || "—")}${p.ExecMainStatus && p.ExecMainStatus !== "0" ? ` · código ${esc(p.ExecMainStatus)}` : ""}</dd>
      ${p.User ? `<dt>Usuário</dt><dd>${esc(p.User)}</dd>` : ""}
      <dt>Arquivo</dt><dd class="mono">${esc(p.FragmentPath || "—")}</dd>
    </dl>
    ${p.ExecStart ? `<div class="cmdline">${esc((/argv\[\]=([^;]*)/.exec(p.ExecStart) || [, p.ExecStart])[1].trim())}</div>` : ""}
    <details ${p.ActiveState === "failed" ? "open" : ""}><summary class="stat-label">Últimas linhas do log</summary>
      <pre class="inc-pre">${esc((d.logs || []).map(m => `${new Date(m.ts * 1000).toLocaleTimeString("pt-BR")} ${m.msg}`).join("\n") || "Sem linhas.")}</pre></details>
    <div class="confirm-text" hidden></div>
    <div class="sheet-actions">
      ${can && !d.self ? `${p.ActiveState === "active" ? `<button class="btn" data-a="restart">Reiniciar</button><button class="btn btn-ghost-danger" data-a="stop">Parar</button>`
        : `<button class="btn btn-primary" data-a="start">Iniciar</button><button class="btn" data-a="restart">Reiniciar</button>`}` : ""}
      ${can && d.self ? `<button class="btn full" data-a="restart">Reiniciar o painel</button>` : ""}
      ${app.info?.features?.logs ? `<button class="btn" data-logs>Ver nos Logs</button>` : ""}
      ${app.info?.features?.incidents ? `<button class="btn" data-inv>Investigar</button>` : ""}
      <button class="btn full" data-close>Fechar</button>
    </div>
    ${scope === "system" && can ? `<p class="note">Serviços do sistema pedem permissão do polkit; sem uma regra para o usuário do painel o sistema recusa.</p>` : ""}`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-logs]")?.addEventListener("click", () => { dlg.close(); location.hash = `logs:${scope === "user" ? "user_unit" : "unit"}=${unit}`; });
  dlg.querySelector("[data-inv]")?.addEventListener("click", () => { dlg.close(); openInvestigate(null, `Serviço ${unit}`); });
  let armed = null;
  dlg.querySelectorAll("[data-a]").forEach(b => b.addEventListener("click", async () => {
    const act = b.dataset.a;
    if ((act === "stop" || d.self) && armed !== act) {
      armed = act; b.textContent = "Toque de novo para confirmar";
      const c = dlg.querySelector(".confirm-text"); c.hidden = false;
      c.textContent = d.self ? "O painel vai sair do ar por alguns segundos." : `Parar ${unit}? O que depende dele pode parar de funcionar.`;
      return;
    }
    b.disabled = true;
    try {
      await app.api(`/api/services/${encodeURIComponent(unit)}/${act}`, { method: "POST", body: { scope } });
      toast({ start: "Iniciado", stop: "Parado", restart: "Reiniciado", reload: "Recarregado" }[act] + `: ${unit}`);
      setTimeout(() => { load(); if (dlg.open) openUnit(unit); }, 800);
    } catch (e) { toast(e.message, true); b.disabled = false; }
  }));
}

export default {
  id: "services", title: "Serviços", icon: ic("package"),
  enabled: a => !!a.info?.features?.services,
  mount,
  show() { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 3000); },
  hide() { clearInterval(timer); },
};
