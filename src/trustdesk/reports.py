"""Reports over requests, compliance and decisions. Every report has the same table shape so the web app can render
any of them and the same data downloads as CSV (cells that could be read as spreadsheet formulas are neutralised)."""
import csv
import io
from datetime import date, datetime, timedelta

from . import request_types, workflow

MAX_ROWS = 5000
SLA_DAYS = 5

LATEST = ("LEFT JOIN LATERAL (SELECT * FROM validation WHERE request_id = r.id ORDER BY id DESC LIMIT 1) v ON true")
BASE = f"FROM request r JOIN employee e ON e.id = r.employee_id {LATEST}"


def _period(date_from: date | None, date_to: date | None, column: str = "r.submitted_at::date") -> tuple[str, list]:
    where, args = [], []
    if date_from:
        where.append(f"{column} >= %s")
        args.append(date_from)
    if date_to:
        where.append(f"{column} <= %s")
        args.append(date_to)
    return (" AND ".join(where) or "true"), args


def _col(key: str, label: str, kind: str = "text") -> dict:
    return {"key": key, "label": label, "type": kind}


def _ref(i: int) -> str:
    return f"REQ-{i:06d}"


def _label(code: str) -> str:
    t = request_types.BY_CODE.get(code)
    return t.label if t else code


def _doc_names(citations) -> str:
    names: list[str] = []
    for c in citations or []:
        label = f"{c['title']} (v{c['version']})"
        if label not in names:
            names.append(label)
    return "; ".join(names)


def _result(name, title, description, columns, rows, date_from, date_to, totals=None) -> dict:
    return {"name": name, "title": title, "description": description, "columns": columns,
            "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS, "totals": totals,
            "period": {"from": date_from, "to": date_to}}


def overview(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to)
    rows = conn.execute(
        f"SELECT r.status, r.override, r.ai_at_decision, r.submitted_at, r.decided_at, v.recommendation, v.compliance "
        f"{BASE} WHERE {where}", args).fetchall()
    n = len(rows)
    by = {s: sum(1 for r in rows if r["status"] == s) for s in workflow.STATUSES}
    decided = [r for r in rows if r["status"] in {"approved", "rejected"}]
    hours = [(r["decided_at"] - r["submitted_at"]).total_seconds() / 3600 for r in decided if r["decided_at"]]
    with_ai = [r for r in decided if r["ai_at_decision"] in {"approve", "reject"}]
    agree = [r for r in with_ai if (r["ai_at_decision"] == "approve") == (r["status"] == "approved")]
    validated = [r for r in rows if r["compliance"]]
    nc = [r for r in validated if r["compliance"] == "non_compliant"]
    oldest = conn.execute("SELECT min(submitted_at) AS t FROM request WHERE status IN ('submitted','pending_review','needs_info')"
                          ).fetchone()["t"]
    metrics = [
        ("Requests in period", n), *[(f"  {s.replace('_', ' ')}", v) for s, v in by.items()],
        ("Approval rate (of decided)", f"{100 * by['approved'] / len(decided):.0f}%" if decided else "n/a"),
        ("Average time to decision (hours)", round(sum(hours) / len(hours), 1) if hours else "n/a"),
        ("AI pre-validated", len(validated)),
        ("AI flagged non-compliant", len(nc)),
        ("Decisions that followed the AI advice", f"{100 * len(agree) / len(with_ai):.0f}%" if with_ai else "n/a"),
        ("Decisions against the AI advice (overrides)", sum(1 for r in decided if r["override"])),
        ("Oldest open request (days)", (datetime.now(oldest.tzinfo) - oldest).days if oldest else 0),
    ]
    return _result("overview", "Overview", "Volumes, outcomes and how decisions compare with the AI advice.",
                   [_col("metric", "Measure"), _col("value", "Value", "number")],
                   [{"metric": m, "value": v} for m, v in metrics], date_from, date_to)


