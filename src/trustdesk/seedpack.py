"""The packaged fictional "Acme" policy pack: security policies plus HR, travel and expense rules."""
from pathlib import Path

from . import embedcfg, vault
from .ingest import ingest_file

PACK = Path(__file__).resolve().parent / "seed" / "acme"
_PREFIXES = {"hr": "hr", "travel": "travel", "expense": "expense", "governance": "governance"}
RETRY_DELAY = 5                     # seconds after startup before a skipped AUTO_SEED is tried again
RETRY_INTERVAL = 60                 # seconds between tries
RETRY_TRIES = 5                     # real attempts (waiting for a locked vault does not count)

_deferred = {"pending": False, "tries": 0}


def doc_type_for(name: str) -> str:
    """Policy type from the file name prefix (hr-, travel-, expense-, governance-); everything else is security."""
    return _PREFIXES.get(name.split("-", 1)[0], "security")


def files() -> list[Path]:
    return sorted(PACK.glob("*.md"))


def load(conn=None, cfg=None) -> list[dict]:
    results = []
    for f in files():
        r = ingest_file(f, doc_type=doc_type_for(f.name), conn=conn, cfg=cfg)
        results.append({"source": f.name, "status": r["status"], "chunks": r["chunks"]})
    return results


def seed_config(conn):
    """The embedding configuration to seed with, or None for the active one. The seed must not need the vault when it can
    avoid it: if the active account's key is stored encrypted and the vault is locked, but the environment account is the
    very same embedder (endpoint, model and size), the environment key produces identical vectors."""
    embedcfg.ensure_loaded(conn)
    cfg = embedcfg.current()
    if not cfg.key_enc or vault.is_unlocked():
        return None
    env = embedcfg.from_env()
    same = (env.base_url, env.model, env.dim) == (cfg.base_url.rstrip("/"), cfg.model, cfg.dim)
    return env if env.base_url and env.key_plain and same else None


def vault_blocks_seed(conn) -> bool:
    """True while the only way to embed is a stored key that is still locked (nothing to try yet)."""
    embedcfg.ensure_loaded(conn)
    cfg = embedcfg.current()
    return bool(cfg.key_enc) and not vault.is_unlocked() and seed_config(conn) is None


def auto_seed(progress=print) -> str:
    """AUTO_SEED: load the pack into an empty knowledge base. Never raises. Returns 'seeded', 'unneeded' (the knowledge
    base already has documents) or 'deferred' (skipped for any reason: it is retried in the background, see `run_deferred`)."""
    from .db import connect, connect_vector
    from .migrate import embedding_state

    try:
        with connect() as conn:
            empty = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM kb_document) AS e").fetchone()["e"]
            embedcfg.ensure_loaded(conn)
            fits = not embedding_state(conn)["needs_reembedding"]
        if not empty:
            _deferred["pending"] = False
            return "unneeded"
        if not fits:
            progress("WARNING: AUTO_SEED skipped: the index needs re-embedding first")
            _deferred["pending"] = True
            return "deferred"
        with connect_vector() as conn:
            results = load(conn, seed_config(conn))
        for r in results:
            progress(f"seed {r['source']}: {r['status']}")
        _deferred["pending"] = False
        return "seeded"
    except Exception as exc:  # noqa: BLE001 - e.g. provider down: the app still comes up and the seed is retried
        progress(f"WARNING: AUTO_SEED skipped: {type(exc).__name__}: {str(exc)[:200]}")
        _deferred["pending"] = True
        return "deferred"


def is_deferred() -> bool:
    return _deferred["pending"] and _deferred["tries"] < RETRY_TRIES


def run_deferred(progress=print) -> str | None:
    """Try a skipped AUTO_SEED again (called once the app is serving and the vault had its chance to unlock). Returns the
    outcome, or None when there was nothing to do or the vault is still locked (waiting is free, it is tried again)."""
    from .db import connect

    if not is_deferred():
        return None
    try:
        with connect() as conn:
            if vault_blocks_seed(conn):
                return None
    except Exception:  # noqa: BLE001 - the database may be asleep: tried again next time
        return None
    _deferred["tries"] += 1
    return auto_seed(progress)


def reset_deferred() -> None:
    _deferred.update(pending=False, tries=0)
