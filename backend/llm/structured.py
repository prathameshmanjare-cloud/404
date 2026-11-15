"""Ask a model for JSON and validate it with Pydantic; one repair attempt on bad output."""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from llm.client import ModelReply, call_model
from llm.errors import ModelUnavailable

T = TypeVar("T", bound=BaseModel)


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def ask_structured(role: str, system: str, user: str, schema: type[T],
                   controls: dict | None = None) -> tuple[T, ModelReply]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error = ""
    for _ in range(2):
        reply = call_model(role, messages, controls)
        try:
            return schema.model_validate(_extract_json(reply.text)), reply
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:300]
            messages = messages + [
                {"role": "assistant", "content": reply.text[:2000]},
                {"role": "user", "content": f"That reply was not valid JSON for the schema ({last_error}). "
                                            "Reply again with only the JSON object."},
            ]
    raise ModelUnavailable(f"{role} returned invalid JSON twice: {last_error}")
