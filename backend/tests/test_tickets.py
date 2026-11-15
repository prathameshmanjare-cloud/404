"""Instructor Tickets page: every ticket with priority, response time and open/closed status. Instructors only."""
import json

from fastapi.testclient import TestClient

import main
from llm.backends import StubBackend
from llm.client import use_backend
from tickets import TicketStore

STUDENT_C = ("THIS IS NOT WORKING!!! my key is sk-proj-abc123def456ghi789 and Aadhaar 2345 6789 0124, "
             "my name is Rahul Sharma. get me a human")


def _client():
    return TestClient(main.app)


def _sign_in(client, user_id):
    return {"X-Session-Token": client.post("/session", json={"user_id": user_id}).json()["token"]}


def _stub():
    return StubBackend({
        "triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
        "requirements": json.dumps({"function_name": "f"}),
        "code": json.dumps({"code": "def f():\n    return 1\n"}),
        "tests": json.dumps({"tests": "from solution import f\n\ndef test_f():\n    assert f() == 1\n"}),
        "handoff": json.dumps({"note": "User is blocked and angry; call back first."})})


def test_admin_sees_every_ticket_with_priority_response_time_and_status():
    client = _client()
    with use_backend(_stub()):
        client.post("/submit", json={"text": STUDENT_C}, headers=_sign_in(client, "student_c"))
        client.post("/submit", json={"text": "can I talk to a person please"}, headers=_sign_in(client, "student_a"))
    data = client.get("/tickets", headers=_sign_in(client, "instructor")).json()
    assert data["counts"] == {"open": 2, "closed": 0}
    by_user = {t["user_id"]: t for t in data["tickets"]}
    p1 = by_user["student_c"]
    assert p1["priority"] == "P1" and p1["sla_hours"] == 1 and p1["status"] == "open"
    assert 0 < p1["minutes_left"] <= 60 and p1["within_sla"] is True and p1["due"] > p1["created"]
    assert by_user["student_a"]["priority"] == "P2" and by_user["student_a"]["sla_hours"] == 4


def test_tickets_never_show_personal_data():
    client = _client()
    with use_backend(_stub()):
        client.post("/submit", json={"text": STUDENT_C}, headers=_sign_in(client, "student_c"))
    blob = json.dumps(client.get("/tickets", headers=_sign_in(client, "instructor")).json())
    for raw in ("sk-proj-abc123def456ghi789", "2345 6789 0124", "Rahul Sharma"):
        assert raw not in blob
    assert "[SECRET]" in blob and "[AADHAAR]" in blob and "[NAME]" in blob


def test_admin_can_close_and_reopen():
    client = _client()
    instructor = _sign_in(client, "instructor")
    with use_backend(_stub()):
        client.post("/submit", json={"text": "get me a human"}, headers=_sign_in(client, "student_b"))
    tid = client.get("/tickets", headers=instructor).json()["tickets"][0]["ticket_id"]
    closed = client.post(f"/tickets/{tid}/status", json={"status": "closed"}, headers=instructor).json()
    assert closed["status"] == "closed" and closed["closed_by"] == "instructor" and closed["minutes_left"] is None
    assert closed["age_minutes"] >= 0 and closed["within_sla"] is True
    assert client.get("/tickets?status=open", headers=instructor).json()["tickets"] == []
    assert len(client.get("/tickets?status=closed", headers=instructor).json()["tickets"]) == 1
    reopened = client.post(f"/tickets/{tid}/status", json={"status": "open"}, headers=instructor).json()
    assert reopened["status"] == "open" and reopened["closed_at"] is None
    assert client.post("/tickets/T-NOPE/status", json={"status": "closed"}, headers=instructor).status_code == 404
    assert client.post(f"/tickets/{tid}/status", json={"status": "done"}, headers=instructor).status_code == 400


def test_only_admins_can_see_or_change_tickets():
    client = _client()
    with use_backend(_stub()):
        client.post("/submit", json={"text": "get me a human"}, headers=_sign_in(client, "student_b"))
    user = _sign_in(client, "student_b")
    r = client.get("/tickets", headers=user)
    assert r.status_code == 403 and "Only an instructor can view tickets" in r.json()["detail"]
    tid = client.get("/tickets", headers=_sign_in(client, "instructor")).json()["tickets"][0]["ticket_id"]
    r = client.post(f"/tickets/{tid}/status", json={"status": "closed"}, headers=user)
    assert r.status_code == 403 and "Only an instructor can change" in r.json()["detail"]
    assert client.get("/tickets").status_code == 401                   # not signed in at all


def test_tickets_outlive_the_three_request_history():
    client = _client()
    u = _sign_in(client, "student_a")
    with use_backend(_stub()):
        client.post("/submit", json={"text": "get me a human"}, headers=u)
        for i in range(4):                                             # pushes the ticket's run out of history
            client.post("/submit", json={"text": f"write f number {i}"}, headers=u)
    assert all("human" not in r["reply"]["request"] for r in client.get("/runs", headers=u).json())
    assert len(client.get("/tickets", headers=_sign_in(client, "instructor")).json()["tickets"]) == 1


def test_overdue_and_response_time_are_computed():
    store = TicketStore(":memory:")
    store.save({"ticket_id": "T-1", "user_id": "student_a", "priority": "P1", "sla_hours": 1, "reason": "angry",
                "handoff_note": "n", "request": "r"}, "run-1")
    row = store._db.execute(store._SELECT + " WHERE ticket_id='T-1'").fetchone()
    created = row[9]
    late = TicketStore._view(row, now=created + 2 * 3600)           # two hours later, still open
    assert late["minutes_left"] == -60.0 and late["within_sla"] is False and late["age_minutes"] == 120.0
