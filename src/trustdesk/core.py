"""This app's instances of the shared blocks (agentpg-kit), and the authorization model of TrustDesk.

Permissions are what the code checks (`requests:decide`); roles (given to people by the identity provider) and scopes
(given to services, local API tokens and the static key) are mapped onto them here, in one place.
"""

from apgkit import metrics as metrics_mod
from apgkit.auth import AppAuth, Authenticator, LocalAuth, from_env
from apgkit.config import env_bool
from apgkit.db import Database
from apgkit.llm import LLMClient
from apgkit.netguard import NetGuard
from apgkit.vault import Vault

from . import __version__

APP = "trustdesk"
COOKIE = "td_session"
CSRF_HEADER = "x-trustdesk-csrf"

db = Database(vector=True)
vault = Vault("TRUSTDESK")
netguard = NetGuard("TRUSTDESK_ALLOW_PRIVATE_LLM_URLS")
llm = LLMClient(netguard, vault)
metrics = metrics_mod.Metrics(APP, __version__)

ALL = frozenset({"*"})
PERMISSIONS = {
    "kb:read": "search and read the knowledge base",
    "kb:write": "upload and delete documents",
    "ask": "ask questions answered by a model (spends provider credit)",
    "requests:client": "client applications: create employees and requests, read their status",
    "requests:read": "see requests, their AI advice and the history",
    "requests:decide": "approve, reject or ask for information; re-run validation",
    "reports:read": "open and download reports",
    "settings:manage": "provider accounts, validation settings, embeddings, tokens, demo data",
    "mcp:use": "use the MCP server",
    "metrics:scrape": "scrape /metrics",
}
ROLE_PERMISSIONS = {
    "admin": ALL,
    "approver": frozenset({"kb:read", "ask", "requests:client", "requests:read", "requests:decide", "reports:read", "mcp:use"}),
    "viewer": frozenset({"kb:read", "mcp:use", "requests:read", "reports:read"}),
}
SCOPE_PERMISSIONS = {
    # local API tokens
    "read": frozenset({"kb:read", "mcp:use"}), "metrics": frozenset({"metrics:scrape"}), "client": frozenset({"requests:client"}), "full": ALL,
    # scopes the identity provider can grant to services
    "trustdesk.kb.read": frozenset({"kb:read", "mcp:use"}), "trustdesk.requests.write": frozenset({"requests:client"}),
    "trustdesk.metrics": frozenset({"metrics:scrape"}),
}
fed = from_env("TRUSTDESK", APP)
APP_AUTH = AppAuth(APP, fed.client_id, ROLE_PERMISSIONS, SCOPE_PERMISSIONS)


def _on_reset(conn) -> None:
    conn.execute("UPDATE llm_account SET api_key_enc = NULL, api_key_last4 = NULL")


local = LocalAuth(APP_AUTH, env_prefix="TRUSTDESK", token_prefix="td_", token_scopes={"read", "metrics", "client", "full"}, vault=vault,
                  on_reset=_on_reset)
auth = Authenticator(APP_AUTH, local, db, cookie_name=COOKIE, verifier=fed.verifier,
                     allow_anonymous=lambda: env_bool("TRUSTDESK_ALLOW_ANONYMOUS"))
public_url_env = "TRUSTDESK_PUBLIC_URL"
