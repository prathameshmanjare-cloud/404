"""submit() and resume(): what the HTTP API and the CLI both call."""
from __future__ import annotations

import logging
import time
import uuid

from langgraph.types import Command

from config import get_config
from examples import example_call
from graph import GRAPH
from guard.access import check_access_request, refusal, refusal_message
from guard.identity import UnknownUser, can_approve, get_identity
from guard.mask import MaskingFilter, find_pii, mask, mask_obj
from guard.quota import QUOTA, check_quota
from guard.session import acting_as
from escalation import should_escalate
from policy import advice_for, check_request_intent, evaluate_code_policy
from tickets import TICKETS, TicketNotFound, build_ticket
from policy_js import evaluate_js_policy
from runs import RUNS, NotFound, request_key
from sandbox import run_js_snippet, run_snippet
from state import Controls, PolicyResult, Triage

log = logging.getLogger("codesmith.service")


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mask_all_log_handlers()


def mask_all_log_handlers() -> None:
    """Every handler of every logger masks PII, including the web server's own (uvicorn) loggers."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", *logging.root.manager.loggerDict]
    for name in names:
        logger = logging.getLogger(name)
        for handler in getattr(logger, "handlers", []):
            if not any(isinstance(f, MaskingFilter) for f in handler.filters):
                handler.addFilter(MaskingFilter())


def quota_message(cfg: dict, identity) -> dict:
    limit = int(cfg["quota"].get(identity.role, 0))
    return {
        "kind": "quota",
        "title": "Run limit reached",
        "reason": (f"You have used all {limit} runs allowed for a {identity.role} account "
                   f"({identity.name}, {identity.user_id}). The limit is set per user in config.yaml and "
                   f"checked in code, so it can't be changed from a message."),
        "allowed": "Your earlier requests and answers are still under Your history. Ask an admin to raise the limit.",
        "assurance": "Nothing was sent to the AI model and nothing was run.",
    }


# Finished results that a repeated request may reuse. Failures (unavailable, invalid output) are retried.
REUSABLE = {"done", "answered", "sql", "awaiting_approval", "escalated", "denied", "rejected", "refused"}


def _reused(earlier, req_key: str) -> dict:
    """The earlier reply for a repeated request, marked as reused. Nothing new is generated, run or ticketed."""
    reply = _remask(dict(earlier.result))
    at = time.strftime("%H:%M", time.localtime(earlier.created))
    reply["reused"] = {"run_id": earlier.run_id, "at": earlier.created, "request_key": req_key[:12]}
    note = (f"Same request as at {at}: the earlier result is shown. Nothing was generated or run again "
            f"(request key {req_key[:12]}).")
    reply["report"] = note + "\n" + (reply.get("report") or "")
    return reply


def _denied_request(cfg: dict, identity, request: str, pii: list, intent) -> dict:
    """A destructive request: refused by the policy before any code exists. Counts as a policy denial (R3)."""
    session = RUNS.session(identity.user_id)
    session.denials += 1
    ticket = None
    escalate, why = should_escalate(Triage(), session, cfg["escalation"])
    if escalate:
        ticket = build_ticket(identity.user_id, request, why, None, {}, cfg["tickets"]["sla_hours"]).model_dump()
        session.ticket_opened()
    report = ("Policy: deny - " + "; ".join(intent.reasons) + ".\n"
              "Refused before any code was written. Nothing was generated or executed.\n"
              "Served by: no model (rules in code only).")
    return {"status": "denied", "tier": "none", "request": request, "pii": pii, "route": "python_function",
            "policy": intent.model_dump(), "ticket": ticket, "report": report, "blocked_before_generation": True}


# Text fields of a stored reply that can carry what the user typed. They are masked again on every read,
# so a rule added later (names, addresses, ...) also covers history saved before it existed.
_USER_TEXT_FIELDS = ("request", "report", "answer", "ticket", "triage", "denial")


def _remask(reply: dict) -> dict:
    return {k: (mask_obj(v) if k in _USER_TEXT_FIELDS else v) for k, v in (reply or {}).items()}


def list_my_runs() -> list[dict]:
    """The signed-in caller's past runs. Takes no user argument: identity comes from the session."""
    return [{"run_id": r.run_id, "owner": r.owner, "created": r.created, "reply": _remask(r.result)}
            for r in RUNS.list_runs() if r.result]


