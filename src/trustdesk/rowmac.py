"""Row MACs: a database WRITE without the vault data key cannot plant a provider account, redirect where requests are sent or change
the validation settings.

A security-relevant row carries `mac`, an HMAC under a subkey of the vault data key (agentpg-kit `Vault.row_mac`) over the columns listed
in FIELDS. Reads that feed a decision call `verify_row`; a row with a missing or wrong MAC is treated exactly like a missing row and
logged once (table and key only, never values). Writes sign in the same transaction.

Enforced only while the vault is unlocked (with sealed boot that is always, before the app serves). While it is locked nothing is
checked and rows are written without a MAC: behaviour is as before row MACs, and `trustdesk adopt-rowmacs` signs them later.

The kit signs `web_session` and `api_token` itself (LocalAuth). Limits (README, "Row MACs"): an older authentic row replays from a
backup; deleting rows is still possible; columns outside FIELDS are not covered.
"""
import json
import logging
import threading

from prometheus_client import Counter

from . import core

log = logging.getLogger("trustdesk.rowmac")

# llm_account: where traffic goes (base_url, provider) and which models it uses. Not the display name, last4 or timestamps, and not
# api_key_enc: that ciphertext is already authenticated by the vault (AEAD under the data key, bound to its row id), so a write
# without the key can neither forge nor move one; it can only blank it or replay the same row's older value.
# app_setting: only the keys named in SETTING_KEYS; the kit's own keys (vault wraps, kit settings) are the kit's to protect.
FIELDS: dict[str, tuple[str, ...]] = {
    "llm_account": ("id", "provider", "base_url", "chat_model", "embedding_model", "embedding_dim"),
    "app_setting": ("key", "value"),
}
PK: dict[str, tuple[str, ...]] = {"llm_account": ("id",), "app_setting": ("key",)}
SETTING_KEYS = frozenset({"embedding_account_id", "validation_account", "validate_backlog_per_minute", "validate_max_attempts"})

tampered = Counter("trustdesk_tampered_rows_total", "Rows refused because their MAC is missing or wrong", ["table"],
                   registry=core.metrics.registry)
_reported: set[tuple] = set()
_reported_lock = threading.Lock()


def enforced() -> bool:
    return core.vault.is_unlocked()


def _canon(row: dict, table: str) -> list[str]:
    return [json.dumps(row[c]) if row[c] is not None else "\x00" for c in FIELDS[table]]   # None differs from the empty string


def sign_row(table: str, row: dict) -> str:
    """The MAC for `row` (a dict holding at least the MAC'd columns of `table`). Raises VaultLocked when the vault is locked."""
    return core.vault.row_mac(table, *_canon(row, table))


def verify_row(table: str, row: dict | None) -> bool:
    """True when the row may be used: its MAC is valid, or MACs are not enforced (vault locked). A missing row is False."""
    if not row:
        return False
    if not enforced():
        return True
    try:
        if core.vault.check_row_mac(table, row.get("mac"), *_canon(row, table)):
            return True
    except Exception:  # noqa: BLE001  (locked mid-call, odd column types: fail closed)
        return False
    key = (table, *(str(row[c]) for c in PK[table]))
    tampered.labels(table).inc()
    with _reported_lock:
        first = key not in _reported
        _reported.add(key)
    if first:
        log.warning("row refused, MAC missing or wrong: table=%s key=%s", table, "/".join(key[1:]))
    return False


def seal(conn, table: str, where: str, params: tuple = ()) -> int:
    """Sign the rows of `table` matching `where`, in the caller's transaction. ONLY for rows this process has just written (or `adopt`).
    With the vault locked the MAC is cleared (the row is ignored once the vault opens, until adopted)."""
    rows = conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()   # noqa: S608  (table is a constant of this module)
    unlocked = enforced()
    pk = PK[table]
    for r in rows:
        conn.execute(f"UPDATE {table} SET mac=%s WHERE " + " AND ".join(f"{c}=%s" for c in pk),    # noqa: S608
                     (sign_row(table, r) if unlocked else None, *(r[c] for c in pk)))
    return len(rows)


def seal_setting(conn, key: str) -> None:
    if key in SETTING_KEYS:
        seal(conn, "app_setting", "key=%s", (key,))


def adopt(conn) -> dict[str, int]:
    """Sign every covered row that has no MAC (a database that existed before row MACs), plus the kit's sessions and API tokens.
    TRUSTS THE DATABASE AS IT IS NOW. Needs the vault open."""
    if not enforced():
        raise RuntimeError("adopt needs an open vault")
    kit = core.local.adopt_rows(conn)           # commits
    out = {"llm_account": seal(conn, "llm_account", "mac IS NULL"),
           "app_setting": seal(conn, "app_setting", "mac IS NULL AND key = ANY(%s)", (sorted(SETTING_KEYS),)),
           "web_session": kit["sessions"], "api_token": kit["tokens"]}
    return out


# ---- data-key rotation: every MAC changes with the key, so rows that verify now are signed again under the new one -------------
def snapshot(conn) -> dict[str, list]:
    """Keys of every row that verifies under the current key (call before `rotate_data_key`). Includes the kit's rows, which the kit
    does not re-sign."""
    out: dict[str, list] = {}
    out["web_session"] = [r["token_hash"] for r in conn.execute("SELECT * FROM web_session").fetchall()
                          if core.vault.check_row_mac("web_session", r["mac"], r["token_hash"], r["subject"], r["name"], r["email"],
                                                      json.dumps(sorted(r["roles"])), r["source"], int(r["expires_at"].timestamp()))]
    out["api_token"] = [r["id"] for r in conn.execute("SELECT * FROM api_token WHERE revoked_at IS NULL").fetchall()
                        if core.vault.check_row_mac("api_token", r["mac"], r["id"], r["name"], r["token_hash"], r["prefix"], r["scope"], 0)]
    out["llm_account"] = [r["id"] for r in conn.execute("SELECT * FROM llm_account").fetchall() if verify_row("llm_account", r)]
    out["app_setting"] = [r["key"] for r in conn.execute("SELECT * FROM app_setting WHERE key = ANY(%s)", (sorted(SETTING_KEYS),)).fetchall()
                          if verify_row("app_setting", r)]
    return out


def reseal(conn, snap: dict[str, list]) -> None:
    """Sign the snapshotted rows again (call after `rotate_data_key`, then commit). Rows that did not verify stay unsigned."""
    for token_hash in snap.get("web_session", []):
        r = conn.execute("SELECT * FROM web_session WHERE token_hash=%s", (token_hash,)).fetchone()
        if r:
            conn.execute("UPDATE web_session SET mac=%s WHERE token_hash=%s", (core.local.session_mac(
                r["token_hash"], r["subject"], r["name"], r["email"], r["roles"], r["source"], r["expires_at"]), token_hash))
    for tid in snap.get("api_token", []):
        r = conn.execute("SELECT * FROM api_token WHERE id=%s", (tid,)).fetchone()
        if r:
            conn.execute("UPDATE api_token SET mac=%s WHERE id=%s", (core.local.token_mac(
                r["id"], r["name"], r["token_hash"], r["prefix"], r["scope"], False), tid))
    for aid in snap.get("llm_account", []):
        seal(conn, "llm_account", "id=%s", (aid,))
    for key in snap.get("app_setting", []):
        seal(conn, "app_setting", "key=%s", (key,))
