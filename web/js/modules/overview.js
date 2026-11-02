import { $, bytes, esc, h, meter, mhz, pct, rate, tempChip, toast } from "../ui.js";
import { Spark } from "../spark.js";
import { openProcessSheet } from "../procsheet.js";

let app, root, sparks = {}, topMode = "cpu";

function card(id, title, extra = "") {
  return `<article class="card ${extra}" id="${id}">
    <div class="card-head"><div><div class="card-title">${title}</div><div class="card-sub" data-sub></div></div><div data-chip></div></div>
    <div data-body></div></article>`;
}

function mount(el, a) {
  app = a; root = el;
  const gpus = app.info.info.gpu?.gpus || [];
  el.innerHTML = `<div class="grid">
    ${card("ov-cpu", "CPU")}
    ${gpus.map((g, i) => card(`ov-gpu-${i}`, gpus.length > 1 ? `GPU ${i + 1}` : "GPU")).join("")}
    ${card("ov-now", "No PC agora")}
    ${card("ov-game", "Jogo")}
    ${card("ov-steam", "Downloads Steam")}
    ${card("ov-mem", "Memória")}
    ${card("ov-top", "Maiores consumidores")}
    ${card("ov-net", "Rede")}
    ${card("ov-disk", "Armazenamento")}
  </div>`;

  // estrutura fixa (atualizações só trocam valores, gráficos não são recriados)
  $("#ov-cpu [data-body]", el).innerHTML = `
    <div class="hero-row"><div class="hero num" data-v="total">—</div></div>
    <div class="spark" data-spark="cpu"></div>
    <div class="cores" data-cores></div>
    <div class="stats" data-stats></div>`;
  sparks.cpu = new Spark($("[data-spark=cpu]", el), { series: [{ key: "cpu.total" }], min: 0, max: 100, format: v => pct(v, 1) });

  gpus.forEach((g, i) => {
    $(`#ov-gpu-${i} [data-body]`, el).innerHTML = `
      <div class="hero-row"><div class="hero num" data-v="busy">—</div></div>
      <div class="spark" data-spark="gpu${i}"></div>
      <div data-meters></div><div class="stats" data-stats></div><div class="note" data-note></div>`;
    sparks[`gpu${i}`] = new Spark($(`[data-spark=gpu${i}]`, el), { series: [{ key: `gpu.${i}.busy` }], min: 0, max: 100, format: v => pct(v, 1) });
  });

  // jogo / FPS
  const gm = $("#ov-game", el);
  $("[data-chip]", gm).innerHTML = `<label class="check" title="Ler o FPS dos jogos pelo MangoHud"><input type="checkbox" data-fps> FPS</label>`;
  $("[data-body]", gm).innerHTML = `<div data-game></div><div class="spark" data-spark="fps" hidden></div><div class="note" data-game-note></div>`;
  sparks.fps = new Spark($("[data-spark=fps]", el), { series: [{ key: "game.fps", color: "--series-2" }], min: 0, format: v => `${Math.round(v)} FPS` });
  $("[data-fps]", gm).addEventListener("change", async e => {
    const on = e.target.checked;
    e.target.disabled = true;
    try {
      await app.api("/api/game", { method: "POST", body: { fps_enabled: on } });
      toast(on ? "Leitura de FPS ativada. Vale para jogos abertos com o MangoHud." : "Leitura de FPS desativada.");
    } catch (err) { toast(err.message, true); e.target.checked = !on; }
    e.target.disabled = false;
  });

  // agora no PC: janela em foco + mídia
  $("#ov-now [data-body]", el).innerHTML = `
    <div class="now-focus" data-focus></div>
    <div class="now-media" data-media hidden>
      <img class="media-art" data-art alt="" hidden>
      <div class="media-info"><div class="media-title" data-mtitle></div><div class="media-sub" data-msub></div>
        <div class="media-prog" data-mprog><div class="meter"><i></i></div><div class="media-time" data-mtime></div></div></div>
      <div class="media-btns" data-mbtns></div>
    </div>
    <div class="note" data-mnote></div>`;
  $("[data-mbtns]", el).addEventListener("click", async e => {
    const b = e.target.closest("button[data-op]");
    if (!b) return;
    b.disabled = true;
    try { await app.api("/api/media", { method: "POST", body: { player: b.dataset.player, op: b.dataset.op } }); }
    catch (err) { toast(err.message, true); }
    b.disabled = false;
  });
  $("#ov-steam", el).hidden = true;

  $("#ov-mem [data-body]", el).innerHTML = `
    <div class="hero-row"><div class="hero num" data-v="pct">—</div></div>
    <div class="spark" data-spark="mem"></div><div data-meters></div>`;
  sparks.mem = new Spark($("[data-spark=mem]", el), { series: [{ key: "memory.percent" }], min: 0, max: 100, format: v => pct(v, 1) });

  $("#ov-net [data-body]", el).innerHTML = `
    <div class="stats" data-stats style="margin-top:0"></div>
    <div class="spark" data-spark="net"></div>
    <div class="spark-legend"><span><i style="background:var(--series-1)"></i>Download</span><span><i style="background:var(--series-2)"></i>Upload</span></div>
    <div data-ifaces class="note"></div>`;
  sparks.net = new Spark($("[data-spark=net]", el), {
    series: [{ key: "network.rx", label: "↓", color: "--series-1" }, { key: "network.tx", label: "↑", color: "--series-2" }],
    min: 0, minMax: 1024 * 50, format: rate,
  });

  $("#ov-disk [data-body]", el).innerHTML = `<div class="stats" data-stats style="margin-top:0"></div><div data-meters></div>`;

  const top = $("#ov-top", el);
  $("[data-chip]", top).innerHTML = `<div class="seg" role="group" aria-label="Ordenar por">
    <button data-m="cpu" aria-pressed="true">CPU</button><button data-m="mem" aria-pressed="false">RAM</button><button data-m="gpu" aria-pressed="false">GPU</button></div>`;
  top.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
    topMode = b.dataset.m;
    top.querySelectorAll(".seg button").forEach(x => x.setAttribute("aria-pressed", x === b));
    if (app.snapshot) update(app.snapshot);
  }));
  $("[data-body]", top).innerHTML = `<ul class="top-list" data-list></ul>
    <div style="margin-top:10px"><a class="btn btn-sm" href="#processes">Ver todos os processos →</a></div>`;
  $("[data-list]", top).addEventListener("click", e => {
    const li = e.target.closest("li[data-pid]");
    if (!li) return;
    const pid = Number(li.dataset.pid);
    app.api("/api/processes").then(r => {
      const p = r.processes.find(p => p.pid === pid);
      if (p) openProcessSheet(app, p);
    });
  });

  app.onSnapshot(update);
}

