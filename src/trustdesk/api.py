import contextlib
import sys
import time
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, auth, vault
from .auth import Principal
from .config import get_settings
from .db import connect
from .ingest import ingest_file, ingest_text, parse_bytes, safe_source_name
from .mcp_server import mcp
from .search import hybrid_search

STATIC = Path(__file__).resolve().parent / "static"
SEED_PACK = Path(__file__).resolve().parent / "seed" / "acme"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_QUERY = 500

mcp_app = mcp.streamable_http_app(streamable_http_path="/", host="0.0.0.0")


COOKIE = "td_session"
CSRF_HEADER = "x-trustdesk-csrf"
_announced = False
_auto_unlock_tried = False


def _announce_setup_code() -> None:
    """Print the one-time setup code to the server log (once per process)."""
    global _announced
    if not _announced:
        _announced = True
        print("\n=== TrustDesk first-run setup ===\n"
              f"Open the app in your browser and enter this one-time code to create the admin password:\n"
              f"    {auth.setup_code()}\n"
              "(It changes every time the server restarts until setup is complete.)\n", file=sys.stderr, flush=True)


def _startup_tasks() -> None:
    """Best effort: unlock the vault with a platform secret and announce the setup code. Never blocks startup."""
    global _auto_unlock_tried
    try:
        with connect(retries=1) as conn:
            _auto_unlock_tried = True
            vault.try_unlock_with_platform_secret(conn)
            if auth.needs_setup(conn):
                _announce_setup_code()
    except Exception:  # noqa: BLE001 - database may be asleep; it is retried on the first request
        pass


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    _startup_tasks()
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


# --- authentication -------------------------------------------------------------------------
# Fail closed: with no credential configured every protected route is denied (and the setup flow is offered),
# unless the operator explicitly sets TRUSTDESK_ALLOW_ANONYMOUS=true.

_FAIL_WINDOW, _FAIL_LIMIT = 60.0, 10
_failures: dict[str, list[float]] = {}


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(client, []) if now - t < _FAIL_WINDOW]
    _failures[client] = recent
    return len(recent) >= _FAIL_LIMIT


def _record_failure(client: str) -> None:
    _failures.setdefault(client, []).append(time.monotonic())
    if len(_failures) > 10_000:  # bound memory under address churn
        _failures.clear()


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def _db_unavailable() -> HTTPException:
    return HTTPException(503, "database unavailable; it may be waking up, retry in a few seconds")


def authenticate(request: Request, *, allow_cookie: bool = True) -> Principal | None:
    """Return the caller's Principal, or None if no credential was presented. Raises 401/429 for bad credentials."""
    if get_settings().allow_anonymous:
        return Principal("anonymous", "full")
    client = _client(request)
    if _rate_limited(client):
        raise HTTPException(429, "too many failed attempts; wait a minute")
    raw = _bearer(request)
    if raw is not None:
        if auth.static_key_matches(raw):
            return Principal("static", "full")
        if raw.startswith(auth.TOKEN_PREFIX):
            try:
                with connect(retries=1) as conn:
                    principal = auth.token_principal(conn, raw)
            except (psycopg.Error, RuntimeError) as exc:
                raise _db_unavailable() from exc
            if principal:
                return principal
        _record_failure(client)
        raise HTTPException(401, "invalid credentials")
    cookie = request.cookies.get(COOKIE) if allow_cookie else None
    if cookie:
        try:
            with connect(retries=1) as conn:
                ok = auth.session_valid(conn, cookie)
        except (psycopg.Error, RuntimeError) as exc:
            raise _db_unavailable() from exc
        if ok:
            return Principal("session", "full")
    return None


def require(scope: str = "read"):
    """Dependency: the caller must be authenticated with at least `scope` ('read' or 'full')."""

    def dep(request: Request) -> Principal:
        principal = authenticate(request)
        if principal is None:
            raise HTTPException(401, "authentication required")
        if scope == "full" and principal.scope != "full":
            raise HTTPException(403, "this token is read-only")
        return principal

    return dep


