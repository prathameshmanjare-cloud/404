"""Triage (R3): one small-model call, merged with rules in code that the model cannot talk past.

If the model is unavailable the rules alone decide, so escalation still works in an outage.
"""
from __future__ import annotations

import logging
import re

from agents.agents import triage as model_triage
from llm.errors import ModelUnavailable
from state import Triage

log = logging.getLogger("codeforge.triage")

HUMAN_RE = re.compile(
    r"\b(get|give|want|need|talk to|speak to|connect me (to|with)|let me talk to)\b[^.!?]{0,20}"
    r"\b(a |an |the |some )?(human|person|real person|agent|someone|staff|support|admin)\b"
    r"|\bhuman please\b|\bescalate\b",
    re.I,
)
UNHAPPY_RE = re.compile(r"\b(still|again|not working|doesn'?t work|broken|useless|frustrat\w*|annoy\w*|"
                        r"waste|ridiculous|terrible|worst|hate)\b", re.I)
URGENT_RE = re.compile(r"\b(urgent|asap|immediately|right now|deadline|exam in)\b", re.I)


def heuristic_triage(text: str) -> Triage:
    letters = [c for c in text if c.isalpha()]
    caps_ratio = sum(c.isupper() for c in letters) / len(letters) if len(letters) >= 8 else 0.0
    bangs = text.count("!")
    angry = caps_ratio > 0.6 or bangs >= 3
    unhappy = bool(UNHAPPY_RE.search(text))
    if angry:
        sentiment, score = "angry", -0.9
    elif unhappy:
        sentiment, score = "unhappy", -0.4
    else:
        sentiment, score = "neutral", 0.0
    return Triage(
        sentiment=sentiment,
        sentiment_score=score,
        urgency="high" if angry or URGENT_RE.search(text) else "normal",
        wants_human=bool(HUMAN_RE.search(text)),
    )


_ORDER = {"angry": 0, "unhappy": 1, "neutral": 2, "positive": 3}


def triage_request(text: str, controls: dict) -> tuple[Triage, str]:
    """Returns the merged triage and the tier that served it ("none" = rules only)."""
    rules = heuristic_triage(text)
    try:
        model, tier = model_triage(text, controls)
    except ModelUnavailable as exc:
        log.warning("triage model unavailable, rules only: %s", exc)
        return rules, "none"
    # The more severe reading wins for every field.
    sentiment = min(model.sentiment, rules.sentiment, key=lambda s: _ORDER[s])
    return Triage(
        task=model.task,
        language=model.language,
        sentiment=sentiment,
        sentiment_score=min(model.sentiment_score, rules.sentiment_score),
        urgency="high" if "high" in (model.urgency, rules.urgency) else model.urgency,
        wants_human=model.wants_human or rules.wants_human,
    ), tier
