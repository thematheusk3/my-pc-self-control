// Aparência do painel: claro ou escuro, cor e estilo. As cores são as de máscara de solda de placas reais, com contatos
// dourados ou prateados (tokens em app.css); os estilos mudam a forma do painel (styles.css). Fica salvo em cada aparelho.
import { $, toast } from "./ui.js";
import { thumb } from "./mural-backdrop.js";

export const PALETTES = [
  { id: "verde", name: "Verde", deep: "#0d1f1a", mid: "#1c3b32", accent: "#e8be57", glow: ["#5fe3b4", "#3fa7d6", "#e8be57"] },
  { id: "azul", name: "Azul", deep: "#0c1a2b", mid: "#1b3552", accent: "#e8be57", glow: ["#5aa9ff", "#9a7bff", "#5fe3e3"] },
  { id: "vermelha", name: "Vermelha", deep: "#230f12", mid: "#452022", accent: "#e8be57", glow: ["#ff6b5e", "#ff9f5a", "#d94f8a"] },
  { id: "roxa", name: "Roxa", deep: "#1a1228", mid: "#33264d", accent: "#e8be57", glow: ["#b388ff", "#ff7ac6", "#6f8bff"] },
  { id: "preta", name: "Preta", deep: "#121416", mid: "#272b2f", accent: "#d7dde2", glow: ["#8fa3b3", "#d7dde2", "#5f7485"] },
  { id: "amarela", name: "Amarela", deep: "#211b0a", mid: "#413618", accent: "#ffd84d", glow: ["#ffd84d", "#ff9f45", "#e0653a"] },
];
const MODES = [["", "Automático"], ["dark", "Escuro"], ["light", "Claro"]];
const STYLES = [["placa", "Placa"], ["vidro", "Vidro"], ["osc", "Osciloscópio"]];

const root = document.documentElement;
export const currentPalette = () => PALETTES.find(p => p.id === root.dataset.palette) || PALETTES[0];

function syncBrowserColor() {
  $('meta[name="theme-color"]').content = getComputedStyle(document.body).backgroundColor;
}

function setPalette(id) {
  if (id === PALETTES[0].id) { delete root.dataset.palette; localStorage.removeItem("palette"); }
  else { root.dataset.palette = id; localStorage.setItem("palette", id); }
}

function setStyle(id) {
  if (id === STYLES[0][0]) { delete root.dataset.style; localStorage.removeItem("style"); }
  else { root.dataset.style = id; localStorage.setItem("style", id); }
}

/** Fundo do estilo Vidro: um quadro só, sem animação, porque o painel fica aberto e no celular gastaria bateria. */
function paintStyleBackground() {
  let c = $("#style-bg");
  if (root.dataset.style !== "vidro") { c?.remove(); return; }
  if (!c) {
    c = document.createElement("canvas");
    c.id = "style-bg";
    c.setAttribute("aria-hidden", "true");
    document.body.prepend(c);
  }
  const p = currentPalette(), css = getComputedStyle(root), dark = css.colorScheme.includes("dark");
  if (dark) {               // baixa resolução: ampliada, a aurora fica macia
    c.width = 480; c.height = 270;
    thumb(c, "aurora", { palette: { deep: p.deep, mid: p.mid, accent: p.accent }, bands: p.glow });
  } else {                  // no tema claro a luz da aurora não aparece: uma placa clara, com as trilhas
    c.width = Math.min(innerWidth, 1920); c.height = Math.round(c.width * innerHeight / innerWidth);
    thumb(c, "board", { palette: { deep: css.getPropertyValue("--bg").trim(), mid: css.getPropertyValue("--surface-2").trim(), accent: css.getPropertyValue("--accent").trim() }, bare: true });
  }
}

function applyLook() {
  paintStyleBackground();
  syncBrowserColor();
}

function setMode(mode) {
  if (mode) { root.dataset.theme = mode; localStorage.setItem("theme", mode); }
  else { delete root.dataset.theme; localStorage.removeItem("theme"); }
}

export function openAppearance(app) {
  const sheet = $("#sheet");
  const render = () => {
    const cur = currentPalette().id, mode = root.dataset.theme || "", style = root.dataset.style || STYLES[0][0];
    sheet.innerHTML = `<h2>Aparência</h2>
      <p class="confirm-text">Vale para todas as abas e fica salvo neste aparelho.</p>
      <p class="appearance-label">Estilo</p>
      <div class="look-tiles">${STYLES.map(([id, l]) => `<button class="look-tile" data-appearance-style="${id}" aria-pressed="${id === style}">
        <span class="look-demo ${id}" aria-hidden="true"></span>${l}</button>`).join("")}</div>
      <p class="appearance-label">Tema</p>
      <div class="seg appearance-modes">${MODES.map(([id, l]) => `<button data-appearance-mode="${id}" aria-pressed="${id === mode}">${l}</button>`).join("")}</div>
      <p class="appearance-label">Cor</p>
      <div class="swatches">${PALETTES.map(p => `<button class="swatch" data-appearance-palette="${p.id}" aria-pressed="${p.id === cur}" style="--mask:${p.mid};--pad:${p.accent}">
        <span class="swatch-board" aria-hidden="true"></span>${p.name}</button>`).join("")}</div>
      <div class="sheet-actions"><button class="btn full" data-appearance-close>Fechar</button></div>`;
  };
  render();
  sheet.onclick = e => {
    if (e.target.closest("[data-appearance-close]")) return sheet.close();
    const mode = e.target.closest("[data-appearance-mode]"), pal = e.target.closest("[data-appearance-palette]");
    const look = e.target.closest("[data-appearance-style]");
    if (!mode && !pal && !look) return;
    if (mode) setMode(mode.dataset.appearanceMode);
    else if (pal) setPalette(pal.dataset.appearancePalette);
    else setStyle(look.dataset.appearanceStyle);
    render();
    applyLook();
    if (app.snapshot) app.pushSnapshot(app.snapshot);   // redesenha os gráficos com as cores novas
  };
  sheet.addEventListener("close", () => { sheet.onclick = null; }, { once: true });
  sheet.showModal();
}

export function setupAppearance(app) {
  $("#theme-btn").addEventListener("click", () => {
    try { openAppearance(app); } catch (e) { toast(e.message, true); }
  });
  applyLook();
  let wait;
  addEventListener("resize", () => { clearTimeout(wait); wait = setTimeout(paintStyleBackground, 300); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyLook);
}
