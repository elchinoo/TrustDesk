"""Compliance workflow integration tests: employees, requests, AI pre-validation, decisions, reports, scopes."""
import json
import os
import re
from datetime import date, timedelta

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import accounts, migrate, validator, vault  # noqa: E402
from trustdesk import ask as asker  # noqa: E402
from trustdesk.db import connect  # noqa: E402
from trustdesk.llm_http import LLMError  # noqa: E402

ADMIN = {"Authorization": "Bearer test-static-key-0123456789-abcdefghij-xyz"}
TODAY = date.today()


def _d(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "test-static-key-0123456789-abcdefghij-xyz")
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()


class FakeModel:
    """Stands in for the chat provider: answers with a verdict that quotes the first passage it was given."""

    def __init__(self, recommendation="approve", compliance=None, **extra):
        self.recommendation, self.compliance, self.extra, self.calls = recommendation, compliance, extra, 0

    def __call__(self, cfg, messages, max_tokens=700):
        self.calls += 1
        text = messages[1]["content"]
        m = re.search(r"\[1\] .*?<passage>\n(.*?)\n</passage>", text, re.S)
        quote = " ".join(m.group(1).split()[:8]) if m else ""
        body = {"recommendation": self.recommendation,
                "compliance": self.compliance or {"approve": "compliant", "reject": "non_compliant"}.get(
                    self.recommendation, "unclear"),
                "summary": "Fake summary.", "reasons": [{"text": "Because of the cited rule.", "passages": [1]}],
                "citations": [{"passage": 1, "quote": quote}], "missing_information": [], **self.extra}
        return {"text": json.dumps(body), "usage": {"total_tokens": 7}}


@pytest.fixture
def model(monkeypatch):
    """A configured chat account (so validation runs) with a controllable fake model."""
    fake = FakeModel()
    cfg = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: cfg)
    monkeypatch.setattr(asker, "chat", fake)
    return fake


@pytest.fixture
def seeded(client):
    assert client.post("/api/seed", headers=ADMIN).status_code == 200


