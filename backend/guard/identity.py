"""Identity (R1). The role comes from config only; message text is never an input."""
from __future__ import annotations

from dataclasses import dataclass


class UnknownUser(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str
    name: str


def get_identity(config: dict, user_id: str) -> Identity:
    users = config.get("users", {})
    if user_id not in users:
        raise UnknownUser(f"unknown user {user_id!r}")
    entry = users[user_id]
    return Identity(user_id=user_id, role=entry["role"], name=entry.get("name", user_id))


def can_approve(config: dict, identity: Identity) -> bool:
    return identity.role in set(config.get("approver_roles", []))
