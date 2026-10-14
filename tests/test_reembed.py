"""Model switching against a disposable Postgres with pgvector (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import ingest, migrate, search  # noqa: E402
from trustdesk.db import connect, connect_vector  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "src" / "trustdesk" / "seed" / "acme"


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    c = connect_vector()
    for f in sorted(SEED.glob("*.md")):
        ingest.ingest_file(f, conn=c)
    yield c
    c.close()


def test_reembed_changes_dimension_and_search_still_works(conn, monkeypatch):
    assert migrate.column_dim(conn) == 768
    before = search.hybrid_search("how fast do you notify customers of a breach", limit=1, conn=conn)[0]["source"]
    n = conn.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"]
    conn.commit()

    monkeypatch.setenv("EMBEDDING_DIM", "64")
    progress = []
    result = migrate.reembed(batch=5, progress=progress.append)
    assert result == {"chunks": n, "dimension": 64, "previous_dimension": 768}
    assert any("768 -> 64" in p for p in progress)

    assert migrate.column_dim(conn) == 64
    nulls = conn.execute("SELECT count(*) AS n FROM kb_chunk WHERE embedding IS NULL").fetchone()["n"]
    assert nulls == 0
    after = search.hybrid_search("how fast do you notify customers of a breach", limit=3, conn=conn)
    assert before in [r["source"] for r in after]
    # new ingests use the new size
    assert ingest.ingest_text(title="T", source="t.md", text="# T\n\n## S\n\nbody text here", conn=conn)["chunks"] == 1


def test_reembed_rejects_unindexable_dimension(conn, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "3072")
    with pytest.raises(ValueError, match="2000"):
        migrate.reembed()
    assert migrate.column_dim(conn) == 768  # untouched
