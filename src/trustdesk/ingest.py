"""Ingest documents: parse -> chunk -> embed -> store. Idempotent on (org, source, sha256)."""
import hashlib
import io
from pathlib import Path

from pgvector import Vector

from . import embedcfg
from .chunking import chunk_markdown
from .db import connect_vector
from .embeddings import embed
from .migrate import ensure_fits

MAX_PDF_PAGES = 500


def safe_source_name(name: str) -> str:
    """Basename only, printable, bounded: the name is stored and echoed back, never used as a path."""
    base = Path(name.replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isprintable())[:200].strip()
    return cleaned or "upload.txt"


def parse_bytes(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has more than {MAX_PDF_PAGES} pages")
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    return data.decode("utf-8", errors="replace")


def ingest_text(
    *, title: str, source: str, text: str, org_id: str = "default", doc_type: str = "policy", conn=None, cfg=None
) -> dict:
    sha = hashlib.sha256(text.encode()).hexdigest()
    own = conn is None
    conn = conn or connect_vector()
    try:
        existing = conn.execute(
            "SELECT id FROM kb_document WHERE org_id=%s AND source=%s AND sha256=%s", (org_id, source, sha)
        ).fetchone()
        if existing:
            return {"doc_id": existing["id"], "chunks": 0, "status": "unchanged"}
        prev = conn.execute(
            "SELECT id, version FROM kb_document WHERE org_id=%s AND source=%s ORDER BY version DESC LIMIT 1",
            (org_id, source),
        ).fetchone()
        version = prev["version"] + 1 if prev else 1
        if prev:  # new version supersedes old content in search
            conn.execute("DELETE FROM kb_document WHERE org_id=%s AND source=%s", (org_id, source))
        chunks = chunk_markdown(text)
        if not chunks:
            return {"doc_id": None, "chunks": 0, "status": "empty"}
        embedcfg.ensure_loaded(conn)
        cfg = cfg or embedcfg.current()
        ensure_fits(conn, cfg)  # NeedsReembedding (HTTP 503) instead of a database error on a vector of the wrong size
        vectors = embed([f"{c.heading}\n{c.body}" for c in chunks], cfg)
        doc = conn.execute(
            "INSERT INTO kb_document (org_id, title, source, doc_type, version, sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (org_id, title, source, doc_type, version, sha),
        ).fetchone()
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO kb_chunk (doc_id, org_id, ord, heading, body, embedding) VALUES (%s,%s,%s,%s,%s,%s)",
                [(doc["id"], org_id, i, c.heading, c.body, Vector(v)) for i, (c, v) in enumerate(zip(chunks, vectors))],
            )
        conn.execute(
            "INSERT INTO kb_embedding_model (id, name, dim) VALUES (1,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, dim=EXCLUDED.dim",
            (cfg.label, cfg.dim),
        )
        conn.commit()
        return {"doc_id": doc["id"], "chunks": len(chunks), "status": "ingested", "version": version}
    finally:
        if own:
            conn.close()


def ingest_file(path: Path, org_id: str = "default", doc_type: str = "policy", conn=None, cfg=None) -> dict:
    text = parse_bytes(path.name, path.read_bytes())
    return ingest_text(title=path.stem.replace("-", " ").title(), source=path.name, text=text,
                       org_id=org_id, doc_type=doc_type, conn=conn, cfg=cfg)
