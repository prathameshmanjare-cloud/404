"""PII detection, validation and masking (R1). Pure functions.

find_pii(text) returns the masked text plus one finding per item: its type, the placeholder that
replaced it, and whether its format/checksum is valid. Validation runs here, in code, on the raw
value, so a model can answer "is my PAN correct?" from the finding without ever seeing the PAN.

Detected (placeholder):
  API keys [SECRET] · email [EMAIL] · UPI id [UPI] · PAN [PAN] · GSTIN [GSTIN] · Aadhaar [AADHAAR]
  · card number [CARD] · phone [PHONE] · voter ID [VOTER_ID] · passport [PASSPORT] (with context)
  · bank account [ACCOUNT] (with context) · driving licence [DL] (with context) · IFSC is not PII.
Digit runs are classified as a whole token, so part of a long number is never mistaken for a
shorter one. Order numbers, dates and 16-digit numbers that fail Luhn (without the word "card"
nearby) stay as typed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

# ---------------------------------------------------------------- validators
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


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


PAN_HOLDER = {
    "P": "an individual", "C": "a company", "H": "a Hindu Undivided Family (HUF)", "F": "a firm or LLP",
    "A": "an association of persons (AOP)", "T": "a trust", "B": "a body of individuals (BOI)",
    "L": "a local authority", "J": "an artificial juridical person", "G": "a government agency",
}
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_valid(g: str) -> bool:
    total = 0
    for i, ch in enumerate(g[:14]):
        p = _B36.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return g[14] == _B36[(36 - total % 36) % 36]


def _card_brand(d: str) -> str:
    if d.startswith("4"):
        return "Visa"
    if d[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(d[:4]) <= 2720:
        return "Mastercard"
    if d[:2] in {"34", "37"}:
        return "American Express"
    if d[:2] in {"60", "65", "81", "82"} or d[:3] == "508":
        return "RuPay or Discover"
    return "unknown network"


# ---------------------------------------------------------------- detection
@dataclass
class Finding:
    type: str              # PAN, AADHAAR, ...
    placeholder: str       # [PAN], [PAN_2], ...
    valid: bool | None     # None = format only, nothing to check
    detail: str            # plain-language result of the checks, never the value itself

    def to_dict(self) -> dict:
        return asdict(self)


KEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:gsk_|sk-|sk_|ghp_|xox[bap]-|AKIA)[A-Za-z0-9_\-]{8,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
UPI_RE = re.compile(r"(?<![\w.@])[A-Za-z0-9.\-_]{2,}@[A-Za-z]{2,20}(?![\w.@])")
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I)
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I)
VOTER_RE = re.compile(r"\b[A-Z]{3}\d{7}\b", re.I)
PASSPORT_RE = re.compile(r"\b[A-Z][1-9]\d{6}\b", re.I)
DL_RE = re.compile(r"\b[A-Z]{2}[ -]?\d{2}[ -]?\d{4}[ -]?\d{7}\b", re.I)
US_PHONE_RE = re.compile(r"(?<!\d)\(\d{3}\)\s?\d{3}[\s.\-]\d{4}(?!\d)")
DIGIT_RUN_RE = re.compile(r"(?<![\w+])\+?\d(?:[ \-]?\d)*(?!\w)")


def _near(text: str, start: int, words: tuple[str, ...], span: int = 40) -> bool:
    before = text[max(0, start - span):start].lower()
    return any(w in before for w in words)


def _candidates(text: str) -> list[tuple[int, int, int, str, bool | None, str]]:
    """(start, end, priority, type, valid, detail). Lower priority number wins an overlap."""
    out = []
    for m in KEY_RE.finditer(text):
        out.append((m.start(), m.end(), 0, "SECRET", None, "an API key or token"))
    for m in EMAIL_RE.finditer(text):
        out.append((m.start(), m.end(), 1, "EMAIL", True, "the email address has a valid format"))
    for m in UPI_RE.finditer(text):
        out.append((m.start(), m.end(), 2, "UPI", True, "the UPI ID has a valid format (name@bank)"))
    for m in GSTIN_RE.finditer(text):
        g = m.group(0).upper()
        ok = gstin_valid(g)
        out.append((m.start(), m.end(), 3, "GSTIN", ok,
                    "the GSTIN format and check character are valid" if ok
                    else "the GSTIN has the right shape but its check character is wrong (likely a typo)"))
    for m in PAN_RE.finditer(text):
        p = m.group(0).upper()
        holder = PAN_HOLDER.get(p[3])
        if holder:
            detail = (f"the PAN format is valid (5 letters, 4 digits, 1 letter); its 4th letter '{p[3]}' "
                      f"means it belongs to {holder}. A format check cannot confirm the PAN was issued: "
                      f"verify it on the Income Tax e-filing portal")
        else:
            detail = (f"the PAN is invalid: it has the PAN shape, but its 4th letter '{p[3]}' is not a valid "
                      f"holder type (allowed: {', '.join(PAN_HOLDER)})")
        out.append((m.start(), m.end(), 4, "PAN", holder is not None, detail))
    for m in VOTER_RE.finditer(text):
        out.append((m.start(), m.end(), 5, "VOTER_ID", None, "a voter ID (EPIC) number"))
    for m in PASSPORT_RE.finditer(text):
        if _near(text, m.start(), ("passport",)):
            out.append((m.start(), m.end(), 5, "PASSPORT", True, "the passport number has a valid Indian format"))
    for m in DL_RE.finditer(text):
        if _near(text, m.start(), ("licence", "license", "dl ", "driving")):
            out.append((m.start(), m.end(), 5, "DL", None, "a driving licence number"))
    for m in US_PHONE_RE.finditer(text):
        out.append((m.start(), m.end(), 6, "PHONE", True, "a phone number"))
    for m in DIGIT_RUN_RE.finditer(text):
        found = _classify_digits(text, m)
        if found:
            out.append((m.start(), m.end(), 7, *found))
    return out


def _classify_digits(text: str, m: re.Match) -> tuple[str, bool | None, str] | None:
    token = m.group(0)
    d = re.sub(r"\D", "", token)
    if token.startswith("+"):
        if 8 <= len(d) <= 15:
            return "PHONE", True, "a phone number with a country code"
        return None
    if _near(text, m.start(), ("account", "a/c", "acct", "acc no", "acc. no")) and 9 <= len(d) <= 18:
        return "ACCOUNT", None, "a bank account number (it has no public checksum to verify)"
    if 13 <= len(d) <= 19:
        if luhn_valid(d):
            return "CARD", True, f"the card number passes the Luhn checksum ({_card_brand(d)})"
        if _near(text, m.start(), ("card", "debit", "credit")):
            return "CARD", False, "the card number fails the Luhn checksum, so it has a typo or is not a real card"
        return None
    if len(d) == 12 and d[0] in "23456789":
        if verhoeff_valid(d):
            return "AADHAAR", True, "the Aadhaar number has a valid format and passes the Verhoeff checksum"
        return "AADHAAR", False, ("the Aadhaar number has 12 digits but fails the Verhoeff checksum, "
                                  "so it has a typo or is not a real Aadhaar")
    if len(d) == 10 and d[0] in "6789":
        return "PHONE", True, "a valid Indian mobile number format"
    return None


def find_pii(text: str) -> tuple[str, list[Finding]]:
    if not text:
        return text, []
    chosen: list[tuple[int, int, int, str, bool | None, str]] = []
    for c in sorted(_candidates(text), key=lambda c: (c[2], c[0])):
        if all(c[1] <= s or c[0] >= e for s, e, *_ in chosen):
            chosen.append(c)
    chosen.sort(key=lambda c: c[0])
    counts: dict[str, int] = {}
    findings: list[Finding] = []
    pieces, pos = [], 0
    for start, end, _, kind, valid, detail in chosen:
        counts[kind] = counts.get(kind, 0) + 1
        ph = f"[{kind}]" if counts[kind] == 1 else f"[{kind}_{counts[kind]}]"
        pieces.append(text[pos:start])
        pieces.append(ph)
        pos = end
        findings.append(Finding(kind, ph, valid, detail))
    pieces.append(text[pos:])
    return "".join(pieces), findings


def mask(text: str) -> str:
    return find_pii(text)[0] if text else text


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
        # Mask the message and each argument in place, keeping the args' shape: some formatters
        # (uvicorn's access log) unpack record.args themselves.
        if isinstance(record.msg, str):
            record.msg = mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: mask(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True
