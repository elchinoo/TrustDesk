"""Accent-insensitive search with the `unaccent` extension (an EDB/AgentPG contrib extension), degrading to plain matching.

Two places use it: policy search (a text search configuration, `td_english`, created by migration 006 and used by the
generated `kb_chunk.tsv` column) and the employee/request name search (`unaccent(col) ILIKE unaccent(pattern)`).
Both check the database each time (one cheap catalog query), so an instance that gains the extension later is picked up
without a restart, and one without it keeps working with exact matching.
"""
import re

FTS_CONFIG = "public.td_english"


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
