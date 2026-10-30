"""A client credential sees and changes only what it created; employee data beyond that needs an explicit scope."""
import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("set TEST_DATABASE_URL to run DB tests", allow_module_level=True)

from test_compliance_db import ADMIN, _db, employee, model, tokens, vacation  # noqa: E402, F401

from trustdesk import core  # noqa: E402


def two_clients(client):
    out = {}
    for name in ("alpha", "beta"):
        r = client.post("/api/tokens", headers=ADMIN, json={"name": name, "scope": "client"})
        assert r.status_code == 200, r.text
        out[name] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def emp(client, headers, no):
    body = {"employee_no": no, "name": f"Person {no}", "email": f"{no.lower()}@example.com", "department": "Ops"}
    r = client.post("/api/v1/employees", headers=headers, json=body)
    assert r.status_code == 201, r.text


def test_a_client_reads_only_the_employees_it_created(client):
    c = two_clients(client)
    employee(client, "HR1", "Hidden Hr")                       # created by the approver/admin: HR data the clients did not create
    emp(client, c["alpha"], "A1")
    emp(client, c["beta"], "B1")
    listed = client.get("/api/v1/employees", headers=c["alpha"]).json()
    assert [e["employee_no"] for e in listed["items"]] == ["A1"] and listed["total"] == 1
    assert client.get("/api/v1/employees/A1", headers=c["alpha"]).status_code == 200
    for other in ("B1", "HR1"):
        assert client.get(f"/api/v1/employees/{other}", headers=c["alpha"]).status_code == 404
    assert client.get("/api/v1/employees", headers=c["alpha"], params={"q": "Hidden"}).json()["total"] == 0
    # the approver still sees everyone
    assert client.get("/api/v1/employees", headers=ADMIN).json()["total"] == 3


def test_a_client_changes_only_its_own_employees(client):
    c = two_clients(client)
    emp(client, c["alpha"], "A1")
    emp(client, c["beta"], "B1")
    assert client.patch("/api/v1/employees/A1", headers=c["alpha"], json={"title": "Lead"}).status_code == 200
    assert client.patch("/api/v1/employees/B1", headers=c["alpha"], json={"title": "Pwned"}).status_code == 404
    assert client.get("/api/v1/employees/B1", headers=ADMIN).json()["title"] == ""


@pytest.mark.usefixtures("model")
def test_a_client_reads_corrects_and_withdraws_only_its_own_requests(client):
    c = two_clients(client)
    employee(client, "E1")
    mine = vacation(client, c["alpha"]).json()["ref"]
    theirs = vacation(client, c["beta"]).json()["ref"]
    assert client.get(f"/api/v1/requests/{mine}", headers=c["alpha"]).status_code == 200
    assert client.get(f"/api/v1/requests/{theirs}", headers=c["alpha"]).status_code == 404
    items = client.get("/api/v1/requests", headers=c["alpha"]).json()
    assert [i["ref"] for i in items["items"]] == [mine] and items["total"] == 1
    assert client.put(f"/api/v1/requests/{theirs}", headers=c["alpha"], json={"title": "x"}).status_code == 404
    assert client.post(f"/api/v1/requests/{theirs}/withdraw", headers=c["alpha"]).status_code == 404
    assert client.get(f"/api/v1/requests/{theirs}", headers=ADMIN).json()["status"] != "withdrawn"
    assert client.post(f"/api/v1/requests/{mine}/withdraw", headers=c["alpha"]).json()["status"] == "withdrawn"
    assert client.get("/api/v1/requests", headers=ADMIN).json()["total"] == 2          # the approver sees both


@pytest.mark.usefixtures("model")
def test_another_clients_idempotency_key_never_returns_its_request(client):
    c = two_clients(client)
    employee(client, "E1")
    first = vacation(client, {**c["alpha"], "Idempotency-Key": "same-1"})
    assert first.status_code == 201, first.text
    again = vacation(client, {**c["alpha"], "Idempotency-Key": "same-1"})
    assert again.status_code == 200 and again.json()["ref"] == first.json()["ref"]
    assert vacation(client, {**c["beta"], "Idempotency-Key": "same-1"}).status_code == 409


def test_an_explicit_scope_reads_every_employee(client):
    c = two_clients(client)
    employee(client, "HR1", "Hidden Hr")
    emp(client, c["alpha"], "A1")
    # a service token (identity scope) with employees.read: modelled through the scope table the authenticator uses
    assert core.SCOPE_PERMISSIONS["trustdesk.employees.read"] == frozenset({"employees:read"})
    assert core.SCOPE_PERMISSIONS["trustdesk.employees.write"] == frozenset({"employees:write"})
    from apgkit.auth import Principal

    from trustdesk import api_v1
    client_only = Principal("service", "cpsim-service", frozenset({"requests:client"}))
    with_scope = Principal("service", "reader", frozenset({"requests:client", "employees:read"}))
    person = Principal("session", "ann", frozenset({"requests:client", "requests:read"}))
    assert api_v1._owner(client_only) == "service:cpsim-service" and api_v1._employee_owner(client_only) == "service:cpsim-service"
    assert api_v1._employee_owner(with_scope) is None and api_v1._owner(with_scope) == "service:reader"
    assert api_v1._owner(person) is None and api_v1._employee_owner(person) is None
