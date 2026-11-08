// Aba Atualização: versão instalada × GitHub, changelog e o aviso na navegação.
// Serve igual no Windows e no Linux: repositório e comandos vêm do backend (sm/selfupdate.py).
import { $, esc, toast } from "../ui.js";
import { ic } from "../icons.js";

const POLL = 30 * 60 * 1000;
let app, root, data = null;

const HEAD = {
  update: v => `Nova versão disponível: ${esc(v)}`,
  current: () => "Você está na versão mais recente",
  ahead: () => "Esta versão é mais nova que a do GitHub",
  off: () => "Verificação de versão desligada",
  unknown: () => "Ainda sem resposta do GitHub",
};

// O changelog vem da internet: escapa tudo antes e só cria links https.
function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, `<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>`)
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
}

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card span-2" data-status>Carregando…</article>
    <article class="card" data-steps></article>
    <article class="card span-all"><div class="card-head"><div class="card-title">O que mudou</div>
      <a class="btn btn-sm" data-log-link target="_blank" rel="noopener noreferrer">Ver no GitHub</a></div>
      <div data-log></div></article>
  </div>`;
  el.addEventListener("click", async e => {
    const b = e.target.closest("[data-check]");
    if (!b) return;
    b.disabled = true;
    try {
      data = await app.api("/api/panel-update/check", { method: "POST", body: {} });
      paint();
      if (data.error) toast(data.error, true);
      else toast(data.state === "update" ? "Há uma versão nova." : "Verificado: nenhuma versão nova.");
    } catch (err) { toast(err.message, true); b.disabled = false; }
  });
  poll();
  setInterval(poll, POLL);
}

async function poll() {
  try {
    data = await app.api("/api/panel-update");
    paint();
    if (data.checking) setTimeout(poll, 3000);   // o GitHub ainda vai responder
  } catch { /* conexão caiu; tenta no próximo ciclo */ }
}

function flagNav() {
  const on = data.state === "update";
  const tab = $('.tab[data-id="update"]'), more = $(".tab-more");
  if (tab && !!$(".tab-flag", tab) !== on) {
    if (on) tab.insertAdjacentHTML("beforeend", `<span class="tab-flag">Nova</span>`);
    else $(".tab-flag", tab).remove();
  }
  if (more) {
    more.classList.toggle("has-update", on);
    if (on) more.setAttribute("aria-label", "Mais, nova versão disponível");
    else more.removeAttribute("aria-label");
  }
}

function paint() {
  if (!data) return;
  flagNav();
  const when = data.checked ? new Date(data.checked * 1000).toLocaleString("pt-BR") : "Ainda não verificado";
  const chip = { update: ["warning", "Atualização disponível"], current: ["good", "Atualizado"] }[data.state];
  $("[data-status]", root).innerHTML = `
    <div class="card-head"><div class="card-title">Atualização do painel</div>${chip ? `<span class="chip ${chip[0]}">${chip[1]}</span>` : ""}</div>
    <p class="update-head">${HEAD[data.state]?.(data.latest) ?? ""}</p>
    ${data.error ? `<div class="fan-alert warning">${esc(data.error)}</div>` : ""}
    <dl class="kv">
      <dt>Instalada</dt><dd>${esc(data.installed)}</dd>
      <dt>No GitHub</dt><dd>${esc(data.latest || "—")}</dd>
      <dt>Última verificação</dt><dd>${esc(when)}</dd>
    </dl>
    <div class="update-actions">
      <button class="btn" data-check ${data.enabled ? "" : "disabled"}>${ic("arrow-clockwise")} Verificar agora</button>
      <a class="btn" href="${esc(data.repo_url)}" target="_blank" rel="noopener noreferrer">Abrir repositório</a>
    </div>
    ${data.enabled ? "" : `<p class="note">Para ligar, use <code>check = true</code> em <code>[update]</code> no config.toml.</p>`}`;

  $("[data-steps]", root).innerHTML = `
    <div class="card-head"><div class="card-title">Como atualizar</div></div>
    <pre class="update-cmd">${data.steps.map(esc).join("\n")}</pre>
    <p class="note">${esc(data.note)} <a href="${esc(data.guide_url)}" target="_blank" rel="noopener noreferrer">Guia completo</a></p>`;

  $("[data-log-link]", root).href = data.changelog_url;
  $("[data-log]", root).innerHTML = data.changelog.map(e => `
    <details class="release" ${e.new || e.installed ? "open" : ""}>
      <summary><span class="release-v">${esc(e.version)}</span>${e.date ? ` <span class="release-d">${esc(e.date.split("-").reverse().join("/"))}</span>` : ""}
        ${e.new ? `<span class="chip warning">Nova</span>` : e.installed ? `<span class="chip good">Instalada</span>` : ""}</summary>
      <ul>${e.items.map(i => `<li>${inline(i)}</li>`).join("")}</ul>
    </details>`).join("") || `<p class="muted">Changelog indisponível. Veja no GitHub.</p>`;
}

export default { id: "update", title: "Atualização", icon: ic("arrow-circle-up"), mount, show: poll };
