"""Ingress masking (R1). Pure functions: text in, masked text out.

Keys and emails are matched by pattern. Digit runs are classified as a whole token
(separators removed) so part of a long number can never be mistaken for a shorter one:
  * 13-19 digits that pass Luhn          -> [CARD]
  * 12 digits starting 2-9 (Aadhaar)     -> [AADHAAR]
  * a leading "+" and 8-15 digits        -> [PHONE]
  * 10 digits starting 6-9 (Indian mobile) or the (xxx) xxx-xxxx form -> [PHONE]
Anything else (order numbers, 16 digits failing Luhn, dates) stays as typed.
"""
from __future__ import annotations

import logging
import re

KEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:gsk_|sk-|sk_)[A-Za-z0-9_\-]{8,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
US_PHONE_RE = re.compile(r"(?<!\d)\(\d{3}\)\s?\d{3}[\s.\-]\d{4}(?!\d)")
DIGIT_RUN_RE = re.compile(r"(?<![\w+])\+?\d(?:[ \-]?\d)*(?!\w)")


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _classify(token: str) -> str | None:
    digits = re.sub(r"\D", "", token)
    if token.startswith("+"):
        return "[PHONE]" if 8 <= len(digits) <= 15 else None
    if 13 <= len(digits) <= 19 and luhn_valid(digits):
        return "[CARD]"
    if len(digits) == 12 and digits[0] in "23456789":
        return "[AADHAAR]"
    if len(digits) == 10 and digits[0] in "6789":
        return "[PHONE]"
    return None


def mask(text: str) -> str:
    if not text:
        return text
    out = KEY_RE.sub("[SECRET]", text)
    out = EMAIL_RE.sub("[EMAIL]", out)
    out = US_PHONE_RE.sub("[PHONE]", out)

    def repl(m: re.Match) -> str:
        token = m.group(0)
        label = _classify(token)
        return label if label else token

    return DIGIT_RUN_RE.sub(repl, out)


def mask_obj(value):
    """Mask every string inside dicts/lists (for tickets and API replies)."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: mask_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_obj(v) for v in value]
    return value


class MaskingFilter(logging.Filter):
    """Defense in depth: every log line is masked even if a caller forgot."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg = mask(msg)
        record.args = ()
        return True
