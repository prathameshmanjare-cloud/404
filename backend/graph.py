"""LangGraph wiring. Nodes stay thin: models propose, the pure functions decide.

triage -> (escalate | route)
route  -> python_function: requirements -> code -> tests -> policy
          sql: sql -> report        other: refuse -> report
policy -> deny: report | needs_approval: approval (interrupt) -> run | allow: run
run    -> pass: report | fail: repair -> policy (at most max_failed_repairs times)
Any model outage in the code path drops to the template tier, which fails closed.
"""
from __future__ import annotations

import logging
import re

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents import agents
from config import get_config
from escalation import should_escalate
from llm.errors import InvalidOutput, ModelUnavailable
from policy import evaluate_code_policy
from policy_js import evaluate_js_policy
from report import render_report
from runs import RUNS, idempotency_key
from sandbox import run_in_sandbox, run_js_tests
from sql_guard import check_sql
from state import CodeForgeState, PolicyResult, Requirements, Triage
from templates.tasks import TEMPLATES, match_template
from tickets import build_ticket
from triage import triage_request

log = logging.getLogger("codeforge.graph")
CHECK_WORDS = re.compile(r"\b(valid|invalid|correct|wrong|right|check|verify|genuine|real|fake|format)\b", re.I)
TIER_RANK = {"primary": 0, "fallback": 1, "template": 2, "none": 3}


def worse(a: str, b: str) -> str:
    return a if TIER_RANK.get(a, 0) >= TIER_RANK.get(b, 0) else b


def _ticket(state: CodeForgeState, reason: str, triage: Triage | None = None) -> dict:
    cfg = get_config()
    return build_ticket(state["user_id"], state["request"], reason, triage, state.get("controls", {}),
                        cfg["tickets"]["sla_hours"]).model_dump()


# ---------------------------------------------------------------- nodes
def triage_node(state: CodeForgeState) -> dict:
    cfg = get_config()
    tri, tier = triage_request(state["request"], state.get("controls", {}))
    session = RUNS.session(state["user_id"])
    session.sentiment.append(tri.sentiment_score)
    escalate, reason = should_escalate(tri, session, cfg["escalation"], include_outcomes=False)
    out: dict = {"triage": tri.model_dump()}
    if escalate:
        out["ticket"] = _ticket(state, reason, tri)
        session.ticket_opened()
        out["status"] = "escalated"
        out["tier"] = tier
    return out


def route_node(state: CodeForgeState) -> dict:
    try:
        route, tier = agents.route_request(state["request"], state.get("controls", {}))
        if route == "other" and state.get("pii") and CHECK_WORDS.search(state["request"]):
            route = "general"  # rule in code: "is my <ID> valid?" is a question about the user's own data
        return {"route": route, "tier": tier,
                "language": "javascript" if route == "javascript_function" else "python"}
    except ModelUnavailable as exc:
        log.warning("router unavailable: %s", exc)
        # Fail closed: only a known template task continues without a model.
        return {"route": "python_function" if match_template(state["request"]) else "unavailable",
                "tier": "template", "language": "python"}


def requirements_node(state: CodeForgeState) -> dict:
    if state.get("tier") == "template":
        return {}
    try:
        req, tier = agents.write_requirements(state["request"], state.get("controls", {}),
                                              state.get("language", "python"))
        return {"requirements": req.model_dump(), "tier": worse(state.get("tier", "primary"), tier)}
    except ModelUnavailable as exc:
        log.warning("requirements unavailable: %s", exc)
        return {"tier": "template"}


def code_node(state: CodeForgeState) -> dict:
    if state.get("tier") != "template":
        try:
            controls = state.get("controls", {})
            req = Requirements.model_validate(state["requirements"])
            lang = state.get("language", "python")
            code, t1 = agents.write_code(state["request"], req, controls, lang)
            tests, t2 = agents.write_tests(req, controls, lang)
            return {"code": code, "tests": tests, "tier": worse(worse(state.get("tier", "primary"), t1), t2)}
        except InvalidOutput as exc:
            log.warning("model output unusable: %s", exc)
            return {"status": "invalid_output", "notes": state.get("notes", []) + [str(exc)]}
        except ModelUnavailable as exc:
            log.warning("code or test model unavailable: %s", exc)
    # Templates are reviewed Python; a JavaScript request never gets Python code silently.
    name = match_template(state["request"]) if state.get("language", "python") == "python" else None
    if name is None:
        return {"tier": "none", "status": "unavailable",
                "ticket": _ticket(state, "models unavailable and no template for this task")}
    tpl = TEMPLATES[name]
    return {"code": tpl["code"], "tests": tpl["tests"], "tier": "template",
            "notes": state.get("notes", []) + [f"template: {name}"]}


