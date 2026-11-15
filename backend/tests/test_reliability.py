"""Retry, breaker, fallback, chaos and outage (R5), and the sandbox (R2)."""
import json
import random

import pytest

import service
from llm.backends import StubBackend
from llm.client import call_model, use_backend
from llm.errors import ModelError, ModelUnavailable
from llm.reliability import CircuitBreaker, backoff_delay
from sandbox import EXECUTIONS, run_in_sandbox
from templates.tasks import TEMPLATES
from llm.client import models_for

ROUTER_PRIMARY, ROUTER_FALLBACK = models_for("router")


def test_backoff_waits_at_least_retry_after():
    rng = random.Random(1)
    for attempt in range(4):
        d = backoff_delay(attempt, 0.5, 8, retry_after=2.0, rng=rng)
        assert 2.0 <= d <= 2.0 + min(8, 0.5 * 2 ** attempt)


def test_breaker_opens_and_half_opens():
    b = CircuitBreaker(failure_threshold=2, cooldown_s=10)
    b.record_failure(now=0)
    assert b.allow(now=1)
    b.record_failure(now=1)
    assert not b.allow(now=2)
    assert b.allow(now=12)
    b.record_success()
    assert b.allow(now=12)


class Flaky(StubBackend):
    def __init__(self, fail_models, status=429):
        super().__init__({"router": '{"route": "sql"}'})
        self.fail_models = set(fail_models)
        self.status = status
        self.seen = []

    def complete(self, model, messages, json_mode, role=""):
        self.seen.append(model)
        if model in self.fail_models:
            raise ModelError("boom", status=self.status, retry_after=1.5)
        return super().complete(model, messages, json_mode, role)


def test_retry_then_fallback_and_honors_retry_after():
    sleeps = []
    backend = Flaky({ROUTER_PRIMARY})
    with use_backend(backend):
        reply = call_model("router", [{"role": "user", "content": "x"}], sleep=sleeps.append)
    assert reply.tier == "fallback" and reply.model == ROUTER_FALLBACK
    assert backend.seen.count(ROUTER_PRIMARY) == 3
    assert sleeps and all(s >= 1.5 for s in sleeps)


def test_non_retryable_error_skips_retries():
    backend = Flaky({ROUTER_PRIMARY}, status=400)

    def complete(model, messages, json_mode, role=""):
        backend.seen.append(model)
        if model == ROUTER_PRIMARY:
            raise ModelError("bad request", status=400, retryable=False)
        return '{"route": "sql"}', {}

    backend.complete = complete
    with use_backend(backend):
        call_model("router", [{"role": "user", "content": "x"}], sleep=lambda s: None)
    assert backend.seen.count(ROUTER_PRIMARY) == 1


def test_outage_raises_unavailable():
    with use_backend(StubBackend({"router": "{}"})):
        with pytest.raises(ModelUnavailable):
            call_model("router", [{"role": "user", "content": "x"}], {"outage": True}, sleep=lambda s: None)


def test_prompt_is_masked_before_backend():
    stub = StubBackend({"router": '{"route": "sql"}'})
    with use_backend(stub):
        call_model("router", [{"role": "user", "content": "key sk-abcdefghijklmnop"}])
    assert "sk-abcdefghijklmnop" not in json.dumps(stub.prompts)


def test_outage_serves_template_for_known_task_and_ticket_for_unknown():
    with use_backend(StubBackend({})):
        known = service.submit("user_1", "Write a Python function that checks if a string is a palindrome",
                               {"outage": True})
        unknown = service.submit("user_1", "Write a function that parses RFC 3339 timestamps", {"outage": True})
    assert known["tier"] == "template" and known["status"] == "done"
    assert known["code"] == TEMPLATES["palindrome"]["code"]
    assert "template tier" in known["report"]
    assert unknown["status"] == "unavailable" and unknown["ticket"] and not unknown["code"]
    assert "Cannot answer fully right now" in unknown["report"]


def test_template_tier_never_auto_approves_risky_code(monkeypatch):
    risky = dict(TEMPLATES["palindrome"], code="def f(p):\n    return open(p).read()\n")
    monkeypatch.setitem(TEMPLATES, "palindrome", risky)
    with use_backend(StubBackend({})):
        reply = service.submit("user_1", "palindrome please", {"outage": True})
    assert reply["status"] == "awaiting_approval" and EXECUTIONS.count == 0


def test_sandbox_basics(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_not_leak")
    ok = run_in_sandbox("def f():\n    return 1\n", "from solution import f\ndef test_f():\n    assert f() == 1\n")
    assert ok.exit_code == 0
    env = run_in_sandbox("", "import os\ndef test_env():\n    assert 'GROQ_API_KEY' not in os.environ\n")
    assert env.exit_code == 0
    net = run_in_sandbox("", "import socket, pytest\ndef test_net():\n    with pytest.raises(OSError):\n"
                             "        socket.create_connection(('1.1.1.1', 80), timeout=1)\n")
    assert net.exit_code == 0
    hang = run_in_sandbox("", "def test_hang():\n    while True:\n        pass\n", timeout_s=1)
    assert hang.timed_out and hang.exit_code is None


def test_long_retry_after_moves_to_fallback_without_waiting():
    sleeps = []

    class SlowLimit(Flaky):
        def complete(self, model, messages, json_mode, role=""):
            self.seen.append(model)
            if model == ROUTER_PRIMARY:
                raise ModelError("rate limited", status=429, retry_after=45)
            return '{"route": "sql"}', {}

    backend = SlowLimit(set())
    with use_backend(backend):
        reply = call_model("router", [{"role": "user", "content": "x"}], sleep=sleeps.append)
    assert reply.tier == "fallback" and backend.seen.count(ROUTER_PRIMARY) == 1 and sleeps == []


def test_replay_follows_the_fallback_model_and_never_trips_the_breaker(tmp_path):
    from llm.backends import ReplayBackend, cassette_key
    from llm.errors import CassetteMissing
    from llm.reliability import BREAKERS
    import json as _json
    msgs = [{"role": "user", "content": "x"}]
    # Recorded while the primary was rate-limited: only the fallback model's answer exists.
    (tmp_path / f"{cassette_key(ROUTER_FALLBACK, msgs)}.json").write_text(_json.dumps({"response": '{"route": "sql"}'}))
    with use_backend(ReplayBackend(tmp_path)):
        for _ in range(5):
            reply = call_model("router", msgs, sleep=lambda s: None)
            assert reply.model == ROUTER_FALLBACK
        assert BREAKERS.get(ROUTER_PRIMARY, 3, 15).state == "closed"
        with pytest.raises(CassetteMissing):                       # nothing recorded at all
            call_model("router", [{"role": "user", "content": "never recorded"}], sleep=lambda s: None)
