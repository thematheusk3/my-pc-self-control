// Aba Apps: abre pelo celular os aplicativos instalados (lidos dos arquivos .desktop).
import { ic } from "../icons.js";
import { $, esc, toast } from "../ui.js";
import { openUnlock } from "./system.js";

let app, root, data = null, q = "", cat = "", loading = false;

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div data-banner></div>
    <div class="toolbar">
      <input class="search" type="search" placeholder="Buscar aplicativo…" aria-label="Buscar aplicativo" data-q>
    </div>
    <div class="seg app-cats" data-cats></div>
    <div class="app-grid" data-grid><div class="card empty span-all">Carregando…</div></div>
    <p class="note">Toque para abrir na tela do PC. Os apps abrem na sessão do usuário logado; se a tela estiver bloqueada,
      eles abrem por trás do bloqueio.</p>`;
  $("[data-q]", el).addEventListener("input", e => { q = e.target.value.toLowerCase(); render(); });
  $("[data-cats]", el).addEventListener("click", e => {
    const b = e.target.closest("[data-c]");
    if (b) { cat = b.dataset.c; render(); }
  });
  $("[data-grid]", el).addEventListener("click", async e => {
    const t = e.target.closest("[data-id]");
    if (!t || t.classList.contains("busy")) return;
    t.classList.add("busy");
    try {
      const r = await app.api(`/api/apps/${encodeURIComponent(t.dataset.id)}/launch`, { method: "POST", body: {} });
      toast(r.locked ? `Abrindo ${r.name}… (a tela do PC está bloqueada)` : `Abrindo ${r.name}…`);
    } catch (err) { toast(err.message, true); }
    setTimeout(() => t.classList.remove("busy"), 1500);
  });
  // ícone que não carregou vira a inicial do nome
  $("[data-grid]", el).addEventListener("error", e => {
    const img = e.target;
    if (img.tagName !== "IMG") return;
    const sp = document.createElement("span");
    sp.className = "app-noicon";
    sp.textContent = img.dataset.letter;
    img.replaceWith(sp);
  }, true);
  $("[data-banner]", el).addEventListener("click", e => {
    if (e.target.closest("[data-unlock]")) openUnlock(app, load);
  });
}

async function load() {
  if (loading) return;
  loading = true;
  try { data = await app.api("/api/apps"); render(); }
  catch (e) { $("[data-grid]", root).innerHTML = `<div class="card empty span-all">${esc(e.message)}</div>`; }
  finally { loading = false; }
}

function render() {
  if (!data) return;
  const s = data.session;
  $("[data-banner]", root).innerHTML = !s
    ? `<div class="fan-alert critical">Ninguém está logado no PC (ele está na tela de login). Os apps só abrem dentro de uma sessão.
        Veja <a href="#system">Sistema &gt; Sessão</a> para ativar o login automático com a tela bloqueada.</div>`
    : s.locked ? `<div class="fan-alert warning">A tela do PC está bloqueada. <button class="btn btn-sm" data-unlock>Desbloquear</button></div>` : "";

  const cats = [...new Set(data.apps.map(a => a.category))].sort();
  $("[data-cats]", root).innerHTML = [["", "Todos"], ...cats.map(c => [c, c])]
    .map(([v, l]) => `<button data-c="${esc(v)}" aria-pressed="${v === cat}">${esc(l)}</button>`).join("");
  const list = data.apps.filter(a => (!cat || a.category === cat) &&
    (!q || [a.name, a.generic, a.comment, a.keywords, a.id].some(v => (v || "").toLowerCase().includes(q))));
  $("[data-grid]", root).innerHTML = list.map(a => `<button class="app-tile" data-id="${esc(a.id)}" title="${esc(a.comment || a.generic || a.name)}">
      ${a.has_icon ? `<img src="/api/apps/${encodeURIComponent(a.id)}/icon" alt="" loading="lazy" data-letter="${esc(a.name[0] || "?")}">`
        : `<span class="app-noicon">${esc(a.name[0] || "?")}</span>`}
      <span class="app-name">${esc(a.name)}</span>
    </button>`).join("") || `<div class="card empty span-all">Nenhum aplicativo encontrado.</div>`;
}

export default {
  id: "apps", title: "Apps", icon: ic("grid-nine"), mount,
  enabled: a => !!a.info?.features?.launch,
  show: load,
};
