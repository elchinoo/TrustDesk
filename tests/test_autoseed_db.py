"""AUTO_SEED at `trustdesk start`: the vault is opened before the seed, the seed avoids the vault when it can, and a skipped
seed is retried in the background instead of waiting for the next restart."""
import asyncio
import dataclasses
import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import api, cli, core, embedcfg, embeddings, ingest, migrate, seedpack, vault  # noqa: E402
from trustdesk import settings_store as store  # noqa: E402
from trustdesk.db import connect  # noqa: E402
from trustdesk.embeddings import EmbeddingError  # noqa: E402

PASSWORD = "correct horse battery"  # pragma: allowlist secret
PLATFORM = "platform-secret-for-tests"  # pragma: allowlist secret


class Embedder:
    """Stands in for the provider: needs the account's key (so a locked vault fails like the live one) and can be 'down'."""

    def __init__(self, monkeypatch):
        self.calls, self.down, self.keys = 0, False, []
        monkeypatch.setattr(ingest, "embed", self)

    def __call__(self, texts, cfg=None):
        self.calls += 1
        if self.down:
            raise EmbeddingError("embeddings endpoint returned HTTP 503: upstream unavailable")
        self.keys.append(cfg.api_key())          # raises VaultLocked while the stored key cannot be decrypted
        return [embeddings.hash_embed(t, cfg.dim) for t in texts]


@pytest.fixture
def provider(monkeypatch):
    return Embedder(monkeypatch)


