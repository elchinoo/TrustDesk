import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    llm_base_url: str
    llm_api_key: str
    embedding_model: str
    embedding_dim: int
    allow_anonymous: bool


API_KEY_FILE = "/etc/trustdesk/api_key"


def _api_key() -> str:
    """Key from TRUSTDESK_API_KEY, else the first line of TRUSTDESK_API_KEY_FILE (default
    /etc/trustdesk/api_key) so platforms that can only mount files can still supply one."""
    key = os.environ.get("TRUSTDESK_API_KEY", "")
    if key:
        return key
    path = os.environ.get("TRUSTDESK_API_KEY_FILE", API_KEY_FILE)
    try:
        with open(path) as fh:
            return fh.readline().strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:  # exists but unreadable, e.g. root-only file and a non-root container user
        print(f"WARNING: cannot read API key file {path}: {exc.strerror}", file=sys.stderr)
        return ""


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
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", "768")),
        allow_anonymous=os.environ.get("TRUSTDESK_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"},
    )
