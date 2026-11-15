"""Per-user run quota (R1), counted in code before any model call."""
from __future__ import annotations

import threading

from guard.identity import Identity


class QuotaTracker:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def check_and_count(self, config: dict, identity: Identity) -> bool:
        """Returns True and counts the run if allowed; False if over quota."""
        limit = int(config.get("quota", {}).get(identity.role, 0))
        with self._lock:
            used = self._counts.get(identity.user_id, 0)
            if used >= limit:
                return False
            self._counts[identity.user_id] = used + 1
            return True

    def used(self, user_id: str) -> int:
        return self._counts.get(user_id, 0)

    def set_used(self, user_id: str, n: int) -> None:
        with self._lock:
            self._counts[user_id] = n

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


QUOTA = QuotaTracker()


def check_quota(config: dict, identity: Identity) -> bool:
    return QUOTA.check_and_count(config, identity)
