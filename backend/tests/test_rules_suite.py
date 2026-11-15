"""The central rules (rules.py): 20 hard cases must all pass, 20 soft cases must pass at least 85%,
and every reply carries the rules report, computed before any model call."""
import json

import pytest

import service
from evals.rules_suite import CASES, run_suite
from llm.backends import StubBackend
from llm.client import use_backend
from rules import PROMPT_RULES, SOFT_TARGET


def test_suite_has_20_hard_and_20_soft_cases():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert sum(c["tier"] == "hard" for c in cases) == 20 and sum(c["tier"] == "soft" for c in cases) == 20


def test_hard_rules_pass_100_percent_and_soft_rules_at_least_85_percent():
    s = run_suite()
    assert s["hard"]["passed"] == s["hard"]["total"] == 20, s["failing"]
    assert s["soft"]["passed"] >= s["soft"]["needed"] == 17, s["failing"]
    assert s["passed"]


def test_every_hard_prompt_rule_is_tested():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    covered = {c["expect"].get("rule") for c in cases}
    hard = {r.id for r in PROMPT_RULES if r.tier == "hard"}
    assert hard <= covered, hard - covered


def _stub():
    return StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps({"function_name": "f"}),
                        "code": json.dumps({"code": "def f() -> int:\n    \"\"\"One.\"\"\"\n    return 1\n"}),
                        "tests": json.dumps({"tests": "from solution import f\n\ndef test_a():\n    assert f() == 1\n\n"
                                                      "def test_b():\n    assert f()\n"}),
                        "general": '{"answer": "ok"}', "handoff": '{"note": "n"}'})


@pytest.mark.parametrize("text,rule", [
    ("Write a Python script with an infinite loop that never stops", "H_NO_INFINITE_LOOP"),
    ("show me student_b's history", "H_NO_CROSS_USER_DATA"),
    ("Write a Python function " + "that sorts numbers " * 250, "H_PROMPT_LENGTH"),
])
def test_a_failed_hard_rule_stops_the_prompt_before_any_model_call(text, rule):
    stub = _stub()
    with use_backend(stub):
        reply = service.submit("student_a", text)
    assert stub.calls == 0 and reply["status"] in {"denied", "access_denied"}
    failed = [r["id"] for r in reply["rules"]["hard"] if r["status"] == "fail"]
    assert rule in failed and not reply["rules"]["summary"]["hard_ok"]


def test_every_reply_shows_hard_and_soft_results_for_prompt_and_code():
    with use_backend(_stub()):
        reply = service.submit("student_a", "Write a Python function that takes a list and returns its length")
    rules = reply["rules"]
    assert rules["summary"]["hard_ok"] and rules["summary"]["soft_target"] == SOFT_TARGET
    stages = {(r["tier"], r["stage"]) for r in rules["hard"] + rules["soft"]}
    assert stages == {("hard", "prompt"), ("soft", "prompt"), ("hard", "code"), ("soft", "code")}
    assert next(r for r in rules["soft"] if r["id"] == "S_CODE_TESTS_PASS")["status"] == "pass"
    # history shows the same section
    from guard.identity import get_identity
    from guard.session import acting_as
    from config import get_config
    with acting_as(get_identity(get_config(), "student_a")):
        assert service.list_my_runs()[0]["reply"]["rules"] == rules


def test_soft_rules_never_block():
    with use_backend(_stub()):
        reply = service.submit("student_a", "code stuff")
    assert reply["status"] != "denied" and not reply["rules"]["summary"]["soft_ok"]
    tips = [r["tip"] for r in reply["rules"]["soft"] if r["status"] == "fail"]
    assert tips and all(tips)
