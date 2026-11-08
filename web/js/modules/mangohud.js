// Aba MangoHud: ajusta o MangoHud.conf (o mesmo do GOverlay) e o HUD do jogo aberto muda na hora.
import { ic } from "../icons.js";
import { $, $$, esc, toast } from "../ui.js";

let app, root, st = null, file = null, busy = 0, undo = [], editorDirty = false;
const pending = {};         // alterações aguardando o debounce dos sliders
let timer = null;

const POSITIONS = [
  ["top-left", "↖"], ["top-center", "↑"], ["top-right", "↗"],
  ["middle-left", "←"], [null, ""], ["middle-right", "→"],
  ["bottom-left", "↙"], ["bottom-center", "↓"], ["bottom-right", "↘"],
];
const POS_NAMES = { "top-left": "Topo à esquerda", "top-center": "Topo ao centro", "top-right": "Topo à direita",
  "middle-left": "Meio à esquerda", "middle-right": "Meio à direita", "bottom-left": "Embaixo à esquerda",
  "bottom-center": "Embaixo ao centro", "bottom-right": "Embaixo à direita" };

// [chave, rótulo, mín, máx, passo, padrão]
const SLIDERS = [
  ["font_size", "Tamanho da fonte", 10, 48, 1, 24],
  ["background_alpha", "Opacidade do fundo", 0, 1, 0.05, 0.5],
  ["alpha", "Opacidade do HUD", 0.1, 1, 0.05, 1],
  ["round_corners", "Cantos arredondados", 0, 20, 1, 0],
  ["table_columns", "Colunas", 1, 6, 1, 3],
];
const COLORS = [["text_color", "Texto", "FFFFFF"], ["background_color", "Fundo", "020202"],
  ["gpu_color", "GPU", "2E9762"], ["cpu_color", "CPU", "2E97CB"], ["frametime_color", "Gráfico", "00FF00"]];
const STYLE_FLAGS = [["horizontal", "Horizontal (uma linha)"], ["hud_compact", "Compacto"], ["hud_no_margin", "Sem margem"]];

