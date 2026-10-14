"""Tiny plain-SQL migration runner (just Postgres: no ORM, no proprietary tooling)."""
from pathlib import Path

from pgvector import Vector

from .config import get_settings
from .db import connect

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def run() -> list[str]:
    dim = get_settings().embedding_dim
    applied: list[str] = []
    with connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            sql = path.read_text().replace("{{EMBEDDING_DIM}}", str(dim))
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
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


def reembed(batch: int = 64, progress=print) -> dict:
    """Re-embed every chunk with the current model, changing the column dimension if needed.

    Run this after changing LLM_BASE_URL / EMBEDDING_MODEL / EMBEDDING_DIM. Search is unavailable
    while it runs. Everything happens in one transaction, so a failure leaves the old data intact.
    """
    from .embeddings import embed

    s = get_settings()
    if s.embedding_dim > MAX_HNSW_DIM:
        raise ValueError(
            f"EMBEDDING_DIM={s.embedding_dim} exceeds the {MAX_HNSW_DIM}-dimension limit for indexed vectors; "
            "pick a smaller size (most providers let you request fewer dimensions)"
        )
    from .db import connect_vector

    with connect_vector() as conn:
        current = column_dim(conn)
        if current is None:
            raise RuntimeError("tables not found; run `trustdesk migrate` first")
        total = conn.execute("SELECT count(*) AS n FROM kb_chunk").fetchone()["n"]
        conn.execute("DROP INDEX IF EXISTS kb_chunk_embedding_idx")
        if current != s.embedding_dim:
            progress(f"changing vector size {current} -> {s.embedding_dim}")
            conn.execute("ALTER TABLE kb_chunk ALTER COLUMN embedding DROP NOT NULL")
            conn.execute(f"ALTER TABLE kb_chunk ALTER COLUMN embedding TYPE vector({int(s.embedding_dim)}) USING NULL")
        done = 0
        last = 0
        while True:
            rows = conn.execute(
                "SELECT id, heading, body FROM kb_chunk WHERE id > %s ORDER BY id LIMIT %s", (last, batch)
            ).fetchall()
            if not rows:
                break
            vectors = embed([f"{r['heading']}\n{r['body']}" for r in rows])
            with conn.cursor() as cur:
                cur.executemany("UPDATE kb_chunk SET embedding = %s WHERE id = %s",
                                [(Vector(v), r["id"]) for r, v in zip(rows, vectors, strict=True)])
            last = rows[-1]["id"]
            done += len(rows)
            progress(f"re-embedded {done}/{total}")
        conn.execute("ALTER TABLE kb_chunk ALTER COLUMN embedding SET NOT NULL")
        conn.execute("CREATE INDEX kb_chunk_embedding_idx ON kb_chunk USING hnsw (embedding vector_cosine_ops)")
        conn.execute(
            "INSERT INTO kb_embedding_model (id, name, dim) VALUES (1,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, dim=EXCLUDED.dim",
            (s.embedding_model if s.llm_base_url else "builtin-hash", s.embedding_dim),
        )
        conn.commit()
    return {"chunks": done, "dimension": s.embedding_dim, "previous_dimension": current}
