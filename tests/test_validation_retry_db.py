"""Bounded automatic retry of failed AI validations: backoff, the attempt cap, permanent errors, the manual button, Settings."""
import os
import shutil
from datetime import date, timedelta

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from apgkit import migrate as kit_migrate  # noqa: E402

from trustdesk import accounts, core, migrate, validator, workflow  # noqa: E402
from trustdesk import ask as asker  # noqa: E402
from trustdesk.config import get_settings  # noqa: E402
from trustdesk.db import connect  # noqa: E402
from trustdesk.llm_http import LLMError  # noqa: E402

ADMIN = {"Authorization": "Bearer k"}
CFG = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    monkeypatch.delenv("VALIDATE_MAX_ATTEMPTS", raising=False)
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    monkeypatch.setattr(validator, "BACKLOG_MIN_AGE", 0)


class Provider:
    """The chat provider: down (503), revoked key (401), refusing the request (400), garbage, or fine."""

    def __init__(self):
        self.mode, self.calls = "down", 0

    def __call__(self, cfg, messages, max_tokens=700):
        self.calls += 1
        if self.mode in {"down", "revoked", "refused"}:
            status = {"down": 503, "revoked": 401, "refused": 400}[self.mode]
            err = LLMError(f"chat endpoint returned HTTP {status}: nope")
            err.status = status
            raise err
        if self.mode == "garbage":
            return {"text": "Sure, looks fine to me.", "usage": None}
        return {"text": '{"recommendation": "needs_info", "compliance": "unclear", "summary": "ok", "reasons": [], '
                        '"citations": [], "missing": ["more detail"]}', "usage": None}


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: CFG)
    monkeypatch.setattr(asker, "chat", p)
    return p


def make_request(client, no="E1"):
    d = date.today() + timedelta(days=60)
    d += timedelta(days=(7 - d.weekday()) % 7)
    if no == "E1":
        r = client.post("/api/v1/employees", headers=ADMIN, json={"employee_no": "E1", "name": "Ann", "email": "e1@example.com"})
        assert r.status_code == 201, r.text
    r = client.post("/api/v1/requests", headers=ADMIN, json={
        "employee_no": "E1", "type": "vacation", "title": "Holiday", "description": "Time at the beach",
        "start_date": d.isoformat(), "end_date": (d + timedelta(days=4)).isoformat()})
    assert r.status_code in (200, 201), r.text
    return r.json()["ref"]


def row(ref):
    with connect() as c:
        return c.execute("SELECT validation_state AS state, validation_attempts AS attempts, validation_retryable AS retryable, "
                         "status FROM request WHERE id=%s", (workflow.parse_ref(ref),)).fetchone()


def age(ref, minutes):
    """Pretend the last failure happened `minutes` ago."""
    with connect() as c:
        c.execute("UPDATE request SET validation_failed_at = now() - %s * interval '1 minute' WHERE id=%s",
                  (minutes, workflow.parse_ref(ref)))
        c.commit()


def events(client, ref):
    return client.get(f"/api/v1/requests/{ref}", headers=ADMIN).json()["events"]


def settings(client):
    return client.get("/api/v1/validation/settings", headers=ADMIN).json()["backlog"]


def test_each_failure_is_counted_and_recorded_in_the_history(client, provider):
    ref = make_request(client)
    assert row(ref) == {"state": "failed", "attempts": 1, "retryable": True, "status": "pending_review"}
    failed = [e for e in events(client, ref) if e["action"] == "validation_failed"]
    assert len(failed) == 1 and failed[0]["data"]["attempt"] == 1 and failed[0]["data"]["will_retry"] is True
    assert "503" in failed[0]["note"]


def test_backlog_retries_a_failed_validation_with_backoff_and_stops_at_the_cap(client, provider):
    ref = make_request(client)
    assert provider.calls == 1
    assert validator.backlog_pass() == 0 and provider.calls == 1       # 5 minutes have not passed
    age(ref, 4)
    assert validator.backlog_pass() == 0 and provider.calls == 1
    age(ref, 6)
    assert validator.backlog_pass() == 1 and provider.calls == 2       # second attempt, fails again
    assert row(ref)["attempts"] == 2
    age(ref, 14)
    assert validator.backlog_pass() == 0 and provider.calls == 2       # now it waits 15 minutes
    age(ref, 16)
    assert validator.backlog_pass() == 1 and provider.calls == 3       # third and last attempt
    last = [e for e in events(client, ref) if e["action"] == "validation_failed"][-1]
    assert last["data"] == {"recommendation": "manual", "attempt": 3, "will_retry": False}
    age(ref, 100000)
    assert validator.backlog_pass() == 0 and provider.calls == 3       # gave up: capped
    assert row(ref)["state"] == "failed" and row(ref)["attempts"] == 3
    assert settings(client)["retrying"] == 0 and settings(client)["gave_up"] == 1


