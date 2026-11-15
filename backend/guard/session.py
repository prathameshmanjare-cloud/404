"""Sessions and the current identity (R1).

Sign-in happens once (POST /session) against config.yaml and returns an opaque token. Every later
request carries only that token, so the server decides who the caller is; a client cannot name another
user in a body or query string. While a request is handled, the caller's identity sits in a context
variable, so functions that return past runs take no user argument at all: they read current_identity().
"""
from __future__ import annotations

import contextvars
import secrets
import threading
from contextlib import contextmanager

from guard.identity import Identity, UnknownUser, get_identity

_current: contextvars.ContextVar[Identity | None] = contextvars.ContextVar("codesmith_identity", default=None)


class NotSignedIn(Exception):
    """No valid session for this request."""


def current_identity() -> Identity:
    ident = _current.get()
    if ident is None:
        raise NotSignedIn("You are not signed in. Choose a user under \"Signed in as\" to start a session.")
    return ident


@contextmanager
def acting_as(identity: Identity):
    """Run a block as the given, already verified identity (set at the edge: the API or the CLI)."""
    token = _current.set(identity)
    try:
        yield identity
    finally:
        _current.reset(token)


class Sessions:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}   # token -> user_id
        self._lock = threading.Lock()

    def create(self, config: dict, user_id: str) -> tuple[str, Identity]:
        identity = get_identity(config, user_id)          # raises UnknownUser for anyone not in config
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = user_id
        return token, identity

    def resolve(self, config: dict, token: str | None) -> Identity:
        """The identity behind a token. The role is re-read from config on every request."""
        user_id = self._tokens.get(token or "")
        if user_id is None:
            raise NotSignedIn("Your session is missing or has expired. Choose a user under \"Signed in as\" "
                              "to sign in again.")
        try:
            return get_identity(config, user_id)
        except UnknownUser as exc:   # the user was removed from config after signing in
            raise NotSignedIn("This account no longer exists in config.yaml.") from exc

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()


SESSIONS = Sessions()
