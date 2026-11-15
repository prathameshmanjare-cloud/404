"""R2 case 1: evaluate_code_policy. Deny sudo, deletes and OS imports automatically; file and network
(http) access need a person's approval; plain code is allowed. Python and JavaScript follow the same rules."""
import pytest

from policy import evaluate_code_policy
from policy_js import evaluate_js_policy

DENY = {  # code -> words the reason must contain
    "import os": "Operating-system access: imports os",
    "import sys": "Operating-system access: imports sys",
    "import subprocess": "Operating-system access: imports subprocess",
    "from os import path": "Operating-system access: imports os",
    "import os\nos.system('ls')": "Runs system commands: calls os.system",
    "def f():\n    return 'sudo apt-get install x'": 'Dangerous shell command: the code contains "sudo"',
    "def f():\n    return 'rm -rf /home'": 'Dangerous shell command: the code contains "rm -rf"',
    "CMD = 'shutdown -h now'": 'Dangerous shell command: the code contains "shutdown"',
    "import shutil\nshutil.rmtree('/tmp/x')": "Deletes or damages files: calls shutil.rmtree",
    "from pathlib import Path\nPath('a').unlink()": "Deletes or damages files: calls pathlib.Path.unlink",
    "def f(p):\n    p.unlink()": "Deletes or damages files: calls p.unlink",
    "from os import remove as rm\nf = rm": "Deletes or damages files: uses os.remove",
    "eval('1 + 1')": "Dynamic code: calls eval()",
    "f = exec": "Dynamic code: uses exec()",
    "().__class__.__bases__[0].__subclasses__()": "Sandbox escape: uses __subclasses__",
}
APPROVAL = {
    "def f(p):\n    with open(p) as fh:\n        return fh.read()": "File access: calls open",
    "from pathlib import Path\nPath('a.txt').read_text()": "File access: imports pathlib",
    "import tempfile": "File access: imports tempfile",
    "import http.client": "Network access: imports http.client",
    "import urllib.request": "Network access: imports urllib.request",
    "import requests": "Network access: imports requests",
    "API = 'https://api.example.com/v1'": "Network access: contains the web address https://api.example.com/v1",
    "def get(fetch):\n    return fetch('http://example.com')": "Network access: contains the web address http://example.com",
    "name = input()": "Console input: calls input()",
    "import numpy": "Unknown module: imports numpy",
}
ALLOW = [
    "def is_palindrome(s: str) -> bool:\n    return s == s[::-1]",
    "items = [1, 2]\nitems.remove(1)\n'a b'.replace(' ', '')",          # list.remove / str.replace are fine
    "import re\nWORD = re.compile(r'\\w+')",
    'def f(n):\n    """Halt when n reaches 0. See https://en.wikipedia.org/wiki/Recursion"""\n    return n',
    "def f():\n    return 'a pseudo-random value'",                        # "pseudo" is not "sudo"
    "from functools import reduce\nfrom operator import add\ntotal = reduce(add, (1, 2), 0)",
]


@pytest.mark.parametrize("code,reason", DENY.items())
def test_denied_automatically(code, reason):
    r = evaluate_code_policy(code)
    assert r.decision == "deny" and any(reason in x for x in r.reasons), r.reasons


@pytest.mark.parametrize("code,reason", APPROVAL.items())
def test_needs_a_person(code, reason):
    r = evaluate_code_policy(code)
    assert r.decision == "needs_approval" and any(reason in x for x in r.reasons), r.reasons


@pytest.mark.parametrize("code", ALLOW)
def test_allowed(code):
    assert evaluate_code_policy(code).decision == "allow"


def test_deny_wins_over_approval():
    r = evaluate_code_policy("import os\ndef f(p):\n    return open(p).read()")
    assert r.decision == "deny"


def test_policy_never_runs_the_code(tmp_path):
    marker = tmp_path / "ran"
    evaluate_code_policy(f"open({str(marker)!r}, 'w').write('x')")
    assert not marker.exists()


@pytest.mark.parametrize("code,decision,reason", [
    ("const fs = require('fs'); fs.unlinkSync('a')", "deny", "Deletes or damages files"),
    ("require('fs').rmSync('/', { recursive: true })", "deny", "Deletes or damages files"),
    ("const os = require('os')", "deny", "Operating-system access: requires os"),
    ("const cp = require('child_process')", "deny", "Operating-system access: requires child_process"),
    ("const cmd = 'sudo reboot'", "deny", 'Dangerous shell command: the code contains "sudo"'),
    ("const fs = require('fs'); fs.readFileSync('a.txt')", "needs_approval", "File access: requires fs"),
    ("const http = require('http')", "needs_approval", "Network access: requires http"),
    ("const https = require('node:https')", "needs_approval", "Network access: requires node:https"),
    ("const url = 'https://api.example.com'", "needs_approval", "Network access: contains the web address"),
    ("fetch('/data')", "needs_approval", "Network access: calls fetch"),
    ("// see https://example.com\nfunction f() { return 1; }", "allow", "no file"),
])
def test_javascript_follows_the_same_rules(code, decision, reason):
    r = evaluate_js_policy(code)
    assert r.decision == decision and any(reason in x for x in r.reasons), r.reasons