def tokens(client):
    out = {}
    for scope in ("client", "read", "metrics", "full"):
        r = client.post("/api/tokens", headers=ADMIN, json={"name": scope, "scope": scope})
        assert r.status_code == 200, r.text
        out[scope] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def employee(client, no="E1", name="Ann Example", **kw):
    body = {"employee_no": no, "name": name, "email": f"{no.lower()}@example.com", "department": "Engineering",
            "title": "Engineer", "level": "L3", "country": "US", "hire_date": _d(-800), **kw}
    r = client.post("/api/v1/employees", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _monday(n: int) -> date:
    d = TODAY + timedelta(days=n)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def vacation(client, headers=ADMIN, emp="E1", **kw):
    body = {"employee_no": emp, "type": "vacation", "title": "Holiday", "description": "Time at the beach",
            "start_date": _monday(60).isoformat(), "end_date": (_monday(60) + timedelta(days=4)).isoformat(), **kw}
    return client.post("/api/v1/requests", headers=headers, json=body)


# ---- employees ---------------------------------------------------------------------------------------
def test_employee_lifecycle_and_validation(client):
    boss = employee(client, "B1", "Bea Boss")
    assert boss["manager_no"] is None and boss["status"] == "active"
    e = employee(client, "E1", manager_no="B1", country="de")
    assert e["manager_no"] == "B1" and e["country"] == "DE"
    assert client.post("/api/v1/employees", headers=ADMIN, json={
        "employee_no": "E9", "name": "Dup", "email": "E1@EXAMPLE.com"}).status_code == 409
    for bad in ({"employee_no": "bad no!", "name": "x", "email": "x@example.com"},
                {"employee_no": "E2", "name": "x", "email": "not-an-email"},
                {"employee_no": "E2", "name": "x", "email": "x@example.com", "manager_no": "NOPE"},
                {"employee_no": "E2", "name": "x", "email": "x@example.com", "country": "USA"},
                {"employee_no": "E2", "name": "x", "email": "x@example.com", "surprise": 1}):
        assert client.post("/api/v1/employees", headers=ADMIN, json=bad).status_code == 422, bad
    r = client.patch("/api/v1/employees/E1", headers=ADMIN, json={"title": "Senior Engineer", "status": "inactive"})
    assert r.json()["title"] == "Senior Engineer" and r.json()["status"] == "inactive"
    assert client.patch("/api/v1/employees/E1", headers=ADMIN, json={"manager_no": "E1"}).status_code == 422
    assert client.get("/api/v1/employees/ZZ", headers=ADMIN).status_code == 404
    listing = client.get("/api/v1/employees", headers=ADMIN, params={"q": "bea"}).json()
    assert listing["total"] == 1 and listing["items"][0]["employee_no"] == "B1"
    assert client.get("/api/v1/employees", headers=ADMIN, params={"q": "%"}).json()["total"] == 0  # LIKE is escaped
    assert vacation(client).status_code == 422  # inactive employees cannot submit


# ---- scopes ------------------------------------------------------------------------------------------
def test_token_scopes_are_strict(client, model):
    t = tokens(client)
    employee(client)
    # a client application can do its own job...
    assert client.get("/api/v1/request-types", headers=t["client"]).status_code == 200
    r = vacation(client, t["client"])
    assert r.status_code == 201
    ref = r.json()["ref"]
    assert client.get(f"/api/v1/requests/{ref}", headers=t["client"]).status_code == 200
    # ...but nothing else
    for method, path in [("get", "/api/v1/reports"), ("get", "/api/v1/requests/counts"), ("get", "/api/documents"),
                         ("get", "/api/search?q=vacation"), ("get", "/api/tokens"), ("get", "/metrics")]:
        assert getattr(client, method)(path, headers=t["client"]).status_code == 403, path
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=t["client"],
                       json={"action": "approve"}).status_code == 403
    assert client.post(f"/api/v1/requests/{ref}/validate", headers=t["client"]).status_code == 403
    assert client.put("/api/v1/validation/settings", headers=t["client"], json={"account": None}).status_code == 403
    assert client.post("/api/v1/demo", headers=t["client"]).status_code == 403
    # other scopes are locked out of the compliance data
    for scope in ("read", "metrics"):
        for path in ("/api/v1/requests", "/api/v1/employees", "/api/v1/request-types"):
            assert client.get(path, headers=t[scope]).status_code == 403, (scope, path)
    # a client token must never reach the knowledge base over MCP
    assert client.post("/mcp", headers={**t["client"], "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 403
    assert client.get("/api/v1/requests").status_code == 401  # anonymous


def test_client_never_sees_ai_advice(client, model, seeded):
    t = tokens(client)
    employee(client)
    created = vacation(client, t["client"]).json()
    assert "ai" not in created and "override" not in created
    ref = created["ref"]
    mine = client.get(f"/api/v1/requests/{ref}", headers=t["client"]).json()
    assert "validation" not in mine and "ai" not in mine
    assert {e["action"] for e in mine["events"]} <= {"submitted", "resubmitted", "withdrawn", "approved", "rejected",
                                                    "info_requested"}
    assert all("data" not in e for e in mine["events"])
    listed = client.get("/api/v1/requests", headers=t["client"], params={"ai": "reject"}).json()
    assert listed["total"] == 1 and "ai" not in listed["items"][0]  # the ai filter is ignored for clients
    admin_view = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert admin_view["validation"]["recommendation"] == "approve"


# ---- submitting and validating ------------------------------------------------------------------------
def test_submit_validates_in_background_and_cites_documents(client, model, seeded):
    employee(client)
    r = vacation(client)
    assert r.status_code == 201 and r.json()["status"] == "submitted" and r.json()["ref"].startswith("REQ-")
    got = client.get(f"/api/v1/requests/{r.json()['ref']}", headers=ADMIN).json()
    assert got["status"] == "pending_review" and got["validation_state"] == "done"
    v = got["validation"]
    assert v["state"] == "ok" and v["recommendation"] == "approve" and v["model"] == "fake-1"
    assert v["citations"] and v["citations"][0]["verified"] is True and v["citations"][0]["title"]
    assert v["passages"] and v["passages"][0]["text"]  # evidence snapshot is stored
    assert got["working_days"] == 5
    actions = [e["action"] for e in got["events"]]
    assert actions == ["submitted", "validated"]
    assert got["ai"]["recommendation"] == "approve"


def test_idempotency_key_prevents_duplicates(client, model, seeded):
    employee(client)
    h = {**ADMIN, "Idempotency-Key": "abc-123"}
    first = vacation(client, h)
    second = vacation(client, h)
    assert first.status_code == 201 and second.status_code == 200 and first.json()["id"] == second.json()["id"]
    assert model.calls == 1  # the retry did not trigger a second validation
    assert client.get("/api/v1/requests", headers=ADMIN).json()["total"] == 1


@pytest.mark.parametrize("change", [
    {"type": "nope"}, {"employee_no": "GHOST"}, {"end_date": _d(10)}, {"description": "x"},
    {"type": "expense_other", "amount": 5}, {"extra_field": 1},
])
def test_bad_submissions_are_rejected(client, change):
    employee(client)
    r = vacation(client, **change)
    assert r.status_code in (404, 422), (change, r.text)
    assert client.get("/api/v1/requests", headers=ADMIN).json()["total"] == 0


def test_every_catalogue_type_can_be_submitted(client, model, seeded):
    employee(client)
    types = client.get("/api/v1/request-types", headers=ADMIN).json()["types"]
    for t in types:
        body = {"employee_no": "E1", "type": t["code"], "title": t["label"], "description": f"Test {t['code']}"}
        if t["dates"] in {"range"}:
            body.update(start_date=_d(50), end_date=_d(52))
        elif t["dates"] == "single":
            body["start_date"] = _d(-2)
        if t["amount_required"]:
            body.update(amount=25.5, currency="EUR")
        body["details"] = {f["name"]: (f["choices"][0] if f["kind"] == "choice" else True if f["kind"] == "bool"
                                       else 1 if f["kind"] == "number" else "text")
                           for f in t["details"] if f["required"]}
        r = client.post("/api/v1/requests", headers=ADMIN, json=body)
        assert r.status_code == 201, (t["code"], r.text)
    assert client.get("/api/v1/requests", headers=ADMIN).json()["total"] == len(types)


def test_validation_failures_are_recorded_and_never_block_a_person(client, monkeypatch, seeded):
    employee(client)
    cfg = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: cfg)

    def provider_down(*a, **k):
        raise LLMError("chat endpoint returned HTTP 503: overloaded")

    monkeypatch.setattr(asker, "chat", provider_down)
    ref = vacation(client).json()["ref"]
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert got["status"] == "pending_review" and got["validation_state"] == "failed"
    assert "503" in got["validation"]["error"] and got["validation"]["recommendation"] == "manual"

    def locked(*a, **k):
        raise vault.VaultLocked("locked")

    monkeypatch.setattr(asker, "chat", locked)
    assert client.post(f"/api/v1/requests/{ref}/validate", headers=ADMIN).status_code == 202
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert "locked" in got["validation"]["error"].lower() and got["validation_state"] == "failed"

    monkeypatch.setattr(asker, "chat", FakeModel("reject"))  # recovery: run it again
    client.post(f"/api/v1/requests/{ref}/validate", headers=ADMIN)
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert got["validation_state"] == "done" and got["validation"]["recommendation"] == "reject"
    # a human can decide even while validation had failed earlier
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=ADMIN,
                       json={"action": "reject", "note": "Not this month."}).status_code == 200


