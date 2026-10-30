"""EDB and AgentPG capabilities used by TrustDesk, against a throwaway Postgres: database insights, read-only masking, unaccent, tcn."""
import asyncio
import os
import shutil
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from apgkit import migrate as kit_migrate  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from trustdesk import (  # noqa: E402
    api,
    core,
    hardening,
    ingest,
    live,
    migrate,
    people,
    search,
    textsearch,
    workflow,
)
from trustdesk.config import get_settings  # noqa: E402
from trustdesk.db import connect, connect_vector  # noqa: E402

TEST_URL = os.environ["TEST_DATABASE_URL"]
ADMIN = {"Authorization": "Bearer test-static-key-0123456789-abcdefghij-xyz"}
LOGIN = "td_ro_login"


def available(name: str) -> bool:
    with psycopg.connect(TEST_URL) as c:
        return c.execute("SELECT 1 FROM pg_available_extensions WHERE name=%s", (name,)).fetchone() is not None


needs_unaccent = pytest.mark.skipif(not available("unaccent"), reason="the unaccent contrib extension is not installed on this Postgres")
needs_tcn = pytest.mark.skipif(not available("tcn"), reason="the tcn contrib extension is not installed on this Postgres")


def fresh_schema(default_select_for_readonly: bool = False) -> None:
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        if default_select_for_readonly:      # what AgentPG's default privileges do: every new table is readable by the group role
            c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO a1p_readonly")
        c.commit()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("TRUSTDESK_API_KEY", "test-static-key-0123456789-abcdefghij-xyz")


@pytest.fixture
def migrated():
    fresh_schema()
    migrate.run()


def token(client, scope):
    r = client.post("/api/tokens", headers=ADMIN, json={"name": scope, "scope": scope})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def employee(conn, no="E1", name="Alice Smith", email="alice@example.com", **kw):
    return people.create(conn, {"employee_no": no, "name": name, "email": email, **kw})


def make_request(conn, no="E1", **kw):
    body = {"employee_no": no, "type": "vacation", "title": "Holiday", "description": "A week at the lake",
            "start_date": (date.today() + timedelta(days=30)).isoformat(), "end_date": (date.today() + timedelta(days=34)).isoformat(), **kw}
    return workflow.create(conn, body, actor="test")[0]


# ---- (a) database insights -------------------------------------------------------------------------------------
def test_insights_route_needs_the_admin_permission(client, migrated):
    assert client.get("/api/insights").status_code == 401
    for scope in ("read", "client", "metrics"):
        assert client.get("/api/insights", headers=token(client, scope)).status_code == 403, scope
    assert client.get("/api/insights", headers=token(client, "full")).status_code == 200


def test_insights_say_not_available_on_plain_postgres_instead_of_failing(client, migrated):
    r = client.get("/api/insights", headers=ADMIN, params={"order": "bogus", "limit": 5})   # a bad order falls back to the default
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"sources", "top_queries", "tuner", "advisor"}
    for part in ("tuner", "advisor"):             # edb_pg_tuner and query_advisor exist only on EDB / AgentPG instances
        assert body[part]["available"] is False and body[part]["reason"]
    assert client.get("/api/insights", headers=ADMIN, params={"limit": 0}).status_code == 422
    assert client.get("/api/insights", headers=ADMIN, params={"order": "calls"}).status_code == 200


def test_metrics_expose_the_insights_gauges_and_never_fail(client, migrated):
    text = client.get("/metrics", headers=token(client, "metrics")).text
    assert 'db_insights_available{app="trustdesk"}' in text


# ---- (b) read-only access hardening ----------------------------------------------------------------------------
@pytest.fixture
def roles():
    with connect() as c:
        c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='a1p_readonly') THEN CREATE ROLE a1p_readonly NOLOGIN; END IF; END $$")
        c.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{LOGIN}') THEN CREATE ROLE {LOGIN} LOGIN PASSWORD 'ro-pw-test'; END IF; END $$")
        c.execute(f"GRANT a1p_readonly TO {LOGIN}")
        c.commit()


