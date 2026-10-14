import pytest

from trustdesk import api
from trustdesk.ingest import safe_source_name


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    api._failures.clear()
    monkeypatch.delenv("TRUSTDESK_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("TRUSTDESK_API_KEY", raising=False)


def test_health_is_public(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_fails_closed_without_a_key(client):
    assert client.get("/api/search", params={"q": "x"}).status_code == 401
    assert client.post("/mcp/", json={}).status_code == 401
    # an empty bearer value must not match an empty key
    assert client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer "}).status_code == 401


def test_api_and_mcp_require_the_right_key(client, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    assert client.get("/api/search", params={"q": "x"}).status_code == 401
    assert client.post("/mcp/", json={}).status_code == 401
    assert client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_repeated_failures_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    codes = [client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer bad"}).status_code
             for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # even the right key is refused while the client is locked out
    assert client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer k"}).status_code == 429


def test_anonymous_mode_is_explicit_opt_in(monkeypatch):
    assert api._check(None, "c") == 401
    monkeypatch.setenv("TRUSTDESK_ALLOW_ANONYMOUS", "true")
    assert api._check(None, "c") == 200


def test_security_headers_and_no_api_docs(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "<script>" not in r.text and "style=" not in r.text  # no inline script/style: CSP stays strict
    assert "img-src 'self'" in r.headers["content-security-policy"]
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_readyz_does_not_leak_connection_details(client, monkeypatch):
    marker = "marker" + "token"
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:{marker}@127.0.0.1:1/db")
    r = client.get("/readyz")
    assert r.status_code == 503
    assert marker not in r.text and "127.0.0.1" not in r.text


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    big = b"a" * (api.MAX_UPLOAD + 1)
    r = client.post("/api/documents", headers={"Authorization": "Bearer k"}, files={"file": ("x.md", big)})
    assert r.status_code == 413


def test_source_names_are_sanitised():
    assert safe_source_name("../../etc/passwd") == "passwd"
    assert safe_source_name("..\\..\\win\\x.md") == "x.md"
    assert safe_source_name("a\x00b\nc.md") == "abc.md"
    assert safe_source_name("") == "upload.txt"
    assert len(safe_source_name("n" * 1000)) == 200


def test_static_assets_are_served(client):
    for path in ("/static/app.js", "/static/app.css", "/static/clients.js", "/static/logo.svg", "/static/favicon.svg"):
        assert client.get(path).status_code == 200, path
