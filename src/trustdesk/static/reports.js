import { api } from "/kit/api.js";
import { $, ADVICE, badge, COMPLIANCE, fill, fmtDateTime, fmtDay, h, scrollRegion, STATUS, toast } from "/kit/ui.js";

let wired = false;
let catalogue = [];

export async function initReports() {
  if (!wired) {
    wired = true;
    try { catalogue = (await api("/api/v1/reports")).reports; } catch (err) { toast(err.message, true); return; }
    $("rep-name").replaceChildren(...catalogue.map((r) => h("option", { value: r.name }, r.title)));
    $("rep-form").addEventListener("submit", (e) => { e.preventDefault(); run(); });
    $("rep-name").addEventListener("change", () => { sync(); run(); });
    $("rep-print").addEventListener("click", () => window.print());
    for (const id of ["rep-from", "rep-to"]) $(id).addEventListener("change", sync);
    sync();
  }
  await run();
}

function params() {
  const p = new URLSearchParams();
  if (!$("rep-from").disabled) {
    if ($("rep-from").value) p.set("from", $("rep-from").value);
    if ($("rep-to").value) p.set("to", $("rep-to").value);
  }
  return p;
}

function sync() {
  const meta = catalogue.find((r) => r.name === $("rep-name").value);
  $("rep-from").disabled = $("rep-to").disabled = !meta?.uses_period;
  const p = params();
  p.set("format", "csv");
  // A plain link: the browser sends the session cookie and the server answers with a file download.
  $("rep-csv").setAttribute("href", `/api/v1/reports/${encodeURIComponent($("rep-name").value)}?${p}`);
}

const BADGES = { status: STATUS, decision: STATUS, ai: ADVICE, compliance: COMPLIANCE };

function cell(col, row) {
  const v = row[col.key];
  if (v == null || v === "") return h("td", { class: col.type === "number" ? "num" : "" }, "");
  if (BADGES[col.key]) return h("td", {}, badge(BADGES[col.key], v));
  if (col.type === "date") return h("td", {}, fmtDay(v));
  if (col.type === "datetime") return h("td", {}, fmtDateTime(v));
  if (col.type === "number") return h("td", { class: "num" }, typeof v === "number" ? v.toLocaleString() : String(v));
  return h("td", { class: "wrap" }, String(v));
}

async function run() {
  sync();
  const name = $("rep-name").value;
  const out = $("rep-out");
  fill(out, h("p", { class: "loading" }, "Running…"));
  try {
    const r = await api(`/api/v1/reports/${encodeURIComponent(name)}?${params()}`);
    const range = r.period.from || r.period.to ? ` · ${r.period.from || "…"} to ${r.period.to || "…"}` : "";
    fill(out,
      h("h2", { class: "section-title" }, r.title),
      h("p", { class: "muted" }, r.description + range),
      r.truncated ? h("div", { class: "banner" }, "Only the first 5,000 rows are shown. Narrow the dates, or download the CSV.") : null,
      r.rows.length ? scrollRegion(h("div", { class: "card flush" }, h("table", { class: "report" },
        h("thead", {}, h("tr", {}, ...r.columns.map((c) => h("th", { class: c.type === "number" ? "num" : "" }, c.label)))),
        h("tbody", {}, ...r.rows.map((row) => h("tr", {}, ...r.columns.map((c) => cell(c, row))))),
        r.totals ? h("tfoot", {}, h("tr", {}, ...r.columns.map((c, i) => h("td", { class: c.type === "number" ? "num" : "" },
          i === 0 ? "Total" : (r.totals[c.key] ?? ""))))) : null)), r.title)
        : h("p", { class: "empty" }, "No rows for this period."),
      h("p", { class: "muted small-text" }, `${r.rows.length} rows · generated ${new Date().toLocaleString()}`));
  } catch (err) { fill(out, h("p", { class: "error" }, err.message)); }
}
