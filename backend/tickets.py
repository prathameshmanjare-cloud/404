"""build_ticket (R3): priority, SLA and a handoff note. Everything in it is masked."""
from __future__ import annotations

import logging
import uuid

from agents.agents import handoff_note
from guard.mask import mask
from llm.errors import ModelUnavailable
from state import Ticket, Triage

log = logging.getLogger("codesmith.tickets")


def priority_for(triage: Triage | None, reason: str) -> str:
    if triage and (triage.sentiment == "angry" or triage.urgency == "high"):
        return "P1"
    if (triage and triage.wants_human) or "denial" in reason or "repair" in reason:
        return "P2"
    return "P3"


def build_ticket(user_id: str, request: str, reason: str, triage: Triage | None, controls: dict,
                 sla_hours: dict) -> Ticket:
    request = mask(request)
    try:
        note = handoff_note(request, reason, controls)
    except ModelUnavailable as exc:
        log.warning("handoff model unavailable, using template note: %s", exc)
        note = f"User {user_id} needs help. Reason: {reason}. Their message (masked): {request[:300]}"
    priority = priority_for(triage, reason)
    return Ticket(
        ticket_id="T-" + uuid.uuid4().hex[:8].upper(),
        user_id=user_id,
        priority=priority,
        sla_hours=int(sla_hours[priority]),
        reason=reason,
        handoff_note=mask(note),   # masked again: the model must not undo masking
        request=request,
    )


# =====================================================================================================
# Ticket store: every ticket is kept (independently of the 3-request history), with its status.
# =====================================================================================================
import json as _json      # noqa: E402
import sqlite3            # noqa: E402
import threading          # noqa: E402
import time as _time      # noqa: E402


class TicketNotFound(Exception):
    pass


class TicketStore:
    STATUSES = ("open", "closed")

    def __init__(self, path: str | None = None) -> None:
        from runs import _db_path
        self.path = path or _db_path()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id    TEXT PRIMARY KEY,
                run_id       TEXT,
                user_id      TEXT NOT NULL,
                priority     TEXT NOT NULL,
                sla_hours    INTEGER NOT NULL,
                reason       TEXT NOT NULL,
                handoff_note TEXT NOT NULL,     -- masked
                request      TEXT NOT NULL,     -- masked
                status       TEXT NOT NULL DEFAULT 'open',
                created      REAL NOT NULL,
                closed_at    REAL,
                closed_by    TEXT
            );
        """)
        self._lock = threading.Lock()

    def save(self, ticket: dict, run_id: str | None) -> None:
        """Store a newly opened ticket once (re-saving the same ticket id changes nothing)."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO tickets (ticket_id, run_id, user_id, priority, sla_hours, reason, "
                "handoff_note, request, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (ticket["ticket_id"], run_id, ticket["user_id"], ticket["priority"], int(ticket["sla_hours"]),
                 mask(ticket["reason"]), mask(ticket["handoff_note"]), mask(ticket["request"]), _time.time()))

    def set_status(self, ticket_id: str, status: str, by: str) -> dict:
        if status not in self.STATUSES:
            raise ValueError("status must be open or closed")
        with self._lock:
            closed_at = _time.time() if status == "closed" else None
            cur = self._db.execute("UPDATE tickets SET status = ?, closed_at = ?, closed_by = ? WHERE ticket_id = ?",
                                   (status, closed_at, by if status == "closed" else None, ticket_id))
            if cur.rowcount == 0:
                raise TicketNotFound(f"Ticket {ticket_id} not found.")
        return self.get(ticket_id)

    def get(self, ticket_id: str) -> dict:
        with self._lock:
            row = self._db.execute(self._SELECT + " WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if not row:
            raise TicketNotFound(f"Ticket {ticket_id} not found.")
        return self._view(row)

    def list(self, status: str = "all") -> list[dict]:
        sql, args = self._SELECT, ()
        if status in self.STATUSES:
            sql, args = sql + " WHERE status = ?", (status,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created DESC", args).fetchall()
        return [self._view(r) for r in rows]

    def counts(self) -> dict:
        with self._lock:
            rows = dict(self._db.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status").fetchall())
        return {"open": rows.get("open", 0), "closed": rows.get("closed", 0)}

    def reset(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tickets")

    _SELECT = ("SELECT ticket_id, run_id, user_id, priority, sla_hours, reason, handoff_note, request, status, "
               "created, closed_at, closed_by FROM tickets")

    @staticmethod
    def _view(row, now: float | None = None) -> dict:
        """A ticket plus its response-time facts: when it is due, how long is left, how long it took."""
        (ticket_id, run_id, user_id, priority, sla_hours, reason, note, request, status, created, closed_at,
         closed_by) = row
        now = now or _time.time()
        due = created + sla_hours * 3600
        end = closed_at if status == "closed" and closed_at else now
        return {
            "ticket_id": ticket_id, "run_id": run_id, "user_id": user_id, "priority": priority,
            "sla_hours": sla_hours, "reason": reason, "handoff_note": note, "request": request,
            "status": status, "created": created, "due": due, "closed_at": closed_at, "closed_by": closed_by,
            "age_minutes": round((end - created) / 60, 1),            # open: waiting so far; closed: time to close
            "minutes_left": round((due - now) / 60, 1) if status == "open" else None,
            "within_sla": end <= due,
        }


TICKETS = TicketStore()
