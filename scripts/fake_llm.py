#!/usr/bin/env python3
"""DEMO STAND-IN MODEL. Not an AI model. Only for the video recording setup (docs/VIDEO-SCRIPT.md) and rehearsals.

A tiny OpenAI-compatible server (standard library only) that answers `/v1/chat/completions` and `/v1/embeddings`
without any model, so a recording is reproducible and needs no provider key:

* Chat, request validation: looks up the request by its title among the fictional Acme demo requests
  (`trustdesk.demo`) and returns a fixed, believable verdict. The quote is copied word for word from the
  retrieved policy passage that holds the rule, so TrustDesk's own quote check passes. A request it does not know
  gets a cautious "needs more information" with no citation.
* Chat, Ask: answers with the sentence of the retrieved passages that best matches the question, cited as [n].
* Embeddings: the same signed feature-hashing vectors as TrustDesk's offline embedder (lexical, not semantic).

It listens on loopback only, refuses any other address, and labels itself everywhere: the model name is
`demo-standin`, every response carries `X-Demo-Standin: scripted`, and the banner is printed at start.

    python scripts/fake_llm.py --port 8099
    LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_API_KEY=demo LLM_CHAT_MODEL=demo-standin \
    EMBEDDING_MODEL=demo-standin EMBEDDING_DIM=768 trustdesk serve
"""
import argparse
import hashlib
import itertools
import json
import math
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "demo-standin"
BANNER = "DEMO STAND-IN MODEL (scripted answers, no AI). For recordings and rehearsals only."

# title of a demo request -> (recommendation, compliance, summary, reason, keywords of the rule's sentence, what is missing)
# The first keyword that is found in the retrieved passages picks the sentence to quote: the quote is always taken from the
# passages the server was actually sent (which ones arrive depends on TrustDesk's retrieval, so a rule may need a second choice).
VERDICTS = {
    "Two weeks in Portugal": ("reject", "non_compliant",
                              "Two and a half weeks of vacation starting soon. The notice given is shorter than the policy asks for a vacation of this length, so rejection is advised.",
                              "A vacation of more than 10 working days needs at least 30 calendar days of notice, and the notice given is much shorter.",
                              ("more than 10 working days needs at least 30 calendar days",), []),
    "Long weekend": ("approve", "compliant",
                     "Four days of vacation with plenty of notice. The request fits the vacation policy, so approval is advised.",
                     "A vacation of up to 5 working days needs 7 calendar days of notice, and this request gives more than that.",
                     ("up to 5 working days needs at least 7 calendar days",), []),
    "Back pain": ("needs_info", "unclear",
                  "Five days of sick leave submitted after the fact, with no medical certificate. The policy asks for one, so more information is needed.",
                  "Sick leave of more than 3 consecutive working days needs a medical certificate, and none was provided.",
                  ("A medical certificate is required for any sick leave of more than 3",), ["A medical certificate for the absence, or the reason it cannot be provided."]),
    "Team dinner with Contoso": ("needs_info", "unclear",
                                 "A customer dinner of 450 USD. The cost per person is above the usual limit unless more people attended than stated.",
                                 "Customer meals are reimbursed up to 100 USD per person per event, and the attendee list does not clearly keep this one under the limit.",
                                 ("100 USD per person per event",), ["The full list of attendees with their company, and the itemised receipt."]),
    "Hotel in Chicago": ("reject", "non_compliant",
                         "One hotel night at 340 USD, above the nightly hotel limit. Higher rates need Director approval before booking, so rejection is advised unless that approval exists.",
                         "The hotel limit is 200 USD per night, and the claim is above it.",
                         ("The hotel limit is 200 USD per night",), ["Whether a Director approved the higher rate before booking."]),
    "London architecture summit": ("needs_info", "unclear",
                                   "A five-day trip to London estimated at 6,500 USD. It is within the trip length limit but needs Director approval because of the cost.",
                                   "Trips estimated between 3,000 and 10,000 USD need Director approval before anything is booked.",
                                   ("A Director approves trips between 3,000 and 10,000 USD",), ["Confirmation that a Director has approved the trip before booking."]),
    "Gift for a customer": ("reject", "non_compliant",
                            "A gift card for a customer. The policy forbids gift cards and caps gifts below the amount requested, so rejection is advised.",
                            "Cash and gift cards are never allowed as gifts to customers, and a gift may be worth at most 75 USD per person per year.",
                            ("Cash and gift cards are never allowed",), []),
    "Weekend incident": ("needs_info", "unclear",
                         "Twelve overtime hours worked on an incident. Overtime needs prior approval, or approval within 24 hours in an emergency.",
                         "Overtime must be approved by the manager before it is worked, or within 24 hours in an emergency; the request does not show that approval.",
                         ("Overtime must be approved by the manager before it is worked",), ["Who approved the overtime, and when."]),
    "Wedding": ("needs_info", "unclear",
                "Three days of vacation with enough notice, but the employee has been employed for only a short time, so HR should confirm nothing special applies before approving.",
                "A vacation of up to 5 working days needs at least 7 calendar days of notice, and this request gives more than that.",
                ("up to 5 working days needs at least 7 calendar days",), ["Whether the manager and HR agreed in writing: the employee is in the first 90 days of employment."]),
    "Working from Lisbon": ("needs_info", "unclear",
                            "Three weeks working from another country. The limit is 20 working days a year and Legal and Tax must approve in advance.",
                            "Working from another country needs approval from the manager and from Legal and Tax, requested at least 30 calendar days ahead.",
                            ("An employee may work from another country for up to 20 working days",), ["Approval from Legal and Tax."]),
    "External monitor": ("needs_info", "unclear",
                         "A monitor for 850 USD. That is above what the manager approves alone, so a Director has to approve it.",
                         "Expenses above 500 USD and up to 2,500 USD are approved by a Director.",
                         ("A Director approves expenses above 500", "Purchases from 200 up to 1,000 USD need prior written approval"), ["The Director's approval."]),
    "Lunch with auditor": ("approve", "compliant",
                           "A business lunch with the external auditor, with a receipt. It has a business purpose and fits the expense policy.",
                           "Expenses with a business purpose that follow the policy are reimbursed, and a receipt is attached.",
                           ("reasonable expenses that have a business purpose",), []),
    "Lunch with auditor (again)": ("needs_info", "unclear",
                                   "This lunch looks identical to another claim by the same employee for the same day and amount. Until that is explained it is unclear whether this expense is a second claim of the same meal.",
                                   "Expenses are reimbursed when they have a business purpose and follow the policy, and the system found a possible duplicate claim.",
                                   ("same expense must not be claimed twice", "reasonable expenses that have a business purpose"), ["Whether this was a separate lunch, or the same one claimed twice."]),
    "Carry over vacation": ("needs_info", "unclear",
                            "A request to carry over 8 vacation days. The policy allows 5, so this is an exception that needs the policy owner's approval.",
                            "At most 5 unused vacation days may be carried into the next year, so 8 days needs an exception from HR.",
                            ("Up to 5 unused vacation days may be carried",), ["HR's approval of the exception."]),
    "Adoption leave": ("needs_info", "unclear",
                       "Eight weeks of leave after an adoption, which matches the entitlement. HR needs the adoption papers and approves this leave.",
                       "Adoptive parents get the same leave as birth parents of the same role, and HR needs the placement papers.",
                       ("Adoptive parents receive the same leave as birth parents",), ["The adoption placement papers."]),
}

