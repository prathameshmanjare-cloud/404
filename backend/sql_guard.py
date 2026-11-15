"""check_sql (R7, optional): only one read-only SELECT passes. Nothing is executed."""
from __future__ import annotations

import re

from state import PolicyResult

WRITE_WORDS = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|"
                         r"replace|attach|copy|call|exec|execute|pragma|vacuum|into)\b", re.I)


def _strip(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)  # string literals cannot hide keywords
    return sql.strip()


def check_sql(sql: str) -> PolicyResult:
    body = _strip(sql).rstrip(";").strip()
    if not body:
        return PolicyResult(decision="deny", reasons=["empty query"])
    if ";" in body:
        return PolicyResult(decision="deny", reasons=["more than one statement"])
    if not re.match(r"^(select|with)\b", body, re.I):
        return PolicyResult(decision="deny", reasons=["only SELECT queries are allowed"])
    bad = sorted({m.group(1).upper() for m in WRITE_WORDS.finditer(body)})
    if bad:
        return PolicyResult(decision="deny", reasons=[f"write or instructor keyword: {', '.join(bad)}"])
    return PolicyResult(decision="allow", reasons=["single read-only SELECT"])
