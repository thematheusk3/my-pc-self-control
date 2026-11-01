// Aba Ventoinha: status ao vivo, modo (automático / manual / placa), velocidade fixa e editor de curva.
import { $, esc, pct, toast } from "../ui.js";
import { Spark } from "../spark.js";

let app, root, sparks = {}, st = null;
let draft = null, dirty = false, busy = false;     // curva em edição
let manualDraft = null;

const T_MIN = 20, T_MAX = 100;                       // eixo X do editor (°C)
const W = 600, H = 260, PAD = { l: 38, r: 12, t: 12, b: 28 };
const sx = t => PAD.l + ((t - T_MIN) / (T_MAX - T_MIN)) * (W - PAD.l - PAD.r);
const sy = p => H - PAD.b - (p / 100) * (H - PAD.t - PAD.b);
const ix = x => T_MIN + ((x - PAD.l) / (W - PAD.l - PAD.r)) * (T_MAX - T_MIN);
const iy = y => ((H - PAD.b - y) / (H - PAD.t - PAD.b)) * 100;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const PRESET_NAMES = { silencioso: "Silencioso", equilibrado: "Equilibrado", desempenho: "Desempenho" };
const MODE_NAMES = { auto: "Automático", manual: "Manual", firmware: "Placa" };

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card" id="fan-status">
      <div class="card-head"><div><div class="card-title">Ventoinha</div><div class="card-sub" data-sub></div></div><div data-chip></div></div>
      <div class="hero-row"><div class="hero num" data-v="rpm">—</div></div>
      <div class="spark" data-spark="rpm"></div>
      <div class="stats" data-stats></div>
      <div class="note" data-reason></div>
    </article>
    <article class="card span-2" id="fan-ctl">
      <div class="card-head"><div class="card-title">Controle</div>
        <div class="seg" role="group" aria-label="Modo" data-modes>
          <button data-m="auto" aria-pressed="false">Automático</button>
          <button data-m="manual" aria-pressed="false">Manual</button>
          <button data-m="firmware" aria-pressed="false">Placa</button>
        </div></div>
      <div data-alerts></div>
      <div data-pane="none" class="note">O painel ainda não está controlando a ventoinha. Escolha um modo acima.</div>
      <div data-pane="firmware" class="note" hidden>A placa (firmware do chip Nuvoton) decide a velocidade. O painel não interfere.</div>
      <div data-pane="manual" hidden>
        <div class="fan-manual">
          <input type="range" min="0" max="100" step="1" data-manual aria-label="Velocidade manual">
          <span class="hero num" data-manual-v>—</span>
        </div>
        <p class="note" data-manual-note></p>
      </div>
      <div data-pane="auto" hidden>
        <div class="toolbar" style="margin:4px 0 8px">
          <span class="stat-label">Perfil</span>
          <div class="seg" role="group" aria-label="Perfis" data-presets>
            ${Object.entries(PRESET_NAMES).map(([k, v]) => `<button data-p="${k}" aria-pressed="false">${v}</button>`).join("")}
          </div>
        </div>
        <svg class="fan-curve" viewBox="0 0 ${W} ${H}" data-svg role="img" aria-label="Curva temperatura × velocidade"></svg>
        <div class="fan-points" data-points></div>
        <div class="toolbar" style="margin-top:10px">
          <button class="btn btn-sm" data-act="add">+ Ponto</button>
          <span style="flex:1"></span>
          <button class="btn btn-sm" data-act="revert" disabled>Desfazer</button>
          <button class="btn btn-sm btn-primary" data-act="save" disabled>Salvar curva</button>
        </div>
        <p class="note">Arraste os pontos ou edite os valores. A velocidade sobe na hora quando esquenta e só desce
          depois que a temperatura cai <span data-hyst>3</span> °C abaixo do ponto, devagar, para não ficar oscilando.</p>
      </div>
    </article>
    <article class="card" id="fan-hist">
      <div class="card-head"><div class="card-title">Temperatura × velocidade</div></div>
      <div class="spark" data-spark="mix" style="height:110px"></div>
      <div class="spark-legend"><span><i style="background:var(--series-2)"></i>Temperatura (°C)</span><span><i style="background:var(--series-1)"></i>Velocidade (%)</span></div>
    </article>
  </div>`;

  sparks.rpm = new Spark($("[data-spark=rpm]", el), { series: [{ key: "fan.rpm" }], min: 0, format: v => `${Math.round(v)} RPM` });
  sparks.mix = new Spark($("[data-spark=mix]", el), {
    series: [{ key: "fan.temp", label: "Temp.", color: "--series-2" }, { key: "fan.duty", label: "Fan", color: "--series-1" }],
    min: 0, max: 100, format: v => v.toFixed(0),
  });

  $("[data-modes]", el).addEventListener("click", e => {
    const b = e.target.closest("button[data-m]");
    if (b && st && b.dataset.m !== st.mode) send({ mode: b.dataset.m }, `Modo: ${MODE_NAMES[b.dataset.m]}`);
  });

  const range = $("[data-manual]", el);
  range.addEventListener("input", () => { manualDraft = Number(range.value); $("[data-manual-v]", el).innerHTML = `${manualDraft}<small>%</small>`; });
  range.addEventListener("change", () => send({ mode: "manual", manual_pct: Number(range.value) }).then(() => { manualDraft = null; }));

  $("[data-presets]", el).addEventListener("click", e => {
    const b = e.target.closest("button[data-p]");
    if (!b || !st) return;
    draft = st.presets[b.dataset.p].map(p => [...p]); dirty = true;
    renderCurve();
  });

  $("#fan-ctl", el).addEventListener("click", e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act || !draft) return;
    if (act === "save") send({ mode: "auto", curve: draft }, "Curva salva").then(ok => { if (ok) dirty = false; renderCurve(); });
    if (act === "revert") { draft = st.curve.map(p => [...p]); dirty = false; renderCurve(); }
    if (act === "add" && draft.length < 12) {
      // insere no maior intervalo de temperatura
      let gi = 0, gap = -1;
      for (let i = 0; i < draft.length - 1; i++) if (draft[i + 1][0] - draft[i][0] > gap) { gap = draft[i + 1][0] - draft[i][0]; gi = i; }
      const a = draft[gi], b = draft[gi + 1];
      draft.splice(gi + 1, 0, [Math.round((a[0] + b[0]) / 2), Math.round((a[1] + b[1]) / 2)]);
      dirty = true; renderCurve();
    }
    if (act === "del") {
      const i = Number(e.target.closest("[data-i]").dataset.i);
      if (draft.length > 2) { draft.splice(i, 1); dirty = true; renderCurve(); }
    }
  });

  $("[data-points]", el).addEventListener("change", e => {
    const inp = e.target.closest("input[data-k]");
    if (!inp) return;
    const i = Number(inp.closest("[data-i]").dataset.i), k = Number(inp.dataset.k);
    let v = Number(inp.value);
    if (Number.isNaN(v)) return renderCurve();
    draft[i][k] = k === 0 ? clamp(v, T_MIN, T_MAX) : clamp(v, 0, 100);
    draft.sort((a, b) => a[0] - b[0]);
    for (let j = 1; j < draft.length; j++) draft[j][1] = Math.max(draft[j][1], draft[j - 1][1]); // nunca decrescente
    dirty = true; renderCurve();
  });

  setupDrag($("[data-svg]", el));
  app.onSnapshot(update);
}

async function send(body, okMsg) {
  if (busy) return false;
  busy = true;
  try {
    st = await app.api("/api/fan", { method: "POST", body });
    if (okMsg) toast(okMsg);
    render();
    return true;
  } catch (err) {
    toast(err.message, true);
    render();
    return false;
  } finally {
    busy = false;
  }
}

// ---------- editor de curva ----------
function setupDrag(svg) {
  let drag = null;
  const pt = e => {
    const r = svg.getBoundingClientRect();
    return [(e.clientX - r.left) * (W / r.width), (e.clientY - r.top) * (H / r.height)];
  };
  svg.addEventListener("pointerdown", e => {
    const c = e.target.closest("circle[data-i]");
    if (!c) return;
    drag = Number(c.dataset.i);
    svg.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  svg.addEventListener("pointermove", e => {
    if (drag == null) return;
    const [x, y] = pt(e);
    const prev = draft[drag - 1], next = draft[drag + 1];
    const t = clamp(Math.round(ix(x)), prev ? prev[0] + 1 : T_MIN, next ? next[0] - 1 : T_MAX);
    const p = clamp(Math.round(iy(y)), prev ? prev[1] : 0, next ? next[1] : 100);
    draft[drag] = [t, p];
    dirty = true;
    renderCurve();
  });
  const end = () => { drag = null; };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
}

function renderCurve() {
  if (!draft || !st) return;
  const svg = $("[data-svg]", root);
  const grid = [];
  for (let t = 20; t <= 100; t += 10) grid.push(`<line x1="${sx(t)}" x2="${sx(t)}" y1="${PAD.t}" y2="${H - PAD.b}" class="g"/><text x="${sx(t)}" y="${H - 8}" text-anchor="middle">${t}°</text>`);
  for (let p = 0; p <= 100; p += 25) grid.push(`<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${sy(p)}" y2="${sy(p)}" class="g"/><text x="${PAD.l - 6}" y="${sy(p) + 4}" text-anchor="end">${p}%</text>`);
  const pts = [[T_MIN, draft[0][1]], ...draft, [T_MAX, draft.at(-1)[1]]];
  const line = pts.map(([t, p]) => `${sx(t).toFixed(1)},${sy(p).toFixed(1)}`).join(" ");
  const area = `${sx(T_MIN)},${sy(0)} ${line} ${sx(T_MAX)},${sy(0)}`;
  const minP = st.min_pct, emer = st.emergency_temp;
  const temp = st.temp_avg ?? st.temp;
  svg.innerHTML = `${grid.join("")}
    ${minP > 0 ? `<rect x="${PAD.l}" y="${sy(minP)}" width="${W - PAD.l - PAD.r}" height="${sy(0) - sy(minP)}" class="min"/>` : ""}
    ${emer < T_MAX ? `<rect x="${sx(emer)}" y="${PAD.t}" width="${sx(T_MAX) - sx(emer)}" height="${H - PAD.t - PAD.b}" class="emer"/>` : ""}
    <polygon points="${area}" class="area"/>
    <polyline points="${line}" class="line"/>
    <g data-now></g>
    ${draft.map(([t, p], i) => `<circle data-i="${i}" cx="${sx(t)}" cy="${sy(p)}" r="9"><title>${t} °C → ${p}%</title></circle>`).join("")}`;
  renderNow(temp);

  $("[data-points]", root).innerHTML = draft.map(([t, p], i) => `<div class="fan-pt" data-i="${i}">
      <label><input class="search" type="number" inputmode="numeric" min="${T_MIN}" max="${T_MAX}" step="1" data-k="0" value="${t}"> °C</label>
      <span class="muted">→</span>
      <label><input class="search" type="number" inputmode="numeric" min="0" max="100" step="1" data-k="1" value="${p}"> %</label>
      <button class="btn btn-sm btn-ghost-danger" data-act="del" ${draft.length <= 2 ? "disabled" : ""} aria-label="Remover ponto">✕</button>
    </div>`).join("");
  $("[data-act=save]", root).disabled = !dirty;
  $("[data-act=revert]", root).disabled = !dirty;
  $("[data-act=add]", root).disabled = draft.length >= 12;
  const same = name => JSON.stringify(st.presets[name]) === JSON.stringify(draft);
  root.querySelectorAll("[data-presets] button").forEach(b => b.setAttribute("aria-pressed", same(b.dataset.p)));
}

