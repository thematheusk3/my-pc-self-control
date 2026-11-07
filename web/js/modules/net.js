// Aba Rede: quem está conectado, para onde, quanto envia/recebe; conexões encerradas; portas abertas.
import { ic } from "../icons.js";
import { $, bytes, duration, esc, rate } from "../ui.js";
import { openInspect } from "../inspect.js";

let app, root, timer = null, data = null, closed = null, view = "procs";
const st = { q: "", hideLocal: true, prog: "" };
const KIND = { internet: ["Internet", "warning"], lan: ["Rede local", "good"], local: ["Esta máquina", ""],
  "link-local": ["Link-local", ""], multicast: ["Multicast", ""], any: ["Qualquer", ""], "?": ["?", ""] };

const ago = t => { const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? `há ${Math.round(s)}s` : s < 3600 ? `há ${Math.round(s / 60)}min` : s < 86400 ? `há ${Math.round(s / 3600)}h` : new Date(t * 1000).toLocaleDateString("pt-BR"); };
const dur = s => (s < 60 ? `${Math.round(s)}s` : duration(s));
const prog = c => (c.procs?.length ? c.procs.map(p => p.name).filter((v, i, a) => a.indexOf(v) === i).join(", ") : null);
const progCell = c => prog(c) ? esc(prog(c)) : `<span class="muted" title="Processo de outro usuário (ex.: root). Sem root o painel não vê o dono.">outro usuário</span>`;
const kindChip = k => { const [l, c] = KIND[k] || [k, ""]; return `<span class="chip ${c}">${l}</span>`; };
const dest = c => `${c.host ? `<b>${esc(c.host)}</b><br>` : ""}<span class="mono muted">${esc(c.rip)}</span>`;
const svc = (c, port) => `${port}${c.service ? ` <span class="muted">${esc(c.service)}</span>` : ""}`;
const b = n => (n == null ? `<span class="muted">—</span>` : bytes(n));

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div class="seg proc-views" data-views>
      <button data-v="procs" aria-pressed="true">Por programa</button>
      <button data-v="conns" aria-pressed="false">Conexões</button>
      <button data-v="closed" aria-pressed="false">Encerradas</button>
      <button data-v="listen" aria-pressed="false">Portas abertas</button>
    </div>
    <div class="toolbar" data-tools>
      <input class="search" type="search" placeholder="Buscar programa, IP, host ou porta…" data-q>
      <label class="check"><input type="checkbox" data-local checked> Ocultar conexões internas (esta máquina)</label>
      <button class="btn btn-sm" data-clearprog hidden></button>
    </div>
    <div class="proc-summary" data-summary></div>
    <div data-body></div>
    <details class="card" style="margin-top:12px"><summary class="card-title" style="cursor:pointer">O que dá para ver (e o que não dá)</summary>
      <div class="note" style="font-size:13px">
        <p>${ic("check")} <b>Quem</b> conecta (programa), <b>para onde</b> (IP, nome reverso do IP, porta/serviço), <b>quanto</b> envia e recebe por conexão TCP e em que ritmo.</p>
        <p>${ic("x")} <b>O conteúdo</b>: quase tudo hoje é criptografado (HTTPS/TLS). Dá para saber que o Discord falou com um servidor da Cloudflare e mandou 30 KB, mas não o que estava dentro.
          Para ver pacotes, use o Wireshark (precisa de root). Para ver dentro do HTTPS do seu próprio navegador, só com um proxy como o mitmproxy, que instala um certificado próprio no sistema. É invasivo e serve mais para depurar.</p>
        <p>${ic("x")} <b>Bytes em UDP</b>: os navegadores usam muito HTTP/3 (QUIC), que roda em UDP. Essas conexões aparecem, mas sem contagem de bytes.</p>
        <p>${ic("warning")} <b>Processos de outros usuários</b> (root, serviços do sistema) aparecem como "outro usuário": sem root o painel não vê o dono do socket.
          O nome do site vem do DNS reverso, que nem sempre existe (CDNs costumam mostrar o nome do provedor, ex.: cloudflare, googleusercontent).</p>
      </div></details>`;
  $("[data-views]", el).addEventListener("click", e => {
    const bt = e.target.closest("[data-v]");
    if (bt) location.hash = bt.dataset.v === "procs" ? "net" : `net:${bt.dataset.v}`;
  });
  $("[data-q]", el).addEventListener("input", e => { st.q = e.target.value.toLowerCase(); render(); });
  $("[data-local]", el).addEventListener("change", e => { st.hideLocal = e.target.checked; render(); });
  $("[data-clearprog]", el).addEventListener("click", () => { st.prog = ""; render(); });
  $("[data-body]", el).addEventListener("click", e => {
    const r = e.target.closest("[data-prog]");
    if (r) { st.prog = r.dataset.prog; location.hash = "net:conns"; return; }
    const c = e.target.closest("[data-cid]");
    if (c) openConn(c.dataset.src === "closed" ? closed.closed.find(x => x.id === c.dataset.cid) : data.connections.find(x => x.id === c.dataset.cid));
    if (e.target.closest("[data-clear]")) clearClosed(e.target.closest("[data-clear]"));
  });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : root.classList.contains("active") && start()));
}

function route(sub) {
  view = ["conns", "closed", "listen"].includes(sub) ? sub : "procs";
  root.querySelectorAll("[data-views] button").forEach(x => x.setAttribute("aria-pressed", x.dataset.v === view));
  if (timer) refresh();
}

async function refresh() {
  try {
    if (view === "closed") closed = await app.api("/api/net/closed");
    else data = await app.api("/api/net");
    render();
  } catch { /* tenta de novo */ }
}
function start() { stop(); refresh(); timer = setInterval(refresh, (data?.interval || 2) * 1000); }
function stop() { clearInterval(timer); timer = null; }

function match(c) {
  const q = st.q;
  if (st.hideLocal && c.kind === "local") return false;
  if (st.prog && (prog(c) || "?") !== st.prog && !(st.prog === "?" && !prog(c))) return false;
  return !q || [prog(c), c.rip, c.host, c.rport, c.lport, c.service].some(v => String(v ?? "").toLowerCase().includes(q));
}

function render() {
  const body = $("[data-body]", root), sum = $("[data-summary]", root);
  $("[data-local]", root).closest("label").hidden = view === "listen" || view === "procs";
  const cp = $("[data-clearprog]", root);
  cp.hidden = !st.prog || view !== "conns";
  cp.textContent = `Programa: ${st.prog === "?" ? "outro usuário" : st.prog} ✕`;

  if (view === "closed") {
    if (!closed) return;
    const list = closed.closed.filter(match);
    sum.textContent = `${list.length} conexões encerradas · guardando as últimas ${closed.max}`;
    body.innerHTML = `<div class="toolbar"><span style="flex:1"></span><button class="btn btn-sm" data-clear>Limpar histórico</button></div>
      <div class="card table-wrap"><div class="hscroll"><table class="ptable"><thead><tr><th>Encerrada</th><th>Programa</th><th>Destino</th><th>Onde</th><th>Porta</th><th class="r">Enviado</th><th class="r">Recebido</th><th class="r">Duração</th></tr></thead>
      <tbody>${list.slice(0, 400).map(c => `<tr data-cid="${esc(c.id)}" data-src="closed"><td>${ago(c.end)}</td><td class="pname">${progCell(c)}</td><td>${dest(c)}</td><td>${kindChip(c.kind)}</td>
        <td>${svc(c, c.rport)}</td><td class="r">${b(c.sent)}</td><td class="r">${b(c.recv)}</td><td class="r">${dur(c.duration)}</td></tr>`).join("") ||
        `<tr><td colspan="8" class="empty">Nenhuma conexão encerrada registrada ainda.</td></tr>`}</tbody></table></div></div>`;
    return;
  }
  if (!data) return;

  if (view === "listen") {
    const list = data.listening.filter(l => !st.q || [l.port, l.service, ...l.procs.map(p => p.name)].some(v => String(v ?? "").toLowerCase().includes(st.q)));
    const pub = list.filter(l => l.scope === "rede").length;
    sum.textContent = `${list.length} portas abertas · ${pub} aceitam conexões de outras máquinas`;
    body.innerHTML = `<div class="card table-wrap"><div class="hscroll"><table class="ptable"><thead><tr><th>Porta</th><th>Protocolo</th><th>Serviço</th><th>Acessível de</th><th>Programa</th><th>Endereço</th></tr></thead>
      <tbody>${list.map(l => `<tr><td class="pname">${l.port}</td><td>${l.proto.toUpperCase()}</td><td>${esc(l.service || "—")}</td>
        <td>${l.scope === "rede" ? `<span class="chip warning">Rede</span>` : `<span class="chip">Só esta máquina</span>`}</td>
        <td>${l.procs.length ? esc([...new Set(l.procs.map(p => p.name))].join(", ")) : `<span class="muted">outro usuário</span>`}</td>
        <td class="mono muted">${esc(l.ips.join(", "))}</td></tr>`).join("")}</tbody></table></div></div>
      <p class="note">"Rede" = qualquer aparelho que alcance esta máquina pode tentar conectar. Portas que você não reconhece, abertas para a rede, valem uma investigação.</p>`;
    return;
  }

  if (view === "procs") {
    const list = data.processes.filter(p => !st.q || (p.name || "outro usuário").toLowerCase().includes(st.q) || p.hosts.some(h => h.toLowerCase().includes(st.q)));
    const up = list.reduce((s, p) => s + p.rate_up, 0), down = list.reduce((s, p) => s + p.rate_down, 0);
    sum.textContent = `${list.length} programas com conexões · agora ↑ ${rate(up)} ↓ ${rate(down)} (TCP) · totais desde ${new Date(data.since * 1000).toLocaleString("pt-BR")}`;
    body.innerHTML = `<div class="net-procs">${list.map(p => `<article class="card susp" data-prog="${esc(p.name || "?")}">
        <div class="card-head" style="margin-bottom:6px"><div style="min-width:0"><div class="pname">${p.name ? esc(p.name) : `<span class="muted">outro usuário</span>`}</div>
          <div class="card-sub">${p.conns} conexões${p.internet ? ` · ${p.internet} com a internet` : ""}${p.pids.length ? ` · PID ${p.pids.slice(0, 4).join(", ")}${p.pids.length > 4 ? "…" : ""}` : ""}</div></div>
          <div class="net-rate"><span>↑ ${rate(p.rate_up)}</span><span>↓ ${rate(p.rate_down)}</span></div></div>
        <div class="stats" style="margin-top:4px"><div><div class="stat-label">Enviado</div><div class="stat-value">${bytes(p.sent)}</div></div>
          <div><div class="stat-label">Recebido</div><div class="stat-value">${bytes(p.recv)}</div></div></div>
        ${p.hosts.length ? `<div class="note net-hosts">${p.hosts.slice(0, 6).map(esc).join(" · ")}${p.hosts.length > 6 ? " …" : ""}</div>` : ""}
      </article>`).join("") || `<div class="card empty">Nenhuma conexão.</div>`}</div>`;
    return;
  }

  // conexões ao vivo
  const list = data.connections.filter(match);
  const inet = list.filter(c => c.kind === "internet").length, inbound = list.filter(c => c.direction === "entrada").length;
  sum.textContent = `${list.length} conexões · ${inet} com a internet · ${inbound} de entrada`;
  body.innerHTML = `<div class="card table-wrap"><div class="hscroll"><table class="ptable"><thead><tr><th>Programa</th><th>Destino</th><th>Onde</th><th>Porta</th><th></th><th class="r">↑/s</th><th class="r">↓/s</th><th class="r">Enviado</th><th class="r">Recebido</th><th class="r">Há</th></tr></thead>
    <tbody>${list.slice(0, 400).map(c => `<tr data-cid="${esc(c.id)}"><td class="pname">${progCell(c)}${c.warn ? ` <span class="chip critical" title="Porta comum em ${esc(c.warn)}">!</span>` : ""}</td>
      <td>${dest(c)}</td><td>${kindChip(c.kind)}</td><td>${svc(c, c.direction === "entrada" ? c.lport : c.rport)} <span class="muted">${c.proto.toUpperCase()}</span></td>
      <td>${c.direction === "entrada" ? `<span class="chip warning" title="Outra máquina conectou aqui">entrada</span>` : ""}</td>
      <td class="r">${c.sent == null ? "—" : rate(c.rate_up)}</td><td class="r">${c.recv == null ? "—" : rate(c.rate_down)}</td>
      <td class="r">${b(c.sent)}</td><td class="r">${b(c.recv)}</td><td class="r">${dur(Date.now() / 1000 - c.first)}</td></tr>`).join("") ||
      `<tr><td colspan="10" class="empty">Nenhuma conexão.</td></tr>`}</tbody></table></div></div>`;
}

async function clearClosed(btn) {
  if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Toque de novo para limpar"; return; }
  await app.api("/api/net/closed/clear", { method: "POST", body: {} }).catch(() => {});
  refresh();
}

function openConn(c) {
  if (!c) return;
  const dlg = $("#sheet");
  const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
  const pub = c.kind === "internet";
  dlg.innerHTML = `<h2>${esc(prog(c) || "Processo de outro usuário")}</h2>
    <div class="muted">${c.proto.toUpperCase()} · ${c.direction === "entrada" ? "conexão de entrada (outra máquina conectou aqui)" : "conexão de saída"}${c.end ? " · encerrada" : ` · ${esc(c.state || "")}`}</div>
    ${c.warn ? `<p class="fan-alert critical">A porta ${c.rport} é comum em ${esc(c.warn)}. Pode ser legítimo, mas vale conferir o programa.</p>` : ""}
    <dl class="kv insp-kv" style="margin-top:10px">
      ${row("Destino", `${c.host ? `<b>${esc(c.host)}</b> · ` : ""}<span class="mono">${esc(c.rip)}:${c.rport}</span>`)}
      ${row("Onde fica", kindChip(c.kind))}
      ${row("Serviço", esc(c.service || "desconhecido"))}
      ${row("Origem local", `<span class="mono">${esc(c.lip)}:${c.lport}</span>`)}
      ${row("Enviado", c.sent == null ? "não medido (UDP)" : bytes(c.sent))}
      ${row("Recebido", c.recv == null ? "não medido (UDP)" : bytes(c.recv))}
      ${!c.end && c.sent != null ? row("Agora", `↑ ${rate(c.rate_up)} · ↓ ${rate(c.rate_down)}`) : ""}
      ${row("Aberta", `${new Date(c.first * 1000).toLocaleString("pt-BR")}`)}
      ${c.end ? row("Encerrada", `${new Date(c.end * 1000).toLocaleString("pt-BR")} · durou ${dur(c.duration)}`) : ""}
      ${row("PID", c.procs.map(p => p.pid).join(", "))}
    </dl>
    <p class="note">O conteúdo não é visível: ${c.rport === 443 || c.service === "HTTPS" ? "HTTPS é criptografado." : "o painel só vê metadados da conexão."}</p>
    <div class="sheet-actions">
      ${c.procs.length && !c.end ? `<button class="btn" data-insp>Analisar processo</button>` : ""}
      ${pub ? `<a class="btn" href="https://ipinfo.io/${encodeURIComponent(c.rip)}" target="_blank" rel="noopener noreferrer" title="Abre o ipinfo.io: mostra a empresa e o país do IP">Quem é este IP?</a>` : ""}
      <button class="btn full" data-close>Fechar</button>
    </div>`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-insp]")?.addEventListener("click", () => openInspect(app, { pid: c.procs[0].pid }));
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); }, { once: true });
  dlg.showModal();
}

export default {
  id: "net", title: "Rede", icon: ic("arrows-down-up"), mount, route,
  enabled: a => !!a.info?.features?.netmon,
  show: start, hide: stop,
};
