"""Compliance API (/api/v1): employees, requests, decisions, reports and validation settings.

Two kinds of caller share these routes. A *client application* holds a `client` token: it can read the request catalogue,
manage employees and submit, read, correct and withdraw requests. It never sees the AI pre-validation or decides
anything. The signed-in approver (browser session or a `full` token) can do everything, including decide.
"""
import contextlib
from datetime import date
from typing import Any

from apgkit.auth import Principal
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, Field

from . import accounts, demo, people, reports, request_types, seedpack, validator, workflow
from .db import connect, connect_vector

PUBLIC_EVENTS = {"submitted", "resubmitted", "withdrawn", "approved", "rejected", "info_requested"}
MAX_BODY = 64 * 1024


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmployeeBody(_Body):
    employee_no: str = Field(max_length=40)
    name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    department: str = Field("", max_length=80)
    title: str = Field("", max_length=80)
    level: str = Field("", max_length=40)
    country: str | None = Field(None, max_length=2)
    manager_no: str | None = Field(None, max_length=40)
    hire_date: str | None = Field(None, max_length=10)
    status: str = Field("active", max_length=10)


class EmployeePatch(_Body):
    name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=254)
    department: str | None = Field(None, max_length=80)
    title: str | None = Field(None, max_length=80)
    level: str | None = Field(None, max_length=40)
    country: str | None = Field(None, max_length=2)
    manager_no: str | None = Field(None, max_length=40)
    hire_date: str | None = Field(None, max_length=10)
    status: str | None = Field(None, max_length=10)


class RequestBody(_Body):
    employee_no: str = Field(max_length=40)
    type: str = Field(max_length=40)
    title: str = Field(max_length=200)
    description: str = Field(max_length=5000)
    start_date: str | None = Field(None, max_length=10)
    end_date: str | None = Field(None, max_length=10)
    amount: float | int | str | None = None
    currency: str | None = Field(None, max_length=3)
    details: dict[str, Any] = Field(default_factory=dict)


class RequestUpdate(_Body):
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    start_date: str | None = Field(None, max_length=10)
    end_date: str | None = Field(None, max_length=10)
    amount: float | int | str | None = None
    currency: str | None = Field(None, max_length=3)
    details: dict[str, Any] | None = None
    note: str | None = Field(None, max_length=1000)


class NoteBody(_Body):
    note: str | None = Field(None, max_length=1000)


class DecisionBody(_Body):
    action: str = Field(pattern="^(approve|reject|request_info)$")
    note: str | None = Field(None, max_length=2000)


class ValidationSettingBody(_Body):
    account: str | None = Field(None, pattern=r"^(env|\d{1,12})$")


def _actor(p: Principal) -> str:
    return p.actor


