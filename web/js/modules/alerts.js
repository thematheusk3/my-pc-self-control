// Aba Alertas: histórico, regras (liga/desliga + limite) e canais de envio.
import { $, esc, toast } from "../ui.js";

let app, root, data = null, conf = null;
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
    if (e.target.closest("[data-browser]")) {
      const p = await Notification.requestPermission();
      toast(p === "granted" ? "Notificações deste navegador ativadas." : "O navegador não permitiu.", p !== "granted");
      renderChannels();
    }
  });
}

async function saveConf(body) {
  try { conf = await app.api("/api/alerts/config", { method: "POST", body }); toast("Salvo."); }
  catch (err) { toast(err.message, true); }
  renderRules(); renderChannels();
}

async function load() {
  try {
    [data, conf] = await Promise.all([app.api("/api/alerts"), app.api("/api/alerts/config")]);
  } catch (e) { $("[data-list]", root).textContent = e.message; return; }
  renderList(); renderRules(); renderChannels();
  if (data.unread) app.api("/api/alerts/read", { method: "POST", body: {} }).catch(() => {});
}

function renderList() {
  $("[data-list]", root).innerHTML = data.alerts.length ? `<ul class="alert-list">${data.alerts.slice(0, 100).map(a => {
    const [cls, lab] = LEVEL[a.level] || LEVEL.warning;
    return `<li class="${a.read ? "" : "unread"}"><span class="chip ${cls}">${lab}</span>
      <div><b>${esc(a.title)}</b><div class="muted">${esc(a.text)}</div></div>
      <span class="muted al-time">${new Date(a.time * 1000).toLocaleString("pt-BR")}</span></li>`;
  }).join("")}</ul>` : `<p class="muted">Nenhum alerta. 👍</p>`;
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
    <p class="check" style="margin:0 0 8px">✔ Aqui no painel (sempre)</p>
    <label class="check"><input type="checkbox" data-ch="pc" ${ch.pc ? "checked" : ""}> Notificação na tela do PC</label>
    <div style="margin-top:12px"><div class="stat-label">Push no celular (ntfy)</div>
      <input class="search" style="width:100%;margin-top:4px" type="url" placeholder="https://ntfy.sh/um-nome-dificil-de-adivinhar" value="${esc(ch.ntfy)}" data-ch="ntfy">
      <p class="note">Instale o app <b>ntfy</b> no celular, assine o mesmo tópico e cole o endereço aqui. No ntfy.sh público, quem souber o nome do tópico lê as mensagens: use um nome longo e aleatório.</p></div>
    <div style="margin-top:4px">${secure
      ? (Notification.permission === "granted" ? `<p class="check">✔ Notificações deste navegador (com o painel aberto)</p>`
        : `<button class="btn btn-sm" data-browser>Ativar notificações neste navegador</button>`)
      : `<p class="note">Notificações do navegador só funcionam em HTTPS (ou localhost); pelo IP da rede local use o ntfy.</p>`}</div>
    <button class="btn" style="width:100%;margin-top:10px" data-test>Enviar alerta de teste</button>`;
}

export default {
  id: "alerts", title: "Alertas", icon: "🔔", mount,
  enabled: a => !!a.info?.features?.alerts,
  show: load,
};
