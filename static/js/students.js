/* Students page: list, search, create, edit, deactivate, Excel import. */

const StudentsPage = (() => {
  let page = 1;
  const pageSize = 15;

  function filters() {
    return {
      q: document.getElementById("q").value.trim(),
      class: document.getElementById("f-class").value,
      status: document.getElementById("f-status").value,
    };
  }

  async function load() {
    const f = filters();
    const params = new URLSearchParams({ page, page_size: pageSize, ...f });
    const tbody = document.getElementById("rows");
    tbody.innerHTML = App.skeletonRows(6, 8);
    try {
      const data = await App.api(`/api/v1/students?${params}`);
      document.getElementById("count").textContent = `${data.total} student(s)`;
      document.getElementById("page-info").textContent = `Page ${data.page} of ${Math.max(1, Math.ceil(data.total / data.page_size))}`;
      tbody.innerHTML = data.items.length ? data.items.map(rowHtml).join("")
        : `<tr><td colspan="8">${App.emptyState("◉", "No students found", "Add a student or adjust the filters.")}</td></tr>`;
      bindRowButtons();
    } catch (e) { App.toast(e.message, "error"); tbody.innerHTML = ""; }
  }

  function rowHtml(s) {
    return `<tr>
      <td><strong>${App.esc(s.student_code)}</strong></td>
      <td>${App.esc(s.full_name)}</td>
      <td>${App.esc(s.father_name || "–")}</td>
      <td>${App.esc(s.class_name)}</td>
      <td>${App.esc(s.section || "–")}</td>
      <td>${App.esc(s.mobile || "–")}</td>
      <td>${App.pill(s.status, s.status === "active" ? "active" : "inactive")}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-ghost btn-sm" data-edit="${s.id}">Edit</button>
        ${s.status === "active"
          ? `<button class="btn btn-danger btn-sm" data-deactivate="${s.id}">Deactivate</button>`
          : `<button class="btn btn-ghost btn-sm" data-activate="${s.id}">Activate</button>`}
      </td></tr>`;
  }

  function bindRowButtons() {
    document.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => openEditor(+b.dataset.edit));
    document.querySelectorAll("[data-deactivate]").forEach(b => b.onclick = async () => {
      const s = await App.api(`/api/v1/students/${b.dataset.deactivate}`);
      if (await App.confirmDialog("Deactivate student",
          `Deactivate ${s.full_name} (${s.student_code})? Their record is kept for history.`,
          "Deactivate")) {
        await App.api(`/api/v1/students/${s.id}`, { method: "PATCH", body: { status: "inactive" } });
        App.toast("Student deactivated", "success"); load();
      }
    });
    document.querySelectorAll("[data-activate]").forEach(b => b.onclick = async () => {
      await App.api(`/api/v1/students/${b.dataset.activate}`, { method: "PATCH", body: { status: "active" } });
      App.toast("Student reactivated", "success"); load();
    });
  }

  function fieldHtml(id, label, value = "", extra = "") {
    return `<div class="field"><label for="${id}">${label}</label>
      <input type="text" id="${id}" value="${App.esc(value)}" ${extra}></div>`;
  }

  async function openEditor(id = null) {
    let s = null;
    if (id) s = await App.api(`/api/v1/students/${id}`);
    const body = `
      ${fieldHtml("f-name", "Full name *", s?.full_name || "")}
      ${fieldHtml("f-father", "Father / guardian name", s?.father_name || "")}
      <div class="form-row">
        ${fieldHtml("f-class2", "Class *", s?.class_name || "")}
        ${fieldHtml("f-section", "Section", s?.section || "")}
      </div>
      ${fieldHtml("f-mobile", "Mobile / WhatsApp", s?.mobile || "", 'placeholder="03XXXXXXXXX"')}
      ${s ? `<div class="field"><label>Status</label>
        <select id="f-status2">
          <option value="active" ${s.status === "active" ? "selected" : ""}>Active</option>
          <option value="inactive" ${s.status === "inactive" ? "selected" : ""}>Inactive</option>
        </select></div>` : ""}`;
    App.openModal(id ? "Edit student" : "Add student", body, {
      submitLabel: id ? "Save changes" : "Add student",
      onSubmit: async () => {
        const payload = {
          full_name: document.getElementById("f-name").value,
          father_name: document.getElementById("f-father").value,
          class_name: document.getElementById("f-class2").value,
          section: document.getElementById("f-section").value,
          mobile: document.getElementById("f-mobile").value,
        };
        if (s) payload.status = document.getElementById("f-status2").value;
        try {
          if (id) await App.api(`/api/v1/students/${id}`, { method: "PATCH", body: payload });
          else await App.api("/api/v1/students", { method: "POST", body: payload });
          App.closeModal(); App.toast(id ? "Student updated" : "Student added", "success");
          load(); loadClasses();
        } catch (e) { App.toast(e.message, "error"); }
      },
    });
  }

  async function loadClasses() {
    try {
      const classes = await App.api("/api/v1/students/classes");
      const sel = document.getElementById("f-class");
      const cur = sel.value;
      sel.innerHTML = `<option value="">All classes</option>` +
        classes.map(c => `<option ${c === cur ? "selected" : ""}>${App.esc(c)}</option>`).join("");
    } catch { /* non-fatal */ }
  }

  function init() {
    document.getElementById("btn-add").onclick = () => openEditor();
    document.getElementById("btn-search").onclick = () => { page = 1; load(); };
    document.getElementById("q").addEventListener("keydown", e => { if (e.key === "Enter") { page = 1; load(); } });
    document.getElementById("prev").onclick = () => { if (page > 1) { page--; load(); } };
    document.getElementById("next").onclick = () => { page++; load(); };
    document.getElementById("file-import").addEventListener("change", async e => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        App.toast("Importing…");
        const res = await App.apiUpload("/api/v1/uploads/students", file);
        App.toast(`Imported ${res.created} student(s)${res.skipped ? `, ${res.skipped} skipped` : ""}${res.duplicates ? `, ${res.duplicates} duplicate(s) ignored` : ""}`, "success");
        if (res.errors?.length) App.toast(res.errors.slice(0, 3).join("; "), "error", 8000);
        page = 1; load(); loadClasses();
      } catch (err) { App.toast(err.message, "error"); }
      e.target.value = "";
    });
    loadClasses(); load();
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", StudentsPage.init);
