import { api } from "/kit/api.js";
import { $, confirmDialog, copy, field, fill, fmtDate, h, toast } from "/kit/ui.js";

let poller = null;

export async function initSettings() {
  await Promise.all([renderAccounts(), renderValidation(), renderEmbeddings(), renderSecurity()]);
}

// ---- provider accounts --------------------------------------------------------------------------
async function renderAccounts() {
  const box = $("settings-accounts");
  let data, presets;
  try { [data, presets] = await Promise.all([api("/api/accounts"), api("/api/providers")]); } catch (err) { box.replaceChildren(h("p", { class: "error" }, err.message)); return; }
  const v = data.vault;
  const notice = !v.initialized
    ? h("div", { class: "banner" }, "To store provider API keys, first set an admin password under Security below. Keys are encrypted with a random data key that your password protects; the database alone reveals nothing.")
    : (!v.unlocked ? h("div", { class: "banner" }, "Provider keys are locked. Unlock them with your password (banner at the top) to test or use accounts.") : null);
  const formHost = h("div", {});
  const cards = [...data.accounts, ...(data.env ? [data.env] : [])].map((a) => accountCard(a, data, () => renderAll(), (acct) => showForm(acct)));

  function showForm(existing) {
    formHost.replaceChildren(accountForm(existing, presets, () => { formHost.replaceChildren(); renderAll(); }, () => formHost.replaceChildren()));
    formHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  fill(box,
    h("div", { class: "row" }, h("h2", {}, "Provider accounts"), h("button", { class: "primary", type: "button", onclick: () => showForm(null) }, "Add account")),
    h("p", { class: "muted" }, "Add as many OpenAI-compatible accounts as you like (OpenAI, OpenRouter, GLM, Kimi, Claude and others). Pick one for embeddings below, and choose one per question in Ask."),
    notice, formHost,
    cards.length ? h("div", { class: "acct-grid" }, ...cards) : h("p", { class: "empty" }, "No accounts yet."));
}

function renderAll() { initSettings(); }

// ---- request validation ---------------------------------------------------------------------------
const DEFAULT = "__default";   // the select's value for "nothing saved: use the default"
let countPoll = null;

// What the saved setting and the effective account mean, in words.
function validationStatus(s) {
  const name = (id) => s.accounts.find((a) => a.id === id)?.name || "the saved account";
  if (s.account === "none") return "Validation is switched off (saved). Requests arrive without AI advice.";
  if (s.effective == null) return "No chat model is available, so requests arrive without AI advice. Add an account with a chat model above.";
  if (s.source === "default") return s.effective === "env" ? "Using the environment account: default. Every new request is checked by it against your policy documents." : `Using ${name(s.effective)}: default (the only account with a chat model).`;
  return `Using ${name(s.effective)} (saved). Every new request is checked by this model against your policy documents before a person sees it.`;
}

async function renderValidation(savedMsg) {
  const box = $("settings-validation");
  let s;
  try { s = await api("/api/v1/validation/settings"); } catch (err) { box.replaceChildren(h("p", { class: "error" }, err.message)); return; }
  const hasEnv = s.accounts.some((a) => a.id === "env");
  const options = [h("option", { value: DEFAULT }, hasEnv ? "Default (environment account)" : "Default (the only account with a chat model)"),
    h("option", { value: "none" }, "None (no AI validation)"),
    ...s.accounts.map((a) => h("option", { value: a.id }, `${a.name} · ${a.chat_model}`))];
  const saved = s.account || DEFAULT;
  if (!options.some((o) => o.value === saved)) options.push(h("option", { value: saved }, "Saved account (no longer available)"));   // show the truth, not the first option
  const pick = h("select", { id: "val-account" }, ...options);
  pick.value = saved;
  const b = s.backlog;
  const rate = h("input", { id: "val-backlog", type: "number", min: "0", max: String(b.max), value: b.saved ? String(b.per_minute) : "", placeholder: String(b.default) });
  const attempts = h("input", { id: "val-attempts", type: "number", min: "0", max: String(b.max_attempts_max), value: b.max_attempts_saved ? String(b.max_attempts) : "", placeholder: String(b.max_attempts_default) });
  const waiting = h("strong", { id: "val-waiting" }, String(b.waiting));
  const retrying = h("strong", { id: "val-retrying" }, String(b.retrying));
  const gaveUp = h("strong", { id: "val-gaveup" }, String(b.gave_up));
  const result = h("p", { class: "hint", role: "status" }, savedMsg || "");
  // Only the counts are redrawn by the poll: the select and the numbers the person is typing are never rebuilt.
  clearInterval(countPoll);
  countPoll = setInterval(async () => {
    try {
      const x = (await api("/api/v1/validation/settings")).backlog;
      waiting.textContent = String(x.waiting); retrying.textContent = String(x.retrying); gaveUp.textContent = String(x.gave_up);
    } catch { /* keep the old counts */ }
  }, 15000);
  fill(box,
    h("h2", {}, "Request validation"),
    h("p", { class: "muted" }, "Which account pre-validates employee requests. The request text and the retrieved policy passages are sent to that provider; employee names, e-mail addresses and numbers are not."),
    field("Account", pick, undefined), h("p", { class: "hint" }, validationStatus(s)),
    h("p", { class: "muted" }, h("span", {}, "Requests waiting for validation: "), waiting),
    h("p", { class: "muted" }, h("span", {}, "Validation failed, will be retried: "), retrying, h("span", {}, " · failed, gave up: "), gaveUp),
    field("Waiting requests validated per minute", rate, `Requests that arrived without an account are picked up automatically, oldest first, once one is available. 0 turns it off. Empty uses the server default (${b.default}). Maximum ${b.max}.`),
    field("Attempts for a failed validation", attempts, `A validation that failed (provider error, revoked key, upstream outage) is retried automatically after ${b.backoff_minutes.join(", ")} minutes, up to this many attempts in all. 0 never retries. Empty uses the server default (${b.max_attempts_default}). Maximum ${b.max_attempts_max}. Validate waiting requests also retries failed ones, now, and starts their count over.`),
    h("div", { class: "btns" }, h("button", { class: "primary", type: "button", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/v1/validation/settings", { method: "PUT", json: { account: pick.value === DEFAULT ? null : pick.value, backlog_per_minute: rate.value === "" ? null : Number(rate.value), max_attempts: attempts.value === "" ? null : Number(attempts.value) } });
        toast("Saved");
        renderValidation("Saved.");
      } catch (err) { toast(err.message, true); result.className = "error"; result.textContent = err.message; } finally { e.target.disabled = false; }
    } }, "Save"),
    h("button", { class: "secondary", type: "button", onclick: async (e) => {
      e.target.disabled = true;
      try { const r = await api("/api/v1/requests/validate-pending", { method: "POST" }); toast(r.queued ? `Validating ${r.queued} waiting or failed requests` : "No requests are waiting or failed"); } catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
    } }, "Validate waiting requests")), result);
}

