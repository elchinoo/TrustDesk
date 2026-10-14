"""Integration tests: need DATABASE_URL pointing at a *disposable* Postgres with pgvector."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.db
if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from trustdesk import ingest, migrate, search
from trustdesk.db import connect, connect_vector

SEED = Path(__file__).resolve().parents[1] / "src" / "trustdesk" / "seed" / "acme"


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    c = connect_vector()
    for f in sorted(SEED.glob("*.md")):
        ingest.ingest_file(f, conn=c)
    yield c
    c.close()


@pytest.mark.parametrize(
    "query,expected_source",
    [
        ("how is customer data encrypted at rest", "encryption-policy.md"),
        ("how fast do you notify customers of a breach", "incident-response-policy.md"),
        ("what is your RTO and RPO", "business-continuity-policy.md"),
        ("remediation SLA for critical vulnerabilities", "vulnerability-management-policy.md"),
        ("do you require MFA", "access-control-policy.md"),
    ],
)
def test_expected_source_in_top3(conn, query, expected_source):
    # The offline hash embedder is lexical, so rank-1 is not guaranteed; top-3 is what RAG needs.
    rows = search.hybrid_search(query, limit=3, conn=conn)
    assert expected_source in [r["source"] for r in rows]


def test_ingest_is_idempotent_and_versions(conn):
    f = SEED / "encryption-policy.md"
    assert ingest.ingest_file(f, conn=conn)["status"] == "unchanged"
    r = ingest.ingest_text(title="Encryption", source=f.name, text=f.read_text() + "\n\n## New\n\nPQC plans.", conn=conn)
    assert r["version"] == 2
    n = conn.execute("SELECT count(*) AS n FROM kb_document WHERE source=%s", (f.name,)).fetchone()["n"]
    assert n == 1
