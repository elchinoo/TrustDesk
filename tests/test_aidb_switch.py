"""AIDB mode, the part that needs no AIDB: the backend switch, every fallback, the plain-Postgres objects AIDB mode reads, the CLI and
/readyz on a database without the extension. Runs in CI (plain pgvector Postgres). The tests that need AIDB itself are in
test_aidb_integration.py (marker `aidb`)."""
import json
import logging
import os
import socket
from datetime import date, timedelta

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import (  # noqa: E402
    accounts,
    aidbmode,
    auth,
    cli,
    core,
    ingest,
    migrate,
    netguard,
    people,
    search,
    validator,
    vault,
    workflow,
)
from trustdesk import ask as asker  # noqa: E402
from trustdesk.db import connect  # noqa: E402

ADMIN = {"Authorization": "Bearer test-static-key-0123456789-abcdefghij-xyz"}
PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
CFG = accounts.ChatConfig(base_url="https://llm.invalid/v1", model="fake-1", trusted=True, label="Fake")


@pytest.fixture(autouse=True)
def _db(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "test-static-key-0123456789-abcdefghij-xyz")
    for v in ("TRUSTDESK_RETRIEVAL_BACKEND", "TRUSTDESK_ENCRYPTION_KEY", "TRUSTDESK_AIDB_ADMIN_DSN", "LLM_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_ENCRYPTION_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("LLM_API_KEY_FILE", "/nonexistent/key")
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    aidbmode.reset()
    vault.lock()
    yield
    aidbmode.reset()


def unlock():
    with connect() as c:
        auth.set_admin_password(c, PASSWORD)
    assert vault.is_unlocked()


POLICY = ("# Travel Policy\n\n## Hotels\nThe hotel limit is 200 USD per night. Higher rates need Director approval before booking.\n\n"
          "## Meals\nMeals on trips are reimbursed up to 60 USD per day.\n")


def seed_docs(**kw):
    with connect() as c:
        return ingest.ingest_text(title="Travel Policy", source="travel-policy.md", text=POLICY, doc_type="travel", conn=c, **kw)


def make_request(kind="travel"):
    d = date.today() + timedelta(days=60)
    d += timedelta(days=(7 - d.weekday()) % 7)
    with connect() as c:
        people.create(c, {"employee_no": "E1", "name": "Ann Example", "email": "ann@example.com", "department": "finance",
                          "level": "L3", "country": "DE"})
        req, _ = workflow.create(c, {"employee_no": "E1", "type": "vacation", "title": "Holiday", "description": "A week at the beach",
                                     "start_date": d.isoformat(), "end_date": (d + timedelta(days=4)).isoformat()}, actor="test")
    return req["id"]


# ---- the switch ----------------------------------------------------------------------------------------------------------
def test_default_backend_is_app_and_touches_nothing_of_aidb(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the app backend must not open an AIDB connection")
    monkeypatch.setattr(aidbmode, "connect", boom)
    monkeypatch.setattr(aidbmode.aidb, "detect", boom)
    seed_docs()
    assert aidbmode.requested() == "app"
    assert aidbmode.state().brief() == {"requested": "app"}
    assert aidbmode.retriever() is None and not aidbmode.agent_enabled()
    assert search.hybrid_search("hotel limit per night", limit=3)[0]["heading"].endswith("Hotels")


def test_unknown_backend_value_is_the_app_backend(monkeypatch, caplog):
    monkeypatch.setenv("TRUSTDESK_RETRIEVAL_BACKEND", "quantum")
    with caplog.at_level(logging.ERROR):
        assert aidbmode.requested() == "app"
    assert "not one of" in caplog.text


def test_aidb_requested_on_plain_postgres_falls_back_loudly_and_everything_works(monkeypatch, caplog, client):
    monkeypatch.setenv("TRUSTDESK_RETRIEVAL_BACKEND", "aidb")
    unlock()
    seed_docs()
    with caplog.at_level(logging.ERROR):
        st = aidbmode.state(fresh=True)
    assert not st.retrieval and not st.agent and st.code == "unavailable"
    assert "RETRIEVAL BACKEND FALLBACK" in caplog.text and "AIDB MODE FALLBACK" in caplog.text
    assert search.hybrid_search("hotel limit per night", limit=3) == search.app_search("hotel limit per night", limit=3)
    # /readyz reports it without failing, in coarse words (the route is unauthenticated)
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready", "aidb": {"requested": "aidb", "retrieval": False, "agent": False,
                                                                           "problem": "unavailable"}}
    # the administrator's view says why, in full, and does not fail either
    g = client.get("/api/aidb/governance", headers=ADMIN)
    assert g.status_code == 200
    assert g.json()["mode"]["code"] == "unavailable" and g.json()["governance"]["available"] is False


