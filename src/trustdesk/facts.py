"""Facts the system can compute exactly (so the model never has to guess them) and the automatic checks built on them.

Only data needed to apply a policy is exposed to the model: no name, no email, no employee number.
"""
from datetime import date

from . import request_types

DAY = 86400


def _ymd(d) -> str | None:
    return d.isoformat() if d else None


def compute(conn, request_id: int, today: date | None = None) -> tuple[dict, list[dict]]:
    """Return (facts, checks). Checks are {code, severity: info|warn|fail, message}."""
    today = today or date.today()
    r = conn.execute(
        "SELECT r.*, e.id AS emp_id, e.department, e.title AS emp_title, e.level, e.country, e.hire_date, "
        "e.manager_id, e.employee_no FROM request r JOIN employee e ON e.id=r.employee_id WHERE r.id=%s",
        (request_id,)).fetchone()
    start, end = r["start_date"], r["end_date"]
    rtype = request_types.BY_CODE.get(r["type"])
    facts: dict = {
        "today": today.isoformat(),
        "request_type": r["type"],
        "request_kind": r["kind"],
        "employee": {
            "department": r["department"] or None, "job_title": r["emp_title"] or None, "level": r["level"] or None,
            "country": r["country"],
            "tenure_days_on_request_start": ((start or today) - r["hire_date"]).days if r["hire_date"] else None,
        },
        "dates": {"start": _ymd(start), "end": _ymd(end),
                  "calendar_days": ((end - start).days + 1) if start and end else None,
                  "working_days_mon_fri": request_types.working_days(start, end) if rtype and rtype.dates == "range"
                  else None,
                  "note": "working days exclude weekends only; public holidays are not subtracted"},
        "amount": {"value": float(r["amount"]), "currency": r["currency"]} if r["amount"] is not None else None,
    }
    checks: list[dict] = []

    def add(code: str, severity: str, message: str) -> None:
        checks.append({"code": code, "severity": severity, "message": message})

    if rtype and rtype.dates == "range" and start:
        facts["dates"]["days_notice"] = (start - r["submitted_at"].date()).days
        if start < r["submitted_at"].date() and r["type"] != "sick_leave":
            add("retroactive", "warn", f"the request was submitted {(r['submitted_at'].date() - start).days} days after it started")
    if r["kind"] == "expense" and start:
        late = (r["submitted_at"].date() - start).days
        facts["dates"]["days_since_expense"] = late
        if late < 0:
            add("future_expense", "fail", "the expense date is in the future")

    if start and end and rtype and rtype.dates == "range":
        clash = conn.execute(
            "SELECT id, type, status, start_date, end_date FROM request WHERE employee_id=%s AND id<>%s "
            "AND status IN ('submitted','pending_review','needs_info','approved') AND kind IN ('leave','travel') "
            "AND start_date <= %s AND end_date >= %s AND type NOT IN ('remote_work') ORDER BY start_date",
            (r["emp_id"], request_id, end, start)).fetchall()
        facts["overlapping_requests"] = [{"ref": f"REQ-{c['id']:06d}", "type": c["type"], "status": c["status"],
                                          "start": _ymd(c["start_date"]), "end": _ymd(c["end_date"])} for c in clash]
        for c in clash:
            add("overlap", "fail" if c["status"] == "approved" else "warn",
                f"overlaps {c['status']} request REQ-{c['id']:06d} ({c['type']}, {c['start_date']} to {c['end_date']})")

    if r["kind"] == "leave" and start:
        year = start.year
        taken = conn.execute(
            "SELECT status, coalesce(sum(end_date - start_date + 1), 0) AS days FROM request WHERE employee_id=%s "
            "AND type=%s AND id<>%s AND status IN ('approved','pending_review','submitted','needs_info') "
            "AND extract(year FROM start_date) = %s GROUP BY status", (r["emp_id"], r["type"], request_id, year)).fetchall()
        facts["same_type_calendar_days_in_year"] = {"year": year, "approved": 0, "pending_or_open": 0}
        for t in taken:
            key = "approved" if t["status"] == "approved" else "pending_or_open"
            facts["same_type_calendar_days_in_year"][key] += int(t["days"])
        if r["manager_id"] and end:
            peers = conn.execute(
                "SELECT count(DISTINCT r2.employee_id) AS n FROM request r2 JOIN employee e2 ON e2.id=r2.employee_id "
                "WHERE e2.manager_id=%s AND r2.employee_id<>%s AND r2.kind='leave' AND r2.status='approved' "
                "AND r2.start_date <= %s AND r2.end_date >= %s", (r["manager_id"], r["emp_id"], end, start)).fetchone()
            team = conn.execute("SELECT count(*) AS n FROM employee WHERE manager_id=%s AND status='active'",
                                (r["manager_id"],)).fetchone()
            facts["team"] = {"size": team["n"], "colleagues_on_approved_leave_same_dates": peers["n"]}

    if r["kind"] == "expense" and r["amount"] is not None:
        ytd = conn.execute(
            "SELECT coalesce(sum(amount),0) AS total, count(*) AS n FROM request WHERE employee_id=%s AND type=%s "
            "AND id<>%s AND status='approved' AND currency=%s AND extract(year FROM start_date)=%s",
            (r["emp_id"], r["type"], request_id, r["currency"], (start or today).year)).fetchone()
        facts["approved_same_type_expenses_this_year"] = {"count": ytd["n"], "total": float(ytd["total"]),
                                                          "currency": r["currency"]}
        dup = conn.execute(
            "SELECT id, status FROM request WHERE employee_id=%s AND id<>%s AND type=%s AND amount=%s AND currency=%s "
            "AND start_date=%s AND status <> 'withdrawn' AND status <> 'rejected' ORDER BY id",
            (r["emp_id"], request_id, r["type"], r["amount"], r["currency"], start)).fetchall()
        if dup:
            facts["possible_duplicates"] = [f"REQ-{d['id']:06d}" for d in dup]
            add("duplicate", "warn", "same employee, type, date and amount as " + ", ".join(
                f"REQ-{d['id']:06d} ({d['status']})" for d in dup))
        if r["details"].get("has_receipt") is False:
            add("no_receipt", "warn", "the employee states there is no receipt")
        elif "has_receipt" not in r["details"]:
            add("receipt_unknown", "info", "receipt availability was not stated")

    if r["type"] == "sick_leave" and r["details"].get("medical_certificate") is False and facts["dates"]["calendar_days"]:
        facts["dates"]["no_medical_certificate"] = True
    if end and start and r["type"] in {"vacation", "unpaid_leave"} and facts["dates"]["working_days_mon_fri"] == 0:
        add("no_working_days", "warn", "the dates contain no working days (weekend only)")
    return facts, checks
