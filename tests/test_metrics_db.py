"""The /metrics endpoint and its scrape-only token scope."""
import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import auth, core, migrate, vault  # noqa: E402
from trustdesk.db import connect  # noqa: E402

H = {"X-TrustDesk-CSRF": "1"}
PASSWORD = "correct-horse-battery"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _db(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    for v in ("TRUSTDESK_API_KEY", "TRUSTDESK_ALLOW_ANONYMOUS", "TRUSTDESK_ENCRYPTION_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_API_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("TRUSTDESK_ENCRYPTION_KEY_FILE", "/nonexistent/key")
    core.auth.limiter.clear()
    core.local._setup_code = None
    vault.lock()
    client.cookies.clear()
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()


def token(client, scope):
    with connect() as c:
        if not auth.admin_configured(c):
            auth.set_admin_password(c, PASSWORD)
    client.post("/api/login", json={"password": PASSWORD}, headers=H)
    tok = client.post("/api/tokens", json={"name": scope, "scope": scope}, headers=H).json()["token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {tok}"}


def test_metrics_needs_a_metrics_token_and_that_token_reads_nothing_else(client):
    m, read = token(client, "metrics"), token(client, "read")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers=read).status_code == 403
    assert client.get("/metrics", headers=m).status_code == 200
    assert client.get("/api/documents", headers=m).status_code == 403
    assert client.get("/api/search", params={"q": "x"}, headers=m).status_code == 403
    assert client.post("/mcp", headers=m, json={}).status_code == 403
    assert client.get("/api/documents", headers=read).status_code == 200      # read tokens are unaffected


def test_metrics_use_the_shared_names_with_low_cardinality_labels(client):
    m, read = token(client, "metrics"), token(client, "read")
    client.get("/api/documents/424242", headers=read)
    text = client.get("/metrics", headers=m).text
    assert 'db_up{app="trustdesk"} 1.0' in text and 'app_info{app="trustdesk"' in text
    assert "trustdesk_documents" in text and "http_request_duration_seconds_bucket" in text
    assert 'route="/api/documents/{doc_id}"' in text and "424242" not in text
    assert 'status="404"' in text
