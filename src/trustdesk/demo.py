"""Fictional demo people and requests, created relative to today so the demo never goes stale.

Everything here is invented (names, e-mail addresses on example.com). Requests marked 'history' are recorded as
already decided so the reports have something to show; the rest are submitted normally and get AI pre-validation.
"""
from datetime import date, timedelta

from . import people, workflow
from .people import Conflict

EMPLOYEES = [
    # no, name, department, title, level, country, hired (days ago), manager
    ("E1000", "Dana Whitfield", "Engineering", "Director of Engineering", "L7", "US", 2900, None),
    ("E1001", "Priya Nair", "Engineering", "Engineering Manager", "L5", "US", 2100, "E1000"),
    ("E1002", "Tom Becker", "Engineering", "Software Engineer", "L3", "US", 700, "E1001"),
    ("E1003", "Lena Fischer", "Engineering", "Senior Engineer", "L4", "DE", 1500, "E1001"),
    ("E1004", "Omar Haddad", "Sales", "Sales Manager", "L5", "US", 1800, None),
    ("E1005", "Grace Liu", "Sales", "Account Executive", "L3", "US", 900, "E1004"),
    ("E1006", "Carlos Mendes", "Finance", "Financial Analyst", "L3", "US", 600, "E1007"),
    ("E1007", "Sofia Rossi", "Finance", "Finance Director", "L6", "IT", 2500, None),
    ("E1008", "Jonas Berg", "Support", "Support Engineer", "L2", "SE", 400, "E1009"),
    ("E1009", "Aiko Tanaka", "Support", "Support Manager", "L4", "JP", 1600, None),
    ("E1010", "Ravi Patel", "Engineering", "Software Engineer", "L2", "US", 40, "E1001"),
]


def _d(today: date, days: int) -> str:
    return (today + timedelta(days=days)).isoformat()


