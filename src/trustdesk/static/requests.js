import { api } from "/kit/api.js";
import { can } from "/kit/session.js";
import { $, ADVICE, badge, COMPLIANCE, field, fill, fmtDateTime, fmtDay, h, money, STATUS, toast } from "/kit/ui.js";

const filters = { status: "open", type: "", q: "", employee_no: "", ai: "" };
const PAGE = 50;
let wired = false;
let types = [];
let items = [];
let total = 0;
let pollTimer = null;
let polls = 0;

export function showEmployeeRequests(employeeNo) {
  Object.assign(filters, { status: "", type: "", q: "", ai: "", employee_no: employeeNo });
  location.hash = "#requests";
}

export async function initRequests() {
  $("req-new").hidden = !can("requests:client");
  if (!wired) {
    wired = true;
    $("req-new").addEventListener("click", openNew);
    $("review-close").addEventListener("click", () => $("review").close());
    $("newreq-close").addEventListener("click", () => $("newreq").close());
    $("req-more").addEventListener("click", () => load(true));
    $("req-q").addEventListener("input", debounce(() => { filters.q = $("req-q").value.trim(); load(); }, 300));
    $("req-status").addEventListener("change", (e) => { filters.status = e.target.value; load(); });
    $("req-type").addEventListener("change", (e) => { filters.type = e.target.value; load(); });
    $("req-ai").addEventListener("change", (e) => { filters.ai = e.target.value; load(); });
    $("req-clear-emp").addEventListener("click", () => { filters.employee_no = ""; load(); });
  }
  if (!types.length) {
    try { types = (await api("/api/v1/request-types")).types; } catch (err) { toast(err.message, true); }
    $("req-type").replaceChildren(h("option", { value: "" }, "All types"), ...types.map((t) => h("option", { value: t.code }, t.label)));
  }
  $("req-status").value = filters.status;
  $("req-type").value = filters.type;
  $("req-ai").value = filters.ai;
  $("req-q").value = filters.q;
  await Promise.all([load(), renderNotice()]);
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

async function renderNotice() {
  const box = $("req-notice");
  try {
    const s = await api("/api/v1/validation/settings");
    box.hidden = s.effective != null;
    fill(box, "AI pre-validation is off: no chat model is configured, so requests arrive without a recommendation. ",
      h("a", { href: "#settings" }, "Add an account with a chat model"), " and choose it under Request validation.");
  } catch { box.hidden = true; }
}

function query(offset) {
  const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
  return p.toString();
}

async function load(more = false) {
  clearTimeout(pollTimer);
  try {
    const [list, counts] = await Promise.all([api("/api/v1/requests?" + query(more ? items.length : 0)), api("/api/v1/requests/counts")]);
    items = more ? [...items, ...list.items] : list.items;
    total = list.total;
    renderCounts(counts);
    renderTable(counts);
    schedulePoll();
  } catch (err) { toast(err.message, true); }
}

function renderCounts(c) {
  const chip = (label, n, patch, tone = "") => h("button", {
    class: "stat " + tone, type: "button",
    onclick: () => { Object.assign(filters, { type: "", q: "", employee_no: "", ai: "" }, patch); initRequests(); },
  }, h("strong", {}, String(n)), h("span", {}, label));
  fill($("req-counts"),
    chip("need a decision", c.by_status.pending_review, { status: "pending_review" }, c.by_status.pending_review ? "hot" : ""),
    chip("flagged by AI", c.ai_flagged, { status: "open", ai: "reject" }, c.ai_flagged ? "hot" : ""),
    chip("validating", c.by_status.submitted, { status: "submitted" }),
    chip("waiting for employee", c.by_status.needs_info, { status: "needs_info" }),
    chip("approved", c.by_status.approved, { status: "approved" }),
    chip("rejected", c.by_status.rejected, { status: "rejected" }));
}

function renderTable(counts) {
  const everything = Object.values(counts.by_status).reduce((a, b) => a + b, 0);
  $("req-onboarding").hidden = everything !== 0;
  $("req-list").hidden = everything === 0;
  if (everything === 0) { renderOnboarding(); return; }
  $("req-employee-filter").hidden = !filters.employee_no;
  $("req-employee-filter-name").textContent = filters.employee_no;
  $("req-body").replaceChildren(...items.map(row));
  $("req-empty").hidden = items.length !== 0;
  $("req-table").hidden = items.length === 0;
  $("req-more").hidden = items.length >= total;
  $("req-total").textContent = `${items.length} of ${total}`;
}

function row(r) {
  const what = r.kind === "expense" ? money(r.amount, r.currency)
    : r.start_date ? `${fmtDay(r.start_date)}${r.end_date && r.end_date !== r.start_date ? " – " + fmtDay(r.end_date) : ""}` : "";
  return h("tr", { class: "clickable", onclick: () => openReview(r.ref) },
    h("td", {}, h("button", { class: "linklike", type: "button", onclick: (e) => { e.stopPropagation(); openReview(r.ref); } }, r.ref)),
    h("td", { class: "title" }, r.employee.name, h("div", { class: "muted small-text" }, r.employee.department || "")),
    h("td", { class: "wrap" }, h("strong", {}, r.type_label), h("div", { class: "muted small-text" }, r.title)),
    h("td", {}, what),
    h("td", {}, r.ai ? badge(ADVICE, r.ai.recommendation) : badge(ADVICE, r.validation_state === "pending" || r.validation_state === "running" ? "" : "manual", "…")),
    h("td", {}, badge(STATUS, r.status)),
    h("td", { class: "col-added" }, fmtDay(r.submitted_at)));
}

function schedulePoll() {
  // While anything is still being validated, keep the list fresh without the user having to reload.
  if (!items.some((r) => r.status === "submitted") || polls > 60) { polls = 0; return; }
  polls += 1;
  pollTimer = setTimeout(() => { if (location.hash.slice(1) === "requests") load(); }, 2500);
}

function renderOnboarding() {
  const btn = h("button", { class: "primary", type: "button" }, "Load demo policies, people and requests");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Loading… (this embeds the policy documents)";
    try {
      const d = await api("/api/v1/demo", { method: "POST" });
      toast(`Loaded ${d.policies} policies, ${d.employees} employees and ${d.requests} requests. AI validation is running.`);
      polls = 0;
      initRequests();
    } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = "Load demo policies, people and requests"; }
  });
  fill($("req-onboarding"),
    h("h2", {}, "Nothing here yet"),
    h("p", { class: "muted" }, "Requests arrive from your client applications through the API (see Connect), or you can add one by hand. To see how TrustDesk works end to end, load the fictional Acme data: HR, travel and expense policies, eleven employees and a mix of compliant and problematic requests, each pre-validated against the policies."),
    h("div", { class: "btns" }, btn, h("button", { class: "secondary", type: "button", onclick: openNew }, "Add a request by hand")));
}

