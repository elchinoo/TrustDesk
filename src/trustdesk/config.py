import os
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


def get_settings() -> Settings:
    return Settings(
        # Hosting platforms often provide the database as DB_EXTERNAL_DSN; DATABASE_URL wins if both are set.
        database_url=os.environ.get("DATABASE_URL")
        or os.environ.get("DB_EXTERNAL_DSN")
        or "postgresql://localhost:5432/trustdesk",  # credentials come from the environment / libpq, never code
        api_key=os.environ.get("TRUSTDESK_API_KEY", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", "768")),
        allow_anonymous=os.environ.get("TRUSTDESK_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"},
    )