def test_no_chat_model_means_skipped_not_stuck(client, seeded):
    employee(client)
    ref = vacation(client).json()["ref"]  # nothing configured in this test
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert got["status"] == "pending_review" and got["validation_state"] == "skipped"
    assert "No chat model" in got["validation"]["error"]
    assert got["validation"]["passages"] == [] and got["ai"]["recommendation"] == "manual"


def test_model_returning_garbage_is_retried_once_then_recorded(client, monkeypatch, seeded):
    employee(client)
    cfg = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: cfg)
    calls = []

    def chatty(cfg, messages, max_tokens=700):
        calls.append(1)
        return {"text": "Sure! I would approve this one.", "usage": None}

    monkeypatch.setattr(asker, "chat", chatty)
    got = client.get(f"/api/v1/requests/{vacation(client).json()['ref']}", headers=ADMIN).json()
    assert len(calls) == 2 and got["validation_state"] == "failed" and "JSON" in got["validation"]["error"]


def test_automatic_checks_downgrade_an_approval(client, model, seeded):
    employee(client)
    first = vacation(client).json()["ref"]
    client.post(f"/api/v1/requests/{first}/decision", headers=ADMIN, json={"action": "approve"})
    second = vacation(client, start_date=(_monday(60) + timedelta(days=2)).isoformat(),
                      end_date=(_monday(60) + timedelta(days=6)).isoformat()).json()["ref"]  # overlaps the approved one
    v = client.get(f"/api/v1/requests/{second}", headers=ADMIN).json()["validation"]
    assert v["recommendation"] == "needs_info"  # the fake model said approve; the check overrules it
    assert any(c["code"] == "overlap" and c["severity"] == "fail" for c in v["checks"])
    assert any("automatic check failed" in w for w in v["warnings"])


