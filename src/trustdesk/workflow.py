"""Request lifecycle: submit -> (AI pre-validation) -> human decision, with an audit trail.

States: submitted -> pending_review -> approved | rejected, with needs_info (the approver asked the employee
something) and withdrawn on the side. Only a person decides; the AI recommendation is advice stored beside it.
"""
import re

import psycopg
from psycopg.types.json import Jsonb

from . import request_types, textsearch
from .request_types import RequestError

OPEN = ("submitted", "pending_review", "needs_info")
DECIDABLE = ("pending_review", "needs_info")
STATUSES = ("submitted", "pending_review", "needs_info", "approved", "rejected", "withdrawn")
STALE_RUNNING_MINUTES = 10
REF = re.compile(r"^(?:REQ-)?0*(\d{1,12})$", re.I)


class WorkflowError(ValueError):
    pass


class NotFound(WorkflowError):
    pass


class Conflict(WorkflowError):
    """The request is not in a state that allows this action (HTTP 409)."""


def ref(request_id: int) -> str:
    return f"REQ-{request_id:06d}"


def parse_ref(value: str) -> int:
    m = REF.match(value.strip())
    if not m:
        raise NotFound("request not found")
    return int(m.group(1))


_SELECT = (
    "SELECT r.id, r.type, r.kind, r.title, r.description, r.start_date, r.end_date, r.amount, r.currency, "
    "r.details, r.status, r.validation_state, r.source, r.submitted_at, r.decided_at, r.decided_by, "
    "r.decision_note, r.override, r.ai_at_decision, e.employee_no, e.name AS employee_name, "
    "e.department, e.title AS employee_title, e.level, e.country, e.status AS employee_status, "
    "m.employee_no AS manager_no, m.name AS manager_name, "
    "v.recommendation AS ai_recommendation, v.compliance AS ai_compliance, v.state AS ai_state "
    "FROM request r JOIN employee e ON e.id = r.employee_id LEFT JOIN employee m ON m.id = e.manager_id "
    "LEFT JOIN LATERAL (SELECT recommendation, compliance, state FROM validation WHERE request_id = r.id "
    "ORDER BY id DESC LIMIT 1) v ON true "
)


def _shape(row: dict) -> dict:
    rtype = request_types.BY_CODE.get(row["type"])
    wd = request_types.working_days(row["start_date"], row["end_date"]) if row["kind"] in {"leave", "travel"} or (
        rtype and rtype.dates == "range") else None
    out = {
        "id": row["id"], "ref": ref(row["id"]), "type": row["type"], "type_label": rtype.label if rtype else row["type"],
        "kind": row["kind"], "title": row["title"], "description": row["description"],
        "start_date": row["start_date"], "end_date": row["end_date"], "working_days": wd,
        "amount": None if row["amount"] is None else float(row["amount"]), "currency": row["currency"],
        "details": row["details"], "source": row["source"], "status": row["status"], "validation_state": row["validation_state"],
        "submitted_at": row["submitted_at"], "decided_at": row["decided_at"], "decided_by": row["decided_by"],
        "decision_note": row["decision_note"], "override": row["override"],
        "employee": {"employee_no": row["employee_no"], "name": row["employee_name"],
                     "department": row["department"], "title": row["employee_title"], "level": row["level"],
                     "country": row["country"], "status": row["employee_status"], "manager_no": row["manager_no"],
                     "manager_name": row["manager_name"]},
        "ai": {"recommendation": row["ai_recommendation"], "compliance": row["ai_compliance"],
               "state": row["ai_state"]} if row["ai_state"] else None,
    }
    return out


def event(conn, request_id: int, actor: str, action: str, note: str | None = None, data: dict | None = None) -> None:
    conn.execute("INSERT INTO request_event (request_id, actor, action, note, data) VALUES (%s,%s,%s,%s,%s)",
                 (request_id, actor[:60], action, note, Jsonb(data or {})))


