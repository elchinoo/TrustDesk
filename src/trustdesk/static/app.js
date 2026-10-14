import { CLIENTS } from "/static/clients.js";

const $ = (id) => document.getElementById(id);
const KEY_STORE = "trustdesk.key";
const EXAMPLES = [
  "How do we encrypt data at rest?",
  "How fast do you notify customers of a breach?",
  "What are your RTO and RPO?",
  "Do you require MFA?",
  "How long do you retain customer data?",
];

let apiKey = "";
try { apiKey = sessionStorage.getItem(KEY_STORE) || ""; } catch { /* storage unavailable: key stays in memory */ }

// ---- helpers ---------------------------------------------------------------------------------
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  el.append(...kids.filter((k) => k != null));
  return el;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 8000 : 3500);
}

class AuthError extends Error {}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}), Authorization: "Bearer " + apiKey };
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) throw new AuthError("unauthorized");
  if (r.status === 429) throw new Error("Too many failed attempts. Wait a minute and try again.");
  if (r.status === 502 || r.status === 503 || r.status === 504) {
    throw new Error("The server is unavailable. It may be paused or waking up; wait a few seconds and try again. Your key is still saved in this tab.");
  }
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* not json */ }
    throw new Error(detail || `Request failed (${r.status})`);
  }
  return r.json();
}

function showLogin(msg = "") {
  for (const id of ["view-search", "view-documents", "view-connect", "nav", "signout"]) $(id).hidden = true;
  $("login").hidden = false;
  $("login-error").textContent = msg;
  $("key").focus();
}

function handle(err) {
  if (err instanceof AuthError) {
    apiKey = "";
    try { sessionStorage.removeItem(KEY_STORE); } catch { /* ignore */ }
    showLogin("That key was not accepted.");
  } else {
    toast(err.message || "Something went wrong", true);
  }
}

// ---- routing ---------------------------------------------------------------------------------
const TABS = ["search", "documents", "connect"];

function route() {
  if (!apiKey) return showLogin();
  $("login").hidden = true;
  $("nav").hidden = false;
  $("signout").hidden = false;
  const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "search";
  for (const t of TABS) $("view-" + t).hidden = t !== tab;
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("active", a.dataset.tab === tab);
  if (tab === "search") initSearch();
  if (tab === "documents") loadDocuments();
  if (tab === "connect") initConnect();
}

// ---- login -----------------------------------------------------------------------------------
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  apiKey = $("key").value.trim();
  $("key").value = "";
  try {
    await api("/api/stats");
    try { sessionStorage.setItem(KEY_STORE, apiKey); } catch { /* ignore */ }
    route();
  } catch (err) {
    if (err instanceof AuthError) { apiKey = ""; showLogin("That key was not accepted."); }
    else showLogin(err.message);
  }
});

$("signout").addEventListener("click", () => {
  apiKey = "";
  try { sessionStorage.removeItem(KEY_STORE); } catch { /* ignore */ }
  showLogin();
});

// ---- search ----------------------------------------------------------------------------------
let searchReady = false;

async function initSearch() {
  if (!searchReady) {
    searchReady = true;
    const chips = $("chips");
    for (const q of EXAMPLES) {
      chips.append(h("button", { class: "chip", type: "button", onclick: () => { $("q").value = q; runSearch(); } }, q));
    }
  }
  $("q").focus();
  try {
    const s = await api("/api/stats");
    const b = $("banner");
    if (s.documents === 0) {
      b.hidden = false;
      b.replaceChildren("Your knowledge base is empty. ", h("a", { href: "#documents" }, "Upload documents or load the demo policies"), " to start searching.");
    } else if (!s.semantic) {
      b.hidden = false;
      b.textContent = "Demo embedder active: search matches words, not meaning. Set LLM_BASE_URL to an embeddings endpoint for semantic search.";
    } else {
      b.hidden = true;
    }
  } catch (err) { handle(err); }
}