def test_facts_sent_to_the_model_exclude_personal_data(client, monkeypatch, seeded):
    employee(client, name="Zelda Privatename", manager_no=None)
    seen = []
    cfg = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: cfg)
    fake = FakeModel("needs_info")

    def spy(cfg, messages, max_tokens=700):
        seen.append(json.dumps(messages))
        return fake(cfg, messages, max_tokens)

    monkeypatch.setattr(asker, "chat", spy)
    vacation(client)
    blob = seen[0]
    assert "Zelda" not in blob and "Privatename" not in blob and "e1@example.com" not in blob and '"E1"' not in blob
    assert "Engineering" in blob and "working_days_mon_fri" in blob


# ---- decisions ----------------------------------------------------------------------------------------
def test_decision_rules(client, model, seeded):
    employee(client)
    ref = vacation(client).json()["ref"]
    d = lambda action, note=None: client.post(f"/api/v1/requests/{ref}/decision", headers=ADMIN,  # noqa: E731
                                              json={"action": action, "note": note})
    assert d("reject").status_code == 422  # a reason is mandatory
    assert d("request_info").status_code == 422
    assert d("shrug").status_code == 422
    asked = d("request_info", "Which days exactly?")
    assert asked.status_code == 200 and asked.json()["status"] == "needs_info"
    assert d("request_info", "again?").status_code == 409
    done = d("approve")
    assert done.status_code == 200 and done.json()["status"] == "approved" and done.json()["decided_by"] == "api key"
    assert done.json()["override"] is False
    assert d("reject", "too late now").status_code == 409  # already decided
    assert client.post(f"/api/v1/requests/{ref}/withdraw", headers=ADMIN).status_code == 409
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert [e["action"] for e in got["events"]] == ["submitted", "validated", "info_requested", "approved"]