def test_retry_succeeds_after_the_cause_is_fixed_and_resets_the_count(client, provider):
    provider.mode = "revoked"
    refs = [make_request(client), make_request(client, "x")]
    assert [row(r)["state"] for r in refs] == ["failed", "failed"]
    assert settings(client)["retrying"] == 2
    provider.mode = "ok"                                               # the key was replaced
    for r in refs:
        age(r, 6)
    assert validator.backlog_pass() == 2
    assert [row(r) for r in refs] == [{"state": "done", "attempts": 0, "retryable": True, "status": "pending_review"}] * 2
    assert settings(client)["retrying"] == 0 and settings(client)["gave_up"] == 0
    assert [e["action"] for e in events(client, refs[0])][-2:] == ["validation_failed", "validated"]


def test_the_pass_respects_the_rate_and_oldest_first(client, provider):
    refs = [make_request(client), make_request(client, "x"), make_request(client, "y")]
    provider.mode = "ok"
    for r in refs:
        age(r, 6)
    client.put("/api/v1/validation/settings", headers=ADMIN, json={"backlog_per_minute": 2})
    assert validator.backlog_pass() == 2
    assert [row(r)["state"] for r in refs] == ["done", "done", "failed"]


def test_zero_attempts_never_retries(client, provider):
    got = client.put("/api/v1/validation/settings", headers=ADMIN, json={"max_attempts": 0}).json()["backlog"]
    assert got["max_attempts"] == 0 and got["max_attempts_saved"] is True
    ref = make_request(client)
    assert [e for e in events(client, ref) if e["action"] == "validation_failed"][0]["data"]["will_retry"] is False
    age(ref, 100000)
    assert validator.backlog_pass() == 0 and provider.calls == 1
    assert settings(client)["retrying"] == 0 and settings(client)["gave_up"] == 1


def test_a_permanent_client_error_is_not_retried_but_a_revoked_key_and_garbage_are(client, provider):
    provider.mode = "refused"                                          # HTTP 400: the provider rejects this very request
    ref = make_request(client)
    assert row(ref)["retryable"] is False
    assert [e for e in events(client, ref) if e["action"] == "validation_failed"][0]["data"]["will_retry"] is False
    age(ref, 100000)
    assert validator.backlog_pass() == 0 and provider.calls == 1
    assert settings(client)["gave_up"] == 1

    provider.mode, provider.calls = "garbage", 0                       # malformed output: retried, within the cap
    ref2 = make_request(client, "x")
    assert row(ref2)["retryable"] is True and provider.calls == 2      # (two calls: the validator's own single re-ask)
    age(ref2, 6)
    assert validator.backlog_pass() == 1 and row(ref2)["attempts"] == 2


def test_requests_that_left_the_queue_are_not_retried(client, provider):
    ref = make_request(client)
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=ADMIN, json={"action": "approve"}).status_code == 200
    age(ref, 100000)
    assert validator.backlog_pass() == 0 and provider.calls == 1
    assert settings(client)["retrying"] == 0 and settings(client)["gave_up"] == 0


def test_the_button_includes_failed_requests_and_starts_their_count_over(client, provider):
    ref = make_request(client)
    for _ in range(2):
        age(ref, 100000)
        validator.backlog_pass()
    assert row(ref)["attempts"] == 3 and settings(client)["gave_up"] == 1
    provider.mode = "ok"
    assert client.post("/api/v1/requests/validate-pending", headers=ADMIN).json() == {"queued": 1}
    assert row(ref) == {"state": "done", "attempts": 0, "retryable": True, "status": "pending_review"}
    # a single "Run again" also starts the count over
    provider.mode = "down"
    ref2 = make_request(client, "x")
    assert row(ref2)["attempts"] == 1
    client.post(f"/api/v1/requests/{ref2}/validate", headers=ADMIN)
    assert row(ref2)["attempts"] == 1                                  # 0 after the reset, 1 after this new failure
    assert client.post("/api/v1/requests/validate-pending", headers=ADMIN).json() == {"queued": 1}
    assert row(ref2)["attempts"] == 1