@contextlib.contextmanager
def _errors():
    """Honest HTTP statuses for domain failures. Messages are written to be safe to show."""
    try:
        yield
    except (people.NotFound, workflow.NotFound) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (people.Conflict, workflow.Conflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (people.PeopleError, workflow.WorkflowError, request_types.RequestError, accounts.AccountError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _request_id(ref: str) -> int:
    try:
        return workflow.parse_ref(ref)
    except workflow.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _public(req: dict) -> dict:
    """What a client application may see: no AI advice and no override flag."""
    return {k: v for k, v in req.items() if k not in {"ai", "override"}}


def _validate_many(ids: list[int]) -> None:
    for i in ids:  # one at a time: be gentle with the provider's rate limits
        with contextlib.suppress(Exception):  # each failure is recorded on its request; the loop must go on
            validator.validate(i)


def build_router(require, idle) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    read_ok = Depends(require("requests:client", "requests:read"))      # a client application, or someone who may see requests
    write_ok = Depends(require("requests:client"))
    count_ok = Depends(require("requests:read"))
    decide_ok = Depends(require("requests:decide"))
    reports_ok = Depends(require("reports:read"))
    admin = Depends(require("settings:manage"))

    def small_body(request: Request) -> None:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            raise HTTPException(413, f"request body is larger than {MAX_BODY // 1024} KB")

    # ---- catalogue ------------------------------------------------------------------------------
    @r.get("/request-types", dependencies=[read_ok])
    def request_types_list():
        return {"types": request_types.catalogue()}

    # ---- employees ------------------------------------------------------------------------------
    @r.post("/employees", status_code=201, dependencies=[write_ok, Depends(small_body)])
    def create_employee(body: EmployeeBody):
        with _errors(), connect() as conn:
            return people.create(conn, body.model_dump())

    @r.get("/employees", dependencies=[read_ok])
    def list_employees(q: str | None = Query(None, max_length=100), status: str | None = Query(None, max_length=10),
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000)):
        with connect() as conn:
            return people.listing(conn, query=q, status=status, limit=limit, offset=offset)

    @r.get("/employees/{employee_no}", dependencies=[read_ok])
    def get_employee(employee_no: str):
        with _errors(), connect() as conn:
            return people.get(conn, employee_no)

    @r.patch("/employees/{employee_no}", dependencies=[write_ok, Depends(small_body)])
    def patch_employee(employee_no: str, body: EmployeePatch):
        with _errors(), connect() as conn:
            return people.update(conn, employee_no, body.model_dump(exclude_unset=True))

    # ---- requests: client side --------------------------------------------------------------------
    @r.post("/requests", status_code=201, dependencies=[Depends(small_body)])
    def submit_request(body: RequestBody, background: BackgroundTasks, response: Response,
                       principal: Principal = write_ok, idempotency_key: str | None = Header(None, max_length=80)):
        with _errors(), connect() as conn:
            req, created = workflow.create(conn, body.model_dump(), actor=_actor(principal), source="api",
                                           idempotency_key=idempotency_key)
        if created:
            background.add_task(validator.validate, req["id"])
        else:
            response.status_code = 200  # the same Idempotency-Key was seen before: nothing new was created
        return req if principal.can("requests:read") else _public(req)

    @r.get("/requests")
    def list_requests(principal: Principal = read_ok, employee_no: str | None = Query(None, max_length=40),
                      status: str | None = Query(None, max_length=20), type: str | None = Query(None, max_length=40),
                      kind: str | None = Query(None, max_length=10), date_from: date | None = Query(None, alias="from"),
                      date_to: date | None = Query(None, alias="to"), ai: str | None = Query(None, max_length=12),
                      q: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0, le=100000)):
        with connect() as conn:
            out = workflow.listing(conn, employee_no=employee_no, status=status, type_=type, kind=kind,
                                   date_from=date_from, date_to=date_to, ai=ai if principal.can("requests:read") else None,
                                   search=q, limit=limit, offset=offset)
        if not principal.can("requests:read"):
            out["items"] = [_public(i) for i in out["items"]]
        return out

    @r.get("/requests/counts", dependencies=[count_ok])
    def request_counts():
        with connect() as conn:
            return workflow.counts(conn)

    @r.post("/requests/validate-pending", status_code=202, dependencies=[decide_ok, Depends(idle)])
    def validate_pending(background: BackgroundTasks):
        with connect() as conn:
            ids = workflow.pending_validation_ids(conn)
        background.add_task(_validate_many, ids)
        return {"queued": len(ids)}

    @r.get("/requests/{ref}")
    def get_request(ref: str, principal: Principal = read_ok):
        rid = _request_id(ref)
        with _errors(), connect() as conn:
            req = workflow.get(conn, rid)
            events = workflow.events(conn, rid)
            if not principal.can("requests:read"):
                events = [{"at": e["at"], "action": e["action"], "note": e["note"]} for e in events
                          if e["action"] in PUBLIC_EVENTS]
                return {**_public(req), "events": events}
            v = validator.latest(conn, rid)
        return {**req, "events": events, "validation": v}

    @r.put("/requests/{ref}", dependencies=[Depends(small_body)])
    def edit_request(ref: str, body: RequestUpdate, background: BackgroundTasks, principal: Principal = write_ok):
        rid = _request_id(ref)
        with _errors(), connect() as conn:
            req = workflow.update(conn, rid, body.model_dump(exclude_unset=True), actor=_actor(principal))
        background.add_task(validator.validate, rid)
        return req if principal.can("requests:read") else _public(req)

    @r.post("/requests/{ref}/withdraw")
    def withdraw_request(ref: str, body: NoteBody | None = None, principal: Principal = write_ok):
        rid = _request_id(ref)
        with _errors(), connect() as conn:
            req = workflow.withdraw(conn, rid, actor=_actor(principal), note=body.note if body else None)
        return req if principal.can("requests:read") else _public(req)

    # ---- requests: approver side ------------------------------------------------------------------
    @r.post("/requests/{ref}/validate", status_code=202, dependencies=[decide_ok, Depends(idle)])
    def revalidate(ref: str, background: BackgroundTasks):
        rid = _request_id(ref)
        with _errors(), connect() as conn:
            req = workflow.get(conn, rid)
            if req["status"] not in workflow.OPEN:
                raise workflow.Conflict(f"this request is already {req['status']}")
        background.add_task(validator.validate, rid)
        return {"queued": True}

    @r.post("/requests/{ref}/decision", dependencies=[Depends(small_body)])
    def decide(ref: str, body: DecisionBody, principal: Principal = decide_ok):
        rid = _request_id(ref)
        with _errors(), connect() as conn:
            return workflow.decide(conn, rid, body.action, body.note, actor=_actor(principal))

    # ---- reports ------------------------------------------------------------------------------------
    @r.get("/reports", dependencies=[reports_ok])
    def report_list():
        return {"reports": reports.CATALOGUE}

    @r.get("/reports/{name}", dependencies=[reports_ok])
    def report(name: str, date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
               format: str = Query("json", pattern="^(json|csv)$")):
        if name not in reports.REPORTS:
            raise HTTPException(404, "unknown report")
        with connect() as conn:
            data = reports.run(conn, name, date_from, date_to)
        if format == "csv":
            fname = f"trustdesk-{name}-{date.today().isoformat()}.csv"
            return Response(reports.to_csv(data), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
        return data

    # ---- validation settings and demo data ------------------------------------------------------------
    @r.get("/validation/settings", dependencies=[count_ok])
    def validation_settings():
        with connect() as conn:
            chosen = validator.store.get(conn, validator.SETTING)
            effective = validator.choose_account(conn)
            usable = [{"id": str(a["id"]), "name": a["name"], "chat_model": a["chat_model"]}
                      for a in accounts.list_accounts(conn) if a["chat_model"]]
        env = accounts.env_account()
        if env and env["chat_model"]:
            usable.insert(0, {"id": "env", "name": env["name"], "chat_model": env["chat_model"]})
        return {"account": chosen, "effective": None if effective is None else str(effective), "accounts": usable}

    @r.put("/validation/settings", dependencies=[admin])
    def set_validation_settings(body: ValidationSettingBody):
        with _errors(), connect() as conn:
            validator.set_account(conn, body.account)
        return validation_settings()

    @r.post("/demo", status_code=201, dependencies=[admin, Depends(idle)])
    def load_demo(background: BackgroundTasks):
        """Policies, fictional employees and a mix of good and problematic requests; each new one is pre-validated."""
        with _errors(), connect_vector() as vconn:
            seeded = seedpack.load(vconn)
        with _errors(), connect() as conn:
            result = demo.load(conn)
        background.add_task(_validate_many, result["queued"])
        return {"policies": len([s for s in seeded if s["status"] == "ingested"]), **result,
                "queued": len(result["queued"])}

    return r