def policy_node(state: CodeForgeState) -> dict:
    evaluate = evaluate_js_policy if state.get("language") == "javascript" else evaluate_code_policy
    code_p = evaluate(state["code"])
    tests_p = evaluate(state["tests"])
    order = {"deny": 0, "needs_approval": 1, "allow": 2}
    decision = min(code_p.decision, tests_p.decision, key=order.get)
    reasons = list(code_p.reasons) if code_p.decision == decision or decision == "allow" else []
    if tests_p.decision != "allow":
        reasons += [f"tests: {r}" for r in tests_p.reasons]
    policy = PolicyResult(decision=decision, reasons=reasons)
    out: dict = {"policy": policy.model_dump()}
    if decision == "deny":
        session = RUNS.session(state["user_id"])
        out["status"] = "denied"
        if any("does not parse" not in r for r in reasons):  # a real policy hit, not broken model output
            session.denials += 1
        cfg = get_config()
        tri = Triage.model_validate(state["triage"]) if state.get("triage") else Triage()
        escalate, reason = should_escalate(tri, session, cfg["escalation"])
        if escalate:
            out["ticket"] = _ticket(state, reason, tri)
            session.ticket_opened()
    elif decision == "needs_approval":
        out["approval"] = "pending"
        out["status"] = "awaiting_approval"
    return out


def approval_node(state: CodeForgeState) -> dict:
    answer = interrupt({"code": state["code"], "tests": state["tests"], "policy": state["policy"]})
    approved = answer.get("decision") == "approve"
    return {"approval": "approved" if approved else "rejected", "approver": answer.get("approver"),
            "status": "running" if approved else "rejected"}


def run_node(state: CodeForgeState) -> dict:
    cfg = get_config()["sandbox"]
    key = idempotency_key(state["user_id"], state["code"], state["tests"])
    cached = RUNS.result_for(key)
    if cached is not None:
        run = dict(cached, cached=True)
    else:
        runner = run_js_tests if state.get("language") == "javascript" else run_in_sandbox
        run = runner(state["code"], state["tests"], cfg["timeout_s"], cfg["max_output_chars"]).model_dump()
        RUNS.store_result(key, run)
    return {"idem_key": key, "run": run, "status": "done"}


def repair_node(state: CodeForgeState) -> dict:
    session = RUNS.session(state["user_id"])
    session.failed_repairs += 1
    req = Requirements.model_validate(state["requirements"])
    try:
        code, tier = agents.repair_code(req, state["code"], state["tests"], state["run"].get("output", ""),
                                        state.get("controls", {}), state.get("language", "python"))
    except ModelUnavailable:
        # Code unchanged: the rerun reuses the stored result through the idempotency key.
        return {"attempts": state.get("attempts", 0) + 1}
    return {"code": code, "attempts": state.get("attempts", 0) + 1, "tier": worse(state.get("tier", "primary"), tier)}


def sql_node(state: CodeForgeState) -> dict:
    try:
        sql, tier = agents.write_sql(state["request"], state.get("controls", {}))
    except ModelUnavailable:
        return {"status": "unavailable", "tier": "none",
                "ticket": _ticket(state, "models unavailable for a SQL request")}
    return {"sql": sql, "policy": check_sql(sql).model_dump(), "status": "sql", "tier": tier}


def refuse_node(state: CodeForgeState) -> dict:
    return {"status": "refused", "notes": state.get("notes", []) + [
        "I can't help with that request. Access and roles come from your account, not from the message, "
        "and I don't change them or show other users' data."]}