function accountCard(a, data, reload, edit) {
  const sameAsEnv = !a.read_only && data.env && a.chat_model && a.chat_model === data.env.chat_model
    && a.base_url.replace(/\/+$/, "") === data.env.base_url.replace(/\/+$/, "");
  const result = h("p", { class: "test-result", role: "status" });
  const test = (target) => async (e) => {
    e.target.disabled = true;
    result.className = "test-result";
    result.textContent = `Testing ${target}…`;
    try {
      const r = await api(`/api/accounts/${a.id}/test`, { method: "POST", json: { target } });
      result.className = "test-result ok";
      result.textContent = target === "chat" ? `Chat works (${r.ms} ms). Reply: "${r.reply}"` : `Embeddings work (${r.ms} ms). The model returns ${r.dimensions} dimensions.`;
    } catch (err) { result.className = "test-result bad"; result.textContent = err.message; } finally { e.target.disabled = false; }
  };
  const active = String(data.active_embedding) === String(a.id);
  return h("article", { class: "card acct" },
    h("div", { class: "row" }, h("h3", {}, a.name),
      h("span", { class: "badges" },
        active ? h("span", { class: "badge alt" }, "embeddings") : null,
        a.read_only ? h("span", { class: "badge plain" }, "server setting") : h("span", { class: "badge plain" }, a.provider))),
    h("dl", { class: "facts" },
      h("dt", {}, "Base URL"), h("dd", {}, a.base_url),
      h("dt", {}, "API key"), h("dd", {}, a.has_key ? (a.api_key_last4 ? `stored, ends in ${a.api_key_last4}` : "set by the server") : "none"),
      h("dt", {}, "Chat model"), h("dd", {}, a.chat_model || "not set"),
      h("dt", {}, "Embedding model"), h("dd", {}, a.embedding_model ? `${a.embedding_model}${a.embedding_dim ? ` (${a.embedding_dim} dims)` : ""}` : "not set")),
    sameAsEnv ? h("p", { class: "hint" }, "Same endpoint as the environment account.") : null,
    h("div", { class: "btns wrap" },
      a.chat_model ? h("button", { class: "secondary small", type: "button", onclick: test("chat") }, "Test chat") : null,
      a.embedding_model ? h("button", { class: "secondary small", type: "button", onclick: test("embeddings") }, "Test embeddings") : null,
      a.read_only ? null : h("button", { class: "secondary small", type: "button", onclick: () => edit(a) }, "Edit"),
      a.read_only ? null : h("button", { class: "danger small", type: "button", onclick: async () => {
        if (!(await confirmDialog(`Delete the account "${a.name}"? Its stored key is erased.`, { title: "Delete account", confirmLabel: "Delete", danger: true, typeToConfirm: a.name }))) return;
        try { await api(`/api/accounts/${a.id}`, { method: "DELETE" }); toast("Account deleted"); reload(); } catch (err) { toast(err.message, true); }
      } }, "Delete")),
    result);
}

