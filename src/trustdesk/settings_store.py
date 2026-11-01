"""The key/value settings store (agentpg-kit `app_setting`), with row MACs on this app's own settings.

The keys in `rowmac.SETTING_KEYS` (which account validates and embeds, the validation rates) are signed when written and ignored
when read back without a valid MAC while the vault is unlocked. Every other key, the kit's own included, passes straight through."""
from apgkit.settings_store import delete

from . import rowmac


def get(conn, key: str) -> str | None:
    row = conn.execute("SELECT key, value, mac FROM app_setting WHERE key=%s", (key,)).fetchone()
    if not row:
        return None
    if key in rowmac.SETTING_KEYS and not rowmac.verify_row("app_setting", row):
        return None
    return row["value"]


def put(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_setting (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, mac=NULL",
                 (key, value))
    rowmac.seal_setting(conn, key)       # same transaction; with the vault locked the MAC stays empty


__all__ = ["delete", "get", "put"]