def test_overriding_the_ai_requires_a_justification_and_is_flagged(client, monkeypatch, model, seeded):
    employee(client)
    model.recommendation, model.compliance = "reject", "non_compliant"
    ref = vacation(client).json()["ref"]
    url = f"/api/v1/requests/{ref}/decision"
    assert client.post(url, headers=ADMIN, json={"action": "approve"}).status_code == 422
    assert client.post(url, headers=ADMIN, json={"action": "approve", "note": "ok"}).status_code == 422  # too short
    ok = client.post(url, headers=ADMIN, json={"action": "approve",
                                               "note": "VP agreed an exception for this launch week."})
    assert ok.status_code == 200 and ok.json()["override"] is True
    rows = client.get("/api/v1/reports/overrides", headers=ADMIN).json()["rows"]
    assert len(rows) == 1 and rows[0]["ai"] == "reject" and rows[0]["decision"] == "approved"
    assert "VP agreed" in rows[0]["justification"]
    ooc = client.get("/api/v1/reports/out_of_compliance", headers=ADMIN).json()["rows"]
    assert ooc[0]["flag"] == "Approved although flagged"


def test_edit_after_info_request_revalidates_and_withdraw(client, model, seeded):
    t = tokens(client)
    employee(client)
    ref = vacation(client, t["client"]).json()["ref"]
    assert client.put(f"/api/v1/requests/{ref}", headers=t["client"], json={"title": "x"}).status_code == 409
    client.post(f"/api/v1/requests/{ref}/decision", headers=ADMIN, json={"action": "request_info", "note": "Which dates?"})
    calls_before = model.calls
    r = client.put(f"/api/v1/requests/{ref}", headers=t["client"],
                   json={"start_date": _d(70), "end_date": _d(72), "note": "Moved by a week."})
    assert r.status_code == 200 and "ai" not in r.json()
    got = client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()
    assert got["start_date"] == _d(70) and got["status"] == "pending_review" and model.calls == calls_before + 1
    assert "resubmitted" in [e["action"] for e in got["events"]]
    w = client.post(f"/api/v1/requests/{ref}/withdraw", headers=t["client"], json={"note": "Plans changed"})
    assert w.status_code == 200 and w.json()["status"] == "withdrawn"
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=ADMIN, json={"action": "approve"}).status_code == 409


def test_request_lookup_and_listing_filters(client, model, seeded):
    employee(client)
    employee(client, "E2", "Bo Example", department="Sales")
    a = vacation(client).json()
    b = vacation(client, emp="E2", type="sick_leave", start_date=_d(-3), end_date=_d(-2)).json()
    for ref in (a["ref"], str(a["id"]), a["ref"].lower()):
        assert client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()["id"] == a["id"]
    assert client.get("/api/v1/requests/REQ-999999", headers=ADMIN).status_code == 404
    assert client.get("/api/v1/requests/banana", headers=ADMIN).status_code == 404
    ls = lambda **p: client.get("/api/v1/requests", headers=ADMIN, params=p).json()  # noqa: E731
    assert ls()["total"] == 2
    assert [i["id"] for i in ls(type="sick_leave")["items"]] == [b["id"]]
    assert [i["id"] for i in ls(employee_no="E1")["items"]] == [a["id"]]
    assert ls(status="open")["total"] == 2 and ls(status="approved")["total"] == 0
    assert ls(ai="approve")["total"] == 2 and ls(kind="leave")["total"] == 2 and ls(q="bo")["total"] == 1
    assert client.get("/api/v1/requests", headers=ADMIN, params={"limit": 0}).status_code == 422
    c = client.get("/api/v1/requests/counts", headers=ADMIN).json()
    assert c["open"] == 2 and c["by_status"]["pending_review"] == 2


def test_oversized_body_is_refused(client):
    employee(client)
    r = client.post("/api/v1/requests", headers={**ADMIN, "Content-Type": "application/json"},
                    content=json.dumps({"employee_no": "E1", "type": "other", "title": "t", "description": "x" * 70000}))
    assert r.status_code == 413