function accountForm(existing, presets, saved, cancel) {
  const e = existing;
  const provider = h("select", { id: "af-provider" }, ...presets.map((p) => h("option", { value: p.id }, p.name)));
  provider.value = e?.provider && presets.some((p) => p.id === e.provider) ? e.provider : "openai";
  const name = h("input", { id: "af-name", value: e?.name || "", maxlength: "60", required: true, placeholder: "e.g. OpenAI work" });
  const base = h("input", { id: "af-base", value: e?.base_url || "", maxlength: "300", required: true, placeholder: "https://api.example.com/v1" });
  const key = h("input", { id: "af-key", type: "password", autocomplete: "off", spellcheck: "false", placeholder: e?.has_key ? "leave blank to keep the stored key" : "sk-…" });
  const chat = h("input", { id: "af-chat", value: e?.chat_model || "", maxlength: "120", placeholder: "model name, e.g. from the provider's docs" });
  const emb = h("input", { id: "af-emb", value: e?.embedding_model || "", maxlength: "120", placeholder: "embedding model name (optional)" });
  const dim = h("input", { id: "af-dim", type: "number", min: "8", max: "2000", value: e?.embedding_dim ? String(e.embedding_dim) : "", placeholder: "e.g. 768" });
  const clear = h("input", { id: "af-clear", type: "checkbox" });
  const note = h("p", { class: "hint" });
  const err = h("p", { class: "error", role: "alert" });
  const applyPreset = () => {
    const p = presets.find((x) => x.id === provider.value);
    note.textContent = p?.note || "";
    if (e || !p) return;
    base.value = p.base_url;
    chat.value = p.chat_model;
    emb.value = p.embedding_model;
    dim.value = p.embedding_dim ? String(p.embedding_dim) : "";
    if (!name.value) name.value = p.id === "custom" ? "" : p.name;
  };
  provider.addEventListener("change", applyPreset);
  applyPreset();
  return h("form", {
    class: "card acct-form",
    onsubmit: async (ev) => {
      ev.preventDefault();
      err.textContent = "";
      const json = { name: name.value, provider: provider.value, base_url: base.value, api_key: key.value || null, clear_key: clear.checked,
        chat_model: chat.value || null, embedding_model: emb.value || null, embedding_dim: dim.value ? Number(dim.value) : null };
      try {
        await api(e ? `/api/accounts/${e.id}` : "/api/accounts", { method: e ? "PUT" : "POST", json });
        key.value = "";
        toast(e ? "Account updated" : "Account added");
        saved();
      } catch (ex) { err.textContent = ex.message; }
    },
  },
  h("h3", {}, e ? `Edit ${e.name}` : "Add an account"),
  field("Provider", provider, null), note,
  h("div", { class: "grid2" }, field("Name", name), field("Base URL", base, "Must be https. Requests go from this server to this address.")),
  field("API key", key, "Stored encrypted. It is never shown again, only its last 4 characters."),
  e?.has_key ? h("label", { class: "check", for: "af-clear" }, clear, " Remove the stored key") : null,
  h("div", { class: "grid3" }, field("Chat model", chat), field("Embedding model", emb), field("Embedding dimensions", dim, "Max 2000")),
  h("div", { class: "btns" }, h("button", { class: "primary", type: "submit" }, e ? "Save changes" : "Add account"),
    h("button", { class: "secondary", type: "button", onclick: cancel }, "Cancel")),
  err);
}

