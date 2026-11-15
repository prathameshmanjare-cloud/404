"""The model-backed agents. Each returns a Pydantic object plus the tier that served it."""
from __future__ import annotations

import ast
import json
import re

from pydantic import BaseModel

from agents import prompts
from llm.errors import InvalidOutput
from llm.structured import ask_structured
from state import Requirements, Route, Triage


class RouteOut(BaseModel):
    route: Route


class CodeOut(BaseModel):
    code: str


class TestsOut(BaseModel):
    tests: str


class NoteOut(BaseModel):
    note: str


class SqlOut(BaseModel):
    sql: str


def route_request(request: str, controls: dict) -> tuple[Route, str]:
    out, reply = ask_structured("router", prompts.ROUTER, request, RouteOut, controls)
    return out.route, reply.tier


def write_requirements(request: str, controls: dict, language: str = "python") -> tuple[Requirements, str]:
    user = f"Language: {LANG_NAME[language]}\nRequest: {request}"
    out, reply = ask_structured("requirements", prompts.REQUIREMENTS, user, Requirements, controls)
    return out, reply.tier


def _spec(req: Requirements) -> str:
    return json.dumps(req.model_dump(), indent=2)


LANG_NAME = {"python": "Python", "javascript": "JavaScript"}


def normalize_source(src: str, language: str = "python") -> str:
    """Repair the two ways models commonly mangle a source file inside JSON:
    a markdown fence around it, and double escaping (literal "\\n" instead of line breaks)."""
    src = (src or "").strip("\ufeff")
    fence = re.match(r"^\s*```(?:python|py|javascript|js|node)?\s*\n(.*?)\n?```\s*$", src, re.S)
    if fence:
        src = fence.group(1)
    if "\\n" in src and _syntax_error(src, language):
        try:
            unescaped = json.loads('"' + src.replace('"', '\\"').replace('\\\\"', '\\"') + '"')
        except json.JSONDecodeError:
            unescaped = src.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")
        if not _syntax_error(unescaped, language):
            src = unescaped
    return src.strip("\n") + "\n"


def _syntax_error(src: str, language: str = "python") -> str | None:
    if language == "javascript":
        from sandbox import js_syntax_error
        return js_syntax_error(src)
    try:
        ast.parse(src)
        return None
    except SyntaxError as exc:
        return f"{exc.msg} (line {exc.lineno})"


def _ask_source(role: str, system: str, user: str, schema, field: str, controls: dict,
                language: str = "python") -> tuple[str, str]:
    """Ask for a source file; normalize it, and ask once more if it still does not parse."""
    lang = LANG_NAME[language]
    out, reply = ask_structured(role, system, user, schema, controls)
    src = normalize_source(getattr(out, field), language)
    err = _syntax_error(src, language)
    if err is None:
        return src, reply.tier
    retry_user = (f"{user}\n\nYour previous answer was not valid {lang} ({err}). Put the file in the JSON "
                  f"string with real line breaks encoded once as \\n, not escaped twice.")
    out, reply = ask_structured(role, system, retry_user, schema, controls)
    src = normalize_source(getattr(out, field), language)
    err = _syntax_error(src, language)
    if err is not None:
        raise InvalidOutput(f"{role} returned invalid {lang} twice: {err}")
    return src, reply.tier


def write_code(request: str, req: Requirements, controls: dict, language: str = "python") -> tuple[str, str]:
    user = f"Request: {request}\n\nSpecification:\n{_spec(req)}"
    system = prompts.JS_CODE if language == "javascript" else prompts.CODE
    return _ask_source("code", system, user, CodeOut, "code", controls, language)


_TIMING = re.compile(r"\s*\(?\b\d+(?:\.\d+)?\s*m?s\b\)?|\bin \d+(?:\.\d+)?s\b|duration_ms [\d.]+", re.I)


def stable_output(output: str) -> str:
    """Test output without timings, so the same failure always gives the same repair prompt."""
    return _TIMING.sub("", output)


def repair_code(req: Requirements, code: str, tests: str, output: str, controls: dict,
                language: str = "python") -> tuple[str, str]:
    runner = "node --test" if language == "javascript" else "pytest"
    output = stable_output(output)
    user = (f"Specification:\n{_spec(req)}\n\nCode:\n{code}\n\nTests:\n{tests}\n\n"
            f"{runner} output (truncated):\n{output[-1500:]}")
    system = prompts.JS_REPAIR if language == "javascript" else prompts.REPAIR
    return _ask_source("code", system, user, CodeOut, "code", controls, language)


def write_tests(req: Requirements, controls: dict, language: str = "python") -> tuple[str, str]:
    user = f"Specification:\n{_spec(req)}"
    system = prompts.JS_TESTS if language == "javascript" else prompts.TESTS
    return _ask_source("tests", system, user, TestsOut, "tests", controls, language)


PII_LABEL = {"PAN": "PAN", "AADHAAR": "Aadhaar number", "CARD": "card number", "PHONE": "phone number",
             "EMAIL": "email address", "UPI": "UPI ID", "GSTIN": "GSTIN", "SECRET": "API key or token",
             "ACCOUNT": "bank account number", "PASSPORT": "passport number", "VOTER_ID": "voter ID",
             "DL": "driving licence number", "NAME": "name", "ADDRESS": "postal address",
             "DOB": "date of birth", "SSN": "US Social Security number", "IP_ADDRESS": "IP address",
             "PINCODE": "PIN code"}


class AnswerOut(BaseModel):
    answer: str


def answer_general(request: str, findings: list[dict], controls: dict) -> tuple[str, str]:
    """Answer a non-coding request. The model sees masked text plus code-verified facts only."""
    if findings:
        facts = "\n".join(f"- {f['placeholder']} (the user's {PII_LABEL.get(f['type'], f['type'].lower())}): "
                          f"{f['detail']}." for f in findings)
    else:
        facts = "- none"
    user = f"Message: {request}\n\nVerified facts (computed by code, the values themselves are hidden):\n{facts}"
    out, reply = ask_structured("general", prompts.GENERAL, user, AnswerOut, controls)
    return out.answer.strip(), reply.tier


def triage(request: str, controls: dict) -> tuple[Triage, str]:
    out, reply = ask_structured("triage", prompts.TRIAGE, request, Triage, controls)
    return out, reply.tier


def handoff_note(request: str, reason: str, controls: dict) -> str:
    user = f"User message: {request}\nWhy it was escalated: {reason}"
    out, _ = ask_structured("handoff", prompts.HANDOFF, user, NoteOut, controls)
    return out.note


def write_sql(request: str, controls: dict) -> tuple[str, str]:
    out, reply = ask_structured("sql", prompts.SQL, request, SqlOut, controls)
    return out.sql, reply.tier