# ---- reports ------------------------------------------------------------------------------------------
def test_demo_data_and_all_reports(client, model, seeded):
    first = client.post("/api/v1/demo", headers=ADMIN)
    assert first.status_code == 201 and first.json()["employees"] == 11 and first.json()["requests"] == 22
    assert client.post("/api/v1/demo", headers=ADMIN).json()["requests"] == 0  # idempotent
    counts = client.get("/api/v1/requests/counts", headers=ADMIN).json()
    assert counts["by_status"]["approved"] == 6 and counts["by_status"]["rejected"] == 1
    assert counts["by_status"]["pending_review"] == 15  # every new request was validated by the fake model
    names = [r["name"] for r in client.get("/api/v1/reports", headers=ADMIN).json()["reports"]]
    assert set(names) == set(__import__("trustdesk.reports", fromlist=["REPORTS"]).REPORTS)
    for name in names:
        rep = client.get(f"/api/v1/reports/{name}", headers=ADMIN)
        assert rep.status_code == 200, name
        body = rep.json()
        assert body["columns"] and isinstance(body["rows"], list)
        csv = client.get(f"/api/v1/reports/{name}", headers=ADMIN, params={"format": "csv"})
        assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
        assert "attachment" in csv.headers["content-disposition"]
    expenses = client.get("/api/v1/reports/expenses", headers=ADMIN).json()["rows"]
    assert any(r["department"] == "Sales" and r["approved"] == 240 for r in expenses)
    leave = client.get("/api/v1/reports/leave", headers=ADMIN, params={"from": _d(-400), "to": _d(400)}).json()
    assert {r["employee_no"] for r in leave["rows"]} >= {"E1002", "E1003", "E1001"}
    assert client.get("/api/v1/reports/overview", headers=ADMIN).json()["rows"][0]["value"] == 22
    assert client.get("/api/v1/reports/nope", headers=ADMIN).status_code == 404
    assert client.get("/api/v1/reports/overview", headers=ADMIN, params={"from": "yesterday"}).status_code == 422
    aging = client.get("/api/v1/reports/aging", headers=ADMIN).json()
    assert aging["rows"] and aging["rows"][0]["age_days"] >= 0


def test_csv_report_neutralises_formula_names(client, model, seeded):
    employee(client, "E7", '=HYPERLINK("http://evil.example","x")')
    vacation(client, emp="E7")
    csv = client.get("/api/v1/reports/requests", headers=ADMIN, params={"format": "csv"}).text
    assert "'=HYPERLINK" in csv and "\n=HYPERLINK" not in csv and ',=HYPERLINK' not in csv


def test_validation_settings(client):
    s = client.get("/api/v1/validation/settings", headers=ADMIN).json()
    assert {k: s[k] for k in ("account", "effective", "source", "accounts")} == {
        "account": None, "effective": None, "source": None, "accounts": []}
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": "999"}).status_code == 422
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": "env"}).status_code == 422
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": "x"}).status_code == 422
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": None}).status_code == 200


# ---- the environment account is the default; requests that waited for an account are picked up ----------
def _env_account(monkeypatch):
    """The fleet's LLM_* environment account. Only the account listing is faked: the embedder keeps its offline default."""
    monkeypatch.setattr(accounts, "env_account", lambda: {
        "id": "env", "name": "Environment (server settings)", "provider": "env", "base_url": "https://llm.invalid/v1",
        "has_key": True, "api_key_last4": None, "chat_model": "chat-default", "embedding_model": None,
        "embedding_dim": None, "read_only": True})


def test_environment_account_is_the_default_and_an_explicit_choice_wins(client, monkeypatch):
    with connect() as c:
        assert validator.choose_account(c) is None                      # nothing configured
        _env_account(monkeypatch)
        assert validator.choose_account(c) == "env"                     # default: nothing saved
        other = c.execute("INSERT INTO llm_account (name, provider, base_url, chat_model) "
                          "VALUES ('mine','custom','https://other.invalid/v1','m') RETURNING id").fetchone()["id"]
        validator.set_account(c, str(other))
        assert validator.choose_account(c) == other                     # a saved choice beats the default
        validator.set_account(c, "none")
        assert validator.choose_account(c) is None                      # 'none' is a choice too, and is respected
        validator.set_account(c, None)
        assert validator.choose_account(c) == "env"                     # forgetting it restores the default


