"""Connection handling. The database may be scaled to zero, so connect lazily and retry."""
import time

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .config import get_settings


def connect(retries: int = 5, delay: float = 1.0) -> psycopg.Connection:
    url = get_settings().database_url
    last: Exception | None = None
    for attempt in range(retries):
        try:
            conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=10)
            return conn
        except psycopg.OperationalError as exc:  # cold start / waking database
            last = exc
            time.sleep(delay * (2**attempt))
    raise RuntimeError(f"database unavailable after {retries} attempts: {last}")


def connect_vector() -> psycopg.Connection:
    """Connection with pgvector adapters; requires the extension to exist (run migrations first)."""
    conn = connect()
    register_vector(conn)
    return conn