def create(conn, payload: dict, *, actor: str, source: str = "api", idempotency_key: str | None = None) -> tuple[dict, bool]:
    """Returns (request, created). The same Idempotency-Key for the same employee returns the original request."""
    emp_no = payload.get("employee_no")
    if not isinstance(emp_no, str):
        raise RequestError("employee_no is required")
    emp = conn.execute("SELECT id, status FROM employee WHERE employee_no=%s", (emp_no,)).fetchone()
    if not emp:
        raise NotFound("employee not found; create the employee first")
    if emp["status"] != "active":
        raise RequestError("this employee is inactive and cannot submit requests")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 80:
        raise RequestError("Idempotency-Key must be 1-80 characters")
    n = request_types.normalize(payload)
    try:
        row = conn.execute(
            "INSERT INTO request (employee_id, type, kind, title, description, start_date, end_date, amount, currency, "
            "details, source, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (emp["id"], n.type, n.kind, n.title, n.description, n.start_date, n.end_date, n.amount, n.currency,
             Jsonb(n.details), source, idempotency_key)).fetchone()
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        existing = conn.execute("SELECT id FROM request WHERE employee_id=%s AND idempotency_key=%s",
                                (emp["id"], idempotency_key)).fetchone()
        return get(conn, existing["id"]), False
    event(conn, row["id"], actor, "submitted", data={"source": source})
    conn.commit()
    return get(conn, row["id"]), True


def get(conn, request_id: int) -> dict:
    row = conn.execute(_SELECT + "WHERE r.id=%s", (request_id,)).fetchone()
    if not row:
        raise NotFound("request not found")
    return _shape(row)


def events(conn, request_id: int) -> list[dict]:
    return conn.execute("SELECT at, actor, action, note, data FROM request_event WHERE request_id=%s ORDER BY id",
                        (request_id,)).fetchall()


def listing(conn, *, employee_no=None, status=None, type_=None, kind=None, date_from=None, date_to=None,
            ai=None, search=None, limit=50, offset=0) -> dict:
    where, args = ["true"], []
    if employee_no:
        where.append("e.employee_no = %s")
        args.append(employee_no)
    if status == "open":
        where.append("r.status = ANY(%s)")
        args.append(list(OPEN))
    elif status in STATUSES:
        where.append("r.status = %s")
        args.append(status)
    if type_:
        where.append("r.type = %s")
        args.append(type_)
    if kind in request_types.KINDS:
        where.append("r.kind = %s")
        args.append(kind)
    if date_from:
        where.append("r.submitted_at::date >= %s")
        args.append(date_from)
    if date_to:
        where.append("r.submitted_at::date <= %s")
        args.append(date_to)
    if ai in {"approve", "reject", "needs_info", "manual"}:
        where.append("v.recommendation = %s")
        args.append(ai)
    if search:
        cond, found = textsearch.ilike_any(conn, ["r.title", "e.name", "e.employee_no"], search)
        where.append(cond)
        args += found
    clause = " AND ".join(where)
    total = conn.execute(
        "SELECT count(*) AS n FROM request r JOIN employee e ON e.id=r.employee_id "
        "LEFT JOIN LATERAL (SELECT recommendation FROM validation WHERE request_id=r.id ORDER BY id DESC LIMIT 1) v ON true "
        f"WHERE {clause}", args).fetchone()["n"]
    rows = conn.execute(_SELECT + f"WHERE {clause} ORDER BY r.submitted_at DESC, r.id DESC LIMIT %s OFFSET %s",
                        (*args, limit, offset)).fetchall()
    return {"total": total, "items": [_shape(r) for r in rows]}


def counts(conn) -> dict:
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM request GROUP BY status")}
    flagged = conn.execute(
        "SELECT count(*) AS n FROM request r JOIN LATERAL (SELECT recommendation FROM validation WHERE request_id=r.id "
        "ORDER BY id DESC LIMIT 1) v ON true WHERE r.status = ANY(%s) AND v.recommendation IN ('reject','needs_info')",
        (list(DECIDABLE),)).fetchone()["n"]
    return {"by_status": {s: by_status.get(s, 0) for s in STATUSES},
            "open": sum(by_status.get(s, 0) for s in OPEN), "ai_flagged": flagged}


