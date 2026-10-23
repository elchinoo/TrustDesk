import contextlib
import sys
import time
from pathlib import Path

import psycopg
from apgkit.auth.rp import safe_return_to
from apgkit.config import env_bool
from apgkit.guard import SecurityGuard
from apgkit.web import is_https, mount_kit_static, public_url
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, accounts, api_v1, core, embedcfg, metrics, reembed_job, seedpack, vault
from . import ask as asker
from .core import COOKIE, CSRF_HEADER, auth, fed, local
from .db import connect, connect_vector
from .embeddings import embed as embed_texts
from .ingest import ingest_text, parse_bytes, safe_source_name
from .llm_http import LLMError
from .mcp_server import mcp
from .migrate import NeedsReembedding, embedding_state
from .netguard import UnsafeURL
from .search import hybrid_search

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_QUERY = 500

mcp_app = mcp.streamable_http_app(streamable_http_path="/", host="0.0.0.0")


_announced = False
_auto_unlock_tried = False


def _announce_setup_code() -> None:
    """Print the one-time setup code to the server log (once per process)."""
    global _announced
    if not _announced:
        _announced = True
        print("\n=== TrustDesk first-run setup ===\n"
              f"Open the app in your browser and enter this one-time code to create the admin password:\n"
              f"    {local.setup_code()}\n"
              "(It changes every time the server restarts until setup is complete.)\n", file=sys.stderr, flush=True)


def _startup_tasks() -> None:
    """Best effort: unlock the vault with a platform secret and announce the setup code. Never blocks startup."""
    global _auto_unlock_tried
    try:
        with connect(retries=1) as conn:
            _auto_unlock_tried = True
            if fed.enabled and not vault.is_initialized(conn):
                core.vault.initialize_with_platform_secret(conn)   # single sign-on only: no admin password to wrap the key with
                conn.commit()
            vault.try_unlock_with_platform_secret(conn)
            if local.needs_setup(conn) and not fed.enabled:
                _announce_setup_code()
    except Exception:  # noqa: BLE001 - database may be asleep; it is retried on the first request
        pass


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    _startup_tasks()
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


# --- authentication ---------------------------------------------------------------------------------------
# Fail closed: with no credential configured every protected route is denied (and the setup flow is offered), unless
# the operator explicitly sets TRUSTDESK_ALLOW_ANONYMOUS=true. The mechanics (local admin, API tokens, federated JWTs,
# sessions, rate limiting) are agentpg-kit's `Authenticator`; here we only name the permission each route needs.

_LEGACY = {"read": "kb:read", "full": "settings:manage", "metrics": "metrics:scrape", "client": "requests:client"}


def authenticate(request: Request, *, allow_cookie: bool = True):
    return auth.authenticate(request, allow_cookie=allow_cookie)


def require(*perms: str):
    """Dependency factory. Each argument is a permission (`requests:decide`) or one of the old scope names (read, full, metrics,
    client); the caller needs ANY of them. With no argument: `kb:read`."""
    wanted = [_LEGACY.get(p, p) for p in (perms or ("kb:read",))]
    if len(wanted) == 1:
        return auth.require(wanted[0])

    def dep(request: Request):
        p = auth.authenticate(request)
        if p is None:
            raise HTTPException(401, "authentication required")
        if not any(p.can(w) for w in wanted):
            raise HTTPException(403, f"this credential cannot do that (it needs one of: {', '.join(wanted)})")
        return p
    return dep


def _client(request: Request) -> str:
    return auth.client(request)


def _db_unavailable() -> HTTPException:
    return auth.db_unavailable()


