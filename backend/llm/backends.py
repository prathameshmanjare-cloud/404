"""Model backends: the real Groq HTTP API, recorded cassettes, and a stub for rule tests."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable

import httpx

from config import BACKEND_DIR, groq_key
from llm.errors import CassetteMissing, ModelError

GROQ_URL = "https://api.groq.com/openai/v1"
CASSETTE_DIR = BACKEND_DIR / "evals" / "cassettes"


class GroqBackend:
    name = "groq"

    def __init__(self, timeout_s: float = 30) -> None:
        self.timeout_s = timeout_s

    def complete(self, model: str, messages: list[dict], json_mode: bool, role: str = "") -> tuple[str, dict]:
        key = groq_key()
        if not key:
            raise ModelError("GROQ_API_KEY is not set", status=401, retryable=False)
        body: dict = {"model": model, "messages": messages, "temperature": 0}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if model.startswith("qwen/"):
            body["reasoning_format"] = "hidden"   # keep thinking text out of the answer
        try:
            resp = httpx.post(f"{GROQ_URL}/chat/completions", json=body, timeout=self.timeout_s,
                              headers={"Authorization": f"Bearer {key}"})
        except httpx.TimeoutException as exc:
            raise ModelError(f"timeout calling {model}", status=None) from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"network error calling {model}: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = _parse_retry_after(resp.headers.get("retry-after"))
            raise ModelError(f"{model} returned {resp.status_code}", status=resp.status_code,
                             retry_after=retry_after)
        if resp.status_code >= 400:
            raise ModelError(f"{model} returned {resp.status_code}: {resp.text[:200]}",
                             status=resp.status_code, retryable=False)
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        return text, data.get("usage", {})

    def list_models(self) -> list[str]:
        key = groq_key()
        if not key:
            return []
        resp = httpx.get(f"{GROQ_URL}/models", timeout=self.timeout_s,
                         headers={"Authorization": f"Bearer {key}"})
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def cassette_key(model: str, messages: list[dict]) -> str:
    blob = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class ReplayBackend:
    """Serves recorded responses. Cassettes hold only masked text."""
    name = "replay"

    def __init__(self, directory: Path = CASSETTE_DIR) -> None:
        self.directory = directory

    def complete(self, model: str, messages: list[dict], json_mode: bool, role: str = "") -> tuple[str, dict]:
        path = self.directory / f"{cassette_key(model, messages)}.json"
        if not path.exists():
            raise CassetteMissing(f"no cassette for {model} ({path.name}); record with LLM_MODE=record")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["response"], data.get("usage", {})


class RecordBackend:
    """Calls the live backend and saves what it returns."""
    name = "record"

    def __init__(self, live: GroqBackend, directory: Path = CASSETTE_DIR) -> None:
        self.live = live
        self.directory = directory

    def complete(self, model: str, messages: list[dict], json_mode: bool, role: str = "") -> tuple[str, dict]:
        text, usage = self.live.complete(model, messages, json_mode, role)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{cassette_key(model, messages)}.json"
        path.write_text(json.dumps({"model": model, "messages": messages, "response": text,
                                    "usage": usage}, indent=2, ensure_ascii=False), encoding="utf-8")
        return text, usage


class StubBackend:
    """Deterministic responses per role, for unit tests and rule cases of the gate.

    responses maps a role name to a string or to a callable(messages) -> string.
    Every prompt it receives is kept in .prompts so tests can check masking.
    """
    name = "stub"

    def __init__(self, responses: dict[str, str | Callable[[list[dict]], str]]) -> None:
        self.responses = responses
        self.prompts: list[tuple[str, list[dict]]] = []
        self._lock = threading.Lock()

    def complete(self, model: str, messages: list[dict], json_mode: bool, role: str = "") -> tuple[str, dict]:
        role = role or "unknown"
        with self._lock:
            self.prompts.append((role, messages))
        if role not in self.responses:
            raise ModelError(f"stub has no response for role {role}", retryable=False)
        resp = self.responses[role]
        return (resp(messages) if callable(resp) else resp), {}

    @property
    def calls(self) -> int:
        return len(self.prompts)
