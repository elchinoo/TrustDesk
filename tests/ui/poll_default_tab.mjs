// Regression: right after sign-in the address has no hash and the Requests tab is the default. While a request is still being validated
// its list must keep refreshing by itself (it used to poll only when the address ended in #requests, so the badges stayed at "...").
// Run:  node tests/ui/poll_default_tab.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, installDom } from "./fakedom.mjs";

let listLoads = 0;
const dom = await installDom({ hash: "", routes: async (p) => {
  if (p.includes("/request-types")) return { types: [] };
  if (p.includes("/requests/counts")) return { by_status: { pending_review: 0, submitted: 1, needs_info: 0, approved: 0, rejected: 0 }, ai_flagged: 0 };
  if (p.includes("/validation/settings")) return { effective: 1 };
  if (p.includes("/requests?")) {
    listLoads += 1;
    return { items: [{ ref: "REQ-1", kind: "leave", employee: { name: "Ada" }, type_label: "Travel", title: "Flight", status: listLoads > 1 ? "pending_review" : "submitted",
      validation_state: listLoads > 1 ? "done" : "running", submitted_at: "2026-01-02", ai: null }], total: 1 };
  }
  return {};
} });
const { initRequests } = await import("/static/requests.js");
await initRequests();
const first = listLoads;
await dom.tick();                                    // the poll timer fires
if (listLoads <= first) fail("the list did not refresh by itself on the default tab (no #requests in the address)");
console.log("ok: the Requests list keeps refreshing on the default tab");
process.exit(0);
