// Regression: destructive actions in Settings ask through the shared modal (confirmDialog), never a native confirm() that would
// freeze an automated browser. Deleting an account needs its name typed, the typed text survives the page's timers, and
// nothing is sent to the server until the person confirms. (fakedom throws if confirm/alert/prompt is ever called.)
// Run:  node tests/ui/confirm_dialogs.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, findAll, findId, installDom } from "./fakedom.mjs";

const calls = [];
const acct = (id, model) => ({ id, name: "Account " + id, provider: "custom", base_url: "https://x", has_key: true, chat_model: "c", embedding_model: model, embedding_dim: 8 });
const dom = await installDom({ hash: "#settings", routes: async (p, init) => {
  if (init.method && init.method !== "GET") calls.push(init.method + " " + p);
  if (p === "/api/accounts") return { vault: { initialized: true, unlocked: true }, accounts: [acct(1, "e1"), acct(2, "e2")], env: null, active_embedding: 1 };
  if (p === "/api/providers") return [];
  if (p.includes("/validation/settings")) return { accounts: [], account: null, effective: null, backlog: { waiting: 0, retrying: 0, gave_up: 0, per_minute: 10, saved: false, default: 10, max: 60,
    max_attempts: 3, max_attempts_saved: false, max_attempts_default: 3, max_attempts_max: 10, backoff_minutes: [5, 15, 60] } };
  if (p === "/api/embeddings/status") return { active: { account: 1, model: "e1", dimension: 8, semantic: true }, job: { state: "idle" } };
  if (p === "/api/stats") return { chunks: 10 };
  if (p === "/api/tokens") return [{ id: 5, name: "ci", prefix: "tdk_ab", scope: "read", created_at: null, last_used_at: null, revoked_at: null }];
  if (p === "/api/session") return { has_admin_password: true };
  return {};
} });
const { initSettings } = await import("/static/settings.js");
await initSettings();
await dom.settle();

const label = (b) => (b.children || []).map((c) => c.data ?? "").join("");
const click = (root, text, nth = 0) => { const b = findAll(root, (e) => e.tag === "button" && label(e) === text)[nth]; if (!b) fail(`no "${text}" button`); b.fire("click"); };
const open = () => dom.dialog();
const flush = () => dom.settle();

// 1. Delete an account: the dialog asks, the name must be typed, Cancel sends nothing, confirming sends the DELETE.
click(dom.el("settings-accounts"), "Delete"); await flush();
if (!open()) fail("deleting an account opened no dialog");
const input = findAll(open(), (e) => e.tag === "input")[0];
if (!input) fail("deleting an account should ask for the account name to be typed");
if (!dom.button("Delete").disabled) fail("Delete should stay disabled until the name is typed");
input.value = "Account 1"; input.fire("input");
await dom.tick();                                           // the page's timers run while the dialog is open
if (!open() || !findAll(open(), (e) => e === input).length || input.value !== "Account 1") fail("a page timer wiped the dialog or the typed name");
dom.button("Cancel").fire("click"); await flush();
if (open() || calls.length) fail("Cancel must close the dialog and send nothing");

click(dom.el("settings-accounts"), "Delete"); await flush();
const input2 = findAll(open(), (e) => e.tag === "input")[0];
input2.value = "Account 1"; input2.fire("input");
dom.button("Delete").fire("click"); await flush();
if (!calls.includes("DELETE /api/accounts/1")) fail("confirming did not delete the account: " + calls.join(","));

// 2. Revoke a token: a plain danger dialog, nothing sent until confirmed.
calls.length = 0;
click(dom.el("settings-security"), "Revoke"); await flush();
if (!open() || findAll(open(), (e) => e.tag === "input").length) fail("revoking a token should open a plain confirm without an input");
dom.button("Cancel").fire("click"); await flush();
if (calls.length) fail("Cancel on revoke sent a request");
click(dom.el("settings-security"), "Revoke"); await flush();
dom.button("Revoke").fire("click"); await flush();
if (!calls.includes("DELETE /api/tokens/5")) fail("confirming did not revoke the token: " + calls.join(","));

// 3. Use for embeddings: asks first; Cancel changes nothing, Confirm activates.
calls.length = 0;
const select = findId(dom.el("settings-embeddings"), "emb-select");
select.value = "2";
click(dom.el("settings-embeddings"), "Use for embeddings"); await flush();
if (!open() || !JSON.stringify(open()).includes("2")) fail("no embeddings confirmation");
if (calls.length) fail("the embeddings account was switched before the person confirmed");
dom.button("Cancel").fire("click"); await flush();
if (calls.length) fail("Cancel switched the embeddings account");
click(dom.el("settings-embeddings"), "Use for embeddings"); await flush();
dom.button("Use for embeddings").fire("click"); await flush();
if (!calls.includes("POST /api/embeddings/activate")) fail("confirming did not activate: " + calls.join(","));

console.log("ok: delete account, revoke token and switch embeddings all ask through the shared dialog");
process.exit(0);