// ---- review window -------------------------------------------------------------------------------
async function openReview(ref) {
  const dlg = $("review");
  review = null;
  fill($("review-body"), h("p", { class: "loading" }, "Loading…"));
  if (!dlg.open) dlg.showModal();
  await renderReview(ref);
}

let reviewPoll = null;
// The review window is built once per request: header, request details and AI card are data regions that the poll
// may redraw; the decision card holds the reviewer's note, so it is rebuilt only when what it shows really changes,
// and then the note, the focus and the caret move to the new textarea.
let review = null;

function reviewShell(ref) {
  const s = { ref, head: h("div", { class: "row" }), left: h("div", {}), ai: h("div", {}), decision: h("div", {}), sig: null, note: null };
  fill($("review-body"), s.head, h("div", { class: "review-cols" }, s.left, h("div", {}, s.ai, s.decision)));
  return s;
}

async function renderReview(ref) {
  clearTimeout(reviewPoll);
  let d;
  try { d = await api("/api/v1/requests/" + encodeURIComponent(ref)); } catch (err) { review = null; fill($("review-body"), h("p", { class: "error" }, err.message)); return; }
  const v = d.validation;
  const working = d.status === "submitted" || d.validation_state === "running" || d.validation_state === "pending";
  if (!review || review.ref !== ref) review = reviewShell(ref);
  fill(review.head, h("div", {}, h("h2", { id: "review-title" }, `${d.ref} · ${d.type_label}`), h("p", { class: "muted small-text" }, `Submitted ${fmtDateTime(d.submitted_at)} · ${d.source || ""}`)),
    badge(STATUS, d.status));
  fill(review.left, requestCard(d), employeeCard(d.employee), timeline(d.events));
  fill(review.ai, working ? h("section", { class: "card ai" }, h("h3", {}, "AI pre-validation"), h("p", { class: "loading" }, "Checking the request against the policies…")) : aiCard(d, v));
  const rec = v && v.state === "ok" ? v.recommendation : null;
  const sig = JSON.stringify([d.status, can("requests:decide"), rec, d.decided_by, d.decided_at, d.decision_note, d.override]);
  if (sig !== review.sig) {
    const old = review.note;
    const kept = old ? { value: old.value, focused: document.activeElement === old, from: old.selectionStart, to: old.selectionEnd } : null;
    const { card, note } = decisionCard(d, v);
    fill(review.decision, card);
    if (note && kept) {
      note.value = kept.value;
      if (kept.focused) { note.focus(); if (kept.from != null) note.setSelectionRange(kept.from, kept.to); }
    }
    review.note = note;
    review.sig = sig;
  }
  if (working) reviewPoll = setTimeout(() => { if ($("review").open) renderReview(ref); }, 2500);
}

