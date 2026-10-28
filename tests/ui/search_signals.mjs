// Search results list the signals that matched; a passage the trigram signal found but the exact-word signal did not says "typo-tolerant match".
// Run:  node tests/ui/search_signals.mjs        (no browser, no server: a tiny fake DOM and a stubbed fetch)
import { fail, findAll, installDom } from "./fakedom.mjs";

const results = [
  { chunk_id: 1, doc_id: 1, title: "Vacation policy", source: "hr-vacation-policy.md", version: 1, heading: "Allowance", body: "25 days.", score: 0.0328, vec_rank: 1, lex_rank: 1, trgm_rank: null, trgm_score: null },
  { chunk_id: 2, doc_id: 2, title: "Travel policy", source: "travel-policy.md", version: 1, heading: "Booking", body: "Book early.", score: 0.0164, vec_rank: null, lex_rank: null, trgm_rank: 2, trgm_score: 0.45 },
  { chunk_id: 3, doc_id: 3, title: "Expense policy", source: "expense-policy.md", version: 1, heading: "Limits", body: "Keep receipts.", score: 0.03, vec_rank: 3, lex_rank: null, trgm_rank: 1, trgm_score: 0.6 },
  { chunk_id: 5, doc_id: 5, title: "Security policy", source: "security-policy.md", version: 1, heading: "Passwords", body: "Rotate.", score: 0.031, vec_rank: 2, lex_rank: 2, trgm_rank: 3, trgm_score: 0.5 },   // found by exact words too
  { chunk_id: 4, doc_id: 4, title: "Old", source: "old.md", version: 1, heading: "", body: "x", score: 0.01, vec_rank: 5, lex_rank: null },   // an older server: no trigram fields at all
];
let asked = "";
const dom = await installDom({ hash: "#search", routes: async (p) => {
  if (p.startsWith("/api/stats")) return { documents: 4, semantic: true };
  if (p.startsWith("/api/search")) { asked = p; return { query: "vaction polcy", results }; }
  return {};
} });
const { initSearch } = await import("/static/search.js");
await initSearch();
dom.el("q").value = "vaction polcy";
dom.el("search-form").fire("submit");
await dom.settle();
if (!asked.includes("q=vaction%20polcy")) fail("the query must be URL-encoded into /api/search: " + asked);

const texts = (el) => findAll(el, (e) => e.tag === "span").map((e) => e.children.map((c) => c.data ?? "").join("") + (e.textContent || ""));
const cards = findAll(dom.el("results"), (e) => e.tag === "article");
if (cards.length !== 5) fail("expected 5 result cards, got " + cards.length);
const hint = (c) => texts(c).includes("typo-tolerant match");
const has = (c, t) => texts(c).includes(t);
if (hint(cards[0]) || has(cards[0], "typo-tolerant #1")) fail("a vector + text match must not show a typo hint");
if (!hint(cards[1]) || !has(cards[1], "typo-tolerant #2")) fail("a trigram-only match must show the typo-tolerant hint and its rank");
if (!hint(cards[2]) || !has(cards[2], "typo-tolerant #1")) fail("a passage that the vector and trigram signals found but the exact words did not shows the typo hint");
if (hint(cards[3]) || !has(cards[3], "typo-tolerant #3")) fail("a passage that the exact words also found shows the trigram rank but not the hint");
if (hint(cards[4])) fail("a result without trigram fields must not show the hint");
if (findAll(dom.el("results"), (e) => e.attrs && "style" in e.attrs).length) fail("no inline styles");
console.log("ok: search results show the trigram signal and flag typo matches");
process.exit(0);
