import { ic } from "../icons.js";
import { $, duration, esc, toast } from "../ui.js";

let app, root;

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid"><article class="card span-2" data-sys></article><article class="card" data-srv></article>
    <article class="card" data-account>
      <div class="card-head"><div class="card-title">Acesso</div></div>
      <form class="pw-form" data-pw>
        <input class="search" type="password" name="current" autocomplete="current-password" placeholder="Senha atual" required>
        <input class="search" type="password" name="new" autocomplete="new-password" placeholder="Nova senha (mín. 6)" minlength="6" required>
        <input class="search" type="password" name="new2" autocomplete="new-password" placeholder="Repita a nova senha" minlength="6" required>
        <button class="btn btn-primary" type="submit">Trocar senha</button>
      </form>
      <p class="note">Trocar a senha desconecta os outros aparelhos.</p>
      <form method="post" action="/logout" style="margin-top:12px"><button class="btn" type="submit" style="width:100%">Sair</button></form>
    </article>
    <article class="card span-2" data-who>
      <div class="card-head"><div class="card-title">Quem está acessando</div><button class="btn btn-sm" data-who-others hidden>Encerrar as outras</button></div>
      <div data-who-body class="note" style="margin-top:0">Carregando…</div>
    </article>
    <article class="card span-2" data-audit>
      <div class="card-head"><div class="card-title">Ações feitas pelo painel</div>
        <div class="audit-filters"><select class="search" data-audit-cat><option value="">Todas</option></select>
          <select class="search" data-audit-sess><option value="">Todos os aparelhos</option><option value="me">Só este aparelho</option></select></div></div>
      <div data-audit-body class="note" style="margin-top:0">Carregando…</div>
    </article>
    ${app.info.features.unlock ? `<article class="card" data-session>
      <div class="card-head"><div class="card-title">Sessão</div><div data-sess-chip></div></div>
      <div data-sess-body class="note" style="margin-top:0">Carregando…</div>
    </article>` : ""}
    ${app.info.features.power ? `<article class="card" data-power>
      <div class="card-head"><div class="card-title">Energia</div></div>
      <div class="power-btns">
        <button class="btn" data-act="reboot">${ic("arrow-clockwise")} Reiniciar</button>
        <button class="btn btn-danger" data-act="poweroff">${ic("power")} Desligar</button>
      </div>
      <label class="check" style="margin-top:12px"><input type="checkbox" data-force checked> Forçar finalização</label>
      <p class="note" style="margin-top:6px">Forçar ignora programas que estão bloqueando o desligamento (downloads, jogos, "deseja salvar?")
        e outros usuários logados. Arquivos não salvos podem ser perdidos.</p>
      <p class="note" data-power-warn hidden></p>
      <div class="power-timer" data-timer>
        <div class="stat-label" style="margin-top:14px">Timer</div>
        <div data-timer-body></div>
      </div>
    </article>` : ""}
    ${app.info.features.updates ? `<article class="card span-2" data-updates>
      <div class="card-head"><div class="card-title">Atualizações pendentes</div><button class="btn btn-sm" data-upd-refresh>Verificar agora</button></div>
      <div data-upd-body class="note" style="margin-top:0">Carregando…</div>
    </article>` : ""}</div>`;
  $("[data-pw]", el).addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target, btn = f.querySelector("button");
    if (f.new.value !== f.new2.value) return toast("As senhas novas não conferem.", true);
    btn.disabled = true;
    try {
      await app.api("/api/auth/password", { method: "POST", body: { current: f.current.value, new: f.new.value } });
      f.reset();
      toast("Senha alterada.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  if (app.info.features.power) setupPower(el);
  if (app.info.features.updates) setupUpdates(el);
  if (app.info.features.unlock) setupSession(el);
  setupWho(el);
  setupAudit(el);
  app.onSnapshot(update);
}

// ---------- sessão (bloquear / desbloquear) ----------
let sessTimer = null;

function setupSession(el) {
  const card = $("[data-session]", el);
  card.addEventListener("click", async e => {
    const b = e.target.closest("[data-sess]");
    if (!b) return;
    if (b.dataset.sess === "unlock") return openUnlock(app, refreshSession);
    b.disabled = true;
    try { await app.api("/api/session/lock", { method: "POST", body: {} }); toast("Tela do PC bloqueada."); }
    catch (err) { toast(err.message, true); }
    refreshSession();
  });
}

async function refreshSession() {
  const card = $("[data-session]", root);
  if (!card) return;
  let st;
  try { st = await app.api("/api/session"); } catch { return; }
  const s = st.session;
  $("[data-sess-chip]", card).innerHTML = !s ? `<span class="chip warning">Tela de login</span>`
    : s.locked ? `<span class="chip warning">Bloqueada</span>` : `<span class="chip good">Desbloqueada</span>`;
  const auto = st.autologin_me && st.lock_on_start;
  $("[data-sess-body]", card).innerHTML = (s ? `
      <dl class="kv" style="margin-bottom:12px"><dt>Usuário</dt><dd>${esc(st.user)}</dd><dt>Sessão</dt><dd>${esc(s.desktop || "")} · ${esc(s.type)}</dd></dl>
      <div class="power-btns">
        <button class="btn" data-sess="lock" ${s.locked ? "disabled" : ""}>${ic("lock")} Bloquear</button>
        <button class="btn btn-primary" data-sess="unlock" ${s.locked ? "" : "disabled"}>${ic("lock-open")} Desbloquear</button>
      </div>`
    : st.other_users?.length
    ? `<p class="confirm-text" style="margin-top:0"><b>${ic("warning")} Há uma sessão aberta de ${esc(st.other_users.join(", "))}, mas o painel está ligado à conta ${esc(st.user)}${st.service_user !== st.user ? ` (serviço rodando como ${esc(st.service_user)})` : ""}.</b>
       Áudio, tela, captura e apps não funcionam assim. Reinstale a partir da conta certa: <code>sudo ./install.sh --user ${esc(st.other_users[0])}</code></p>`
    : `<p class="confirm-text" style="margin-top:0">Ninguém está logado: o PC está na tela de login, que não pode ser controlada pelo painel.</p>`) +
    `<p class="note">${auto
      ? `${ic("check")} <b>Entrar pelo celular ativado</b>: ao ligar, o PC entra sozinho em <b>${esc(st.user)}</b> com a tela bloqueada; desbloqueie por aqui.`
      : `Para entrar pelo celular ao ligar o PC, ative o login automático com a tela já bloqueada. Na máquina, rode:<br>
         <code>sudo /opt/applications/self-monitoring/tools/login-pelo-celular.sh ativar</code>
         ${st.autologin_me && !st.lock_on_start ? `<br><b>${ic("warning")} O login automático está ligado SEM bloqueio ao iniciar: qualquer um que ligar o PC entra na sua conta.</b>` : ""}`}</p>`;
}

export function openUnlock(a, onDone) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>Desbloquear a tela do PC</h2>
    <p class="confirm-text">Digite a senha <b>do painel</b> para confirmar. A tela do PC vai ser desbloqueada e quem estiver na frente dele terá acesso.</p>
    <form data-f style="display:grid;gap:8px;margin-top:12px">
      <input class="search" type="password" name="password" autocomplete="current-password" placeholder="Senha do painel" required style="flex:none;width:100%">
      <div class="sheet-actions" style="margin-top:4px">
        <button type="button" class="btn" data-no>Cancelar</button>
        <button type="submit" class="btn btn-primary">Desbloquear</button>
      </div>
    </form>`;
  const f = dlg.querySelector("[data-f]");
  dlg.querySelector("[data-no]").onclick = () => dlg.close();
  f.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = f.querySelector("[type=submit]");
    btn.disabled = true;
    try {
      const r = await a.api("/api/session/unlock", { method: "POST", body: { password: f.password.value } });
      toast(r.session?.locked ? "Pedido enviado, mas a tela ainda aparece bloqueada." : "Tela do PC desbloqueada.", !!r.session?.locked);
      dlg.close();
      onDone?.();
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
      f.password.select();
    }
  });
  dlg.showModal();
  f.password.focus();
}