def update(conn, request_id: int, payload: dict, *, actor: str) -> dict:
    """The employee answers an approver's question: fields may change, then the request is validated again."""
    cur = conn.execute("SELECT r.status, r.type, e.employee_no FROM request r JOIN employee e ON e.id=r.employee_id "
                       "WHERE r.id=%s", (request_id,)).fetchone()
    if not cur:
        raise NotFound("request not found")
    if cur["status"] != "needs_info":
        raise Conflict("a request can only be edited while the approver is waiting for more information")
    old = conn.execute("SELECT title, description, start_date, end_date, amount, currency, details FROM request "
                       "WHERE id=%s", (request_id,)).fetchone()
    old["amount"] = None if old["amount"] is None else str(old["amount"])
    editable = {"title", "description", "start_date", "end_date", "amount", "currency", "details"}
    merged = {**old, **{k: v for k, v in payload.items() if k in editable},
              "employee_no": cur["employee_no"], "type": cur["type"]}
    n = request_types.normalize(merged)
    note = payload.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 1000):
        raise RequestError("note must be text up to 1000 characters")
    updated = conn.execute(
        "UPDATE request SET title=%s, description=%s, start_date=%s, end_date=%s, amount=%s, currency=%s, details=%s, "
        "status='submitted', validation_state='pending', validation_attempts=0, validation_retryable=true, "
        "updated_at=now() WHERE id=%s AND status='needs_info' RETURNING id",
        (n.title, n.description, n.start_date, n.end_date, n.amount, n.currency, Jsonb(n.details), request_id)).fetchone()
    if not updated:
        conn.rollback()
        raise Conflict("the request changed while you were editing it; reload it")
    event(conn, request_id, actor, "resubmitted", note)
    conn.commit()
    return get(conn, request_id)


def withdraw(conn, request_id: int, *, actor: str, note: str | None = None) -> dict:
    row = conn.execute("UPDATE request SET status='withdrawn', updated_at=now() WHERE id=%s AND status = ANY(%s) "
                       "RETURNING id", (request_id, list(OPEN))).fetchone()
    if not row:
        get(conn, request_id)  # 404 if missing
        raise Conflict("only an open request can be withdrawn")
    event(conn, request_id, actor, "withdrawn", note)
    conn.commit()
    return get(conn, request_id)


ACTIONS = {"approve": "approved", "reject": "rejected", "request_info": "needs_info"}


def decide(conn, request_id: int, action: str, note: str | None, *, actor: str) -> dict:
    """Record the human decision. Rejecting or asking for information needs a note for the employee, and approving
    against an AI 'reject' recommendation needs a written justification (it is flagged as an override)."""
    if action not in ACTIONS:
        raise WorkflowError("action must be approve, reject or request_info")
    note = (note or "").strip() or None
    if note and len(note) > 2000:
        raise WorkflowError("note is longer than 2000 characters")
    cur = conn.execute("SELECT r.status, r.validation_state FROM request r WHERE r.id=%s", (request_id,)).fetchone()
    if not cur:
        raise NotFound("request not found")
    if cur["status"] == "submitted":
        raise Conflict("AI pre-validation is still running for this request; wait for it, or run it again")
    if cur["status"] not in DECIDABLE:
        raise Conflict(f"this request is already {cur['status']}")
    if action == "request_info" and cur["status"] == "needs_info":
        raise Conflict("information has already been requested; wait for the employee's answer")
    ai = conn.execute("SELECT recommendation FROM validation WHERE request_id=%s ORDER BY id DESC LIMIT 1",
                      (request_id,)).fetchone()
    rec = ai["recommendation"] if ai else None
    if action in {"reject", "request_info"} and not note:
        raise WorkflowError("a note is required: the employee will see why")
    contradicts = (action == "approve" and rec == "reject") or (action == "reject" and rec == "approve")
    if contradicts and (not note or len(note) < 10):
        raise WorkflowError(f"the AI recommended '{rec}'. Explain in the note (10+ characters) why you are deciding "
                            "otherwise; it is recorded as an override")
    final = ACTIONS[action]
    final_row = conn.execute(
        "UPDATE request SET status=%s, decided_at=CASE WHEN %s IN ('approved','rejected') THEN now() END, "
        "decided_by=%s, decision_note=%s, ai_at_decision=%s, override=%s, updated_at=now() "
        "WHERE id=%s AND status = ANY(%s) RETURNING id",
        (final, final, actor[:60], note, rec, contradicts, request_id, list(DECIDABLE))).fetchone()
    if not final_row:
        conn.rollback()
        raise Conflict("the request changed while you were deciding; reload it")
    event(conn, request_id, actor, {"approved": "approved", "rejected": "rejected",
                                    "needs_info": "info_requested"}[final], note,
          {"ai_recommendation": rec, "override": contradicts})
    conn.commit()
    return get(conn, request_id)


