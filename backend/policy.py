"""evaluate_code_policy (R2): parses with ast, never executes. Deny wins over approval.

This is a policy layer, not a security boundary; the sandbox is the second layer.
"""
from __future__ import annotations

import ast

from state import PolicyResult

# Call names refused wherever they appear (matched on the last part of the dotted name,
# so os.system, posix.system and an aliased "from os import system" are all caught).
DENY_NAMES = {
    "system", "popen", "execv", "execve", "execl", "execvp", "spawnl", "spawnv", "fork", "kill", "killpg",
    "unlink", "rmdir", "removedirs", "rmtree", "chmod", "chown", "truncate",
}
# Builtins that run or reach arbitrary code. Refused when used as a bare name (called or passed around),
# so re.compile() or obj.vars stay fine while eval(), f = exec and getattr(x, "__class__") do not.
DENY_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint", "getattr", "setattr", "delattr",
                 "globals", "locals", "vars"}
# Ambiguous short names refused only when they come from os or shutil (list.remove stays fine).
DENY_QUALIFIED = {"os.remove", "os.rename", "os.replace", "shutil.move", "shutil.copy", "shutil.copytree"}
# Modules that are refused on import.
DENY_MODULES = {"subprocess", "ctypes", "pty", "multiprocessing", "signal", "importlib", "builtins",
                "marshal", "pickle", "shelve", "code", "codeop"}
# Modules or calls that touch files or the network: allowed only after a person approves.
APPROVAL_MODULES = {"os", "pathlib", "shutil", "socket", "requests", "urllib", "http", "ftplib",
                    "smtplib", "tempfile", "glob", "io", "sqlite3", "asyncio", "threading"}
APPROVAL_CALLS = {"open", "input", "Path", "read_text", "write_text", "read_bytes", "write_bytes",
                  "mkdir", "iterdir", "glob"}
# Imports known to be harmless; anything not here and not above fails closed to approval.
SAFE_MODULES = {"math", "cmath", "string", "re", "collections", "itertools", "functools", "operator",
                "typing", "dataclasses", "decimal", "fractions", "statistics", "random", "heapq",
                "bisect", "enum", "abc", "datetime", "json", "copy", "textwrap", "unicodedata",
                "__future__", "numbers", "array", "calendar", "pytest", "solution", "time",
                "hashlib", "base64", "uuid", "secrets", "zoneinfo", "contextlib", "pprint", "unittest",
                "graphlib", "difflib", "binascii", "struct", "csv", "textwrap", "html"}
DENY_ATTRS = {"__subclasses__", "__globals__", "__builtins__", "__code__", "__bases__", "__mro__",
              "__dict__", "__getattribute__", "__reduce__", "__reduce_ex__", "f_globals", "f_locals"}


def _call_name(node: ast.AST, aliases: dict[str, str]) -> str:
    """Dotted name of a call target with import aliases resolved (np.x -> numpy.x)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
    elif isinstance(node, ast.Call):
        inner = _call_name(node.func, aliases)
        if inner:
            parts.append(inner)
    return ".".join(reversed(parts))


def evaluate_code_policy(code: str) -> PolicyResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return PolicyResult(decision="deny", reasons=[f"code does not parse: {exc.msg} (line {exc.lineno})"])

    deny: list[str] = []
    approve: list[str] = []
    aliases: dict[str, str] = {}

    def check_module(name: str) -> None:
        root = name.split(".")[0]
        if root in DENY_MODULES:
            deny.append(f"imports {name}")
        elif root in APPROVAL_MODULES:
            approve.append(f"imports {name} (file, OS or network access)")
        elif root not in SAFE_MODULES:
            approve.append(f"imports unknown module {name}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                check_module(a.name)
                aliases[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            check_module(mod)
            for a in node.names:
                aliases[a.asname or a.name] = f"{mod}.{a.name}"
        elif isinstance(node, ast.Call):
            name = _call_name(node.func, aliases)
            short = name.split(".")[-1]
            tail2 = ".".join(name.split(".")[-2:])
            if short in DENY_NAMES or tail2 in DENY_QUALIFIED or name in DENY_BUILTINS:
                deny.append(f"calls {name}")
            elif short in APPROVAL_CALLS or tail2 in APPROVAL_CALLS:
                approve.append(f"calls {name} (file or console access)")
        elif isinstance(node, ast.Attribute) and node.attr in DENY_ATTRS:
            deny.append(f"uses {node.attr}")
        elif isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__spec__"}:
            deny.append(f"uses {node.id}")
        elif isinstance(node, ast.Name) and node.id in DENY_BUILTINS and isinstance(node.ctx, ast.Load):
            deny.append(f"uses {node.id}")

    if deny:
        return PolicyResult(decision="deny", reasons=sorted(set(deny)))
    if approve:
        return PolicyResult(decision="needs_approval", reasons=sorted(set(approve)))
    return PolicyResult(decision="allow", reasons=["no file, OS, network or dynamic-code access found"])