const POWER = {
  poweroff: { verb: "Desligar", ing: "Desligando", done: "A máquina vai desligar. O painel vai parar de responder." },
  reboot: { verb: "Reiniciar", ing: "Reiniciando", done: "A máquina vai reiniciar. O painel volta sozinho quando ela ligar." },
};

function setupPower(el) {
  const card = $("[data-power]", el);
  setupTimer(card);
  app.api("/api/power").then(st => {
    if (st.poweroff !== "yes" && st.poweroff !== "unknown") {
      const w = $("[data-power-warn]", card);
      w.hidden = false;
      w.innerHTML = `${ic("warning")} O serviço ainda não tem permissão para desligar (o sistema pediria senha). Rode <code>sudo ./install.sh</code> na pasta do self-monitoring.`;
    }
  }).catch(() => {});
  card.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (b) confirmPower(b.dataset.act, $("[data-force]", card).checked);
  });
}

// ---------- timer ----------
let timerState = null, timerTick = null;

async function refreshTimer() {
  const box = $("[data-timer-body]", root);
  if (!box) return;
  try { timerState = (await app.api("/api/power/timer")).timer; } catch { return; }
  renderTimer();
}

function renderTimer() {
  const box = $("[data-timer-body]", root);
  if (!box) return;
  const t = timerState;
  if (t) {
    const left = Math.max(0, Math.round(t.at - Date.now() / 1000));
    const hh = Math.floor(left / 3600), mm = Math.floor((left % 3600) / 60), ss = left % 60;
    box.innerHTML = `<div class="timer-on"><div><div class="hero num" style="font-size:30px">${hh ? `${hh}:` : ""}${String(mm).padStart(hh ? 2 : 1, "0")}:${String(ss).padStart(2, "0")}</div>
        <div class="muted">${t.action === "poweroff" ? "para desligar" : "para reiniciar"} às ${new Date(t.at * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })} · forçado</div></div>
      <button class="btn" data-timer-cancel>Cancelar</button></div>`;
  } else {
    box.innerHTML = `<div class="timer-form">
        <select class="search" data-timer-action><option value="poweroff">Desligar</option><option value="reboot">Reiniciar</option></select>
        <span>em</span>
        <select class="search" data-timer-min>${[15, 30, 45, 60, 90, 120, 180, 240].map(m => `<option value="${m}" ${m === 60 ? "selected" : ""}>${m < 60 ? `${m} min` : `${m / 60} h${m % 60 ? "30" : ""}`}</option>`).join("")}<option value="custom">outro…</option></select>
        <button class="btn btn-primary" data-timer-start>Agendar</button></div>
      <p class="note">O timer sempre força o desligamento: nada impede (programas abertos, downloads). Aparece um aviso na tela do PC ao agendar, 5 min e 1 min antes.</p>`;
  }
}