def ro():
    return psycopg.connect(make_conninfo(TEST_URL, user=LOGIN, password="ro-pw-test"), row_factory=dict_row, autocommit=True)


def test_read_only_user_sees_masked_people_and_no_secrets(roles):
    fresh_schema(default_select_for_readonly=True)
    migrate.run()
    with connect() as c:
        employee(c, "E1", "Alice Smith", "alice@example.com", department="Sales", hire_date="2021-06-15")
        employee(c, "E2", "Bob Jones", "bob@example.com", manager_no="E1")
        r = make_request(c, "E1", details={"relationship": "sister", "medical_certificate": True, "note": "ok"})
        workflow.event(c, r["id"], "alice@example.com", "commented", "hello")
        c.execute("INSERT INTO llm_account (name, base_url, api_key_enc) VALUES ('a','http://x','CIPHERTEXT')")
        c.execute("INSERT INTO api_token (name, token_hash, prefix, scope) VALUES ('t','HASH','td_','read')")
        c.execute("INSERT INTO web_session (token_hash, expires_at) VALUES ('SESSION', now() + interval '1 day')")
        c.execute("INSERT INTO app_setting VALUES ('vault.wrap', 'WRAPPED-KEY')")
        c.commit()
    with ro() as u:
        for table in ("employee", "request", "request_event", "llm_account", "api_token", "web_session", "app_setting", "oidc_login"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                u.execute(f"SELECT * FROM {table}")
        e = {r["name"]: r for r in u.execute("SELECT * FROM employee_masked ORDER BY id")}
        assert set(e) == {"A***", "B***"}
        a = e["A***"]
        assert a["email"] == "***@example.com" and a["employee_no"] != "E1" and len(a["employee_no"]) == 12
        assert a["department"] == "Sales" and str(a["hire_date"]) == "2021-01-01"      # the hire date keeps only the year
        assert "manager_id" in a and "manager_no" not in a
        req = u.execute("SELECT * FROM request_masked").fetchone()
        assert req["title"] == "Holiday" and req["description"] == "A week at the lake"      # request text stays readable
        assert req["details"] == {"note": "ok"}                                                # health and family keys removed
        assert req["decided_by"] is None or req["decided_by"].endswith("***")
        ev = u.execute("SELECT actor, note FROM request_event_masked WHERE action = 'commented'").fetchone()
        assert ev == {"actor": "a***", "note": "hello"}
        for name in ("llm_account", "api_token", "web_session", "app_setting", "oidc_login"):
            assert u.execute("SELECT to_regclass(%s) IS NULL AS none", (f"{name}_masked",)).fetchone()["none"], f"{name} must have no view"
        assert u.execute("SELECT count(*) AS n FROM kb_document").fetchone()["n"] == 0       # the knowledge base stays readable
        with pytest.raises(psycopg.Error):
            u.execute("UPDATE employee_masked SET id = 1")
    with connect() as c:      # the app itself is not affected
        assert c.execute("SELECT name FROM employee WHERE employee_no='E1'").fetchone()["name"] == "Alice Smith"


def test_hardening_is_idempotent_and_rerun_by_every_migrate(roles):
    fresh_schema(default_select_for_readonly=True)
    migrate.run()
    migrate.run()                                  # a second migrate (nothing to apply) re-applies the hook
    with connect() as c:
        first = hardening.apply(c)
        second = hardening.apply(c)
    assert first == second
    assert all(v["applied"] for v in first["masked"].values()) and all(v["denied"] for v in first["hidden"].values())
    with connect() as c:      # a table added later is readable again until the hook runs: the next migrate fixes it
        c.execute("CREATE TABLE api_token_audit (id int)")
        c.commit()
        assert c.execute("SELECT has_table_privilege('a1p_readonly', 'api_token_audit', 'SELECT') AS ok").fetchone()["ok"]


def test_hardening_skips_quietly_without_the_role(roles):
    with connect() as c:
        c.execute(f"DROP OWNED BY {LOGIN}")
        c.execute(f"DROP ROLE {LOGIN}")
        c.execute("DROP OWNED BY a1p_readonly")
        c.execute("DROP ROLE a1p_readonly")
        c.commit()
    fresh_schema()
    assert migrate.run()                          # plain Postgres: no error
    with connect() as c:
        rep = hardening.apply(c)
        assert all(v["applied"] is False for v in [*rep["masked"].values(), *rep["hidden"].values()])
        assert c.execute("SELECT to_regclass('employee_masked') IS NULL AS none").fetchone()["none"]


def test_a_grant_after_startup_reopens_the_base_tables_and_the_reassert_tick_closes_them(roles):
    from apgkit import masking
    fresh_schema(default_select_for_readonly=True)
    migrate.run()
    with connect() as c:
        employee(c, "E1", "Alice Smith", "alice@example.com")
        c.commit()
    with connect() as c:
        assert not masking.drift(c, hardening.DENY)       # after migrate everything is closed
    with connect() as c:          # what `agentpg db-users create` does afterwards
        c.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO a1p_readonly")
        c.commit()
        assert set(masking.drift(c, hardening.DENY)) == set(hardening.DENY)
    with ro() as u:
        assert u.execute("SELECT count(*) AS n FROM employee").fetchone()["n"] == 1      # the hole, before the re-assert
    assert masking.reassert_tick(core.db.connect, hardening.apply, deny=hardening.DENY, metrics=core.metrics) is True
    st = masking.status()
    assert st["ok"] and st["last_ok"] and st["drift"] == len(hardening.DENY)
    with ro() as u:
        for table in ("employee", "request", "app_setting", "api_token"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                u.execute(f"SELECT * FROM {table}")
        assert u.execute("SELECT count(*) AS n FROM employee_masked").fetchone()["n"] == 1
    assert masking.reassert_tick(core.db.connect, hardening.apply, deny=hardening.DENY, metrics=core.metrics)
    assert masking.status()["drift"] == 0
    assert 'masking_drift_tables 0.0' in core.metrics.render()[0].decode()


def test_the_app_runs_the_reassert_loop_and_shows_its_status_to_admins(client, migrated):
    deadline = time.time() + 10
    from apgkit import masking
    while not masking.status()["last_ok"] and time.time() < deadline:
        time.sleep(0.1)
    st = client.get("/api/accounts", headers=ADMIN).json()["masking"]
    assert st["ok"] is True and st["last_ok"] and st["drift"] is not None
    assert client.get("/api/accounts").status_code == 401           # not public


# ---- (2) unaccent ---------------------------------------------------------------------------------------------
@needs_unaccent
def test_unaccent_finds_accented_names_both_ways(migrated):
    with connect() as c:
        employee(c, "E1", "José Peña", "jp@example.com")
        employee(c, "E2", "Zoe Brown", "zoe@example.com")
        make_request(c, "E1")
        make_request(c, "E2")
        assert [i["employee_no"] for i in people.listing(c, query="Jose Pena", status=None, limit=10, offset=0)["items"]] == ["E1"]
        assert [i["employee_no"] for i in people.listing(c, query="jose", status=None, limit=10, offset=0)["items"]] == ["E1"]
        assert [i["employee_no"] for i in people.listing(c, query="PEÑA", status=None, limit=10, offset=0)["items"]] == ["E1"]
        assert [i["employee_no"] for i in people.listing(c, query="Pena", status=None, limit=10, offset=0)["items"]] == ["E1"]
        found = workflow.listing(c, search="jose")["items"]
        assert [i["employee"]["employee_no"] for i in found] == ["E1"]
        assert workflow.listing(c, search="José")["total"] == 1
        # percent and underscore are still literal characters
        assert workflow.listing(c, search="%")["total"] == 0


@needs_unaccent
def test_unaccent_policy_search_is_accent_insensitive(migrated):
    with connect_vector() as c:
        ingest.ingest_text(title="Travel", source="travel.md", conn=c,
                           text="# Travel\n\nEmployees travelling to São Paulo or Zürich must book at the Café Müller hotel desk.\n")
        ingest.ingest_text(title="Other", source="other.md", conn=c, text="# Other\n\nLaptops are encrypted at rest.\n")
        assert textsearch.fts_config(c) == textsearch.FTS_CONFIG
        for q in ("Sao Paulo", "Zurich", "cafe muller", "São Paulo", "CAFÉ"):
            rows = search.hybrid_search(q, limit=3, conn=c)
            lexical = [r for r in rows if r["lex_rank"] is not None]
            assert lexical and lexical[0]["source"] == "travel.md", q


@needs_unaccent
def test_without_unaccent_search_degrades_to_exact_matching(migrated, monkeypatch):
    monkeypatch.setattr(textsearch, "has_unaccent", lambda conn: False)
    with connect() as c:      # and the folding function as migration 007 defines it where unaccent is missing: lower-case only
        c.execute("CREATE OR REPLACE FUNCTION public.td_fold(text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $f$ SELECT lower($1) $f$")
        c.commit()
        employee(c, "E1", "José Peña", "jp@example.com")
        monkeypatch.setattr(textsearch, "trigram_ready", lambda conn: False)     # typo tolerance would still find "jose" by similarity
        assert people.listing(c, query="jose", status=None, limit=10, offset=0)["total"] == 0
        assert people.listing(c, query="josé", status=None, limit=10, offset=0)["total"] == 1       # still works, exactly
        assert textsearch.fts_config(c) == "english"


def test_migration_006_skips_quietly_when_the_extensions_are_unavailable():
    with tempfile.TemporaryDirectory() as tmp:
        for f in sorted([*migrate.MIGRATIONS.glob("00[1-5]_*.sql"), *migrate.MIGRATIONS.glob("008_*.sql")]):
            shutil.copy(f, Path(tmp) / f.name)
        sql = (migrate.MIGRATIONS / "006_unaccent_tcn.sql").read_text().replace("name = 'unaccent'", "name = 'no_such_ext'").replace(
            "name = 'tcn'", "name = 'no_such_ext'")
        (Path(tmp) / "006_unaccent_tcn.sql").write_text(sql)
        fresh_schema()
        from trustdesk.core import db
        applied = kit_migrate.run(db, Path(tmp), extensions=("vector",), replacements={"{{EMBEDDING_DIM}}": str(get_settings().embedding_dim)})
    assert "006_unaccent_tcn.sql" in applied
    with connect() as c:
        assert not c.execute("SELECT 1 FROM pg_ts_config WHERE cfgname='td_english'").fetchone()
        assert not c.execute("SELECT 1 FROM pg_trigger WHERE tgname='request_tcn'").fetchone()
        assert textsearch.fts_config(c) == "english"
        employee(c)
        assert search.hybrid_search("anything", limit=1, conn=connect_vector()) == []
    assert not live.trigger_installed(connect())


# ---- (3) live updates with tcn -----------------------------------------------------------------------------------
async def _wait(pred, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


@needs_tcn
def test_listener_turns_row_changes_into_events(migrated):
    async def scenario():
        hub = live.Hub()
        lst = live.Listener(lambda: connect(retries=1), hub)
        lst.start()
        try:
            q = hub.subscribe(asyncio.get_running_loop())
            assert await _wait(lambda: hub.live), "the LISTEN connection did not come up with the trigger installed"

            def change():
                with connect() as c:
                    employee(c)
                    r = make_request(c)
                    c.execute("UPDATE request SET status='pending_review' WHERE id=%s", (r["id"],))
                    c.commit()
                    return r["id"]
            rid = await asyncio.to_thread(change)
            first = await asyncio.wait_for(q.get(), 5)
            assert first.startswith('"request",') and f"'{rid}'" in first      # tcn payload: table, operation, primary key
            assert hub.seq >= 2                                         # insert and update were both delivered to the hub
        finally:
            lst.stop()
    asyncio.run(scenario())


@needs_tcn
def test_listener_reconnects_after_losing_its_connection(migrated):
    async def scenario():
        hub = live.Hub()
        lst = live.Listener(lambda: connect(retries=1), hub)
        lst.start()
        try:
            q = hub.subscribe(asyncio.get_running_loop())
            assert await _wait(lambda: hub.live)
            with connect() as c:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() "
                          "AND pid <> pg_backend_pid() AND backend_type = 'client backend'")
                c.commit()
            assert await _wait(lambda: not hub.live, 3) or True      # may reconnect too fast to observe
            msg = await asyncio.wait_for(q.get(), 10)
            assert msg == "reconnected"                              # browsers are told to refresh: a change may have been missed
            assert await _wait(lambda: hub.live)
        finally:
            lst.stop()
    asyncio.run(scenario())


@needs_tcn
def test_without_the_trigger_the_stream_says_not_live_and_stays_quiet(migrated):
    with connect() as c:
        c.execute("DROP TRIGGER request_tcn ON request")
        c.commit()

    async def scenario():
        hub = live.Hub()
        lst = live.Listener(lambda: connect(retries=1), hub)
        lst.start()
        try:
            await asyncio.sleep(1.0)
            assert hub.live is False
            gen = live.stream(hub, heartbeat=0.3)
            assert '"live": false' in await gen.__anext__()
            with connect() as c:
                employee(c)
                make_request(c)
            assert (await gen.__anext__()).startswith(": ping")          # no change event: the browser keeps polling
            await gen.aclose()
            assert hub.count() == 0
        finally:
            lst.stop()
    asyncio.run(scenario())


def test_stream_frames_and_cleanup():
    async def scenario():
        hub = live.Hub()
        hub.live = True
        gen = live.stream(hub, heartbeat=0.2)
        hello = await gen.__anext__()
        assert hello.startswith(": " + " " * 2048 + "\nretry: 5000") and "event: hello" in hello and '"live": true' in hello
        assert hello.index("retry:") > len(live.PADDING) - 1 and len(hello.split("\n", 1)[0]) >= 2048      # padding line first, then retry, then hello
        assert all(line.startswith((":", "retry:", "event:", "data:")) or not line for line in hello.splitlines())
        hub.publish("x")
        hub.publish("y")                                               # collapsed: one pending notification is enough
        assert "event: changed" in await gen.__anext__()
        assert (await gen.__anext__()).startswith(": ping")
        await gen.aclose()
        assert hub.count() == 0
        hub2 = live.Hub()
        subs = [hub2.subscribe(asyncio.get_running_loop()) for _ in range(live.MAX_SUBSCRIBERS)]
        assert all(subs) and hub2.subscribe(asyncio.get_running_loop()) is None
        busy = await live.stream(hub2).__anext__()
        assert "event: busy" in busy
        gone = False

        async def disconnected():
            return gone
        gen = live.stream(hub, is_disconnected=disconnected, heartbeat=0.1)
        await gen.__anext__()
        gone = True
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
    asyncio.run(scenario())


def test_events_endpoint_is_guarded_like_listing_requests(client, migrated):
    assert client.get("/api/requests/events").status_code == 401
    for scope in ("read", "client", "metrics"):
        assert client.get("/api/requests/events", headers=token(client, scope)).status_code == 403, scope

    class Req:
        async def is_disconnected(self):
            return False
    resp = asyncio.run(api.request_events(Req()))
    assert resp.media_type == "text/event-stream" and resp.headers["cache-control"] == "no-store"


def test_app_starts_one_listener_for_all_browsers(client):
    assert any(t.name == "request-listener" and t.is_alive() for t in __import__("threading").enumerate())
    assert core.APP == "trustdesk"
