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


def write_requirements(request: str, controls: dict) -> tuple[Requirements, str]:
    out, reply = ask_structured("requirements", prompts.REQUIREMENTS, request, Requirements, controls)
    return out, reply.tier


def _spec(req: Requirements) -> str:
    return json.dumps(req.model_dump(), indent=2)


def normalize_source(src: str) -> str:
    """Repair the two ways models commonly mangle a source file inside JSON:
    a markdown fence around it, and double escaping (literal "\\n" instead of line breaks)."""
    src = (src or "").strip("\ufeff")
    fence = re.match(r"^\s*```(?:python|py)?\s*\n(.*?)\n?```\s*$", src, re.S)
    if fence:
        src = fence.group(1)
    if "\\n" in src:
        try:
            ast.parse(src)
        except SyntaxError:
            try:
                unescaped = json.loads('"' + src.replace('"', '\\"').replace('\\\\"', '\\"') + '"')
            except json.JSONDecodeError:
                unescaped = src.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")
            try:
                ast.parse(unescaped)
                src = unescaped
            except SyntaxError:
                pass
    return src.strip("\n") + "\n"


def _syntax_error(src: str) -> str | None:
    try:
        ast.parse(src)
        return None
    except SyntaxError as exc:
        return f"{exc.msg} (line {exc.lineno})"


def _ask_source(role: str, system: str, user: str, schema, field: str, controls: dict) -> tuple[str, str]:
    """Ask for a Python source file; normalize it, and ask once more if it still does not parse."""
    out, reply = ask_structured(role, system, user, schema, controls)
    src = normalize_source(getattr(out, field))
    err = _syntax_error(src)
    if err is None:
        return src, reply.tier
    retry_user = (f"{user}\n\nYour previous answer was not valid Python ({err}). Put the file in the JSON "
                  f"string with real line breaks encoded once as \\n, not escaped twice.")
    out, reply = ask_structured(role, system, retry_user, schema, controls)
    src = normalize_source(getattr(out, field))
    err = _syntax_error(src)
    if err is not None:
        raise InvalidOutput(f"{role} returned invalid Python twice: {err}")
    return src, reply.tier


def write_code(request: str, req: Requirements, controls: dict) -> tuple[str, str]:
    user = f"Request: {request}\n\nSpecification:\n{_spec(req)}"
    return _ask_source("code", prompts.CODE, user, CodeOut, "code", controls)


def repair_code(req: Requirements, code: str, tests: str, output: str, controls: dict) -> tuple[str, str]:
    user = (f"Specification:\n{_spec(req)}\n\nCode:\n{code}\n\nTests:\n{tests}\n\n"
            f"pytest output (truncated):\n{output[-1500:]}")
    return _ask_source("code", prompts.REPAIR, user, CodeOut, "code", controls)


def write_tests(req: Requirements, controls: dict) -> tuple[str, str]:
    user = f"Specification:\n{_spec(req)}"
    return _ask_source("tests", prompts.TESTS, user, TestsOut, "tests", controls)


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
