"""Row MACs (agentpg-kit 0.4.2 pin): a database write without the vault data key cannot plant or edit a provider account, a validation
setting, a session or an API token. Enforced while the vault is unlocked, unchanged while it is locked."""
import os
import socket

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from trustdesk import accounts, auth, cli, core, migrate, netguard, rowmac, validator, vault  # noqa: E402
from trustdesk import settings_store as store  # noqa: E402
from trustdesk.db import connect  # noqa: E402

PASSWORD = "correct-horse-battery"  # pragma: allowlist secret
PLANT = ("INSERT INTO llm_account (name, provider, base_url, chat_model, embedding_model) "
         "VALUES ('planted','custom','https://attacker.example/v1','m','e') RETURNING id")


@pytest.fixture(autouse=True)
def _db(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    for v in ("TRUSTDESK_API_KEY", "TRUSTDESK_ALLOW_ANONYMOUS", "TRUSTDESK_ENCRYPTION_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_API_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("TRUSTDESK_ENCRYPTION_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("LLM_API_KEY_FILE", "/nonexistent/key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    core.auth.limiter.clear()
    core.local._setup_code = None
    vault.lock()
    client.cookies.clear()
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    rowmac._reported.clear()
    with connect() as c:
        auth.set_admin_password(c, PASSWORD)     # initialises the vault with the password wrap
    assert vault.is_unlocked()


def _account(**over) -> int:
    with connect() as c:
        return accounts.create(c, **({"name": "main", "provider": "openai", "base_url": "https://llm.example/v1", "api_key": "sk-test-key-1234",
                                      "chat_model": "chat-1", "embedding_model": "emb-1", "embedding_dim": 32} | over))


def _visible() -> list[str]:
    with connect() as c:
        return [a["name"] for a in accounts.list_accounts(c)]


def _plant(sql=PLANT) -> int:
    with connect() as c:
        i = c.execute(sql).fetchone()["id"]
        c.commit()
    return i


def test_an_account_written_by_the_app_verifies():
    a = _account()
    assert _visible() == ["main"]
    with connect() as c:
        assert c.execute("SELECT mac FROM llm_account WHERE id=%s", (a,)).fetchone()["mac"]
        assert accounts.chat_config_for(c, a).base_url == "https://llm.example/v1"
        accounts.update(c, a, name="renamed", provider="openai", base_url="https://other.example/v1", api_key=None, clear_key=False,
                        chat_model="chat-2", embedding_model="emb-1", embedding_dim=32)
        assert accounts.chat_config_for(c, a).model == "chat-2"        # an update from the app re-signs
    assert _visible() == ["renamed"]


def test_a_swapped_or_blanked_key_blob_is_ignored_while_unlocked():
    a, b = _account(name="one"), _account(name="two")
    with connect() as c:
        enc_a = c.execute("SELECT api_key_enc FROM llm_account WHERE id=%s", (a,)).fetchone()["api_key_enc"]
        c.execute("UPDATE llm_account SET api_key_enc=%s WHERE id=%s", (enc_a, b))      # a's key blob copied into b
        c.execute("UPDATE llm_account SET api_key_enc=NULL, api_key_last4=NULL WHERE id=%s", (a,))   # a's key blanked
        c.commit()
    assert _visible() == []
    with connect() as c:
        for i in (a, b):
            with pytest.raises(accounts.AccountError):
                accounts.chat_config_for(c, i)
    vault.lock()                                                         # no vault: behaviour as before row MACs
    assert sorted(_visible()) == ["one", "two"]


def test_a_forged_account_is_ignored_while_unlocked_and_accepted_while_locked():
    _account()
    forged = _plant()
    assert _visible() == ["main"]
    with connect() as c:
        with pytest.raises(accounts.AccountError):
            accounts.chat_config_for(c, forged)                          # routing never reaches the attacker's host
        with pytest.raises(accounts.AccountError):
            accounts.embedding_config_for(c, forged)
        assert validator.choose_account(c) != forged                     # not picked as "the only usable account" either
    vault.lock()                                                         # no vault: behaviour as before row MACs
    assert sorted(_visible()) == ["main", "planted"]
    with connect() as c:
        assert accounts.chat_config_for(c, forged).base_url == "https://attacker.example/v1"


def test_an_edited_authority_column_is_ignored_but_a_display_column_is_not():
    a = _account()
    with connect() as c:
        c.execute("UPDATE llm_account SET name='cosmetic' WHERE id=%s", (a,))
        c.commit()
    assert _visible() == ["cosmetic"]                                    # name is not an authority column
    for col, val in (("base_url", "https://attacker.example/v1"), ("chat_model", "evil"), ("embedding_model", "evil"),
                     ("provider", "custom"), ("embedding_dim", 99)):
        b = _account(name=f"x-{col}")
        with connect() as c:
            c.execute(f"UPDATE llm_account SET {col}=%s WHERE id=%s", (val, b))   # noqa: S608
            c.commit()
        with connect() as c, pytest.raises(accounts.AccountError):
            accounts.get_account(c, b)


def test_an_account_with_a_blanked_mac_is_refused_and_logged_once_without_values(caplog):
    a = _account()
    with connect() as c:
        c.execute("UPDATE llm_account SET mac=NULL WHERE id=%s", (a,))
        c.commit()
        for _ in range(3):
            assert accounts.list_accounts(c) == []
    msgs = [r.getMessage() for r in caplog.records if r.name == "trustdesk.rowmac"]
    assert msgs == [f"row refused, MAC missing or wrong: table=llm_account key={a}"]


def test_a_forged_setting_is_ignored_while_unlocked_and_accepted_while_locked():
    with connect() as c:
        c.execute("INSERT INTO app_setting (key, value) VALUES ('validation_account','env'), ('validate_max_attempts','99')")
        c.commit()
        assert store.get(c, validator.SETTING) is None and validator.max_attempts(c) == core_default()
    vault.lock()
    with connect() as c:
        assert store.get(c, validator.SETTING) == "env"
        assert validator.max_attempts(c) == min(99, validator.ATTEMPTS_MAX)


def core_default() -> int:
    from trustdesk.config import get_settings
    return get_settings().validate_max_attempts


def test_a_setting_written_by_the_app_verifies_and_other_keys_pass_through():
    with connect() as c:
        validator.set_max_attempts(c, 5)
        validator.set_max_attempts(c, 7)                                 # an overwrite re-signs
        store.put(c, "some_other_key", "v")
        c.commit()
        assert validator.max_attempts(c) == 7
        assert store.get(c, "some_other_key") == "v"
        assert c.execute("SELECT mac FROM app_setting WHERE key='validate_max_attempts'").fetchone()["mac"]
        c.execute("UPDATE app_setting SET value='1' WHERE key='validate_max_attempts'")
        c.commit()
        assert validator.max_attempts(c) == core_default()               # an edited value is ignored


def test_the_kit_ignores_a_planted_api_token_and_session():
    with connect() as c:
        _, raw = core.local.create_token(c, "legit", "full")
        c.commit()
        assert core.local.token_principal(c, raw) is not None
        c.execute("UPDATE api_token SET mac=NULL")
        c.commit()
        assert core.local.token_principal(c, raw) is None                # valid columns, no MAC: treated as missing


def test_adopt_signs_legacy_rows_and_the_cli_asks_for_the_password(monkeypatch, capsys):
    a = _account()
    with connect() as c:
        _, raw = core.local.create_token(c, "legit", "full")
        validator.set_max_attempts(c, 4)
        c.execute("UPDATE llm_account SET mac=NULL; UPDATE app_setting SET mac=NULL WHERE key='validate_max_attempts'; "
                  "UPDATE api_token SET mac=NULL")
        c.commit()
        assert accounts.list_accounts(c) == [] and validator.max_attempts(c) == core_default()
        assert core.local.token_principal(c, raw) is None
    vault.lock()                                                         # the CLI opens the vault itself, with the typed password
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSWORD)
    assert cli.main(["adopt-rowmacs"]) == 0
    out = capsys.readouterr().out
    assert "llm_account 1" in out and "app_setting 1" in out and "api_token 1" in out and PASSWORD not in out
    with connect() as c:
        assert [x["id"] for x in accounts.list_accounts(c)] == [a]
        assert validator.max_attempts(c) == 4
        assert core.local.token_principal(c, raw) is not None


def test_adopt_refuses_a_wrong_password(monkeypatch, capsys):
    _account()
    vault.lock()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "not-the-password-xx")
    assert cli.main(["adopt-rowmacs"]) == 1
    assert "locked" in capsys.readouterr().err


def test_rotating_the_data_key_keeps_signed_rows_valid_and_planted_rows_out():
    a = _account()
    with connect() as c:
        validator.set_max_attempts(c, 6)
        _, raw = core.local.create_token(c, "legit", "full")
        c.commit()
    forged = _plant()
    with connect() as c:
        snap = rowmac.snapshot(c)
        c.rollback()
        vault.rotate_data_key(c, password=PASSWORD)
        rowmac.reseal(c, snap)
        c.commit()
    with connect() as c:
        assert [x["id"] for x in accounts.list_accounts(c)] == [a]       # re-signed under the new key; the forged row stays out
        assert validator.max_attempts(c) == 6
        assert core.local.token_principal(c, raw) is not None
        with pytest.raises(accounts.AccountError):
            accounts.get_account(c, forged)


def test_setting_keys_match_the_names_the_app_uses():
    from trustdesk import embedcfg
    assert rowmac.SETTING_KEYS == {embedcfg.SETTING, validator.SETTING, validator.BACKLOG_SETTING, validator.ATTEMPTS_SETTING}
