"""The demo stand-in model (scripts/fake_llm.py): its scripted verdicts must pass TrustDesk's own checks, so a recording is stable."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from trustdesk import demo, seedpack, validator
from trustdesk.chunking import chunk_markdown

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fake_llm.py"
spec = importlib.util.spec_from_file_location("fake_llm", SCRIPT)
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


def corpus() -> list[dict]:
    out = []
    for f in seedpack.files():
        for c in chunk_markdown(f.read_text()):
            out.append({"title": f.stem, "heading": c.heading, "version": 1, "source": f.name, "text": c.body,
                        "chunk_id": len(out) + 1, "doc_id": 1})
    return out


def passages_for(rule_keyword: str) -> list[dict]:
    """The passage that holds the rule first (as retrieval would rank it), then two unrelated ones."""
    pool = corpus()
    first = next(p for p in pool if rule_keyword.lower() in p["text"].lower())
    rest = [p for p in pool if p is not first][:2]
    return [{**p, "n": i} for i, p in enumerate([first, *rest], 1)]



def test_every_new_demo_request_has_a_scripted_verdict():
    titles = {r["title"] for r in demo.requests(date.today()) if not r["history"]}
    assert titles == set(fake.VERDICTS)
    assert {v[0] for v in fake.VERDICTS.values()} == {"approve", "reject", "needs_info"}


@pytest.mark.parametrize("title", sorted(fake.VERDICTS))
def test_scripted_verdict_survives_the_real_verdict_checks(title):
    rec, _, _, _, keywords, _ = fake.VERDICTS[title]
    passages = passages_for(keywords[-1])   # the last choice: the one that has to work when only the weakest rule was retrieved
    request = {"type_label": "x", "title": title, "description": "d", "start_date": None, "end_date": None, "amount": None,
               "currency": None, "details": {}}
    messages = validator.build_messages(request, {}, passages)
    reply = json.loads(fake.chat_text(messages))
    v = validator.parse_verdict(json.dumps(reply), passages, [])
    assert v["recommendation"] == rec and v["warnings"] == []
    assert v["citations"] and all(c["verified"] for c in v["citations"])


def test_unknown_request_gets_a_cautious_answer_and_ask_cites_a_passage():
    passages = passages_for("25 working days")
    request = {"type_label": "x", "title": "Something else", "description": "d", "start_date": None, "end_date": None,
               "amount": None, "currency": None, "details": {}}
    reply = json.loads(fake.chat_text(validator.build_messages(request, {}, passages)))
    assert reply["recommendation"] == "needs_info" and reply["citations"] == []
    from trustdesk import ask
    out = fake.chat_text(ask.build_messages("How many vacation days do employees get?", [{**p, "body": p["text"]} for p in passages]))
    assert out.endswith("[1]") and "25 working days" in out
    assert fake.chat_text([{"role": "user", "content": "Reply with the single word OK."}]) == "OK"


def test_embeddings_match_the_offline_embedder():
    from trustdesk.embeddings import hash_embed
    assert fake.hash_embed("vacation notice period", 64) == hash_embed("vacation notice period", 64)
