"""The catalogue of request types and the validation of a submission. Pure functions, no database.

A request has common fields (title, description, dates, amount) plus a small flat `details` object whose fields
depend on the type. The catalogue is also served to client applications (GET /api/v1/request-types) so they can
build forms without hard-coding anything. `other` accepts any custom label, so nothing a company needs is blocked.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

MAX_DETAILS = 20
MAX_TEXT = 500
MAX_RANGE_DAYS = 366
CURRENCY = re.compile(r"^[A-Z]{3}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class RequestError(ValueError):
    """The submission is malformed. The message is safe to show to the caller."""


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str                       # text | number | bool | choice
    required: bool = False
    choices: tuple[str, ...] = ()
    help: str = ""


@dataclass(frozen=True)
class RType:
    code: str
    label: str
    kind: str                       # leave | travel | expense | other
    dates: str                      # range | single | optional | none
    amount: bool                    # an amount and currency are required
    description: str
    details: tuple[Field, ...] = ()
    topics: tuple[str, ...] = field(default_factory=tuple)   # policy search queries used when validating


def _receipt() -> Field:
    return Field("has_receipt", "Receipt attached", "bool", help="Does the employee hold a receipt or invoice?")


TYPES: tuple[RType, ...] = (
    RType("vacation", "Vacation / annual leave", "leave", "range", False, "Paid time off for rest or personal plans.",
          topics=("vacation annual leave entitlement notice approval", "leave balance carry over blackout periods")),
    RType("sick_leave", "Sick leave", "leave", "range", False, "Time off because of illness or injury.",
          (Field("medical_certificate", "Medical certificate available", "bool"),),
          ("sick leave notification medical certificate", "sick leave paid days entitlement")),
    RType("parental_leave", "Parental leave", "leave", "range", False, "Maternity, paternity or adoption leave.",
          (Field("parental_type", "Type", "choice", True, ("maternity", "paternity", "adoption")),),
          ("parental leave maternity paternity adoption entitlement", "parental leave notice and documentation")),
    RType("bereavement_leave", "Bereavement leave", "leave", "range", False, "Time off after the death of a relative.",
          (Field("relationship", "Relationship", "text", True),),
          ("bereavement leave days by relationship",)),
    RType("unpaid_leave", "Unpaid leave", "leave", "range", False, "Extended time off without pay.",
          topics=("unpaid leave eligibility approval duration",)),
    RType("business_trip", "Business trip", "travel", "range", False, "Approval to travel for work.",
          (Field("destination", "Destination", "text", True), Field("purpose", "Purpose", "text", True),
           Field("transport", "Transport", "choice", False, ("flight", "train", "car", "other")),
           Field("customer", "Customer or event", "text"),
           Field("estimated_cost", "Estimated total cost", "number")),
          ("business travel pre-approval booking rules", "travel class hotel limits per diem")),
    RType("remote_work", "Remote work", "other", "range", False, "Working from another location or country.",
          (Field("location", "Location", "text", True), Field("country", "Country (ISO code)", "text")),
          ("remote work eligibility approval", "working from abroad tax and security rules")),
    RType("overtime", "Overtime", "other", "single", False, "Hours worked beyond the normal schedule.",
          (Field("hours", "Hours", "number", True),),
          ("overtime approval compensation limits",)),
    RType("training_request", "Training or conference", "other", "optional", False, "Approval to attend training.",
          (Field("course", "Course or event", "text", True), Field("provider", "Provider", "text"),
           Field("estimated_cost", "Estimated cost", "number")),
          ("training and conference approval budget",)),
    RType("equipment_request", "Equipment", "other", "none", False, "Request for hardware or a work tool.",
          (Field("item", "Item", "text", True), Field("estimated_cost", "Estimated cost", "number")),
          ("equipment purchase approval limits",)),
    RType("expense_travel", "Expense: travel", "expense", "single", True, "Flights, trains, taxis, lodging, mileage.",
          (Field("category", "Category", "choice", True, ("flight", "train", "taxi", "lodging", "mileage", "other")),
           Field("merchant", "Merchant", "text"), Field("trip_ref", "Trip request reference", "text"), _receipt()),
          ("travel expense reimbursement limits receipts", "lodging and transport expense caps")),
    RType("expense_customer", "Expense: customer", "expense", "single", True,
          "Meals, entertainment or gifts for customers and partners.",
          (Field("customer", "Customer or partner", "text", True), Field("attendees", "Number of attendees", "number"),
           Field("merchant", "Merchant", "text"), Field("business_purpose", "Business purpose", "text", True),
           _receipt()),
          ("customer entertainment expense limits approval", "gifts to customers and anti-bribery rules")),
    RType("expense_meals", "Expense: meals", "expense", "single", True, "Business meals and per diem.",
          (Field("merchant", "Merchant", "text"), Field("attendees", "Number of attendees", "number"), _receipt()),
          ("meal expense limits per person", "per diem meal allowance")),
    RType("expense_medical", "Expense: medical / sick-related", "expense", "single", True,
          "Medical costs related to an illness or injury.",
          (Field("provider", "Provider", "text"), _receipt()),
          ("medical expense reimbursement eligibility", "sick leave related costs reimbursement")),
    RType("expense_training", "Expense: training", "expense", "single", True, "Courses, books, certification fees.",
          (Field("course", "Course or item", "text", True), Field("provider", "Provider", "text"), _receipt()),
          ("training expense reimbursement approval", "certification exam fees reimbursement")),
    RType("expense_equipment", "Expense: equipment", "expense", "single", True, "Hardware, software or office supplies.",
          (Field("item", "Item", "text", True), Field("merchant", "Merchant", "text"), _receipt()),
          ("equipment and supplies reimbursement limits", "purchase approval thresholds")),
    RType("expense_home_office", "Expense: home office", "expense", "single", True, "Home office setup or running costs.",
          (Field("item", "Item", "text", True), _receipt()),
          ("home office allowance and reimbursement",)),
    RType("expense_other", "Expense: other", "expense", "single", True, "Any other business expense.",
          (Field("merchant", "Merchant", "text"), Field("category_note", "What was it for", "text"), _receipt()),
          ("general expense reimbursement rules receipts deadline",)),
    RType("other", "Other request", "other", "optional", False,
          "Anything not covered above. Give it a custom label in `custom_type`.",
          (Field("custom_type", "What kind of request", "text", True),
           Field("estimated_cost", "Estimated cost", "number")),
          ("employee request approval authority", "exceptions to company policy")),
)
BY_CODE = {t.code: t for t in TYPES}
KINDS = ("leave", "travel", "expense", "other")


def catalogue() -> list[dict]:
    """JSON-ready description of every type, for client applications and the web form."""
    return [{
        "code": t.code, "label": t.label, "kind": t.kind, "dates": t.dates, "amount_required": t.amount,
        "description": t.description,
        "details": [{"name": f.name, "label": f.label, "kind": f.kind, "required": f.required,
                     "choices": list(f.choices), "help": f.help} for f in t.details],
    } for t in TYPES]


@dataclass(frozen=True)
class Normalized:
    type: str
    kind: str
    title: str
    description: str
    start_date: date | None
    end_date: date | None
    amount: Decimal | None
    currency: str | None
    details: dict


def _text(value, name: str, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise RequestError(f"{name} must be text")
    value = value.strip()
    if not lo <= len(value) <= hi:
        raise RequestError(f"{name} must be between {lo} and {hi} characters")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise RequestError(f"{name} contains control characters")
    return value


def _date(value, name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        d = value
    else:
        try:
            d = date.fromisoformat(str(value))
        except ValueError as exc:
            raise RequestError(f"{name} must be a date like 2026-07-31") from exc
    today = date.today()
    if d < today - timedelta(days=366 * 5) or d > today + timedelta(days=366 * 3):
        raise RequestError(f"{name} is outside the supported range (5 years back to 3 years ahead)")
    return d


def _amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise RequestError("amount must be a number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise RequestError("amount must be a number") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("10000000"):
        raise RequestError("amount must be between 0 and 10,000,000")
    if amount != amount.quantize(Decimal("0.01")):
        raise RequestError("amount can have at most 2 decimal places")
    return amount.quantize(Decimal("0.01"))


def _detail_value(name: str, value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            raise RequestError(f"details.{name} is not a valid number")
        return value
    if isinstance(value, str):
        value = value.strip()
        if len(value) > MAX_TEXT:
            raise RequestError(f"details.{name} is longer than {MAX_TEXT} characters")
        return value
    raise RequestError(f"details.{name} must be text, a number or true/false (no nested values)")


def normalize(payload: dict) -> Normalized:
    """Validate a submission against the catalogue. Raises RequestError with a caller-safe message."""
    code = payload.get("type")
    rtype = BY_CODE.get(code) if isinstance(code, str) else None
    if rtype is None:
        raise RequestError("unknown request type; see GET /api/v1/request-types")
    title = _text(payload.get("title"), "title", 1, 160)
    description = _text(payload.get("description"), "description", 5, 4000)
    start, end = _date(payload.get("start_date"), "start_date"), _date(payload.get("end_date"), "end_date")

    if rtype.dates == "range" and (start is None or end is None):
        raise RequestError("start_date and end_date are required for this type")
    if rtype.dates == "single":
        if start is None:
            raise RequestError("start_date is required for this type (the date it happened)")
        end = end or start
    if rtype.dates == "none":
        start = end = None
    if rtype.dates == "optional" and (start is None) != (end is None):
        raise RequestError("give both start_date and end_date, or neither")
    if start and end:
        if end < start:
            raise RequestError("end_date is before start_date")
        if (end - start).days > MAX_RANGE_DAYS:
            raise RequestError(f"the date range is longer than {MAX_RANGE_DAYS} days")

    amount = _amount(payload.get("amount"))
    currency = payload.get("currency")
    if currency not in (None, ""):
        if not isinstance(currency, str) or not CURRENCY.match(currency.upper()):
            raise RequestError("currency must be a 3-letter code such as USD")
        currency = currency.upper()
    else:
        currency = None
    if rtype.amount and (amount is None or currency is None):
        raise RequestError("amount and currency are required for this type")
    if amount is None and currency is not None:
        raise RequestError("currency was given without an amount")
    if amount is not None and currency is None:
        raise RequestError("amount needs a currency")

    raw = payload.get("details") or {}
    if not isinstance(raw, dict):
        raise RequestError("details must be an object")
    if len(raw) > MAX_DETAILS:
        raise RequestError(f"details has more than {MAX_DETAILS} fields")
    details: dict = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise RequestError("detail names use lowercase letters, digits and underscores")
        details[key] = _detail_value(key, value)
    for f in rtype.details:
        value = details.get(f.name)
        if f.required and value in (None, ""):
            raise RequestError(f"details.{f.name} is required for {rtype.code}")
        if value is None:
            continue
        if f.kind == "bool" and not isinstance(value, bool):
            raise RequestError(f"details.{f.name} must be true or false")
        if f.kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise RequestError(f"details.{f.name} must be a number")
        if f.kind == "number" and not 0 <= value <= 1_000_000:
            raise RequestError(f"details.{f.name} is out of range")
        if f.kind in {"text", "choice"} and not isinstance(value, str):
            raise RequestError(f"details.{f.name} must be text")
        if f.kind == "choice" and value not in f.choices:
            raise RequestError(f"details.{f.name} must be one of: {', '.join(f.choices)}")
    return Normalized(rtype.code, rtype.kind, title, description, start, end, amount, currency, details)


def working_days(start: date | None, end: date | None) -> int | None:
    """Monday to Friday days in [start, end]. There is no public-holiday calendar, so this is an upper bound."""
    if start is None or end is None:
        return None
    n = (end - start).days + 1
    full, extra = divmod(n, 7)
    days = full * 5
    for i in range(extra):
        if (start + timedelta(days=full * 7 + i)).weekday() < 5:
            days += 1
    return days
