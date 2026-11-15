"""Idempotency key, the per-user run store and the per-user session record (R1, R2, R3)."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field

from guard.identity import Identity


class NotFound(Exception):
    """Raised for a missing run and for someone else's run, with the same message."""


def idempotency_key(user_id: str, code: str, tests: str) -> str:
    h = hashlib.sha256()
    for part in (user_id, code, tests):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class Run:
    run_id: str
    owner: str
    request: str           # masked
    status: str = "started"
    result: dict = field(default_factory=dict)


@dataclass
class Session:
    """What escalation needs to remember across turns (a thread covers only one request)."""
    denials: int = 0
    failed_repairs: int = 0
    sentiment: list[float] = field(default_factory=list)

    def ticket_opened(self) -> None:
        """A person now has the case: start counting afresh so the next request is not escalated again."""
        self.denials = 0
        self.failed_repairs = 0
        self.sentiment.clear()

    def succeeded(self) -> None:
        self.denials = 0
        self.failed_repairs = 0


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._results: dict[str, dict] = {}   # idempotency key -> RunResult dict
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # runs
    def create(self, run_id: str, owner: str, request: str) -> Run:
        with self._lock:
            run = Run(run_id=run_id, owner=owner, request=request)
            self._runs[run_id] = run
            return run

    def update(self, run_id: str, status: str, result: dict) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id].status = status
                self._runs[run_id].result = result

    def get_run(self, caller: Identity, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None or (run.owner != caller.user_id and caller.role != "admin"):
            raise NotFound("run not found")
        return run

    def list_runs(self, caller: Identity) -> list[Run]:
        return [r for r in self._runs.values() if caller.role == "admin" or r.owner == caller.user_id]

    # idempotency
    def result_for(self, key: str) -> dict | None:
        return self._results.get(key)

    def store_result(self, key: str, result: dict) -> None:
        with self._lock:
            self._results[key] = result

    # sessions
    def session(self, user_id: str) -> Session:
        with self._lock:
            return self._sessions.setdefault(user_id, Session())

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()
            self._results.clear()
            self._sessions.clear()


RUNS = RunStore()


def get_run(caller: Identity, run_id: str) -> Run:
    return RUNS.get_run(caller, run_id)
