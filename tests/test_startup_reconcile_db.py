"""A 768-dim database started with another EMBEDDING_DIM (the fleet env change), against a disposable Postgres."""
import os
from pathlib import Path

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import cli, embeddings, ingest, migrate, search  # noqa: E402
from trustdesk.db import connect, connect_vector  # noqa: E402
from trustdesk.embeddings import EmbeddingError  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "src" / "trustdesk" / "seed" / "acme"
AUTH = {"Authorization": "Bearer k"}


class Counter:
    """Wraps the real embedder to count calls and to simulate a provider outage."""

    def __init__(self, monkeypatch):
        self.calls = 0
        self.down = False
        self.real = embeddings.embed
        monkeypatch.setattr(embeddings, "embed", self)

    def __call__(self, texts, cfg=None):
        self.calls += 1
        if self.down:
            raise EmbeddingError("embeddings endpoint returned HTTP 503: upstream unavailable")
        return self.real(texts, cfg)


@pytest.fixture
def provider(monkeypatch):
    return Counter(monkeypatch)


@pytest.fixture
def db768(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    with connect_vector() as c:
        for f in sorted(SEED.glob("*.md"))[:4]:
            ingest.ingest_file(f, conn=c)
    monkeypatch.setenv("EMBEDDING_DIM", "1536")
    migrate._startup["message"] = ""
    return None


def start(monkeypatch):
    monkeypatch.setattr(cli, "_serve", lambda: None)
    return cli._start()


def stored():
    with connect_vector() as c:
        return migrate.column_dim(c), c.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"]


def test_startup_with_a_small_corpus_reconciles_768_to_1536(db768, provider, monkeypatch):
    assert start(monkeypatch) == 0                    # used to print 'startup failed' and exit 1, forever
    dim, n = stored()
    assert dim == 1536 and n > 0
    with connect_vector() as c:
        assert c.execute("SELECT count(*) AS n FROM kb_chunk WHERE vector_dims(embedding) <> 1536").fetchone()["n"] == 0
        assert search.hybrid_search("how fast do you notify customers of a breach", conn=c)
        assert not migrate.embedding_state(c)["needs_reembedding"]


def test_second_start_is_a_noop(db768, provider, monkeypatch):
    assert start(monkeypatch) == 0
    calls = provider.calls
    assert start(monkeypatch) == 0
    assert provider.calls == calls


def test_provider_down_at_startup_keeps_the_old_data_and_logs_one_line(db768, provider, monkeypatch, capsys):
    provider.down = True
    _, before = stored()
    assert start(monkeypatch) == 0
    assert stored() == (768, before)                  # atomic: nothing changed
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1 and "HTTP 503" in err
    with connect_vector() as c:
        st = migrate.embedding_state(c)
    assert st["needs_reembedding"] and "failed" in st["reason"]
    provider.down = False                             # the next start fixes it
    assert start(monkeypatch) == 0
    assert stored() == (1536, before)


def test_large_corpus_is_not_re_embedded_automatically(db768, provider, monkeypatch):
    msgs = []
    r = migrate.reconcile_on_startup(max_chunks=1, progress=msgs.append)
    assert r["action"] == "too_large" and "trustdesk reembed" in r["message"]
    assert len(msgs) == 1 and msgs[0].startswith("WARNING")
    assert stored()[0] == 768 and provider.calls == 0
    assert start(monkeypatch) == 0                    # default limit 5000: this small corpus is fixed
    assert stored()[0] == 1536


def test_dimension_above_the_index_limit_is_refused_but_the_app_starts(db768, provider, monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDING_DIM", "3072")
    assert start(monkeypatch) == 0
    assert "2000" in capsys.readouterr().err
    assert stored()[0] == 768 and provider.calls == 0


def test_search_during_a_mismatch_is_text_only_not_a_500_and_uploads_get_503(db768, provider, client):
    with connect_vector() as c:
        assert search.hybrid_search("notify customers of a breach", conn=c)       # full-text results, no exception
    r = client.get("/api/search", headers=AUTH, params={"q": "notify customers of a breach"})
    assert r.status_code == 200 and r.json()["results"]
    up = client.post("/api/documents", headers=AUTH, files={"file": ("n.md", b"# T\n\n## S\n\nsome new text to store")})
    assert up.status_code == 503 and "re-embedding" in up.json()["detail"]
    assert stored()[0] == 768


def test_status_apis_surface_needs_reembedding_and_activating_fixes_it(db768, provider, client):
    st = client.get("/api/embeddings/status", headers=AUTH).json()
    assert st["needs_reembedding"] and st["stored_dimension"] == 768 and st["configured_dimension"] == 1536
    assert client.get("/api/stats", headers=AUTH).json()["needs_reembedding"] is True
    # activating the (unchanged) environment configuration no longer says 'nothing to re-embed' while mismatched
    r = client.post("/api/embeddings/activate", headers=AUTH, json={"account": "env"}).json()
    assert r["started"] is True
    import time
    for _ in range(100):
        if client.get("/api/embeddings/status", headers=AUTH).json()["job"]["state"] != "running":
            break
        time.sleep(0.1)
    st = client.get("/api/embeddings/status", headers=AUTH).json()
    assert st["job"]["state"] == "done" and not st["needs_reembedding"] and st["stored_dimension"] == 1536
