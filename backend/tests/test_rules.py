"""Unit tests for every pure rule: masking, identity, runs, policy, SQL guard, escalation, report."""
import pytest

from config import get_config
from escalation import should_escalate
from guard.identity import UnknownUser, can_approve, get_identity
from guard.mask import luhn_valid, mask
from guard.quota import QUOTA, check_quota
from guard.session import acting_as
from policy import evaluate_code_policy
from report import render_report
from runs import RUNS, NotFound, Session, idempotency_key
from sql_guard import check_sql
from state import Triage
from templates.tasks import match_template
from triage import heuristic_triage

CFG = get_config()


# ---------- mask
@pytest.mark.parametrize("raw,label", [
    ("key sk-proj-abc123def456ghi789", "[SECRET]"),
    ("key gsk_ABCdef1234567890xyz", "[SECRET]"),
    ("mail priya.sharma@example.com", "[EMAIL]"),
    ("call 9876543210", "[PHONE]"),
    ("call +91 98765 43210", "[PHONE]"),
    ("call (415) 555-0100", "[PHONE]"),
    ("aadhaar 2345 6789 0123", "[AADHAAR]"),
    ("aadhaar 234567890123", "[AADHAAR]"),
    ("card 4111 1111 1111 1111", "[CARD]"),
    ("card 4111-1111-1111-1111", "[CARD]"),
])
def test_mask_hides(raw, label):
    out = mask(raw)
    assert label in out
    assert not any(ch.isdigit() for ch in out.split(" ", 1)[1].replace(label, "")) or label == "[SECRET]"


@pytest.mark.parametrize("raw", ["order 123456", "ref 1234 5678 9012 3456", "date 2024-01-05", "x = 10",
                                 "aadhaar-like 1234 5678 9012"])
def test_mask_keeps(raw):
    assert mask(raw) == raw


def test_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("1234567890123456")


# ---------- identity, quota, runs
def test_identity_from_config_only():
    me = get_identity(CFG, "user_1")
    assert me.role == "user"
    assert not can_approve(CFG, me)
    assert can_approve(CFG, get_identity(CFG, "admin"))
    with pytest.raises(UnknownUser):
        get_identity(CFG, "root")


def test_quota():
    me = get_identity(CFG, "user_1")
    QUOTA.set_used(me.user_id, CFG["quota"]["user"] - 1)
    assert check_quota(CFG, me)
    assert not check_quota(CFG, me)


def test_not_found_is_identical():
    a, b = get_identity(CFG, "user_1"), get_identity(CFG, "user_2")
    RUNS.create("r1", b.user_id, "x")
    errors = []
    for rid in ("r1", "missing"):
        with pytest.raises(NotFound) as exc:
            with acting_as(a):
                RUNS.get_run(rid)
        errors.append(str(exc.value))
    assert errors[0] == errors[1]
    with acting_as(b):
        assert RUNS.get_run("r1").owner == "user_2"
    with acting_as(get_identity(CFG, "admin")):
        assert RUNS.get_run("r1")


def test_idempotency_key():
    assert idempotency_key("u", "c", "t") == idempotency_key("u", "c", "t")
    assert idempotency_key("u", "c", "t") != idempotency_key("v", "c", "t")
    assert idempotency_key("u", "ab", "c") != idempotency_key("u", "a", "bc")


# ---------- policy
@pytest.mark.parametrize("code", [
    "import os\nos.system('ls')",
    "from os import system as s\ns('ls')",
    "import subprocess",
    "eval('1+1')",
    "exec('x=1')",
    "__import__('os')",
    "import shutil\nshutil.rmtree('/')",
    "from pathlib import Path\nPath('x').unlink()",
    "import os\nos.remove('x')",
    "import os\nos.popen('ls')",
    "import ctypes",
    "getattr(__builtins__, 'eval')",
    "().__class__.__bases__[0].__subclasses__()",
    "def broken(:",
    "import os\nprint(os.getcwd())",
])
def test_policy_denies(code):
    assert evaluate_code_policy(code).decision == "deny"


@pytest.mark.parametrize("code", [
    "def f(p):\n    return open(p).read()",
    "import socket",
    "import requests",
    "import numpy",
    "from pathlib import Path\nPath('a').read_text()",
])
def test_policy_needs_approval(code):
    assert evaluate_code_policy(code).decision == "needs_approval"


@pytest.mark.parametrize("code", [
    "def is_palindrome(s: str) -> bool:\n    return s == s[::-1]",
    "from functools import reduce\nfrom operator import add\ndef f(t): return reduce(add, t, 0)",
    "x = [1, 2]\nx.remove(1)",
])
def test_policy_allows(code):
    assert evaluate_code_policy(code).decision == "allow"


# ---------- sql guard
def test_sql_guard():
    assert check_sql("SELECT region, SUM(amount) FROM sales GROUP BY region;").decision == "allow"
    assert check_sql("-- tables: sales\nWITH t AS (SELECT 1) SELECT * FROM t").decision == "allow"
    assert check_sql("SELECT 'drop table x' AS s").decision == "allow"
    for bad in ["DROP TABLE sales", "SELECT 1; DELETE FROM sales", "UPDATE sales SET a=1",
                "SELECT * INTO backup FROM sales", ""]:
        assert check_sql(bad).decision == "deny"


# ---------- triage heuristics and escalation
def test_heuristic_triage():
    assert heuristic_triage("get me a human").wants_human
    assert heuristic_triage("can I talk to a real person?").wants_human
    assert not heuristic_triage("write a palindrome checker").wants_human
    angry = heuristic_triage("THIS IS NOT WORKING!!!")
    assert angry.sentiment == "angry" and angry.urgency == "high"
    assert heuristic_triage("still not great, honestly").sentiment == "unhappy"


def test_should_escalate():
    esc = CFG["escalation"]
    assert should_escalate(Triage(wants_human=True), Session(sentiment=[0.0]), esc)[0]
    assert should_escalate(Triage(sentiment="angry", sentiment_score=-0.9), Session(sentiment=[-0.9]), esc)[0]
    assert should_escalate(Triage(), Session(denials=2, sentiment=[0.0]), esc)[0]
    assert should_escalate(Triage(), Session(failed_repairs=2, sentiment=[0.0]), esc)[0]
    assert not should_escalate(Triage(sentiment="unhappy", sentiment_score=-0.4), Session(sentiment=[-0.4]), esc)[0]
    assert should_escalate(Triage(sentiment="unhappy", sentiment_score=-0.4),
                           Session(sentiment=[-0.4, -0.4]), esc)[0]
    assert not should_escalate(Triage(), Session(sentiment=[0.0, 0.0]), esc)[0]


# ---------- report and templates
def test_report_says_passed_only_on_exit_zero():
    ok = render_report({"status": "done", "run": {"exit_code": 0}, "tier": "primary"})
    bad = render_report({"status": "done", "run": {"exit_code": 1}, "tier": "fallback"})
    hung = render_report({"status": "done", "run": {"exit_code": None, "timed_out": True}, "tier": "primary"})
    assert "Tests passed" in ok and "primary model" in ok
    assert "Tests passed" not in bad and "fallback" in bad
    assert "Tests passed" not in hung and "timeout" in hung


def test_template_matching():
    assert match_template("Write a Python function that checks if a string is a palindrome") == "palindrome"
    assert match_template("sum a list with a comprehension and a tuple functionally") == "list_sum"
    assert match_template("write a poem") is None
