"""should_escalate (R3): pure rules over the triage result and the user's session record."""
from __future__ import annotations

from runs import Session
from state import Triage


def should_escalate(triage: Triage, session: Session, cfg: dict, include_outcomes: bool = True) -> tuple[bool, str]:
    """session.sentiment must already include this turn's score.

    include_outcomes=False is used when a new message arrives: denials and failed repairs escalate at the
    moment they happen (once), and must never block the user's later, unrelated requests.
    """
    if triage.wants_human:
        return True, "the user asked for a person"
    if triage.sentiment == "angry":
        return True, "the user is angry"
    if include_outcomes and session.denials >= cfg["max_denials"]:
        return True, f"{session.denials} policy denials in a row"
    if include_outcomes and session.failed_repairs >= cfg["max_failed_repairs"]:
        return True, f"{session.failed_repairs} failed repair attempts"
    window = session.sentiment[-cfg["rolling_window"]:]
    if len(window) >= cfg["rolling_window"] and sum(window) <= cfg["rolling_threshold"]:
        return True, "the user has been unhappy for several turns"
    return False, ""