function setupTimer(card) {
  card.addEventListener("click", async e => {
    if (e.target.closest("[data-timer-cancel]")) {
      try { await app.api("/api/power/timer", { method: "POST", body: { cancel: true } }); toast("Timer cancelado."); }
      catch (err) { toast(err.message, true); }
      return refreshTimer();
    }
    if (!e.target.closest("[data-timer-start]")) return;
    const action = $("[data-timer-action]", card).value;
    let minutes = $("[data-timer-min]", card).value;
    if (minutes === "custom") {
      minutes = prompt("Daqui a quantos minutos?", "60");
      if (!minutes) return;
    }
    try {
      timerState = (await app.api("/api/power/timer", { method: "POST", body: { action, minutes: Number(minutes) } })).timer;
      toast(`${action === "poweroff" ? "Desligamento" : "Reinício"} agendado.`);
      renderTimer();
    } catch (err) { toast(err.message, true); }
  });
}

// ---------- atualizações ----------
const SENS = { kernel: ["Kernel", "warning"], driver: ["Driver", "warning"], sistema: ["Sistema", ""] };

function setupUpdates(el) {
  const card = $("[data-updates]", el);
  card.querySelector("[data-upd-refresh]").addEventListener("click", async e => {
    e.target.disabled = true;
    try { renderUpdates(await app.api("/api/updates/refresh", { method: "POST", body: {} })); } catch (err) { toast(err.message, true); }
    pollUpdates();
  });
}