function renderNow(temp) {
  const g = $("[data-now]", root);
  if (!g) return;
  if (temp == null) { g.innerHTML = ""; return; }
  const t = clamp(temp, T_MIN, T_MAX);
  const duty = st.duty_pct;
  g.innerHTML = `<line x1="${sx(t)}" x2="${sx(t)}" y1="${PAD.t}" y2="${H - PAD.b}" class="now"/>
    ${duty != null && st.mode === "auto" ? `<circle cx="${sx(t)}" cy="${sy(duty)}" r="5" class="nowpt"/>` : ""}
    <text x="${sx(t) + 6}" y="${PAD.t + 12}" class="nowtxt">${temp.toFixed(0)} °C</text>`;
}

// ---------- status ----------
function stat(label, value) {
  return `<div><div class="stat-label">${label}</div><div class="stat-value">${value}</div></div>`;
}

function alerts() {
  const a = [];
  if (st.error) a.push(["critical", esc(st.error)]);
  else if (!st.writable) a.push(["warning", `O painel ainda não tem permissão para controlar a ventoinha. Na máquina, rode
    <code>sudo ./install.sh</code> em <code>/opt/applications/self-monitoring</code>.`]);
  if (st.coolercontrol && (st.mode === "auto" || st.mode === "manual"))
    a.push([st.external ? "critical" : "warning", `O <b>CoolerControl</b> está rodando${st.external ? " e mudou a ventoinha" : ""}. Dois programas
      controlando a mesma ventoinha brigam entre si: no CoolerControl, deixe <b>${esc(st.label)}</b> no perfil <i>Default</i>,
      ou pare o serviço com <code>sudo systemctl disable --now coolercontrold</code>.`]);
  else if (st.external) a.push(["warning", "Outro programa alterou a ventoinha nos últimos segundos."]);
  if (st.reason?.startsWith("Emergência") || st.reason?.startsWith("Sem leitura")) a.push(["critical", esc(st.reason)]);
  return a.map(([c, t]) => `<div class="fan-alert ${c}">${t}</div>`).join("");
}

