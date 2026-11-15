"""submit() and resume(): what the HTTP API and the CLI both call."""
from __future__ import annotations

import logging
import uuid

from langgraph.types import Command

from config import get_config
from examples import example_call
from graph import GRAPH
from guard.identity import UnknownUser, can_approve, get_identity
from guard.mask import MaskingFilter, find_pii, mask, mask_obj
from guard.quota import check_quota
from policy import evaluate_code_policy
from policy_js import evaluate_js_policy
from runs import RUNS, NotFound
from sandbox import run_js_snippet, run_snippet
from state import Controls, PolicyResult

log = logging.getLogger("codeforge.service")


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, MaskingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.addFilter(MaskingFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
    # Final pass: nothing unmasked leaves the backend, even text a model echoed back.
    for key in ("report", "ticket", "triage", "answer"):
        out[key] = mask_obj(out[key])
    RUNS.update(thread_id, status, {k: out[k] for k in ("status", "report", "tier")})
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
    if not check_quota(cfg, identity):
        RUNS.create(thread_id, identity.user_id, request)
        return _reply(thread_id, {"status": "refused", "tier": "none", "request": request,
                                  "pii": pii,
                                  "report": "Run quota reached for this user. No model was called."}, False)
    RUNS.create(thread_id, identity.user_id, request)
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
        raise ServiceError(f"role {approver.role} cannot approve or reject code", 403)
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
        RUNS.get_run(caller, thread_id)
    except NotFound as exc:
        raise ServiceError("run not found", 404) from exc
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
        raise ServiceError("run quota reached for this user", 429)

    sb = cfg["sandbox"]
    log.info("execute thread=%s user=%s snippet=%s", thread_id, user_id, snippet[:200])
    runner = run_js_snippet if language == "javascript" else run_snippet
    result = runner(code, snippet, sb["timeout_s"], sb["max_output_chars"])
    return {"thread_id": thread_id, "exit_code": result.exit_code, "timed_out": result.timed_out,
            "output": mask(result.output), "sandbox": result.sandbox, "policy": snippet_policy.model_dump()}
