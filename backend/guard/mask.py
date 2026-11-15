"""PII redaction (R1): find personal data and secrets, check them, and mask them. Pure functions.

How it works
------------
Every kind of sensitive value is one RedactionRule below, with:
  * name         what it is: PAN, AADHAAR, CARD, ...  (also used in the placeholder: [PAN])
  * pattern      ONE regular expression, documented next to it
  * needs_words  optional words that must appear just before the match (passport, account, ...);
                 used for shapes that are too common to mask on their own
  * check        optional validation run IN CODE on the raw value (Luhn, Verhoeff, GSTIN, PAN holder)
                 returning (valid, plain-language detail) or None to reject the match

redact(text) runs every rule, keeps the winning match where two overlap (rules earlier in RULES win),
replaces each value with its placeholder ([PAN], then [PAN_2] for a second one) and returns the
masked text plus one Finding per value: its type, placeholder, validity and detail. The value itself
is never stored in a Finding, so a model can be told "the PAN format is valid" without seeing the PAN.

Digit-based rules use DIGITS(...), which only matches a WHOLE run of digits (spaces or dashes between
digits allowed), so part of a long number can never be mistaken for a shorter one. Numbers that match
no rule (order numbers, dates, a 16-digit number failing Luhn with no "card" nearby) stay as typed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Callable

# =====================================================================================================
# Validators: checks done in code on the raw value. A model never runs these and never sees the value.
# =====================================================================================================
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
    """Aadhaar's check digit algorithm."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(digits: str) -> bool:
    """Card numbers' check digit algorithm."""
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
    """GSTIN's 15th character is a mod-36 check character over the first 14."""
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


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


# =====================================================================================================
# Rules: one named regex per kind of sensitive value. Order matters: on an overlap, the earlier rule wins.
# =====================================================================================================
Check = Callable[[str, str, int], "tuple[bool | None, str] | None"]   # (value, whole text, start) -> result


@dataclass(frozen=True)
class RedactionRule:
    name: str
    pattern: re.Pattern
    description: str
    check: Check | None = None
    needs_words: tuple[str, ...] = ()   # one of these must appear in the 40 characters before the match
    group: int = 0                      # mask only this capture group ("my name is <Rahul Sharma>")

    @property
    def placeholder(self) -> str:
        return f"[{self.name}]"


def DIGITS(lo: int, hi: int, first: str = r"\d") -> str:
    """A whole run of lo..hi digits, single spaces or dashes allowed between digits, nothing glued on."""
    return (rf"(?<![\w+])(?<!\d[ \-]){first}(?:[ \-]?\d){{{lo - 1},{hi - 1}}}"
            rf"(?![ \-]?\d)(?!\w)")


def _fixed(detail: str, valid: bool | None = True) -> Check:
    return lambda value, text, start: (valid, detail)


def _check_pan(value: str, text: str, start: int):
    p = value.upper()
    holder = PAN_HOLDER.get(p[3])
    if holder:
        return True, (f"the PAN format is valid (5 letters, 4 digits, 1 letter); its 4th letter '{p[3]}' means it "
                      f"belongs to {holder}. A format check cannot confirm the PAN was issued: verify it on the "
                      f"Income Tax e-filing portal")
    return False, (f"the PAN is invalid: it has the PAN shape, but its 4th letter '{p[3]}' is not a valid holder "
                   f"type (allowed: {', '.join(PAN_HOLDER)})")


def _check_gstin(value: str, text: str, start: int):
    if gstin_valid(value.upper()):
        return True, "the GSTIN format and check character are valid"
    return False, "the GSTIN has the right shape but its check character is wrong (likely a typo)"


def _check_card(value: str, text: str, start: int):
    d = _digits(value)
    if luhn_valid(d):
        return True, f"the card number passes the Luhn checksum ({_card_brand(d)})"
    if _words_before(text, start, ("card", "debit", "credit")):
        return False, "the card number fails the Luhn checksum, so it has a typo or is not a real card"
    return None   # 13-19 digits failing Luhn with no "card" nearby: a reference number, not a card


def _check_aadhaar(value: str, text: str, start: int):
    if verhoeff_valid(_digits(value)):
        return True, "the Aadhaar number has a valid format and passes the Verhoeff checksum"
    return False, ("the Aadhaar number has 12 digits but fails the Verhoeff checksum, so it has a typo or is not "
                   "a real Aadhaar")


RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        "SECRET",
        # API keys and tokens: OpenAI sk-/sk_, Groq gsk_, GitHub ghp_, Slack xoxb-/xoxa-/xoxp-, AWS AKIA
        re.compile(r"(?<![A-Za-z0-9])(?:gsk_|sk-|sk_|ghp_|xox[bap]-|AKIA)[A-Za-z0-9_\-]{8,}"),
        "API keys and tokens", _fixed("an API key or token", None)),
    RedactionRule(
        "EMAIL",
        # name@domain.tld
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        "Email addresses", _fixed("the email address has a valid format")),
    RedactionRule(
        "NAME",
        # A person's name after "my name is" / "name:" (1-3 words); the phrase itself stays
        re.compile(r"\b(?:my\s+(?:full\s+)?name\s+is|my\s+name's|(?:full\s+)?name\s*[:=])\s+"
                   # not a type annotation ("name: str") or a placeholder word
                   r"(?!(?:str|string|int|integer|float|bool|boolean|list|dict|tuple|set|any|none|optional|text|"
                   r"varchar|char|number|object|bytes|self)\b)"
                   r"([A-Za-z][A-Za-z.'\-]+(?:\s+(?!(?:and|i|my|is|from|with|but|so|please|age|aged)\b)"
                   r"[A-Za-z][A-Za-z.'\-]+){0,2})", re.I),
        "Personal names", _fixed("a person's name", None), group=1),
    RedactionRule(
        "ADDRESS",
        # A postal address after "my address is" / "address:" / "I live at", up to the end of the sentence
        re.compile(r"\b(?:my\s+(?:home\s+|postal\s+)?address\s+is|(?:home\s+|postal\s+)?address\s*[:=]|"
                   r"i\s+live\s+at|i\s+stay\s+at|residing\s+at)\s+([^.;!?\n]{4,120})", re.I),
        "Postal addresses", _fixed("a postal address", None), group=1),
    RedactionRule(
        "DOB",
        # A date of birth after "DOB" / "date of birth" / "born on" / "birthday": 12/05/1990, 1990-05-12, 12 May 1990
        re.compile(r"\b(?:d\.?o\.?b\.?|date\s+of\s+birth|born(?:\s+on)?|birth\s*day)\b\W{0,4}(?:is\s+)?"
                   r"(\d{1,2}[/.\- ]\d{1,2}[/.\- ]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|"
                   r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
                   re.I),
        "Dates of birth", _fixed("a date of birth", None), group=1),
    RedactionRule(
        "SSN",
        # US Social Security number: 123-45-6789
        re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
        "US Social Security numbers", _fixed("a US Social Security number", None)),
    RedactionRule(
        "IP_ADDRESS",
        # IPv4 address: 192.168.1.10 (each part 0-255)
        re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
                   r"(?![\d.])"),
        "IP addresses", _fixed("an IP address", None)),
    RedactionRule(
        "UPI",
        # name@bank with no dot after the @ (an email has a domain with a dot)
        re.compile(r"(?<![\w.@])[A-Za-z0-9.\-_]{2,}@[A-Za-z]{2,20}(?![\w.@])"),
        "UPI payment IDs", _fixed("the UPI ID has a valid format (name@bank)")),
    RedactionRule(
        "GSTIN",
        # 2-digit state code + PAN + entity number + Z + check character, e.g. 27AAPFU0939F1ZV
        re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I),
        "GST identification numbers", _check_gstin),
    RedactionRule(
        "PAN",
        # 5 letters, 4 digits, 1 letter, e.g. ABCPE1234F (4th letter = holder type)
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I),
        "Permanent Account Numbers", _check_pan),
    RedactionRule(
        "VOTER_ID",
        # EPIC: 3 letters, 7 digits, e.g. ABC1234567
        re.compile(r"\b[A-Z]{3}\d{7}\b", re.I),
        "Voter ID (EPIC) numbers", _fixed("a voter ID (EPIC) number", None)),
    RedactionRule(
        "PASSPORT",
        # Indian passport: 1 letter, 7 digits, e.g. A1234567 (too common a shape: needs "passport" nearby)
        re.compile(r"\b[A-Z][1-9]\d{6}\b", re.I),
        "Passport numbers", _fixed("the passport number has a valid Indian format"),
        needs_words=("passport",)),
    RedactionRule(
        "DL",
        # Driving licence: state code, RTO code, year, number, e.g. MH12 2011 0012345 (needs a keyword)
        re.compile(r"\b[A-Z]{2}[ -]?\d{2}[ -]?\d{4}[ -]?\d{7}\b", re.I),
        "Driving licence numbers", _fixed("a driving licence number", None),
        needs_words=("licence", "license", "dl ", "driving")),
    RedactionRule(
        "PHONE",
        # International number written with a country code: +<8-15 digits>, e.g. +91 98765 43210
        re.compile(r"(?<![\w+])\+\d(?:[ \-]?\d){7,14}(?![ \-]?\d)(?!\w)"),
        "Phone numbers with a country code", _fixed("a phone number with a country code")),
    RedactionRule(
        "ACCOUNT",
        # Bank account: 9-18 digits, only with "account"/"a/c" nearby (no public checksum exists)
        re.compile(DIGITS(9, 18)),
        "Bank account numbers", _fixed("a bank account number (it has no public checksum to verify)", None),
        needs_words=("account", "a/c", "acct", "acc no", "acc. no")),
    RedactionRule(
        "PINCODE",
        # Indian PIN code: 6 digits starting 1-9, only with "pin" / "pincode" / "postal" / "zip" nearby
        re.compile(DIGITS(6, 6, first="[1-9]")),
        "Postal PIN codes", _fixed("a postal PIN code", None),
        needs_words=("pin", "pincode", "pin code", "postal", "zip")),
    RedactionRule(
        "CARD",
        # Payment card: 13-19 digits; masked when it passes Luhn, or with "card" nearby
        re.compile(DIGITS(13, 19)),
        "Payment card numbers", _check_card),
    RedactionRule(
        "AADHAAR",
        # Aadhaar: 12 digits, first digit 2-9, usually written 4-4-4 (masked even if the checksum fails)
        re.compile(DIGITS(12, 12, first="[2-9]")),
        "Aadhaar numbers", _check_aadhaar),
    RedactionRule(
        "PHONE",
        # Indian mobile: 10 digits starting 6-9, e.g. 98765 43210
        re.compile(DIGITS(10, 10, first="[6-9]")),
        "Indian mobile numbers", _fixed("a valid Indian mobile number format")),
    RedactionRule(
        "PHONE",
        # North American style: (415) 555-0100
        re.compile(r"(?<!\d)\(\d{3}\)\s?\d{3}[\s.\-]\d{4}(?!\d)"),
        "Phone numbers written as (xxx) xxx-xxxx", _fixed("a phone number")),
)