function requestCard(d) {
  const facts = [["Title", d.title]];
  if (d.start_date) facts.push(["Dates", `${fmtDay(d.start_date)}${d.end_date && d.end_date !== d.start_date ? " – " + fmtDay(d.end_date) : ""}${d.working_days != null ? ` (${d.working_days} working days)` : ""}`]);
  if (d.amount != null) facts.push(["Amount", money(d.amount, d.currency)]);
  for (const [k, v] of Object.entries(d.details || {})) facts.push([k.replaceAll("_", " "), typeof v === "boolean" ? (v ? "yes" : "no") : String(v ?? "")]);
  return h("section", { class: "card" }, h("h3", {}, "Request"),
    h("p", { class: "body" }, d.description),
    h("dl", { class: "facts" }, ...facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
}

function employeeCard(e) {
  return h("section", { class: "card" }, h("h3", {}, "Employee"),
    h("dl", { class: "facts" },
      h("dt", {}, "Name"), h("dd", {}, `${e.name} (${e.employee_no})`),
      h("dt", {}, "Role"), h("dd", {}, [e.title, e.level, e.department].filter(Boolean).join(" · ") || "n/a"),
      h("dt", {}, "Country"), h("dd", {}, e.country || "n/a"),
      h("dt", {}, "Manager"), h("dd", {}, e.manager_name || "none")));
}

function timeline(events) {
  return h("section", { class: "card" }, h("h3", {}, "History"),
    h("ol", { class: "timeline" }, ...events.map((e) => h("li", {},
      h("strong", {}, e.action.replaceAll("_", " ")), ` · ${e.actor} · ${fmtDateTime(e.at)}`,
      e.note ? h("div", { class: "muted small-text" }, e.note) : null))));
}

function aiCard(d, v) {
  const head = h("div", { class: "row" }, h("h3", {}, "AI pre-validation"),
    h("button", { class: "secondary small", type: "button", onclick: () => rerun(d) }, "Run again"));
  if (!v) return h("section", { class: "card ai" }, head, h("p", { class: "muted" }, "No validation has run yet."));
  const checks = v.checks.length ? h("div", {}, h("h4", {}, "Automatic checks"),
    h("ul", { class: "checks" }, ...v.checks.map((c) => h("li", { class: c.severity }, h("span", { class: "sev" }, { fail: "✗", warn: "!", info: "i" }[c.severity]), " ", c.message)))) : null;
  if (v.state !== "ok") {
    return h("section", { class: "card ai" }, head,
      h("div", { class: "banner" }, v.error || "Validation did not run."),
      h("p", { class: "muted" }, "You can still decide this request. Read the policies yourself, or run the validation again once the problem above is fixed."), checks);
  }
  const passageText = (n) => (v.passages.find((p) => p.n === n) || {}).text || "";
  return h("section", { class: "card ai" }, head,
    h("div", { class: "badges" }, badge(ADVICE, v.recommendation), badge(COMPLIANCE, v.compliance)),
    h("p", { class: "answer-text" }, v.summary || ""),
    ...v.warnings.map((w) => h("div", { class: "banner" }, w)),
    v.reasons.length ? h("div", {}, h("h4", {}, "Why"), h("ol", { class: "reasons" }, ...v.reasons.map((r) => h("li", {}, r.text, " ",
      ...r.passages.map((n) => h("a", { class: "cite", href: `#cite-${n}`, onclick: (e) => { e.preventDefault(); document.getElementById(`cite-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); } }, `[${n}]`)))))) : null,
    v.missing.length ? h("div", {}, h("h4", {}, "Ask the employee"), h("ul", {}, ...v.missing.map((m) => h("li", {}, m)))) : null,
    h("h4", {}, "Foundation (policy documents)"),
    v.citations.length ? h("div", {}, ...v.citations.map((c) => h("article", { class: "cite-card", id: `cite-${c.n}` },
      h("div", { class: "badges" }, h("span", { class: "badge" }, `[${c.n}]`), h("strong", {}, c.title), h("span", { class: "muted" }, `› ${c.heading || "intro"} · v${c.version}`)),
      c.quote ? h("blockquote", {}, c.quote) : null,
      c.quote ? h("p", { class: "small-text " + (c.verified ? "ok-text" : "warn-text") }, c.verified ? "✓ Quote found word for word in the document" : "⚠ Quote not found word for word: read the passage") : null,
      h("details", {}, h("summary", {}, "Show the full passage"), h("p", { class: "body" }, passageText(c.n)))))) : h("p", { class: "muted" }, "No policy passage was cited."),
    checks,
    h("p", { class: "muted small-text" }, `${v.model || "model"} via ${v.account || "account"} · ${fmtDateTime(v.created_at)} · ${v.duration_ms} ms. The AI advises; you decide.`));
}

async function rerun(d) {
  try { await api(`/api/v1/requests/${d.ref}/validate`, { method: "POST" }); toast("Validation started"); renderReview(d.ref); } catch (err) { toast(err.message, true); }
}

function decisionCard(d, v) {
  if (!can("requests:decide") && ["pending_review", "needs_info"].includes(d.status)) {
    return { card: h("section", { class: "card" }, h("h3", {}, "Decision"), h("p", { class: "muted" }, "Your role can read requests but not decide them.")), note: null };
  }
  if (!["pending_review", "needs_info"].includes(d.status)) {
    return { note: null, card: h("section", { class: "card" }, h("h3", {}, "Decision"),
      h("p", {}, badge(STATUS, d.status), ` by ${d.decided_by || "n/a"} ${d.decided_at ? "on " + fmtDateTime(d.decided_at) : ""}`),
      d.decision_note ? h("p", { class: "body" }, d.decision_note) : null,
      d.override ? h("div", { class: "banner" }, "This decision went against the AI advice. It is listed in the overrides report.") : null) };
  }
  const note = h("textarea", { id: "decision-note", rows: "3", maxlength: "2000", placeholder: "Reason shown to the employee (required to reject or ask for information)" });
  const rec = v && v.state === "ok" ? v.recommendation : null;
  const problem = h("p", { class: "error", role: "alert" });
  const act = (action) => async (e) => {
    e.target.disabled = true;
    problem.textContent = "";
    try {
      await api(`/api/v1/requests/${d.ref}/decision`, { method: "POST", json: { action, note: note.value.trim() || null } });
      toast({ approve: "Approved", reject: "Rejected", request_info: "Information requested" }[action]);
      $("review").close();
      initRequests();
    } catch (err) { problem.textContent = err.message; toast(err.message, true); e.target.disabled = false; }
  };
  const card = h("section", { class: "card" }, h("h3", {}, "Your decision"),
    rec === "reject" ? h("p", { class: "warn-text" }, "The AI suggests rejecting. Approving needs a written justification.") : null,
    rec === "approve" ? h("p", { class: "muted small-text" }, "The AI suggests approving. Rejecting needs a written justification.") : null,
    field("Note", note), problem,
    h("div", { class: "btns wrap" },
      h("button", { class: "primary", type: "button", onclick: act("approve") }, "Approve"),
      h("button", { class: "danger", type: "button", onclick: act("reject") }, "Reject"),
      d.status === "pending_review" ? h("button", { class: "secondary", type: "button", onclick: act("request_info") }, "Review: ask for information") : null));
  return { card, note };
}

// ---- new request -----------------------------------------------------------------------------------
async function openNew() {
  const dlg = $("newreq");
  let emps;
  try { emps = (await api("/api/v1/employees?status=active&limit=200")).items; } catch (err) { toast(err.message, true); return; }
  if (!emps.length) { toast("Add an employee first (Employees tab).", true); return; }
  const emp = h("select", { id: "nr-emp" }, ...emps.map((e) => h("option", { value: e.employee_no }, `${e.name} (${e.employee_no})`)));
  const kinds = { leave: "Leave", travel: "Travel", expense: "Expenses", other: "Other" };
  const type = h("select", { id: "nr-type" }, ...Object.entries(kinds).map(([k, label]) =>
    h("optgroup", { label }, ...types.filter((t) => t.kind === k).map((t) => h("option", { value: t.code }, t.label)))));
  const dyn = h("div", {});
  const err = h("p", { class: "error", role: "alert" });
  const inputs = {};
  const build = () => {
    const t = types.find((x) => x.code === type.value);
    for (const k of Object.keys(inputs)) delete inputs[k];
    const input = (name, label, kind, attrs = {}, hint) => {
      const el = kind === "area" ? h("textarea", { rows: "3", maxlength: "4000", ...attrs }) : h("input", { type: kind, ...attrs });
      inputs[name] = el;
      return field(label, el, hint);
    };
    const parts = [h("p", { class: "muted" }, t.description),
      input("title", "Title", "text", { maxlength: "160", required: true }),
      input("description", "Description", "area", { required: true, minlength: "5" })];
    if (t.dates !== "none") {
      const single = t.dates === "single";
      parts.push(h("div", { class: "grid2" }, input("start_date", single ? "Date" : "From", "date", { required: t.dates === "range" || single }),
        single ? null : input("end_date", "To", "date", { required: t.dates === "range" })));
    }
    if (t.kind === "expense") {
      parts.push(h("div", { class: "grid2" }, input("amount", "Amount", "number", { step: "0.01", min: "0", required: true }),
        input("currency", "Currency", "text", { maxlength: "3", value: "USD", required: true })));
    }
    const details = t.details.map((f) => {
      if (f.kind === "bool") { const el = h("input", { type: "checkbox" }); inputs["d:" + f.name] = el; return h("label", { class: "check" }, el, f.label); }
      if (f.kind === "choice") { const el = h("select", {}, h("option", { value: "" }, "(choose)"), ...f.choices.map((c) => h("option", { value: c }, c))); inputs["d:" + f.name] = el; return field(f.label + (f.required ? "" : " (optional)"), el); }
      return input("d:" + f.name, f.label + (f.required ? "" : " (optional)"), f.kind === "number" ? "number" : "text", { step: "any", min: "0", maxlength: "300", required: f.required }, f.help || undefined);
    });
    fill(dyn, ...parts, details.length ? h("h4", {}, "Details") : null, ...details);
  };
  type.addEventListener("change", build);
  build();
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    const t = types.find((x) => x.code === type.value);
    const body = { employee_no: emp.value, type: t.code, details: {} };
    for (const [k, el] of Object.entries(inputs)) {
      const val = el.type === "checkbox" ? el.checked : el.value.trim();
      if (k.startsWith("d:")) {
        if (el.type === "checkbox") body.details[k.slice(2)] = val; // an unticked box is a real answer: "no receipt"
        else if (val !== "") body.details[k.slice(2)] = el.type === "number" ? Number(val) : val;
      }
      else if (val !== "") body[k] = k === "amount" ? Number(val) : val;
    }
    try {
      const r = await api("/api/v1/requests", { method: "POST", json: body });
      toast(`Submitted ${r.ref}. AI validation is running.`);
      dlg.close();
      Object.assign(filters, { status: "open", type: "", q: "", employee_no: "", ai: "" });
      polls = 0;
      await initRequests();
      openReview(r.ref);
    } catch (ex) { err.textContent = ex.message; }
  } }, field("Employee", emp), field("Request type", type), dyn, err,
  h("div", { class: "btns" }, h("button", { class: "primary", type: "submit" }, "Submit request")));
  fill($("newreq-body"), h("h2", {}, "New request"), form);
  dlg.showModal();
}
