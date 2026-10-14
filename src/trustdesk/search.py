"""Hybrid retrieval: exact filters -> vector + lexical in parallel -> reciprocal rank fusion.

Results always carry provenance (document, version, heading, per-signal ranks) so answers can cite.
"""
from pgvector import Vector

from . import embedcfg
from .db import connect_vector
from .embeddings import embed

RRF_K = 60

_SQL = """
WITH vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qv)s) AS rnk
    FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id
    WHERE c.org_id = %(org)s AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
    ORDER BY c.embedding <=> %(qv)s
    LIMIT %(k)s
), tq AS (  -- OR semantics: questions are long, passages short; ranking favours more matching terms
    SELECT replace(plainto_tsquery('english', %(q)s)::text, '&', '|')::tsquery AS q
), lex AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, tq.q) DESC) AS rnk
    FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id CROSS JOIN tq
    WHERE c.org_id = %(org)s AND c.tsv @@ tq.q
      AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
    ORDER BY ts_rank_cd(c.tsv, tq.q) DESC
    LIMIT %(k)s
), fused AS (
    SELECT id, sum(1.0 / (%(rrf)s + rnk)) AS score,
           max(rnk) FILTER (WHERE src = 'vec') AS vec_rank,
           max(rnk) FILTER (WHERE src = 'lex') AS lex_rank
    FROM (SELECT id, rnk, 'vec' AS src FROM vec UNION ALL SELECT id, rnk, 'lex' FROM lex) u
    GROUP BY id
)
SELECT c.id AS chunk_id, d.id AS doc_id, d.title, d.source, d.version, d.doc_type,
       c.heading, c.body, f.score, f.vec_rank, f.lex_rank
FROM fused f JOIN kb_chunk c ON c.id = f.id JOIN kb_document d ON d.id = c.doc_id
ORDER BY f.score DESC
LIMIT %(limit)s
"""


def hybrid_search(query: str, *, org_id: str = "default", doc_type: str | None = None,
                  limit: int = 5, candidates: int = 30, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect_vector()
    try:
        embedcfg.ensure_loaded(conn)
        qv = embed([query])[0]
        rows = conn.execute(
            _SQL,
            {"qv": Vector(qv), "q": query, "org": org_id, "doc_type": doc_type, "k": candidates,
             "rrf": RRF_K, "limit": limit},
        ).fetchall()
    finally:
        if own:
            conn.close()
    for r in rows:
        r["score"] = float(r["score"])
    return rows
