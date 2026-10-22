import { api } from "/kit/api.js";
import { $, h, toast } from "/kit/ui.js";

const STORE = "trustdesk.ask.account";
let ready = false;

function remembered() {
  try { return localStorage.getItem(STORE) || ""; } catch { return ""; }
}
function remember(v) {
  try { localStorage.setItem(STORE, v); } catch { /* storage unavailable */ }
}

export async function initAsk() {
  if (!ready) {
    ready = true;
    $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); run(); });
    $("ask-account").addEventListener("change", (e) => remember(e.target.value));
  }
  await loadAccounts();
  $("ask-q").focus();
}

async function loadAccounts() {
  const select = $("ask-account");
  const note = $("ask-note");
  try {
    const d = await api("/api/accounts");
    const usable = [...d.accounts, ...(d.env ? [d.env] : [])].filter((a) => a.chat_model);
    select.replaceChildren(...usable.map((a) => h("option", { value: String(a.id) }, `${a.name} · ${a.chat_model}`)));
    const want = remembered();
    if (usable.some((a) => String(a.id) === want)) select.value = want;
    const locked = d.vault.initialized && !d.vault.unlocked;
    $("ask-submit").disabled = usable.length === 0;
    note.hidden = usable.length > 0 && !locked;
    note.replaceChildren(
      usable.length === 0
        ? h("span", {}, "No account has a chat model yet. ", h("a", { href: "#settings" }, "Add one in Settings"), " to use Ask.")
        : h("span", {}, "Provider keys are locked: unlock them above to ask questions."));
  } catch (err) { toast(err.message, true); }
}

async function run() {
  const q = $("ask-q").value.trim();
  if (q.length < 3) return;
  const out = $("ask-result");
  const btn = $("ask-submit");
  btn.disabled = true;
  out.replaceChildren(h("p", { class: "loading" }, "Searching your documents and asking the model…"));
  try {
    const d = await api("/api/ask", { method: "POST", json: { question: q, account: $("ask-account").value } });
    out.replaceChildren(render(d));
  } catch (err) {
    out.replaceChildren(h("p", { class: "error", role: "alert" }, err.message));
  } finally { btn.disabled = false; }
}

function withCitations(text) {
  // Turn [n] markers into links to the matching source card. Everything stays text nodes.
  return text.split(/(\[\d{1,2}\])/).map((part) => {
    const m = /^\[(\d{1,2})\]$/.exec(part);
    return m ? h("a", { class: "cite", href: `#src-${m[1]}`, onclick: (e) => { e.preventDefault(); document.getElementById(`src-${m[1]}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); } }, `[${m[1]}]`) : part;
  });
}

function render(d) {
  if (d.answer == null) return h("div", { class: "banner" }, d.message);
  const usage = d.usage?.total_tokens ? ` · ${d.usage.total_tokens} tokens` : "";
  return h("div", {},
    ...d.warnings.map((w) => h("div", { class: "banner" }, w)),
    h("article", { class: "card answer" },
      h("p", { class: "answer-text" }, ...withCitations(d.answer)),
      h("p", { class: "muted small-text" }, `Answered by ${d.model} via ${d.account}${usage}. Check the sources below before relying on it.`)),
    h("h2", { class: "section-title" }, "Sources"),
    ...d.sources.map((s) => h("article", { class: "card source" + (s.cited ? " cited" : ""), id: `src-${s.n}` },
      h("div", { class: "badges" },
        h("span", { class: "badge" }, `[${s.n}]`),
        s.cited ? h("span", { class: "badge alt" }, "cited") : h("span", { class: "badge plain" }, "retrieved, not cited"),
        h("strong", {}, s.title)),
      h("div", { class: "crumb" }, `${s.heading || "intro"} · v${s.version} · ${s.source}`),
      h("p", { class: "body" }, s.text))));
}