def requests_ledger(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to)
    rows = conn.execute(
        f"SELECT r.id, e.employee_no, e.name, e.department, r.type, r.title, r.start_date, r.end_date, r.amount, "
        f"r.currency, r.status, v.recommendation, v.compliance, r.submitted_at, r.decided_at, r.decided_by, r.override "
        f"{BASE} WHERE {where} ORDER BY r.id DESC LIMIT {MAX_ROWS + 1}", args).fetchall()
    out = [{"ref": _ref(r["id"]), "employee_no": r["employee_no"], "employee": r["name"],
            "department": r["department"], "type": _label(r["type"]), "title": r["title"],
            "start": r["start_date"], "end": r["end_date"],
            "amount": None if r["amount"] is None else float(r["amount"]), "currency": r["currency"],
            "status": r["status"], "ai": r["recommendation"], "compliance": r["compliance"],
            "submitted": r["submitted_at"], "decided": r["decided_at"], "decided_by": r["decided_by"],
            "override": "yes" if r["override"] else ""} for r in rows]
    cols = [_col("ref", "Ref"), _col("employee_no", "Employee no."), _col("employee", "Employee"),
            _col("department", "Department"), _col("type", "Type"), _col("title", "Title"),
            _col("start", "From", "date"), _col("end", "To", "date"), _col("amount", "Amount", "number"),
            _col("currency", "Cur."), _col("status", "Status"), _col("ai", "AI advice"),
            _col("compliance", "AI compliance"), _col("submitted", "Submitted", "datetime"),
            _col("decided", "Decided", "datetime"), _col("decided_by", "Decided by"), _col("override", "Override")]
    return _result("requests", "All requests", "Every request in the period, with the AI advice and the decision.",
                   cols, out, date_from, date_to)


def compliance(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to)
    rows = conn.execute(
        f"SELECT r.type, count(*) AS total, count(*) FILTER (WHERE v.compliance='compliant') AS ok, "
        f"count(*) FILTER (WHERE v.compliance='non_compliant') AS bad, "
        f"count(*) FILTER (WHERE v.compliance='unclear') AS unclear, "
        f"count(*) FILTER (WHERE v.id IS NULL OR v.state <> 'ok') AS unvalidated "
        f"{BASE} WHERE {where} GROUP BY r.type ORDER BY total DESC", args).fetchall()
    out = []
    for r in rows:
        judged = r["ok"] + r["bad"]
        out.append({"type": _label(r["type"]), "total": r["total"], "compliant": r["ok"], "non_compliant": r["bad"],
                    "unclear": r["unclear"], "not_validated": r["unvalidated"],
                    "rate": f"{100 * r['ok'] / judged:.0f}%" if judged else "n/a"})
    totals = {k: sum(r[k] for r in out) for k in ("total", "compliant", "non_compliant", "unclear", "not_validated")}
    cols = [_col("type", "Request type"), _col("total", "Requests", "number"), _col("compliant", "Compliant", "number"),
            _col("non_compliant", "Non-compliant", "number"), _col("unclear", "Unclear", "number"),
            _col("not_validated", "Not validated", "number"), _col("rate", "Compliance rate")]
    return _result("compliance", "Compliance by request type",
                   "How requests fared against company policy, as judged by the AI pre-validation.",
                   cols, out, date_from, date_to, totals)


def out_of_compliance(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to)
    rows = conn.execute(
        f"SELECT r.id, e.name, e.department, r.type, r.status, r.override, v.recommendation, v.compliance, "
        f"v.summary, v.citations, v.checks {BASE} WHERE {where} AND r.status <> 'withdrawn' AND "
        f"(v.compliance = 'non_compliant' OR v.recommendation = 'reject' OR "
        f"EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(v.checks, '[]')) c WHERE c->>'severity' = 'fail')) "
        f"ORDER BY r.id DESC LIMIT {MAX_ROWS + 1}", args).fetchall()
    out = []
    for r in rows:
        fails = [c["message"] for c in r["checks"] if c["severity"] == "fail"]
        note = "Approved although flagged" if r["status"] == "approved" else ""
        out.append({"ref": _ref(r["id"]), "employee": r["name"], "department": r["department"],
                    "type": _label(r["type"]), "status": r["status"], "ai": r["recommendation"],
                    "finding": r["summary"], "checks": "; ".join(fails), "policies": _doc_names(r["citations"]),
                    "flag": note})
    cols = [_col("ref", "Ref"), _col("employee", "Employee"), _col("department", "Department"),
            _col("type", "Type"), _col("status", "Status"), _col("ai", "AI advice"), _col("finding", "Finding"),
            _col("checks", "Failed checks"), _col("policies", "Policies cited"), _col("flag", "Flag")]
    return _result("out_of_compliance", "Out of compliance",
                   "Requests the AI judged non-compliant or that failed an automatic check, including any that were "
                   "approved anyway.", cols, out, date_from, date_to)