def test_readyz_with_the_app_backend_says_so(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready", "aidb": {"requested": "app"}}


def test_a_failing_aidb_search_falls_back_for_that_call(monkeypatch, caplog):
    seed_docs()

    class Broken:
        def retrieve(self, *a, **k):
            raise RuntimeError("knowledge base exploded")
    monkeypatch.setattr(aidbmode, "retriever", lambda: Broken())
    before = aidbmode.FALLBACKS.labels("retrieval", "RuntimeError")._value.get()
    with caplog.at_level(logging.ERROR):
        got = search.hybrid_search("hotel limit per night", limit=3)
    assert got == search.app_search("hotel limit per night", limit=3) and got
    assert "AIDB MODE FALLBACK at run time" in caplog.text
    assert aidbmode.FALLBACKS.labels("retrieval", "RuntimeError")._value.get() == before + 1


def test_a_failing_aidb_search_inside_the_callers_transaction_leaves_its_work_intact(monkeypatch):
    seed_docs()

    class Broken:
        def retrieve(self, conn, *a, **k):
            conn.execute("SELECT 1/0")          # a database error inside the AIDB path
    monkeypatch.setattr(aidbmode, "retriever", lambda: Broken())
    with connect() as c:
        c.execute("INSERT INTO app_setting (key, value) VALUES ('x_probe', '1')")   # uncommitted work of the caller
        assert search.hybrid_search("hotel", conn=c, limit=2)
        assert c.execute("SELECT value FROM app_setting WHERE key='x_probe'").fetchone()["value"] == "1"
        c.rollback()


# ---- the agent and its fallbacks ----------------------------------------------------------------------------------------
class Gateway:
    """The gateway path's chat call: records that it was used."""

    def __init__(self, text=None):
        self.calls = 0
        self.text = text or ('{"recommendation": "needs_info", "compliance": "unclear", "summary": "gateway", "reasons": [], '
                             '"citations": [], "missing_information": ["more"]}')

    def __call__(self, cfg, messages, max_tokens=700):
        self.calls += 1
        return {"text": self.text, "usage": None}


@pytest.fixture
def gateway(monkeypatch):
    g = Gateway()
    monkeypatch.setattr(validator, "choose_account", lambda conn: "env")
    monkeypatch.setattr(accounts, "chat_config_for", lambda conn, ref: CFG)
    monkeypatch.setattr(asker, "chat", g)
    return g


def _validation(rid):
    with connect() as c:
        return c.execute("SELECT * FROM validation WHERE request_id=%s ORDER BY id DESC LIMIT 1", (rid,)).fetchone()


def test_app_backend_validates_through_the_gateway_only(gateway, monkeypatch):
    monkeypatch.setattr(aidbmode, "run_agent", lambda *a: (_ for _ in ()).throw(AssertionError("no agent in app mode")))
    seed_docs()
    rid = make_request()
    assert validator.validate(rid) == "ok"
    assert gateway.calls == 1 and _validation(rid)["summary"] == "gateway" and _validation(rid)["model"] == "fake-1"


@pytest.mark.parametrize("outcome", [(None, "error"), (None, "timeout"), (None, "refused"), ("Sure, looks fine.", "ok"),
                                     ('{"recommendation": "maybe"}', "ok")])
def test_agent_that_errors_times_out_is_refused_or_unusable_falls_back_to_the_gateway(gateway, monkeypatch, caplog, outcome):
    monkeypatch.setattr(aidbmode, "agent_enabled", lambda: True)
    monkeypatch.setattr(aidbmode, "run_agent", lambda rid, msg: outcome)
    seed_docs()
    rid = make_request()
    with caplog.at_level(logging.ERROR):
        assert validator.validate(rid) == "ok"
    assert gateway.calls == 1
    v = _validation(rid)
    assert v["summary"] == "gateway" and v["model"] == "fake-1"
    assert "AIDB MODE FALLBACK" in caplog.text


def test_agent_failure_with_no_gateway_account_is_the_old_skipped_state(monkeypatch):
    monkeypatch.setattr(aidbmode, "agent_enabled", lambda: True)
    monkeypatch.setattr(aidbmode, "run_agent", lambda rid, msg: (None, "error"))
    monkeypatch.setattr(validator, "choose_account", lambda conn: None)
    seed_docs()
    rid = make_request()
    assert validator.validate(rid) == "skipped"


def test_agent_answer_is_checked_like_a_gateway_answer(gateway, monkeypatch):
    seed_docs()
    rid = make_request()
    with connect() as c:
        passages = validator.gather_passages(c, workflow.get(c, rid))
    hotel = next(p for p in passages if "200 USD per night" in p["text"])
    answer = {"recommendation": "reject", "compliance": "non_compliant", "summary": "Too expensive.",
              "reasons": [{"text": "Over the hotel limit", "passages": [hotel["n"]]}],
              "citations": [{"passage": hotel["n"], "quote": "The hotel limit is 200 USD per night"},
                            {"passage": hotel["n"], "quote": "a quote the passage does not contain at all"},
                            {"passage": 99, "quote": "a passage that does not exist"}],
              "missing_information": []}
    seen = {}

    def agent(request_id, message):
        seen["id"], seen["message"] = request_id, message
        return json.dumps(answer), "ok"
    monkeypatch.setattr(aidbmode, "agent_enabled", lambda: True)
    monkeypatch.setattr(aidbmode, "run_agent", agent)
    assert validator.validate(rid) == "ok"
    assert gateway.calls == 0 and seen["id"] == rid and "POLICY PASSAGES" in seen["message"] and "<request>" in seen["message"]
    v = _validation(rid)
    assert v["model"] == f"aidb:{aidbmode.AGENT}" and v["account"] == "AIDB agent" and v["recommendation"] == "reject"
    cites = {c["n"]: c for c in v["citations"]}
    assert cites[hotel["n"]]["verified"] is True and cites[hotel["n"]]["quote"].startswith("The hotel limit")
    assert 99 not in cites                                   # a citation of a passage that does not exist is dropped


def test_agent_answer_citing_nothing_cannot_approve(gateway, monkeypatch):
    monkeypatch.setattr(aidbmode, "agent_enabled", lambda: True)
    monkeypatch.setattr(aidbmode, "run_agent", lambda rid, msg: (json.dumps({
        "recommendation": "approve", "compliance": "compliant", "summary": "fine", "reasons": [], "citations": [],
        "missing_information": []}), "ok"))
    seed_docs()
    rid = make_request()
    assert validator.validate(rid) == "ok"
    v = _validation(rid)
    assert v["recommendation"] == "needs_info" and any("without citing" in w for w in v["warnings"])


# ---- the objects AIDB mode reads (plain Postgres) -----------------------------------------------------------------------
def test_chunks_carry_filters_from_their_document(monkeypatch):
    seed_docs(meta={"department": "finance", "effective_date": "2031-02-03"})
    with connect() as c:
        row = c.execute("SELECT doc_type, department, effective_date, embed_text FROM kb_chunk ORDER BY id LIMIT 1").fetchone()
        assert row["doc_type"] == "travel" and row["department"] == "finance" and row["effective_date"] == date(2031, 2, 3)
        assert row["embed_text"].startswith("Travel Policy") or "\n" in row["embed_text"]
        ingest.ingest_text(title="Plain", source="plain.md", text="# Plain\n\n## Intro\nNothing special here at all.\n", conn=c)
        plain = c.execute("SELECT c.department, c.effective_date, d.created_at::date AS day FROM kb_chunk c JOIN kb_document d "
                          "ON d.id=c.doc_id WHERE d.source='plain.md'").fetchone()
        assert plain["department"] == "all" and plain["effective_date"] == plain["day"]


def test_applying_the_aidb_objects_again_changes_nothing():
    from trustdesk import aidbschema
    seed_docs()
    with connect() as c:
        before = c.execute("SELECT md5(string_agg(c::text, '|' ORDER BY id)) AS h FROM kb_chunk c").fetchone()["h"]
        aidbschema.apply(c)
        aidbschema.apply(c)
        c.commit()
        assert c.execute("SELECT md5(string_agg(c::text, '|' ORDER BY id)) AS h FROM kb_chunk c").fetchone()["h"] == before


def test_ingest_rejects_an_impossible_effective_date(client):
    r = client.post("/api/documents?doc_type=travel&effective_date=2026-13-45", headers=ADMIN, files={"file": ("x.md", POLICY.encode())})
    assert r.status_code == 422
    ok = client.post("/api/documents?doc_type=travel&department=finance&effective_date=2031-02-03", headers=ADMIN,
                     files={"file": ("x.md", POLICY.encode())})
    assert ok.status_code == 200, ok.text
    with connect() as c:
        assert c.execute("SELECT department, effective_date FROM kb_chunk LIMIT 1").fetchone() == {
            "department": "finance", "effective_date": date(2031, 2, 3)}


def test_a_role_with_only_the_two_views_reads_policy_and_balances_and_nothing_else():
    """The database-enforced boundary of the agent's purpose, shown on plain Postgres: the views run with their owner's rights, so the
    role needs no access to any table, and every unrelated read or any write is refused by Postgres."""
    import psycopg
    seed_docs()
    rid = make_request()
    with connect() as c:
        c.execute("DROP ROLE IF EXISTS td_view_only")
        c.execute("CREATE ROLE td_view_only NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
        c.execute("GRANT USAGE ON SCHEMA public TO td_view_only")
        c.execute("GRANT SELECT ON td_policy_v, td_employee_balance_v TO td_view_only")
        c.commit()
        try:
            c.execute("SET ROLE td_view_only")
            assert c.execute("SELECT count(*) AS n FROM td_policy_v").fetchone()["n"] > 0
            bal = c.execute("SELECT * FROM td_employee_balance_v WHERE request_id=%s", (rid,)).fetchone()
            assert bal["department"] == "finance" and bal["approved_days_same_type_year"] == 0
            assert not {"name", "email", "employee_no"} & set(bal)
            for q in ("SELECT 1 FROM employee", "SELECT 1 FROM request", "SELECT 1 FROM kb_chunk", "SELECT 1 FROM llm_account",
                      "SELECT 1 FROM app_setting", "SELECT 1 FROM api_token", "UPDATE request SET status='approved'",
                      "INSERT INTO request_event (request_id, actor, action) VALUES (1, 'x', 'y')", "DELETE FROM kb_document"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(q)
                c.rollback()
                c.execute("SET ROLE td_view_only")
        finally:
            c.rollback()
            c.execute("RESET ROLE")
            c.execute("DROP OWNED BY td_view_only")
            c.execute("DROP ROLE td_view_only")
            c.commit()


def test_balance_view_counts_approved_and_open_days_of_the_same_type_and_year():
    seed_docs()
    rid = make_request()
    with connect() as c:
        first = workflow.get(c, rid)
        year_start = first["start_date"]
        for status, offset in (("approved", 14), ("pending_review", 28)):
            r, _ = workflow.create(c, {"employee_no": "E1", "type": "vacation", "title": status, "description": "extra days off",
                                       "start_date": (year_start + timedelta(days=offset)).isoformat(),
                                       "end_date": (year_start + timedelta(days=offset + 1)).isoformat()}, actor="t")
            c.execute("UPDATE request SET status=%s WHERE id=%s", (status, r["id"]))
        c.commit()
        row = c.execute("SELECT approved_days_same_type_year AS a, open_days_same_type_year AS o FROM td_employee_balance_v "
                        "WHERE request_id=%s", (rid,)).fetchone()
    assert (year_start + timedelta(days=15)).year == year_start.year, "pick another date: the test dates cross a year end"
    assert row["a"] == 2 and row["o"] == 2


# ---- the operator commands ----------------------------------------------------------------------------------------------
def test_aidb_setup_on_plain_postgres_fails_cleanly_without_leaking_the_admin_connection(monkeypatch, capsys):
    unlock()
    dsn = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setenv("TRUSTDESK_AIDB_ADMIN_DSN", dsn)
    code = cli.main(["aidb-setup"])
    out = capsys.readouterr()
    assert code == 1 and "aidb-setup failed" in out.err
    secret = dsn.split("@")[0].split(":")[-1]
    assert secret not in out.out + out.err and dsn not in out.out + out.err
    with connect() as c:                                 # nothing was left behind: the seal table and objects do not exist
        assert c.execute("SELECT to_regclass('apgkit_aidb_seal') IS NULL AS gone").fetchone()["gone"]


def test_aidb_setup_refuses_while_the_vault_is_locked(monkeypatch, capsys):
    monkeypatch.setenv("TRUSTDESK_AIDB_ADMIN_DSN", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    assert cli.main(["aidb-setup"]) == 1
    assert "vault" in capsys.readouterr().err.lower()


def test_aidb_status_on_plain_postgres_reports_and_exits_zero(capsys):
    assert cli.main(["aidb-status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["requested_backend"] == "app" and report["aidb"]["aidb"]["available"] is False
    assert report["governance"]["available"] is False


def test_the_tampered_counter_and_the_seal_names_are_the_row_mac_ones():
    """Drift is reported through the same counter the row MACs use, with table `aidb_<kind>`."""
    from trustdesk import rowmac
    before = rowmac.tampered.labels("aidb_agent")._value.get()
    aidbmode._report_drift([{"kind": "agent", "name": "td_prevalidator", "problem": "modified"}])
    assert rowmac.tampered.labels("aidb_agent")._value.get() == before + 1
    assert aidbmode.DRIFT._value.get() == 1
    aidbmode._report_drift([])
    assert aidbmode.DRIFT._value.get() == 0
    assert core.vault is not None
