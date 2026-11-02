// Aba Controles: Áudio (saída, volume, por app, microfone), Tela (resolução, brilho, desligar) e Captura de tela.
import { $, bytes, esc, toast } from "../ui.js";

let app, root, view = "audio", timer = null, busy = false;
let audio = null, disp = null, ddc = null, shots = null;
const ICON = { hdmi: "🖥", headset: "🎧", bluetooth: "🎧", usb: "🔌", speaker: "🔊" };

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div class="seg proc-views" data-views>
      <button data-v="audio" aria-pressed="true">Áudio</button>
      <button data-v="display" aria-pressed="false">Tela</button>
      ${app.info.features.screenshot ? `<button data-v="shot" aria-pressed="false">Captura de tela</button>` : ""}
    </div>
    <div data-body></div>`;
  $("[data-views]", el).addEventListener("click", e => {
    const b = e.target.closest("[data-v]");
    if (b) location.hash = b.dataset.v === "audio" ? "controls" : `controls:${b.dataset.v}`;
  });
  const body = $("[data-body]", el);
  body.addEventListener("click", onClick);
  body.addEventListener("change", onChange);
  // sliders: aplica ao soltar; mostra o valor enquanto arrasta
  body.addEventListener("input", e => {
    const r = e.target.closest("input[type=range]");
    if (r) { busy = true; const out = r.parentElement.querySelector("output"); if (out) out.textContent = `${r.value}%`; }
  });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : root.classList.contains("active") && start()));
}

function route(sub) {
  view = ["display", "shot"].includes(sub) ? sub : "audio";
  root.querySelectorAll("[data-views] button").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === view));
  $("[data-body]", root).innerHTML = `<div class="card empty">Carregando…</div>`;
  if (timer) start();
}

function start() {
  stop();
  refresh(true);
  timer = setInterval(() => refresh(false), view === "audio" ? 2000 : 5000);
}
function stop() { clearInterval(timer); timer = null; }

async function refresh(first) {
  if (busy && !first) return;
  // não redesenha enquanto um menu/controle está em uso (fecharia o menu aberto no celular)
  const act = document.activeElement;
  if (!first && act && root.contains(act) && act.matches("select, input")) return;
  try {
    if (view === "audio") { audio = await app.api("/api/audio"); renderAudio(); }
    else if (view === "display") {
      disp = await app.api("/api/display");
      if (first || !ddc) app.api("/api/display/ddc").then(r => { ddc = r.monitors; renderDisplay(); }).catch(e => { ddc = { error: e.message }; renderDisplay(); });
      renderDisplay();
    } else if (view === "shot") { await loadShots(first); }
  } catch (e) {
    $("[data-body]", root).innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
  }
}

async function post(url, body, okMsg) {
  try {
    const r = await app.api(url, { method: "POST", body });
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) { toast(e.message, true); return null; }
  finally { busy = false; }
}

// ---------- áudio ----------
const slider = (attrs, v) => `<span class="vol"><input type="range" min="0" max="150" step="1" value="${v ?? 0}" ${attrs}><output>${v ?? "—"}%</output></span>`;
const muteBtn = (attrs, m, onIco = "🔇", offIco = "🔊") => `<button class="btn btn-sm mute ${m ? "on" : ""}" ${attrs} aria-pressed="${m}" title="${m ? "Ativar som" : "Mudo"}">${m ? onIco : offIco}</button>`;

function renderAudio() {
  const a = audio;
  if (!a) return;
  const sinkOpts = sel => a.sinks.map(s => `<option value="${esc(s.name)}" ${s.name === sel ? "selected" : ""}>${esc(short(s.description))}</option>`).join("");
  const mic = a.sources.find(s => s.default);
  $("[data-body]", root).innerHTML = `<div class="grid">
    <article class="card span-2">
      <div class="card-head"><div class="card-title">Saída de som</div></div>
      <div class="dev-list">${a.sinks.map(s => `<div class="dev ${s.default ? "default" : ""}">
          <button class="dev-pick" data-op="set_default" data-kind="sink" data-name="${esc(s.name)}" aria-pressed="${s.default}" title="Usar esta saída">
            <span class="dev-ico">${ICON[s.kind] || "🔊"}</span><span class="dev-name">${esc(short(s.description))}${s.default ? ` <span class="chip good">em uso</span>` : ""}</span></button>
          <div class="dev-ctl">${muteBtn(`data-op="mute" data-kind="sink" data-name="${esc(s.name)}" data-mute="${!s.mute}"`, s.mute)}
            ${slider(`data-op="volume" data-kind="sink" data-name="${esc(s.name)}"`, s.volume)}</div></div>`).join("") || `<p class="muted">Nenhuma saída de áudio.</p>`}</div>
      <p class="note">Tocar numa saída faz dela a padrão e leva para ela os apps que estão tocando.</p>
    </article>
    <article class="card">
      <div class="card-head"><div class="card-title">Microfone</div>${mic ? `<span class="chip ${mic.mute ? "critical" : "good"}">${mic.mute ? "Mudo" : "Ligado"}</span>` : ""}</div>
      ${mic ? `<button class="btn mic-big ${mic.mute ? "btn-danger" : ""}" data-op="mute" data-kind="source" data-name="${esc(mic.name)}" data-mute="${!mic.mute}">${mic.mute ? "🎙 Microfone mudo · tocar para ligar" : "🎙 Mutar microfone"}</button>
        <div class="dev-ctl" style="margin-top:10px">${slider(`data-op="volume" data-kind="source" data-name="${esc(mic.name)}"`, mic.volume)}</div>
        ${a.sources.length > 1 ? `<select class="search" style="margin-top:10px;width:100%" data-op="set_default" data-kind="source">${a.sources.map(s => `<option value="${esc(s.name)}" ${s.default ? "selected" : ""}>${esc(short(s.description))}</option>`).join("")}</select>` : ""}
        ${a.recording.length ? `<p class="note">Usando o microfone agora: <b>${esc(a.recording.map(r => r.app).join(", "))}</b></p>` : `<p class="note">Nenhum app usando o microfone agora.</p>`}`
      : `<p class="muted">Nenhum microfone.</p>`}
    </article>
    <article class="card span-all">
      <div class="card-head"><div class="card-title">Áudio por aplicativo</div></div>
      ${a.apps.length ? `<div class="app-audio">${a.apps.map(s => `<div class="dev">
          <div class="dev-name"><b>${esc(s.app)}</b>${s.media && s.media !== s.app ? ` <span class="muted">· ${esc(s.media)}</span>` : ""}${s.corked ? ` <span class="chip">pausado</span>` : ""}</div>
          <div class="dev-ctl">${muteBtn(`data-op="mute" data-kind="app" data-index="${s.index}" data-mute="${!s.mute}"`, s.mute)}
            ${slider(`data-op="volume" data-kind="app" data-index="${s.index}"`, s.volume)}
            <select class="search out-sel" data-op="move" data-index="${s.index}" title="Por onde este app sai">${sinkOpts(s.device)}</select></div></div>`).join("")}</div>`
      : `<p class="muted">Nenhum aplicativo tocando som agora.</p>`}
      <p class="note">Cada app pode sair por um dispositivo diferente, ex.: o jogo no HDMI e o Discord no fone.</p>
    </article></div>`;
}

const short = d => (d || "").replace(/ Estéreo (analógico|digital)| Stereo| Mono| Analog(ue)?/gi, "").replace(/HD-Audio Generic /, "");

async function onClick(e) {
  const b = e.target.closest("button[data-op], button[data-act]");
  if (!b || b.disabled) return;
  const d = b.dataset;
  if (view === "audio" && d.op) {
    const body = { op: d.op, kind: d.kind, name: d.name, index: d.index != null ? Number(d.index) : undefined };
    if (d.op === "mute") body.mute = d.mute === "true";
    b.disabled = true;
    const r = await post("/api/audio", body);
    if (r) { audio = r; renderAudio(); }
    return;
  }
  if (d.act) return displayAct(d, b);
}

async function onChange(e) {
  const t = e.target;
  const d = t.dataset;
  if (view === "audio" && d.op) {
    let body;
    if (t.type === "range") body = { op: "volume", kind: d.kind, name: d.name, index: d.index != null ? Number(d.index) : undefined, volume: Number(t.value) };
    else if (d.op === "move") body = { op: "move", index: Number(d.index), sink: t.value };
    else if (d.op === "set_default") body = { op: "set_default", kind: d.kind, name: t.value };
    if (!body) return;
    const r = await post("/api/audio", body);
    if (r) { audio = r; renderAudio(); }
  }
  if (view === "display" && d.ddc) {
    busy = true;
    const r = await post("/api/display/ddc", { bus: Number(d.bus), [d.ddc]: Number(t.value) });
    if (r) { ddc = r.monitors; renderDisplay(); }
  }
}

// ---------- tela ----------
function renderDisplay() {
  if (!disp) return;
  const p = disp.pending;
  const outs = disp.outputs.map(o => {
    const groups = {};
    for (const m of o.modes) (groups[`${m.w}x${m.h}`] ||= []).push(m);
    return `<article class="card">
      <div class="card-head"><div><div class="card-title">Monitor ${esc(o.name)}</div>
        <div class="card-sub">${o.current ? `${o.current.w}x${o.current.h} @ ${o.current.hz} Hz` : "—"} · escala ${o.scale}</div></div></div>
      <label class="stat-label" for="mode-${esc(o.name)}">Resolução e taxa de atualização</label>
      <select class="search" id="mode-${esc(o.name)}" style="width:100%;margin-top:4px" data-mode-sel="${esc(o.name)}">
        ${Object.entries(groups).map(([res, ms]) => `<optgroup label="${res}">${ms.map(m => `<option value="${m.id}" ${o.current?.id === m.id ? "selected" : ""}>${m.w}x${m.h} @ ${m.hz} Hz${o.preferred.includes(m.id) ? " (recomendado)" : ""}</option>`).join("")}</optgroup>`).join("")}
      </select>
      <button class="btn btn-primary" style="width:100%;margin-top:8px" data-act="mode" data-out="${esc(o.name)}" ${p ? "disabled" : ""}>Aplicar</button>
      <p class="note">Depois de aplicar, você tem ${disp.revert_after} s para confirmar. Se a tela ficar preta, não faça nada: ela volta sozinha.</p>
    </article>`;
  }).join("");

  let ddcHtml;
  if (!ddc) ddcHtml = `<p class="muted">Lendo o monitor…</p>`;
  else if (ddc.error) ddcHtml = `<p class="muted">${esc(ddc.error)}</p>`;
  else if (!ddc.length) ddcHtml = `<p class="muted">Nenhum monitor com DDC/CI. Ative "DDC/CI" no menu do monitor.</p>`;
  else ddcHtml = ddc.map(m => `<div style="margin-bottom:12px"><div class="pname">${esc(m.model)} <span class="muted">${esc(m.connector || "")}</span>${m.dimmed ? ` <span class="chip warning">apagada</span>` : ""}</div>
      <div class="ddc-row"><span>Brilho</span><span class="vol"><input type="range" min="0" max="${m.brightness_max || 100}" value="${m.brightness ?? 0}" data-ddc="brightness" data-bus="${m.bus}"><output>${m.brightness ?? "—"}%</output></span></div>
      <div class="ddc-row"><span>Contraste</span><span class="vol"><input type="range" min="0" max="${m.contrast_max || 100}" value="${m.contrast ?? 0}" data-ddc="contrast" data-bus="${m.bus}"><output>${m.contrast ?? "—"}%</output></span></div>
      <div class="power-btns" style="margin-top:8px">
        <button class="btn" data-act="dim" data-bus="${m.bus}" ${m.dimmed ? "disabled" : ""}>🌑 Apagar tela</button>
        <button class="btn" data-act="restore" data-bus="${m.bus}" ${m.dimmed ? "" : "disabled"}>☀ Restaurar</button>
      </div></div>`).join("");

  $("[data-body]", root).innerHTML = `
    ${p ? `<div class="fan-alert warning">Nova resolução: <b>${esc(p.label)}</b>. Manter? Volta sozinha em <b>${p.seconds_left}s</b>.
      <div class="power-btns" style="margin-top:8px"><button class="btn btn-primary" data-act="confirm">Manter</button><button class="btn" data-act="revert">Desfazer agora</button></div></div>` : ""}
    <div class="grid">${outs}
      <article class="card">
        <div class="card-head"><div class="card-title">Brilho do monitor (DDC/CI)</div></div>
        ${ddcHtml}
        <p class="note"><b>Apagar tela</b> zera brilho e contraste pelo próprio monitor: fica praticamente preta, mas <b>continua ligada</b> e o PC continua vendo o monitor conectado. <b>Restaurar</b> volta aos valores de antes.</p>
      </article>
      <article class="card">
        <div class="card-head"><div class="card-title">Desligar a tela (espera)</div></div>
        <button class="btn" style="width:100%" data-act="dpms-off">⏾ Pôr o monitor em espera</button>
        <p class="note">Usa o modo de espera do monitor (DPMS); qualquer tecla ou mouse no PC acorda. ⚠ Alguns monitores em DisplayPort "somem" do sistema em espera, como quando você desliga pelo botão. Se acontecer com o seu, prefira <b>Apagar tela</b>.</p>
      </article>
    </div>`;
}

async function displayAct(d, b) {
  b.disabled = true;
  if (d.act === "mode") {
    const sel = $(`[data-mode-sel="${CSS.escape(d.out)}"]`, root);
    const r = await post("/api/display/mode", { output: d.out, mode: sel.value });
    if (r) { disp.pending = r.pending; renderDisplay(); setTimeout(() => refresh(true), 1500); }
  } else if (d.act === "confirm" || d.act === "revert") {
    await post("/api/display/mode", { op: d.act }, d.act === "confirm" ? "Resolução mantida." : "Resolução desfeita.");
    refresh(true);
  } else if (d.act === "dim" || d.act === "restore") {
    const r = await post("/api/display/ddc", { bus: Number(d.bus), op: d.act }, d.act === "dim" ? "Tela apagada (monitor continua ligado)." : "Brilho restaurado.");
    if (r) { ddc = r.monitors; renderDisplay(); }
  } else if (d.act === "dpms-off") {
    await post("/api/display/dpms", { on: false }, "Monitor em espera. Mexa no mouse do PC para acordar.");
    b.disabled = false;
  }
}

// ---------- captura ----------
const shotKey = list => (list || []).map(s => `${s.name}:${s.size}`).join("|");
let taking = false;

// recarrega a lista (também a cada 5 s, para capturas feitas por outro aparelho); só redesenha se mudou
async function loadShots(force) {
  if (taking && !force) return;
  const list = (await app.api("/api/screenshots")).shots;
  if (!force && shotKey(list) === shotKey(shots)) return;
  shots = list;
  renderShots();
}

// imagem que falhou (ex.: ainda sendo gravada): tenta de novo algumas vezes
function retryImg(img) {
  const n = Number(img.dataset.retry || 0);
  if (n >= 5) return;
  img.dataset.retry = n + 1;
  setTimeout(() => { img.src = `${img.src.split("&r=")[0]}&r=${n + 1}`; }, 800 * (n + 1));
}

function renderShots() {
  $("[data-body]", root).innerHTML = `
    <article class="card">
      <div class="card-head"><div class="card-title">Captura de tela</div></div>
      <button class="btn btn-primary" style="width:100%" data-act-shot="take">📸 Capturar a tela agora</button>
      <p class="note">Toda captura mostra antes um aviso visível na tela do PC. Se o aviso não puder aparecer, a captura não é feita. As últimas 30 ficam guardadas no PC.</p>
    </article>
    <div class="shots">${(shots || []).map(s => `<figure class="card shot">
        <a href="/api/screenshots/${s.name}?v=${s.size}" target="_blank" rel="noopener"><img src="/api/screenshots/${s.name}?v=${s.size}" alt="Captura de ${new Date(s.time * 1000).toLocaleString("pt-BR")}" loading="lazy"></a>
        <figcaption><span>${new Date(s.time * 1000).toLocaleString("pt-BR")} · ${bytes(s.size)}</span>
          <span><a class="btn btn-sm" href="/api/screenshots/${s.name}?download=1">Baixar</a>
          <button class="btn btn-sm btn-ghost-danger" data-act-shot="del" data-name="${s.name}">Apagar</button></span></figcaption>
      </figure>`).join("")}</div>`;
  $("[data-body]", root).querySelectorAll(".shot img").forEach(img => img.addEventListener("error", () => retryImg(img)));
  $("[data-body]", root).onclick = async e => {
    const b = e.target.closest("[data-act-shot]");
    if (!b) return;
    b.disabled = true;
    taking = true;
    try {
      if (b.dataset.actShot === "take") {
        b.textContent = "Capturando…";
        const r = await post("/api/screenshot", {}, "Captura feita (o aviso apareceu na tela do PC).");
        if (r?.name) openShot(r);
      } else {
        await post(`/api/screenshots/${b.dataset.name}/delete`, {});
      }
      await loadShots(true);
    } catch (err) { toast(err.message, true); renderShots(); }
    finally { taking = false; }
  };
}

// abre a captura recém-tirada por cima do painel (window.open depois de um await é bloqueado no celular)
function openShot(s) {
  const dlg = $("#sheet");
  const src = `/api/screenshots/${encodeURIComponent(s.name)}?v=${s.size}`;
  dlg.innerHTML = `
    <h2>Captura de tela</h2>
    <div class="muted">${esc(s.name)} · ${bytes(s.size)}</div>
    <a href="${src}" target="_blank" rel="noopener"><img class="shot-view" src="${src}" alt="Captura recém-tirada"></a>
    <div class="sheet-actions">
      <a class="btn" href="${src}" target="_blank" rel="noopener">Tamanho real</a>
      <a class="btn" href="${src}&download=1">Baixar</a>
      <button class="btn full" data-close>Fechar</button>
    </div>`;
  dlg.querySelector("img").addEventListener("error", e => retryImg(e.target));
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); }, { once: true });
  if (!dlg.open) dlg.showModal();
}

export default {
  id: "controls", title: "Controles", icon: "🎚", mount, route,
  enabled: a => !!(a.info?.features?.control || a.info?.features?.screenshot),
  show: start, hide: stop,
};
