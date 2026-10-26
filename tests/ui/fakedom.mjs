// A tiny fake DOM for the browser modules (no browser, no server). Shared by the *_typing.mjs checks.
// It models what matters for "a redraw wiped what the person was typing": element identity, value, focus and caret,
// and (as a real browser does) it drops the focus when the focused element is removed from the page.
import { pathToFileURL } from "node:url";
import { register } from "node:module";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname);
const STATIC = path.resolve(here, "../../src/trustdesk/static");
const KIT = path.resolve(here, "../../../agentpg-kit/src/apgkit/static");
const loader = `export async function resolve(s, c, n) {
  if (s.startsWith("/kit/")) return { url: ${JSON.stringify(pathToFileURL(KIT).href)} + "/" + s.slice(5), shortCircuit: true };
  if (s.startsWith("/static/")) return { url: ${JSON.stringify(pathToFileURL(STATIC).href)} + "/" + s.slice(8), shortCircuit: true };
  return n(s, c);
}`;
register("data:text/javascript," + encodeURIComponent(loader));

const modals = [];   // open modal dialogs, last = topmost
const contains = (root, el) => root === el || (root.children || []).some((c) => contains(c, el));

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.value = ""; this.listeners = {}; this.classes = new Set(); this.classList = { toggle: (c, on) => { if (on === undefined ? !this.classes.has(c) : on) this.classes.add(c); else this.classes.delete(c); }, add: (c) => this.classes.add(c), remove: (c) => this.classes.delete(c), contains: (c) => this.classes.has(c) }; this.style = {}; this.className = ""; this.disabled = false; Object.defineProperty(this, "parent", { value: null, writable: true, enumerable: false }); this.textContent = ""; this.open = false; this.selectionStart = null; this.selectionEnd = null; }
  append(...k) { for (const x of k) if (x && x.nodeType === 1) x.parent = this; this.children.push(...k); }
  prepend(...k) { this.children.unshift(...k); }
  replaceChildren(...k) {
    const old = this.children;
    this.children = k;
    for (const x of k) if (x && x.nodeType === 1) x.parent = this;
    const a = document.activeElement;
    if (a && old.some((c) => contains(c, a)) && !k.some((c) => contains(c, a))) document.activeElement = null;   // a removed element loses focus
  }
  remove() {   // like a browser: a removed element leaves the page and loses the focus
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
    const a = document.activeElement;
    if (a && contains(this, a)) document.activeElement = null;
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  fire(t, ev = {}) { const e = { target: this, preventDefault() {}, ...ev }; for (const f of this.listeners[t] || []) f(e); for (let n = this.parent; n; n = n.parent) for (const f of n.listeners[t] || []) f(e); }
  focus() { document.activeElement = this; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  // A native <dialog>: showModal() opens it on top of the modal stack, close() leaves it, and the browser fires "cancel" on Escape.
  showModal() { this.open = true; this.attrs.open = ""; modals.push(this); }
  close() { this.open = false; delete this.attrs.open; const i = modals.indexOf(this); if (i >= 0) modals.splice(i, 1); }
  scrollIntoView() {}
  get nodeType() { return 1; }
  querySelector() { return null; } querySelectorAll() { return []; } matches() { return false; }
}

export const find = (el, pred) => (pred(el) ? el : (el.children || []).map((c) => find(c, pred)).find(Boolean));
export const findId = (el, id) => find(el, (e) => e.attrs?.id === id);
export const findAll = (el, pred) => [...(pred(el) ? [el] : []), ...(el.children || []).flatMap((c) => findAll(c, pred))];

// Installs the globals and returns helpers. `routes(path, init)` answers every fetch with a JSON body.
export async function installDom({ hash, routes, perms = ["*"] }) {
  const els = {};
  globalThis.document = { body: new El("body"), activeElement: null, hidden: false, documentElement: { dataset: { csrf: "X-TrustDesk-CSRF" } }, getElementById: (id) => Object.values(els).map((r) => find(r, (e) => e.attrs?.id === id)).find(Boolean) || (els[id] ||= new El("#" + id)),   // like a page: a created element is found once it is attached
    createElement: (t) => new El(t), createElementNS: (_, t) => new El(t), createTextNode: (d) => ({ nodeType: 3, data: d }), querySelectorAll: () => [] };
  globalThis.window = { dispatchEvent() {}, addEventListener() {} };
  globalThis.CustomEvent = class {};
  for (const n of ["confirm", "alert", "prompt"]) globalThis[n] = () => { throw new Error(`native ${n}() called: use confirmDialog/promptDialog`); };   // a native dialog would freeze a real browser
  globalThis.location = { origin: "http://x", hash, search: "", pathname: "/" };
  globalThis.fetch = async (p, init) => {
    const body = await routes(p, init || {});
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => body, text: async () => JSON.stringify(body) };
  };
  const { state } = await import("/kit/session.js");
  state.session = { state: "logged_in", user: { permissions: perms } };
  // Timers: nothing fires by itself. A test runs the pending ones by hand, in order.
  const realSetTimeout = globalThis.setTimeout, realSetInterval = globalThis.setInterval;
  const timers = [];
  globalThis.setTimeout = (f) => { const t = { f, kind: "timeout", live: true }; timers.push(t); return t; };
  globalThis.clearTimeout = (t) => { if (t) t.live = false; };
  globalThis.setInterval = (f) => { const t = { f, kind: "interval", live: true }; timers.push(t); return t; };
  globalThis.clearInterval = (t) => { if (t) t.live = false; };
  const settle = () => new Promise((r) => realSetTimeout(r, 30));
  // Fire every live timer once (a timeout is then spent), then let the async refresh it started finish.
  const tick = async () => {
    for (const t of timers.filter((x) => x.live)) { if (t.kind === "timeout") t.live = false; await t.f(); }
    await settle();
  };
  // The shared modal is a native <dialog role="alertdialog"> under <body>: these find it (while it is open) and its buttons.
  const dialog = () => find(document.body, (e) => e.tag === "dialog" && e.attrs.role === "alertdialog" && e.open);
  const escape = () => { const top = modals[modals.length - 1]; if (top) top.fire("cancel"); };   // what Escape does to a modal dialog
  const button = (label) => find(dialog(), (e) => e.tag === "button" && e.children.some((c) => c.data === label));
  return { dialog, button, escape, el: (id) => document.getElementById(id), timers, tick, settle, realSetTimeout, realSetInterval };
}

export function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }
