"""call_model(): the one wrapper every agent uses.

Order for each role: primary model, then the fallback model. Each model gets retries with
full jitter that honor retry-after, behind its own circuit breaker. Chaos and outage switches
inject failures before the provider call, so the real retry and fallback code is exercised.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass

from config import get_config, llm_mode
from guard.mask import mask
from llm.backends import GroqBackend, RecordBackend, ReplayBackend
from llm.errors import CassetteMissing, ModelError, ModelUnavailable
from llm.reliability import BREAKERS, backoff_delay, chaos_check

log = logging.getLogger("codesmith.llm")

_override = None
_override_lock = threading.Lock()


@dataclass
class ModelReply:
    text: str
    model: str
    tier: str  # primary | fallback


class Meter:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.tokens: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self._lock = threading.Lock()

    def ok(self, model: str, usage: dict) -> None:
        with self._lock:
            self.calls[model] = self.calls.get(model, 0) + 1
            self.tokens[model] = self.tokens.get(model, 0) + int(usage.get("total_tokens", 0) or 0)

    def fail(self, model: str) -> None:
        with self._lock:
            self.failures[model] = self.failures.get(model, 0) + 1

    def snapshot(self) -> dict:
        return {"calls": dict(self.calls), "tokens": dict(self.tokens), "failures": dict(self.failures)}


METER = Meter()


@contextlib.contextmanager
def use_backend(backend):
    """Swap the backend (a StubBackend in tests and gate rule cases)."""
    global _override
    with _override_lock:
        previous, _override = _override, backend
    try:
        yield backend
    finally:
        with _override_lock:
            _override = previous


def _backend(replay: bool):
    if _override is not None:
        return _override
    cfg = get_config()["reliability"]
    if replay:
        return ReplayBackend()
    mode = llm_mode()
    if mode == "replay":
        return ReplayBackend()
    live = GroqBackend(timeout_s=cfg["request_timeout_s"])
    return RecordBackend(live) if mode == "record" else live


def models_for(role: str) -> list[str]:
    cfg = get_config()
    primary = cfg["models"][cfg["roles"][role]]
    fallback = cfg["models"]["fallback"]
    return [primary] if primary == fallback else [primary, fallback]


def call_model(role: str, messages: list[dict], controls: dict | None = None,
               json_mode: bool = True, sleep=time.sleep) -> ModelReply:
    controls = controls or {}
    rel = get_config()["reliability"]
    backend = _backend(bool(controls.get("replay")))
    # Every prompt is masked again here: no unmasked text can reach a provider or a cassette.
    messages = [{"role": m["role"], "content": mask(m["content"])} for m in messages]
    errors: list[str] = []

    for idx, model in enumerate(models_for(role)):
        tier = "primary" if idx == 0 else "fallback"
        breaker = BREAKERS.get(model, rel["breaker_failures"], rel["breaker_cooldown_s"])
        if not breaker.allow():
            errors.append(f"{model}: breaker open")
            log.info("breaker open for %s, skipping", model)
            continue
        for attempt in range(rel["max_attempts"]):
            try:
                chaos_check(bool(controls.get("chaos")), bool(controls.get("outage")),
                            rel["chaos_rate"], rel["chaos_retry_after_s"])
                log.info("model call role=%s model=%s attempt=%d prompt=%s", role, model, attempt,
                         messages[-1]["content"][:300])
                text, usage = backend.complete(model, messages, json_mode, role)
                breaker.record_success()
                METER.ok(model, usage)
                return ModelReply(text=text, model=model, tier=tier)
            except CassetteMissing:
                raise
            except ModelError as exc:
                METER.fail(model)
                errors.append(f"{model}: {exc}")
                log.warning("model error role=%s model=%s attempt=%d: %s", role, model, attempt, exc)
                if not exc.retryable or attempt + 1 >= rel["max_attempts"]:
                    break
                if exc.retry_after and exc.retry_after > rel.get("max_wait_s", 8):
                    log.info("%s asks to wait %.0fs; moving to the next model instead", model, exc.retry_after)
                    break
                sleep(backoff_delay(attempt, rel["base_delay_s"], rel["max_delay_s"], exc.retry_after))
        # Retries for this model are exhausted: one failure for its breaker, then the next model.
        breaker.record_failure()
    raise ModelUnavailable("; ".join(errors[-4:]) or f"no model available for {role}")
