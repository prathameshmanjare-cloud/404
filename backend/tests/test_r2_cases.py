"""R2 cases: idempotent requests and runs, no infinite loops, deny cases refused before any code exists."""
import json

import pytest

import service
from llm.backends import StubBackend
from llm.client import use_backend
from runs import RUNS, idempotency_key, request_key
from sandbox import EXECUTIONS
from tickets import TICKETS

TESTS = json.dumps({"tests": "from solution import f\n\ndef test_f():\n    assert f() == 1\n"})


def _stub(code="def f():\n    return 1\n"):
    return StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps({"function_name": "f"}),
                        "code": code if callable(code) else json.dumps({"code": code}), "tests": TESTS,
                        "handoff": '{"note": "n"}'})


# ---------------------------------------------------------------- idempotency
def test_keys_are_hashes_of_user_request_code_and_tests():
    assert idempotency_key("u", "c", "t") == idempotency_key("u", "c", "t") != idempotency_key("v", "c", "t")
    assert request_key("u", "Write  F.") == request_key("u", "write f") != request_key("v", "write f")
    assert request_key("u", "write f", {"chaos": True}) != request_key("u", "write f", {})


def test_same_request_twice_runs_once_and_says_so():
    stub = _stub()
    with use_backend(stub):
        first = service.submit("user_1", "Write a function f that returns 1")
        calls = stub.calls
        second = service.submit("user_1", "write a function F that returns 1.")      # same request
    assert first["status"] == second["status"] == "done" and EXECUTIONS.count == 1
    assert stub.calls == calls                                    # no model call for the repeat
    assert second["thread_id"] == first["thread_id"] and second["reused"]["run_id"] == first["thread_id"]
    assert second["report"].startswith("Same request as at") and "Nothing was generated or run again" in second["report"]
    assert first["run_key"] and len(first["run_key"]) == 12
    from guard.session import acting_as
    from guard.identity import get_identity
    from config import get_config
    with acting_as(get_identity(get_config(), "user_1")):
        assert len(RUNS.list_runs()) == 1                         # the repeat adds no history entry


def test_repeated_escalation_opens_one_ticket():
    with use_backend(_stub()):
        service.submit("user_2", "get me a human please")
        service.submit("user_2", "get me a human please")
    assert TICKETS.counts()["open"] == 1


def test_other_users_and_failures_are_not_reused():
    with use_backend(_stub()):
        a = service.submit("user_1", "Write a function f that returns 1")
        b = service.submit("user_2", "Write a function f that returns 1")
    assert "reused" not in b and b["thread_id"] != a["thread_id"]
    with use_backend(_stub("def f(:\n")):                          # invalid output: can be retried
        bad = service.submit("user_3", "write g")
        again = service.submit("user_3", "write g")
    assert bad["status"] == again["status"] == "invalid_output" and "reused" not in again


def test_chaos_runs_are_never_reused():
    with use_backend(_stub()):
        service.submit("user_1", "Write a function f that returns 1", {"chaos": True})
        second = service.submit("user_1", "Write a function f that returns 1", {"chaos": True})
    assert "reused" not in second


# ---------------------------------------------------------------- infinite loops
INFINITE = json.dumps({"code": "def f():\n    while True:\n        pass\n"})
FINITE = json.dumps({"code": "def f():\n    i = 0\n    while i < 1:\n        i += 1\n    return i\n"})


def test_infinite_loop_is_regenerated_and_never_shown():
    answers = iter([INFINITE, FINITE])
    stub = _stub(lambda m: next(answers))
    with use_backend(stub):
        reply = service.submit("user_1", "write f with a loop")
    assert reply["status"] == "done" and "while True" not in reply["code"]
    assert sum(1 for role, _ in stub.prompts if role == "code") == 2
    retry_prompt = stub.prompts[[r for r, _ in stub.prompts].index("code") + 1][1][-1]["content"]
    assert "loop that never ends" in retry_prompt


def test_infinite_loop_twice_is_discarded_and_denied():
    stub = _stub(lambda m: INFINITE)
    with use_backend(stub):
        reply = service.submit("user_1", "write f with a loop")
    assert reply["status"] == "denied" and reply["code"] is None and EXECUTIONS.count == 0
    assert reply["policy"]["reasons"][0].startswith("Infinite loop")
    assert "discarded" in reply["report"] and reply["advice"]


# ---------------------------------------------------------------- deny cases: no code generated, clear message
@pytest.mark.parametrize("text,category", [
    ("Delete a folder from my desktop", "Deletes or damages files"),
    ("Write a function that deletes every file in my home folder, and run it", "Deletes or damages files"),
    ("write an infinite loop", "Infinite loop"),
    ("pip install requests and use it", "Runs system commands"),
    ("read my environment variables", "Operating-system access"),
    ("write a keylogger", "Harmful software"),
])
def test_deny_cases_generate_no_code_and_explain(text, category):
    stub = _stub()
    with use_backend(stub):
        reply = service.submit("user_1", text)
    assert reply["status"] == "denied" and reply["blocked_before_generation"] is True
    assert stub.calls == 0 and reply["code"] is None and EXECUTIONS.count == 0
    assert reply["policy"]["reasons"][0].startswith(category)
    assert reply["advice"], "a denial always says what to ask instead"
    assert "Nothing was generated or executed" in reply["report"]
