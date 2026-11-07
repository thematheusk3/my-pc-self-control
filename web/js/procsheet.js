// Painel de detalhes de processo + finalizar (compartilhado entre abas).
import { ic } from "./icons.js";
import { $, bytes, esc, pct, toast } from "./ui.js";
import { openInspect } from "./inspect.js";

export function openProcessSheet(app, proc, onDone) {
  const dlg = $("#sheet");
  const canKill = app.info?.features?.kill && !proc.kernel && !proc.self;
  const started = proc.create_time ? new Date(proc.create_time * 1000).toLocaleString("pt-BR") : "—";
  dlg.innerHTML = `
    <h2>${esc(proc.name)}</h2>
    <div class="muted">PID ${proc.pid} · ${esc(proc.user)} · ${esc(proc.status || "")}</div>
    ${proc.cmd ? `<div class="cmdline">${esc(proc.cmd)}</div>` : ""}
    <dl class="kv">
      <dt>CPU</dt><dd>${pct(proc.cpu, 1)}</dd>
      <dt>Memória</dt><dd>${bytes(proc.mem)}${proc.mem_pct != null ? ` · ${pct(proc.mem_pct, 1)}` : ""}</dd>
      ${proc.gpu != null ? `<dt>GPU</dt><dd>${pct(proc.gpu, 1)}${proc.gpu_mem ? ` · ${bytes(proc.gpu_mem)}` : ""}</dd>` : ""}
      ${proc.threads != null ? `<dt>Threads</dt><dd>${proc.threads}</dd>` : ""}
      ${proc.ppid != null ? `<dt>PID pai</dt><dd>${proc.ppid}</dd>` : ""}
      <dt>Iniciado</dt><dd>${started}</dd>
    </dl>
    ${proc.critical && canKill ? `<p class="fan-alert warning" style="margin:8px 0">${ic("warning")} Processo essencial do sistema: finalizá-lo pode derrubar a sessão gráfica, o som ou a rede.</p>` : ""}
    <div class="confirm-text" hidden></div>
    <div class="sheet-actions">
      ${app.info?.features?.exited ? `<button class="btn full" data-inspect>Analisar processo</button>` : ""}
      ${canKill ? `
        <button class="btn btn-danger" data-sig="TERM">Finalizar</button>
        <button class="btn btn-ghost-danger" data-sig="KILL">Forçar (kill -9)</button>` :
        `<p class="muted full" style="margin:0">${proc.kernel ? "Thread do kernel — não pode ser finalizada." : proc.self ? "Este é o próprio servidor de monitoramento." : "Finalizar processos está desativado."}</p>`}
      <button class="btn full" data-close>Fechar</button>
    </div>`;

  const confirmEl = dlg.querySelector(".confirm-text");
  let armed = null;
  dlg.querySelectorAll("[data-sig]").forEach(btn => btn.addEventListener("click", async () => {
    const sig = btn.dataset.sig;
    if (armed !== sig) {   // primeiro toque arma, segundo confirma
      armed = sig;
      dlg.querySelectorAll("[data-sig]").forEach(b => (b.textContent = b.dataset.sig === "TERM" ? "Finalizar" : "Forçar (kill -9)"));
      btn.textContent = "Toque de novo para confirmar";
      confirmEl.hidden = false;
      confirmEl.textContent = (sig === "KILL"
        ? `Forçar o encerramento de "${proc.name}" imediatamente? Dados não salvos serão perdidos.`
        : `Pedir para "${proc.name}" fechar (como clicar no X)? Ele pode perguntar se você quer salvar. Processos sem janela recebem SIGTERM.`)
        + (proc.critical ? " ⚠ É um processo essencial do sistema." : "");
      return;
    }
    btn.disabled = true;
    try {
      const r = await app.api(`/api/processes/${proc.pid}/signal`, { method: "POST",
        body: { signal: sig, create_time: proc.create_time, confirm_critical: !!proc.critical } });
      toast(sig === "KILL" ? `Forçado: ${proc.name} (PID ${proc.pid})`
        : r.closed_windows ? `Pedido de fechar enviado a ${proc.name} (${r.closed_windows} janela${r.closed_windows > 1 ? "s" : ""})`
        : `Sinal enviado: ${proc.name} (PID ${proc.pid})`);
      dlg.close();
      onDone?.();
    } catch (e) {
      btn.disabled = false;
      if (e.data?.critical && !proc.critical) {   // aberto de uma lista sem a marca de crítico: pede de novo
        proc.critical = true;
        confirmEl.textContent = `⚠ ${e.message}`;
        btn.textContent = "Toque de novo para confirmar";
        return;
      }
      toast(e.message, true);
    }
  }));
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  dlg.querySelector("[data-inspect]")?.addEventListener("click", () => openInspect(app, { pid: proc.pid, create_time: proc.create_time }));
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); }, { once: true });
  dlg.showModal();
}