def overrides(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to, "r.decided_at::date")
    rows = conn.execute(
        f"SELECT r.id, e.name, r.type, r.status, r.ai_at_decision, r.decided_by, r.decision_note, r.decided_at "
        f"{BASE} WHERE {where} AND r.override ORDER BY r.decided_at DESC LIMIT {MAX_ROWS + 1}", args).fetchall()
    out = [{"ref": _ref(r["id"]), "employee": r["name"], "type": _label(r["type"]), "ai": r["ai_at_decision"],
            "decision": r["status"], "decided_by": r["decided_by"], "decided": r["decided_at"],
            "justification": r["decision_note"]} for r in rows]
    cols = [_col("ref", "Ref"), _col("employee", "Employee"), _col("type", "Type"), _col("ai", "AI advised"),
            _col("decision", "Decision"), _col("decided_by", "Decided by"), _col("decided", "Decided", "datetime"),
            _col("justification", "Justification")]
    return _result("overrides", "Decisions against AI advice",
                   "Audit view: every decision that went against the AI recommendation, with the written reason.",
                   cols, out, date_from, date_to)


def expenses(conn, date_from=None, date_to=None) -> dict:
    where, args = _period(date_from, date_to, "r.start_date")
    rows = conn.execute(
        f"SELECT e.department, r.type, r.currency, count(*) AS n, sum(r.amount) AS total, "
        f"coalesce(sum(r.amount) FILTER (WHERE r.status='approved'), 0) AS approved, "
        f"coalesce(sum(r.amount) FILTER (WHERE r.status='rejected'), 0) AS rejected, "
        f"coalesce(sum(r.amount) FILTER (WHERE r.status IN ('submitted','pending_review','needs_info')), 0) AS open "
        f"{BASE} WHERE {where} AND r.kind='expense' AND r.status <> 'withdrawn' "
        f"GROUP BY e.department, r.type, r.currency ORDER BY e.department, r.type", args).fetchall()
    out = [{"department": r["department"] or "(none)", "type": _label(r["type"]), "currency": r["currency"],
            "requests": r["n"], "claimed": float(r["total"]), "approved": float(r["approved"]),
            "rejected": float(r["rejected"]), "open": float(r["open"])} for r in rows]
    cols = [_col("department", "Department"), _col("type", "Expense type"), _col("currency", "Cur."),
            _col("requests", "Requests", "number"), _col("claimed", "Claimed", "number"),
            _col("approved", "Approved", "number"), _col("rejected", "Rejected", "number"),
            _col("open", "Awaiting decision", "number")]
    return _result("expenses", "Expenses by department and type",
                   "Totals per currency (amounts in different currencies are never added together). "
                   "Period is the expense date.", cols, out, date_from, date_to)


def leave(conn, date_from=None, date_to=None) -> dict:
    today = date.today()
    date_from = date_from or date(today.year, 1, 1)
    date_to = date_to or date(today.year, 12, 31)
    where, args = _period(date_from, date_to, "r.start_date")
    rows = conn.execute(
        f"SELECT e.employee_no, e.name, e.department, r.type, r.start_date, r.end_date {BASE} "
        f"WHERE {where} AND r.kind='leave' AND r.status='approved'", args).fetchall()
    per: dict[str, dict] = {}
    for r in rows:
        p = per.setdefault(r["employee_no"], {"employee_no": r["employee_no"], "employee": r["name"],
                                              "department": r["department"], "vacation": 0, "sick": 0, "other": 0})
        days = request_types.working_days(r["start_date"], r["end_date"]) or 0
        p["vacation" if r["type"] == "vacation" else "sick" if r["type"] == "sick_leave" else "other"] += days
    out = sorted(per.values(), key=lambda p: (p["department"], p["employee"]))
    cols = [_col("employee_no", "Employee no."), _col("employee", "Employee"), _col("department", "Department"),
            _col("vacation", "Vacation days", "number"), _col("sick", "Sick days", "number"),
            _col("other", "Other leave days", "number")]
    totals = {k: sum(p[k] for p in out) for k in ("vacation", "sick", "other")}
    return _result("leave", "Approved leave (working days)",
                   "Mon-Fri days of approved leave that start in the period; public holidays are not subtracted.",
                   cols, out, date_from, date_to, totals)


def aging(conn, date_from=None, date_to=None) -> dict:
    rows = conn.execute(
        f"SELECT r.id, e.name, e.department, r.type, r.status, r.validation_state, r.submitted_at, v.recommendation "
        f"{BASE} WHERE r.status IN ('submitted','pending_review','needs_info') ORDER BY r.submitted_at LIMIT {MAX_ROWS + 1}"
    ).fetchall()
    now = datetime.now().astimezone()
    out = []
    for r in rows:
        age = (now - r["submitted_at"]).days
        out.append({"ref": _ref(r["id"]), "employee": r["name"], "department": r["department"],
                    "type": _label(r["type"]), "status": r["status"], "ai": r["recommendation"],
                    "validation": r["validation_state"], "submitted": r["submitted_at"], "age_days": age,
                    "sla": f"over {SLA_DAYS} days" if age > SLA_DAYS else ""})
    cols = [_col("ref", "Ref"), _col("employee", "Employee"), _col("department", "Department"),
            _col("type", "Type"), _col("status", "Status"), _col("ai", "AI advice"),
            _col("validation", "Validation"), _col("submitted", "Submitted", "datetime"),
            _col("age_days", "Age (days)", "number"), _col("sla", "Service level")]
    return _result("aging", "Open requests by age", f"Requests still waiting. Over {SLA_DAYS} days is flagged.",
                   cols, out, None, None)


def absences(conn, date_from=None, date_to=None) -> dict:
    today = date.today()
    date_from = date_from or today
    date_to = date_to or today + timedelta(days=90)
    rows = conn.execute(
        f"SELECT e.name, e.department, r.type, r.start_date, r.end_date {BASE} WHERE r.status='approved' "
        f"AND r.kind IN ('leave','travel') AND r.end_date >= %s AND r.start_date <= %s "
        f"ORDER BY r.start_date, e.name LIMIT {MAX_ROWS + 1}", (date_from, date_to)).fetchall()
    out = [{"employee": r["name"], "department": r["department"], "type": _label(r["type"]), "start": r["start_date"],
            "end": r["end_date"], "working_days": request_types.working_days(r["start_date"], r["end_date"])}
           for r in rows]
    cols = [_col("employee", "Employee"), _col("department", "Department"), _col("type", "Type"),
            _col("start", "From", "date"), _col("end", "To", "date"), _col("working_days", "Working days", "number")]
    return _result("absences", "Upcoming absences and trips",
                   "Approved leave and travel overlapping the period (default: the next 90 days).",
                   cols, out, date_from, date_to)


REPORTS = {
    "overview": overview, "requests": requests_ledger, "compliance": compliance,
    "out_of_compliance": out_of_compliance, "overrides": overrides, "expenses": expenses, "leave": leave,
    "aging": aging, "absences": absences,
}
CATALOGUE = [
    {"name": "overview", "title": "Overview", "uses_period": True},
    {"name": "requests", "title": "All requests", "uses_period": True},
    {"name": "compliance", "title": "Compliance by request type", "uses_period": True},
    {"name": "out_of_compliance", "title": "Out of compliance", "uses_period": True},
    {"name": "overrides", "title": "Decisions against AI advice", "uses_period": True},
    {"name": "expenses", "title": "Expenses by department and type", "uses_period": True},
    {"name": "leave", "title": "Approved leave (working days)", "uses_period": True},
    {"name": "aging", "title": "Open requests by age", "uses_period": False},
    {"name": "absences", "title": "Upcoming absences and trips", "uses_period": True},
]


def run(conn, name: str, date_from: date | None, date_to: date | None) -> dict:
    return REPORTS[name](conn, date_from, date_to)


# ---- CSV --------------------------------------------------------------------------------------------
def _cell(value) -> str:
    if value is None:
        return ""
    text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    # Spreadsheet formula injection: a cell starting with these characters would be evaluated by Excel/Sheets.
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c["label"] for c in report["columns"]])
    for row in report["rows"]:
        w.writerow([_cell(row.get(c["key"])) for c in report["columns"]])
    return buf.getvalue()
