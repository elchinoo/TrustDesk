"""What AgentPG's read-only role (`a1p_readonly`) and its restricted MCP server may read in this database.

Run after every migrate (agentpg-kit `migrate.run(after_migrate=[...])`), because AgentPG's default privileges give the role SELECT
on every new table, and again every 5 minutes while the app runs (`masking.reassert_forever` in the api.py lifespan), because
`agentpg db-users create` and those default privileges re-grant SELECT on base tables after startup. Two kinds of protection, both enforced by the database whatever client connects:

* personal data: the base table is taken away and a masked view `<table>_masked` is granted instead (`apgkit.masking.mask_view`);
* secrets, hashes and ciphertext: the base table is taken away and nothing replaces it (`hide_table`).

Both are idempotent and skip quietly when the role does not exist (plain Postgres, local development, CI) or the table is absent.
Only the table owner can revoke; a revoke that did not take is reported (`denied: False`), never assumed.
"""
import logging

import psycopg
from apgkit import masking

log = logging.getLogger("trustdesk.hardening")

ROLE = masking.READONLY_ROLE

# Keys of request.details that reveal health or family circumstances (bereavement relationship, medical certificate, parental leave kind).
_SENSITIVE_DETAILS = ("relationship", "medical_certificate", "parental_type")
_DETAILS = "details" + "".join(f" - '{k}'" for k in _SENSITIVE_DETAILS)

# table -> (view, {column: rule}). Columns not listed are not exposed. Request text (title, description, notes) stays readable on
# purpose: an assistant answering "which requests are flagged and why" needs it; the employee identity is what gets masked.
MASKED = {
    "employee": ("employee_masked", {
        "id": "pass", "employee_no": "hash", "name": "initial", "email": "email", "department": "pass", "title": "pass",
        "level": "pass", "country": "pass", "manager_id": "pass", "hire_date": "date_trunc('year', hire_date)::date",
        "status": "pass", "created_at": "pass", "updated_at": "pass"}),
    "request": ("request_masked", {
        "id": "pass", "employee_id": "pass", "type": "pass", "kind": "pass", "title": "pass", "description": "pass",
        "start_date": "pass", "end_date": "pass", "amount": "pass", "currency": "pass", "details": _DETAILS,
        "status": "pass", "validation_state": "pass", "source": "pass", "submitted_at": "pass", "decided_at": "pass",
        "decided_by": "initial", "decision_note": "pass", "ai_at_decision": "pass", "override": "pass", "updated_at": "pass"}),
    "request_event": ("request_event_masked", {
        "id": "pass", "request_id": "pass", "at": "pass", "actor": "initial", "action": "pass", "note": "pass", "data": "pass"}),
}

# Secrets, hashes and ciphertext, with no view: kit settings and the vault's wrapped data key and password hash (app_setting), LLM
# account keys (llm_account), API token hashes (api_token), session cookie hashes and SSO identity (web_session), SSO login state (oidc_login).
HIDDEN = ("app_setting", "llm_account", "api_token", "web_session", "oidc_login")


# Every base table the read-only role must NOT be able to read: the masked ones (a view replaces them) and the hidden ones. The re-assert
# loop (api.py lifespan) counts how many of these the role could read before each re-apply: `masking_drift_tables`.
DENY = (*MASKED, *HIDDEN)


def hide_table(conn: psycopg.Connection, table: str, role: str = ROLE) -> dict:
    """REVOKE SELECT on `table` from the read-only role, without a replacement view (kit `masking.deny_tables`).
    Returns {"applied", "reason", "denied"}."""
    res = masking.deny_tables(conn, [table], readonly_role=role)
    if table in res["skipped"]:
        return {"applied": False, "reason": "table does not exist", "denied": False}
    denied = bool(res["tables"].get(table, False))
    return {"applied": denied, "reason": "" if denied else (res["reason"] or "table still readable by the role"), "denied": denied}


def apply(conn: psycopg.Connection, role: str = ROLE) -> dict:
    """Mask the tables with personal data and hide the ones with secrets. Never raises: a failure is logged and reported so that
    startup survives (the read-only role simply keeps whatever it had)."""
    report: dict = {"masked": {}, "hidden": {}}
    for table, (view, columns) in MASKED.items():
        try:
            if conn.execute("SELECT to_regclass(%s) IS NULL AS missing", (f"public.{table}",)).fetchone()["missing"]:
                continue
            report["masked"][table] = masking.mask_view(conn, table, view, columns, readonly_role=role)
        except Exception as exc:  # noqa: BLE001 - startup must not fail because a privilege change did
            log.warning("masking %s failed: %s", table, exc)
            report["masked"][table] = {"applied": False, "reason": str(exc)[:200]}
            conn.rollback()
    for table in HIDDEN:
        try:
            report["hidden"][table] = hide_table(conn, table, role)
        except Exception as exc:  # noqa: BLE001
            log.warning("hiding %s failed: %s", table, exc)
            report["hidden"][table] = {"applied": False, "reason": str(exc)[:200], "denied": False}
            conn.rollback()
    return report
