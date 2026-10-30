"""API integration tests against a disposable Postgres with pgvector (TEST_DATABASE_URL)."""
import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import migrate  # noqa: E402
from trustdesk.db import connect  # noqa: E402

AUTH = {"Authorization": "Bearer test-static-key-0123456789-abcdefghij-xyz"}


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "test-static-key-0123456789-abcdefghij-xyz")
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()


def test_seed_stats_list_view_delete_roundtrip(client):
    assert client.get("/api/stats", headers=AUTH).json()["documents"] == 0

    r = client.post("/api/seed", headers=AUTH).json()["results"]
    assert len(r) == 16 and all(x["status"] == "ingested" for x in r)
    again = client.post("/api/seed", headers=AUTH).json()["results"]
    assert all(x["status"] == "unchanged" for x in again)  # idempotent

    stats = client.get("/api/stats", headers=AUTH).json()
    assert stats["documents"] == 16 and stats["chunks"] > 16 and stats["semantic"] is False

    docs = client.get("/api/documents", headers=AUTH).json()
    doc = docs[0]
    detail = client.get(f"/api/documents/{doc['id']}", headers=AUTH).json()
    assert detail["title"] == doc["title"] and len(detail["chunks"]) == doc["chunks"]

    assert client.delete(f"/api/documents/{doc['id']}", headers=AUTH).json() == {"deleted": doc["id"]}
    assert client.get(f"/api/documents/{doc['id']}", headers=AUTH).status_code == 404
    assert client.delete(f"/api/documents/{doc['id']}", headers=AUTH).status_code == 404
    assert client.get("/api/stats", headers=AUTH).json()["documents"] == 15


def test_upload_search_and_validation(client):
    body = b"# Guest Wifi\n\n## Rules\n\nGuests use an isolated network with no access to internal systems."
    up = client.post("/api/documents", headers=AUTH, files={"file": ("../guest-wifi.md", body)}).json()
    assert up["status"] == "ingested"
    hits = client.get("/api/search", headers=AUTH, params={"q": "isolated guest network"}).json()["results"]
    assert hits[0]["source"] == "guest-wifi.md"  # path components stripped
    assert client.get("/api/search", headers=AUTH, params={"q": "x" * 501}).status_code == 422
    bad = client.post("/api/documents", headers=AUTH, files={"file": ("broken.pdf", b"not a pdf")})
    assert bad.status_code == 422


def test_endpoints_require_auth(client):
    for method, path in [("get", "/api/stats"), ("post", "/api/seed"), ("get", "/api/documents/1"),
                         ("delete", "/api/documents/1")]:
        assert getattr(client, method)(path, headers={"X-TrustDesk-CSRF": "1"}).status_code == 401, path
