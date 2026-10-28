// Live updates: the Requests page opens an EventSource on /api/requests/events and re-reads the list when "changed" arrives; when the
// stream is not live (no tcn, dropped connection) the poll is the fallback; leaving the page closes the stream.
// Run:  node tests/ui/live_updates.mjs        (no browser, no server: a tiny fake DOM, a fake EventSource and a stubbed fetch)
import { fail, installDom } from "./fakedom.mjs";

const sources = [];
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.on = {}; this.closed = false; sources.push(this); }
  addEventListener(t, f) { this.on[t] = f; }
  close() { this.closed = true; }
  emit(t, data) { this.on[t]({ data: JSON.stringify(data) }); }
};

let listLoads = 0;
const dom = await installDom({ hash: "#requests", routes: async (p) => {
  if (p.includes("/request-types")) return { types: [] };
  if (p.includes("/requests/counts")) return { by_status: { pending_review: 0, submitted: 1, needs_info: 0, approved: 0, rejected: 0 }, ai_flagged: 0 };
  if (p.includes("/validation/settings")) return { effective: 1 };
  if (p.includes("/requests?")) {
    listLoads += 1;
    return { items: [{ ref: "REQ-1", kind: "leave", employee: { name: "Ada" }, type_label: "Travel", title: "Flight", status: "submitted", validation_state: "running", submitted_at: "2026-01-02", ai: null }], total: 1 };
  }
  return {};
} });
const { initRequests, stopLive } = await import("/static/requests.js");
await initRequests();
if (sources.length !== 1 || sources[0].url !== "/api/requests/events") fail("the page must open one EventSource on /api/requests/events");
await initRequests();
if (sources.length !== 1) fail("opening the page again must reuse the stream");

// 1. live: no polling, an event refreshes
sources[0].emit("hello", { live: true });
let before = listLoads;
await dom.tick();
if (listLoads !== before) fail("while the stream is live the page must not poll");
sources[0].emit("changed", { live: true });
sources[0].emit("changed", { live: true });
await dom.tick();
if (listLoads !== before + 1) fail(`a burst of events must refresh once (loads went ${before} -> ${listLoads})`);

// 2. the stream says tcn is not available: the poll takes over
sources[0].emit("hello", { live: false });
before = listLoads;
await dom.tick();
if (listLoads <= before) fail("without live updates the poll must keep the list fresh");

// 3. the connection drops: fallback again, even after having been live
sources[0].emit("hello", { live: true });
before = listLoads;
await dom.tick();
if (listLoads !== before) fail("live again: no polling");
sources[0].onerror();
before = listLoads;
await dom.tick();
if (listLoads <= before) fail("after a stream error the poll must resume");

// 4. leaving the page closes the stream (browsers allow few connections per site)
stopLive();
if (!sources[0].closed) fail("stopLive must close the EventSource");
await initRequests();
if (sources.length !== 2) fail("coming back opens a new stream");
console.log("ok: live updates refresh once per burst, the poll is the fallback, the stream closes when leaving");
process.exit(0);