def _next_monday(today: date, at_least: int) -> date:
    d = today + timedelta(days=at_least)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def requests(today: date) -> list[dict]:
    m21, m10, m14, m20, m60 = (_next_monday(today, n) for n in (21, 10, 14, 20, 60))

    def r(key, emp, type_, title, desc, start=None, end=None, amount=None, currency=None, details=None, history=None,
          note=None):
        return {"key": key, "employee_no": emp, "type": type_, "title": title, "description": desc,
                "start_date": start.isoformat() if hasattr(start, "isoformat") else start,
                "end_date": end.isoformat() if hasattr(end, "isoformat") else end,
                "amount": amount, "currency": currency, "details": details or {}, "history": history, "note": note}

    return [
        # ---- history (already decided) ----
        r("h1", "E1002", "vacation", "Summer break", "Five days at the lake.", _d(today, -60), _d(today, -56),
          history="approved"),
        r("h2", "E1003", "sick_leave", "Flu", "Flu, three days in bed.", _d(today, -30), _d(today, -28),
          details={"medical_certificate": False}, history="approved"),
        r("h3", "E1005", "expense_customer", "Dinner with Northwind", "Dinner with two Northwind buyers after the demo.",
          _d(today, -25), amount=240, currency="USD",
          details={"customer": "Northwind", "attendees": 3, "business_purpose": "Contract renewal", "has_receipt": True},
          history="approved"),
        r("h4", "E1006", "expense_training", "SQL certification", "Exam fee for a database certification.",
          _d(today, -20), amount=300, currency="USD", details={"course": "SQL Associate", "has_receipt": True},
          history="approved"),
        r("h5", "E1004", "business_trip", "Chicago customer visit", "Quarterly review with the Chicago customer.",
          m20, m20 + timedelta(days=3), details={"destination": "Chicago", "purpose": "Customer review",
                                                "transport": "flight", "estimated_cost": 2200}, history="approved"),
        r("h6", "E1001", "vacation", "Family trip", "Planned trip with the family.", m60, m60 + timedelta(days=4),
          history="approved"),
        r("h7", "E1008", "expense_other", "Phone case", "Phone case for my personal phone.", _d(today, -14),
          amount=18, currency="USD", details={"merchant": "Phone shop", "has_receipt": False}, history="rejected",
          note="Personal items are not reimbursable under the expense policy."),
        # ---- new requests awaiting validation and a decision ----
        r("n1", "E1002", "vacation", "Two weeks in Portugal", "Two and a half weeks off to visit family in Portugal.",
          m10, m10 + timedelta(days=16)),
        r("n2", "E1003", "vacation", "Long weekend", "Four days off to go hiking.", m21, m21 + timedelta(days=3)),
        r("n3", "E1009", "sick_leave", "Back pain", "Five days off with severe back pain; I could not tell the doctor "
          "in time and have no certificate.", _d(today, -7), _d(today, -3), details={"medical_certificate": False}),
        r("n4", "E1005", "expense_customer", "Team dinner with Contoso",
          "Dinner with three Contoso executives to close the deal.", _d(today, -3), amount=450, currency="USD",
          details={"customer": "Contoso", "attendees": 3, "merchant": "Harbor Grill",
                   "business_purpose": "Closing the Q3 deal", "has_receipt": True}),
        r("n5", "E1006", "expense_travel", "Hotel in Chicago", "One night at the hotel next to the customer office.",
          _d(today, -10), amount=340, currency="USD",
          details={"category": "lodging", "merchant": "Lakeview Hotel", "has_receipt": True}),
        r("n6", "E1000", "business_trip", "London architecture summit",
          "Five days in London to attend the architecture summit and meet two partners.", _next_monday(today, 45),
          _next_monday(today, 45) + timedelta(days=4),
          details={"destination": "London", "purpose": "Conference and partner meetings", "transport": "flight",
                   "estimated_cost": 6500}),
        r("n7", "E1004", "expense_customer", "Gift for a customer", "A 100 dollar gift card for the buyer at Fabrikam "
          "as a thank-you.", _d(today, -5), amount=100, currency="USD",
          details={"customer": "Fabrikam", "business_purpose": "Thank-you gift", "has_receipt": True}),
        r("n8", "E1008", "overtime", "Weekend incident", "Twelve hours on a customer incident this week.",
          _d(today, -2), details={"hours": 12}),
        r("n9", "E1010", "vacation", "Wedding", "Three days off for a friend's wedding.", m14, m14 + timedelta(days=2)),
        r("n10", "E1001", "remote_work", "Working from Lisbon", "Fifteen working days working from my parents' home "
          "in Portugal.", m20, m20 + timedelta(days=20), details={"location": "Lisbon", "country": "PT"}),
        r("n11", "E1002", "expense_equipment", "External monitor", "A 27 inch monitor for my home desk.",
          _d(today, -4), amount=850, currency="USD", details={"item": "27 inch monitor", "merchant": "TechShop",
                                                              "has_receipt": True}),
        r("n12", "E1006", "expense_meals", "Lunch with auditor", "Lunch with the external auditor.", _d(today, -1),
          amount=64, currency="USD", details={"merchant": "Bistro Roma", "attendees": 2, "has_receipt": True}),
        r("n13", "E1006", "expense_meals", "Lunch with auditor (again)", "Lunch with the external auditor.",
          _d(today, -1), amount=64, currency="USD", details={"merchant": "Bistro Roma", "attendees": 2,
                                                             "has_receipt": True}),
        r("n14", "E1009", "other", "Carry over vacation", "Please let me carry 8 unused vacation days into next year "
          "because of the product launch.", details={"custom_type": "Vacation carry-over exception"}),
        r("n15", "E1005", "parental_leave", "Adoption leave", "Eight weeks of leave after the adoption of our son.",
          m60, m60 + timedelta(days=55), details={"parental_type": "adoption"}),
    ]


def load(conn, today: date | None = None) -> dict:
    """Idempotent: employees are matched by number, requests by an idempotency key."""
    today = today or date.today()
    made = 0
    for no, name, dept, title, level, country, hired, _mgr in EMPLOYEES:
        try:
            people.create(conn, {"employee_no": no, "name": name, "email": f"{name.lower().replace(' ', '.')}@example.com",
                                 "department": dept, "title": title, "level": level, "country": country,
                                 "hire_date": (today - timedelta(days=hired)).isoformat()})
            made += 1
        except Conflict:
            pass
    for no, mgr in [(e[0], e[7]) for e in EMPLOYEES if e[7]]:
        people.update(conn, no, {"manager_no": mgr})
    created, queued = 0, []
    for item in requests(today):
        item = dict(item)
        key, history, note = item.pop("key"), item.pop("history"), item.pop("note")
        req, new = workflow.create(conn, item, actor="demo", source="demo", idempotency_key=f"demo-{key}")
        if not new:
            continue
        created += 1
        if history:
            conn.execute(
                "UPDATE request SET status=%s, validation_state='skipped', submitted_at=now() - interval '10 days', "
                "decided_at=now() - interval '8 days', decided_by='demo-approver', decision_note=%s WHERE id=%s",
                (history, note, req["id"]))
            workflow.event(conn, req["id"], "demo-approver", history, note)
            conn.commit()
        else:
            queued.append(req["id"])
    return {"employees": made, "requests": created, "queued": queued}
