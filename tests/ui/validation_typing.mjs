// Regression: Settings > Request validation shows the true saved choice, and the waiting-requests count refreshes on
// its own timer without rebuilding the form: the numbers the person is typing (and the focus) must survive it.
// Run:  node tests/ui/validation_typing.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, findAll, findId, installDom } from "./fakedom.mjs";

let waiting = 3, retrying = 2, gaveUp = 1, saved = null;
const settings = () => ({ account: saved, effective: saved === "none" ? null : "env", source: saved ? "saved" : "default",
  accounts: [{ id: "env", name: "Environment (server settings)", chat_model: "c" }, { id: "7", name: "gateway", chat_model: "chat-default" }],
  backlog: { waiting, retrying, gave_up: gaveUp, per_minute: 10, saved: false, default: 10, max: 60,
    max_attempts: 3, max_attempts_saved: false, max_attempts_default: 3, max_attempts_max: 10, backoff_minutes: [5, 15, 60] } });
const puts = [];
const dom = await installDom({ hash: "#settings", routes: async (p, init) => {
  if (p === "/api/accounts") return { vault: { initialized: true, unlocked: true }, accounts: [{ id: 7, name: "gateway", provider: "custom", base_url: "https://gw.example/v1/", has_key: true, api_key_last4: "abcd", chat_model: "chat-default", embedding_model: null, embedding_dim: null }],
    env: { id: "env", name: "Environment (server settings)", provider: "env", base_url: "https://gw.example/v1", has_key: true, chat_model: "chat-default", embedding_model: "e", embedding_dim: 8, read_only: true }, active_embedding: "env" };
  if (p === "/api/providers") return [];
  if (p.includes("/validation/settings")) {
    if (init.method === "PUT") { puts.push(init.body); saved = JSON.parse(init.body).account; }
    return settings();
  }
  if (p === "/api/embeddings/status") return { active: { account: "env", model: "e", dimension: 8, semantic: true, default: true }, job: { state: "idle" } };
  if (p === "/api/stats") return { chunks: 1 };
  if (p === "/api/tokens") return [];
  if (p === "/api/session") return { has_admin_password: true };
  return {};
} });
const { initSettings } = await import("/static/settings.js");
await initSettings();
await dom.settle();

const text = (el) => (el.children || []).map((c) => c.data ?? text(c)).join("");
const box = dom.el("settings-validation");
const select = findId(box, "val-account");
const rate = findId(box, "val-backlog");
if (!select || !rate) fail("the validation form was not rendered");
if (select.value !== "__default") fail("nothing is saved, so the select must show the default, not the first account: " + select.value);
const labels = findAll(select, (e) => e.tag === "option").map(text);
if (!labels.includes("Default (environment account)") || !labels.includes("None (no AI validation)")) fail("default and none options are missing: " + labels);
if (!text(box).includes("Using the environment account: default")) fail("the page does not say that the environment account is in use");
if (!text(dom.el("settings-accounts")).includes("Same endpoint as the environment account")) fail("the stored account on the same endpoint is not flagged");

rate.focus(); rate.value = "25"; rate.setSelectionRange(1, 1);
const attempts = findId(box, "val-attempts");
if (!attempts) fail("the attempts field was not rendered");
attempts.value = "4";
waiting = 9; retrying = 5; gaveUp = 7;
await dom.tick();                                   // every timer fires: the count refresh runs
const after = findId(dom.el("settings-validation"), "val-backlog");
if (after !== rate || after.value !== "25") fail("the refresh rebuilt the form and wiped the rate being typed");
if (document.activeElement !== rate) fail("the refresh took the focus away from the rate");
if (findId(dom.el("settings-validation"), "val-waiting").textContent !== "9") fail("the waiting count was not refreshed");
if (findId(dom.el("settings-validation"), "val-retrying").textContent !== "5") fail("the failed-and-will-retry count was not refreshed");
if (findId(dom.el("settings-validation"), "val-gaveup").textContent !== "7") fail("the failed-and-gave-up count was not refreshed");
if (findId(dom.el("settings-validation"), "val-attempts") !== attempts || attempts.value !== "4") fail("the refresh wiped the attempts being typed");

// Save: "none" must be sendable, shown afterwards, and confirmed on the page.
select.value = "none";
const save = findAll(box, (e) => e.tag === "button").find((b) => text(b) === "Save");
await save.listeners.click[0]({ target: save });
await dom.settle();
const box2 = dom.el("settings-validation");
if (findId(box2, "val-account").value !== "none") fail("the saved 'none' is not shown after saving");
if (!text(box2).includes("Saved.")) fail("saving gave no visible confirmation");
const sent = JSON.parse(puts[0]);
if (sent.account !== "none" || sent.backlog_per_minute !== 25 || sent.max_attempts !== 4) fail("wrong body sent: " + puts[0]);
console.log("ok: the validation form shows the saved state and survives the count refresh");
process.exit(0);
