// Painel de análise de processo (vivo ou encerrado): o que é, de onde veio, sinais de atenção.
import { $, bytes, duration, esc, toast } from "./ui.js";

const LEVEL = {
  alto: { cls: "critical", label: "Alto" },
  medio: { cls: "warning", label: "Médio" },
  info: { cls: "", label: "Info" },
};

export function verdict(score, flags = []) {
  if (score >= 50) return { cls: "critical", label: "Suspeito" };
  if (score > 0) return { cls: "warning", label: "Atenção" };
  return flags.length ? { cls: "good", label: "Só informativo" } : { cls: "good", label: "Nenhum sinal" };
}

export function flagList(flags) {
  if (!flags.length) return `<p class="muted" style="margin:6px 0">Nenhum ponto de atenção encontrado.</p>`;
  const order = { alto: 0, medio: 1, info: 2 };
  return `<ul class="flags">${[...flags].sort((a, b) => order[a.level] - order[b.level]).map(f =>
    `<li><span class="chip ${LEVEL[f.level].cls}">${LEVEL[f.level].label}</span><span>${esc(f.text)}</span></li>`).join("")}</ul>`;
}

const when = t => (t ? new Date(t * 1000).toLocaleString("pt-BR") : "—");

/** target: {pid, create_time} para processo vivo, ou {id} para registro do histórico */
export async function openInspect(app, target, onChange) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>Analisando…</h2><p class="muted">Lendo informações do processo e calculando o hash do executável.</p>`;
  if (!dlg.open) dlg.showModal();
  let d;
  try {
    d = target.id
      ? await app.api(`/api/processes/record/${encodeURIComponent(target.id)}/inspect`)
      : await app.api(`/api/processes/${target.pid}/inspect${target.create_time ? `?create_time=${target.create_time}` : ""}`);
  } catch (e) {
    dlg.innerHTML = `<h2>Não foi possível analisar</h2><p class="confirm-text">${esc(e.message)}</p>
      <div class="sheet-actions"><button class="btn full" data-close>Fechar</button></div>`;
    dlg.querySelector("[data-close]").onclick = () => dlg.close();
    return;
  }
  render(app, dlg, d, onChange);
}

function render(app, dlg, d, onChange) {
  const p = d.process, i = d.info, f = d.file, pk = d.package_info;
  const v = verdict(d.score, d.flags);
  const row = (k, val) => (val ? `<dt>${k}</dt><dd>${val}</dd>` : "");
  const chain = (d.ancestors || []).map(a => `${esc(a.name)} <span class="muted">${a.pid}</span>`).join(" ← ");

  let what;
  if (d.package) what = `Instalado pelo pacote <b>${esc(d.package)}</b>${pk?.version ? ` <span class="muted">${esc(pk.version)}</span>` : ""}${pk?.description ? `: ${esc(pk.description)}` : "."}`;
  else if (p.container) what = "Roda dentro de um container ou sandbox (Flatpak, Steam Runtime, Docker…).";
  else if (d.exe) what = `Não veio de nenhum pacote${d.package_tool ? ` do ${esc(d.package_tool.replace("-query", ""))}` : ""}: foi instalado manualmente, baixado ou faz parte de outro app.`;
  else what = "Não foi possível identificar o executável.";

  dlg.innerHTML = `
    <div class="insp-head">
      <div style="min-width:0"><h2>${esc(p.name)}</h2>
        <div class="muted">PID ${p.pid} · ${esc(p.user || "?")} · ${i.alive ? "em execução" : "encerrado"}</div></div>
      <span class="chip ${v.cls}">${v.label}</span>
    </div>
    <p class="insp-what">${what}</p>
    ${p.cmd ? `<div class="cmdline">${esc(p.cmd)}</div>` : ""}
    <div class="stat-label" style="margin-top:12px">Pontos de atenção</div>
    ${flagList(d.flags)}
    <div class="stat-label" style="margin-top:12px">Detalhes</div>
    <dl class="kv insp-kv">
      ${row("Executável", d.exe ? `<span class="mono">${esc(d.exe)}</span>${d.deleted ? ` <span class="chip critical">apagado</span>` : ""}${d.exe_guessed ? ` <span class="muted">(deduzido)</span>` : ""}` : "")}
      ${pk?.url ? row("Site", `<a href="${esc(pk.url)}" target="_blank" rel="noopener noreferrer">${esc(pk.url)}</a>`) : ""}
      ${f?.sha256 ? row("SHA-256", `<span class="mono hash" title="${f.sha256}">${f.sha256}</span>${f.note ? `<span class="muted" style="font-size:12px">${esc(f.note)}</span>` : ""}`) : f?.exists && f.hash_error ? row("SHA-256", `<span class="muted">${esc(f.hash_error)}</span>`) : ""}
      ${f?.exists ? row("Arquivo", `${bytes(f.size)} · ${esc(f.owner)} · ${f.mode.replace("0o", "")} · modificado ${when(f.mtime)}`) : f && !f.exists ? row("Arquivo", `<span class="muted">não existe mais no disco</span>`) : ""}
      ${row("Iniciado", when(p.start))}
      ${p.end ? row("Encerrado", `${when(p.end)} · durou ${p.duration < 60 ? `${p.duration.toFixed(1)}s` : duration(p.duration)}`) : ""}
      ${row("Serviço/escopo", p.unit ? `<span class="mono">${esc(p.unit)}</span>` : "")}
      ${row("Origem (pais)", chain)}
      ${row("Pasta atual", i.cwd ? `<span class="mono">${esc(i.cwd)}</span>` : "")}
      ${i.alive ? row("Threads / arquivos", `${i.threads ?? "?"} / ${i.fds ?? "?"}`) : ""}
      ${!i.alive && p.peak_mem ? row("Pico de uso", `CPU ${p.peak_cpu}% · ${bytes(p.peak_mem)}`) : ""}
      ${i.listening?.length ? row("Portas abertas", i.listening.map(l => `${l.port}${l.public ? " <span class='chip warning'>rede</span>" : " <span class='muted'>local</span>"}`).join(", ")) : ""}
      ${i.remote?.length ? row("Conexões", `${i.remote.length}: ${i.remote.slice(0, 6).map(c => `<span class="mono">${esc(c.ip)}:${c.port}</span>`).join(", ")}${i.remote.length > 6 ? "…" : ""}`) : ""}
      ${i.ld_preload ? row("LD_PRELOAD", `<span class="mono">${esc(i.ld_preload)}</span>`) : ""}
    </dl>
    ${d.visibility === "limited" ? `<p class="note">Este processo é de outro usuário. Sem root o painel não vê o executável real, as conexões nem as variáveis de ambiente dele. Para análise completa, instale o serviço com <code>sudo ./install.sh --root</code>.</p>` : ""}
    ${!i.alive ? `<p class="note">Processo encerrado: estes são os dados capturados quando ele rodava, mais a análise do arquivo se ele ainda existir.</p>` : ""}
    <div class="sheet-actions">
      ${f?.sha256 ? `<a class="btn" href="https://www.virustotal.com/gui/file/${f.sha256}" target="_blank" rel="noopener noreferrer" title="Abre o VirusTotal com o hash. O arquivo não é enviado.">Ver no VirusTotal</a>` : ""}
      ${f?.sha256 ? `<button class="btn" data-copy>Copiar hash</button>` : ""}
      ${d.exe ? `<button class="btn full" data-trust>${d.trusted ? "Remover dos confiáveis" : "Marcar como confiável"}</button>` : ""}
      <button class="btn full" data-close>Fechar</button>
    </div>`;

  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-copy]")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(f.sha256); toast("Hash copiado"); }
    catch { toast("Não foi possível copiar (o navegador só permite em HTTPS ou localhost).", true); }
  });
  dlg.querySelector("[data-trust]")?.addEventListener("click", async () => {
    try {
      await app.api("/api/processes/trust", { method: "POST", body: { exe: d.exe, trusted: !d.trusted } });
      d.trusted = !d.trusted;
      toast(d.trusted ? "Marcado como confiável: não aparece mais nos suspeitos." : "Removido dos confiáveis.");
      render(app, dlg, d, onChange);
      onChange?.();
    } catch (e) { toast(e.message, true); }
  });
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); }, { once: true });
}
