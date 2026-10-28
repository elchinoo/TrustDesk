"""TrustDesk federated: people and services authenticate with the ecosystem identity provider, and roles decide what they may do."""
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from apgkit.auth import Federation, RelyingParty, TokenVerifier  # noqa: E402
from joserfc import jwt  # noqa: E402
from joserfc.jwk import RSAKey  # noqa: E402

from trustdesk import api, core, migrate, vault  # noqa: E402
from trustdesk.db import connect  # noqa: E402

ISS, AUD = "https://id.example.org", "trustdesk"
KEY = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig", "alg": "RS256"})
CSRF = {"X-TrustDesk-CSRF": "1"}


class Provider:
    def __init__(self):
        self.nonce = None
        self.id_claims = {"sub": "usr_1", "name": "Ada", "email": "ada@example.org", "roles": ["trustdesk:approver"]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISS, "jwks_uri": f"{ISS}/jwks", "authorization_endpoint": f"{ISS}/oauth/authorize",
                                             "token_endpoint": f"{ISS}/oauth/token", "end_session_endpoint": f"{ISS}/oauth/logout"})
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [KEY.as_dict(private=False)]})
        if path == "/oauth/token":
            claims = {"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300, "nonce": self.nonce, **self.id_claims}
            return httpx.Response(200, json={"id_token": jwt.encode({"alg": "RS256", "kid": "k1"}, claims, KEY), "access_token": "x"})
        return httpx.Response(404)

    def http(self):
        return lambda: httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False)


def access(**claims) -> dict:
    base = {"iss": ISS, "aud": AUD, "sub": "usr_1", "exp": int(time.time()) + 600, "sub_type": "user"}
    base.update(claims)
    return {"Authorization": "Bearer " + jwt.encode({"alg": "RS256", "kid": "k1", "typ": "at+jwt"}, base, KEY)}


