"""Regressions from live testing: double-escaped output, policy false positives, escalation cascade,
and examples for classes."""
import json

from agents.agents import normalize_source, write_tests
from examples import example_call
from llm.backends import StubBackend
from llm.client import use_backend
from policy import evaluate_code_policy
from sandbox import run_snippet
from state import Requirements
import service

DOUBLE_ESCAPED = ('import pytest\\nfrom solution import f\\n\\n\\ndef test_a():\\n    assert f("x\\\\n") == 1\\n')


def test_double_escaped_source_is_repaired():
    fixed = normalize_source(DOUBLE_ESCAPED)
    assert fixed.count("\n") >= 5 and 'f("x\\n")' in fixed
    compile(fixed, "t", "exec")


def test_fenced_source_is_unwrapped():
    assert normalize_source("```python\ndef f():\n    return 1\n```") == "def f():\n    return 1\n"


def test_good_source_is_untouched():
    src = 'def f():\n    return "a\\nb"\n'
    assert normalize_source(src) == src


def test_tests_agent_repairs_double_escaping():
    req = Requirements(function_name="f")
    with use_backend(StubBackend({"tests": json.dumps({"tests": DOUBLE_ESCAPED})})):
        tests, _ = write_tests(req, {})
    compile(tests, "t", "exec")


def test_tests_agent_asks_again_when_still_invalid():
    answers = iter([json.dumps({"tests": "def broken(:"}), json.dumps({"tests": "def test_ok():\n    assert True\n"})])
    with use_backend(StubBackend({"tests": lambda m: next(answers)})):
        tests, _ = write_tests(Requirements(function_name="f"), {})
    assert "test_ok" in tests


def test_policy_allows_common_safe_calls():
    for code in ["import re\nEMAIL = re.compile(r'^[^@]+@[^@]+$')",
                 "import re\ndef ok(s):\n    return re.fullmatch(r'a+', s) is not None",
                 "class A:\n    def compile(self):\n        return 1\nA().compile()"]:
        assert evaluate_code_policy(code).decision == "allow", code
    for code in ["eval('1')", "run = exec\nrun('x=1')", "compile('1', 'f', 'eval')"]:
        assert evaluate_code_policy(code).decision == "deny", code


def _stub(code):
    req = {"function_name": "f", "parameters": [], "returns": "Any"}
    return StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps(req), "code": json.dumps({"code": code}),
                        "tests": json.dumps({"tests": "from solution import f\n\ndef test_f():\n    assert f() == 1\n"}),
                        "handoff": '{"note": "n"}'})


def test_denials_escalate_once_and_do_not_block_later_requests():
    bad = "import os\n\ndef f():\n    return os.system('ls')\n"
    with use_backend(_stub(bad)):
        first = service.submit("user_1", "list files")
        second = service.submit("user_1", "list files again")
    assert first["status"] == second["status"] == "denied"
    assert not first["ticket"] and second["ticket"]          # one ticket, at the second denial
    with use_backend(_stub("def f():\n    return 1\n")):
        later = service.submit("user_1", "write f")
    assert later["status"] == "done" and not later["ticket"]  # not escalated forever


def test_unparsable_output_is_not_counted_as_a_denial():
    with use_backend(_stub("def f(:\n")):
        for _ in range(3):
            reply = service.submit("user_1", "write f")
    assert reply["status"] == "invalid_output" and not reply["ticket"]
    assert "not valid Python" in reply["report"]


def test_class_example_runs():
    code = ("class Stack:\n    def __init__(self):\n        self.items = []\n    def push(self, item):\n"
            "        self.items.append(item)\n    def pop(self):\n        return self.items.pop()\n"
            "    def peek(self):\n        return self.items[-1]\n")
    ex = example_call(code)
    assert "stack = Stack()" in ex and "stack.push(10)" in ex
    assert run_snippet(code, ex).exit_code == 0


def test_example_comes_from_a_valid_call_in_the_tests():
    code = "def roman_to_int(s: str) -> int:\n    return {'XIV': 14}.get(s, 0)\n"
    tests = ("import pytest\nfrom solution import roman_to_int\n\n"
             "def test_bad():\n    with pytest.raises(KeyError):\n        roman_to_int('Q')\n\n"
             "def test_not():\n    assert not roman_to_int('')\n\n"
             "def test_ok():\n    assert roman_to_int('XIV') == 14\n")
    assert example_call(code, None, tests).endswith("roman_to_int('XIV')")


def test_last_none_is_not_printed():
    assert run_snippet("def f():\n    print('hi')\n", "f()").output == "hi\n"
    assert run_snippet("def f():\n    return 0\n", "f()").output == "0\n"
