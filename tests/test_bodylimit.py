"""Every route has a body cap: 1 MiB by default, a larger bound only for the document upload; chunked bodies are counted too."""
import asyncio

from trustdesk import bodylimit
from trustdesk.bodylimit import DEFAULT_MAX, UPLOAD_MAX, BodyLimit


async def _drain(scope, receive, send):
    while True:
        msg = await receive()
        if msg["type"] == "http.request" and not msg.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def call(path, chunks, length=None, method="POST"):
    """Run the middleware over a fake request; returns (status, whether the app was reached)."""
    sent, reached = [], {"v": False}

    async def app(scope, receive, send):
        reached["v"] = True
        await _drain(scope, receive, send)

    headers = [] if length is None else [(b"content-length", str(length).encode())]
    pending = list(chunks)

    async def receive():
        body = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": body, "more_body": bool(pending)}

    async def send(msg):
        sent.append(msg)

    asyncio.run(BodyLimit(app)({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
    return sent[0]["status"], reached["v"]


def test_a_declared_length_over_the_cap_is_refused_before_the_app_runs():
    assert call("/api/v1/requests", [b"x"], length=DEFAULT_MAX + 1) == (413, False)
    assert call("/mcp/", [b"x"], length=DEFAULT_MAX + 1) == (413, False)
    assert call("/api/v1/requests", [b"x" * 100], length=100) == (200, True)


def test_a_chunked_body_is_counted_as_it_streams():
    chunk = b"x" * 65536
    assert call("/api/v1/employees", [chunk] * (DEFAULT_MAX // 65536 + 1)) == (413, True)       # no Content-Length: stopped mid-stream
    assert call("/api/v1/employees", [chunk] * (DEFAULT_MAX // 65536)) == (200, True)
    assert call("/api/v1/employees", [chunk] * (DEFAULT_MAX // 65536 + 1), length=10) == (413, True)   # a lying length cannot widen the cap


def test_only_the_upload_route_has_a_larger_bound():
    big = DEFAULT_MAX * 5
    assert call("/api/documents", [b"x" * big], length=big) == (200, True)
    assert call("/api/documents", [b"x" * (UPLOAD_MAX + 1)], length=UPLOAD_MAX + 1) == (413, False)
    assert call("/api/documents/1", [b"x" * big], length=big, method="POST")[0] == 413              # other paths under it do not inherit it
    assert call("/api/documents", [b"x" * big], length=big, method="PUT")[0] == 413                 # nor other methods
    assert call("/api/v1/requests", [b"x" * big], length=big)[0] == 413


def test_the_app_has_the_limit_installed():
    from trustdesk import api
    assert any(m.cls is BodyLimit for m in api.app.user_middleware)
    assert bodylimit.UPLOAD_MAX > 10 * 1024 * 1024 >= bodylimit.DEFAULT_MAX


def test_http_level_413_through_the_whole_stack(client):
    r = client.post("/api/login", headers={"X-TrustDesk-CSRF": "1"}, content=b"x" * (DEFAULT_MAX + 10))
    assert r.status_code == 413
