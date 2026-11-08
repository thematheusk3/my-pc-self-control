// Aba Incidentes: o que deu errado no PC, com linha do tempo, métricas, hipóteses e texto para colar numa IA.
import { ic } from "../icons.js";
import { $, $$, bytes, esc, toast } from "../ui.js";

let app, root, items = [], filter = "open", current = null, timer = null, detailTimer = null, watcher = null;
const SEV = { critical: ["critical", "Crítico"], error: ["serious", "Erro"], warning: ["warning", "Aviso"], notice: ["", "Nota"], info: ["", "Info"] };
const STATUS = { analyzing: "Analisando…", open: "Aberto", resolved: "Resolvido", ignored: "Ignorado" };
const FAMILY = { gpu: "GPU", crash: "Programa", memory: "Memória", service: "Serviço", thermal: "Temperatura", storage: "Disco",
  hardware: "Hardware", kernel: "Kernel", power: "Energia", session: "Sessão", manual: "Manual", change: "Mudança", other: "Outro" };
const CHARTS = [["cpu.temp", "Temp. CPU", "°C"], ["gpu.0.temp", "Temp. GPU", "°C"], ["gpu.0.busy", "Uso da GPU", "%"],
  ["cpu.total", "Uso da CPU", "%"], ["memory.percent", "Memória", "%"], ["gpu.0.gtt", "GTT da GPU", "%"], ["game.fps", "FPS", ""]];

