"""Hybrid retrieval: exact filters -> vector + lexical + trigram (typo tolerance) in parallel -> reciprocal rank fusion.

Results always carry provenance (document, version, heading, per-signal ranks) so answers can cite.
"""
from pgvector import Vector

from . import embedcfg, textsearch
from .db import connect_vector
from .embeddings import embed
from .migrate import column_dim

RRF_K = 60

_SQL = """
WITH vec AS (
    __VEC__
), tq AS (  -- OR semantics: questions are long, passages short; ranking favours more matching terms
    SELECT replace(plainto_tsquery(%(cfg)s::regconfig, %(q)s)::text, '&', '|')::tsquery AS q
), lex AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, tq.q) DESC) AS rnk
    FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id CROSS JOIN tq
    WHERE c.org_id = %(org)s AND c.tsv @@ tq.q
      AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
    ORDER BY ts_rank_cd(c.tsv, tq.q) DESC
    LIMIT %(k)s
), trg AS (
    __TRG__
), fused AS (
    SELECT id, sum(1.0 / (%(rrf)s + rnk)) AS score,
           max(rnk) FILTER (WHERE src = 'vec') AS vec_rank,
           max(rnk) FILTER (WHERE src = 'lex') AS lex_rank,
           max(rnk) FILTER (WHERE src = 'trg') AS trgm_rank
    FROM (SELECT id, rnk, 'vec' AS src FROM vec UNION ALL SELECT id, rnk, 'lex' FROM lex
          UNION ALL SELECT id, rnk, 'trg' FROM trg) u
    GROUP BY id
)
SELECT c.id AS chunk_id, d.id AS doc_id, d.title, d.source, d.version, d.doc_type,
       c.heading, c.body, f.score, f.vec_rank, f.lex_rank, f.trgm_rank, t.sim AS trgm_score
FROM fused f JOIN kb_chunk c ON c.id = f.id JOIN kb_document d ON d.id = c.doc_id
     LEFT JOIN trg t ON t.id = f.id
ORDER BY f.score DESC
LIMIT %(limit)s
"""


_VEC = """SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qv)s) AS rnk
    FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id
    WHERE c.org_id = %(org)s AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
    ORDER BY c.embedding <=> %(qv)s
    LIMIT %(k)s"""
# Typo tolerance: word_similarity of the folded (lower-case, accent-free) query against the chunk text and the document title, the
# same expressions as the GIN indexes of migration 007. `<%%` is the index-assisted test; the HAVING is the exact bar.
_TRG = """SELECT id, row_number() OVER (ORDER BY sim DESC, id) AS rnk, sim FROM (
        SELECT id, max(sim) AS sim FROM (
            SELECT c.id, word_similarity(public.td_fold(%(q)s), public.td_fold(c.heading || ' ' || c.body)) AS sim
            FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id
            WHERE c.org_id = %(org)s AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
              AND public.td_fold(%(q)s) <%% public.td_fold(c.heading || ' ' || c.body)
            UNION ALL
            SELECT c.id, word_similarity(public.td_fold(%(q)s), public.td_fold(d.title)) AS sim
            FROM kb_chunk c JOIN kb_document d ON d.id = c.doc_id
            WHERE c.org_id = %(org)s AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
              AND public.td_fold(%(q)s) <%% public.td_fold(d.title)
        ) m GROUP BY id HAVING max(sim) >= %(trgm)s ORDER BY max(sim) DESC LIMIT %(k)s
    ) t"""
_NO_TRG = "SELECT NULL::bigint AS id, NULL::bigint AS rnk, NULL::real AS sim WHERE false"   # pg_trgm or td_fold missing
_NO_VEC = "SELECT NULL::bigint AS id, NULL::bigint AS rnk WHERE false"   # full-text only


def hybrid_search(query: str, *, org_id: str = "default", doc_type: str | None = None,
                  limit: int = 5, candidates: int = 30, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect_vector()
    try:
        embedcfg.ensure_loaded(conn)
        params = {"cfg": textsearch.fts_config(conn), "q": query, "org": org_id, "doc_type": doc_type, "k": candidates, "rrf": RRF_K, "limit": limit}
        have = column_dim(conn)
        if have is None or have == embedcfg.current().dim:
            params["qv"] = Vector(embed([query])[0])
            sql = _SQL.replace("__VEC__", _VEC)
        else:  # the stored vectors have another size (EMBEDDING_DIM changed, not re-embedded yet): never a 500
            sql = _SQL.replace("__VEC__", _NO_VEC)
        trigram = len(query.strip()) >= textsearch.TRGM_MIN_LEN and textsearch.trigram_ready(conn)
        sql = sql.replace("__TRG__", _TRG if trigram else _NO_TRG)
        params["trgm"] = textsearch.TRGM_POLICY_THRESHOLD
        with conn.transaction():      # the threshold of `<%` is a setting, local to this transaction
            textsearch.set_trgm_threshold(conn, textsearch.TRGM_POLICY_THRESHOLD)
            rows = conn.execute(sql, params).fetchall()
    finally:
        if own:
            conn.close()
    for r in rows:
        r["score"] = float(r["score"])
        r["trgm_score"] = None if r["trgm_score"] is None else round(float(r["trgm_score"]), 3)
    return rows