# Requests typed on camera in the video (docs/video/record.spec.ts): title -> (recommendation, compliance, summary, [(reason, keywords of the
# rule's sentence)], what is missing). Several reasons, each quoting its own passage. A reason whose rule was not retrieved is left out.
MULTI = {
    "Mountain long weekend": ("approve", "compliant",
                              "Three working days of vacation with plenty of notice. The notice is long enough and the manager can approve it alone, so approval is advised.",
                              [("A vacation of up to 5 working days needs at least 7 calendar days of notice, and this request gives much more.", ("up to 5 working days needs at least 7 calendar days",)),
                               ("The direct manager approves vacations of up to 10 working days, so no higher approval is needed.", ("The direct manager approves vacations of up to 10 working days",))],
                              []),
    "Three weeks at the coast": ("reject", "non_compliant",
                                 "Fifteen working days of vacation starting in two days. The notice is far shorter than the policy asks for, and a vacation this long needs Director approval, so rejection is advised.",
                                 [("A vacation of more than 10 working days needs at least 30 calendar days of notice, and this request gives 2 days.", ("more than 10 working days needs at least 30 calendar days",)),
                                  ("Vacations of 11 to 15 working days need Director approval, so the manager cannot approve this alone.", ("Vacations of 11 to 15 working days need Director approval",)),
                                  ("The Finance blackout period does not apply here: the employee works in Marketing, not in Finance.", ("Employees in the Finance department may not take vacation",))],
                                 []),
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "and", "or", "in", "is", "are", "for", "on", "at", "by", "how", "what", "do", "does", "we", "our", "can", "i", "my", "it"}


def passages_from(prompt: str) -> list[tuple[int, str]]:
    return [(int(n), body) for n, body in re.findall(r"\[(\d+)\] [^\n]*\n<passage>\n(.*?)\n</passage>", prompt, re.S)]


def sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            out.extend(s.strip() for s in _SENTENCE.split(line) if s.strip())
    return out