# =====================================================================================================
# redact(): run the rules, resolve overlaps, replace values with placeholders.
# =====================================================================================================
@dataclass
class Finding:
    type: str              # PAN, AADHAAR, ...
    placeholder: str       # [PAN], [PAN_2], ...
    valid: bool | None     # None = nothing to check
    detail: str            # plain-language result of the checks, never the value itself

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Redaction:
    text: str                  # the masked text
    findings: list[Finding]    # one per masked value, in order of appearance


def _words_before(text: str, start: int, words: tuple[str, ...], span: int = 40) -> bool:
    before = text[max(0, start - span):start].lower()
    return any(w in before for w in words)


def _matches(text: str) -> list[tuple[int, int, int, RedactionRule, bool | None, str]]:
    """Every rule's matches as (start, end, rule order, rule, valid, detail), before overlaps are resolved."""
    found = []
    for order, rule in enumerate(RULES):
        for m in rule.pattern.finditer(text):
            start, end = m.span(rule.group)
            if rule.needs_words and not _words_before(text, start, rule.needs_words):
                continue
            value = m.group(rule.group)
            result = rule.check(value, text, start) if rule.check else (None, rule.description)
            if result is None:
                continue
            found.append((start, end, order, rule, *result))
    return found


def redact(text: str) -> Redaction:
    """Mask every sensitive value in text. See the module docstring for how the rules are applied."""
    if not text:
        return Redaction(text, [])
    chosen = []
    for match in sorted(_matches(text), key=lambda m: (m[2], m[0])):           # earlier rules first
        if all(match[1] <= s or match[0] >= e for s, e, *_ in chosen):         # skip overlaps
            chosen.append(match)
    chosen.sort(key=lambda m: m[0])

    counts: dict[str, int] = {}
    findings: list[Finding] = []
    pieces, pos = [], 0
    for start, end, _, rule, valid, detail in chosen:
        counts[rule.name] = counts.get(rule.name, 0) + 1
        n = counts[rule.name]
        placeholder = rule.placeholder if n == 1 else f"[{rule.name}_{n}]"
        pieces += [text[pos:start], placeholder]
        pos = end
        findings.append(Finding(rule.name, placeholder, valid, detail))
    pieces.append(text[pos:])
    return Redaction("".join(pieces), findings)


def find_pii(text: str) -> tuple[str, list[Finding]]:
    """redact() as a (masked text, findings) pair."""
    r = redact(text)
    return r.text, r.findings


def mask(text: str) -> str:
    """Just the masked text."""
    return redact(text).text if text else text


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
