import pytest
from starlette.requests import Request

from trustdesk import api, core
from trustdesk.ingest import safe_source_name


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    core.auth.limiter.clear()
    for v in ("TRUSTDESK_ALLOW_ANONYMOUS", "TRUSTDESK_API_KEY", "TRUSTDESK_API_KEY_FILE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_API_KEY_FILE", "/nonexistent/key")


def _request(headers=None, client="9.9.9.9"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b"",
                    "client": (client, 1), "scheme": "http", "server": ("t", 80)})


def test_health_is_public(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_fails_closed_without_credentials(client):
    assert client.get("/api/search", params={"q": "x"}).status_code == 401
    assert client.post("/mcp/", json={}).status_code == 401
    # an empty bearer value must not match an empty key
    assert client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer "}).status_code == 401


def test_wrong_credentials_are_rejected_and_rate_limited(client, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    bad = {"Authorization": "Bearer bad"}
    codes = [client.get("/api/search", params={"q": "x"}, headers=bad).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # even the right key is refused while the client is locked out
    assert client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer k"}).status_code == 429


def test_missing_credentials_do_not_count_towards_lockout(client):
    for _ in range(15):
        assert client.get("/api/search", params={"q": "x"}).status_code == 401


def test_static_key_works_and_is_full_scope(monkeypatch):
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    p = api.authenticate(_request({"Authorization": "Bearer k"}))
    assert p is not None and p.kind == "static" and p.can("anything:at-all")


def test_anonymous_mode_is_explicit_opt_in(monkeypatch):
    assert api.authenticate(_request()) is None
    monkeypatch.setenv("TRUSTDESK_ALLOW_ANONYMOUS", "true")
    assert api.authenticate(_request()).kind == "anonymous"


def test_browser_writes_need_the_csrf_header(client):
    assert client.post("/api/login", json={"password": "x"}).status_code == 403
    assert client.delete("/api/documents/1").status_code == 403
    # with the header the request proceeds to normal auth handling
    assert client.delete("/api/documents/1", headers={"X-TrustDesk-CSRF": "1"}).status_code == 401


def test_security_headers_and_no_api_docs(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "img-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "<script>" not in r.text and "style=" not in r.text  # no inline script/style: CSP stays strict
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
