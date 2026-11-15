"""Retry with full jitter that honors retry-after, per-model circuit breaker, and chaos (R5)."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

from llm.errors import ModelError


def backoff_delay(attempt: int, base: float, cap: float, retry_after: float | None,
                  rng: random.Random | None = None) -> float:
    """Wait at least retry-after, plus full jitter: uniform(0, min(cap, base * 2**attempt))."""
    rng = rng or random
    jitter = rng.uniform(0, min(cap, base * (2 ** attempt)))
    return (retry_after or 0.0) + jitter


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 15.0
    failures: int = 0
    opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.opened_at is None:
                return True
            if now - self.opened_at >= self.cooldown_s:
                return True  # half-open: let one call probe
            return False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = now

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.cooldown_s else "open"


class BreakerBoard:
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, model: str, threshold: int, cooldown: float) -> CircuitBreaker:
        with self._lock:
            if model not in self._breakers:
                self._breakers[model] = CircuitBreaker(threshold, cooldown)
            return self._breakers[model]

    def snapshot(self) -> dict[str, str]:
        return {m: b.state for m, b in self._breakers.items()}

    def reset(self) -> None:
        with self._lock:
            self._breakers.clear()


BREAKERS = BreakerBoard()


def chaos_check(chaos: bool, outage: bool, rate: float, retry_after: float,
                rng: random.Random | None = None) -> None:
    """Raise a simulated provider failure before the real call when a switch is on."""
    rng = rng or random
    if outage:
        raise ModelError("simulated total outage", status=503, retry_after=0.0)
    if chaos and rng.random() < rate:
        if rng.random() < 0.5:
            raise ModelError("chaos: simulated 429", status=429, retry_after=retry_after)
        raise ModelError("chaos: simulated 503", status=503, retry_after=None)
