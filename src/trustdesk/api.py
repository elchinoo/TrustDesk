import contextlib
import hmac
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
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


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


# --- authentication -------------------------------------------------------------------------
# Fail closed: with no key configured every protected route is denied, unless the operator
# explicitly sets TRUSTDESK_ALLOW_ANONYMOUS=true. `trustdesk serve` generates a key when unset.

_FAIL_WINDOW, _FAIL_LIMIT = 60.0, 10
_failures: dict[str, list[float]] = {}


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(client, []) if now - t < _FAIL_WINDOW]
    _failures[client] = recent
    return len(recent) >= _FAIL_LIMIT


def _record_failure(client: str) -> None:
    _failures.setdefault(client, []).append(time.monotonic())
    if len(_failures) > 10_000:  # bound memory under address churn
        _failures.clear()


def _check(header: str | None, client: str) -> int:
    """Return 200 if authorized, else 401, or 429 when this client keeps failing."""
    s = get_settings()
    if s.allow_anonymous:
        return 200
    if _rate_limited(client):
        return 429
    if s.api_key and header and hmac.compare_digest(header.encode(), f"Bearer {s.api_key}".encode()):
        return 200
    _record_failure(client)
    return 401


def require_key(request: Request, authorization: str | None = Header(default=None)) -> None:
    code = _check(authorization, request.client.host if request.client else "?")
    if code != 200:
        raise HTTPException(code, "unauthorized" if code == 401 else "too many failed attempts")


class Guard(BaseHTTPMiddleware):
    """Bearer auth for the mounted MCP app (route dependencies don't apply to mounts) + security headers."""

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            code = _check(request.headers.get("authorization"), request.client.host if request.client else "?")
            if code != 200:
                return JSONResponse({"error": "unauthorized" if code == 401 else "rate limited"}, status_code=code)
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


@app.get("/api/search", dependencies=[Depends(require_key)])
def search(q: str = Query(min_length=1, max_length=MAX_QUERY), limit: int = Query(5, ge=1, le=20),
           doc_type: str | None = Query(None, max_length=50)):
    return {"query": q, "results": hybrid_search(q, limit=limit, doc_type=doc_type)}


@app.post("/api/documents", dependencies=[Depends(require_key)])
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


@app.get("/api/documents", dependencies=[Depends(require_key)])
def list_documents():
    with connect() as conn:
        return conn.execute(
            "SELECT d.id, d.title, d.source, d.doc_type, d.version, d.created_at, count(c.id) AS chunks "
            "FROM kb_document d LEFT JOIN kb_chunk c ON c.doc_id=d.id GROUP BY d.id ORDER BY d.id"
        ).fetchall()


@app.get("/api/documents/{doc_id}", dependencies=[Depends(require_key)])
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


@app.delete("/api/documents/{doc_id}", dependencies=[Depends(require_key)])
def delete_document(doc_id: int):
    with connect() as conn:
        row = conn.execute("DELETE FROM kb_document WHERE id=%s RETURNING id", (doc_id,)).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "document not found")
    return {"deleted": doc_id}


@app.post("/api/seed", dependencies=[Depends(require_key)])
def load_demo_pack():
    """Load the packaged fictional demo policies. Idempotent: unchanged files are skipped."""
    from .db import connect_vector

    results = []
    with connect_vector() as conn:
        for f in sorted(SEED_PACK.glob("*.md")):
            r = ingest_file(f, conn=conn)
            results.append({"source": f.name, "status": r["status"], "chunks": r["chunks"]})
    return {"results": results}


@app.get("/api/stats", dependencies=[Depends(require_key)])
def stats():
    s = get_settings()
    with connect() as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM kb_document) AS documents, (SELECT count(*) FROM kb_chunk) AS chunks"
        ).fetchone()
    row["embedder"] = s.embedding_model if s.llm_base_url else "builtin-hash"
    row["semantic"] = bool(s.llm_base_url)
    return row


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
