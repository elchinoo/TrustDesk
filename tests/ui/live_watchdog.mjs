// SSE watchdog: an ingress that buffers the response leaves the EventSource CONNECTING for ever (no hello, no error). The Requests page
// must give up after LIVE_TIMING.helloMs, close the stream and poll, retry the stream ONCE after LIVE_TIMING.retryMs, and stop there.
// Run:  node tests/ui/live_watchdog.mjs        (fake DOM, fake EventSource, fake timers: nothing really waits)
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
const { initRequests, stopLive, LIVE_TIMING } = await import("/static/requests.js");
const pending = (ms) => dom.timers.filter((t) => t.live && t.ms === ms);
const fire = async (ms) => { const t = pending(ms)[0]; if (!t) fail(`no pending ${ms} ms timer`); t.live = false; await t.f(); await dom.settle(); };

// 1. the stream never says hello: a watchdog is armed, and firing it closes the stream and starts polling
await initRequests();
if (sources.length !== 1) fail("the page must open one EventSource");
if (pending(LIVE_TIMING.helloMs).length !== 1) fail("a hello watchdog must be armed when the stream is opened");
let before = listLoads;
await fire(LIVE_TIMING.helloMs);
if (!sources[0].closed) fail("the watchdog must close a stream that never said hello");
await initRequests();
if (sources.length !== 1) fail("while the watchdog has tripped, reopening the page must not open another stream before the retry");
if (pending(LIVE_TIMING.retryMs).length !== 1) fail("one retry must be scheduled");
await fire(2500);   // the poll timer (the page's schedulePoll)
if (listLoads <= before) fail("after the watchdog the poll must keep the list fresh");

// 2. the retry opens a new stream; it never says hello either: closed again and NO further retry
await fire(LIVE_TIMING.retryMs);
if (sources.length !== 2) fail("the retry must open a second stream");
if (pending(LIVE_TIMING.helloMs).length !== 1) fail("the retried stream has its own watchdog");
await fire(LIVE_TIMING.helloMs);
if (!sources[1].closed) fail("the second silent stream must be closed too");
if (pending(LIVE_TIMING.retryMs).length !== 0) fail("only one retry is allowed");
await initRequests();
if (sources.length !== 2) fail("no third stream in the same visit");

// 3. leaving the page resets everything: a new visit streams again
stopLive();
await initRequests();
if (sources.length !== 3 || pending(LIVE_TIMING.helloMs).length !== 1) fail("a new visit opens a stream with a watchdog");

// 4. a hello in time disarms the watchdog: the stream stays, nothing polls
sources[2].emit("hello", { live: true });
if (pending(LIVE_TIMING.helloMs).length !== 0) fail("hello must disarm the watchdog");
if (sources[2].closed) fail("a live stream must stay open");

// 5. leaving the page cancels a pending retry
stopLive();
await initRequests();
await fire(LIVE_TIMING.helloMs);          // silent again: retry scheduled
if (pending(LIVE_TIMING.retryMs).length !== 1) fail("retry scheduled again after a new visit");
stopLive();
if (pending(LIVE_TIMING.retryMs).length !== 0 || pending(LIVE_TIMING.helloMs).length !== 0) fail("stopLive must cancel the watchdog and the retry");
console.log("ok: no hello in 6 s closes the stream and polls; one retry after 60 s; a hello disarms it; leaving the page cancels it");
process.exit(0);
