// Regression: Settings polls a running re-embedding every second. When it finishes it must not rebuild the
// "Account to use" select, or the account the person is choosing at that moment is lost.
// Run:  node tests/ui/embeddings_typing.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, findId, installDom } from "./fakedom.mjs";

let job = { state: "running", done: 1, total: 10, message: "" };
const acct = (id, model) => ({ id, name: "Account " + id, provider: "custom", base_url: "https://x", has_key: true, chat_model: "c", embedding_model: model, embedding_dim: 8 });
const dom = await installDom({ hash: "#settings", routes: async (p) => {
  if (p === "/api/accounts") return { vault: { initialized: true, unlocked: true }, accounts: [acct(1, "e1"), acct(2, "e2")], env: null, active_embedding: 1 };
  if (p === "/api/providers") return [];
  if (p.includes("/validation/settings")) return { accounts: [], account: null, effective: null, backlog: { waiting: 0, retrying: 0, gave_up: 0, per_minute: 10, saved: false, default: 10, max: 60,
    max_attempts: 3, max_attempts_saved: false, max_attempts_default: 3, max_attempts_max: 10, backoff_minutes: [5, 15, 60] } };
  if (p === "/api/embeddings/status") return { active: { account: 1, model: "e1", dimension: 8, semantic: true }, job };
  if (p === "/api/stats") return { chunks: 10 };
  if (p === "/api/tokens") return [];
  if (p === "/api/session") return { has_admin_password: true };
  return {};
} });
const { initSettings } = await import("/static/settings.js");
await initSettings();
await dom.settle();

const select = findId(dom.el("settings-embeddings"), "emb-select");
if (!select) fail("the embeddings select was not rendered");
select.value = "2";                                 // the person is choosing the next account while the job runs
select.focus();
job = { state: "done", done: 10, total: 10, message: "" };
await dom.tick();                                   // the poll sees the job finish

const after = findId(dom.el("settings-embeddings"), "emb-select");
if (after !== select || after.value !== "2") fail("the end of the re-embedding rebuilt the select and lost the choice");
if (document.activeElement !== select) fail("the end of the re-embedding took the focus away from the select");
console.log("ok: the chosen account survives the end of a re-embedding");
process.exit(0);
