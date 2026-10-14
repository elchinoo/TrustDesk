"""Switching the embedding provider re-embeds every chunk. It runs in a background thread with progress."""
import threading
import time

from . import embedcfg, migrate
from . import settings_store as store
from .db import connect
from .embeddings import EmbeddingError

_lock = threading.Lock()
_state: dict = {"state": "idle", "done": 0, "total": 0, "message": "", "started_at": None}


def status() -> dict:
    return dict(_state)


def running() -> bool:
    return _state["state"] == "running"


def start(target: embedcfg.EmbeddingConfig) -> None:
    with _lock:
        if running():
            raise RuntimeError("a re-embedding job is already running")
        _state.update(state="running", done=0, total=0, message="starting", started_at=time.time())
    threading.Thread(target=_run, args=(target,), daemon=True).start()


def _run(target: embedcfg.EmbeddingConfig) -> None:
    def progress(msg: str) -> None:
        _state["message"] = msg

    def counts(done: int, total: int) -> None:
        _state.update(done=done, total=total)

    try:
        migrate.reembed(cfg=target, progress=progress, on_count=counts)
        with connect() as conn:
            store.put(conn, embedcfg.SETTING, "" if target.account_id is None else str(target.account_id))
            conn.commit()
        embedcfg.set_active(None if target.account_id is None else target)
        _state.update(state="done", message="finished")
    except (EmbeddingError, ValueError, RuntimeError) as exc:
        _state.update(state="error", message=f"no changes were made: {exc}")
    except Exception:  # noqa: BLE001 - never leak internals; the transaction rolled back
        _state.update(state="error", message="unexpected failure; no changes were made (see server log)")
        raise
