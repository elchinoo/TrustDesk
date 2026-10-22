import { api } from "/kit/api.js";
import { initAsk } from "/static/ask.js";
import { initConnect } from "/static/connect.js";
import { initDocuments } from "/static/documents.js";
import { initEmployees } from "/static/employees.js";
import { initReports } from "/static/reports.js";
import { initRequests } from "/static/requests.js";
import { initSearch } from "/static/search.js";
import { initSettings } from "/static/settings.js";
import { can, refreshSession, renderLogin, renderSetup, renderVaultBanner, state } from "/kit/session.js";
import { $, h } from "/kit/ui.js";

const TABS = ["requests", "employees", "reports", "documents", "search", "ask", "settings", "connect"];
const INIT = { requests: initRequests, employees: initEmployees, reports: initReports, search: initSearch, ask: initAsk, documents: initDocuments, settings: initSettings, connect: initConnect };
const SECTIONS = ["auth-card", "vault-banner", "nav", "signout", ...TABS.map((t) => "view-" + t)];

// Earlier versions kept the API key in sessionStorage. Nothing writes it any more; remove any leftover.
try { sessionStorage.removeItem("trustdesk.key"); } catch { /* storage unavailable */ }

function hideAll() { for (const id of SECTIONS) $(id).hidden = true; }

function showUnavailable(err) {
  hideAll();
  $("auth-card").replaceChildren(
    h("img", { class: "hero-logo", src: "/static/logo.svg", alt: "", width: "72", height: "72" }),
    h("h1", {}, "TrustDesk can't be reached"),
    h("p", { class: "muted" }, err.message),
    h("p", { class: "hint" }, "If this server runs on a platform that pauses idle apps, resume it first (for AgentPG: agentpg sandbox resume <sandbox-id>), then retry."),
    h("button", { class: "primary", type: "button", onclick: () => boot() }, "Retry"));
  $("auth-card").hidden = false;
}

async function boot(notice = "") {
  hideAll();
  let s;
  try { s = await refreshSession(); } catch (err) { showUnavailable(err); return; }
  if (s.state === "setup_required") renderSetup(() => boot(), "TrustDesk");
  else if (s.state === "logged_out") renderLogin(() => boot(), notice, "TrustDesk");
  else showApp();
}

const NEEDS = { requests: "requests:read", employees: "requests:read", reports: "reports:read", documents: "kb:read", search: "kb:read", ask: "ask", settings: "settings:manage", connect: "mcp:use" };

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