const GROUPS = [
  ["Desempenho", [["fps", "FPS"], ["frame_timing", "Gráfico de frametime"], ["frametime", "Frametime (ms)"],
    ["fps_metrics", "1% / 0,1% low", "avg,0.01,0.001"], ["show_fps_limit", "Limite de FPS"], ["frame_count", "Contador de quadros"]]],
  ["GPU", [["gpu_stats", "Uso da GPU"], ["gpu_temp", "Temperatura"], ["gpu_junction_temp", "Temp. junção"],
    ["gpu_mem_temp", "Temp. memória"], ["gpu_core_clock", "Clock do núcleo"], ["gpu_mem_clock", "Clock da memória"],
    ["gpu_power", "Consumo (W)"], ["gpu_fan", "Ventoinha"], ["vram", "VRAM"], ["gpu_name", "Nome da GPU"],
    ["throttling_status", "Throttling"]]],
  ["CPU", [["cpu_stats", "Uso da CPU"], ["cpu_temp", "Temperatura"], ["cpu_power", "Consumo (W)"],
    ["cpu_mhz", "Clock"], ["core_load", "Uso por núcleo"]]],
  ["Memória e disco", [["ram", "RAM"], ["swap", "Swap"], ["procmem", "Memória do jogo"], ["io_read", "Leitura de disco"], ["io_write", "Escrita de disco"]]],
  ["Sistema", [["time", "Relógio"], ["resolution", "Resolução"], ["engine_version", "Versão do Vulkan/OpenGL"],
    ["vulkan_driver", "Driver"], ["wine", "Versão do Wine/Proton"], ["arch", "Arquitetura"], ["gamemode", "GameMode"],
    ["vkbasalt", "vkBasalt"], ["fsr", "FSR"], ["hdr", "HDR"], ["battery", "Bateria"], ["version", "Versão do MangoHud"]]],
];
const DEFAULT_ON = new Set(["fps", "frametime", "frame_timing", "cpu_stats", "gpu_stats"]);
const VSYNC = [["", "Padrão do jogo"], ["0", "Adaptativo"], ["1", "Desligado"], ["2", "Mailbox"], ["3", "Ligado"]];

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card span-all" id="mh-top">
      <div class="card-head"><div><div class="card-title">MangoHud</div><div class="card-sub" data-sub>Carregando…</div></div>
        <div class="toolbar mh-actions" style="margin:0">
          <button class="btn btn-sm" data-act="undo" disabled title="Volta a última alteração">${ic("arrow-clockwise")} Desfazer</button>
          <button class="btn btn-sm" data-act="goverlay" hidden>Abrir GOverlay no PC</button>
        </div></div>
      <div class="mh-row">
        <label class="mh-file"><span class="stat-label">Arquivo</span><select class="search" data-file></select></label>
        <label class="check mh-big"><input type="checkbox" data-hide> Esconder o HUD</label>
        <span class="mh-saved" data-saved aria-live="polite"></span>
      </div>
      <div data-game></div>
      <p class="note">As mudanças vão direto para o arquivo e o MangoHud recarrega sozinho: o HUD do jogo aberto muda em
        um ou dois segundos. Se não mudar, aperte <b>Shift esquerdo + F4</b> no jogo. O GOverlay usa o mesmo arquivo — se você
        salvar algo lá depois, ele sobrescreve o que foi feito aqui.</p>
    </article>

    <article class="card">
      <div class="card-head"><div class="card-title">Posição</div></div>
      <div class="mh-pos" data-pos role="group" aria-label="Posição do HUD">
        ${POSITIONS.map(([k, s]) => k ? `<button data-p="${k}" title="${POS_NAMES[k]}" aria-label="${POS_NAMES[k]}" aria-pressed="false">${s}</button>` : `<span></span>`).join("")}
      </div>
      <div class="mh-flags">${STYLE_FLAGS.map(([k, l]) => `<label class="check"><input type="checkbox" data-flag="${k}"> ${l}</label>`).join("")}</div>
    </article>

    <article class="card">
      <div class="card-head"><div class="card-title">Aparência</div></div>
      ${SLIDERS.map(([k, l, mn, mx, stp]) => `<div class="mh-slider">
        <div class="lbl"><span>${l}</span><span class="num" data-sv="${k}">—</span></div>
        <input type="range" min="${mn}" max="${mx}" step="${stp}" data-slider="${k}" aria-label="${l}"></div>`).join("")}
      <div class="mh-colors">${COLORS.map(([k, l]) => `<label class="mh-color"><input type="color" data-color="${k}"><span>${l}</span></label>`).join("")}</div>
    </article>

    <article class="card">
      <div class="card-head"><div class="card-title">Limite de FPS</div></div>
      <div class="mh-field"><span class="stat-label">Limites (o atalho alterna entre eles; 0 = sem limite)</span>
        <input class="search" data-text="fps_limit" placeholder="ex.: 0,60,144" inputmode="numeric"></div>
      <div class="mh-field"><span class="stat-label">Método</span>
        <div class="seg" role="group" data-seg="fps_limit_method">
          <button data-v="late" aria-pressed="false" title="Menos atraso de entrada">Late</button>
          <button data-v="early" aria-pressed="false" title="Frametime mais estável">Early</button></div></div>
      <div class="mh-field"><span class="stat-label">VSync (Vulkan)</span>
        <select class="search" data-select="vsync">${VSYNC.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
      <div class="mh-field"><span class="stat-label">Atalho para mostrar/esconder o HUD</span>
        <input class="search" data-text="toggle_hud" placeholder="Shift_R+F12"></div>
      <div class="mh-field"><span class="stat-label">Atalho para trocar o limite de FPS</span>
        <input class="search" data-text="toggle_fps_limit" placeholder="Shift_L+F1"></div>
    </article>

    <article class="card span-all">
      <div class="card-head"><div><div class="card-title">O que aparece no HUD</div>
        <div class="card-sub" data-order-note></div></div></div>
      <div class="mh-groups" data-groups>
        ${GROUPS.map(([g, items]) => `<fieldset class="mh-group"><legend>${g}</legend>
          ${items.map(([k, l, v]) => `<label class="check"><input type="checkbox" data-metric="${k}" ${v ? `data-on="${v}"` : ""}> ${l}</label>`).join("")}
        </fieldset>`).join("")}
      </div>
    </article>

    <article class="card span-all">
      <div class="card-head"><div><div class="card-title">Arquivo completo</div>
        <div class="card-sub">Para opções que não estão acima. Linhas <code>exec</code> não podem ser alteradas por aqui.</div></div>
        <div class="toolbar" style="margin:0">
          <button class="btn btn-sm" data-act="reload-text" disabled>Descartar</button>
          <button class="btn btn-sm btn-primary" data-act="save-text" disabled>Salvar e aplicar</button></div></div>
      <textarea class="mh-text" data-textarea spellcheck="false" autocapitalize="off" autocomplete="off" rows="16" aria-label="Conteúdo do arquivo"></textarea>
    </article>
  </div>`;

  $("[data-file]", el).addEventListener("change", e => { file = e.target.value; undo = []; load(); });
  $("[data-hide]", el).addEventListener("change", e => set({ no_display: e.target.checked }));
  $("[data-pos]", el).addEventListener("click", e => {
    const b = e.target.closest("[data-p]");
    if (b) set({ position: b.dataset.p });
  });
  el.addEventListener("change", e => {
    const t = e.target;
    if (t.dataset.flag) set({ [t.dataset.flag]: t.checked });
    else if (t.dataset.metric) set({ [t.dataset.metric]: t.checked ? (t.dataset.on || true) : false });
    else if (t.dataset.text !== undefined) set({ [t.dataset.text]: t.value.trim() || null });
    else if (t.dataset.select !== undefined) set({ [t.dataset.select]: t.value || null });
    else if (t.dataset.color) set({ [t.dataset.color]: t.value.slice(1).toUpperCase() });
  });
  el.addEventListener("input", e => {
    const t = e.target;
    if (t.dataset.slider) {
      $(`[data-sv=${t.dataset.slider}]`, el).textContent = fmt(t.dataset.slider, t.value);
      later({ [t.dataset.slider]: t.value });
    } else if (t.dataset.color) later({ [t.dataset.color]: t.value.slice(1).toUpperCase() });
    else if (t.dataset.textarea !== undefined) { editorDirty = true; textButtons(); }
  });
  el.addEventListener("click", e => {
    const seg = e.target.closest("[data-seg] [data-v]");
    if (seg) return set({ [seg.closest("[data-seg]").dataset.seg]: seg.dataset.v });
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "undo" && undo.length) send({ text: undo.pop() }, false, "Alteração desfeita.");
    if (act === "goverlay") openGoverlay();
    if (act === "save-text") send({ text: $("[data-textarea]", el).value }, true, "Arquivo salvo e aplicado.").then(ok => { if (ok) editorDirty = false; textButtons(); });
    if (act === "reload-text") { editorDirty = false; render(); }
  });
}

const fmt = (k, v) => (k.includes("alpha") ? `${Math.round(Number(v) * 100)}%` : k === "font_size" ? `${v} px` : String(v));

function later(changes) {
  Object.assign(pending, changes);
  clearTimeout(timer);
  timer = setTimeout(() => { const c = { ...pending }; for (const k in pending) delete pending[k]; set(c); }, 180);
}

function set(changes) { return send({ set: changes }, true); }

async function send(body, pushUndo, msg) {
  if (!st) return false;
  const before = st.text;
  busy++;
  $("[data-saved]", root).textContent = "Aplicando…";
  try {
    st = await app.api("/api/mangohud", { method: "POST", body: { file, ...body } });
    if (pushUndo && st.text !== before) { undo.push(before); if (undo.length > 30) undo.shift(); }
    $("[data-saved]", root).textContent = `Aplicado às ${new Date().toLocaleTimeString("pt-BR")}`;
    if (msg) toast(msg);
    return true;
  } catch (e) {
    toast(e.message, true);
    $("[data-saved]", root).textContent = "";
    return false;
  } finally {
    busy--;
    render();
  }
}

async function load() {
  try {
    st = await app.api(`/api/mangohud${file ? `?file=${encodeURIComponent(file)}` : ""}`);
    file = st.file;
    editorDirty = false;
    render();
  } catch (e) { $("[data-sub]", root).textContent = e.message; }
}

async function openGoverlay() {
  try {
    const r = await app.api(`/api/apps/${encodeURIComponent("io.github.benjamimgois.goverlay.desktop")}/launch`, { method: "POST", body: {} });
    toast(`Abrindo ${r.name} no PC…`);
  } catch (e) { toast(e.message, true); }
}

function textButtons() {
  $("[data-act=save-text]", root).disabled = !editorDirty;
  $("[data-act=reload-text]", root).disabled = !editorDirty;
}

function render() {
  if (!st || busy) return;
  const o = st.options, el = root;
  $("[data-sub]", el).textContent = [st.version ? `MangoHud ${st.version}` : null, st.path, st.exists ? null : "arquivo novo"].filter(Boolean).join(" · ");
  $("[data-act=goverlay]", el).hidden = !(st.goverlay && app.info?.features?.launch);
  $("[data-act=undo]", el).disabled = !undo.length;
  const sel = $("[data-file]", el);
  const label = f => f.scope === "global" ? `${f.name} (todos os jogos)` : f.scope === "app" ? `${f.name} (só um jogo)` : f.name;
  sel.innerHTML = st.files.map(f => `<option value="${esc(f.name)}" ${f.name === st.file ? "selected" : ""}>${esc(label(f))}</option>`).join("");
  $("[data-hide]", el).checked = !!o.no_display && o.no_display !== "0";

  const pos = o.position || "top-left";
  for (const b of $$("[data-p]", el)) b.setAttribute("aria-pressed", b.dataset.p === pos);
  for (const i of $$("[data-flag]", el)) i.checked = on(o[i.dataset.flag]);
  for (const [k, , , , , def] of SLIDERS) {
    const v = o[k] ?? def, r = $(`[data-slider=${k}]`, el);
    if (document.activeElement === r && Object.keys(pending).length) continue;   // usuário arrastando
    r.value = v;
    $(`[data-sv=${k}]`, el).textContent = fmt(k, v) + (o[k] == null ? " (padrão)" : "");
  }
  for (const [k, , def] of COLORS) {
    const v = String(o[k] ?? def).replace(/^#/, "").slice(0, 6);
    $(`[data-color=${k}]`, el).value = /^[0-9a-f]{6}$/i.test(v) ? `#${v}` : `#${def}`;
  }
  for (const i of $$("[data-text]", el)) if (document.activeElement !== i) i.value = typeof o[i.dataset.text] === "string" ? o[i.dataset.text] : "";
  for (const s of $$("[data-select]", el)) s.value = o[s.dataset.select] ?? "";
  for (const s of $$("[data-seg]", el)) {
    const v = o[s.dataset.seg] || "late";
    for (const b of $$("[data-v]", s)) b.setAttribute("aria-pressed", b.dataset.v === v);
  }
  for (const i of $$("[data-metric]", el)) {
    const k = i.dataset.metric;
    i.checked = k in o ? on(o[k]) : (st.legacy_layout && DEFAULT_ON.has(k));
  }
  $("[data-order-note]", el).textContent = st.legacy_layout
    ? "Layout clássico: o MangoHud organiza os itens sozinho."
    : "Os itens aparecem na ordem do arquivo; os ligados agora entram no fim do HUD.";
  if (!editorDirty) { $("[data-textarea]", el).value = st.text; textButtons(); }
  renderGame();
}

