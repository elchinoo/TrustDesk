"""Employees. Client applications own the identity (employee_no); TrustDesk keeps only what policy checks need."""
import re
from datetime import date

import psycopg

EMPLOYEE_NO = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")
COLUMNS = ("employee_no, name, email, department, title, level, country, hire_date, status, created_at, updated_at")


class PeopleError(ValueError):
    pass


class NotFound(PeopleError):
    pass


class Conflict(PeopleError):
    pass


def _text(value, name: str, hi: int, required: bool = False) -> str:
    value = (value or "").strip() if isinstance(value, str) or value is None else None
    if value is None:
        raise PeopleError(f"{name} must be text")
    if required and not value:
        raise PeopleError(f"{name} is required")
    if len(value) > hi or any(ord(c) < 32 for c in value):
        raise PeopleError(f"{name} is too long or has control characters")
    return value


def _clean(data: dict, partial: bool) -> dict:
    out: dict = {}
    if not partial or "employee_no" in data:
        no = data.get("employee_no")
        if not isinstance(no, str) or not EMPLOYEE_NO.match(no):
            raise PeopleError("employee_no must be 1-40 letters, digits, dots, dashes or underscores")
        out["employee_no"] = no
    if not partial or "name" in data:
        out["name"] = _text(data.get("name"), "name", 120, True)
    if not partial or "email" in data:
        email = _text(data.get("email"), "email", 254, True).lower()
        if not EMAIL.match(email):
            raise PeopleError("email is not valid")
        out["email"] = email
    for key, hi in (("department", 80), ("title", 80), ("level", 40)):
        if not partial or key in data:
            out[key] = _text(data.get(key), key, hi)
    if not partial or "country" in data:
        country = data.get("country")
        if country in (None, ""):
            out["country"] = None
        elif isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country):
            out["country"] = country.upper()
        else:
            raise PeopleError("country must be a 2-letter ISO code such as US")
    if not partial or "hire_date" in data:
        hire = data.get("hire_date")
        try:
            out["hire_date"] = None if hire in (None, "") else date.fromisoformat(str(hire))
        except ValueError as exc:
            raise PeopleError("hire_date must be a date like 2024-03-18") from exc
    if "status" in data:
        if data["status"] not in {"active", "inactive"}:
            raise PeopleError("status must be active or inactive")
        out["status"] = data["status"]
    return out


def _manager_id(conn, manager_no) -> int | None:
    if manager_no in (None, ""):
        return None
    row = conn.execute("SELECT id FROM employee WHERE employee_no=%s", (manager_no,)).fetchone()
    if not row:
        raise PeopleError("manager_no does not match any employee")
    return row["id"]


def _shape(conn, row: dict) -> dict:
    mid = row.pop("manager_id", None)
    row["manager_no"] = None
    if mid:
        m = conn.execute("SELECT employee_no FROM employee WHERE id=%s", (mid,)).fetchone()
        row["manager_no"] = m["employee_no"] if m else None
    return row


def get(conn, employee_no: str) -> dict:
    row = conn.execute(f"SELECT id, manager_id, {COLUMNS} FROM employee WHERE employee_no=%s", (employee_no,)).fetchone()
    if not row:
        raise NotFound("employee not found")
    row.pop("id")
    return _shape(conn, row)


def create(conn, data: dict) -> dict:
    vals = _clean(data, partial=False)
    mid = _manager_id(conn, data.get("manager_no"))
    try:
        conn.execute(
            "INSERT INTO employee (employee_no, name, email, department, title, level, country, hire_date, "
            "manager_id, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (vals["employee_no"], vals["name"], vals["email"], vals["department"], vals["title"], vals["level"],
             vals["country"], vals["hire_date"], mid, vals.get("status", "active")))
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise Conflict("an employee with that employee_no or email already exists") from exc
    return get(conn, vals["employee_no"])


def update(conn, employee_no: str, data: dict) -> dict:
    vals = _clean({k: v for k, v in data.items() if k != "employee_no"}, partial=True)
    sets, args = [f"{k}=%s" for k in vals], list(vals.values())
    if "manager_no" in data:
        mid = _manager_id(conn, data["manager_no"])
        row = conn.execute("SELECT id FROM employee WHERE employee_no=%s", (employee_no,)).fetchone()
        if row and mid == row["id"]:
            raise PeopleError("an employee cannot be their own manager")
        sets.append("manager_id=%s")
        args.append(mid)
    if not sets:
        return get(conn, employee_no)
    try:
        row = conn.execute(f"UPDATE employee SET {', '.join(sets)}, updated_at=now() WHERE employee_no=%s RETURNING id",
                           (*args, employee_no)).fetchone()
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise Conflict("another employee already uses that email") from exc
    if not row:
        raise NotFound("employee not found")
    return get(conn, employee_no)


def listing(conn, *, query: str | None, status: str | None, limit: int, offset: int) -> dict:
    where, args = ["true"], []
    if status in {"active", "inactive"}:
        where.append("e.status=%s")
        args.append(status)
    if query:
        where.append("(e.name ILIKE %s OR e.email ILIKE %s OR e.employee_no ILIKE %s OR e.department ILIKE %s)")
        like = "%" + re.sub(r"([\\%_])", r"\\\1", query) + "%"
        args += [like] * 4
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM employee e WHERE {clause}", args).fetchone()["n"]
    rows = conn.execute(
        f"SELECT e.employee_no, e.name, e.email, e.department, e.title, e.level, e.country, e.hire_date, e.status, "
        f"m.employee_no AS manager_no, "
        f"(SELECT count(*) FROM request r WHERE r.employee_id=e.id) AS requests, "
        f"(SELECT count(*) FROM request r WHERE r.employee_id=e.id AND r.status IN ('submitted','pending_review','needs_info')) AS open_requests "
        f"FROM employee e LEFT JOIN employee m ON m.id=e.manager_id WHERE {clause} "
        f"ORDER BY e.name, e.id LIMIT %s OFFSET %s", (*args, limit, offset)).fetchall()
    return {"total": total, "items": rows}
