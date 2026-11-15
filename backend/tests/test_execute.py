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


def submit(code, user="student_a", tests=PAL_TESTS):
    with use_backend(stub(code, tests)):
        return service.submit(user, "write a function")


def test_runs_snippet_and_shows_output():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "student_a", 'print("hi")\nis_palindrome("racecar")')
    assert out["exit_code"] == 0 and out["output"] == "hi\nTrue\n"


def test_errors_and_timeouts_come_back_as_output():
    t = submit(PAL)["thread_id"]
    err = service.execute(t, "student_a", "is_palindrome(1)")
    assert err["exit_code"] != 0 and "TypeError" in err["output"]
    # An obvious infinite loop is refused before it runs ...
    with pytest.raises(service.ServiceError) as exc:
        service.execute(t, "student_a", "while True:\n    pass")
    assert exc.value.status == 403 and "Infinite loop" in str(exc.value)
    # ... and a loop that only looks finite is stopped by the sandbox timeout.
    hang = service.execute(t, "student_a", "i = 0\nwhile i >= 0:\n    i += 1")
    assert hang["timed_out"]


def test_output_is_masked():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "student_a", 'print("mail me at a.b@example.com")')
    assert "[EMAIL]" in out["output"] and "example.com" not in out["output"]


def test_snippet_goes_through_policy():
    t = submit(PAL)["thread_id"]
    for bad in ["import os\nos.system('ls')", "__import__('os')"]:
        with pytest.raises(service.ServiceError) as exc:
            service.execute(t, "student_a", bad)
        assert exc.value.status == 403


READ = "open('notes.txt', 'w').write('hello')\nopen('notes.txt').read()"


def test_file_reading_snippet_waits_for_approval_instead_of_being_refused():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "student_a", READ)
    assert out["status"] == "needs_approval" and out["output"] == ""
    assert out["approval"]["status"] == "pending" and any("File access" in r for r in out["policy"]["reasons"])
    assert service.execute(t, "student_a", READ)["approval"]["snippet_id"] == out["approval"]["snippet_id"]
    assert [a["status"] for a in service.snippet_approvals(t, "student_a")] == ["pending"]
    queue = service.approval_queue("instructor")["snippets"]
    assert [(q["snippet_id"], q["owner_name"]) for q in queue] == [(out["approval"]["snippet_id"], "Student A")]


def test_admin_approves_and_it_runs_then_owner_can_rerun_the_same_snippet():
    t = submit(PAL)["thread_id"]
    sid = service.execute(t, "student_a", READ)["approval"]["snippet_id"]
    ran = service.decide_snippet(sid, "instructor", "approve")
    assert ran["status"] == "ran" and ran["output"] == "'hello'\n" and ran["approval"]["decided_by"] == "instructor"
    again = service.execute(t, "student_a", READ)
    assert again["status"] == "ran" and again["output"] == "'hello'\n"
    edited = service.execute(t, "student_a", READ + "\n1")            # a changed snippet asks again
    assert edited["status"] == "needs_approval"


def test_admin_rejects_and_nothing_runs():
    t = submit(PAL)["thread_id"]
    sid = service.execute(t, "student_a", READ)["approval"]["snippet_id"]
    out = service.decide_snippet(sid, "instructor", "reject")
    assert out["status"] == "rejected" and out["output"] == ""
    assert service.execute(t, "student_a", READ)["status"] == "needs_approval"     # asking again is allowed


def test_only_an_admin_decides_snippets():
    t = submit(PAL)["thread_id"]
    sid = service.execute(t, "student_a", READ)["approval"]["snippet_id"]
    with pytest.raises(service.ServiceError) as exc:
        service.decide_snippet(sid, "student_a", "approve")
    assert exc.value.status == 403
    with pytest.raises(service.ServiceError) as exc:
        service.snippet_approvals(t, "student_b")                      # not their thread: looks missing
    assert exc.value.status == 404


def test_pending_snippet_is_shown_masked():
    t = submit(PAL)["thread_id"]
    out = service.execute(t, "student_a", "open('x.txt').read()  # PAN ABCPE1234F")
    assert "ABCPE1234F" not in json.dumps(out) and "[PAN]" in out["approval"]["snippet"]


def test_only_the_owner_runs_code():
    t = submit(PAL)["thread_id"]
    for other in ("student_b", "instructor"):                  # instructors do not browse students' runs
        with pytest.raises(service.ServiceError) as exc:
            service.execute(t, other, "is_palindrome('a')")
        assert exc.value.status == 404
    assert service.execute(t, "student_a", "is_palindrome('a')")["output"] == "True\n"


def test_denied_code_never_runs():
    reply = submit(DENY_CODE, tests="from solution import ls\n\ndef test_ls():\n    ls()\n")
    assert reply["status"] == "denied"
    with pytest.raises(service.ServiceError) as exc:
        service.execute(reply["thread_id"], "student_a", "ls()")
    assert exc.value.status == 403


def test_unapproved_code_waits_for_approver():
    reply = submit(FILE_CODE, tests="from solution import read\n\ndef test_r():\n    assert read\n")
    assert reply["status"] == "awaiting_approval"
    with pytest.raises(service.ServiceError):
        service.execute(reply["thread_id"], "student_a", "read")
    service.resume(reply["thread_id"], "instructor", "approve")
    assert service.execute(reply["thread_id"], "student_a", "read")["exit_code"] == 0


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
        reply = service.submit("student_a", "Write a function to check if a number is palindrome or not")
    assert reply["tier"] == "template" and "is_palindrome_number" in reply["code"]
    out = service.execute(reply["thread_id"], "student_a", reply["example"])
    assert out["exit_code"] == 0 and out["output"].strip() == "True"


def test_approval_queue_shows_only_what_waits_and_is_for_instructors_only():
    done = submit(PAL)["thread_id"]
    with use_backend(stub(FILE_CODE, "from solution import read\n\ndef test_r():\n    assert read\n")):
        paused = service.submit("student_a", "write a function that reads a file")["thread_id"]
    queue = service.approval_queue("instructor")
    assert [c["run_id"] for c in queue["code"]] == [paused] and done not in json.dumps(queue)
    assert queue["code"][0]["owner_name"] == "Student A" and queue["code"][0]["reply"]["code"]
    service.resume(paused, "instructor", "approve")
    assert service.approval_queue("instructor")["code"] == []          # decided: it leaves the queue
    with pytest.raises(service.ServiceError) as exc:
        service.approval_queue("student_a")
    assert exc.value.status == 403
