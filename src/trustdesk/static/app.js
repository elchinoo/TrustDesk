import { api } from "/kit/api.js";
import { initInsights } from "/kit/insights.js";
import { initAsk } from "/static/ask.js";
import { initConnect } from "/static/connect.js";
import { initDocuments } from "/static/documents.js";
import { initEmployees } from "/static/employees.js";
import { initReports } from "/static/reports.js";
import { initRequests, stopLive } from "/static/requests.js";
import { initSearch } from "/static/search.js";
import { initSettings } from "/static/settings.js";
import { bootSession, can, refreshSession, renderLogin, renderSetup, renderVaultBanner, state } from "/kit/session.js";
import { $, h, scrollRegion } from "/kit/ui.js";

const TABS = ["requests", "employees", "reports", "documents", "search", "ask", "settings", "connect", "insights"];
const INIT = { requests: initRequests, employees: initEmployees, reports: initReports, search: initSearch, ask: initAsk, documents: initDocuments, settings: initSettings, connect: initConnect, insights: () => initInsights($("view-insights"), api) };
const SECTIONS = ["auth-card", "vault-banner", "nav", "signout", ...TABS.map((t) => "view-" + t)];

// Earlier versions kept the API key in sessionStorage. Nothing writes it any more; remove any leftover.
try { sessionStorage.removeItem("trustdesk.key"); } catch { /* storage unavailable */ }

// Wide tables and code blocks scroll on a phone: make every such container reachable by keyboard.
for (const [id, label] of [["req-table", "Requests"], ["emp-table", "Employees"], ["doc-table", "Documents"]]) scrollRegion($(id).parentElement, label);
for (const [i, pre] of [...document.querySelectorAll("pre.code")].entries()) scrollRegion(pre, `Code example ${i + 1}`);

function hideAll() { stopLive(); for (const id of SECTIONS) $(id).hidden = true; }

async function boot(notice = "") {
  hideAll();
  // The card shows "Loading…" (a single sign-on round trip can take seconds), or an error with a retry; onReady runs after
  // a successful retry too, so the page never stays on the error.
  $("auth-card").hidden = false;
  await bootSession($("auth-card"), { onReady: (s) => {
    if (s.state === "setup_required") renderSetup(() => boot(), "TrustDesk");
    else if (s.state === "logged_out") renderLogin(() => boot(), notice, "TrustDesk");
    else showApp();
  } });
}

const NEEDS = { requests: "requests:read", employees: "requests:read", reports: "reports:read", documents: "kb:read", search: "kb:read", ask: "ask", settings: "settings:manage", connect: "mcp:use", insights: "settings:manage" };

function showApp() {
  $("auth-card").hidden = true;
  for (const a of document.querySelectorAll("#nav a")) a.hidden = !can(NEEDS[a.dataset.tab]);
  $("nav").hidden = false;
  $("signout").hidden = false;
  renderVaultBanner();
  route();
}

function route() {
  if (!state.session || state.session.state !== "logged_in") return;
  const allowed = TABS.filter((t) => can(NEEDS[t]));
  const want = location.hash.slice(1);
  const tab = allowed.includes(want) ? want : (allowed[0] || "requests");
  for (const t of TABS) $("view-" + t).hidden = t !== tab;
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("active", a.dataset.tab === tab);
  if (tab !== "requests") stopLive();       // the live stream holds one of the browser's few connections per site
  INIT[tab]();
}

$("signout").addEventListener("click", async () => {
  let out = {};
  try { out = await api("/api/logout", { method: "POST" }); } catch { /* the cookie is cleared server-side regardless */ }
  if (out.logout_url) { location.assign(out.logout_url); return; }   // also end the single sign-on session
  boot();
});

let lostTimer;
window.addEventListener("apg-auth-lost", () => {
  clearTimeout(lostTimer);
  lostTimer = setTimeout(() => boot("Your session ended. Please sign in again."), 50);
});
window.addEventListener("apg-vault-locked", async () => {
  try { await refreshSession(); renderVaultBanner(); } catch { /* ignore */ }
});
window.addEventListener("apg-unlocked", route);
window.addEventListener("hashchange", route);

boot();
