"""evaluate_js_policy (R2 for JavaScript): a static scan, never executes. Deny wins over approval.

Like the Python policy this is a policy layer, not a security boundary. The JavaScript sandbox adds
Node's permission model (no child processes, files only inside the run folder) and blocks the network.
"""
from __future__ import annotations

import re

from state import PolicyResult

SAFE_MODULES = {"assert", "assert/strict", "test", "util", "events", "string_decoder", "url", "querystring",
                "buffer", "path", "crypto", "timers", "timers/promises", "stream", "zlib", "perf_hooks"}
APPROVAL_MODULES = {"fs", "fs/promises", "net", "http", "https", "http2", "dgram", "dns", "dns/promises",
                    "tls", "os", "readline", "readline/promises"}
DENY_MODULES = {"child_process", "vm", "worker_threads", "cluster", "v8", "inspector", "module", "process",
                "repl", "wasi", "trace_events"}

DENY_PATTERNS = [
    (re.compile(r"\beval\s*\("), "calls eval"),
    (re.compile(r"\bnew\s+Function\b|(?<![\w.])Function\s*\("), "builds code with Function"),
    (re.compile(r"(?<![\w.])import\s*\("), "uses dynamic import()"),
    (re.compile(r"\bprocess\s*\.\s*(binding|_linkedBinding|dlopen|env|exit|kill|chdir|setuid|setgid|mainModule|"
                r"reallyExit|abort)\b"), "uses a dangerous process API"),
    (re.compile(r"\bprocess\s*\["), "indexes into process"),
    (re.compile(r"\bconstructor\s*\.\s*constructor\b|\[\s*['\"]constructor['\"]\s*\]"), "reaches the Function constructor"),
    (re.compile(r"__proto__|Object\s*\.\s*setPrototypeOf\s*\(\s*(globalThis|global)\b"), "changes prototypes of globals"),
    (re.compile(r"\b(globalThis|global)\s*\["), "indexes into the global object"),
    (re.compile(r"\brequire\s*\(\s*[^'\"`\s)]"), "requires a module by a computed name"),
    (re.compile(r"\brequire\s*\.\s*(cache|resolve|main)\b|\bmodule\s*\.\s*constructor\b"), "touches the module loader"),
]
APPROVAL_PATTERNS = [
    (re.compile(r"\bfetch\s*\("), "calls fetch (network access)"),
    (re.compile(r"\b(XMLHttpRequest|WebSocket|EventSource)\b"), "uses a network API"),
]
MODULE_RE = re.compile(r"""\brequire\s*\(\s*['"`]([^'"`]+)['"`]\s*\)|\bfrom\s+['"]([^'"]+)['"]|\bimport\s+['"]([^'"]+)['"]""")


def _strip_comments(code: str) -> str:
    """Remove // and /* */ comments but keep string contents (module names live in strings)."""
    out, i, n = [], 0, len(code)
    quote = None
    while i < n:
        ch = code[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            out.append(ch)
        elif code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j == -1 else j
            continue
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _blank_strings(src: str) -> str:
    """Keep quotes, blank what is inside them: 'eval(1)' in a string is text, not a call."""
    return re.sub(r"""(['"`])(?:\\.|(?!\1).)*\1""", lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1),
                  src, flags=re.S)


def evaluate_js_policy(code: str) -> PolicyResult:
    src = _strip_comments(code or "")
    code_only = _blank_strings(src)
    deny: list[str] = []
    approve: list[str] = []
    for m in MODULE_RE.finditer(src):
        name = next(g for g in m.groups() if g)
        base = name[5:] if name.startswith("node:") else name
        if base.startswith((".", "/")):
            if base.lstrip("./").removesuffix(".js") != "solution":
                approve.append(f"loads local file {name}")
        elif base in DENY_MODULES:
            deny.append(f"requires {name}")
        elif base in APPROVAL_MODULES:
            approve.append(f"requires {name} (file, OS or network access)")
        elif base not in SAFE_MODULES:
            approve.append(f"requires unknown module {name}")
    for rx, why in DENY_PATTERNS:
        if rx.search(code_only):
            deny.append(why)
    for rx, why in APPROVAL_PATTERNS:
        if rx.search(code_only):
            approve.append(why)
    if deny:
        return PolicyResult(decision="deny", reasons=sorted(set(deny)))
    if approve:
        return PolicyResult(decision="needs_approval", reasons=sorted(set(approve)))
    return PolicyResult(decision="allow", reasons=["no file, OS, network or dynamic-code access found"])