def claim_validation(conn, request_id: int) -> bool:
    """Mark validation as running unless another worker holds it (a stale claim is taken over)."""
    row = conn.execute(
        "UPDATE request SET validation_state='running', validation_started_at=now() WHERE id=%s "
        "AND status IN ('submitted','pending_review','needs_info') AND (validation_state <> 'running' OR "
        "validation_started_at < now() - %s * interval '1 minute') RETURNING id",
        (request_id, STALE_RUNNING_MINUTES)).fetchone()
    conn.commit()
    return row is not None


def finish_validation(conn, request_id: int, state: str, *, retryable: bool = True) -> int:
    """Record how validation ended and hand the request to a person (unless it left the queue meanwhile). A failure counts
    as one more attempt (any other ending resets the count); returns the number of failed attempts in a row."""
    failed = state == "failed"
    row = conn.execute(
        "UPDATE request SET validation_state=%s, status=CASE WHEN status='submitted' THEN 'pending_review' ELSE status END, "
        "validation_attempts=CASE WHEN %s THEN validation_attempts + 1 ELSE 0 END, "
        "validation_failed_at=CASE WHEN %s THEN now() END, validation_retryable=CASE WHEN %s THEN %s ELSE true END, "
        "updated_at=now() WHERE id=%s RETURNING validation_attempts",
        (state, failed, failed, failed, retryable, request_id)).fetchone()
    conn.commit()
    return row["validation_attempts"] if row else 0


def reset_attempts(conn, request_ids: list[int]) -> None:
    """A person asked for validation explicitly: the failed attempts so far no longer count against the automatic retry."""
    if request_ids:
        conn.execute("UPDATE request SET validation_attempts=0, validation_retryable=true WHERE id = ANY(%s)", (request_ids,))
        conn.commit()


_WAITING = ("((status='submitted' AND (validation_state='pending' OR (validation_state='running' AND "
            "validation_started_at < now() - %s * interval '1 minute'))) OR "
            "(status IN ('submitted','pending_review') AND validation_state='skipped'))")

# A failed validation is retried after 5, then 15, then 60 minutes (and every 60 after that, if the cap allows it).
RETRY_BACKOFF_MINUTES = (5, 15, 60)
_FAILED_OPEN = "(validation_state='failed' AND status = ANY(%s))"
_FAILED_RETRYABLE = _FAILED_OPEN + " AND validation_retryable AND validation_attempts < %s"
_FAILED_DUE = (_FAILED_RETRYABLE + " AND validation_failed_at < now() - (%s::int[])[LEAST(GREATEST(validation_attempts, 1), "
               f"{len(RETRY_BACKOFF_MINUTES)})] * interval '1 minute'")


def pending_validation_ids(conn, limit: int = 50, older_than_seconds: int = 0, *, failed: str = "none",
                           max_attempts: int = 0) -> list[int]:
    """Requests that still lack an AI verdict (never started, skipped for lack of an account, or a stale claim),
    oldest first. `older_than_seconds` leaves a request that was only just submitted to its own background task.
    `failed`: 'none' leaves failed validations out, 'due' adds those whose automatic retry is due (backoff elapsed, fewer
    than `max_attempts` attempts, failure not permanent), 'all' adds every open request whose validation failed."""
    clause, args = _WAITING, [STALE_RUNNING_MINUTES]
    if failed == "due" and max_attempts > 0:
        clause += " OR " + _FAILED_DUE
        args += [list(OPEN), max_attempts, list(RETRY_BACKOFF_MINUTES)]
    elif failed == "all":
        clause += " OR " + _FAILED_OPEN
        args += [list(OPEN)]
    return [r["id"] for r in conn.execute(
        f"SELECT id FROM request WHERE ({clause}) AND submitted_at < now() - %s * interval '1 second' "
        "ORDER BY submitted_at, id LIMIT %s", (*args, older_than_seconds, limit)).fetchall()]


def waiting_validation_count(conn) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM request WHERE {_WAITING}", (STALE_RUNNING_MINUTES,)).fetchone()["n"]


def failed_validation_counts(conn, max_attempts: int) -> dict:
    """Open requests whose validation failed: how many will be retried automatically, and how many it gave up on (the cap
    was reached, retries are off, or the failure was a permanent error of the request). A person can still re-run them."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE validation_retryable AND validation_attempts < %s) AS retrying, "
        "count(*) FILTER (WHERE NOT (validation_retryable AND validation_attempts < %s)) AS gave_up "
        f"FROM request WHERE {_FAILED_OPEN}", (max_attempts, max_attempts, list(OPEN))).fetchone()
    return {"retrying": row["retrying"], "gave_up": row["gave_up"]}
