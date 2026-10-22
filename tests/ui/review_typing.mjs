// Regression: while the AI is (re)validating a request, the review window refreshes itself every 2.5 s.
// The reviewer's note (and where the caret is) must survive that, also when the refresh brings in the AI advice.
// Run:  node tests/ui/review_typing.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, findId, installDom } from "./fakedom.mjs";

const request = (extra) => ({ ref: "REQ-1", type_label: "Travel", submitted_at: "2026-01-02T10:00:00Z", source: "api", status: "pending_review", validation_state: "running",
  validation: null, employee: { name: "Ada", employee_no: "E1" }, events: [], title: "Flight", description: "Flight to Berlin", details: {}, ...extra });
const advice = { state: "ok", recommendation: "reject", compliance: "non_compliant", summary: "Too expensive", checks: [], passages: [], warnings: [], reasons: [], missing: [], citations: [], model: "m", account: "a", created_at: "2026-01-02T10:01:00Z", duration_ms: 5 };
let detail = request({});
const dom = await installDom({ hash: "#requests", routes: async (p) => {
  if (p.includes("/request-types")) return { types: [] };
  if (p.includes("/requests/counts")) return { by_status: { pending_review: 1, submitted: 0, needs_info: 0, approved: 0, rejected: 0 }, ai_flagged: 0 };
  if (p.includes("/validation/settings")) return { effective: 1 };
  if (p.includes("/requests/REQ-1")) return detail;
  if (p.includes("/requests?")) return { items: [{ ref: "REQ-1", kind: "leave", employee: { name: "Ada" }, type_label: "Travel", title: "Flight", status: "pending_review", validation_state: "running", submitted_at: "2026-01-02", ai: null }], total: 1 };
  return {};
} });
const { initRequests } = await import("/static/requests.js");
await initRequests();

const row = dom.el("req-body").children[0];
if (!row) fail("the request list was not rendered");
await row.listeners.click[0]();                    // open the review window
await dom.settle();
const note = findId(dom.el("review-body"), "decision-note");
if (!note) fail("the decision note was not rendered");
note.focus(); note.value = "needs a receipt"; note.setSelectionRange(5, 5);

await dom.tick();                                    // the poll fires: still validating
detail = request({ validation: advice, validation_state: "done" });
await dom.tick();                                    // the poll fires: the AI advice arrives

const after = findId(dom.el("review-body"), "decision-note");
if (!after || after.value !== "needs a receipt") fail("the refresh wiped the reviewer's note");
if (document.activeElement !== after) fail("the refresh took the focus away from the note");
if (after.selectionStart !== 5) fail("the refresh moved the caret");
if (!JSON.stringify(dom.el("review-body")).includes("Suggests rejection") && !JSON.stringify(dom.el("review-body")).includes("Too expensive")) fail("the refresh did not bring in the AI advice");
console.log("ok: the note, focus and caret survive the review refresh");
process.exit(0);
