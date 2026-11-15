"""Idempotency key, the per-user run store (SQLite, survives restarts) and the per-user session record."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from guard.session import current_identity


class NotFound(Exception):
    """Raised for a missing run and for someone else's run, with the same message."""


def request_key(user_id: str, request: str, controls: dict | None = None) -> str:
    """Request-level idempotency key: hash of the user, the (masked) request and the switches.
    Case and spacing do not matter, so "Write f" and "write  f " are the same request."""
    norm = " ".join((request or "").lower().split()).rstrip(" .!?")
    h = hashlib.sha256()
    for part in (user_id, norm, json.dumps(controls or {}, sort_keys=True)):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def idempotency_key(user_id: str, code: str, tests: str) -> str:
    """Run-level idempotency key (R2): hash of the user, the code and the tests."""
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
    result: dict = field(default_factory=dict)   # the full (masked) reply
    created: float = field(default_factory=time.time)
    seq: int = 0                                  # creation order (timestamps can tie)


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


def _db_path() -> str:
    """CODESMITH_DB overrides the file (tests and the gate use ":memory:" so they never touch real history)."""
    path = os.getenv("CODESMITH_DB")
    if path:
        return path
    from config import BACKEND_DIR, get_config
    rel = get_config().get("storage", {}).get("db_path", "data/codesmith.db")
    full = (BACKEND_DIR / rel).resolve()
    full.parent.mkdir(parents=True, exist_ok=True)
    return str(full)


# Account ids before they were renamed to match the problem statement (students and an instructor).
LEGACY_IDS = {"user_1": "student_a", "user_2": "student_b", "user_3": "student_c", "admin": "instructor"}


class RunStore:
    """Runs and idempotent results live in SQLite, so history survives a backend restart.
    Escalation counters (sessions) stay in memory: they only matter within a conversation."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _db_path()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                seq      INTEGER PRIMARY KEY AUTOINCREMENT,   -- creation order
                run_id   TEXT UNIQUE NOT NULL,
                owner    TEXT NOT NULL,
                request  TEXT NOT NULL,                       -- masked
                status   TEXT NOT NULL,
                result   TEXT NOT NULL DEFAULT '{}',          -- the full masked reply, JSON
                created  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner, seq);
            CREATE INDEX IF NOT EXISTS runs_created ON runs(created);
            CREATE TABLE IF NOT EXISTS results (               -- idempotency key -> sandbox result
                key      TEXT PRIMARY KEY,
                result   TEXT NOT NULL
            );
        """)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(runs)")}
        if "req_key" not in cols:                        # databases created before request keys existed
            self._db.execute("ALTER TABLE runs ADD COLUMN req_key TEXT")
        for old_id, new_id in LEGACY_IDS.items():       # history saved before accounts were renamed
            self._db.execute("UPDATE runs SET owner = ? WHERE owner = ?", (new_id, old_id))
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def find_recent(self, owner: str, req_key: str, statuses: set[str], max_age_s: float) -> Run | None:
        """The owner's newest finished run of the same request within the window, if any."""
        with self._lock:
            row = self._db.execute(
                "SELECT seq, run_id, owner, request, status, result, created FROM runs "
                "WHERE owner = ? AND req_key = ? AND created >= ? ORDER BY seq DESC LIMIT 1",
                (owner, req_key, time.time() - max_age_s)).fetchone()
        run = self._row(row) if row else None
        return run if run and run.status in statuses and run.result else None

    @staticmethod
    def _row(row) -> Run:
        seq, run_id, owner, request, status, result, created = row
        return Run(run_id=run_id, owner=owner, request=request, status=status, result=json.loads(result),
                   created=created, seq=seq)

    # runs
    def create(self, run_id: str, owner: str, request: str, keep: int | None = None,
               req_key: str | None = None) -> Run:
        """Store a new run. With keep=N, only the owner's N most recent runs are kept; older ones are
        deleted for good (their request, code and answer), so history never grows past N per user."""
        with self._lock:
            now = time.time()
            cur = self._db.execute("INSERT INTO runs (run_id, owner, request, status, created, req_key) "
                                   "VALUES (?,?,?,?,?,?)", (run_id, owner, request, "started", now, req_key))
            if keep is not None:
                self._db.execute("DELETE FROM runs WHERE owner = ? AND seq NOT IN "
                                 "(SELECT seq FROM runs WHERE owner = ? ORDER BY seq DESC LIMIT ?)",
                                 (owner, owner, max(keep, 0)))
            return Run(run_id=run_id, owner=owner, request=request, created=now, seq=cur.lastrowid)

    def update(self, run_id: str, status: str, result: dict) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET status = ?, result = ? WHERE run_id = ?",
                             (status, json.dumps(result, ensure_ascii=False), run_id))

    def get_run(self, run_id: str) -> Run:
        """One run for the signed-in caller. Takes no user argument: the caller comes from the session."""
        caller = current_identity()
        with self._lock:
            row = self._db.execute("SELECT seq, run_id, owner, request, status, result, created FROM runs "
                                   "WHERE run_id = ?", (run_id,)).fetchone()
        run = self._row(row) if row else None
        if run is None or run.owner != caller.user_id:
            # Same answer for "missing" and "not yours": the caller learns nothing about other users.
            # Instructors too: they never browse students' history (they review only the approval queue).
            raise NotFound("Run not found. You can only view your own runs.")
        return run

    def list_runs(self) -> list[Run]:
        """The signed-in caller's own runs, newest first. No user argument; the same for every role."""
        caller = current_identity()
        with self._lock:
            rows = self._db.execute("SELECT seq, run_id, owner, request, status, result, created FROM runs "
                                    "WHERE owner = ? ORDER BY seq DESC", (caller.user_id,)).fetchall()
        return [self._row(r) for r in rows]

    def awaiting_approval(self) -> list[Run]:
        """Runs paused for an approver. Only the approval queue reads this (approvers only, checked by the caller)."""
        with self._lock:
            rows = self._db.execute("SELECT seq, run_id, owner, request, status, result, created FROM runs "
                                    "WHERE status = 'awaiting_approval' ORDER BY seq").fetchall()
        return [self._row(r) for r in rows]

    def get_for_review(self, run_id: str) -> Run:
        """One run for an approver deciding on it (snippet approvals). Not reachable from the history API."""
        with self._lock:
            row = self._db.execute("SELECT seq, run_id, owner, request, status, result, created FROM runs "
                                   "WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFound("Run not found.")
        return self._row(row)

    # idempotency
    def result_for(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT result FROM results WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def store_result(self, key: str, result: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO results (key, result) VALUES (?, ?)",
                             (key, json.dumps(result, ensure_ascii=False)))

    # sessions
    def session(self, user_id: str) -> Session:
        with self._lock:
            return self._sessions.setdefault(user_id, Session())

    def reset(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM runs")
            self._db.execute("DELETE FROM results")
            self._sessions.clear()


RUNS = RunStore()


def get_run(run_id: str) -> Run:
    return RUNS.get_run(run_id)


def list_runs() -> list[Run]:
    return RUNS.list_runs()
