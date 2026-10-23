"""Tiny plain-SQL migration runner (just Postgres: no ORM, no proprietary tooling)."""
from pathlib import Path

from pgvector import Vector

from .config import get_settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def run() -> list[str]:
    """Apply the SQL migrations. The shared tables (settings, sessions, API tokens, OIDC state) come from the kit."""
    from apgkit import migrate as kit_migrate
    from apgkit.auth.schema import SQL as AUTH_SQL

    from .core import db

    applied = kit_migrate.run(db, MIGRATIONS, extensions=("vector",), replacements={"{{EMBEDDING_DIM}}": str(get_settings().embedding_dim)})
    with db.connect() as conn:      # after the app's own migrations: it upgrades tables they created (sessions) and adds the OIDC state
        conn.execute(AUTH_SQL)
        conn.commit()
    return applied


MAX_HNSW_DIM = 2000  # pgvector's limit for HNSW/IVFFlat indexes on the `vector` type


def column_dim(conn) -> int | None:
    """Vector dimension of kb_chunk.embedding, or None if the table does not exist yet."""
    row = conn.execute(
        "SELECT a.atttypmod AS dim FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "WHERE c.relname = 'kb_chunk' AND a.attname = 'embedding' AND c.relnamespace = 'public'::regnamespace "
        "AND NOT a.attisdropped"
    ).fetchone()
    return row["dim"] if row else None


def reembed(cfg=None, batch: int = 64, progress=print, on_count=None) -> dict:
    """Re-embed every chunk with `cfg` (default: the active configuration), changing the column dimension if needed.

    Run this after changing the embedding provider, model or dimension. Search is unavailable while it runs.
    Everything happens in one transaction, so a failure leaves the old data intact.
    """
    from . import embedcfg
    from .db import connect_vector
    from .embeddings import embed

    with connect_vector() as conn:
        if cfg is None:
            embedcfg.ensure_loaded(conn)
            cfg = embedcfg.current()
        if cfg.dim > MAX_HNSW_DIM:
            raise ValueError(
                f"{cfg.dim} dimensions exceeds the {MAX_HNSW_DIM}-dimension limit for indexed vectors; "
                "pick a smaller size (most providers let you request fewer dimensions)"
            )
        current = column_dim(conn)
        if current is None:
            raise RuntimeError("tables not found; run `trustdesk migrate` first")
        embed(["connection test"], cfg)  # fail fast, before anything is altered
        total = conn.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"]
        conn.execute("DROP INDEX IF EXISTS kb_chunk_embedding_idx")
        if current != cfg.dim:
            progress(f"changing vector size {current} -> {cfg.dim}")
            conn.execute("ALTER TABLE kb_chunk ALTER COLUMN embedding DROP NOT NULL")
            conn.execute(f"ALTER TABLE kb_chunk ALTER COLUMN embedding TYPE vector({int(cfg.dim)}) USING NULL")
        done = 0
        last = 0
        if on_count:
            on_count(0, total)
        while True:
            rows = conn.execute(
                "SELECT id, heading, body FROM kb_chunk WHERE id > %s ORDER BY id LIMIT %s", (last, batch)
            ).fetchall()
            if not rows:
                break
            vectors = embed([f"{r['heading']}\n{r['body']}" for r in rows], cfg)
            with conn.cursor() as cur:
                cur.executemany("UPDATE kb_chunk SET embedding = %s WHERE id = %s",
                                [(Vector(v), r["id"]) for r, v in zip(rows, vectors, strict=True)])
            last = rows[-1]["id"]
            done += len(rows)
            progress(f"re-embedded {done}/{total}")
            if on_count:
                on_count(done, total)
        conn.execute("ALTER TABLE kb_chunk ALTER COLUMN embedding SET NOT NULL")
        conn.execute("CREATE INDEX kb_chunk_embedding_idx ON kb_chunk USING hnsw (embedding vector_cosine_ops)")
        conn.execute(
            "INSERT INTO kb_embedding_model (id, name, dim) VALUES (1,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, dim=EXCLUDED.dim",
            (cfg.label, cfg.dim),
        )
        conn.commit()
    return {"chunks": done, "dimension": cfg.dim, "previous_dimension": current}


