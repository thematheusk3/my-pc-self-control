// Aba Alertas: histórico, regras (liga/desliga + limite) e canais de envio.
import { ICON_NAMES, ic, noteIcon } from "../icons.js";
import { $, esc, toast } from "../ui.js";

let app, root, data = null, conf = null, hook = null, freshToken = null;
const LEVEL = { critical: ["critical", "Crítico"], warning: ["warning", "Atenção"], info: ["", "Info"] };

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card span-2" data-hist>
      <div class="card-head"><div class="card-title">Alertas</div>
        <button class="btn btn-sm" data-clear>Limpar</button></div>
      <div data-list>Carregando…</div>
    </article>
    <article class="card" data-channels>
      <div class="card-head"><div class="card-title">Onde avisar</div></div>
      <div data-ch-body></div>
    </article>
    <article class="card span-all" data-webhook></article>
    <article class="card span-all" data-rules>
      <div class="card-head"><div class="card-title">O que deve gerar alerta</div></div>
      <div data-rules-body></div>
    </article></div>`;
  $("[data-clear]", el).addEventListener("click", async () => {
    await app.api("/api/alerts/clear", { method: "POST", body: {} }).catch(() => {});
    load();
  });
  el.addEventListener("change", async e => {
    const t = e.target;
    if (t.dataset.rule) {
      const r = {};
      r[t.dataset.field] = t.type === "checkbox" ? t.checked : Number(t.value);
      await saveConf({ rules: { [t.dataset.rule]: r } });
    } else if (t.dataset.ch) {
      await saveConf({ channels: { [t.dataset.ch]: t.type === "checkbox" ? t.checked : t.value } });
    }
  });
  el.addEventListener("click", async e => {
    if (e.target.closest("[data-test]")) {
      try {
        const r = await app.api("/api/alerts/test", { method: "POST", body: {} });
        const parts = [];
        if ("pc" in r) parts.push(`PC: ${r.pc ? "enviado" : "falhou"}`);
        if ("ntfy" in r) parts.push(`ntfy: ${r.ntfy ? "enviado" : "falhou (" + r.ntfy_error + ")"}`);
        toast(parts.join(" · ") || "Nenhum canal extra ligado: os alertas aparecem só aqui no painel.", parts.some(p => p.includes("falhou")));
      } catch (err) { toast(err.message, true); }
    }
    if (e.target.closest("[data-hook-new]")) {
      if (hook.enabled && !confirm("Gerar um novo token desliga o atual. Os programas que usam o token atual vão parar de funcionar. Continuar?")) return;
      return hookAction("/api/webhook/token");
    }
    if (e.target.closest("[data-hook-test]")) return hookAction("/api/webhook/test", "Notificação de teste enviada.");
    if (e.target.closest("[data-hook-off]")) {
      if (!confirm("Desligar o webhook? Os programas que usam o token vão parar de funcionar.")) return;
      return hookAction("/api/webhook/revoke", "Webhook desligado.");
    }
    if (e.target.closest("[data-browser]")) {
      const p = await Notification.requestPermission();
      toast(p === "granted" ? "Notificações deste navegador ativadas." : "O navegador não permitiu.", p !== "granted");
      renderChannels();
    }
  });
}

// ---------- webhook ----------
function renderWebhook() {
  const token = freshToken, url = `${location.origin}/api/webhook/notify`;
  const s = hook, sample = `curl -X POST ${url} \\
  -H "Authorization: Bearer ${token || "SEU_TOKEN"}" \\
  -H "Content-Type: application/json" \\
  -d '{"title": "Backup concluído", "message": "Tudo certo.", "icon": "check", "source": "Meu app"}'`;
  $("[data-webhook]", root).innerHTML = `
    <div class="card-head"><div><div class="card-title">Notificações por webhook</div>
      <div class="card-sub">Outros programas enviam avisos para o painel.</div></div>
      ${s.enabled ? `<span class="chip good">Ligado</span>` : `<span class="chip">Desligado</span>`}</div>
    ${token ? `<div class="fan-alert warning">Copie o token agora: ele não será mostrado de novo.</div><div class="webhook-token">${esc(token)}</div>` : ""}
    ${s.enabled ? `<p class="note">Token terminado em <code>${esc(s.hint || "")}</code>. Recebidas: ${s.count}${s.last_used ? `. Última: ${new Date(s.last_used * 1000).toLocaleString("pt-BR")}` : ""}.</p>`
      : `<p class="note">Gere um token para ligar. Sem token, o painel recusa qualquer chamada.</p>`}
    <div class="note-actions">
      <button class="btn btn-sm" data-hook-new>${s.enabled ? "Gerar novo token" : "Gerar token"}</button>
      ${s.enabled ? `<button class="btn btn-sm" data-hook-test>Enviar teste</button><button class="btn btn-sm btn-ghost-danger" data-hook-off>Desligar</button>` : ""}
    </div>
    <details class="note"><summary>Como chamar</summary>
      <pre class="webhook-doc">${esc(sample)}</pre>
      <ul class="webhook-fields">
        <li><b>title</b>: título, obrigatório (até 120 caracteres)</li>
        <li><b>message</b>: texto (até 1000)</li>
        <li><b>icon</b>: um emoji ou nome de ícone: ${ICON_NAMES.map(esc).join(", ")}</li>
        <li><b>level</b>: info, warning ou critical (padrão info)</li>
        <li><b>source</b>: nome do programa que enviou</li>
        <li><b>url</b>: link aberto a partir do alerta</li>
        <li><b>display</b>: como aparece no Mural: auto (padrão, critical ocupa a tela), toast ou fullscreen</li>
        <li><b>duration</b>: segundos no Mural, de 3 a 300 (padrão: info 5, warning 8, critical 12, tela inteira 20)</li>
        <li><b>silent</b>: true para ficar só no painel e no Mural, sem tela do PC nem ntfy</li>
      </ul>
      <p>O painel usa HTTP na rede local: o token viaja sem criptografia. Chame de programas na sua rede ou por VPN.</p>
    </details>`;
}

async function hookAction(path, done) {
  try {
    const r = await app.api(path, { method: "POST", body: {} });
    freshToken = r.token || (path.endsWith("revoke") ? null : freshToken);   // some ao sair da aba
    if (done) toast(done);
    await load();
  } catch (err) { toast(err.message, true); }
}

async function saveConf(body) {
  try { conf = await app.api("/api/alerts/config", { method: "POST", body }); toast("Salvo."); }
  catch (err) { toast(err.message, true); }
  renderRules(); renderChannels();
}

async function load() {
  try {
    [data, conf, hook] = await Promise.all([app.api("/api/alerts"), app.api("/api/alerts/config"), app.api("/api/webhook")]);
  } catch (e) { $("[data-list]", root).textContent = e.message; return; }
  renderList(); renderRules(); renderChannels(); renderWebhook();
  if (data.unread) app.api("/api/alerts/read", { method: "POST", body: {} }).catch(() => {});
}

function renderList() {
  $("[data-list]", root).innerHTML = data.alerts.length ? `<ul class="alert-list">${data.alerts.slice(0, 100).map(a => {
    const [cls, lab] = LEVEL[a.level] || LEVEL.warning;
    return `<li class="${a.read ? "" : "unread"}"><span class="chip ${cls}">${lab}</span>
      <div><b>${a.rule === "webhook" ? `<span class="al-icon">${noteIcon(a.icon)}</span>` : ""}${esc(a.title)}</b><div class="muted">${esc(a.text)}</div>
        ${a.source ? `<span class="muted al-source">Enviado por ${esc(a.source)}</span>` : ""}
        ${/^https?:\/\//.test(a.url || "") ? `<a class="al-source" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">Abrir link</a>` : ""}</div>
      <span class="muted al-time">${new Date(a.time * 1000).toLocaleString("pt-BR")}</span></li>`;
  }).join("")}</ul>` : `<p class="muted">Nenhum alerta.</p>`;
}

function renderRules() {
  $("[data-rules-body]", root).innerHTML = `<div class="rules">${conf.rules.map(r => `<div class="rule">
      <label class="check"><input type="checkbox" data-rule="${r.id}" data-field="enabled" ${r.enabled ? "checked" : ""}> <b>${esc(r.label)}</b></label>
      ${r.unit ? `<label class="rule-val">limite <input class="search" type="number" min="1" max="1000" value="${r.value}" data-rule="${r.id}" data-field="value" ${r.enabled ? "" : "disabled"}> ${esc(r.unit)}</label>` : ""}
      <div class="muted rule-help">${esc(r.help)}</div></div>`).join("")}</div>`;
}

function renderChannels() {
  const ch = conf.channels;
  const secure = window.isSecureContext && "Notification" in window;
  $("[data-ch-body]", root).innerHTML = `
    <p class="check" style="margin:0 0 8px">${ic("check")} Aqui no painel (sempre)</p>
    <label class="check"><input type="checkbox" data-ch="pc" ${ch.pc ? "checked" : ""}> Notificação na tela do PC</label>
    <div style="margin-top:12px"><div class="stat-label">Push no celular (ntfy)</div>
      <input class="search" style="width:100%;margin-top:4px" type="url" placeholder="https://ntfy.sh/um-nome-dificil-de-adivinhar" value="${esc(ch.ntfy)}" data-ch="ntfy">
      <p class="note">Instale o app <b>ntfy</b> no celular, assine o mesmo tópico e cole o endereço aqui. No ntfy.sh público, quem souber o nome do tópico lê as mensagens: use um nome longo e aleatório.</p></div>
    <div style="margin-top:4px">${secure
      ? (Notification.permission === "granted" ? `<p class="check">${ic("check")} Notificações deste navegador (com o painel aberto)</p>`
        : `<button class="btn btn-sm" data-browser>Ativar notificações neste navegador</button>`)
      : `<p class="note">Notificações do navegador só funcionam em HTTPS (ou localhost); pelo IP da rede local use o ntfy.</p>`}</div>
    <button class="btn" style="width:100%;margin-top:10px" data-test>Enviar alerta de teste</button>`;
}

export default {
  id: "alerts", title: "Alertas", icon: ic("bell"), mount,
  enabled: a => !!a.info?.features?.alerts,
  show: load,
  hide: () => { freshToken = null; },
};