def get_my_run(run_id: str) -> dict:
    """One past run of the signed-in caller; "missing" and "not yours" raise the same NotFound."""
    r = RUNS.get_run(run_id)
    return {"run_id": r.run_id, "owner": r.owner, "created": r.created, "reply": _remask(r.result)}


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _reply(thread_id: str, state: dict, interrupted: bool) -> dict:
    status = "awaiting_approval" if interrupted else state.get("status", "done")
    run = state.get("run") or {}
    out = {
        "thread_id": thread_id,
        "status": status,
        "request": state.get("request"),
        "route": state.get("route"),
        "language": state.get("language") if state.get("code") else None,
        "answer": state.get("answer"),
        "denial": state.get("denial"),
        "pii": state.get("pii") or [],
        "code": state.get("code"),
        "tests": state.get("tests"),
        "sql": state.get("sql"),
        "report": state.get("report") if not interrupted else
        "Policy: needs_approval - " + "; ".join((state.get("policy") or {}).get("reasons", []))
        + ".\nWaiting for an approver. Nothing has been executed yet.",
        "tier": state.get("tier", "none"),
        "policy": state.get("policy"),
        "ticket": state.get("ticket"),
        "triage": state.get("triage"),
        "requirements": state.get("requirements"),
        "example": example_call(state.get("code"), state.get("requirements"), state.get("tests"),
                                state.get("language", "python")),
        "run": {k: run.get(k) for k in ("exit_code", "timed_out", "output", "sandbox", "cached")} if run else None,
    }
    out["blocked_before_generation"] = bool(state.get("blocked_before_generation"))
    out["run_key"] = (state.get("idem_key") or "")[:12] or None     # hash of user + code + tests (R2)
    out["advice"] = advice_for((out.get("policy") or {}).get("reasons", [])) if status == "denied" else []
    if out.get("ticket"):
        TICKETS.save(mask_obj(out["ticket"]), thread_id)     # every ticket is kept for the admin's Tickets page
    # Final pass: nothing unmasked leaves the backend, even text a model echoed back.
    for key in ("report", "ticket", "triage", "answer"):
        out[key] = mask_obj(out[key])
    RUNS.update(thread_id, status, out)  # the owner (and admins) can reload it; nobody else
    return out