function render() {
  if (!st || !root.classList.contains("active")) return;
  const s = $("#fan-status", root);
  $("[data-sub]", s).textContent = `${st.label} · ${st.device}`;
  $("[data-chip]", s).innerHTML = st.mode ? `<span class="chip ${st.reason?.startsWith("Emerg") ? "critical" : "good"}">${MODE_NAMES[st.mode]}</span>` : `<span class="chip">Não gerenciada</span>`;
  $("[data-v=rpm]", s).innerHTML = st.rpm != null ? `${Math.round(st.rpm)}<small>RPM</small>` : `<span class="muted">—</span>`;
  $("[data-stats]", s).innerHTML =
    stat("Velocidade", st.pwm_enable === 1 ? pct(st.duty_pct) : "Placa") +
    stat("Alvo", st.target_pct != null ? pct(st.target_pct) : "—") +
    stat("Temperatura", st.temp != null ? `${st.temp.toFixed(1)} °C` : "—") +
    stat("Média 5 s", st.temp_avg != null ? `${st.temp_avg.toFixed(1)} °C` : "—");
  $("[data-reason]", s).textContent = [st.reason, st.temp_source ? `Sensor: ${st.temp_source}` : ""].filter(Boolean).join(" · ");

  const c = $("#fan-ctl", root);
  c.querySelectorAll("[data-modes] button").forEach(b => {
    b.setAttribute("aria-pressed", b.dataset.m === st.mode);
    b.disabled = !st.writable || busy;
  });
  $("[data-alerts]", c).innerHTML = alerts();
  for (const p of c.querySelectorAll("[data-pane]")) p.hidden = p.dataset.pane !== (st.mode || "none");

  const range = $("[data-manual]", c);
  range.min = st.min_pct;
  if (manualDraft == null) {
    range.value = st.manual_pct;
    $("[data-manual-v]", c).innerHTML = `${Math.round(st.manual_pct)}<small>%</small>`;
  }
  $("[data-manual-note]", c).textContent =
    `Mínimo ${st.min_pct}%. Acima de ${st.emergency_temp} °C vai para 100% mesmo no modo manual.`;
  $("[data-hyst]", c).textContent = st.hysteresis;

  if (!dirty) { draft = st.curve.map(p => [...p]); renderCurve(); }
  else renderNow(st.temp_avg ?? st.temp);
}

function update(snap) {
  if (!root.classList.contains("active")) return;
  const f = snap.data.fan;
  if (!f || f.error && f.available === undefined) {
    $(".grid", root).innerHTML = `<div class="card empty span-all">Controle de ventoinha indisponível${f?.error ? `: ${esc(f.error)}` : ""}.</div>`;
    return;
  }
  st = f;
  for (const sp of Object.values(sparks)) sp.setData(app.history);
  render();
}

export default {
  id: "fan", title: "Ventoinha", icon: "✇", mount,
  enabled: a => !!a.info?.features?.fan,
  show: () => { if (app.snapshot) update(app.snapshot); },
};
