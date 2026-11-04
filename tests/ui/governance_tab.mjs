// The Governance tab: AIDB mode, seals and purpose decisions render; plain Postgres says so; the tab needs settings:manage.
// Run:  node tests/ui/governance_tab.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { readFileSync } from "node:fs";
import { fail, installDom } from "./fakedom.mjs";

const here = new URL(".", import.meta.url).pathname;
const asked = [];
let payload;
const dom = await installDom({ hash: "#governance", perms: ["settings:manage"], routes: async (p) => { asked.push(p); return payload; } });
const { initGovernance } = await import("/static/governance.js");
const { api } = await import("/kit/api.js");
const text = () => JSON.stringify(dom.el("view-governance"));

payload = { mode: { requested: "app", retrieval: false, agent: false, reason: "", seals: [] },
  governance: { available: false, reason: "AIDB is not installed", decisions: [], counts: {} } };
await initGovernance(dom.el("view-governance"), api);
if (!asked.some((p) => p.startsWith("/api/aidb/governance"))) fail("the page did not call GET /api/aidb/governance");
if (!text().includes("The app backend is in use")) fail("plain Postgres must say the app backend is in use");
if (!text().includes("Not available on this database") || !text().includes("AIDB is not installed")) fail("a missing AIDB must say so with the reason");

payload = { mode: { requested: "aidb", retrieval: false, agent: false, reason: "AIDB definitions do not match their seals: agent td_prevalidator (modified)",
  seals: [{ kind: "agent", name: "td_prevalidator", problem: "modified" }], aidb: { version: "7.9.0" } },
  governance: { available: true, source: "td_governance_decisions_v", purpose: "td_prevalidation", counts: { denied: 1, allowed: 1 },
    decisions: [{ recorded_at: "2026-11-04T10:00:00Z", status: "denied", agent: "td_prevalidator", role: "aidb_p_td_prevalidation", reason: "engine_privilege_denied", sql: "SELECT count(*) FROM employee" },
      { recorded_at: "2026-11-04T09:59:00Z", status: "allowed", agent: "td_prevalidator", role: "aidb_p_td_prevalidation", reason: null, sql: "SELECT 1 FROM td_policy_v" }] } };
await initGovernance(dom.el("view-governance"), api);
for (const want of ["agent td_prevalidator: modified", "engine_privilege_denied", "SELECT count(*) FROM employee", "7.9.0", "aidb --reseal".replace("aidb --", "aidb-setup --")])
  if (!text().includes(want)) fail("missing in the page: " + want);

const app = readFileSync(here + "../../src/trustdesk/static/app.js", "utf8");
const html = readFileSync(here + "../../src/trustdesk/static/index.html", "utf8");
if (!/governance:\s*"settings:manage"/.test(app)) fail("the governance tab must need settings:manage");
if (!html.includes('data-tab="governance"') || !html.includes('id="view-governance"')) fail("index.html lacks the governance tab or section");
console.log("ok governance tab");
process.exit(0);