const hm = t => new Date(t * 1000).toLocaleTimeString("pt-BR");
const when = t => new Date(t * 1000).toLocaleString("pt-BR");
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}
const sevChip = s => { const [c, l] = SEV[s] || ["", s]; return `<span class="chip ${c}">${l}</span>`; };

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div data-list-view>
      <div class="grid">
        <article class="card span-all">
          <div class="card-head"><div><div class="card-title">Incidentes</div>
            <div class="card-sub" data-watch>—</div></div>
            <button class="btn btn-sm btn-primary" data-act="investigate">Investigar um momento</button></div>
          <p class="note" style="margin-top:0">O painel lê o log do sistema o tempo todo e abre um incidente quando a GPU trava, um programa
            ou serviço cai, a memória acaba, o disco dá erro ou o PC desliga sem aviso. Cada incidente traz a linha do tempo, as
            métricas em volta, as hipóteses (por regras, sem IA) e um texto pronto para colar num chat com uma IA.</p>
          <div class="seg" role="group" aria-label="Filtro" data-filter>
            <button data-f="open" aria-pressed="true">Abertos</button><button data-f="" aria-pressed="false">Todos</button>
            <button data-f="resolved" aria-pressed="false">Resolvidos</button></div>
        </article>
      </div>
      <ul class="inc-list card" data-items><li class="empty">Carregando…</li></ul>
    </div>
    <div data-detail-view hidden></div>`;

  $("[data-filter]", el).addEventListener("click", e => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    filter = b.dataset.f;
    $$("[data-f]", el).forEach(x => x.setAttribute("aria-pressed", x === b));
    load();
  });
  $("[data-items]", el).addEventListener("click", e => {
    const li = e.target.closest("[data-id]");
    if (li) location.hash = `incidents:${li.dataset.id}`;
  });
  el.addEventListener("click", e => {
    if (e.target.closest("[data-act=investigate]")) openInvestigate();
  });
}

async function load() {
  try {
    const r = await app.api(`/api/incidents?limit=200${filter ? `&status=${filter}` : ""}`);
    items = filter === "open" ? r.items.filter(i => i.status === "open" || i.status === "analyzing") : r.items;
    watcher = r.watcher;
    renderList();
  } catch (e) { $("[data-items]", root).innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
}

function renderList() {
  const w = watcher;
  $("[data-watch]", root).textContent = !w ? "Vigia do journal desligado"
    : w.running ? `Vigiando o log do sistema · ${w.matched} eventos classificados${w.backlog_done ? "" : " · lendo o histórico…"}`
    : `Vigia parado${w.error ? `: ${w.error}` : ""}`;
  const ul = $("[data-items]", root);
  if (!items.length) {
    ul.innerHTML = `<li class="empty">${filter === "open" ? "Nenhum incidente aberto. Tudo certo por aqui." : "Nenhum incidente."}</li>`;
    return;
  }
  ul.innerHTML = items.map(i => `
    <li data-id="${i.id}" class="inc-row sev-${esc(i.severity)}">
      <div class="inc-main"><div class="inc-title">${esc(i.title)}${i.count > 1 ? ` <span class="chip">x${i.count}</span>` : ""}</div>
        <div class="meta">${esc(i.summary || STATUS[i.status] || "")}</div></div>
      <div class="vals">${sevChip(i.severity)}<div class="meta" title="${when(i.trigger_ts)}">${ago(i.trigger_ts)}</div>
        ${i.status !== "open" ? `<div class="meta">${STATUS[i.status] || i.status}</div>` : ""}</div>
    </li>`).join("");
}

// ---------- detalhe ----------
async function openDetail(id) {
  $("[data-list-view]", root).hidden = true;
  const box = $("[data-detail-view]", root);
  box.hidden = false;
  if (current?.id !== Number(id)) box.innerHTML = `<div class="card empty">Carregando…</div>`;
  clearTimeout(detailTimer);
  try {
    current = await app.api(`/api/incidents/${id}`);
  } catch (e) { box.innerHTML = `<div class="card empty">${esc(e.message)} <a href="#incidents">Voltar</a></div>`; return; }
  renderDetail();
  if (current.status === "analyzing") detailTimer = setTimeout(() => openDetail(id), 3000);
}

function renderDetail() {
  const i = current, r = i.report || {}, box = $("[data-detail-view]", root);
  const sub = r.subject || {};
  box.innerHTML = `<div class="grid">
    <article class="card span-all inc-head">
      <a class="inc-back" href="#incidents">← Incidentes</a>
      <div class="card-head" style="align-items:flex-start"><div>
        <h2 class="inc-h">${esc(i.title)}</h2>
        <div class="card-sub">${when(i.trigger_ts)} · ${esc(FAMILY[i.family] || i.family)}${i.count > 1 ? ` · aconteceu ${i.count} vezes` : ""}</div></div>
        <div>${sevChip(i.severity)} <span class="chip">${STATUS[i.status] || i.status}</span></div></div>
      ${i.status === "analyzing" ? `<p class="note">Juntando os eventos dos segundos seguintes e montando o relatório…</p>` : ""}
      <div class="toolbar inc-actions">
        <button class="btn btn-sm btn-primary" data-act="copy" ${r.ai_text ? "" : "disabled"}>Copiar para IA</button>
        <button class="btn btn-sm" data-act="download" ${r.ai_text ? "" : "disabled"}>Baixar .md</button>
        ${i.status === "resolved" ? `<button class="btn btn-sm" data-act="reopen">Reabrir</button>` : `<button class="btn btn-sm" data-act="resolve">Marcar como resolvido</button>`}
        <button class="btn btn-sm btn-ghost-danger" data-act="delete">Apagar</button>
      </div>
    </article>

    ${(r.hypotheses || []).length ? `<article class="card span-all">
      <div class="card-head"><div><div class="card-title">Hipóteses</div><div class="card-sub">Por correlação de eventos e regras (sem IA). A primeira é a mais provável.</div></div></div>
      <div class="hyps">${r.hypotheses.map((h, n) => `
        <details class="hyp" ${n === 0 ? "open" : ""}>
          <summary><span class="hyp-title">${esc(h.title)}</span>
            <span class="hyp-score lvl-${h.level === "alta" ? "hi" : h.level === "média" ? "mid" : "lo"}"><i style="width:${h.score}%"></i></span>
            <span class="hyp-level">${esc(h.level)}</span></summary>
          ${h.evidence.length ? `<div class="stat-label">Evidências</div><ul>${h.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
          ${h.checks.length ? `<div class="stat-label">O que verificar</div><ul>${h.checks.map(e => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
        </details>`).join("")}</div>
    </article>` : ""}

    ${sub.type ? `<article class="card ${sub.stack?.length || sub.logs?.length ? "span-2" : ""}">
      <div class="card-head"><div class="card-title">${sub.type === "unit" ? "Serviço envolvido" : sub.type === "gpu" ? "Dispositivo" : "Programa envolvido"}</div></div>
      <dl class="kv">
        ${sub.game ? `<dt>Jogo</dt><dd>${esc(sub.game)}</dd>` : ""}
        <dt>${sub.type === "unit" ? "Unidade" : "Nome"}</dt><dd>${esc(sub.name || "—")}</dd>
        ${sub.launcher ? `<dt>Aberto por</dt><dd>${esc(sub.launcher)}</dd>` : ""}
        ${sub.signal ? `<dt>Sinal</dt><dd>${esc(sub.signal)}</dd>` : ""}
        ${sub.pid ? `<dt>PID</dt><dd>${sub.pid}</dd>` : ""}
        ${sub.runtime != null ? `<dt>Rodou por</dt><dd>${dur(sub.runtime)}</dd>` : ""}
        ${sub.result ? `<dt>Resultado</dt><dd>${esc(sub.result)}</dd>` : ""}
        ${sub.unit ? `<dt>Unidade</dt><dd class="mono">${esc(sub.unit)}</dd>` : ""}
      </dl>
      ${sub.exe ? `<div class="cmdline">${esc(sub.exe)}${sub.cmdline ? `\n${esc(sub.cmdline)}` : ""}</div>` : ""}
      ${sub.stack?.length ? `<details><summary class="stat-label">Pilha do core dump</summary><pre class="inc-pre">${esc(sub.stack.join("\n"))}</pre></details>` : ""}
      ${sub.logs?.length ? `<details open><summary class="stat-label">Últimas linhas do serviço</summary><pre class="inc-pre">${esc(sub.logs.map(m => `${hm(m.ts)} ${m.msg}`).join("\n"))}</pre></details>` : ""}
    </article>` : ""}

    ${metricsCard(r)}

    <article class="card span-all">
      <div class="card-head"><div><div class="card-title">Linha do tempo</div>
        <div class="card-sub">${r.window ? `${hm(r.window[0])} a ${hm(r.window[1])}` : ""} · eventos classificados, ações do painel, arquivos e pacotes</div></div></div>
      ${(r.timeline || []).length ? `<ol class="timeline">${r.timeline.map(e => `
        <li class="tl fam-${esc(e.family)} ${e.trigger ? "tl-trigger" : ""} sev-${esc(e.severity)}">
          <time>${hm(e.ts)}</time>
          <div><span class="tl-tag">${esc(e.label || FAMILY[e.family] || e.kind)}</span>${e.who ? ` <span class="muted">${esc(e.who)}</span>` : ""}
            ${e.count > 1 ? `<span class="chip">x${e.count}</span>` : ""}
            <div class="tl-text">${esc(e.text)}</div></div></li>`).join("")}</ol>` : `<p class="muted">Nenhum evento além do gatilho.</p>`}
    </article>

    ${contextCard(r)}
    ${changesCard(r)}

    ${(r.logs || []).length ? `<article class="card span-all"><details><summary class="card-title">Trecho do log do sistema (${r.logs.length} linhas)</summary>
      <pre class="inc-pre">${esc(r.logs.join("\n"))}</pre></details></article>` : ""}

    <article class="card span-all">
      <div class="card-head"><div class="card-title">Observações</div></div>
      <textarea class="mh-text" data-note rows="3" placeholder="O que você estava fazendo, o que tentou, o que resolveu…">${esc(i.note || "")}</textarea>
      <div class="toolbar" style="margin-top:8px"><button class="btn btn-sm" data-act="note">Salvar observação</button></div>
    </article>
  </div>`;
  box.onclick = onDetailClick;
}

function metricsCard(r) {
  const m = r.metrics || {};
  if (!m.summary?.length) return `<article class="card"><div class="card-head"><div class="card-title">Métricas</div></div>
    <p class="muted">Sem métricas daquele momento${r.context ? "" : " (o painel não estava gravando ou o incidente é antigo)"}.</p></article>`;
  const charts = CHARTS.filter(([k]) => (m.series || {})[k]?.length > 2);
  return `<article class="card span-2">
    <div class="card-head"><div><div class="card-title">Métricas em volta</div><div class="card-sub">${esc(m.source || "")} · a linha vertical marca o gatilho</div></div></div>
    ${charts.length ? `<div class="inc-charts">${charts.map(([k, l, u]) => `<figure>${chart(m.series[k], r.trigger_ts, r.window)}
      <figcaption>${l}</figcaption></figure>`).join("")}</div>` : ""}
    <div class="table-wrap"><table class="ptable inc-table"><thead><tr><th>Métrica</th><th>Antes</th><th>No momento</th><th>Pico</th></tr></thead>
      <tbody>${m.summary.map(x => `<tr><td>${esc(x.label)}</td><td>${fmt(x.before, x.unit)}</td><td><b>${fmt(x.at, x.unit)}</b></td><td>${fmt(x.max, x.unit)}</td></tr>`).join("")}</tbody></table></div>
  </article>`;
}

function chart(pts, t, win) {
  const W = 220, H = 56, t0 = win ? win[0] : pts[0][0], t1 = win ? win[1] : pts.at(-1)[0];
  const vals = pts.map(p => p[1]);
  let mn = Math.min(...vals), mx = Math.max(...vals);
  if (mx - mn < 1) { mx += 1; mn -= 1; }
  const x = ts => ((ts - t0) / (t1 - t0 || 1)) * W, y = v => H - 4 - ((v - mn) / (mx - mn)) * (H - 8);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="inc-chart" role="img"><path d="${d}" fill="none" stroke="var(--series-1)" stroke-width="1.6"/>
    <line x1="${x(t)}" x2="${x(t)}" y1="0" y2="${H}" stroke="var(--critical)" stroke-dasharray="3 2"/>
    <text x="2" y="10" class="inc-chart-v">${Math.round(mx)}</text><text x="2" y="${H - 1}" class="inc-chart-v">${Math.round(mn)}</text></svg>`;
}

function contextCard(r) {
  const c = r.context;
  if (!c) return "";
  const row = p => `<tr><td>${esc(p.name)}</td><td>${p.pid}</td><td>${p.cpu != null ? `${Number(p.cpu).toFixed(1)}%` : "—"}</td><td>${bytes(p.mem)}</td></tr>`;
  return `<article class="card">
    <div class="card-head"><div><div class="card-title">No momento</div><div class="card-sub">Capturado às ${hm(c.captured)}</div></div></div>
    ${c.game ? `<p>Jogo aberto: <b>${esc(c.game.name)}</b>${c.game.fps != null ? ` · ${Math.round(c.game.fps)} FPS` : ""}</p>` : ""}
    <div class="stat-label">Mais CPU</div>
    <table class="ptable inc-table"><tbody>${(c.top_cpu || []).slice(0, 6).map(row).join("")}</tbody></table>
    <div class="stat-label" style="margin-top:8px">Mais memória</div>
    <table class="ptable inc-table"><tbody>${(c.top_mem || []).slice(0, 5).map(row).join("")}</tbody></table>
  </article>`;
}

function changesCard(r) {
  const ch = r.changes || {};
  const pk = ch.packages || [], hw = ch.hardware || [], panel = ch.panel || [], files = ch.files || [];
  if (!pk.length && !hw.length && !panel.length && !files.length) return "";
  return `<article class="card">
    <div class="card-head"><div><div class="card-title">Mudanças recentes</div><div class="card-sub">Pacotes (3 dias), hardware (7 dias), painel e arquivos</div></div></div>
    <ul class="inc-changes">
      ${files.map(f => `<li><time>${hm(f.ts)}</time> ${esc(f.path)} alterado</li>`).join("")}
      ${panel.map(a => `<li><time>${hm(a.ts)}</time> Painel: ${esc(a.text)}</li>`).join("")}
      ${pk.slice(-12).map(u => `<li><time>${new Date(u.ts * 1000).toLocaleDateString("pt-BR")}</time> ${esc(u.action)} ${esc(u.package)} <span class="muted">${esc(u.version)}</span></li>`).join("")}
      ${hw.map(h => `<li><time>${new Date(h.ts * 1000).toLocaleDateString("pt-BR")}</time> Hardware: ${esc((h.changes || []).map(c => c.path).join(", "))}</li>`).join("")}
    </ul></article>`;
}

const fmt = (v, u) => v == null ? "—" : u === "B/s" ? `${bytes(v)}/s` : `${v}${u && !["%", "°C"].includes(u) ? " " : ""}${u}`;
function dur(s) { s = Math.round(s); return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`; }

let armedDelete = false;
async function onDetailClick(e) {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act || !current) return;
  const r = current.report || {};
  if (act === "copy") {
    try { await navigator.clipboard.writeText(r.ai_text); toast("Copiado. Cole no chat da IA."); }
    catch { fallbackCopy(r.ai_text); }
  }
  if (act === "download") {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([r.ai_text], { type: "text/markdown" }));
    a.download = `incidente-${current.id}-${new Date(current.trigger_ts * 1000).toISOString().slice(0, 16).replace(/[:T]/g, "-")}.md`;
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  if (act === "resolve" || act === "reopen") await update({ status: act === "resolve" ? "resolved" : "open" }, act === "resolve" ? "Marcado como resolvido." : "Reaberto.");
  if (act === "note") await update({ note: $("[data-note]", root).value }, "Observação salva.");
  if (act === "delete") {
    if (!armedDelete) { armedDelete = true; e.target.textContent = "Toque de novo para apagar"; setTimeout(() => { armedDelete = false; }, 4000); return; }
    try { await app.api(`/api/incidents/${current.id}/delete`, { method: "POST", body: {} }); toast("Incidente apagado."); location.hash = "incidents"; }
    catch (err) { toast(err.message, true); }
  }
}

// navegador sem clipboard (http fora do localhost): mostra o texto selecionado
function fallbackCopy(text) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>Texto para a IA</h2><p class="muted">Selecione tudo e copie.</p>
    <textarea class="mh-text" rows="14" readonly>${esc(text)}</textarea><div class="sheet-actions"><button class="btn full" data-close>Fechar</button></div>`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.showModal();
  const ta = dlg.querySelector("textarea"); ta.focus(); ta.select();
}

async function update(body, msg) {
  try { current = await app.api(`/api/incidents/${current.id}`, { method: "POST", body }); toast(msg); renderDetail(); }
  catch (e) { toast(e.message, true); }
}

// ---------- investigação manual ----------
export function openInvestigate(at = null, note = "") {
  const dlg = $("#sheet");
  const local = t => { const d = new Date(t * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
  dlg.innerHTML = `<h2>Investigar um momento</h2>
    <p class="muted">O painel junta logs, métricas, processos e mudanças em volta do momento e monta as hipóteses.</p>
    <div class="mh-field"><span class="stat-label">Momento</span><input class="search" type="datetime-local" data-at value="${local(at || Date.now() / 1000)}"></div>
    <div class="mh-field"><span class="stat-label">Quanto tempo antes analisar</span>
      <div class="seg" data-before role="group">${[[300, "5 min"], [900, "15 min"], [3600, "1 h"]].map(([v, l], n) =>
        `<button data-v="${v}" aria-pressed="${n === 0}">${l}</button>`).join("")}</div></div>
    <div class="mh-field"><span class="stat-label">O que aconteceu (opcional)</span>
      <textarea class="mh-text" rows="3" data-note placeholder="ex.: a tela congelou e o som travou">${esc(note)}</textarea></div>
    <div class="sheet-actions"><button class="btn btn-primary" data-go>Investigar</button><button class="btn" data-close>Cancelar</button></div>`;
  let before = 300;
  dlg.querySelector("[data-before]").onclick = e => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    before = Number(b.dataset.v);
    dlg.querySelectorAll("[data-before] button").forEach(x => x.setAttribute("aria-pressed", x === b));
  };
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-go]").onclick = async () => {
    const t = new Date(dlg.querySelector("[data-at]").value).getTime() / 1000;
    try {
      const r = await app.api("/api/incidents/investigate", { method: "POST",
        body: { at: isNaN(t) ? Date.now() / 1000 : t, before, note: dlg.querySelector("[data-note]").value } });
      dlg.close();
      location.hash = `incidents:${r.id}`;
    } catch (e) { toast(e.message, true); }
  };
  dlg.showModal();
}

function route(sub) {
  clearTimeout(detailTimer);
  if (sub && /^\d+$/.test(sub)) openDetail(sub);
  else {
    current = null;
    $("[data-detail-view]", root).hidden = true;
    $("[data-list-view]", root).hidden = false;
    load();
  }
}

export default {
  id: "incidents", title: "Incidentes", icon: ic("warning"),
  enabled: a => !!a.info?.features?.incidents,
  mount, route,
  show() { timer = setInterval(() => { if (!current && !document.hidden) load(); }, 10000); },
  hide() { clearInterval(timer); clearTimeout(detailTimer); },
};