def general_node(state: CodeForgeState) -> dict:
    """Non-coding requests: a text answer. The model sees masked text and code-verified PII facts only."""
    try:
        answer, tier = agents.answer_general(state["request"], state.get("pii") or [], state.get("controls", {}))
    except ModelUnavailable as exc:
        log.warning("general answer unavailable: %s", exc)
        return {"status": "unavailable", "tier": "none", "answer": _offline_answer(state)}
    return {"status": "answered", "answer": answer, "tier": tier}


def _offline_answer(state: CodeForgeState) -> str | None:
    """With no model, the code-verified checks can still answer "is my <ID> valid?"."""
    found = state.get("pii") or []
    if not found:
        return None
    lines = ["The assistant is offline, but the built-in checks ran on what you sent:"]
    lines += [f"- {agents.PII_LABEL.get(f['type'], f['type'])}: {f['detail']}." for f in found]
    return "\n".join(lines)


def unavailable_node(state: CodeForgeState) -> dict:
    return {"status": "unavailable", "tier": "none", "answer": _offline_answer(state),
            "ticket": _ticket(state, "models unavailable and no template for this task")}


def escalation_after_run(state: CodeForgeState) -> dict:
    """After the last failed repair, open a ticket if the session rules say so."""
    run = state.get("run") or {}
    session = RUNS.session(state["user_id"])
    if run.get("exit_code") == 0:
        session.succeeded()
        return {}
    if state.get("ticket"):
        return {}
    tri = Triage.model_validate(state["triage"]) if state.get("triage") else Triage()
    escalate, reason = should_escalate(tri, session, get_config()["escalation"])
    if not escalate:
        return {}
    session.ticket_opened()
    return {"ticket": _ticket(state, reason, tri)}


def report_node(state: CodeForgeState) -> dict:
    status = state.get("status") or "done"
    return {"status": status, "report": render_report({**state, "status": status})}


# ---------------------------------------------------------------- edges
def after_triage(state: CodeForgeState) -> str:
    return "report" if state.get("status") == "escalated" else "route"


def after_route(state: CodeForgeState) -> str:
    return {"python_function": "requirements", "javascript_function": "requirements", "sql": "sql",
            "general": "general", "other": "refuse"}.get(state.get("route"), "unavailable")


def after_code(state: CodeForgeState) -> str:
    return "report" if state.get("status") in {"unavailable", "invalid_output"} else "policy"


def after_policy(state: CodeForgeState) -> str:
    decision = (state.get("policy") or {}).get("decision")
    return {"deny": "report", "needs_approval": "approval"}.get(decision, "run")


def after_approval(state: CodeForgeState) -> str:
    return "run" if state.get("approval") == "approved" else "report"


def after_run(state: CodeForgeState) -> str:
    run = state.get("run") or {}
    limit = get_config()["escalation"]["max_failed_repairs"]
    if run.get("exit_code") == 0 or run.get("timed_out") or state.get("tier") == "template":
        return "post_run"
    if state.get("attempts", 0) < limit and state.get("requirements"):
        return "repair"
    return "post_run"


def build_graph():
    g = StateGraph(CodeForgeState)
    for name, fn in [("triage", triage_node), ("route", route_node), ("requirements", requirements_node),
                     ("code", code_node), ("policy", policy_node), ("approval", approval_node),
                     ("run", run_node), ("repair", repair_node), ("post_run", escalation_after_run),
                     ("sql", sql_node), ("refuse", refuse_node), ("unavailable", unavailable_node),
                     ("general", general_node),
                     ("report", report_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", after_triage, ["report", "route"])
    g.add_conditional_edges("route", after_route, ["requirements", "sql", "refuse", "unavailable", "general"])
    g.add_edge("requirements", "code")
    g.add_conditional_edges("code", after_code, ["report", "policy"])
    g.add_conditional_edges("policy", after_policy, ["report", "approval", "run"])
    g.add_conditional_edges("approval", after_approval, ["run", "report"])
    g.add_conditional_edges("run", after_run, ["repair", "post_run"])
    g.add_edge("repair", "policy")
    g.add_edge("post_run", "report")
    for n in ("sql", "refuse", "unavailable", "general"):
        g.add_edge(n, "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=MemorySaver())


GRAPH = build_graph()