function stat(label, value) {
  return `<div><div class="stat-label">${label}</div><div class="stat-value">${value}</div></div>`;
}

function update(snap) {
  if (!root.classList.contains("active")) return;
  const d = snap.data;
  for (const s of Object.values(sparks)) s.setData(app.history);

  // CPU
  const c = d.cpu;
  if (c) {
    const el = $("#ov-cpu", root);
    $("[data-sub]", el).textContent = `${c.model} · ${c.cores}C/${c.threads}T`;
    $("[data-chip]", el).innerHTML = tempChip(c.temp);
    $("[data-v=total]", el).innerHTML = `${c.total.toFixed(0)}<small>%</small>`;
    const cores = $("[data-cores]", el);
    cores.style.setProperty("--n", c.per_core.length);
    if (cores.children.length !== c.per_core.length) cores.innerHTML = c.per_core.map(() => `<div class="core"><i></i></div>`).join("");
    c.per_core.forEach((v, i) => {
      const k = cores.children[i];
      k.firstChild.style.height = `${v}%`;
      k.title = `Thread ${i}: ${v.toFixed(0)}%${c.freq.per_core[i] ? ` · ${mhz(c.freq.per_core[i])}` : ""}`;
    });
    $("[data-stats]", el).innerHTML =
      stat("Clock médio", mhz(c.freq.avg)) + stat("Temperatura", c.temp != null ? `${c.temp.toFixed(1)} °C` : "—") +
      stat("Load 1/5/15", c.load.map(x => x.toFixed(2)).join(" ")) +
      stat("Usuário / Sist.", `${c.breakdown.user.toFixed(0)}% / ${c.breakdown.system.toFixed(0)}%`);
  }

  // GPUs
  (d.gpu?.gpus || []).forEach((g, i) => {
    const el = $(`#ov-gpu-${i}`, root);
    if (!el) return;
    $("[data-sub]", el).textContent = g.name;
    $("[data-chip]", el).innerHTML = tempChip(g.temp);
    $("[data-v=busy]", el).innerHTML = g.busy != null ? `${Math.round(g.busy)}<small>%</small>` : `<span class="muted">n/d</span>`;
    let m = "";
    if (g.vram?.total) m += meter(g.integrated ? "VRAM (reservada)" : "VRAM", g.vram.used, g.vram.total, `${bytes(g.vram.used)} / ${bytes(g.vram.total)}`);
    if (g.gtt?.total) m += meter("GTT (memória compartilhada)", g.gtt.used, g.gtt.total, `${bytes(g.gtt.used)} / ${bytes(g.gtt.total)}`);
    $("[data-meters]", el).innerHTML = m;
    let st = "";
    if (g.temp != null) st += stat(g.temp_source === "cpu_die" ? "Temp. (die)" : "Temperatura", `${g.temp.toFixed(1)} °C`);
    for (const [k, v] of Object.entries(g.clocks || {})) st += stat(`Clock ${k}`, mhz(v));
    if (g.power != null) st += stat("Potência", `${g.power.toFixed(1)} W`);
    if (g.fan != null) st += stat("Fan", `${Math.round(g.fan)} RPM`);
    if (g.fan_pct != null) st += stat("Fan", pct(g.fan_pct));
    $("[data-stats]", el).innerHTML = st;
    $("[data-note]", el).textContent = [...(g.notes || []), g.busy_source === "fdinfo" ? "Uso estimado pelos processos visíveis." : ""].filter(Boolean).join(" ");
  });

  // Jogo
  const g = d.game;
  if (g) {
    const el = $("#ov-game", root);
    const cb = $("[data-fps]", el);
    if (!cb.disabled) cb.checked = g.enabled;
    const name = g.game?.name;
    $("[data-sub]", el).textContent = name ? name : "Nenhum jogo aberto";
    const sp = $("[data-spark=fps]", el);
    sp.hidden = !(g.enabled && g.live);
    let body = "", note = "";
    if (g.enabled && g.live) {
      body = `<div class="hero-row"><div class="hero num">${g.fps != null ? Math.round(g.fps) : "—"}<small>FPS</small></div></div>
        <div class="stats">${stat("1% low", g.low1 != null ? `${Math.round(g.low1)} FPS` : "—")}${stat("Frametime", g.frametime != null ? `${g.frametime.toFixed(1)} ms` : "—")}
        ${g.gpu_load != null ? stat("GPU", pct(g.gpu_load)) : ""}${g.cpu_load != null ? stat("CPU", pct(g.cpu_load)) : ""}</div>`;
      note = "Medido pelo MangoHud dentro do jogo. 1% low aproximado (últimos 60 s).";
    } else if (name) {
      body = `<div class="hero-row"><div class="hero" style="font-size:22px">${esc(name)}</div></div>`;
      note = g.enabled
        ? `Sem leitura de FPS: abra o jogo com o MangoHud (Steam → Propriedades → Opções de inicialização: <code>mangohud %command%</code>).`
        : `Ative "FPS" para ver os quadros por segundo (precisa do MangoHud).`;
    } else {
      body = `<p class="muted" style="margin:4px 0">Nenhum jogo rodando.</p>`;
      note = g.enabled ? "A leitura de FPS está ligada e começa quando um jogo abrir com o MangoHud."
        : "Leitura de FPS desligada (não gasta nada). Ative em \"FPS\" acima.";
    }
    if (g.enabled && g.config_ok === false) note += ` ⚠ O MangoHud.conf foi alterado por outro programa (ex.: Goverlay); desative e ative de novo.`;
    $("[data-game]", el).innerHTML = body;
    $("[data-game-note]", el).innerHTML = note;
  }

  if (d.focus || d.media) updateNow(d.focus, d.media, snap.ts);
  if (d.steam) updateSteam(d.steam, d.network);

  // Memória
  const mm = d.memory;
  if (mm) {
    const el = $("#ov-mem", root);
    $("[data-sub]", el).textContent = `${bytes(mm.used)} de ${bytes(mm.total)}`;
    $("[data-v=pct]", el).innerHTML = `${mm.percent.toFixed(0)}<small>%</small>`;
    $("[data-meters]", el).innerHTML =
      meter("Em uso", mm.used, mm.total, bytes(mm.used)) +
      meter("Cache/buffers", mm.cached + mm.buffers, mm.total, bytes(mm.cached + mm.buffers)) +
      (mm.swap.total ? meter("Swap", mm.swap.used, mm.swap.total, `${bytes(mm.swap.used)} / ${bytes(mm.swap.total)}`) : "");
  }

  // Top
  const p = d.processes;
  if (p) {
    const el = $("#ov-top", root);
    $("[data-sub]", el).textContent = `${p.count} processos · ${p.threads} threads`;
    const list = p[`top_${topMode}`] || [];
    const fmt = x => topMode === "mem" ? bytes(x.mem) : pct(x[topMode], 1);
    const max = Math.max(...list.map(x => (topMode === "mem" ? x.mem : x[topMode]) || 0), topMode === "mem" ? 1 : 1);
    $("[data-list]", el).innerHTML = list.length ? list.map(x => {
      const v = (topMode === "mem" ? x.mem : x[topMode]) || 0;
      return `<li data-pid="${x.pid}" style="cursor:pointer"><span class="name">${esc(x.name)} <span class="muted">· ${x.pid}</span></span>
        <span class="bar-cell"><span class="mini"><i style="width:${(v / max) * 100}%"></i></span><span class="val">${fmt(x)}</span></span></li>`;
    }).join("") : `<li class="muted">${topMode === "gpu" && p.gpu_visibility !== "all" ? "Nenhum uso de GPU visível (só processos do seu usuário sem root)." : "Nada consumindo agora."}</li>`;
  }

  // Rede
  const n = d.network;
  if (n) {
    const el = $("#ov-net", root);
    $("[data-stats]", el).innerHTML = stat("Download", rate(n.rx)) + stat("Upload", rate(n.tx));
    $("[data-ifaces]", el).innerHTML = n.interfaces.filter(i => i.up).map(i => `${esc(i.name)}: ${esc(i.ipv4.join(", ") || "sem IPv4")}`).join(" · ");
  }

  // Discos
  const dk = d.disks;
  if (dk) {
    const el = $("#ov-disk", root);
    $("[data-stats]", el).innerHTML = stat("Leitura", rate(dk.read)) + stat("Escrita", rate(dk.write));
    $("[data-meters]", el).innerHTML = dk.partitions.map(x =>
      meter(`${x.mount} (${x.fs})`, x.used, x.total, `${bytes(x.used)} / ${bytes(x.total)}`)).join("");
  }
}