def test_validation_settings_api_reports_the_true_saved_state(client, monkeypatch):
    _env_account(monkeypatch)
    got = client.get("/api/v1/validation/settings", headers=ADMIN).json()
    assert got["account"] is None and got["effective"] == "env" and got["source"] == "default"
    assert got["accounts"][0]["id"] == "env" and got["backlog"]["per_minute"] == 10 and got["backlog"]["waiting"] == 0
    put = client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": "none", "backlog_per_minute": 4})
    assert put.status_code == 200
    got = put.json()
    assert got["account"] == "none" and got["effective"] is None and got["source"] == "saved"
    assert got["backlog"]["per_minute"] == 4 and got["backlog"]["saved"] is True
    # leaving a field out leaves it alone; null forgets it
    got = client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": None}).json()
    assert got["account"] is None and got["effective"] == "env" and got["backlog"]["per_minute"] == 4
    got = client.put("/api/v1/validation/settings", headers=ADMIN, json={"backlog_per_minute": None}).json()
    assert got["backlog"]["per_minute"] == 10 and got["backlog"]["saved"] is False
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"backlog_per_minute": 61}).status_code == 422
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": "nope"}).status_code == 422
    monkeypatch.setenv("VALIDATE_BACKLOG_PER_MINUTE", "3")
    assert client.get("/api/v1/validation/settings", headers=ADMIN).json()["backlog"]["per_minute"] == 3


def test_backlog_validates_skipped_requests_oldest_first_within_the_rate(client, monkeypatch, seeded):
    employee(client)
    refs = [vacation(client, start_date=_d(60 + 7 * i), end_date=_d(61 + 7 * i)).json()["ref"] for i in range(3)]
    for ref in refs:
        assert client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()["validation_state"] == "skipped"
    assert client.get("/api/v1/validation/settings", headers=ADMIN).json()["backlog"]["waiting"] == 3
    monkeypatch.setattr(validator, "BACKLOG_MIN_AGE", 0)
    assert validator.backlog_pass() == 0                                # still no account: nothing is recorded again
    assert [e["action"] for e in client.get(f"/api/v1/requests/{refs[0]}", headers=ADMIN).json()["events"]] == [
        "submitted", "validation_skipped"]

    fake = FakeModel()
    cfg = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")
    _env_account(monkeypatch)
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: cfg)
    monkeypatch.setattr(asker, "chat", fake)
    client.put("/api/v1/validation/settings", headers=ADMIN, json={"backlog_per_minute": 0})
    assert validator.backlog_pass() == 0 and fake.calls == 0            # 0 switches the pass off
    client.put("/api/v1/validation/settings", headers=ADMIN, json={"backlog_per_minute": 2})
    assert validator.backlog_pass() == 2 and fake.calls == 2            # capped by the rate, oldest first
    states = [client.get(f"/api/v1/requests/{r}", headers=ADMIN).json() for r in refs]
    assert [s["validation_state"] for s in states] == ["done", "done", "skipped"]
    assert [e["action"] for e in states[0]["events"]] == ["submitted", "validation_skipped", "validated"]
    assert client.get("/api/v1/validation/settings", headers=ADMIN).json()["backlog"]["waiting"] == 1
    assert validator.backlog_pass() == 1 and validator.backlog_pass() == 0
    assert client.get("/api/v1/validation/settings", headers=ADMIN).json()["backlog"]["waiting"] == 0


def test_backlog_leaves_a_request_that_was_just_submitted_alone(client, monkeypatch, seeded):
    employee(client)
    ref = vacation(client).json()["ref"]                                # skipped: no account yet
    _env_account(monkeypatch)
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, r: accounts.ChatConfig(
        base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake"))
    monkeypatch.setattr(asker, "chat", FakeModel())
    assert validator.backlog_pass() == 0                                # younger than BACKLOG_MIN_AGE
    assert client.post("/api/v1/requests/validate-pending", headers=ADMIN).json() == {"queued": 1}   # the button has no such delay
    assert client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()["validation_state"] == "done"