let updTimer = null;
async function pollUpdates() {
  clearTimeout(updTimer);
  let st;
  try { st = await app.api("/api/updates"); } catch { return; }
  renderUpdates(st);
  if (st.running) updTimer = setTimeout(pollUpdates, 2000);
}

function renderUpdates(st) {
  const card = $("[data-updates]", root);
  if (!card) return;
  card.querySelector("[data-upd-refresh]").disabled = st.running;
  const body = $("[data-upd-body]", card);
  const rb = st.reboot;
  const rebootHtml = rb ? `<p class="fan-alert warning" style="margin:0 0 8px">${ic("arrow-clockwise")} <b>O PC precisa reiniciar</b> para usar o que foi atualizado${
    rb.reason === "kernel" ? `: está rodando o kernel <span class="mono">${esc(rb.running)}</span>${rb.installed ? `, mas o instalado é <span class="mono">${esc(rb.installed)}</span>` : ", que já foi removido do disco"}.
      Até reiniciar, módulos novos (ex.: um pendrive ou driver carregado agora) podem não funcionar` : ""}.</p>` : "";
  if (st.count == null) { body.innerHTML = rebootHtml + (st.running ? "Verificando…" : "Ainda não verificado."); return; }
  const by = {};
  for (const i of st.items) (by[i.source] ||= []).push(i);
  const sens = st.items.filter(i => i.sensitive);
  body.innerHTML = `${rebootHtml}<p style="margin:0 0 8px">${st.count ? `<b>${st.count}</b> atualizações pendentes` : "Tudo atualizado"}
      · verificado ${new Date(st.checked * 1000).toLocaleString("pt-BR")}${st.running ? " · verificando…" : ""}</p>
    ${sens.length ? `<p class="fan-alert warning" style="margin:0 0 8px">Inclui pacotes sensíveis (${esc(sens.map(i => i.name).join(", "))}).
      Atualize <b>sentado na frente do PC</b> e reinicie em seguida.</p>` : ""}
    ${Object.entries(by).map(([src, items]) => `<details ${items.length <= 10 ? "open" : ""}><summary class="stat-label" style="cursor:pointer;margin:6px 0">${esc(src)} (${items.length})</summary>
      <table class="ptable upd"><tbody>${items.map(i => `<tr><td class="pname">${esc(i.name)} ${i.sensitive ? `<span class="chip ${SENS[i.sensitive][1]}">${SENS[i.sensitive][0]}</span>` : ""}</td>
        <td class="muted mono">${esc(i.current || "")}</td><td class="mono">→ ${esc(i.new || "")}</td></tr>`).join("")}</tbody></table></details>`).join("")}
    ${(st.errors || []).map(e => `<p class="muted">${esc(e)}</p>`).join("")}
    <p class="note">O painel só mostra as atualizações; para instalar, use o terminal ou a loja de apps no PC.</p>`;
}