@pytest.fixture(autouse=True)
def _db(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("TRUSTDESK_API_KEY", "k")
    for v in ("LLM_BASE_URL", "TRUSTDESK_OIDC_ISSUER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_ENCRYPTION_KEY", PLATFORM)
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    monkeypatch.setenv("AUTO_SEED", "true")
    monkeypatch.setattr(cli, "_serve", lambda: None)
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    migrate._startup["message"] = ""
    seedpack.reset_deferred()
    yield
    seedpack.reset_deferred()


def stored_embedding_account(base_url="https://gw.example/v1", model="emb-1"):
    """An embedding account with an encrypted key, chosen as the active one, then the process 'restarts' (vault locked)."""
    with connect() as c:
        vault.initialize(c, PASSWORD)
        row = c.execute("INSERT INTO llm_account (name, provider, base_url, api_key_enc, api_key_last4, embedding_model, "
                        "embedding_dim) VALUES ('gw','custom',%s,%s,'cret',%s,768) RETURNING id",
                        (base_url, vault.encrypt("sk-stored-secret"), model)).fetchone()
        store.put(c, embedcfg.SETTING, str(row["id"]))
        c.commit()
    vault.lock()
    embedcfg.reset()


def documents() -> int:
    with connect() as c:
        return c.execute("SELECT count(*) AS n FROM kb_document").fetchone()["n"]


def test_start_unlocks_the_vault_before_seeding(provider):
    """The live bug: the vault was only opened inside the app (after the seed), so the seed always failed with VaultLocked."""
    stored_embedding_account()
    served_with = {}

    def serve():
        served_with["documents"], served_with["unlocked"] = documents(), vault.is_unlocked()

    cli._serve = serve
    assert cli._start() == 0
    assert served_with == {"documents": len(seedpack.files()), "unlocked": True}
    assert provider.keys and set(provider.keys) == {"sk-stored-secret"}
    assert not seedpack.is_deferred()


def test_the_seed_does_not_need_the_vault_when_the_environment_embedder_is_the_same(provider, monkeypatch):
    stored_embedding_account()
    monkeypatch.delenv("TRUSTDESK_ENCRYPTION_KEY")          # nothing can unlock the vault
    monkeypatch.setenv("LLM_BASE_URL", "https://gw.example/v1/")
    monkeypatch.setenv("LLM_API_KEY", "sk-env-key")         # pragma: allowlist secret
    monkeypatch.setenv("EMBEDDING_MODEL", "emb-1")
    assert cli._start() == 0
    assert documents() == len(seedpack.files()) and not vault.is_unlocked()
    assert set(provider.keys) == {"sk-env-key"} and not seedpack.is_deferred()
    with connect() as c:
        assert c.execute("SELECT name, dim FROM kb_embedding_model").fetchone() == {"name": "emb-1", "dim": 768}


def test_a_different_environment_embedder_is_not_substituted(provider, monkeypatch):
    stored_embedding_account()
    monkeypatch.delenv("TRUSTDESK_ENCRYPTION_KEY")
    monkeypatch.setenv("LLM_BASE_URL", "https://other.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-env-key")         # pragma: allowlist secret
    monkeypatch.setenv("EMBEDDING_MODEL", "emb-1")
    assert cli._start() == 0
    assert documents() == 0 and seedpack.is_deferred()      # other vectors would not match what search embeds with


def test_the_environment_account_alone_never_touches_the_vault(provider, monkeypatch):
    monkeypatch.delenv("TRUSTDESK_ENCRYPTION_KEY")
    monkeypatch.setenv("LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-env-key")         # pragma: allowlist secret
    assert cli._start() == 0
    assert documents() == len(seedpack.files()) and set(provider.keys) == {"sk-env-key"}


def test_a_skipped_seed_is_retried_once_the_vault_is_unlocked(provider, monkeypatch, capsys):
    stored_embedding_account()
    monkeypatch.delenv("TRUSTDESK_ENCRYPTION_KEY")          # only the admin password can unlock it
    assert cli._start() == 0
    assert documents() == 0 and seedpack.is_deferred()
    assert "AUTO_SEED skipped" in capsys.readouterr().err
    calls = provider.calls
    assert seedpack.run_deferred() is None                  # still locked: waiting is free, nothing is tried or counted
    assert provider.calls == calls and seedpack.is_deferred() and seedpack._deferred["tries"] == 0
    with connect() as c:
        assert vault.unlock_with_password(c, PASSWORD)      # the admin signs in
    assert seedpack.run_deferred() == "seeded"
    assert documents() == len(seedpack.files()) and not seedpack.is_deferred()
    assert seedpack.run_deferred() is None


def test_a_provider_outage_is_retried_in_the_background_loop(provider, monkeypatch):
    provider.down = True
    assert cli._start() == 0
    assert documents() == 0 and seedpack.is_deferred()
    monkeypatch.setattr(seedpack, "RETRY_DELAY", 0)
    monkeypatch.setattr(seedpack, "RETRY_INTERVAL", 0.01)
    real = provider.__call__

    def recovers_on_the_second_try(texts, cfg=None):
        provider.down = seedpack._deferred["tries"] < 2
        return real(texts, cfg)

    monkeypatch.setattr(ingest, "embed", recovers_on_the_second_try)
    asyncio.run(asyncio.wait_for(api._seed_retry_loop(), 10))
    assert documents() == len(seedpack.files()) and not seedpack.is_deferred() and seedpack._deferred["tries"] == 2


def test_retries_are_bounded_and_never_seed_twice(provider, monkeypatch):
    provider.down = True
    cli._start()
    for _ in range(seedpack.RETRY_TRIES + 3):
        seedpack.run_deferred(progress=lambda m: None)
    assert seedpack._deferred["tries"] == seedpack.RETRY_TRIES and not seedpack.is_deferred()
    seedpack.reset_deferred()
    provider.down = False
    seedpack._deferred["pending"] = True
    assert seedpack.auto_seed(lambda m: None) == "seeded"
    n = documents()
    seedpack._deferred["pending"] = True                    # something else filled the knowledge base meanwhile: nothing to do
    assert seedpack.run_deferred() == "unneeded" and documents() == n and not seedpack.is_deferred()


def test_auto_unlock_is_shared_by_the_entrypoint_and_the_app(monkeypatch):
    """One helper does the single-sign-on initialisation and the unlock for both, so they cannot drift."""
    sso = dataclasses.replace(core.fed, verifier=object())
    assert sso.enabled
    monkeypatch.setattr(core, "fed", sso)
    with connect() as c:
        assert not vault.is_initialized(c)
        assert vault.auto_unlock(c) is True                 # created under the platform secret alone, and open
        assert vault.is_initialized(c) and vault.is_unlocked()
    vault.lock()
    with connect() as c:
        assert vault.auto_unlock(c) is True and vault.is_unlocked()
    vault.lock()
    api._startup_tasks()                                    # the app goes through the same helper
    assert vault.is_unlocked()
    vault.lock()
    monkeypatch.delenv("TRUSTDESK_ENCRYPTION_KEY")
    with connect() as c:
        assert vault.auto_unlock(c) is False and not vault.is_unlocked()
