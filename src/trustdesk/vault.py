"""Envelope encryption for stored provider keys. The implementation lives in agentpg-kit; this module keeps the old function API
(`vault.encrypt(...)`) bound to this app's single vault."""
from apgkit.vault import VaultError, VaultLocked, is_legacy_token  # noqa: F401

from .core import LLM_KEY_CONTEXT
from .core import vault as _v

is_initialized = _v.is_initialized
is_unlocked = _v.is_unlocked
lock = _v.lock
status = _v.status
initialize = _v.initialize
unlock_with_password = _v.unlock_with_password
try_unlock_with_platform_secret = _v.try_unlock_with_platform_secret
change_password = _v.change_password
reset = _v.reset
encrypt = _v.encrypt
decrypt = _v.decrypt
rotate_data_key = _v.rotate_data_key
rencrypt = _v.reencrypt_legacy


def llm_key_context(account_id: int) -> str:
    """The context every `llm_account.api_key_enc` value is encrypted and decrypted with."""
    return LLM_KEY_CONTEXT.format(id=account_id)


def auto_unlock(conn) -> bool:
    """Open the vault from the platform secret (TRUSTDESK_ENCRYPTION_KEY), creating it first on a single-sign-on-only
    deployment that has no admin password to wrap the key with. The one place both the container entrypoint (before it
    seeds) and the app (at startup) do this, so they cannot drift. Returns whether the vault is now unlocked."""
    from .core import fed

    if fed.enabled and not is_initialized(conn):
        _v.initialize_with_platform_secret(conn)
        conn.commit()
    return try_unlock_with_platform_secret(conn) or is_unlocked()
