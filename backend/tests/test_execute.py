"""Running generated code from the UI (POST /execute)."""
import json

import pytest

import service
from llm.backends import StubBackend
from llm.client import use_backend

PAL = "def is_palindrome(text: str) -> bool:\n    return text == text[::-1]\n"
PAL_TESTS = "from solution import is_palindrome\n\ndef test_a():\n    assert is_palindrome('aba')\n"
FILE_CODE = "def read(p: str) -> str:\n    with open(p) as fh:\n        return fh.read()\n"
DENY_CODE = "import os\n\ndef ls() -> int:\n    return os.system('ls')\n"


def stub(code, tests=PAL_TESTS):
    req = {"function_name": "f", "parameters": [], "returns": "Any", "edge_cases": [], "branches": []}
    return StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps(req), "code": json.dumps({"code": code}),
                        "tests": json.dumps({"tests": tests}), "handoff": '{"note": "n"}'})


def submit(code, user="user_1", tests=PAL_TESTS):
    with use_backend(stub(code, tests)):
        return service.submit(user, "write a function")


def test_runs_snippet_and_shows_output():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "user_1", 'print("hi")\nis_palindrome("racecar")')
    assert out["exit_code"] == 0 and out["output"] == "hi\nTrue\n"


def test_errors_and_timeouts_come_back_as_output():
    t = submit(PAL)["thread_id"]
    err = service.execute(t, "user_1", "is_palindrome(1)")
    assert err["exit_code"] != 0 and "TypeError" in err["output"]
    hang = service.execute(t, "user_1", "while True:\n    pass")
    assert hang["timed_out"]


def test_output_is_masked():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "user_1", 'print("mail me at a.b@example.com")')
    assert "[EMAIL]" in out["output"] and "example.com" not in out["output"]


def test_snippet_goes_through_policy():
    t = submit(PAL)["thread_id"]
    for bad in ["import os\nos.system('ls')", "open('/etc/passwd').read()", "__import__('os')"]:
        with pytest.raises(service.ServiceError) as exc:
            service.execute(t, "user_1", bad)
        assert exc.value.status == 403


def test_only_owner_or_admin():
    t = submit(PAL)["thread_id"]
    with pytest.raises(service.ServiceError) as exc:
        service.execute(t, "user_2", "is_palindrome('a')")
    assert exc.value.status == 404
    assert service.execute(t, "admin", "is_palindrome('a')")["output"] == "True\n"


def test_denied_code_never_runs():
    reply = submit(DENY_CODE, tests="from solution import ls\n\ndef test_ls():\n    ls()\n")
    assert reply["status"] == "denied"
    with pytest.raises(service.ServiceError) as exc:
        service.execute(reply["thread_id"], "user_1", "ls()")
    assert exc.value.status == 403


def test_unapproved_code_waits_for_approver():
    reply = submit(FILE_CODE, tests="from solution import read\n\ndef test_r():\n    assert read\n")
    assert reply["status"] == "awaiting_approval"
    with pytest.raises(service.ServiceError):
        service.execute(reply["thread_id"], "user_1", "read")
    service.resume(reply["thread_id"], "admin", "approve")
    assert service.execute(reply["thread_id"], "user_1", "read")["exit_code"] == 0


# ---------- regressions: "check if a number is palindrome" errored when run
from examples import example_call  # noqa: E402
from sandbox import run_snippet  # noqa: E402
from templates.tasks import TEMPLATES, match_template  # noqa: E402


def test_number_palindrome_gets_number_template():
    assert match_template("Write a function to check if a number is palindrome or not") == "palindrome_number"
    assert match_template("Write a Python function that checks if a string is a palindrome") == "palindrome"


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_example_runs(name):
    code = TEMPLATES[name]["code"]
    result = run_snippet(code, example_call(code))
    assert result.exit_code == 0, result.output


def test_example_uses_values_not_parameter_names():
    ex = example_call("def is_palindrome(text: str) -> bool:\n    return text == text[::-1]\n")
    assert ex.endswith('is_palindrome("racecar")')
    ex = example_call("def f(n, items):\n    return n\n")
    assert ex.endswith("f(10, [3, 1, 4, 1, 5])")


def test_errors_are_readable():
    out = run_snippet("def f(n: int) -> int:\n    return n + 1\n", "f('x')").output
    assert "codesmith-run-" not in out and "runpy" not in out and "_launcher" not in out
    assert 'File "solution.py"' in out and "TypeError" in out


def test_number_palindrome_end_to_end_without_model():
    with use_backend(StubBackend({})):
        reply = service.submit("user_1", "Write a function to check if a number is palindrome or not")
    assert reply["tier"] == "template" and "is_palindrome_number" in reply["code"]
    out = service.execute(reply["thread_id"], "user_1", reply["example"])
    assert out["exit_code"] == 0 and out["output"].strip() == "True"
