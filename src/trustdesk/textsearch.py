"""Accent-insensitive search with the `unaccent` extension (an EDB/AgentPG contrib extension), degrading to plain matching.

Two places use it: policy search (a text search configuration, `td_english`, created by migration 006 and used by the
generated `kb_chunk.tsv` column) and the employee/request name search (`unaccent(col) ILIKE unaccent(pattern)`).
Both check the database each time (one cheap catalog query), so an instance that gains the extension later is picked up
without a restart, and one without it keeps working with exact matching.
"""
import re

FTS_CONFIG = "public.td_english"

# Typo tolerance (pg_trgm, migration 007). word_similarity(query, text) is the share of the query's trigrams found in the best
# matching stretch of text, 0..1. Names and titles are short, so a stricter bar than passages.
TRGM_THRESHOLD = 0.4          # people and request titles
TRGM_POLICY_THRESHOLD = 0.3   # policy passages
TRGM_MIN_LEN = 3              # shorter queries match too much by chance
FUZZY_LIMIT = 50              # at most this many typo matches for a list search


def has_unaccent(conn) -> bool:
    return conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'unaccent'").fetchone() is not None


def fts_config(conn) -> str:
    """The text search configuration `kb_chunk.tsv` was built with: accent folding when the migration could enable it."""
    row = conn.execute("SELECT 1 FROM pg_ts_config c JOIN pg_namespace n ON n.oid = c.cfgnamespace "
                       "WHERE n.nspname = 'public' AND c.cfgname = 'td_english'").fetchone()
    return FTS_CONFIG if row and has_unaccent(conn) else "english"


def like_pattern(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def ilike_any(conn, columns: list[str], text: str) -> tuple[str, list]:
    """SQL condition and arguments: any of `columns` contains `text`, ignoring case and, if possible, accents."""
    like = like_pattern(text)
    if has_unaccent(conn):
        return "(" + " OR ".join(f"unaccent({c}) ILIKE unaccent(%s)" for c in columns) + ")", [like] * len(columns)
    return "(" + " OR ".join(f"{c} ILIKE %s" for c in columns) + ")", [like] * len(columns)


def trigram_ready(conn) -> bool:
    """pg_trgm and the folding function of migration 007 exist (one catalog query); otherwise typo tolerance is simply off."""
    return conn.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') "
                        "AND to_regprocedure('public.td_fold(text)') IS NOT NULL AS ok").fetchone()["ok"]


def set_trgm_threshold(conn, threshold: float) -> None:
    """The `<%` operator (the one the GIN index serves) reads this setting; it is local to the current transaction."""
    conn.execute("SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)", (str(threshold),))


def fuzzy_hits(conn, frm: str, id_col: str, cols: list[str], text: str, *, threshold: float = TRGM_THRESHOLD,
               limit: int = FUZZY_LIMIT) -> list[tuple[int, float]]:
    """(id, similarity) of the rows whose `cols` resemble `text` (typos, missing accents), best first, at most `limit`.

    `frm`, `id_col` and `cols` are constants written by the callers (never user input); the user's text is always a parameter.
    Returns [] when typo search is unavailable or the text is too short to say anything."""
    folded = text.strip()
    if len(folded) < TRGM_MIN_LEN or not trigram_ready(conn):
        return []
    branches = " UNION ALL ".join(
        f"SELECT {id_col} AS id, word_similarity(public.td_fold(%s), public.td_fold({c})) AS sim FROM {frm} "
        f"WHERE public.td_fold(%s) <%% public.td_fold({c})" for c in cols)
    args = [a for _ in cols for a in (folded, folded)]
    with conn.transaction():
        set_trgm_threshold(conn, threshold)
        rows = conn.execute(
            f"SELECT id, max(sim) AS sim FROM ({branches}) u GROUP BY id HAVING max(sim) >= %s ORDER BY max(sim) DESC, id LIMIT %s",
            (*args, threshold, limit)).fetchall()
    return [(r["id"], float(r["sim"])) for r in rows]


def fuzzy_join(alias: str, id_col: str, hits: list[tuple[int, float]]) -> tuple[str, list]:
    """A LEFT JOIN that brings each hit's similarity as `fz.sim` (NULL for rows that are not typo matches)."""
    return (f" LEFT JOIN unnest(%s::bigint[], %s::real[]) AS fz(id, sim) ON fz.id = {alias}.{id_col}",
            [[h[0] for h in hits], [h[1] for h in hits]])
