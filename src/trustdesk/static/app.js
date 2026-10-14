import { api } from "/static/api.js";
import { initAsk } from "/static/ask.js";
import { initConnect } from "/static/connect.js";
import { initDocuments } from "/static/documents.js";
import { initSearch } from "/static/search.js";
import { initSettings } from "/static/settings.js";
import { refreshSession, renderLogin, renderSetup, renderVaultBanner, state } from "/static/session.js";
import { $, h } from "/static/ui.js";

const TABS = ["search", "ask", "documents", "settings", "connect"];
const INIT = { search: initSearch, ask: initAsk, documents: initDocuments, settings: initSettings, connect: initConnect };
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
  if (s.state === "setup_required") renderSetup(() => boot());
  else if (s.state === "logged_out") renderLogin(() => boot(), notice);
  else showApp();
}

function showApp() {
  $("auth-card").hidden = true;
  $("nav").hidden = false;
  $("signout").hidden = false;
  renderVaultBanner();
  route();
}

function route() {
  if (!state.session || state.session.state !== "logged_in") return;
  const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "search";
  for (const t of TABS) $("view-" + t).hidden = t !== tab;
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("active", a.dataset.tab === tab);
  INIT[tab]();
}

$("signout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* the cookie is cleared server-side regardless */ }
  boot();
});

let lostTimer;
window.addEventListener("td-auth-lost", () => {
  clearTimeout(lostTimer);
  lostTimer = setTimeout(() => boot("Your session ended. Please sign in again."), 50);
});
window.addEventListener("td-vault-locked", async () => {
  try { await refreshSession(); renderVaultBanner(); } catch { /* ignore */ }
});
window.addEventListener("td-unlocked", route);
window.addEventListener("hashchange", route);

boot();
