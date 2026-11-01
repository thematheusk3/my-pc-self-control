import { $, bytes, esc, h, meter, mhz, pct, rate, tempChip } from "../ui.js";
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

export default { id: "overview", title: "Visão geral", icon: "▦", mount, show: () => app.snapshot && update(app.snapshot) };
