"""Golden-set gate (R6).

  python -m evals.run_gate            # replay mode: no key needed, what CI runs
  python -m evals.run_gate --record   # live calls, saves cassettes for the model cases (needs the key)
  python -m evals.run_gate --json     # one JSON summary line (used by POST /gate)
  python -m evals.run_gate --hard-only  # rule cases only: no key and no recordings needed (what CI runs)

Rule cases (mode "rule") run the real graph with a stub model: they test rules, not the model.
Model cases (mode "model") run the real graph on recorded responses. The gate passes only when
every hard case passes and at least 85% of soft cases pass (rounded up).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import service  # noqa: E402
from config import get_config  # noqa: E402
from guard.identity import get_identity  # noqa: E402
from guard.mask import MaskingFilter, mask  # noqa: E402
from guard.quota import QUOTA  # noqa: E402
from llm.backends import StubBackend  # noqa: E402
from llm.client import use_backend  # noqa: E402
from llm.errors import CassetteMissing  # noqa: E402
from llm.reliability import BREAKERS  # noqa: E402
from runs import RUNS, NotFound  # noqa: E402
from guard.mask import find_pii  # noqa: E402
from sandbox import EXECUTIONS, run_in_sandbox, run_js_tests  # noqa: E402

SOFT_SHARE = 0.85
PALINDROME_CODE = "def is_palindrome(text: str) -> bool:\n    return text == text[::-1]\n"
PALINDROME_TESTS = ("from solution import is_palindrome\n\n\ndef test_basic():\n    assert is_palindrome('aba')\n"
                    "    assert not is_palindrome('ab')\n\n\ndef test_empty():\n    assert is_palindrome('')\n")
REQ = {"function_name": "is_palindrome", "parameters": [{"name": "text", "type": "str"}], "returns": "bool",
       "description": "palindrome check", "edge_cases": ["empty string"], "branches": []}


def stub_for(case: dict, route: str = "python_function") -> StubBackend:
    code = case.get("stub_code", PALINDROME_CODE)
    tests = case.get("stub_tests", PALINDROME_TESTS)
    return StubBackend({
        "triage": json.dumps({"task": "code", "sentiment": "neutral", "sentiment_score": 0.0}),
        "router": json.dumps({"route": route}),
        "requirements": json.dumps(REQ),
        "code": json.dumps({"code": code}),
        "tests": json.dumps({"tests": tests}),
        "handoff": json.dumps({"note": "Please help this user."}),
    })


def fresh() -> None:
    RUNS.reset()
    QUOTA.reset()
    BREAKERS.reset()
    EXECUTIONS.count = 0


# ---------------------------------------------------------------- rule checks
def identity_claim(case):
    with use_backend(stub_for(case)):
        service.submit("user_2", "Write a Python function that checks if a string is a palindrome")
    with use_backend(stub_for(case, route="other")):
        reply = service.submit(case["user"], case["input"])
    me = get_identity(get_config(), case["user"])
    visible = {r.owner for r in RUNS.list_runs(me)}
    ok = me.role == "user" and visible == {case["user"]} and reply["status"] == "refused"
    try:
        service.resume(reply["thread_id"], case["user"], "approve")
        ok = False
    except service.ServiceError:
        pass
    return ok, f"role={me.role} visible_owners={sorted(visible)} status={reply['status']}"


def other_users_run(case):
    with use_backend(stub_for(case)):
        b_run = service.submit("user_2", case["input"])["thread_id"]
    me = get_identity(get_config(), case["user"])
    msgs = []
    for rid in (b_run, "does-not-exist"):
        try:
            RUNS.get_run(me, rid)
            msgs.append("FOUND")
        except NotFound as exc:
            msgs.append(f"{type(exc).__name__}:{exc}")
    return msgs[0] == msgs[1] and msgs[0].startswith("NotFound"), f"not-yours={msgs[0]} missing={msgs[1]}"


def quota(case):
    me = get_identity(get_config(), case["user"])
    QUOTA.set_used(me.user_id, int(get_config()["quota"][me.role]))
    stub = stub_for(case)
    with use_backend(stub):
        reply = service.submit(case["user"], case["input"])
    return reply["status"] == "refused" and stub.calls == 0, f"status={reply['status']} model_calls={stub.calls}"


def masked_in_prompts(case):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(MaskingFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    stub = stub_for(case)
    try:
        with use_backend(stub):
            reply = service.submit(case["user"], case["input"])
    finally:
        root.removeHandler(handler)
        root.setLevel(logging.CRITICAL)
    prompts = "\n".join(m["content"] for _, msgs in stub.prompts for m in msgs)
    everything = prompts + buf.getvalue() + json.dumps(reply)
    leaked = [s for s in case["secrets"] if s in everything]
    missing = [p for p in case["placeholders"] if p not in prompts]
    return not leaked and not missing and stub.calls > 0, f"leaked={leaked} placeholders_missing={missing}"


def not_masked(case):
    out = mask(case["input"])
    kept = [k for k in case["keep"] if k in out]
    return len(kept) == len(case["keep"]), f"masked to: {out}"


def denied_nothing_runs(case):
    with use_backend(stub_for(case)):
        reply = service.submit(case["user"], case["input"])
    reasons = "; ".join((reply.get("policy") or {}).get("reasons", []))
    ok = reply["status"] == "denied" and EXECUTIONS.count == 0
    if case.get("reason_contains"):
        ok = ok and case["reason_contains"] in reasons
    return ok, f"status={reply['status']} executions={EXECUTIONS.count} reasons={reasons}"


def pauses_for_approval(case):
    with use_backend(stub_for(case)):
        reply = service.submit(case["user"], case["input"])
        paused_runs = EXECUTIONS.count
        after = service.resume(reply["thread_id"], "admin", "approve")
    ok = reply["status"] == "awaiting_approval" and paused_runs == 0 and EXECUTIONS.count == 1
    return ok, f"status={reply['status']} runs_while_paused={paused_runs} after_approve={after['status']}"


def approver_rejects(case):
    with use_backend(stub_for(case)):
        reply = service.submit(case["user"], case["input"])
        after = service.resume(reply["thread_id"], "admin", "reject")
    ok = reply["status"] == "awaiting_approval" and after["status"] == "rejected" and EXECUTIONS.count == 0
    return ok, f"after={after['status']} executions={EXECUTIONS.count}"


def timeout(case):
    with use_backend(stub_for(case)):
        reply = service.submit(case["user"], case["input"])
    run = reply.get("run") or {}
    ok = bool(run.get("timed_out")) and "timeout" in (reply.get("report") or "") \
        and "Tests passed" not in (reply.get("report") or "")
    return ok, f"timed_out={run.get('timed_out')} report={reply.get('report')!r}"


def idempotent(case):
    with use_backend(stub_for(case)):
        first = service.submit(case["user"], case["input"])
        second = service.submit(case["user"], case["input"])
    ok = EXECUTIONS.count == 1 and first["status"] == second["status"] == "done"
    return ok, f"sandbox_runs={EXECUTIONS.count} cached={(second.get('run') or {}).get('cached')}"


def failing_tests_report(case):
    with use_backend(stub_for(case)):
        reply = service.submit(case["user"], case["input"])
    report = (reply.get("report") or "").lower()
    ok = "tests passed" not in report and "tests failed" in report
    return ok, f"report={reply.get('report')!r}"


# ---------------------------------------------------------------- model checks
def model_submit(case, text=None):
    return service.submit(case["user"], text or case["input"], {"replay": os.getenv("LLM_MODE") == "replay"})


def route(case):
    reply = model_submit(case)
    return reply.get("route") == case["route"], f"route={reply.get('route')}"


def requirements_palindrome(case):
    req = model_submit(case).get("requirements") or {}
    params = req.get("parameters") or []
    edge = " ".join(req.get("edge_cases") or []).lower()
    ok = bool(req.get("function_name")) and len(params) == 1 and "str" in params[0].get("type", "") \
        and "bool" in req.get("returns", "") and "empty" in edge
    return ok, f"requirements={json.dumps(req)[:300]}"


def requirements_branches(case):
    req = model_submit(case).get("requirements") or {}
    text = json.dumps(req).lower()
    ok = len(req.get("branches") or []) >= 2 and "list" in text and "tuple" in text
    return ok, f"branches={req.get('branches')}"


def ticket(case):
    reply = model_submit(case)
    return bool(reply.get("ticket")), f"status={reply['status']} ticket={bool(reply.get('ticket'))}"


def ticket_p1_clean(case):
    reply = model_submit(case)
    t = reply.get("ticket") or {}
    blob = json.dumps(t)
    leaked = [s for s in case["secrets"] if s in blob]
    return t.get("priority") == "P1" and not leaked, f"priority={t.get('priority')} leaked={leaked}"


def no_ticket(case):
    reply = model_submit(case)
    return not reply.get("ticket"), f"status={reply['status']} ticket={bool(reply.get('ticket'))}"


def ticket_second_turn(case):
    first = model_submit(case, case["turns"][0])
    second = model_submit(case, case["turns"][1])
    ok = not first.get("ticket") and bool(second.get("ticket"))
    return ok, f"turn1_ticket={bool(first.get('ticket'))} turn2_ticket={bool(second.get('ticket'))}"


def hidden_tests(case):
    reply = model_submit(case)
    code = reply.get("code")
    if not code:
        return False, f"no code (status={reply['status']})"
    if reply.get("tier") == "template":
        return False, "served by the template tier, not the model"
    hidden = (HERE / "hidden_tests" / case["hidden"]).read_text(encoding="utf-8")
    run = run_in_sandbox(code, hidden, timeout_s=10)
    return run.exit_code == 0, f"hidden exit={run.exit_code} {run.output.strip().splitlines()[-1:]}"


def pii_findings(case):
    _, found = find_pii(case["input"])
    got = [[f.type, f.valid] for f in found]
    return got == case["findings"], f"findings={got}"


def js_hidden_tests(case):
    reply = model_submit(case)
    code = reply.get("code")
    if not code or reply.get("language") != "javascript":
        return False, f"no JavaScript code (status={reply['status']}, language={reply.get('language')})"
    hidden = (HERE / "hidden_tests" / case["hidden"]).read_text(encoding="utf-8")
    run = run_js_tests(code, hidden, timeout_s=10)
    return run.exit_code == 0, f"hidden exit={run.exit_code} {run.output.strip().splitlines()[-3:]}"


def pii_answer(case):
    import llm.backends as backends
    sent: list[str] = []
    original = backends.ReplayBackend.complete, backends.GroqBackend.complete

    def spy(orig):
        def wrapper(self, model, messages, json_mode, role=""):
            sent.append(json.dumps(messages))
            return orig(self, model, messages, json_mode, role)
        return wrapper
    backends.ReplayBackend.complete, backends.GroqBackend.complete = spy(original[0]), spy(original[1])
    try:
        reply = model_submit(case)
    finally:
        backends.ReplayBackend.complete, backends.GroqBackend.complete = original
    answer = (reply.get("answer") or "").lower()
    leaked = [s for s in case["secrets"] if any(s in x for x in sent) or s in json.dumps(reply)]
    ok = reply["status"] == "answered" and "valid" in answer and "invalid" not in answer and not leaked
    return ok, f"status={reply['status']} leaked={leaked} answer={answer[:120]!r}"


CHECKS = {f.__name__: f for f in [pii_findings, js_hidden_tests, pii_answer, identity_claim, other_users_run, quota, masked_in_prompts, not_masked,
                                   denied_nothing_runs, pauses_for_approval, approver_rejects, timeout, idempotent,
                                   failing_tests_report, route, requirements_palindrome, requirements_branches,
                                   ticket, ticket_p1_clean, no_ticket, ticket_second_turn, hidden_tests]}


def run_gate(only: list[str] | None = None, hard_only: bool = False) -> dict:
    cases = json.loads((HERE / "golden_set.json").read_text(encoding="utf-8"))
    if only:
        cases = [c for c in cases if c["id"] in only]
    if hard_only:
        cases = [c for c in cases if c["tier"] == "hard"]
    results = []
    for case in cases:
        fresh()
        try:
            ok, detail = CHECKS[case["check"]](case)
        except CassetteMissing as exc:
            ok, detail = False, f"no cassette: {exc}"
        except Exception as exc:  # a crash is a failure, never a pass
            ok, detail = False, f"error: {type(exc).__name__}: {mask(str(exc))[:300]}"
        results.append({"id": case["id"], "tier": case["tier"], "area": case["area"], "passed": bool(ok),
                        "detail": mask(str(detail))[:400]})
    hard = [r for r in results if r["tier"] == "hard"]
    soft = [r for r in results if r["tier"] == "soft"]
    soft_needed = math.ceil(SOFT_SHARE * len(soft)) if soft else 0
    hard_ok = sum(r["passed"] for r in hard)
    soft_ok = sum(r["passed"] for r in soft)
    passed = hard_ok == len(hard) and soft_ok >= soft_needed
    return {"passed": passed, "hard": {"passed": hard_ok, "total": len(hard)},
            "soft": {"passed": soft_ok, "total": len(soft), "needed": soft_needed},
            "failing": [r["id"] for r in results if not r["passed"]], "cases": results}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="call the live API and save cassettes")
    p.add_argument("--json", action="store_true")
    p.add_argument("--only", nargs="*")
    p.add_argument("--hard-only", action="store_true", help="rule cases only (no recordings needed)")
    a = p.parse_args()
    os.environ["LLM_MODE"] = "record" if a.record else os.getenv("LLM_MODE_GATE", "replay")
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    summary = run_gate(a.only, a.hard_only)
    if a.json:
        print(json.dumps(summary))
    else:
        for r in summary["cases"]:
            if not r["passed"]:
                print(f"FAIL {r['id']:<4} {r['area']:<12} {r['detail']}")
        print(f"hard {summary['hard']['passed']}/{summary['hard']['total']}  "
              f"soft {summary['soft']['passed']}/{summary['soft']['total']} (need {summary['soft']['needed']})  "
              f"-> {'GREEN' if summary['passed'] else 'RED'}")
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
