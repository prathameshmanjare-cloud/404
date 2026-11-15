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