class McpGuard:
    """MCP never accepts browser cookies; the credential must carry `mcp:use` (a read or full token, or a federated user or
    service allowed to). Scrape-only and client-application credentials are refused."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)
        if scope["path"] == "/mcp":
            # Serve the bare path directly: the mount would answer with a redirect, and behind a TLS-terminating proxy that
            # redirect can point at http://, where clients (rightly) drop the Authorization header.
            scope["path"] = scope["raw_path"] = "/mcp/"
        request = Request(scope, receive)
        try:
            principal = auth.authenticate(request, allow_cookie=False)
        except HTTPException as exc:
            return await JSONResponse({"error": exc.detail}, status_code=exc.status_code)(scope, receive, send)
        if principal is None:
            return await JSONResponse({"error": "authentication required"}, status_code=401)(scope, receive, send)
        if not principal.can("mcp:use"):
            return await JSONResponse({"error": "this credential cannot use MCP"}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


# Interactive API docs load scripts from a CDN and expose the schema; keep them off.
app = FastAPI(title="TrustDesk", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(McpGuard)
app.add_middleware(SecurityGuard, csrf_header=CSRF_HEADER, csrf_prefixes=("/api/",), body_prefixes=(), observe=core.metrics.observe)
app.mount("/mcp", mcp_app)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
mount_kit_static(app)



@contextlib.contextmanager
def _translate():
    """Map domain failures to honest HTTP statuses without leaking internals or secrets."""
    try:
        yield
    except vault.VaultLocked as exc:
        raise HTTPException(423, str(exc)) from exc  # 423 Locked: sign in / unlock to continue
    except vault.VaultError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NeedsReembedding as exc:
        raise HTTPException(503, str(exc)) from exc
    except (accounts.AccountError, UnsafeURL, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


def idle() -> None:
    if reembed_job.running():
        raise HTTPException(503, "re-embedding is in progress; search and uploads are paused until it finishes")


app.include_router(api_v1.build_router(require, idle))


@app.get("/metrics", dependencies=[Depends(require("metrics"))])
def prometheus_metrics():
    body, ctype = metrics.render()
    return Response(body, media_type=ctype)


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


@app.get("/api/search", dependencies=[Depends(require("read")), Depends(idle)])
def search(q: str = Query(min_length=1, max_length=MAX_QUERY), limit: int = Query(5, ge=1, le=20),
           doc_type: str | None = Query(None, max_length=50)):
    with _translate():
        metrics.SEARCHES.labels("search").inc()
        return {"query": q, "results": hybrid_search(q, limit=limit, doc_type=doc_type)}


@app.post("/api/documents", dependencies=[Depends(require("full")), Depends(idle)])
async def upload_document(file: UploadFile = File(...), doc_type: str = Query("policy", pattern="^[a-z][a-z0-9_-]{0,29}$")):
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
    with _translate():
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


@app.post("/api/seed", dependencies=[Depends(require("full")), Depends(idle)])
def load_demo_pack():
    """Load the packaged fictional demo policies. Idempotent: unchanged files are skipped."""
    with _translate(), connect_vector() as conn:
        return {"results": seedpack.load(conn)}


@app.get("/api/stats", dependencies=[Depends(require("read"))])
def stats():
    with connect() as conn:
        embedcfg.ensure_loaded(conn)
        row = conn.execute(
            "SELECT (SELECT count(*) FROM kb_document) AS documents, (SELECT count(*) FROM kb_chunk) AS chunks"
        ).fetchone()
        state = embedding_state(conn)
    cfg = embedcfg.current()
    row.update(embedder=cfg.label, semantic=cfg.semantic, dimension=cfg.dim, reembedding=reembed_job.running(),
               needs_reembedding=state["needs_reembedding"], stored_dimension=state["stored_dimension"])
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
    scope: str = Field(pattern="^(read|metrics|client|full)$")


def _start_session(conn, request: Request, response: Response, **who) -> None:
    raw = local.create_session(conn, **who)
    response.set_cookie(COOKIE, raw, httponly=True, samesite="strict" if not who else "lax", secure=is_https(request),
                        max_age=12 * 3600, path="/")


def _guard_attempts(request: Request) -> str:
    client = auth.client(request)
    if auth.limiter.limited(client):
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
            setup = local.needs_setup(conn) and not env_bool("TRUSTDESK_ALLOW_ANONYMOUS") and not fed.enabled
            v = vault.status(conn)
            has_password = local.admin_configured(conn)
    except HTTPException:
        raise
    except (psycopg.Error, RuntimeError) as exc:
        raise _db_unavailable() from exc
    if setup:
        _announce_setup_code()
    state = "setup_required" if setup else "logged_in" if principal else "logged_out"
    out = {"state": state, "vault": v, "has_admin_password": has_password,
           "password_login": has_password and (fed.local_login or not fed.enabled), "key_login": local.static_key_configured() and (fed.local_login or not fed.enabled),
           "sso": {"issuer": fed.issuer, "login_url": "/auth/login"} if fed.enabled else None}
    if principal:
        out["user"] = {"name": principal.name, "email": principal.email, "source": principal.source, "roles": sorted(principal.roles),
                       "permissions": ["*"] if "*" in principal.permissions else sorted(principal.permissions)}
    return out


# ---- federated sign-in (only when TRUSTDESK_OIDC_ISSUER is set) ----------------------------------------------
@app.get("/auth/login")
def sso_login(request: Request, next: str | None = None):
    if not fed.enabled:
        raise HTTPException(404, "single sign-on is not configured")
    try:
        with connect(retries=1) as conn:
            url = fed.rp.begin(conn, public_url(request, core.public_url_env) + "/auth/callback", next)
    except psycopg.Error as exc:
        raise _db_unavailable() from exc
    except Exception:  # noqa: BLE001 - the identity provider may be unreachable
        return RedirectResponse("/?sso_error=the identity provider is unavailable", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/auth/callback")
def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if not fed.enabled:
        raise HTTPException(404, "single sign-on is not configured")
    if error or not code or not state:
        return RedirectResponse("/?sso_error=the sign-in was cancelled or failed", status_code=303)
    from apgkit.auth import TokenError
    try:
        with connect(retries=1) as conn:
            claims, back, id_token = fed.rp.finish(conn, public_url(request, core.public_url_env) + "/auth/callback", code, state)
            roles = [r for r in claims.get("roles", []) if isinstance(r, str)]
            resp = RedirectResponse(safe_return_to(back), status_code=303)
            principal_roles = core.APP_AUTH.permissions_for(roles=roles)
            if not principal_roles:
                return RedirectResponse("/?sso_error=your account has no role in TrustDesk; ask an administrator", status_code=303)
            _start_session(conn, request, resp, subject=str(claims["sub"]), name=str(claims.get("name") or ""), email=str(claims.get("email") or ""),
                           roles=roles, source="oidc", id_token_hint=id_token)
            return resp
    except TokenError as exc:
        return RedirectResponse("/?sso_error=" + str(exc)[:120], status_code=303)


@app.post("/api/setup")
def setup(body: SetupBody, request: Request, response: Response):
    client = _guard_attempts(request)
    try:
        with connect(retries=1) as conn:
            try:
                local.complete_setup(conn, body.code, body.password)
            except PermissionError as exc:
                auth.limiter.record(client)
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
                ok = local.login_password(conn, body.password)
            elif body.api_key is not None and local.static_key_matches(body.api_key):
                ok = True
                vault.try_unlock_with_platform_secret(conn)
            if not ok:
                auth.limiter.record(client)
                raise HTTPException(401, "invalid credentials")
            _start_session(conn, request, response)
    except (psycopg.Error, RuntimeError) as exc:
        raise _db_unavailable() from exc
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    out: dict = {"ok": True}
    try:
        with connect(retries=1) as conn:
            row = local.delete_session(conn, request.cookies.get(COOKIE))
        if row and row["source"] == "oidc" and fed.enabled:
            with contextlib.suppress(Exception):
                out["logout_url"] = fed.rp.logout_url(row["id_token_hint"], public_url(request, core.public_url_env) + "/")
    except (psycopg.Error, RuntimeError):
        pass  # the cookie is cleared regardless; the session row expires on its own
    response.delete_cookie(COOKIE, path="/")
    return out


@app.post("/api/vault/unlock", dependencies=[Depends(require("full"))])
def unlock_vault(body: UnlockBody, request: Request):
    client = _guard_attempts(request)
    with connect() as conn:
        if not vault.is_initialized(conn):
            raise HTTPException(409, "no provider keys are stored yet; set an admin password first")
        if not vault.unlock_with_password(conn, body.password):
            auth.limiter.record(client)
            raise HTTPException(403, "wrong password")  # 403, not 401: the session itself is still valid
        conn.commit()
    return {"ok": True}


@app.post("/api/admin-password", dependencies=[Depends(require("full"))])
def admin_password(body: PasswordBody, request: Request):
    """Set the admin password (first time, when signed in with a static key) or change it."""
    client = _guard_attempts(request)
    with connect() as conn:
        try:
            if local.admin_configured(conn):
                if body.current is None:
                    raise HTTPException(422, "current password is required")
                local.change_password(conn, body.current, body.new)
            else:
                local.set_admin_password(conn, body.new)
        except PermissionError as exc:
            auth.limiter.record(client)
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        conn.execute("DELETE FROM web_session")  # sign every browser out after a password change
        conn.commit()
    return {"ok": True, "signed_out_everywhere": True}


@app.get("/api/tokens", dependencies=[Depends(require("full"))])
def list_tokens():
    with connect() as conn:
        return local.list_tokens(conn)


@app.post("/api/tokens", dependencies=[Depends(require("full"))])
def create_token(body: TokenBody):
    """The token value is returned exactly once; only its hash is stored."""
    with connect() as conn:
        token_id, raw = local.create_token(conn, body.name.strip(), body.scope)
    return {"id": token_id, "token": raw, "scope": body.scope, "name": body.name.strip()}


@app.delete("/api/tokens/{token_id}", dependencies=[Depends(require("full"))])
def revoke_token(token_id: int):
    with connect() as conn:
        if not local.revoke_token(conn, token_id):
            raise HTTPException(404, "token not found or already revoked")
    return {"revoked": token_id}


# --- provider accounts, embeddings, ask ------------------------------------------------------------

class AccountBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    provider: str = Field(default="custom", max_length=20)
    base_url: str = Field(max_length=300)
    api_key: str | None = Field(default=None, max_length=512)
    clear_key: bool = False
    chat_model: str | None = Field(default=None, max_length=120)
    embedding_model: str | None = Field(default=None, max_length=120)
    embedding_dim: int | None = Field(default=None, ge=8, le=2000)


class TestBody(BaseModel):
    target: str = Field(pattern="^(chat|embeddings)$")


class ActivateBody(BaseModel):
    account: str = Field(pattern=r"^(env|\d{1,12})$")


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    account: str = Field(pattern=r"^(env|\d{1,12})$")
    limit: int = Field(default=6, ge=1, le=10)


def _ref(value: str) -> int | str:
    return value if value == "env" else int(value)


@app.get("/api/providers", dependencies=[Depends(require("read"))])
def providers():
    return accounts.PRESETS


@app.get("/api/accounts", dependencies=[Depends(require("full"))])
def list_accounts():
    with connect() as conn:
        embedcfg.ensure_loaded(conn)
        return {"accounts": accounts.list_accounts(conn), "env": accounts.env_account(),
                "vault": vault.status(conn), "active_embedding": accounts.active_embedding_ref(conn)}


@app.post("/api/accounts", dependencies=[Depends(require("full"))])
def create_account(body: AccountBody):
    with _translate(), connect() as conn:
        new_id = accounts.create(conn, name=body.name, provider=body.provider, base_url=body.base_url,
                                 api_key=body.api_key, chat_model=body.chat_model,
                                 embedding_model=body.embedding_model, embedding_dim=body.embedding_dim)
        return accounts.get_account(conn, new_id)


@app.put("/api/accounts/{account_id}", dependencies=[Depends(require("full")), Depends(idle)])
def update_account(account_id: int, body: AccountBody):
    with _translate(), connect() as conn:
        accounts.update(conn, account_id, name=body.name, provider=body.provider, base_url=body.base_url,
                        api_key=body.api_key, clear_key=body.clear_key, chat_model=body.chat_model,
                        embedding_model=body.embedding_model, embedding_dim=body.embedding_dim)
        return accounts.get_account(conn, account_id)


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(require("full"))])
def delete_account(account_id: int):
    with _translate(), connect() as conn:
        embedcfg.ensure_loaded(conn)
        accounts.delete(conn, account_id)
    return {"deleted": account_id}


@app.post("/api/accounts/{account}/test", dependencies=[Depends(require("full"))])
def test_account(account: str, body: TestBody):
    """Make one tiny real call to the provider so mistakes (URL, key, model name) show up immediately."""
    if account != "env" and not account.isdigit():
        raise HTTPException(422, "invalid account")
    started = time.monotonic()
    with _translate(), connect() as conn:
        if body.target == "chat":
            reply = asker.chat(accounts.chat_config_for(conn, _ref(account)),
                               [{"role": "user", "content": "Reply with the single word OK. Keep any reasoning very short."}], max_tokens=64)
            detail = {"reply": reply["text"][:60]}
        else:
            cfg = (embedcfg.from_env() if account == "env" else accounts.embedding_config_for(conn, int(account)))
            vec = embed_texts(["connection test"], cfg)[0]
            detail = {"dimensions": len(vec), "model": cfg.model}
    return {"ok": True, "ms": int((time.monotonic() - started) * 1000), **detail}


@app.get("/api/embeddings/status", dependencies=[Depends(require("read"))])
def embeddings_status():
    with connect() as conn:
        embedcfg.ensure_loaded(conn)
        state = embedding_state(conn)
    cfg = embedcfg.current()
    return {"job": reembed_job.status(), **state,
            "active": {"account": "env" if cfg.account_id is None else cfg.account_id, "model": cfg.label,
                       "dimension": cfg.dim, "semantic": cfg.semantic}}


@app.post("/api/embeddings/activate", dependencies=[Depends(require("full")), Depends(idle)])
def activate_embeddings(body: ActivateBody):
    """Use an account for embeddings. If the model or vector size changes, every chunk is re-embedded in the
    background (progress via /api/embeddings/status); nothing is changed unless the provider call works."""
    with _translate(), connect() as conn:
        embedcfg.ensure_loaded(conn)
        target = embedcfg.from_env() if body.account == "env" else accounts.embedding_config_for(conn, int(body.account))
        current = embedcfg.current()
        embed_texts(["connection test"], target)  # fail fast with a clear message before touching any data
        same = ((target.base_url, target.model, target.dim) == (current.base_url, current.model, current.dim)
                and not embedding_state(conn)["needs_reembedding"])   # a size mismatch always needs the re-embedding
        if same:
            embedcfg.store.put(conn, embedcfg.SETTING, "" if target.account_id is None else str(target.account_id))
            conn.commit()
            embedcfg.set_active(None if target.account_id is None else target)
            return {"started": False, "message": "already using this embedding model; nothing to re-embed"}
    try:
        reembed_job.start(target)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"started": True, "message": "re-embedding started"}


@app.post("/api/ask", dependencies=[Depends(require("full")), Depends(idle)])
def ask_question(body: AskBody):
    with _translate(), connect_vector() as conn:
        cfg = accounts.chat_config_for(conn, _ref(body.account))
        return asker.answer(conn, body.question.strip(), cfg, limit=body.limit)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