class Guard(BaseHTTPMiddleware):
    """MCP bearer auth (route dependencies don't apply to mounts), CSRF check, and security headers."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/mcp"):
            try:
                principal = authenticate(request, allow_cookie=False)  # MCP never accepts cookies
            except HTTPException as exc:
                return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
            if principal is None:
                return JSONResponse({"error": "authentication required"}, status_code=401)
        elif (path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}
              and _bearer(request) is None and not request.headers.get(CSRF_HEADER)):
            # Cookie-authenticated (browser) writes must carry a custom header, which cross-site pages can't set.
            return JSONResponse({"detail": "missing CSRF header"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        return resp


# Interactive API docs load scripts from a CDN and expose the schema; keep them off.
app = FastAPI(title="TrustDesk", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(Guard)
app.mount("/mcp", mcp_app)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
def readyz():
    try:
        with connect(retries=1) as conn:
            conn.execute("SELECT 1")
        return {"status": "ready"}
    except Exception:  # noqa: BLE001 - database may be waking; never echo connection details
        return JSONResponse({"status": "waking-database"}, status_code=503)


@app.get("/api/search", dependencies=[Depends(require("read"))])
def search(q: str = Query(min_length=1, max_length=MAX_QUERY), limit: int = Query(5, ge=1, le=20),
           doc_type: str | None = Query(None, max_length=50)):
    return {"query": q, "results": hybrid_search(q, limit=limit, doc_type=doc_type)}


@app.post("/api/documents", dependencies=[Depends(require("full"))])
async def upload_document(file: UploadFile = File(...), doc_type: str = Query("policy", max_length=50)):
    data = await file.read(MAX_UPLOAD + 1)  # bounded read: never buffer more than the limit
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large (10 MB limit)")
    name = safe_source_name(file.filename or "upload.txt")
    try:
        text = parse_bytes(name, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - malformed PDFs raise many pypdf-specific errors
        raise HTTPException(422, "could not parse file") from exc
    return ingest_text(title=name.rsplit(".", 1)[0], source=name, text=text, doc_type=doc_type)


@app.get("/api/documents", dependencies=[Depends(require("read"))])
def list_documents():
    with connect() as conn:
        return conn.execute(
            "SELECT d.id, d.title, d.source, d.doc_type, d.version, d.created_at, count(c.id) AS chunks "
            "FROM kb_document d LEFT JOIN kb_chunk c ON c.doc_id=d.id GROUP BY d.id ORDER BY d.id"
        ).fetchall()


@app.get("/api/documents/{doc_id}", dependencies=[Depends(require("read"))])
def get_document(doc_id: int):
    with connect() as conn:
        doc = conn.execute(
            "SELECT id, title, source, doc_type, version, created_at FROM kb_document WHERE id=%s", (doc_id,)
        ).fetchone()
        if not doc:
            raise HTTPException(404, "document not found")
        doc["chunks"] = conn.execute(
            "SELECT id, ord, heading, body FROM kb_chunk WHERE doc_id=%s ORDER BY ord", (doc_id,)
        ).fetchall()
    return doc


@app.delete("/api/documents/{doc_id}", dependencies=[Depends(require("full"))])
def delete_document(doc_id: int):
    with connect() as conn:
        row = conn.execute("DELETE FROM kb_document WHERE id=%s RETURNING id", (doc_id,)).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "document not found")
    return {"deleted": doc_id}


@app.post("/api/seed", dependencies=[Depends(require("full"))])
def load_demo_pack():
    """Load the packaged fictional demo policies. Idempotent: unchanged files are skipped."""
    from .db import connect_vector

    results = []
    with connect_vector() as conn:
        for f in sorted(SEED_PACK.glob("*.md")):
            r = ingest_file(f, conn=conn)
            results.append({"source": f.name, "status": r["status"], "chunks": r["chunks"]})
    return {"results": results}


@app.get("/api/stats", dependencies=[Depends(require("read"))])
def stats():
    s = get_settings()
    with connect() as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM kb_document) AS documents, (SELECT count(*) FROM kb_chunk) AS chunks"
        ).fetchone()
    row["embedder"] = s.embedding_model if s.llm_base_url else "builtin-hash"
    row["semantic"] = bool(s.llm_base_url)
    return row


# --- session, setup, login, tokens ----------------------------------------------------------

class SetupBody(BaseModel):
    code: str = Field(max_length=64)
    password: str = Field(max_length=256)


class LoginBody(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    api_key: str | None = Field(default=None, max_length=512)


class UnlockBody(BaseModel):
    password: str = Field(max_length=256)


class PasswordBody(BaseModel):
    current: str | None = Field(default=None, max_length=256)
    new: str = Field(max_length=256)


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: str = Field(pattern="^(read|full)$")


def _start_session(conn, request: Request, response: Response) -> None:
    raw = auth.create_session(conn)
    response.set_cookie(COOKIE, raw, httponly=True, samesite="strict", secure=_is_https(request),
                        max_age=auth.SESSION_HOURS * 3600, path="/")


def _guard_attempts(request: Request) -> str:
    client = _client(request)
    if _rate_limited(client):
        raise HTTPException(429, "too many failed attempts; wait a minute")
    return client


@app.get("/api/session")
def session_state(request: Request):
    """Public: tells the UI which screen to show. Reveals no secrets."""
    global _auto_unlock_tried
    principal = None
    try:
        principal = authenticate(request)
        with connect(retries=1) as conn:
            if not _auto_unlock_tried:
                _auto_unlock_tried = True
                vault.try_unlock_with_platform_secret(conn)
            setup = auth.needs_setup(conn) and not get_settings().allow_anonymous
            v = vault.status(conn)
            has_password = auth.admin_configured(conn)
    except HTTPException:
        raise
    except (psycopg.Error, RuntimeError) as exc:
        raise _db_unavailable() from exc
    if setup:
        _announce_setup_code()
    state = "setup_required" if setup else "logged_in" if principal else "logged_out"
    return {"state": state, "vault": v, "has_admin_password": has_password,
            "password_login": has_password, "key_login": auth.static_key_configured()}


@app.post("/api/setup")
def setup(body: SetupBody, request: Request, response: Response):
    client = _guard_attempts(request)
    try:
        with connect(retries=1) as conn:
            try:
                auth.complete_setup(conn, body.code, body.password)
            except PermissionError as exc:
                _record_failure(client)
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            _start_session(conn, request, response)
    except (psycopg.Error, RuntimeError) as exc:
        raise _db_unavailable() from exc
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    client = _guard_attempts(request)
    try:
        with connect(retries=1) as conn:
            ok = False
            if body.password is not None:
                ok = auth.login_password(conn, body.password)
            elif body.api_key is not None and auth.static_key_matches(body.api_key):
                ok = True
                vault.try_unlock_with_platform_secret(conn)
            if not ok:
                _record_failure(client)
                raise HTTPException(401, "invalid credentials")
            _start_session(conn, request, response)
    except (psycopg.Error, RuntimeError) as exc:
        raise _db_unavailable() from exc
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    try:
        with connect(retries=1) as conn:
            auth.delete_session(conn, request.cookies.get(COOKIE))
    except (psycopg.Error, RuntimeError):
        pass  # the cookie is cleared regardless; the session row expires on its own
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.post("/api/vault/unlock", dependencies=[Depends(require("full"))])
def unlock_vault(body: UnlockBody, request: Request):
    client = _guard_attempts(request)
    with connect() as conn:
        if not vault.is_initialized(conn):
            raise HTTPException(409, "no provider keys are stored yet; set an admin password first")
        if not vault.unlock_with_password(conn, body.password):
            _record_failure(client)
            raise HTTPException(401, "wrong password")
        conn.commit()
    return {"ok": True}


@app.post("/api/admin-password", dependencies=[Depends(require("full"))])
def admin_password(body: PasswordBody, request: Request):
    """Set the admin password (first time, when signed in with a static key) or change it."""
    client = _guard_attempts(request)
    with connect() as conn:
        try:
            if auth.admin_configured(conn):
                if body.current is None:
                    raise HTTPException(422, "current password is required")
                auth.change_password(conn, body.current, body.new)
            else:
                auth.set_admin_password(conn, body.new)
        except PermissionError as exc:
            _record_failure(client)
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        conn.execute("DELETE FROM web_session")  # sign every browser out after a password change
        conn.commit()
    return {"ok": True, "signed_out_everywhere": True}


@app.get("/api/tokens", dependencies=[Depends(require("full"))])
def list_tokens():
    with connect() as conn:
        return auth.list_tokens(conn)


@app.post("/api/tokens", dependencies=[Depends(require("full"))])
def create_token(body: TokenBody):
    """The token value is returned exactly once; only its hash is stored."""
    with connect() as conn:
        token_id, raw = auth.create_token(conn, body.name.strip(), body.scope)
    return {"id": token_id, "token": raw, "scope": body.scope, "name": body.name.strip()}


@app.delete("/api/tokens/{token_id}", dependencies=[Depends(require("full"))])
def revoke_token(token_id: int):
    with connect() as conn:
        if not auth.revoke_token(conn, token_id):
            raise HTTPException(404, "token not found or already revoked")
    return {"revoked": token_id}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