// ---- embeddings ---------------------------------------------------------------------------------
async function renderEmbeddings() {
  const box = $("settings-embeddings");
  let acc, status, stats;
  try { [acc, status, stats] = await Promise.all([api("/api/accounts"), api("/api/embeddings/status"), api("/api/stats")]); } catch (err) { box.replaceChildren(h("p", { class: "error" }, err.message)); return; }
  const builtin = { id: "env", name: "Built-in demo embedder", embedding_model: "word matching only", embedding_dim: null };
  const options = [...acc.accounts.filter((a) => a.embedding_model), acc.env && acc.env.embedding_model ? acc.env : builtin];
  const select = h("select", { id: "emb-select" }, ...options.map((a) => h("option", { value: String(a.id) }, `${a.name} · ${a.embedding_model}${a.embedding_dim ? ` · ${a.embedding_dim} dims` : ""}`)));
  const act = status.active;
  select.value = String(act.account);
  const progress = h("progress", { max: "1", value: "0", hidden: true });
  const msg = h("p", { class: "muted", role: "status" });
  // The facts are the only part that a finished re-embedding changes. They live in their own region so that the
  // poll never has to rebuild the select above them (a choice the person is making would be lost).
  const facts = h("dl", { class: "facts" });
  const showFacts = (a, st) => fill(facts, h("dt", {}, "Active"), h("dd", {}, `${a.model} · ${a.dimension} dimensions · ${a.semantic ? "semantic" : "word matching only"}`),
    h("dt", {}, "Passages"), h("dd", {}, String(st.chunks)),
    ...(st.needs_reembedding ? [h("dt", {}, "Status"), h("dd", { class: "error" }, `Needs re-embedding: the index stores ${st.stored_dimension}-dimension vectors. Use the account below again to convert it.`)] : []));
  showFacts(act, stats);
  const refresh = async () => {
    try { const [st, sx] = await Promise.all([api("/api/embeddings/status"), api("/api/stats")]); showFacts(st.active, sx); } catch { /* keep the old facts */ }
    progress.hidden = true;
    msg.textContent = "";
    go.disabled = options.length === 0;
  };
  const go = h("button", { class: "primary", type: "button", disabled: options.length === 0, onclick: async () => {
    const chosen = options.find((a) => String(a.id) === select.value);
    if (!(await confirmDialog(`Use "${chosen.name}" (${chosen.embedding_model}) for embeddings?\n\nIf the model or vector size differs, all ${stats.chunks} passages are re-embedded. Search and uploads are paused while that runs, and nothing changes if the provider call fails.`, { title: "Use for embeddings", confirmLabel: "Use for embeddings" }))) return;
    go.disabled = true;
    try {
      const r = await api("/api/embeddings/activate", { method: "POST", json: { account: select.value } });
      toast(r.message);
      if (r.started) poll(progress, msg, go, refresh); else await refresh();
    } catch (err) { go.disabled = false; msg.className = "error"; msg.textContent = err.message; }
  } }, "Use for embeddings");
  box.replaceChildren(
    h("h2", {}, "Search embeddings"),
    h("p", { class: "muted" }, "Embeddings turn passages and questions into vectors for semantic search. The built-in demo embedder only matches words; a provider model understands meaning."),
    ...(act.default && acc.env && act.account === "env" ? [h("p", { class: "hint" }, "Using the environment account: default.")] : []),
    facts,
    options.length ? h("div", { class: "field" }, h("label", { for: "emb-select" }, "Account to use"), select) : h("p", { class: "hint" }, "Add an account with an embedding model to switch."),
    h("div", { class: "btns" }, go), progress, msg);
  if (status.job.state === "running") poll(progress, msg, go, refresh);
}

function poll(progress, msg, go, refresh) {
  clearInterval(poller);
  progress.hidden = false;
  go.disabled = true;
  const tick = async () => {
    try {
      const { job } = await api("/api/embeddings/status");
      progress.max = Math.max(job.total, 1);
      progress.value = job.done;
      msg.className = "muted";
      msg.textContent = `Re-embedding… ${job.done} of ${job.total} passages`;
      if (job.state !== "running") {
        clearInterval(poller);
        if (job.state === "done") toast("Embeddings switched"); else toast(job.message, true);
        refresh();
      }
    } catch { /* transient: keep polling */ }
  };
  poller = setInterval(tick, 1000);
  tick();
}

