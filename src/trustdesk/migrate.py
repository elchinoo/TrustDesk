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
