"""Request-body cap for every route: 1 MiB by default, a larger bound only on the document upload route; 413 beyond it.

Pure ASGI. A declared Content-Length over the cap is refused before anything is read; a body with no length (chunked) or a wrong one is
counted as it streams and refused the moment it passes the cap, so nothing buffers more than the cap. The kit's SecurityGuard has a
single cap for all its prefixes, so the per-route bound lives here.
"""
import json

DEFAULT_MAX = 1024 * 1024                    # JSON and form bodies
UPLOAD_MAX = 11 * 1024 * 1024                # the document upload: the 10 MB file limit plus the multipart framing
UPLOAD_PATHS = frozenset({"/api/documents"})


class _TooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app, *, default: int = DEFAULT_MAX, upload: int = UPLOAD_MAX, upload_paths=UPLOAD_PATHS):
        self.app, self.default, self.upload, self.upload_paths = app, default, upload, frozenset(upload_paths)

    def cap_for(self, method: str, path: str) -> int:
        return self.upload if method == "POST" and path.rstrip("/") in self.upload_paths else self.default

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = self.cap_for(scope["method"], scope["path"])
        started = {"v": False}

        async def reject():
            body = json.dumps({"detail": "request body too large"}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})

        for k, v in scope["headers"]:
            if k.lower() == b"content-length" and v.isdigit() and int(v) > cap:
                return await reject()
        seen = {"n": 0}

        async def counted():
            msg = await receive()
            if msg["type"] == "http.request":
                seen["n"] += len(msg.get("body", b""))
                if seen["n"] > cap:
                    raise _TooLarge
            return msg

        async def watch(msg):
            if msg["type"] == "http.response.start":
                started["v"] = True
            await send(msg)

        try:
            await self.app(scope, counted, watch)
        except _TooLarge:
            if started["v"]:
                raise
            await reject()