// ---- security -----------------------------------------------------------------------------------
async function renderSecurity() {
  const box = $("settings-security");
  let tokens, session;
  try { [tokens, session] = await Promise.all([api("/api/tokens"), api("/api/session")]); } catch (err) { box.replaceChildren(h("p", { class: "error" }, err.message)); return; }
  const reveal = h("div", { hidden: true });
  const tName = h("input", { id: "tk-name", maxlength: "60", required: true, placeholder: "e.g. Claude Code on my laptop" });
  const tScope = h("select", { id: "tk-scope" }, h("option", { value: "read" }, "Read-only (search and list; recommended for MCP)"), h("option", { value: "client" }, "Client application (employees and requests only)"), h("option", { value: "metrics" }, "Metrics only (scrape /metrics; reads nothing else)"), h("option", { value: "full" }, "Full access"));
  const tErr = h("p", { class: "error", role: "alert" });
  const tokenForm = h("form", { class: "grid-inline", onsubmit: async (e) => {
    e.preventDefault();
    tErr.textContent = "";
    try {
      const t = await api("/api/tokens", { method: "POST", json: { name: tName.value, scope: tScope.value } });
      tName.value = "";
      reveal.replaceChildren(
        h("p", { class: "warn-text" }, "Copy this token now. It is shown only once and cannot be recovered; only a hash is stored."),
        h("div", { class: "codewrap" }, h("pre", { class: "code" }, h("code", {}, t.token)), h("button", { class: "secondary small", type: "button", onclick: () => copy(t.token, "Token copied") }, "Copy")));
      reveal.hidden = false;
      renderTokenTable();
    } catch (ex) { tErr.textContent = ex.message; }
  } }, field("Name", tName), field("Access", tScope), h("button", { class: "primary", type: "submit" }, "Create token"));

  const table = h("div", {});
  async function renderTokenTable() {
    const list = await api("/api/tokens");
    table.replaceChildren(list.length ? h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Name", "Token", "Access", "Created", "Last used", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, ...list.map((t) => h("tr", {},
        h("td", { class: "title" }, t.name), h("td", {}, h("code", {}, `${t.prefix}…`)), h("td", {}, t.scope),
        h("td", {}, fmtDate(t.created_at)), h("td", {}, fmtDate(t.last_used_at)),
        h("td", { class: "actions" }, t.revoked_at ? h("span", { class: "muted" }, "revoked") : h("button", { class: "danger small", type: "button", onclick: async () => {
          if (!(await confirmDialog(`Revoke "${t.name}"? Clients using it stop working immediately.`, { title: "Revoke token", confirmLabel: "Revoke", danger: true }))) return;
          try { await api(`/api/tokens/${t.id}`, { method: "DELETE" }); toast("Token revoked"); renderTokenTable(); } catch (err) { toast(err.message, true); }
        } }, "Revoke"))))))) : h("p", { class: "hint" }, "No tokens yet."));
  }

  const has = session.has_admin_password;
  const cur = h("input", { id: "pw-cur", type: "password", autocomplete: "current-password" });
  const nw = h("input", { id: "pw-new", type: "password", autocomplete: "new-password", minlength: "12", required: true });
  const nw2 = h("input", { id: "pw-new2", type: "password", autocomplete: "new-password", minlength: "12", required: true });
  const pErr = h("p", { class: "error", role: "alert" });
  const pwForm = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    pErr.textContent = "";
    if (nw.value !== nw2.value) { pErr.textContent = "The new passwords do not match."; return; }
    try {
      await api("/api/admin-password", { method: "POST", json: { current: has ? cur.value : null, new: nw.value } });
      cur.value = nw.value = nw2.value = "";
      toast("Password saved. You were signed out everywhere.");
      window.dispatchEvent(new CustomEvent("apg-auth-lost"));
    } catch (ex) { pErr.textContent = ex.message; }
  } },
  has ? field("Current password", cur) : null,
  field(has ? "New password (12+ characters)" : "Admin password (12+ characters)", nw),
  field("Repeat", nw2),
  h("button", { class: "primary", type: "submit" }, has ? "Change password" : "Set admin password"), pErr);

  box.replaceChildren(
    h("h2", {}, "Security"),
    h("h3", {}, "Admin password"),
    h("p", { class: "muted" }, has ? "Changing it signs every browser out and re-protects the stored provider keys." : "No admin password is set (you signed in with the server's API key). Set one to store provider keys and to sign in without that key."),
    h("div", { class: "card" }, pwForm),
    h("h3", {}, "API tokens"),
    h("p", { class: "muted" }, "For MCP clients and scripts. Prefer read-only tokens: they can search but can't change anything or spend provider credit."),
    h("div", { class: "card" }, tokenForm, tErr), reveal, table);
  renderTokenTable();
}
