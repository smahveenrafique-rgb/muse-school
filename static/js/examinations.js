/* Examinations page: tabs per exam type, job submission to the Exam Engine,
   job status checks, AI outline, and platform-side submission history. */

const ExamsPage = (() => {
  let examType = "CAT-1";

  function jobCard(job) {
    const kind = { queued: "pending", running: "pending", succeeded: "succeeded", failed: "failed" }[job.status] || "info";
    return `<div class="card" style="box-shadow:none">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>${App.esc(job.job_id)}</strong>${App.pill(job.status, kind)}
      </div>
      <p class="muted" style="margin:.5rem 0 0">${App.esc(job.exam_type)} · ${App.esc(job.detail || "")}</p>
      ${job.artifact_url ? `<p><a href="${App.esc(job.artifact_url)}" target="_blank" rel="noopener">Download artifact</a></p>` : ""}
    </div>`;
  }

  async function submitJob() {
    const btn = document.getElementById("btn-submit");
    const payload = {
      exam_type: examType,
      class_name: document.getElementById("class-name").value.trim(),
      subject: document.getElementById("subject").value.trim(),
    };
    if (!payload.class_name || !payload.subject) { App.toast("Class and subject are required", "error"); return; }
    App.setBusy(btn, true, "Submitting…");
    try {
      const job = await App.api("/api/v1/examinations/jobs", { method: "POST", body: payload });
      document.getElementById("job-result").innerHTML = jobCard(job);
      document.getElementById("job-id").value = job.job_id;
      App.toast("Job submitted to the Exam Engine", "success");
    } catch (e) { App.toast(e.message, "error"); }
    App.setBusy(btn, false);
  }

  async function checkStatus() {
    const id = document.getElementById("job-id").value.trim();
    if (!id) { App.toast("Enter a job id", "error"); return; }
    const box = document.getElementById("job-status");
    box.innerHTML = `<span class="spinner"></span> Checking…`;
    try {
      box.innerHTML = jobCard(await App.api(`/api/v1/examinations/jobs/${encodeURIComponent(id)}`));
    } catch (e) { box.innerHTML = `<div class="banner error">${App.esc(e.message)}</div>`; }
  }

  async function draftOutline() {
    const btn = document.getElementById("btn-ai-outline");
    const className = document.getElementById("class-name").value.trim();
    const subject = document.getElementById("subject").value.trim();
    if (!className || !subject) { App.toast("Enter class and subject first", "error"); return; }
    App.setBusy(btn, true, "Asking AI…");
    const box = document.getElementById("ai-output");
    try {
      const res = await App.api("/api/v1/ai/run", { method: "POST", body: {
        task: "draft_exam_outline", context: examType,
        input: `Draft a question-paper outline for class ${className}, subject ${subject}. Recent syllabus topics only. No marks or totals.`,
      }});
      const d = res.data;
      box.innerHTML = `<h3>${App.esc(d.title)}</h3>` +
        d.sections.map(s => `<p><strong>${App.esc(s.heading)}</strong></p><ul>${
          s.points.map(p => `<li>${App.esc(p)}</li>`).join("")}</ul>`).join("") +
        (d.notes ? `<p class="muted">${App.esc(d.notes)}</p>` : "") +
        `<p class="muted">Generated via ${App.esc(res.provider)} — review before sending to the Exam Engine.</p>`;
    } catch (e) { box.innerHTML = `<div class="banner error">${App.esc(e.message)}</div>`; }
    App.setBusy(btn, false);
  }

  async function loadHistory() {
    const tbody = document.getElementById("history-rows");
    tbody.innerHTML = App.skeletonRows(5, 7);
    try {
      const rows = await App.api("/api/v1/examinations/history");
      tbody.innerHTML = rows.length ? rows.map(r => `<tr>
        <td class="muted">${App.esc((r.submitted_at || "").slice(0, 16).replace("T", " "))}</td>
        <td><strong>${App.esc(r.job_id)}</strong></td>
        <td>${App.esc(r.exam_type)}</td>
        <td>${App.esc(r.class_name)}</td>
        <td>${App.esc(r.subject || "–")}</td>
        <td>${App.pill(r.status, r.status === "submitted" ? "pending" : "info")}</td>
        <td><button class="btn btn-ghost" data-job="${App.esc(r.job_id)}">Check status</button></td>
      </tr>`).join("") :
        `<tr><td colspan="7">${App.emptyState("✎", "No submissions yet", "Submit an exam job to the Exam Engine and it will appear here.")}</td></tr>`;
      tbody.querySelectorAll("button[data-job]").forEach(b => b.onclick = async () => {
        try {
          const job = await App.api(`/api/v1/examinations/jobs/${encodeURIComponent(b.dataset.job)}`);
          App.openModal("Job status", jobCard(job));
        } catch (e) { App.toast(e.message, "error"); }
      });
    } catch (e) { tbody.innerHTML = `<tr><td colspan="7"><div class="banner error">${App.esc(e.message)}</div></td></tr>`; }
  }

  function selectTab(name) {
    document.querySelectorAll("#exam-tabs .tab").forEach(t =>
      t.classList.toggle("active", t.dataset.exam === name));
    const history = name === "HISTORY";
    document.getElementById("pane-job").hidden = history;
    document.getElementById("pane-history").hidden = !history;
    if (!history) {
      examType = name;
      document.getElementById("job-exam-label").textContent =
        name === "FINAL" ? "Final / Annual" : name;
    } else {
      loadHistory();
    }
  }

  function init() {
    document.getElementById("btn-submit").onclick = submitJob;
    document.getElementById("btn-status").onclick = checkStatus;
    document.getElementById("btn-ai-outline").onclick = draftOutline;
    document.querySelectorAll("#exam-tabs .tab").forEach(t =>
      t.onclick = () => selectTab(t.dataset.exam));
  }
  return { init };
})();

document.addEventListener("DOMContentLoaded", ExamsPage.init);
