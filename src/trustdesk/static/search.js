import { api } from "/kit/api.js";
import { $, copy, h, toast } from "/kit/ui.js";

const EXAMPLES = [
  "How do we encrypt data at rest?",
  "How fast do you notify customers of a breach?",
  "What are your RTO and RPO?",
  "Do you require MFA?",
  "How long do you retain customer data?",
];
let ready = false;

export async function initSearch() {
  if (!ready) {
    ready = true;
    for (const q of EXAMPLES) {
      $("chips").append(h("button", { class: "chip", type: "button", onclick: () => { $("q").value = q; run(); } }, q));
    }
    $("search-form").addEventListener("submit", (e) => { e.preventDefault(); run(); });
  }
  $("q").focus();
  try {
    const s = await api("/api/stats");
    const b = $("banner");
    if (s.reembedding) {
      b.hidden = false;
      b.textContent = "Re-embedding is in progress. Search is paused until it finishes.";
    } else if (s.needs_reembedding) {
      b.hidden = false;
      b.replaceChildren("Needs re-embedding: the index stores ", String(s.stored_dimension), "-dimension vectors but the embedder produces ", String(s.dimension),
        ". Search matches words only until it is re-embedded. ", h("a", { href: "#settings" }, "Open Settings"), " and use the embedding account again, or run `trustdesk reembed`.");
    } else if (s.documents === 0) {
      b.hidden = false;
      b.replaceChildren("Your knowledge base is empty. ", h("a", { href: "#documents" }, "Upload documents or load the demo policies"), " to start searching.");
    } else if (!s.semantic) {
      b.hidden = false;
      b.replaceChildren("Demo embedder active: search matches words, not meaning. ", h("a", { href: "#settings" }, "Add a provider account"), " and switch embeddings for semantic search.");
    } else {
      b.hidden = true;
    }
  } catch { /* the app shell already reports auth and availability problems */ }
}

async function run() {
  const q = $("q").value.trim();
  if (!q) return;
  const out = $("results");
  out.replaceChildren(h("p", { class: "loading" }, "Searching…"));
  try {
    const d = await api("/api/search?limit=8&q=" + encodeURIComponent(q));
    out.replaceChildren(...(d.results.length ? d.results.map(card) : [h("p", { class: "empty" }, "No matching passages found.")]));
  } catch (err) { out.replaceChildren(); toast(err.message, true); }
}

function card(r) {
  const crumb = r.heading || r.title;
  const citation = `${r.title}, ${crumb} (v${r.version}, ${r.source})`;
  return h("article", { class: "card result" },
    h("h3", {}, r.title),
    h("div", { class: "crumb" }, `${crumb} · v${r.version} · ${r.source}`),
    h("p", { class: "body" }, r.body),
    h("div", { class: "badges" },
      r.vec_rank != null ? h("span", { class: "badge" }, "vector #" + r.vec_rank) : null,
      r.lex_rank != null ? h("span", { class: "badge alt" }, "text #" + r.lex_rank) : null,
      h("span", { class: "badge plain" }, "score " + r.score.toFixed(4)),
      h("span", { class: "spacer" }),
      h("button", { class: "secondary small", type: "button", onclick: () => copy(citation, "Citation copied") }, "Copy citation")));
}
