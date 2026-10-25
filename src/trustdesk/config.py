import os
from dataclasses import dataclass

from apgkit.config import read_secret


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    llm_base_url: str
    llm_api_key: str
    embedding_model: str
    llm_chat_model: str
    embedding_dim: int
    allow_anonymous: bool
    send_dimensions: bool
    validate_backlog_per_minute: int
    validate_max_attempts: int


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _api_key() -> str:
    return read_secret("TRUSTDESK_API_KEY", "/etc/trustdesk/api_key")


def get_settings() -> Settings:
    return Settings(
        # First match wins. An empty DSN makes libpq fall back to the standard PG* variables
        # (PGHOST, PGUSER, PGPASSWORD, PGDATABASE...), which linked sandboxes also provide.
        database_url=os.environ.get("DATABASE_URL")
        or os.environ.get("DB_EXTERNAL_DSN")
        or os.environ.get("PSQL_CONNECTION_STRING")
        or "",
        api_key=_api_key(),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        llm_api_key=read_secret("LLM_API_KEY", "/etc/trustdesk/llm_api_key"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        llm_chat_model=os.environ.get("LLM_CHAT_MODEL", ""),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", "768")),
        allow_anonymous=os.environ.get("TRUSTDESK_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"},
        # Some providers reject an unknown `dimensions` field; set EMBEDDING_SEND_DIMENSIONS=false for them.
        send_dimensions=os.environ.get("EMBEDDING_SEND_DIMENSIONS", "true").lower() not in {"0", "false", "no"},
        # Requests waiting for validation (no account was configured when they arrived) are picked up in the
        # background at this rate. 0 turns it off; Settings can override it at runtime.
        validate_backlog_per_minute=_int_env("VALIDATE_BACKLOG_PER_MINUTE", 10),
        # A validation that ended in `failed` is retried in the background (after 5, 15, then 60 minutes) up to this many
        # attempts in total. 0 never retries; Settings can override it at runtime.
        validate_max_attempts=_int_env("VALIDATE_MAX_ATTEMPTS", 3),
    )