def test_resubmitting_after_a_question_starts_the_count_over(client, provider):
    ref = make_request(client)
    with connect() as c:
        c.execute("UPDATE request SET status='needs_info', validation_attempts=3 WHERE id=%s", (workflow.parse_ref(ref),))
        c.commit()
        workflow.update(c, workflow.parse_ref(ref), {"description": "More detail"}, actor="ann")
        assert c.execute("SELECT validation_attempts AS n FROM request WHERE id=%s", (workflow.parse_ref(ref),)).fetchone()["n"] == 0


def test_max_attempts_setting(client, monkeypatch):
    assert settings(client)["max_attempts"] == 3 and settings(client)["max_attempts_saved"] is False
    assert settings(client)["max_attempts_default"] == 3 and settings(client)["backoff_minutes"] == [5, 15, 60]
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"max_attempts": 5}).json()["backlog"]["max_attempts"] == 5
    got = client.put("/api/v1/validation/settings", headers=ADMIN, json={"account": None}).json()["backlog"]   # left alone
    assert got["max_attempts"] == 5
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"max_attempts": 11}).status_code == 422
    assert client.put("/api/v1/validation/settings", headers=ADMIN, json={"max_attempts": -1}).status_code == 422
    got = client.put("/api/v1/validation/settings", headers=ADMIN, json={"max_attempts": None}).json()["backlog"]
    assert got["max_attempts"] == 3 and got["max_attempts_saved"] is False
    monkeypatch.setenv("VALIDATE_MAX_ATTEMPTS", "2")
    assert settings(client)["max_attempts"] == 2 and get_settings().validate_max_attempts == 2
    monkeypatch.setenv("VALIDATE_MAX_ATTEMPTS", "lots")
    assert get_settings().validate_max_attempts == 3


def test_upgrade_counts_requests_that_were_already_failed_as_one_attempt(client):
    """A database at migration 004 with a request stuck in 'failed' (the live bug) is picked up by 005 and gets retried."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        for f in sorted(migrate.MIGRATIONS.glob("00[1-4]_*.sql")):
            shutil.copy(f, Path(tmp) / f.name)
        with connect() as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            c.commit()
        kit_migrate.run(core.db, Path(tmp), extensions=("vector",),
                        replacements={"{{EMBEDDING_DIM}}": str(get_settings().embedding_dim)})
    with connect() as c:
        assert not c.execute("SELECT 1 FROM information_schema.columns WHERE table_name='request' "
                             "AND column_name='validation_attempts'").fetchone()
        emp = c.execute("INSERT INTO employee (employee_no, name, email) VALUES ('E1','Ann','e1@example.com') RETURNING id").fetchone()["id"]
        ids = [c.execute("INSERT INTO request (employee_id, type, kind, title, description, status, validation_state) "
                         "VALUES (%s,'vacation','leave','t','d',%s,%s) RETURNING id", (emp, st, vs)).fetchone()["id"]
               for st, vs in (("pending_review", "failed"), ("pending_review", "done"))]
        c.execute("INSERT INTO validation (request_id, state, error, created_at) VALUES (%s,'failed','HTTP 402', "
                  "now() - interval '3 days')", (ids[0],))
        c.commit()
    assert migrate.run() == ["005_validation_retry.sql", "006_unaccent_tcn.sql", "007_trigram_search.sql", "008_client_scope.sql"]   # 006 and 007 only add optional extensions and indexes
    with connect() as c:
        failed, done = (c.execute("SELECT validation_attempts AS a, validation_retryable AS r, "
                                  "(validation_failed_at < now() - interval '2 days') AS old FROM request WHERE id=%s",
                                  (i,)).fetchone() for i in ids)
        assert failed == {"a": 1, "r": True, "old": True} and done["a"] == 0 and done["r"] is True
        assert workflow.pending_validation_ids(c, failed="due", max_attempts=3) == [ids[0]]     # due at once
        assert workflow.pending_validation_ids(c, failed="due", max_attempts=0) == []
        assert workflow.pending_validation_ids(c) == []
        assert workflow.failed_validation_counts(c, 3) == {"retrying": 1, "gave_up": 0}
    assert migrate.run() == []
