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
    ${app.info.features.unlock ? `<article class="card" data-session>
      <div class="card-head"><div class="card-title">Sessão</div><div data-sess-chip></div></div>
      <div data-sess-body class="note" style="margin-top:0">Carregando…</div>
    </article>` : ""}
    ${app.info.features.power ? `<article class="card" data-power>
      <div class="card-head"><div class="card-title">Energia</div></div>
      <div class="power-btns">
        <button class="btn" data-act="reboot">↻ Reiniciar</button>
        <button class="btn btn-danger" data-act="poweroff">⏻ Desligar</button>
      </div>
      <label class="check" style="margin-top:12px"><input type="checkbox" data-force> Forçar finalização</label>
      <p class="note" style="margin-top:6px">Forçar ignora programas que estão bloqueando o desligamento (downloads, jogos, "deseja salvar?")
        e outros usuários logados. Arquivos não salvos podem ser perdidos.</p>
      <p class="note" data-power-warn hidden></p>
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
  if (app.info.features.unlock) setupSession(el);
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
        <button class="btn" data-sess="lock" ${s.locked ? "disabled" : ""}>🔒 Bloquear</button>
        <button class="btn btn-primary" data-sess="unlock" ${s.locked ? "" : "disabled"}>🔓 Desbloquear</button>
      </div>`
    : `<p class="confirm-text" style="margin-top:0">Ninguém está logado: o PC está na tela de login, que não pode ser controlada pelo painel.</p>`) +
    `<p class="note">${auto
      ? `✔ <b>Entrar pelo celular ativado</b>: ao ligar, o PC entra sozinho em <b>${esc(st.user)}</b> com a tela bloqueada; desbloqueie por aqui.`
      : `Para entrar pelo celular ao ligar o PC, ative o login automático com a tela já bloqueada. Na máquina, rode:<br>
         <code>sudo /opt/applications/self-monitoring/tools/login-pelo-celular.sh ativar</code>
         ${st.autologin_me && !st.lock_on_start ? `<br><b>⚠ O login automático está ligado SEM bloqueio ao iniciar: qualquer um que ligar o PC entra na sua conta.</b>` : ""}`}</p>`;
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
  app.api("/api/power").then(st => {
    if (st.poweroff !== "yes" && st.poweroff !== "unknown") {
      const w = $("[data-power-warn]", card);
      w.hidden = false;
      w.innerHTML = `⚠ O serviço ainda não tem permissão para desligar (o sistema pediria senha). Rode <code>sudo ./install.sh</code> na pasta do self-monitoring.`;
    }
  }).catch(() => {});
  card.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (b) confirmPower(b.dataset.act, $("[data-force]", card).checked);
  });
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
    ${row("Versão", esc(i.version))}
    ${row("Amostragem", `${i.sample_interval}s`)}
    ${row("Processos", `a cada ${i.process_interval}s`)}
    ${row("Finalizar processos", i.features.kill ? "Ativado" : "Desativado")}
    ${row("Visibilidade GPU/proc.", snap.data.processes?.gpu_visibility === "all" ? "Todos (root)" : "Só do usuário do serviço")}
    ${row("Coletores", esc(i.collectors.join(", ")))}
  </dl>`;
}

export default {
  id: "system", title: "Sistema", icon: "ⓘ", mount,
  show: () => {
    if (app.snapshot) update(app.snapshot);
    if (app.info.features.unlock) { refreshSession(); sessTimer = setInterval(refreshSession, 5000); }
  },
  hide: () => { clearInterval(sessTimer); sessTimer = null; },
};
