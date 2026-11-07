// Sub-visões da aba Processos: histórico de encerrados e lista de suspeitos.
import { $, duration, esc, toast } from "./ui.js";
import { flagList, openInspect, verdict } from "./inspect.js";

const ago = t => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return `há ${Math.round(s)}s`;
  if (s < 3600) return `há ${Math.round(s / 60)}min`;
  if (s < 86400) return `há ${Math.round(s / 3600)}h`;
  return new Date(t * 1000).toLocaleDateString("pt-BR");
};
const dur = s => (s == null ? "—" : s < 1 ? `${Math.round(s * 1000)}ms` : s < 60 ? `${s.toFixed(1)}s` : duration(s));

// ---------- encerrados ----------
export function exitedView(app, el) {
  const st = { q: "", user: "", group: false, data: null, limit: 200 };
  let timer = null;
  el.innerHTML = `
    <div class="toolbar">
      <input class="search" type="search" placeholder="Buscar nome, PID, comando ou pai…" aria-label="Buscar encerrados" data-q>
      <select class="search" data-user aria-label="Filtrar por usuário"><option value="">Todos os usuários</option></select>
      <label class="check"><input type="checkbox" data-group> Agrupar iguais</label>
      <button class="btn btn-sm" data-clear>Limpar histórico</button>
    </div>
    <div class="proc-summary" data-summary></div>
    <div class="card table-wrap desktop-only"><table class="ptable"><thead data-head></thead><tbody data-tbody></tbody></table></div>
    <div class="card table-wrap mobile-only"><ul class="plist" data-list></ul></div>
    <div style="text-align:center;margin-top:12px"><button class="btn btn-sm" data-more hidden>Mostrar mais</button></div>
    <p class="note" data-note></p>`;

  $("[data-q]", el).addEventListener("input", e => { st.q = e.target.value.toLowerCase(); render(); });
  $("[data-user]", el).addEventListener("change", e => { st.user = e.target.value; render(); });
  $("[data-group]", el).addEventListener("change", e => { st.group = e.target.checked; render(); });
  $("[data-more]", el).addEventListener("click", () => { st.limit += 200; render(); });
  let armed = false;
  $("[data-clear]", el).addEventListener("click", async e => {
    if (!armed) { armed = true; e.target.textContent = "Toque de novo para limpar"; setTimeout(() => { armed = false; e.target.textContent = "Limpar histórico"; }, 4000); return; }
    armed = false; e.target.textContent = "Limpar histórico";
    try { await app.api("/api/processes/exited/clear", { method: "POST", body: {} }); toast("Histórico limpo"); refresh(); }
    catch (err) { toast(err.message, true); }
  });
  const open = e => {
    const row = e.target.closest("[data-id]");
    if (row) openInspect(app, { id: row.dataset.id });
  };
  $("[data-tbody]", el).addEventListener("click", open);
  $("[data-list]", el).addEventListener("click", open);

  async function refresh() {
    try { st.data = await app.api("/api/processes/exited"); render(); } catch { /* tenta de novo */ }
  }

  function render() {
    const d = st.data;
    if (!d) return;
    const users = [...new Set(d.exited.map(r => r.user))].sort();
    const sel = $("[data-user]", el);
    if (sel.options.length - 1 !== users.length)
      sel.innerHTML = `<option value="">Todos os usuários</option>` + users.map(u => `<option ${u === st.user ? "selected" : ""}>${esc(u)}</option>`).join("");
    const q = st.q;
    let list = d.exited.filter(r => (!st.user || r.user === st.user) &&
      (!q || r.name.toLowerCase().includes(q) || String(r.pid).startsWith(q) || r.cmd.toLowerCase().includes(q) ||
        (r.parent_name || "").toLowerCase().includes(q)));
    if (st.group) {
      const g = new Map();
      for (const r of list) {
        const k = `${r.name}\u0000${r.cmd}`;
        const x = g.get(k);
        if (x) { x.count++; x.totalDur += r.duration; } else g.set(k, { ...r, count: 1, totalDur: r.duration });
      }
      list = [...g.values()].sort((a, b) => b.count - a.count || b.end - a.end);
    }
    $("[data-summary]", el).textContent = `${list.length} ${st.group ? "comandos distintos" : "processos"} · ${d.total} encerrados desde que o monitor iniciou (${ago(d.since)}) · guardando os últimos ${d.max}`;
    $("[data-note]", el).innerHTML = `O Linux não guarda histórico de processos: o painel verifica a cada ${d.interval}s quem apareceu e quem sumiu.
      Processos mais rápidos que isso (ex.: um <code>ls</code>) podem não aparecer. Mais usados: ${d.top_names.slice(0, 6).map(([n, c]) => `${esc(n)} (${c})`).join(", ") || "—"}.`;
    const total = list.length;
    list = list.slice(0, st.limit);
    $("[data-more]", el).hidden = total <= st.limit;

    $("[data-head]", el).innerHTML = st.group
      ? `<tr><th class="r">Vezes</th><th>Processo</th><th>Usuário</th><th>Última vez</th><th class="r">Duração média</th><th>Pai</th><th>Comando</th></tr>`
      : `<tr><th>Encerrado</th><th>Processo</th><th class="r">PID</th><th>Usuário</th><th class="r">Duração</th><th>Pai</th><th>Comando</th></tr>`;
    $("[data-tbody]", el).innerHTML = list.map(r => st.group
      ? `<tr data-id="${esc(r.id)}"><td class="r">${r.count}×</td><td class="pname">${esc(r.name)}</td><td>${esc(r.user)}</td>
          <td>${ago(r.end)}</td><td class="r">${dur(r.totalDur / r.count)}</td><td>${esc(r.parent_name || "—")}</td>
          <td class="cmd" title="${esc(r.cmd)}">${esc(r.cmd)}</td></tr>`
      : `<tr data-id="${esc(r.id)}"><td title="${new Date(r.end * 1000).toLocaleString("pt-BR")}">${ago(r.end)}</td><td class="pname">${esc(r.name)}${r.preexisting ? ` <span class="muted" title="Já rodava quando o monitor iniciou">·</span>` : ""}</td>
          <td class="r">${r.pid}</td><td>${esc(r.user)}</td><td class="r">${dur(r.duration)}</td><td>${esc(r.parent_name || "—")}</td>
          <td class="cmd" title="${esc(r.cmd)}">${esc(r.cmd)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="empty">Nenhum processo encerrado registrado ainda.</td></tr>`;
    $("[data-list]", el).innerHTML = list.map(r => `<li data-id="${esc(r.id)}">
        <span class="pname">${st.group ? `${r.count}× ` : ""}${esc(r.name)}</span>
        <span class="vals">${ago(r.end)}</span>
        <span class="meta">${st.group ? "" : `${r.pid} · `}${esc(r.user)}${r.parent_name ? ` · de ${esc(r.parent_name)}` : ""}</span>
        <span class="vals">${dur(st.group ? r.totalDur / r.count : r.duration)}</span></li>`).join("") ||
      `<li class="empty">Nenhum processo encerrado registrado ainda.</li>`;
  }

  return {
    start() { this.stop(); refresh(); timer = setInterval(refresh, 3000); },
    stop() { clearInterval(timer); timer = null; },
  };
}

// ---------- suspeitos ----------
export function suspiciousView(app, el) {
  let timer = null, showInfo = false, data = null;
  el.innerHTML = `
    <div class="toolbar">
      <label class="check"><input type="checkbox" data-info> Mostrar também os só informativos</label>
    </div>
    <div class="proc-summary" data-summary></div>
    <div class="susp-list" data-list></div>
    <details class="card" style="margin-top:12px" data-trusted-box><summary class="card-title" style="cursor:pointer">Confiáveis</summary><div data-trusted></div></details>
    <p class="note">Estes são sinais comuns em malware para Linux: executável apagado ou em pasta temporária, disfarce de thread do kernel,
      minerador, shell reverso, biblioteca injetada, programa fora dos pacotes rodando como root… Programas legítimos também podem
      disparar algum deles; não é um antivírus. Toque num item para ver os detalhes, o hash e o link do VirusTotal. Se for algo que você
      conhece, marque como confiável.</p>`;
  $("[data-info]", el).addEventListener("change", e => { showInfo = e.target.checked; refresh(); });
  $("[data-list]", el).addEventListener("click", e => {
    const c = e.target.closest("[data-k]");
    if (!c) return;
    const it = data.items[Number(c.dataset.k)];
    openInspect(app, it.alive ? { pid: it.pid, create_time: it.create_time } : { id: it.id }, refresh);
  });
  $("[data-trusted]", el).addEventListener("click", async e => {
    const b = e.target.closest("[data-untrust]");
    if (!b) return;
    try { await app.api("/api/processes/trust", { method: "POST", body: { exe: b.dataset.untrust, trusted: false } }); refresh(); }
    catch (err) { toast(err.message, true); }
  });

  async function refresh() {
    try { data = await app.api(`/api/processes/suspicious${showInfo ? "?info=1" : ""}`); render(); } catch { /* */ }
  }

  function render() {
    const items = data.items;
    const hi = items.filter(i => i.score >= 50).length, med = items.filter(i => i.score > 0 && i.score < 50).length;
    $("[data-summary]", el).textContent = items.length
      ? `${hi} suspeito(s) · ${med} com pontos de atenção${showInfo ? ` · ${items.length - hi - med} só informativos` : ""}${data.limited ? " · processos de outros usuários têm análise limitada (sem root)" : ""}`
      : "";
    $("[data-list]", el).innerHTML = items.length ? items.map((it, k) => {
      const v = verdict(it.score, it.flags);
      const fl = showInfo ? it.flags : it.flags.filter(f => f.level !== "info");
      return `<article class="card susp" data-k="${k}">
        <div class="card-head" style="margin-bottom:4px"><div style="min-width:0">
          <div class="pname">${esc(it.name)} <span class="muted">· ${it.pid}${it.count > 1 ? ` · ${it.count}×` : ""}</span></div>
          <div class="card-sub">${esc(it.user)} · ${it.alive ? "em execução" : `encerrado ${ago(it.end)}`}</div></div>
          <span class="chip ${v.cls}">${v.label}</span></div>
        ${it.exe ? `<div class="mono muted susp-exe">${esc(it.exe)}</div>` : ""}
        ${flagList(fl)}
      </article>`;
    }).join("") : `<div class="card empty">Nenhum processo com sinais de atenção.</div>`;
    $("[data-trusted]", el).innerHTML = data.trusted.length
      ? `<ul class="top-list">${data.trusted.map(t => `<li><span class="name mono">${esc(t)}</span><button class="btn btn-sm" data-untrust="${esc(t)}">Remover</button></li>`).join("")}</ul>`
      : `<p class="muted">Nenhum. Use "Marcar como confiável" na análise de um processo para escondê-lo desta lista.</p>`;
    $("[data-trusted-box] summary", el).textContent = `Confiáveis (${data.trusted.length})`;
  }

  return {
    start() { this.stop(); refresh(); timer = setInterval(refresh, 5000); },
    stop() { clearInterval(timer); timer = null; },
  };
}