def find_sentence(passages: list[tuple[int, str]], keyword: str) -> tuple[int, str] | None:
    key = keyword.lower()
    for n, body in passages:
        for s in sentences(body):
            if key in s.lower() and len(s) >= 12:
                return n, s
    return None


def best_sentence(passages: list[tuple[int, str]], question: str) -> tuple[int, str] | None:
    want = {w for w in _WORD.findall(question.lower()) if w not in _STOP}
    best, score = None, 0
    for n, body in passages:
        for s in sentences(body):
            overlap = len(want & set(_WORD.findall(s.lower())))
            if overlap > score and len(s) >= 12:
                best, score = (n, s), overlap
    return best


def verdict_for(prompt: str) -> dict:
    passages = passages_from(prompt)
    m = re.search(r"<request>\n(.*?)\n</request>", prompt, re.S)
    try:
        title = json.loads(m.group(1)).get("title", "") if m else ""
    except ValueError:
        title = ""
    multi = MULTI.get(title)
    if multi:
        rec, comp, summary, reason_specs, missing = multi
        reasons, citations = [], []
        for text, keywords in reason_specs:
            hit = next((h for h in (find_sentence(passages, k) for k in keywords) if h), None)
            if hit:
                reasons.append({"text": text, "passages": [hit[0]]})
                citations.append({"passage": hit[0], "quote": hit[1]})
        if citations:
            return {"recommendation": rec, "compliance": comp, "summary": summary, "reasons": reasons,
                    "citations": citations, "missing_information": missing}
    known = VERDICTS.get(title)
    if known:
        rec, comp, summary, reason, keywords, missing = known
        hit = next((h for h in (find_sentence(passages, k) for k in keywords) if h), None)
        if hit:
            n, quote = hit
            return {"recommendation": rec, "compliance": comp, "summary": summary,
                    "reasons": [{"text": reason, "passages": [n]}], "citations": [{"passage": n, "quote": quote}],
                    "missing_information": missing}
    return {"recommendation": "needs_info", "compliance": "unclear",
            "summary": "The retrieved policy passages do not settle this request, so a person has to read the rules and decide.",
            "reasons": [{"text": "No retrieved passage clearly covers this request.", "passages": [passages[0][0]] if passages else []}],
            "citations": [], "missing_information": ["Which policy applies to this request."]}


def answer_for(prompt: str) -> str:
    passages = passages_from(prompt)
    q = re.search(r"Question:\s*(.*)\Z", prompt, re.S)
    hit = best_sentence(passages, q.group(1) if q else "")
    if not hit:
        return "I could not find this in the provided passages."
    n, s = hit
    return f"{s} [{n}]"


def hash_embed(text: str, dim: int) -> list[float]:
    """The same algorithm as trustdesk.embeddings.hash_embed (kept here so this script has no dependencies)."""
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for tok in tokens + [f"{a}_{b}" for a, b in itertools.pairwise(tokens)]:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def chat_text(messages: list[dict]) -> str:
    prompt = "\n".join(str(m.get("content", "")) for m in messages)
    if "POLICY PASSAGES" in prompt and "<request>" in prompt:
        return json.dumps(verdict_for(prompt))
    if "<passage>" in prompt and "Question:" in prompt:
        return answer_for(prompt)
    return "OK"


class Handler(BaseHTTPRequestHandler):
    server_version = "demo-standin"

    def log_message(self, fmt, *args):  # quiet: one short line per call, never the bodies
        sys.stderr.write(f"[demo stand-in] {self.command} {self.path.split('?')[0]}\n")

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Demo-Standin", "scripted")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"error": {"message": "invalid JSON"}})
        path = self.path.split("?")[0]
        if path.endswith("/chat/completions"):
            text = chat_text(body.get("messages") or [])
            return self._send(200, {"id": "chatcmpl-demo-standin", "object": "chat.completion", "model": MODEL,
                                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
        if path.endswith("/embeddings"):
            inputs = body.get("input")
            inputs = inputs if isinstance(inputs, list) else [str(inputs or "")]
            dim = int(body.get("dimensions") or 768)
            return self._send(200, {"object": "list", "model": MODEL,
                                    "data": [{"object": "embedding", "index": i, "embedding": hash_embed(str(t), dim)} for i, t in enumerate(inputs)],
                                    "usage": {"prompt_tokens": 0, "total_tokens": 0}})
        self._send(404, {"error": {"message": "not found"}})


def main() -> None:
    ap = argparse.ArgumentParser(description=BANNER)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("refusing to listen on a non-loopback address: this is a recording stand-in, not a service")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"{BANNER}\nListening on http://{args.host}:{args.port}/v1 (model name: {MODEL})", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
