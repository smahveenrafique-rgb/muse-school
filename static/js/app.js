/* Shared frontend helpers: API client, toasts, modal, confirm dialog. */

const App = (() => {
  async function api(path, { method = "GET", body = null } = {}) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body !== null) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (res.status === 401) { window.location.href = "/login"; throw new Error("signed out"); }
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
    return data;
  }

  async function apiUpload(path, file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(path, { method: "POST", body: form });
    if (res.status === 401) { window.location.href = "/login"; throw new Error("signed out"); }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.detail) || `Upload failed (${res.status})`);
    return data;
  }

  function toast(msg, kind = "info", ms = 4200) {
    const box = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function emptyState(icon, title, hint) {
    return `<div class="empty"><div class="big">${icon}</div><h3>${esc(title)}</h3><p class="muted">${esc(hint)}</p></div>`;
  }

  function skeletonRows(n, cols) {
    return Array.from({ length: n }, () =>
      `<tr>${Array.from({ length: cols }, () => `<td><div class="skeleton">&nbsp;</div></td>`).join("")}</tr>`).join("");
  }

  function openModal(title, bodyHtml, { onSubmit = null, submitLabel = "Save" } = {}) {
    closeModal();
    const bd = document.createElement("div");
    bd.className = "modal-backdrop";
    bd.id = "modal-backdrop";
    bd.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <header><h2>${esc(title)}</h2><button class="x" aria-label="Close">&times;</button></header>
        <div class="body">${bodyHtml}</div>
        <footer>
          <button class="btn btn-ghost" data-act="cancel">Cancel</button>
          ${onSubmit ? `<button class="btn btn-primary" data-act="submit">${esc(submitLabel)}</button>` : ""}
        </footer>
      </div>`;
    document.body.appendChild(bd);
    bd.querySelector(".x").onclick = closeModal;
    bd.querySelector('[data-act=cancel]').onclick = closeModal;
    bd.addEventListener("click", e => { if (e.target === bd) closeModal(); });
    if (onSubmit) bd.querySelector('[data-act=submit]').onclick = onSubmit;
    return bd;
  }

  function closeModal() {
    document.getElementById("modal-backdrop")?.remove();
  }

  function confirmDialog(title, message, confirmLabel = "Confirm") {
    return new Promise(resolve => {
      const bd = openModal(title, `<p>${esc(message)}</p>`, {
        submitLabel: confirmLabel,
        onSubmit: () => { closeModal(); resolve(true); },
      });
      bd.querySelector('[data-act=cancel]').onclick = () => { closeModal(); resolve(false); };
      bd.querySelector(".x").onclick = () => { closeModal(); resolve(false); };
    });
  }

  function setBusy(btn, busy, label = "Working…") {
    if (!btn) return;
    if (busy) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${esc(label)}`; }
    else { btn.disabled = false; btn.innerHTML = btn.dataset.label || btn.innerHTML; }
  }

  function pill(text, kind = "info") {
    return `<span class="pill ${esc(kind)}">${esc(text)}</span>`;
  }

  // Academic session label (April–March session year used by the school).
  function sessionLabel() {
    const now = new Date();
    const y = now.getFullYear();
    const start = now.getMonth() + 1 >= 4 ? y : y - 1;
    return `Session ${start}–${String(start + 1).slice(2)}`;
  }

  // Engine/AI status pill — controlled "Not Connected" state, never fake data.
  function enginePill(status) {
    const kind = { ok: "ok", stub: "stub", degraded: "degraded",
                   unreachable: "unreachable" }[status] || "info";
    const label = { ok: "Connected", stub: "Stub mode",
                    degraded: "Degraded", unreachable: "Not Connected" }[status] || status;
    return pill(label, kind);
  }

  // Show a stub-mode banner once per page load so offline doubles are never
  // mistaken for live services. Exposes a promise for page scripts.
  const readyPromise = api("/api/v1/ready").then(r => {
    if (r && r.stub_mode) {
      const b = document.getElementById("stub-banner");
      if (b) {
        b.textContent = "Stub mode — one or more engines or the AI are offline doubles. Values shown are for development only, never production data.";
        b.classList.add("show");
      }
    }
    return r;
  }).catch(() => null);

  return { api, apiUpload, toast, esc, emptyState, skeletonRows, openModal, closeModal, confirmDialog, setBusy, pill, sessionLabel, enginePill, ready: () => readyPromise };
})();