# ---------------------------------------------------------------- the request itself (before any code)
from policy import check_request_intent  # noqa: E402

DESTRUCTIVE_REQUESTS = [
    "Write a function that deletes every file in my home folder, and run it",     # the PDF's Student B
    "Write a script to wipe my hard drive", "Write code to format the C: drive",
    "Write a function that shuts down the computer", "run sudo apt update in python",
    "write code that does rm -rf /", "Write a function to delete all files in a directory",
    "kill all processes on the server", "Write a function that executes a shell command",
    "Remove the folder /tmp/data recursively", "empty my downloads folder",
]
ORDINARY_REQUESTS = [
    "Write a function that removes duplicates from a list", "Write a function that deletes the last element of a list",
    "Remove all vowels from a string", "delete a node from a linked list", "Explain how to restart a for loop",
    "Write a function that reads a CSV file", "Write a function that removes empty strings from a list of file names",
    "remove file extensions from a list of names", "Write a function that lists files in a folder",
]


@pytest.mark.parametrize("text", DESTRUCTIVE_REQUESTS)
def test_destructive_requests_are_denied_before_generation(text):
    r = check_request_intent(text)
    assert r.decision == "deny" and r.reasons


@pytest.mark.parametrize("text", ORDINARY_REQUESTS)
def test_ordinary_requests_pass_the_request_check(text):
    assert check_request_intent(text).decision == "allow"


def test_student_b_is_denied_with_no_model_call_and_nothing_run():
    import json
    import service
    from llm.backends import StubBackend
    from llm.client import use_backend
    from sandbox import EXECUTIONS
    harmless = StubBackend({"code": json.dumps({"code": "def f():\n    return 1\n"})})
    with use_backend(harmless):
        reply = service.submit("user_2", "Write a function that deletes every file in my home folder, and run it")
    assert reply["status"] == "denied" and harmless.calls == 0 and EXECUTIONS.count == 0
    assert reply["policy"]["decision"] == "deny"
    assert any(r.startswith("Deletes or damages files") for r in reply["policy"]["reasons"])
    assert "Nothing was generated or executed" in reply["report"]


def test_approved_network_code_is_run_once_and_reported_honestly():
    import json
    import service
    from llm.backends import StubBackend
    from llm.client import use_backend
    code = ("import urllib.request\n\n\ndef fetch(url: str) -> str:\n"
            "    return urllib.request.urlopen(url, timeout=2).read().decode()\n")
    tests = "from solution import fetch\n\n\ndef test_fetch():\n    assert fetch('http://example.com')\n"
    req = {"function_name": "fetch", "parameters": [{"name": "url", "type": "str"}], "returns": "str"}
    stub = StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps(req), "code": json.dumps({"code": code}),
                        "tests": json.dumps({"tests": tests}), "handoff": '{"note": "n"}'})
    with use_backend(stub):
        paused = service.submit("user_1", "Write a function that downloads a web page over http")
        assert paused["status"] == "awaiting_approval"
        assert any(r.startswith("Network access") for r in paused["policy"]["reasons"])
        done = service.resume(paused["thread_id"], "admin", "approve")
    code_calls = sum(1 for role, _ in stub.prompts if role == "code")
    assert code_calls == 1                                    # no pointless repair round
    assert done["run"]["exit_code"] != 0 and "sandbox always blocks" in done["report"]
    assert "Tests passed" not in done["report"]


def test_only_an_admin_can_approve_and_says_so():
    import json
    import service
    from llm.backends import StubBackend
    from llm.client import use_backend
    req = {"function_name": "read", "parameters": [{"name": "p", "type": "str"}], "returns": "str"}
    stub = StubBackend({"triage": '{"sentiment": "neutral"}', "router": '{"route": "python_function"}',
                        "requirements": json.dumps(req),
                        "code": json.dumps({"code": "def read(p: str) -> str:\n    return open(p).read()\n"}),
                        "tests": json.dumps({"tests": "from solution import read\n\ndef test_r():\n    assert read\n"})})
    with use_backend(stub):
        paused = service.submit("user_1", "read a file")
    with pytest.raises(service.ServiceError) as exc:
        service.resume(paused["thread_id"], "user_1", "approve")
    assert exc.value.status == 403 and "Only an admin can approve" in str(exc.value)