// ---------- agora no PC ----------
const mmss = s => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(s % 60).padStart(2, "0")}`; };
const sinceTxt = t => { const s = Date.now() / 1000 - t; return s < 60 ? "agora" : s < 3600 ? `há ${Math.floor(s / 60)} min` : `há ${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`; };
const STATUS = { playing: ["good", "Tocando"], paused: ["", "Pausado"], stopped: ["", "Parado"] };

function artSrc(p) {
  if (!p.art) return null;
  if (/^https?:\/\//.test(p.art)) return p.art;
  if (p.art.startsWith("file://")) return `/api/media/art?player=${encodeURIComponent(p.id)}&v=${encodeURIComponent(p.art.slice(-40))}`;
  return null;
}

function updateNow(f, m, ts) {
  const el = $("#ov-now", root);
  // janela em foco
  const fe = $("[data-focus]", el);
  const w = f?.window;
  let sub = "";
  if (!f) fe.innerHTML = "";
  else if (!f.session) fe.innerHTML = `<div class="stat-label">Janela em foco</div><p class="muted" style="margin:2px 0">Ninguém logado no PC.</p>`;
  else if (f.locked) fe.innerHTML = `<div class="stat-label">Janela em foco</div><p style="margin:2px 0">🔒 Sessão bloqueada</p>`;
  else if (w) {
    const appName = w.desktop || w.cls || w.process || "?";
    fe.innerHTML = `<div class="stat-label">Janela em foco</div>
      <div class="focus-title" title="${esc(w.caption)}">${esc(w.caption || appName)}</div>
      <div class="focus-sub">${esc(appName)}${w.process && w.process !== appName ? ` · ${esc(w.process)}` : ""}${w.pid ? ` · PID ${w.pid}` : ""}${f.since ? ` · ${sinceTxt(f.since)}` : ""}${w.fullscreen ? ` <span class="chip">tela cheia</span>` : ""}</div>`;
    sub = appName;
  } else fe.innerHTML = `<div class="stat-label">Janela em foco</div><p class="muted" style="margin:2px 0">${f.source ? "Nenhuma janela em foco." : "Não disponível neste ambiente (precisa de KDE ou X11)."}</p>`;

  // mídia
  const me = $("[data-media]", el), note = $("[data-mnote]", el);
  const p = m?.current;
  me.hidden = !p;
  if (!m) note.textContent = "";
  else if (!p) note.innerHTML = m.players?.length ? `Player aberto (${esc(m.players.map(x => x.app).join(", "))}), nada tocando.` : "Nada tocando.";
  else {
    note.innerHTML = m.players.length > 1 ? `Outros players: ${esc(m.players.filter(x => x.id !== p.id).map(x => `${x.app} (${STATUS[x.status]?.[1].toLowerCase() || x.status})`).join(", "))}` : "";
    const img = $("[data-art]", el), src = artSrc(p);
    if (src && img.getAttribute("src") !== src) { img.hidden = false; img.src = src; img.onerror = () => { img.hidden = true; }; }
    else if (!src) img.hidden = true;
    $("[data-mtitle]", el).textContent = p.title;
    $("[data-msub]", el).innerHTML = `${esc([p.artist, p.album].filter(Boolean).join(" · "))}
      <span class="media-app">${esc(p.app)} <span class="chip ${STATUS[p.status]?.[0] || ""}">${STATUS[p.status]?.[1] || p.status}</span></span>`;
    const prog = $("[data-mprog]", el);
    prog.hidden = !p.length;
    if (p.length) {
      const pos = Math.min(p.length, (p.position || 0) + (p.status === "playing" ? Math.max(0, ts - p.at) * (p.rate || 1) : 0));
      prog.querySelector("i").style.width = `${(pos / p.length) * 100}%`;
      $("[data-mtime]", el).textContent = `${mmss(pos)} / ${mmss(p.length)}`;
    }
    const btns = $("[data-mbtns]", el);
    const key = `${p.id}|${p.status}|${JSON.stringify(p.can)}`;
    if (app.info.features.control && btns.dataset.key !== key) {
      btns.dataset.key = key;
      const pid = esc(p.id);
      btns.innerHTML = `${p.can.previous ? `<button class="btn btn-sm" data-op="previous" data-player="${pid}" title="Anterior" aria-label="Anterior">⏮</button>` : ""}
        ${p.can.play_pause ? `<button class="btn btn-sm" data-op="play_pause" data-player="${pid}" title="${p.status === "playing" ? "Pausar" : "Tocar"}" aria-label="${p.status === "playing" ? "Pausar" : "Tocar"}">${p.status === "playing" ? "⏸" : "▶"}</button>` : ""}
        ${p.can.next ? `<button class="btn btn-sm" data-op="next" data-player="${pid}" title="Próxima" aria-label="Próxima">⏭</button>` : ""}`;
    }
    if (!sub) sub = `${p.app}: ${p.title}`;
  }
  $("[data-sub]", el).textContent = p ? `♪ ${p.title}${p.artist ? ` · ${p.artist}` : ""}` : sub;
}

// ---------- downloads da Steam ----------
const DL_STATUS = { running: ["good", "Baixando"], paused: ["warning", "Pausado"], queued: ["", "Na fila"] };
function eta(s) {
  if (s == null) return "";
  if (s < 60) return "menos de 1 min";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

function updateSteam(st, net) {
  const el = $("#ov-steam", root);
  const list = st.downloads || [];
  el.hidden = !list.length;
  if (!list.length) return;
  const active = list.filter(x => x.status === "running");
  $("[data-sub]", el).textContent = active.length ? `${active.length} baixando${list.length > active.length ? ` · ${list.length - active.length} na fila/pausado` : ""}` : `${list.length} na fila/pausado`;
  $("[data-chip]", el).innerHTML = active.length ? `<span class="chip good">${rate(active.reduce((a, x) => a + (x.rate || 0), 0) || null)}</span>` : "";
  $("[data-body]", el).innerHTML = list.map(x => {
    const [cls, lbl] = DL_STATUS[x.status] || ["", x.status];
    const phase = x.phase && x.phase !== "baixando" ? x.phase : null;
    const label = `${esc(x.name)} <span class="chip ${cls}">${phase ? phase[0].toUpperCase() + phase.slice(1) : lbl}</span>`;
    const staging = phase === "instalando" && x.stage_total;
    const v = staging ? x.stage_done : x.done, t = staging ? x.stage_total : x.total;
    const txt = t ? `${pct(v / t * 100, 1)} · ${bytes(v)} / ${bytes(t)}` : "tamanho ainda desconhecido";
    const extra = [x.rate ? rate(x.rate) : "", x.eta ? `faltam ~${eta(x.eta)}` : ""].filter(Boolean).join(" · ");
    return `<div class="dl-item">${meter("", v || 0, t || 0, txt).replace('<span></span>', `<span class="dl-name">${label}</span>`)}
      ${extra ? `<div class="dl-extra">${extra}</div>` : ""}</div>`;
  }).join("") + `<p class="note">Lido dos arquivos da Steam (que atualizam o progresso em saltos). A velocidade é o tráfego do processo da Steam.</p>`;
}

export default { id: "overview", title: "Visão geral", icon: "▦", mount, show: () => app.snapshot && update(app.snapshot) };
