/* Results page: tabs per scope, read-only views over the Results Engine,
   AI summary of engine-provided rows, and platform-side query history. */

const ResultsPage = (() => {
  let scope = "CAT-1";
  let lastRows = [];

  function params() {
    return new URLSearchParams({
      scope,
      class: document.getElementById("class-name").value.trim(),
      subject: document.getElementById("subject").value.trim(),
      student_code: document.getElementById("student-code").value.trim(),
    });
  }

  function fmt(n) { return n === null || n === undefined || n === "" ? "–" : n; }

  async function load() {
    if (!document.getElementById("class-name").value.trim()) { App.toast("Enter a class", "error"); return; }
    const btn = document.getElementById("btn-load");
    const tbody = document.getElementById("rows");
    App.setBusy(btn, true, "Loading…");
    tbody.innerHTML = App.skeletonRows(6, 9);
    try {
      const data = await App.api(`/api/v1/results?${params()}`);
      lastRows = data.items;
      tbody.innerHTML = data.items.length ? data.items.map(r => `<tr>
        <td><strong>${App.esc(r.student_code)}</strong><br><span class="muted">${App.esc(r.student_name)}</span></td>
        <td>${App.esc(r.class_name)}</td><td>${App.esc(r.subject)}</td><td>${App.esc(r.exam_type)}</td>
        <td>${fmt(r.marks_obtained)}</td><td>${fmt(r.total_marks)}</td><td>${fmt(r.percentage)}</td>
        <td>${App.esc(r.grade || "–")}</td>
        <td>${r.result ? App.pill(r.result, /pass|promot/i.test(r.result) ? "pass" : "fail") : "–"}</td>
      </tr>`).join("") : `<tr><td colspan="9">${App.emptyState("▤", "No results", "The Results Engine returned no rows for this query.")}</td></tr>`;
      document.getElementById("meta").textContent =
        `${data.items.length} row(s) · source: ${data.source}${data.generated_at ? ` · generated ${data.generated_at}` : ""}`;
    } catch (e) { App.toast(e.message, "error"); tbody.innerHTML = ""; }
    App.setBusy(btn, false);
  }

  function aiContext() {
    // CONSOLIDATED queries get the RESULTS context; per-exam scopes keep theirs.
    return scope === "CONSOLIDATED" ? "RESULTS" : scope;
  }

  async function aiSummary() {
    if (!lastRows.length) { App.toast("Load results first", "error"); return; }
    const btn = document.getElementById("btn-ai-summary");
    const card = document.getElementById("summary-card");
    const box = document.getElementById("ai-summary");
    card.hidden = false;
    App.setBusy(btn, true, "Summarising…");
    // Only engine-provided figures go to the AI; it may only repeat them.
    const rowsText = lastRows.slice(0, 50).map(r =>
      `${r.student_code}: obtained=${fmt(r.marks_obtained)}, total=${fmt(r.total_marks)}, grade=${r.grade || "–"}, result=${r.result || "–"}`).join("\n");
    try {
      const res = await App.api("/api/v1/ai/run", { method: "POST", body: {
        task: "summarize_results",
        context: aiContext(),
        input: `Summarise these engine-provided result rows. Repeat figures exactly as given:\n${rowsText}`,
      }});
      const d = res.data;
      box.innerHTML = `<p>${App.esc(d.summary)}</p>` +
        (d.highlights?.length ? `<p><strong>Highlights</strong></p><ul>${d.highlights.map(h => `<li>${App.esc(h)}</li>`).join("")}</ul>` : "") +
        (d.concerns?.length ? `<p><strong>Concerns</strong></p><ul>${d.concerns.map(c => `<li>${App.esc(c)}</li>`).join("")}</ul>` : "") +
        `<p class="muted">Figures are engine-provided; summary generated via ${App.esc(res.provider)}.</p>`;
    } catch (e) { box.innerHTML = `<div class="banner error">${App.esc(e.message)}</div>`; }
    App.setBusy(btn, false);
  }

  async function loadHistory() {
    const tbody = document.getElementById("history-rows");
    tbody.innerHTML = App.skeletonRows(5, 7);
    try {
      const rows = await App.api("/api/v1/results/history");
      tbody.innerHTML = rows.length ? rows.map(r => `<tr>
        <td class="muted">${App.esc((r.queried_at || "").slice(0, 16).replace("T", " "))}</td>
        <td>${App.esc(r.scope)}</td>
        <td>${App.esc(r.class_name || "–")}</td>
        <td>${App.esc(r.subject || "–")}</td>
        <td>${App.esc(r.student_code || "–")}</td>
        <td>${r.item_count}</td>
        <td class="muted">${App.esc(r.source || "")}</td>
      </tr>`).join("") :
        `<tr><td colspan="7">${App.emptyState("▤", "No queries yet", "Load results from the Results Engine and the query will appear here.")}</td></tr>`;
    } catch (e) { tbody.innerHTML = `<tr><td colspan="7"><div class="banner error">${App.esc(e.message)}</div></td></tr>`; }
  }

  function selectTab(name) {
    document.querySelectorAll("#result-tabs .tab").forEach(t =>
      t.classList.toggle("active", t.dataset.scope === name));
    const history = name === "HISTORY";
    document.getElementById("pane-query").hidden = history;
    document.getElementById("pane-history").hidden = !history;
    if (!history) {
      scope = name;
      const label = name === "FINAL" ? "Final / Annual" : name;
      document.getElementById("scope-label").textContent = label;
    } else {
      loadHistory();
    }
  }

  function init() {
    document.getElementById("btn-load").onclick = load;
    document.getElementById("btn-ai-summary").onclick = aiSummary;
    document.querySelectorAll("#result-tabs .tab").forEach(t =>
      t.onclick = () => selectTab(t.dataset.scope));
  }
  return { init };
})();

document.addEventListener("DOMContentLoaded", ResultsPage.init);
