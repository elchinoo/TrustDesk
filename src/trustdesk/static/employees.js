import { api } from "/kit/api.js";
import { $, field, fill, fmtDay, h, toast } from "/kit/ui.js";
import { showEmployeeRequests } from "/static/requests.js";

let wired = false;
let query = "";

export async function initEmployees() {
  if (!wired) {
    wired = true;
    $("emp-add").addEventListener("click", () => showForm(null));
    $("emp-q").addEventListener("input", (e) => { clearTimeout(initEmployees.t); initEmployees.t = setTimeout(() => { query = e.target.value.trim(); load(); }, 300); });
  }
  await load();
}

async function load() {
  try {
    const p = new URLSearchParams({ limit: "200" });
    if (query) p.set("q", query);
    const d = await api("/api/v1/employees?" + p);
    $("emp-body").replaceChildren(...d.items.map((e) => h("tr", {},
      h("td", {}, e.employee_no),
      h("td", { class: "title" }, e.name, h("div", { class: "muted small-text" }, e.email)),
      h("td", {}, [e.title, e.level].filter(Boolean).join(" · ")),
      h("td", {}, e.department),
      h("td", { class: "col-added" }, e.manager_no || ""),
      h("td", { class: "col-added" }, e.hire_date ? fmtDay(e.hire_date) : ""),
      h("td", {}, h("span", { class: "badge " + (e.status === "active" ? "ok" : "plain") }, e.status)),
      h("td", { class: "num" }, `${e.open_requests} / ${e.requests}`),
      h("td", { class: "actions" }, h("div", { class: "btns" },
        h("button", { class: "secondary small", type: "button", onclick: () => showEmployeeRequests(e.employee_no) }, "Requests"),
        h("button", { class: "secondary small", type: "button", onclick: () => showForm(e) }, "Edit"))))));
    $("emp-table").hidden = d.items.length === 0;
    $("emp-empty").hidden = d.items.length !== 0;
    $("emp-total").textContent = `${d.items.length} of ${d.total}`;
  } catch (err) { toast(err.message, true); }
}

function showForm(e) {
  const box = $("emp-form");
  const v = (k) => (e && e[k]) || "";
  const f = {
    employee_no: h("input", { maxlength: "40", required: true, value: v("employee_no"), disabled: !!e }),
    name: h("input", { maxlength: "120", required: true, value: v("name") }),
    email: h("input", { type: "email", maxlength: "254", required: true, value: v("email") }),
    department: h("input", { maxlength: "80", value: v("department") }),
    title: h("input", { maxlength: "80", value: v("title") }),
    level: h("input", { maxlength: "40", value: v("level"), placeholder: "e.g. L3" }),
    country: h("input", { maxlength: "2", value: v("country"), placeholder: "US" }),
    manager_no: h("input", { maxlength: "40", value: v("manager_no"), placeholder: "employee number" }),
    hire_date: h("input", { type: "date", value: v("hire_date") ? String(e.hire_date).slice(0, 10) : "" }),
  };
  const status = h("select", {}, h("option", { value: "active" }, "active"), h("option", { value: "inactive" }, "inactive"));
  status.value = e ? e.status : "active";
  const err = h("p", { class: "error", role: "alert" });
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    const body = {};
    for (const [k, el] of Object.entries(f)) if (!el.disabled) body[k] = el.value.trim() || null;
    body.status = status.value;
    for (const k of ["department", "title", "level"]) if (body[k] == null) body[k] = "";
    try {
      if (e) await api("/api/v1/employees/" + encodeURIComponent(e.employee_no), { method: "PATCH", json: body });
      else await api("/api/v1/employees", { method: "POST", json: body });
      toast(e ? "Employee updated" : "Employee added");
      box.hidden = true;
      load();
    } catch (ex) { err.textContent = ex.message; }
  } },
  h("div", { class: "grid3" }, field("Employee number", f.employee_no), field("Name", f.name), field("Email", f.email)),
  h("div", { class: "grid3" }, field("Department", f.department), field("Job title", f.title), field("Level", f.level)),
  h("div", { class: "grid3" }, field("Country (ISO code)", f.country), field("Manager", f.manager_no), field("Hire date", f.hire_date)),
  field("Status", status, "Inactive employees cannot submit requests; their history is kept."),
  err,
  h("div", { class: "btns" }, h("button", { class: "primary", type: "submit" }, e ? "Save" : "Add employee"),
    h("button", { class: "secondary", type: "button", onclick: () => { box.hidden = true; } }, "Cancel")));
  fill(box, h("h2", {}, e ? `Edit ${e.name}` : "Add employee"), form);
  box.hidden = false;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
