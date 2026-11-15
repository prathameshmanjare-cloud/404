"""render_report: a template filled from real results. The model never writes the report."""
from __future__ import annotations

TIER_TEXT = {
    "primary": "primary model",
    "fallback": "fallback model (the primary was unavailable)",
    "template": "template tier (models unavailable; built-in reviewed code)",
    "none": "no model (rules in code only)",
}


def render_report(state: dict) -> str:
    lines: list[str] = []
    status = state.get("status", "")
    policy = state.get("policy") or {}
    run = state.get("run") or {}
    tier = state.get("tier", "none")

    if status == "escalated":
        t = state.get("ticket") or {}
        lines.append(f"Handed to a person: ticket {t.get('ticket_id')} ({t.get('priority')}, "
                     f"response within {t.get('sla_hours')}h). Reason: {t.get('reason')}.")
    elif status == "refused":
        lines.append(state.get("notes", ["Request refused."])[-1])
    elif status == "unavailable":
        lines.append("Cannot answer fully right now: the models are unavailable and this task has no "
                     "reviewed template. A ticket was opened so a person can follow up.")
    elif status == "answered":
        lines.append("Answered in text. No code was generated or run.")
    elif status == "invalid_output":
        lines.append("The model's answer was not valid Python, even after asking it again. Nothing was executed. "
                     "Please try again or rephrase the request.")
    elif status == "sql":
        lines.append("SQL query written; it was checked but not executed (no database is attached).")
        if policy:
            lines.append(f"SQL guard: {policy.get('decision')} - {'; '.join(policy.get('reasons', []))}.")
    else:
        if policy:
            lines.append(f"Policy: {policy.get('decision')} - {'; '.join(policy.get('reasons', []))}.")
        if status == "denied":
            lines.append("Nothing was executed.")
        elif status == "awaiting_approval":
            lines.append("Waiting for an approver. Nothing has been executed yet.")
        elif status == "rejected":
            lines.append(f"The approver ({state.get('approver')}) rejected the code. Nothing was executed.")
        elif run:
            if run.get("timed_out"):
                lines.append("Tests did not finish: the run was killed at the timeout.")
            elif run.get("exit_code") == 0:
                lines.append("Tests passed (pytest exit code 0).")
            else:
                lines.append(f"Tests failed (pytest exit code {run.get('exit_code')}).")
            if state.get("attempts"):
                lines.append(f"Repair attempts: {state.get('attempts')}.")
            if run.get("cached"):
                lines.append("Same code and tests as an earlier run: the stored result was reused, "
                             "the sandbox did not run again.")
    pii = state.get("pii") or []
    if pii:
        kinds = ", ".join(dict.fromkeys(f["placeholder"] for f in pii))
        lines.append(f"Sensitive data masked before any model saw it: {kinds}.")
    lines.append(f"Served by: {TIER_TEXT.get(tier, tier)}.")
    return "\n".join(lines)