@pytest.fixture(autouse=True)
def _federated(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    for v in ("TRUSTDESK_API_KEY", "TRUSTDESK_ALLOW_ANONYMOUS", "TRUSTDESK_ENCRYPTION_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTDESK_API_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("TRUSTDESK_ENCRYPTION_KEY_FILE", "/nonexistent/key")
    core.auth.limiter.clear()
    core.local._setup_code = None
    vault.lock()
    client.cookies.clear()
    with connect() as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.commit()
    migrate.run()
    provider = Provider()
    verifier = TokenVerifier(ISS, AUD, http=provider.http())
    monkeypatch.setattr(core.auth, "verifier", verifier)
    monkeypatch.setattr(api, "fed", Federation(ISS, AUD, verifier, RelyingParty(ISS, AUD, None, http=provider.http()), True))
    client.provider = provider
    yield


def employee(client, headers, no="E1"):
    r = client.post("/api/v1/employees", headers=headers, json={"employee_no": no, "name": "Ann", "email": f"{no.lower()}@example.org"})
    assert r.status_code == 201, r.text


def test_roles_decide_what_a_person_may_do(client):
    approver, viewer = access(roles=["trustdesk:approver"]), access(sub="usr_2", roles=["trustdesk:viewer"])
    admin, stranger = access(sub="usr_3", roles=["trustdesk:admin"]), access(sub="usr_4", roles=["otherapp:admin"])
    employee(client, admin)
    assert client.get("/api/v1/requests", headers=approver).status_code == 200
    assert client.get("/api/v1/requests", headers=viewer).status_code == 200
    assert client.get("/api/v1/requests", headers=stranger).status_code == 403                          # signed in, but no role in this app
    assert client.get("/api/v1/requests/counts", headers=viewer).status_code == 200
    assert client.get("/api/accounts", headers=approver).status_code == 403 and client.get("/api/accounts", headers=admin).status_code == 200
    assert client.post("/api/tokens", headers=approver, json={"name": "x", "scope": "full"}).status_code == 403
    assert client.get("/api/search", headers=viewer, params={"q": "x"}).status_code == 200
    assert client.get("/api/search", headers=stranger, params={"q": "x"}).status_code == 403
    assert client.get("/api/v1/reports/overview", headers=viewer).status_code == 200
    assert client.post("/api/v1/demo", headers=approver).status_code == 403                             # settings:manage only


def test_only_approvers_decide_and_the_decision_names_the_person(client):
    approver, viewer = access(name="Ada Lovelace", roles=["trustdesk:approver"]), access(sub="usr_2", roles=["trustdesk:viewer"])
    employee(client, approver)
    sub = client.post("/api/v1/requests", headers=approver, json={"employee_no": "E1", "type": "other", "title": "Exception", "description": "Please make an exception",
                                                                  "details": {"custom_type": "Policy exception"}})
    assert sub.status_code == 201
    ref = sub.json()["ref"]
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=viewer, json={"action": "approve"}).status_code == 403
    done = client.post(f"/api/v1/requests/{ref}/decision", headers=approver, json={"action": "approve"})
    assert done.status_code == 200 and done.json()["decided_by"] == "Ada Lovelace"
    assert [e["actor"] for e in client.get(f"/api/v1/requests/{ref}", headers=approver).json()["events"]][-1] == "Ada Lovelace"


def test_a_service_with_the_requests_scope_is_a_client_application(client):
    svc = access(sub="hr-portal", sub_type="service", scope="trustdesk.requests.write", client_id="hr-portal")
    approver = access(roles=["trustdesk:approver"])
    employee(client, svc)
    r = client.post("/api/v1/requests", headers=svc, json={"employee_no": "E1", "type": "other", "title": "Hi", "description": "A request",
                                                            "details": {"custom_type": "Misc"}})
    assert r.status_code == 201 and "ai" not in r.json() and r.json()["employee"]["employee_no"] == "E1"
    ref = r.json()["ref"]
    assert "validation" not in client.get(f"/api/v1/requests/{ref}", headers=svc).json()           # never the AI advice
    assert client.get("/api/v1/requests/counts", headers=svc).status_code == 403
    assert client.post(f"/api/v1/requests/{ref}/decision", headers=svc, json={"action": "approve"}).status_code == 403
    assert client.get("/api/search", headers=svc, params={"q": "x"}).status_code == 403
    assert "validation" in client.get(f"/api/v1/requests/{ref}", headers=approver).json()          # a person with requests:read does see it
    read_only = access(sub="kb-bot", sub_type="service", scope="trustdesk.kb.read")
    assert client.get("/api/search", headers=read_only, params={"q": "x"}).status_code == 200
    assert client.get("/api/v1/requests", headers=read_only).status_code == 403


def test_tokens_for_another_app_or_with_a_bad_signature_are_refused(client):
    assert client.get("/api/v1/requests", headers=access(aud="some-other-app", roles=["trustdesk:admin"])).status_code == 401
    assert client.get("/api/v1/requests", headers=access(exp=int(time.time()) - 3600, roles=["trustdesk:admin"])).status_code == 401
    assert client.get("/api/v1/requests", headers=access(iss="https://evil.example.org", roles=["trustdesk:admin"])).status_code == 401
    other = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig", "alg": "RS256"})
    forged = {"Authorization": "Bearer " + jwt.encode({"alg": "RS256", "kid": "k1"}, {"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 60, "roles": ["trustdesk:admin"]}, other)}
    assert client.get("/api/v1/requests", headers=forged).status_code == 401


def test_mcp_needs_the_mcp_permission(client):
    h = {"Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/mcp", headers={**h, **access(roles=["trustdesk:viewer"])}, json=body).status_code != 403
    svc = access(sub="hr-portal", sub_type="service", scope="trustdesk.requests.write")
    assert client.post("/mcp", headers={**h, **svc}, json=body).status_code == 403
    assert client.post("/mcp", headers={**h, **access(sub="x", roles=["otherapp:admin"])}, json=body).status_code == 403


def test_single_sign_on_login_creates_a_role_bound_session(client):
    r = client.get("/auth/login", params={"next": "/#requests"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"{ISS}/oauth/authorize?")
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["http://testserver/auth/callback"] and q["client_id"] == [AUD]
    client.provider.nonce = q["nonce"][0]
    cb = client.get("/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert cb.status_code == 303 and cb.headers["location"] == "/#requests" and "td_session" in cb.cookies
    s = client.get("/api/session").json()
    assert s["state"] == "logged_in" and s["user"]["name"] == "Ada" and s["user"]["roles"] == ["trustdesk:approver"] and s["user"]["source"] == "oidc"
    assert "requests:decide" in s["user"]["permissions"] and "settings:manage" not in s["user"]["permissions"]
    assert s["sso"]["login_url"] == "/auth/login" and s["sso"]["issuer"] == ISS
    assert client.get("/api/v1/requests").status_code == 200
    assert client.get("/api/accounts").status_code == 403                                           # an approver is not an administrator
    out = client.post("/api/logout", headers=CSRF).json()
    assert out["logout_url"].startswith(f"{ISS}/oauth/logout?") and "id_token_hint=" in out["logout_url"]
    assert client.get("/api/v1/requests").status_code == 401


def test_sso_refuses_people_without_a_role_here_and_replayed_or_foreign_logins(client):
    client.provider.id_claims = {"sub": "usr_9", "name": "Nobody", "roles": ["otherapp:admin"]}
    r = client.get("/auth/login", follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    client.provider.nonce = q["nonce"][0]
    cb = client.get("/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert cb.headers["location"].startswith("/?sso_error=") and "td_session" not in cb.cookies
    assert client.get("/api/v1/requests").status_code == 401
    again = client.get("/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert again.headers["location"].startswith("/?sso_error=")                                      # the state was single-use
    assert client.get("/auth/callback", params={"error": "access_denied"}, follow_redirects=False).headers["location"].startswith("/?sso_error=")


def test_a_federated_session_does_not_survive_a_local_password_reset_of_other_sessions(client):
    r = client.get("/auth/login", follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    client.provider.nonce = q["nonce"][0]
    client.get("/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert client.get("/api/v1/requests").status_code == 200
    with connect() as c:
        c.execute("DELETE FROM web_session")
        c.commit()
    assert client.get("/api/v1/requests").status_code == 401


def test_sso_endpoints_are_404_when_federation_is_off(client, monkeypatch):
    monkeypatch.setattr(api, "fed", Federation("", AUD, None, None, True))
    assert client.get("/auth/login", follow_redirects=False).status_code == 404
    assert client.get("/auth/callback", follow_redirects=False).status_code == 404
    assert client.get("/api/session").json()["sso"] is None


def test_approvers_may_ask_with_a_narrow_account_list_but_cannot_open_accounts(client, monkeypatch):
    approver, viewer = access(roles=["trustdesk:approver"]), access(sub="usr_2", roles=["trustdesk:viewer"])
    admin = access(sub="usr_3", roles=["trustdesk:admin"])
    with connect() as c:   # inserted directly: the outbound URL guard (DNS) is not what this test is about
        new_id = c.execute("INSERT INTO llm_account (name, provider, base_url, chat_model) VALUES ('local', 'custom', 'https://llm.example/v1', 'chat-1') "
                           "RETURNING id").fetchone()["id"]
        c.commit()
    assert client.get("/api/accounts", headers=admin).status_code == 200
    listed = client.get("/api/ask/accounts", headers=approver)
    assert listed.status_code == 200
    assert listed.json()["accounts"][0] == {"id": new_id, "name": "local", "chat_model": "chat-1"}    # no URL, no key details
    assert set(listed.json()["vault"]) == {"initialized", "unlocked"}
    assert client.get("/api/accounts", headers=approver).status_code == 403                # the full listing stays admin-only
    assert client.post("/api/accounts", headers={**approver, **CSRF}, json={}).status_code == 403
    assert client.delete("/api/accounts/1", headers={**approver, **CSRF}).status_code == 403
    # /api/ask is open to approvers and closed to viewers
    monkeypatch.setattr(api.asker, "answer", lambda conn, q, cfg, limit: {"answer": "ok", "sources": []})
    monkeypatch.setattr(api.accounts, "chat_config_for", lambda conn, ref: None)
    asked = client.post("/api/ask", headers={**approver, **CSRF}, json={"question": "How many vacation days?", "account": str(new_id)})
    assert asked.status_code == 200 and asked.json()["answer"] == "ok"
    assert client.post("/api/ask", headers={**viewer, **CSRF}, json={"question": "How many vacation days?", "account": "env"}).status_code == 403
    assert client.get("/api/ask/accounts", headers=viewer).status_code == 403
    # a read-only service (MCP, search) has no ask permission either
    assert client.get("/api/ask/accounts", headers=access(sub="kb-bot", sub_type="service", scope="trustdesk.kb.read")).status_code == 403


def test_document_upload_and_delete_need_kb_write(client):
    approver, viewer = access(roles=["trustdesk:approver"]), access(sub="usr_2", roles=["trustdesk:viewer"])
    admin = access(sub="usr_3", roles=["trustdesk:admin"])
    files = {"file": ("leave.md", b"# Leave\n\nEmployees get 25 days of leave.\n")}
    for who in (approver, viewer):
        assert client.post("/api/documents", headers={**who, **CSRF}, files=files).status_code == 403
        assert client.delete("/api/documents/1", headers={**who, **CSRF}).status_code == 403
    up = client.post("/api/documents", headers={**admin, **CSRF}, files=files)
    assert up.status_code == 200, up.text
    doc_id = client.get("/api/documents", headers=admin).json()[0]["id"]
    assert client.get("/api/documents", headers=approver).status_code == 200               # reading is open to kb:read
    assert client.delete(f"/api/documents/{doc_id}", headers={**approver, **CSRF}).status_code == 403
    assert client.delete(f"/api/documents/{doc_id}", headers={**admin, **CSRF}).json() == {"deleted": doc_id}
