"""Sealed boot (agentpg-kit 0.4.1): with APGKIT_SEALED set the app serves nothing but /_seal/* and the health checks until an operator
unseals it; without it nothing changes (every other test runs that way)."""
import contextlib
import importlib
import sys

import pytest
from apgkit import sealed as sealing
from fastapi.testclient import TestClient

PASS = "correct-horse-battery-staple-9"


@pytest.fixture
def sealed_app(monkeypatch):
    """A fresh import of trustdesk.api with a sealed blob in the environment; the callbacks that need a database are replaced."""
    blob = sealing.seal({"DATABASE_URL": "postgresql://sealed/none", "TRUSTDESK_OIDC_CLIENT_SECRET": "cs"}, PASS)
    monkeypatch.setenv("APGKIT_SEALED", blob)
    import trustdesk
    saved = sys.modules["trustdesk.api"]
    sys.modules.pop("trustdesk.api", None)
    api = importlib.import_module("trustdesk.api")
    api.sealed._callbacks.clear()
    ran = []
    api.sealed.on_unsealed(lambda s, p: ran.append(s.get("DATABASE_URL")))

    @contextlib.asynccontextmanager
    async def noop(_):     # the real lifespan migrates and seeds the database, which this test does not have
        yield
    api.app.router.lifespan_context = api.sealed.deferred(noop)
    try:
        yield api, ran
    finally:
        sealing.set_active(None)
        sys.modules["trustdesk.api"] = saved
        trustdesk.api = saved


def test_sealed_app_answers_503_until_unsealed(sealed_app):
    api, ran = sealed_app
    with TestClient(api.app) as c:
        assert c.get("/api/stats").status_code == 503
        assert c.get("/mcp/").status_code == 503
        assert c.get("/healthz").status_code == 200
        assert c.get("/_seal/status").json() == {"state": "sealed"}
        key = c.get("/_seal/key").json()["public_key"]
        assert c.post("/_seal/unseal", json=sealing.make_envelope(key, PASS)).json() == {"state": "unsealed"}
        assert ran == ["postgresql://sealed/none"]
        assert c.get("/api/stats").status_code != 503
        from apgkit.config import database_url, read_secret
        assert database_url() == "postgresql://sealed/none" and read_secret("TRUSTDESK_OIDC_CLIENT_SECRET") == "cs"


def test_wrong_passphrase_stays_sealed(sealed_app):
    api, _ = sealed_app
    with TestClient(api.app) as c:
        key = c.get("/_seal/key").json()["public_key"]
        assert c.post("/_seal/unseal", json=sealing.make_envelope(key, "wrong-wrong-wrong-wrong-1")).status_code == 401
        assert c.get("/api/stats").status_code == 503


def test_without_a_blob_sealing_is_off(client):
    from trustdesk import api
    assert not api.sealed.enabled and api.sealed.state == "disabled"
    assert client.get("/_seal/status").status_code == 404
