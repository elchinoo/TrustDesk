// The Database tab mounts the kit's initInsights on /api/insights: every source renders, a source the database lacks says so honestly.
// Run:  node tests/ui/insights_tab.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { readFileSync } from "node:fs";
import { fail, installDom } from "./fakedom.mjs";

const here = new URL(".", import.meta.url).pathname;
const asked = [];
let payload;
const dom = await installDom({ hash: "#insights", perms: ["settings:manage"], routes: async (p) => { asked.push(p); return payload; } });
const { initInsights } = await import("/kit/insights.js");
const { api } = await import("/kit/api.js");
const text = () => JSON.stringify(dom.el("view-insights"));

payload = { sources: {}, top_queries: { available: false, reason: "extension edb_stat_monitor is not installed" },
  tuner: { available: false, reason: "edb_pg_tuner is not installed" }, advisor: { available: false, reason: "query_advisor is not installed" } };
await initInsights(dom.el("view-insights"), api);
if (!asked.some((p) => p.startsWith("/api/insights"))) fail("the page did not call GET /api/insights");
if ((text().match(/Not available on this database/g) || []).length !== 3) fail("each missing source must say it is not available");
if (!text().includes("edb_pg_tuner is not installed")) fail("the server's reason must be shown");

payload = { sources: {}, top_queries: { available: true, source: "edb_stat_monitor", since: null, queries: [{ queryid: "1", query: "SELECT * FROM request WHERE id = $1", calls: 3, total_ms: 9, mean_ms: 3, max_ms: 4, rows: 3 }] },
  tuner: { available: true, recommendations: [{ setting: "shared_buffers", value: "186 MB", raw: "" }] }, advisor: { available: true, suggestions: [] } };
await initInsights(dom.el("view-insights"), api);
if (!text().includes("FROM request WHERE id") || !text().includes("shared_buffers")) fail("available sources must render");

// the tab is wired in the app shell and only offered to people who may manage settings
const app = readFileSync(here + "../../src/trustdesk/static/app.js", "utf8");
const html = readFileSync(here + "../../src/trustdesk/static/index.html", "utf8");
if (!/insights:\s*"settings:manage"/.test(app)) fail("the insights tab must need settings:manage, like Settings");
if (!html.includes('data-tab="insights"') || !html.includes('id="view-insights"')) fail("index.html lacks the insights tab or section");
console.log("ok: the Database tab renders the kit's insights and is admin-gated");
process.exit(0);