$("search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

async function runSearch() {
  const q = $("q").value.trim();
  if (!q) return;
  const out = $("results");
  out.replaceChildren(h("p", { class: "loading" }, "Searching…"));
  try {
    const d = await api("/api/search?limit=8&q=" + encodeURIComponent(q));
    if (!d.results.length) { out.replaceChildren(h("p", { class: "empty" }, "No matching passages found.")); return; }
    out.replaceChildren(...d.results.map(resultCard));
  } catch (err) { out.replaceChildren(); handle(err); }
}

function resultCard(r) {
  const crumb = r.heading || r.title;
  const citation = `${r.title}, ${crumb} (v${r.version}, ${r.source})`;
  const badges = h("div", { class: "badges" },
    r.vec_rank != null ? h("span", { class: "badge" }, "vector #" + r.vec_rank) : null,
    r.lex_rank != null ? h("span", { class: "badge alt" }, "text #" + r.lex_rank) : null,
    h("span", { class: "badge plain" }, "score " + r.score.toFixed(4)),
    h("span", { class: "spacer" }),
    h("button", { class: "secondary small", type: "button", onclick: async () => {
      try { await navigator.clipboard.writeText(citation); toast("Citation copied"); } catch { toast("Copy failed", true); }
    } }, "Copy citation"));
  return h("article", { class: "card result" },
    h("h3", {}, r.title),
    h("div", { class: "crumb" }, `${crumb} · v${r.version} · ${r.source}`),
    h("p", { class: "body" }, r.body),
    badges);
}

// ---- documents -------------------------------------------------------------------------------
const fmtDate = (s) => new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

async function loadDocuments() {
  try {
    const docs = await api("/api/documents");
    const body = $("doc-body");
    body.replaceChildren(...docs.map((d) => h("tr", {},
      h("td", { class: "title" }, d.title),
      h("td", { class: "source col-source" }, d.source),
      h("td", { class: "num" }, String(d.version)),
      h("td", { class: "num" }, String(d.chunks)),
      h("td", { class: "col-added" }, fmtDate(d.created_at)),
      h("td", { class: "actions" }, h("div", { class: "btns" },
        h("button", { class: "secondary small", type: "button", onclick: () => viewDocument(d.id) }, "View"),
        h("button", { class: "danger small", type: "button", onclick: () => removeDocument(d) }, "Delete"))))));
    $("doc-table").hidden = docs.length === 0;
    $("doc-empty").hidden = docs.length !== 0;
    $("doc-detail").hidden = true;
  } catch (err) { handle(err); }
}

async function viewDocument(id) {
  try {
    const d = await api("/api/documents/" + id);
    const box = $("doc-detail");
    box.replaceChildren(
      h("div", { class: "row" }, h("h2", {}, d.title),
        h("button", { class: "secondary small", type: "button", onclick: () => { box.hidden = true; } }, "Close")),
      h("p", { class: "muted" }, `${d.source} · version ${d.version} · ${d.chunks.length} chunks`),
      ...d.chunks.map((c) => h("div", { class: "chunk" }, h("h4", {}, c.heading || "(intro)"), h("p", {}, c.body))));
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { handle(err); }
}

async function removeDocument(d) {
  if (!confirm(`Delete "${d.title}" and its ${d.chunks} chunks? This cannot be undone.`)) return;
  try { await api("/api/documents/" + d.id, { method: "DELETE" }); toast("Deleted " + d.title); loadDocuments(); }
  catch (err) { handle(err); }
}

$("load-demo").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const d = await api("/api/seed", { method: "POST" });
    const added = d.results.filter((r) => r.status === "ingested").length;
    toast(added ? `Loaded ${added} demo policies` : "Demo policies already loaded");
    loadDocuments();
  } catch (err) { handle(err); } finally { e.target.disabled = false; }
});

const MAX_BYTES = 10 * 1024 * 1024;
const OK_EXT = /\.(md|markdown|txt|pdf)$/i;

async function uploadFiles(files) {
  for (const f of files) {
    const bar = h("progress", { max: "1" });
    const state = h("span", { class: "muted" }, "Uploading…");
    const li = h("li", {}, h("span", { class: "name" }, f.name), bar, state);
    $("uploads").prepend(li);
    const fail = (m) => { bar.remove(); state.className = "bad"; state.textContent = m; };
    if (!OK_EXT.test(f.name)) { fail("Unsupported type"); continue; }
    if (f.size > MAX_BYTES) { fail("Over 10 MB"); continue; }
    try {
      const form = new FormData();
      form.append("file", f);
      const r = await api("/api/documents", { method: "POST", body: form });
      bar.remove();
      state.className = "ok";
      state.textContent = r.status === "unchanged" ? "Already up to date" : `Added (${r.chunks} chunks)`;
    } catch (err) {
      if (err instanceof AuthError) return handle(err);
      fail(err.message);
    }
  }
  loadDocuments();
}

const dz = $("dropzone");
dz.addEventListener("click", () => $("file-input").click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file-input").click(); } });
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); });
$("file-input").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });

// ---- connect ---------------------------------------------------------------------------------
let connectReady = false;

function renderClient() {
  const c = CLIENTS.find((x) => x.id === $("client").value) || CLIENTS[0];
  const url = location.origin + "/mcp";
  $("client-where").textContent = c.where;
  $("client-snippet").textContent = c.snippet(url);
  $("mcp-url").textContent = url;
}

function initConnect() {
  if (!connectReady) {
    connectReady = true;
    for (const c of CLIENTS) $("client").append(h("option", { value: c.id }, c.name));
    $("client").addEventListener("change", renderClient);
    $("copy-snippet").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($("client-snippet").textContent); toast("Copied"); } catch { toast("Copy failed", true); }
    });
  }
  renderClient();
}

window.addEventListener("hashchange", route);
route();