# --- startup reconciliation ---------------------------------------------------------------------------
AUTO_REEMBED_MAX = 5000   # up to this many chunks the container re-embeds by itself at startup
_RECONCILE_LOCK = 7204201


class NeedsReembedding(RuntimeError):
    """The stored vectors have another size than the configured embedder produces: search degrades, uploads are refused."""


_startup: dict = {"message": ""}


def embedding_state(conn, cfg=None) -> dict:
    """Stored vs configured vector size. `needs_reembedding` is what the status API and the UI show."""
    from . import embedcfg

    cfg = cfg or embedcfg.current()
    have = column_dim(conn)
    needs = have is not None and have != cfg.dim
    return {"stored_dimension": have, "configured_dimension": cfg.dim, "needs_reembedding": needs,
            "reason": (_startup["message"] or f"the index stores {have}-dimension vectors but the embedder produces "
                       f"{cfg.dim}") if needs else ""}


def ensure_fits(conn, cfg=None) -> None:
    st = embedding_state(conn, cfg)
    if st["needs_reembedding"]:
        raise NeedsReembedding(
            f"the index stores {st['stored_dimension']}-dimension vectors but the configured embedder produces "
            f"{st['configured_dimension']}: it needs re-embedding (run `trustdesk reembed` or switch embeddings in Settings)")


def reconcile_on_startup(max_chunks: int = AUTO_REEMBED_MAX, progress=print) -> dict:
    """If kb_chunk's vector size differs from the configured one, fix it when the corpus is small, otherwise say so.

    Never raises: a provider failure, an unindexable size or a big corpus is logged as one clear line and the app comes
    up regardless (search falls back to full-text, uploads are refused with 503 until re-embedded). `reembed` is atomic,
    so a failure leaves the old data intact. Returns {"action": none|reembedded|too_large|refused|failed, ...}."""
    from . import embedcfg
    from .db import connect_vector

    try:
        with connect_vector() as conn:
            embedcfg.ensure_loaded(conn)
            cfg = embedcfg.current()
            have = column_dim(conn)
            if have is None or have == cfg.dim:
                _startup["message"] = ""
                return {"action": "none", "stored": have, "configured": cfg.dim}
            chunks = conn.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"]
            conn.rollback()
            head = f"the index stores {have}-dimension vectors but the configured embedder uses {cfg.dim}"
            if cfg.dim > MAX_HNSW_DIM:
                msg = (f"{head}; {cfg.dim} exceeds the {MAX_HNSW_DIM}-dimension limit for indexed vectors, so it cannot be "
                       f"applied. Set EMBEDDING_DIM to {have} or a size up to {MAX_HNSW_DIM}.")
                _startup["message"] = msg
                progress(f"WARNING: {msg}")
                return {"action": "refused", "stored": have, "configured": cfg.dim, "message": msg}
            if chunks > max_chunks:
                msg = (f"{head}; {chunks} chunks is more than the automatic limit of {max_chunks}. Search uses full-text only "
                       "and uploads are refused until you run `trustdesk reembed` (or switch embeddings in Settings).")
                _startup["message"] = msg
                progress(f"WARNING: {msg}")
                return {"action": "too_large", "stored": have, "configured": cfg.dim, "chunks": chunks, "message": msg}
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_RECONCILE_LOCK,)).fetchone()["ok"]:
                return {"action": "none", "stored": have, "configured": cfg.dim, "message": "another instance is re-embedding"}
            try:
                progress(f"{head}: re-embedding {chunks} chunks")
                r = reembed(cfg=cfg, progress=lambda m: None)
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (_RECONCILE_LOCK,))
                conn.commit()
        _startup["message"] = ""
        progress(f"re-embedded {r['chunks']} chunks: vector size {r['previous_dimension']} -> {r['dimension']}")
        return {"action": "reembedded", **r}
    except Exception as exc:  # noqa: BLE001 - startup must survive provider, network and index problems
        msg = (f"automatic re-embedding failed, nothing was changed ({type(exc).__name__}: {str(exc)[:200]}). Search uses "
               "full-text only and uploads are refused until `trustdesk reembed` succeeds.")
        _startup["message"] = msg
        progress(f"WARNING: {msg}")
        return {"action": "failed", "message": msg}