def _run_graph(payload, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = GRAPH.invoke(payload, config)
    interrupted = bool(result.get("__interrupt__"))
    return _reply(thread_id, result, interrupted)


def submit(user_id: str, text: str, controls: dict | None = None) -> dict:
    cfg = get_config()
    try:
        identity = get_identity(cfg, user_id)
    except UnknownUser as exc:
        raise ServiceError(str(exc), 403) from exc
    # First thing: find, check and mask PII. Nothing below ever sees the raw text; the findings keep
    # only the type and the result of the checks, never the value.
    request, findings = find_pii(text or "")
    request = request.strip()
    pii = [f.to_dict() for f in findings]
    if not request:
        raise ServiceError("empty request")
    ctl = Controls.model_validate(controls or {}).model_dump()
    log.info("submit user=%s request=%s", user_id, request[:200])

    thread_id = uuid.uuid4().hex
    # Rule in code, before any model call: no identity claims, no reaching into other users' data.
    denial = check_access_request(request, identity, cfg)
    if denial:
        log.warning("access refused user=%s reason=%s", identity.user_id, denial)
        RUNS.create(thread_id, identity.user_id, request, keep=cfg["history"]["max_per_user"])
        return _reply(thread_id, {"status": "access_denied", "tier": "none", "request": request, "pii": pii,
                                  "denial": refusal(identity, denial),
                                  "report": refusal_message(identity, denial)}, False)
    # Rule in code, before any model call: a request whose goal is destructive is denied outright.
    intent = check_request_intent(request)
    if intent.decision == "deny":
        log.warning("request denied by policy user=%s reasons=%s", identity.user_id, intent.reasons)
        RUNS.create(thread_id, identity.user_id, request, keep=cfg["history"]["max_per_user"],
                    req_key=request_key(identity.user_id, request, ctl))
        return _reply(thread_id, _denied_request(cfg, identity, request, pii, intent), False)
    # Request-level idempotency: the same request again returns the earlier result. Nothing is generated or
    # run twice and no second ticket is opened. Chaos/outage demos always run fresh.
    req_key = request_key(identity.user_id, request, ctl)
    if not (ctl.get("chaos") or ctl.get("outage")):
        earlier = RUNS.find_recent(identity.user_id, req_key, REUSABLE,
                                   60 * cfg.get("idempotency", {}).get("request_window_minutes", 60))
        if earlier:
            log.info("duplicate request user=%s reuses run=%s", identity.user_id, earlier.run_id)
            return _reused(earlier, req_key)
    if not check_quota(cfg, identity):
        log.warning("quota reached user=%s", identity.user_id)
        RUNS.create(thread_id, identity.user_id, request, keep=cfg["history"]["max_per_user"])
        limit = quota_message(cfg, identity)
        return _reply(thread_id, {"status": "quota_exceeded", "tier": "none", "request": request, "pii": pii,
                                  "denial": limit, "report": f"{limit['title']}.\n{limit['reason']}"}, False)
    RUNS.create(thread_id, identity.user_id, request, keep=cfg["history"]["max_per_user"], req_key=req_key)
    state = {"thread_id": thread_id, "user_id": identity.user_id, "role": identity.role, "request": request,
             "controls": ctl, "attempts": 0, "tier": "none", "notes": [], "status": "started", "pii": pii,
             "language": "python"}
    return _run_graph(state, thread_id)


def resume(thread_id: str, user_id: str, decision: str) -> dict:
    cfg = get_config()
    try:
        approver = get_identity(cfg, user_id)
    except UnknownUser as exc:
        raise ServiceError(str(exc), 403) from exc
    if decision not in {"approve", "reject"}:
        raise ServiceError("decision must be approve or reject")
    if not can_approve(cfg, approver):
        raise ServiceError(f"Only an admin can approve or reject code. You are signed in as {approver.name} "
                           f"({approver.user_id}), a {approver.role} account.", 403)
    snapshot = GRAPH.get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot or not snapshot.next or "approval" not in snapshot.next:
        raise ServiceError("unknown thread or nothing waiting for approval (a restart forgets paused runs; "
                           "resubmit the request)", 404)
    log.info("resume thread=%s approver=%s decision=%s", thread_id, user_id, decision)
    return _run_graph(Command(resume={"decision": decision, "approver": approver.user_id}), thread_id)


MAX_SNIPPET_CHARS = 5000


def execute(thread_id: str, user_id: str, snippet: str) -> dict:
    """Run a thread's generated code with the caller's snippet in the sandbox and return the output.

    Allowed only for the thread's owner (or an admin), only when the code passed the policy gate or
    was approved, and only when the snippet itself passes the policy gate. Each run counts toward quota.
    """
    cfg = get_config()
    try:
        caller = get_identity(cfg, user_id)
    except UnknownUser as exc:
        raise ServiceError(str(exc), 403) from exc
    try:
        with acting_as(caller):
            RUNS.get_run(thread_id)
    except NotFound as exc:
        raise ServiceError(str(exc), 404) from exc
    snippet = snippet or ""
    if len(snippet) > MAX_SNIPPET_CHARS:
        raise ServiceError(f"snippet is longer than {MAX_SNIPPET_CHARS} characters")

    values = GRAPH.get_state({"configurable": {"thread_id": thread_id}}).values or {}
    code = values.get("code")
    if not code:
        raise ServiceError("this request produced no Python code to run", 400)
    decision = (values.get("policy") or {}).get("decision")
    if decision == "deny":
        raise ServiceError("the policy gate denied this code, so it cannot be run", 403)
    if decision == "needs_approval" and values.get("approval") != "approved":
        raise ServiceError("this code needs an approver before it can run", 403)

    language = values.get("language", "python")
    evaluate = evaluate_js_policy if language == "javascript" else evaluate_code_policy
    snippet_policy = evaluate(snippet) if snippet.strip() else PolicyResult(
        decision="allow", reasons=["no snippet: the code runs as a script"])
    if snippet_policy.decision != "allow":
        raise ServiceError("the snippet was refused by the policy gate: " + "; ".join(snippet_policy.reasons), 403)
    if not check_quota(cfg, caller):
        raise ServiceError(quota_message(cfg, caller)["title"] + ". " + quota_message(cfg, caller)["reason"], 429)

    sb = cfg["sandbox"]
    log.info("execute thread=%s user=%s snippet=%s", thread_id, user_id, snippet[:200])
    runner = run_js_snippet if language == "javascript" else run_snippet
    result = runner(code, snippet, sb["timeout_s"], sb["max_output_chars"])
    return {"thread_id": thread_id, "exit_code": result.exit_code, "timed_out": result.timed_out,
            "output": mask(result.output), "sandbox": result.sandbox, "policy": snippet_policy.model_dump()}


# ---------------------------------------------------------------- tickets (admins only)
def _require_admin(identity, what: str) -> None:
    if not can_approve(get_config(), identity):
        raise ServiceError(f"Only an admin can {what}. You are signed in as {identity.name} ({identity.user_id}), "
                           f"a {identity.role} account.", 403)


def list_tickets(identity, status: str = "all") -> dict:
    _require_admin(identity, "view tickets")
    return {"tickets": TICKETS.list(status), "counts": TICKETS.counts()}


def set_ticket_status(identity, ticket_id: str, status: str) -> dict:
    _require_admin(identity, "change a ticket's status")
    if status not in TICKETS.STATUSES:
        raise ServiceError("Status must be open or closed.")
    try:
        ticket = TICKETS.set_status(ticket_id, status, identity.user_id)
    except TicketNotFound as exc:
        raise ServiceError(str(exc), 404) from exc
    log.info("ticket %s set to %s by %s", ticket_id, status, identity.user_id)
    return ticket