const on = v => v === true || (v != null && v !== "0" && v !== "false");

// jogo aberto: o HUD está ativo? usa outro arquivo (config só daquele jogo)?
function renderGame() {
  const g = app.snapshot?.data?.game;
  const box = $("[data-game]", root);
  if (!g?.game) { box.innerHTML = `<div class="note">Nenhum jogo aberto. Abra um jogo com o MangoHud para ver as mudanças ao vivo.</div>`; return; }
  const exe = (g.log_file || "").replace(/_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv$/, "").toLowerCase();
  const own = exe && st?.files.find(f => [`wine-${exe}.conf`, `${exe}.conf`].includes(f.name.toLowerCase()));
  let html = `<div class="mh-game"><span class="chip ${g.live ? "good" : ""}">${g.live ? "HUD ativo" : "HUD não detectado"}</span>
    <b>${esc(g.game.name)}</b>${g.fps != null ? ` <span class="muted num">${Math.round(g.fps)} FPS</span>` : ""}</div>`;
  if (own && own.name !== st.file) {
    html += `<div class="note">Este jogo usa o arquivo próprio <b>${esc(own.name)}</b>, não o ${esc(st.file)}.
      <button class="btn btn-sm" data-switch="${esc(own.name)}">Editar ${esc(own.name)}</button></div>`;
  } else if (!g.live && g.enabled === false) {
    html += `<div class="note">A leitura de FPS está desligada, então o painel não sabe se o HUD está no jogo. Ela pode ser ligada na Visão geral.</div>`;
  } else if (!g.live) {
    html += `<div class="note">O jogo não parece estar com o MangoHud. Na Steam, use a opção de inicialização <code>mangohud %command%</code>.</div>`;
  }
  box.innerHTML = html;
  $("[data-switch]", box)?.addEventListener("click", e => { file = e.target.dataset.switch; undo = []; load(); });
}

let unsub = null, lastGame = "";
export default {
  id: "mangohud", title: "MangoHud", icon: ic("frame-corners"),
  enabled: a => !!a.info?.features?.mangohud,
  mount,
  show() {
    load();
    unsub = app.onSnapshot(snap => {
      const g = snap.data?.game;
      const sig = `${g?.game?.key}|${g?.live}|${g?.fps != null ? Math.round(g.fps) : ""}`;
      if (sig !== lastGame && st) { lastGame = sig; renderGame(); }
    });
  },
  hide() { unsub?.(); unsub = null; },
};
