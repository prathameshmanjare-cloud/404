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
    """CODEFORGE_DB overrides the file (tests and the gate use ":memory:" so they never touch real history)."""
    path = os.getenv("CODEFORGE_DB")
    if path:
        return path
    from config import BACKEND_DIR, get_config
    rel = get_config().get("storage", {}).get("db_path", "data/codeforge.db")
    full = (BACKEND_DIR / rel).resolve()
    full.parent.mkdir(parents=True, exist_ok=True)
    return str(full)


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
            CREATE TABLE IF NOT EXISTS results (               -- idempotency key -> sandbox result
                key      TEXT PRIMARY KEY,
                result   TEXT NOT NULL
            );
        """)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _row(row) -> Run:
        seq, run_id, owner, request, status, result, created = row
        return Run(run_id=run_id, owner=owner, request=request, status=status, result=json.loads(result),
                   created=created, seq=seq)

    # runs
    def create(self, run_id: str, owner: str, request: str, keep: int | None = None) -> Run:
        """Store a new run. With keep=N, only the owner's N most recent runs are kept; older ones are
        deleted for good (their request, code and answer), so history never grows past N per user."""
        with self._lock:
            now = time.time()
            cur = self._db.execute("INSERT INTO runs (run_id, owner, request, status, created) VALUES (?,?,?,?,?)",
                                   (run_id, owner, request, "started", now))
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
        if run is None or (run.owner != caller.user_id and caller.role != "admin"):
            # Same answer for "missing" and "not yours": the caller learns nothing about other users.
            raise NotFound("Run not found. You can only view your own runs.")
        return run

    def list_runs(self) -> list[Run]:
        """The signed-in caller's runs, newest first (every run for an admin). No user argument."""
        caller = current_identity()
        sql = "SELECT seq, run_id, owner, request, status, result, created FROM runs"
        args: tuple = ()
        if caller.role != "admin":
            sql += " WHERE owner = ?"
            args = (caller.user_id,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY seq DESC", args).fetchall()
        return [self._row(r) for r in rows]

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
