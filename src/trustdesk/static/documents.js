import { api, ApiError } from "/kit/api.js";
import { can } from "/kit/session.js";
import { $, confirmDialog, fmtDate, h, toast } from "/kit/ui.js";

const MAX_BYTES = 10 * 1024 * 1024;
const OK_EXT = /\.(md|markdown|txt|pdf)$/i;
let wired = false;

export async function initDocuments() {
  // Reading is for everyone with kb:read; uploading and deleting need kb:write, loading the demo policies needs settings:manage.
  const writer = can("kb:write");
  for (const el of [$("dropzone"), $("doc-type").closest(".field"), $("uploads")]) el.hidden = !writer;
  $("load-demo").hidden = !can("settings:manage");
  if (!wired) {
    wired = true;
    const dz = $("dropzone");
    dz.addEventListener("click", () => $("file-input").click());
    dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file-input").click(); } });
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("over"));
    dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload([...e.dataTransfer.files]); });
    $("file-input").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
    $("load-demo").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const d = await api("/api/seed", { method: "POST" });
        const added = d.results.filter((r) => r.status === "ingested").length;
        toast(added ? `Loaded ${added} demo policies (security, HR, travel and expenses)` : "Demo policies already loaded");
        load();
      } catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
    });
  }
  await load();
}

async function load() {
  try {
    const docs = await api("/api/documents");
    $("doc-body").replaceChildren(...docs.map((d) => h("tr", {},
      h("td", { class: "title" }, d.title),
      h("td", {}, h("span", { class: "badge plain" }, d.doc_type)),
      h("td", { class: "source col-source" }, d.source),
      h("td", { class: "num" }, String(d.version)),
      h("td", { class: "num" }, String(d.chunks)),
      h("td", { class: "col-added" }, fmtDate(d.created_at)),
      h("td", { class: "actions" }, h("div", { class: "btns" },
        h("button", { class: "secondary small", type: "button", onclick: () => view(d.id) }, "View"),
        can("kb:write") ? h("button", { class: "danger small", type: "button", onclick: () => remove(d) }, "Delete") : null)))));
    $("doc-table").hidden = docs.length === 0;
    $("doc-empty").hidden = docs.length !== 0;
    $("doc-detail").hidden = true;
  } catch (err) { toast(err.message, true); }
}

async function view(id) {
  try {
    const d = await api("/api/documents/" + id);
    const box = $("doc-detail");
    box.replaceChildren(
      h("div", { class: "row" }, h("h2", {}, d.title), h("button", { class: "secondary small", type: "button", onclick: () => { box.hidden = true; } }, "Close")),
      h("p", { class: "muted" }, `${d.source} · version ${d.version} · ${d.chunks.length} chunks`),
      ...d.chunks.map((c) => h("div", { class: "chunk" }, h("h4", {}, c.heading || "(intro)"), h("p", {}, c.body))));
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { toast(err.message, true); }
}

async function remove(d) {
  if (!(await confirmDialog(`Delete "${d.title}" and its ${d.chunks} chunks? This cannot be undone.`, { title: "Delete document", confirmLabel: "Delete", danger: true }))) return;
  try { await api("/api/documents/" + d.id, { method: "DELETE" }); toast("Deleted " + d.title); load(); } catch (err) { toast(err.message, true); }
}

async function upload(files) {
  for (const f of files) {
    const bar = h("progress", { max: "1" });
    const state = h("span", { class: "muted" }, "Uploading…");
    $("uploads").prepend(h("li", {}, h("span", { class: "name" }, f.name), bar, state));
    const fail = (m) => { bar.remove(); state.className = "bad"; state.textContent = m; };
    if (!OK_EXT.test(f.name)) { fail("Unsupported type"); continue; }
    if (f.size > MAX_BYTES) { fail("Over 10 MB"); continue; }
    try {
      const form = new FormData();
      form.append("file", f);
      const r = await api("/api/documents?doc_type=" + encodeURIComponent($("doc-type").value), { method: "POST", body: form });
      bar.remove();
      state.className = "ok";
      state.textContent = r.status === "unchanged" ? "Already up to date" : `Added (${r.chunks} chunks)`;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return;
      fail(err.message);
    }
  }
  load();
}
