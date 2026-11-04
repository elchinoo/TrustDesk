// Governance tab: AIDB mode at a glance (docs/GUIDE.md, "AIDB mode"). GET /api/aidb/governance says which backend is requested and
// in use (with the reason when AIDB is not), whether every sealed AIDB definition still matches its seal, and the recent purpose
// decisions of the pre-validation agent: what its database role was allowed to read and what Postgres refused. On plain Postgres
// it says so. Text goes through textContent only (the `h` helper); styling is base.css.
import { fill, fmtDateTime, h, errorState, loading, scrollRegion } from "/kit/ui.js";

const YES = (b) => (b ? "in use" : "off");

function modeBlock(mode) {
  const rows = [["Requested backend", mode.requested || "app"]];
  if (mode.requested === "aidb") {
    rows.push(["Knowledge base retrieval", YES(mode.retrieval)], ["Pre-validation agent", YES(mode.agent)]);
    if (mode.aidb && mode.aidb.version) rows.push(["AIDB version", mode.aidb.version]);
  }
  return h("section", { class: "card" }, h("h2", {}, "AIDB mode"),
    h("table", {}, h("caption", { class: "sr-only" }, "AIDB mode"), h("tbody", {}, ...rows.map(([k, v]) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, v))))),
    mode.requested !== "aidb"
      ? h("p", { class: "hint" }, "The app backend is in use: retrieval and pre-validation use the app's own code on plain Postgres. Set TRUSTDESK_RETRIEVAL_BACKEND=aidb to use AIDB.")
      : null,
    mode.reason ? h("p", { class: "empty", role: "status" }, "Using the app path for what is off: " + mode.reason) : null);
}

function sealsBlock(mode) {
  const seals = mode.seals || [];
  if (mode.requested !== "aidb") return null;
  return h("section", { class: "card" }, h("h2", {}, "Seals"),
    seals.length
      ? h("div", {}, h("p", { class: "empty", role: "alert" }, "These AIDB definitions no longer match their seal. AIDB mode is off until they are inspected and re-sealed with: trustdesk aidb-setup --reseal"),
        h("ul", {}, ...seals.map((s) => h("li", {}, `${s.kind} ${s.name}: ${s.problem}`))))
      : h("p", { class: "hint" }, "Every sealed AIDB definition (pipeline, model, tools, purpose, agent) matches its seal."));
}

function decisionsBlock(g) {
  if (!g || !g.available) {
    return h("section", { class: "card" }, h("h2", {}, "Decisions"), h("p", { class: "empty insights-na", role: "status" }, "Not available on this database."),
      g && g.reason ? h("p", { class: "hint" }, g.reason) : null);
  }
  const head = ["When", "Decision", "Agent", "Role", "Reason", "Statement"].map((t) => h("th", { scope: "col" }, t));
  const rows = (g.decisions || []).map((d) => h("tr", {}, h("td", {}, fmtDateTime(d.recorded_at)), h("td", {}, d.status), h("td", {}, d.agent || ""),
    h("td", {}, d.role || ""), h("td", {}, d.reason || ""), h("td", { class: "insights-sql" }, h("code", {}, d.sql || ""))));
  const wrap = scrollRegion(h("div", {}), "Decisions (scrollable)");
  wrap.append(h("table", {}, h("caption", { class: "sr-only" }, "Recent purpose decisions"), h("thead", {}, h("tr", {}, ...head)), h("tbody", {}, ...rows)));
  const c = g.counts || {};
  return h("section", { class: "card" }, h("h2", {}, "Decisions"),
    h("p", { class: "hint" }, `What the agent's database role (${g.purpose}) was allowed (${c.allowed || 0}) and refused (${c.denied || 0}) recently, from ${g.source}.`),
    g.note ? h("p", { class: "empty" }, g.note) : null,
    rows.length ? wrap : h("p", { class: "empty" }, "No decisions recorded yet."));
}

export async function initGovernance(root, api) {
  loading(root);
  try {
    const data = await api("/api/aidb/governance?limit=50");
    fill(root, h("h1", {}, "Governance"), modeBlock(data.mode), sealsBlock(data.mode), decisionsBlock(data.governance));
  } catch (e) {
    errorState(root, e.message || String(e), () => initGovernance(root, api));
  }
}