function confirmPower(action, force) {
  const t = POWER[action], host = app.info.info.system?.hostname || "esta máquina";
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>${t.verb} ${esc(host)}?</h2>
    <p class="confirm-text">${force
      ? `<b>Modo forçado:</b> programas que estiverem bloqueando o desligamento serão ignorados e fechados. Trabalho não salvo pode ser perdido.`
      : `Os programas abertos serão avisados para fechar. Se algum bloquear (ex.: download em andamento), o pedido pode ser recusado; use "Forçar finalização".`}</p>
    <p class="confirm-text muted">Depois disso o painel fica inacessível até a máquina ${action === "reboot" ? "voltar" : "ser ligada de novo"}.</p>
    <div class="sheet-actions">
      <button class="btn" data-no>Cancelar</button>
      <button class="btn btn-danger" data-yes>${t.verb}${force ? " (forçar)" : ""}</button>
    </div>`;
  dlg.querySelector("[data-no]").onclick = () => dlg.close();
  dlg.querySelector("[data-yes]").onclick = async e => {
    e.target.disabled = true;
    try {
      await app.api("/api/power", { method: "POST", body: { action, force, confirm: true } });
      dlg.innerHTML = `<h2>${t.ing}…</h2><p class="confirm-text">${t.done}</p>
        <div class="sheet-actions"><button class="btn full" data-close>Fechar</button></div>`;
      dlg.querySelector("[data-close]").onclick = () => dlg.close();
    } catch (err) {
      toast(err.message, true);
      dlg.close();
    }
  };
  dlg.addEventListener("click", ev => { if (ev.target === dlg) dlg.close(); }, { once: true });
  dlg.showModal();
}

// ---------- quem está acessando ----------
let whoTimer = null;
const EVENTS = {
  login: ["Entrou", "good"], setup: ["Senha criada", "good"], logout: ["Saiu", ""],
  fail: ["Senha errada", "warning"], lockout: ["Bloqueado", "critical"], ended: ["Encerrou sessão", ""],
};

const ago = t => (Date.now() / 1000 - t < 60 ? "agora" : `há ${duration(Date.now() / 1000 - t).replace(/ \d+s$/, "")}`);
const when = t => new Date(t * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

function whereHtml(w, ip) {
  if (!w) return `<span class="mono">${esc(ip || "?")}</span>`;
  const extra = [w.host && `<b>${esc(w.host)}</b>`, esc(w.origin),
    w.mac && `MAC <span class="mono">${esc(w.mac)}</span>${w.mac_random ? " (aleatório)" : ""}`].filter(Boolean).join(" · ");
  const geo = w.kind === "internet" ? ` · <a href="https://ipinfo.io/${encodeURIComponent(w.ip)}" target="_blank" rel="noopener noreferrer">ver localização</a>` : "";
  return `<span class="mono">${esc(w.ip)}</span> · ${extra}${geo}`;
}

function setupWho(el) {
  const card = $("[data-who]", el);
  card.addEventListener("click", async e => {
    const v = e.target.closest("[data-who-audit]");
    if (v) {
      const sel = $("[data-audit-sess]", root);
      if (![...sel.options].some(o => o.value === v.dataset.whoAudit)) sel.add(new Option(v.dataset.label, v.dataset.whoAudit));
      sel.value = v.dataset.whoAudit;
      refreshAudit();
      return $("[data-audit]", root).scrollIntoView({ behavior: "smooth", block: "start" });
    }
    const b = e.target.closest("[data-who-end], [data-who-others]");
    if (!b) return;
    const others = b.hasAttribute("data-who-others");
    if (others && !confirm("Desconectar todos os outros aparelhos? Eles vão precisar da senha para entrar de novo.")) return;
    b.disabled = true;
    try {
      const r = await app.api("/api/auth/sessions/end", { method: "POST", body: others ? { others: true } : { id: b.dataset.whoEnd } });
      toast(r.ended ? (others ? `${r.ended} sessão(ões) encerrada(s).` : "Sessão encerrada.") : "Não havia outras sessões.");
    } catch (err) { toast(err.message, true); }
    refreshWho();
  });
}

async function refreshWho() {
  const card = $("[data-who]", root);
  let st;
  try { st = await app.api("/api/auth/sessions"); } catch { return; }
  const body = $("[data-who-body]", card);
  if (!st.enabled) { body.innerHTML = "A senha do painel está desligada na configuração (auth_enabled = false): qualquer um na rede entra sem login."; return; }
  const now = Date.now() / 1000;
  $("[data-who-others]", card).hidden = st.sessions.length < 2;
  body.innerHTML = `<div class="who-list">${st.sessions.map(s => `<div class="who-row">
      <div class="who-main">
        <div><b>${esc(s.device)}</b> ${s.current ? `<span class="chip good">este aparelho</span>` : ""}
          ${now - s.seen < 120 ? `<span class="chip good">ativo</span>` : `<span class="muted">visto ${ago(s.seen)}</span>`}</div>
        <div class="muted">${whereHtml(s.where, s.last_ip)}${s.ip !== s.last_ip ? ` · entrou por <span class="mono">${esc(s.ip)}</span>` : ""}</div>
        <div class="muted">Entrou em ${when(s.created)} · expira em ${when(s.expires)}</div>
      </div>
      <button class="btn btn-sm" data-who-audit="${esc(s.id)}" data-label="${esc(`${s.device} — ${s.last_ip}`)}">Ver ações</button>
      ${s.current ? "" : `<button class="btn btn-sm" data-who-end="${esc(s.id)}">Encerrar</button>`}
    </div>`).join("") || `<p class="muted">Nenhuma sessão ativa.</p>`}</div>
    <details style="margin-top:10px"><summary class="stat-label" style="cursor:pointer">Últimos acessos (${st.log.length})</summary>
      <div class="who-list">${st.log.map(e => `<div class="who-row"><div class="who-main">
        <div><span class="chip ${EVENTS[e.event]?.[1] || ""}">${esc(EVENTS[e.event]?.[0] || e.event)}</span> ${esc(e.device)} <span class="muted">· ${when(e.time)}</span></div>
        <div class="muted">${whereHtml(e.where, e.ip)}${e.target ? ` · ${esc(e.target)}` : ""}</div></div></div>`).join("") || `<p class="muted">Nada registrado ainda.</p>`}</div>
    </details>
    <p class="note">Todo login e todo bloqueio por senha errada aparecem como notificação na tela do PC. O nome vem do DNS da rede
      (nem todo aparelho responde) e o MAC da tabela ARP; celulares costumam usar MAC aleatório por rede.</p>`;
}

// ---------- ações feitas pelo painel ----------
let auditItems = [], auditMore = false;

function setupAudit(el) {
  const card = $("[data-audit]", el);
  card.addEventListener("change", e => { if (e.target.closest("select")) refreshAudit(); });
  card.addEventListener("click", e => { if (e.target.closest("[data-audit-more]")) refreshAudit(true); });
}

async function refreshAudit(more = false) {
  const card = $("[data-audit]", root);
  const cat = $("[data-audit-cat]", card), sess = $("[data-audit-sess]", card);
  const q = new URLSearchParams({ limit: "50" });
  if (cat.value) q.set("cat", cat.value);
  let st;
  try {
    if (sess.value === "me") q.set("session", (await app.api("/api/audit?limit=1")).current_session || "-");
    else if (sess.value) q.set("session", sess.value);
    if (more && auditItems.length) q.set("before", auditItems[auditItems.length - 1].seq);
    st = await app.api(`/api/audit?${q}`);
  } catch { return; }
  if (cat.options.length === 1) for (const [k, v] of Object.entries(st.categories)) cat.add(new Option(v, k));
  auditItems = more ? auditItems.concat(st.items) : st.items;
  auditMore = st.items.length === 50;
  $("[data-audit-body]", card).innerHTML = `<div class="who-list">${auditItems.map(e => `<div class="who-row"><div class="who-main">
      <div>${e.ok ? "" : `<span class="chip critical">Falhou</span> `}<b>${esc(e.text)}</b>${e.count > 1 ? ` <span class="muted">(${e.count} ajustes)</span>` : ""}</div>
      <div class="muted">${esc(st.categories[e.cat] || e.cat)} · ${when(e.time)} · ${esc(e.device)} · <span class="mono">${esc(e.ip)}</span>${e.session && e.session === st.current_session ? " · este aparelho" : ""}</div>
      ${e.error ? `<div class="muted">${esc(e.error)}</div>` : ""}</div></div>`).join("") || `<p class="muted">Nenhuma ação registrada${cat.value || sess.value ? " com esse filtro" : ""}.</p>`}</div>
    ${auditMore ? `<button class="btn btn-sm" data-audit-more style="margin-top:8px">Carregar mais</button>` : ""}
    <p class="note">Tudo o que muda algo no PC pelo painel fica registrado aqui (processos, energia, áudio, tela, mídia, apps, captura,
      configurações do painel), com quem fez e se deu certo. Ajustes seguidos do mesmo controle (ex.: arrastar o volume) aparecem como um só.</p>`;
}

function update(snap) {
  if (!root.classList.contains("active")) return;
  const s = snap.data.system || {}, c = snap.data.cpu || {}, g = snap.data.gpu?.gpus || [];
  const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
  $("[data-sys]", root).innerHTML = `<div class="card-head"><div class="card-title">Sistema</div></div><dl class="kv">
    ${row("Hostname", esc(s.hostname))}
    ${row("Sistema", esc(s.os))}
    ${row("Kernel", esc(s.kernel))}
    ${row("Arquitetura", esc(s.arch))}
    ${row("Placa", esc([s.board_vendor, s.board].filter(Boolean).join(" ")))}
    ${row("CPU", esc(`${c.model || s.cpu_model} · ${c.cores}C/${c.threads}T`))}
    ${g.map((x, i) => row(`GPU${g.length > 1 ? " " + (i + 1) : ""}`, esc(`${x.name} (${x.driver})`))).join("")}
    ${row("Ligado há", s.uptime ? duration(s.uptime) : "")}
    ${row("Boot", s.boot_time ? new Date(s.boot_time * 1000).toLocaleString("pt-BR") : "")}
    ${s.battery ? row("Bateria", `${s.battery.percent}% · ${esc(s.battery.status)}`) : ""}
  </dl>${s.is_bc250 ? `<p class="note">Hardware detectado: AMD BC-250 (APU Cyan Skillfish com 16 GB GDDR6 compartilhados entre CPU e GPU). O painel usa leituras adaptadas para essa placa.</p>` : ""}`;

  const i = app.info;
  $("[data-srv]", root).innerHTML = `<div class="card-head"><div class="card-title">Monitor</div></div><dl class="kv">
    ${row("Versão", `<a href="#update">${esc(i.version)}</a>`)}
    ${row("Amostragem", `${i.sample_interval}s`)}
    ${row("Processos", `a cada ${i.process_interval}s`)}
    ${row("Finalizar processos", i.features.kill ? "Ativado" : "Desativado")}
    ${row("Visibilidade GPU/proc.", snap.data.processes?.gpu_visibility === "all" ? "Todos (root)" : "Só do usuário do serviço")}
    ${row("Coletores", esc(i.collectors.join(", ")))}
  </dl>`;
}

export default {
  id: "system", title: "Sistema", icon: ic("cpu"), mount,
  show: () => {
    if (app.snapshot) update(app.snapshot);
    if (app.info.features.unlock) { refreshSession(); sessTimer = setInterval(refreshSession, 5000); }
    if (app.info.features.power) { refreshTimer(); timerTick = setInterval(() => (timerState ? renderTimer() : null), 1000); }
    if (app.info.features.updates) pollUpdates();
    refreshWho(); refreshAudit(); whoTimer = setInterval(() => { refreshWho(); if (!auditItems.length || auditItems.length <= 50) refreshAudit(); }, 10000);
  },
  hide: () => { clearInterval(whoTimer); whoTimer = null; clearInterval(sessTimer); sessTimer = null; clearInterval(timerTick); timerTick = null; clearTimeout(updTimer); },
};
