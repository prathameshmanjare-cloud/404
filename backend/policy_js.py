"""evaluate_js_policy (R2 for JavaScript): a static scan, never executes. Deny wins over approval.

Same categories and wording as the Python policy (policy.py):
DENY:           operating-system access (child_process, os, vm, worker_threads, ...), deleting files
                (fs.unlink / rm / rmdir, sync and async), dangerous shell text ("sudo", "rm -rf", ...),
                dynamic code (eval, new Function, dynamic import), sandbox escapes (process.env, constructor
                chains, computed require, ...)
NEEDS APPROVAL: file access (fs, readline), network access (http, https, net, fetch, WebSocket, and any
                http:// or https:// address), unknown modules
Like the Python policy this is a policy layer, not a security boundary. The JavaScript sandbox adds
Node's permission model (no child processes, files only inside the run folder) and blocks the network.
"""
from __future__ import annotations

import re

from policy import DANGEROUS_SHELL, URL_IN_TEXT
from state import PolicyResult

SAFE_MODULES = {"assert", "assert/strict", "test", "util", "events", "string_decoder", "url", "querystring",
                "buffer", "path", "crypto", "timers", "timers/promises", "stream", "zlib", "perf_hooks"}
FILE_MODULES = {"fs", "fs/promises", "readline", "readline/promises"}
NETWORK_MODULES = {"net", "http", "https", "http2", "dgram", "dns", "dns/promises", "tls"}
DENY_MODULES = {"child_process": "runs other programs", "os": "operating-system access", "vm": "runs code dynamically",
                "worker_threads": "starts threads with their own code", "cluster": "starts processes",
                "v8": "engine internals", "inspector": "engine internals", "module": "the module loader",
                "process": "process control", "repl": "runs code dynamically", "wasi": "system interface",
                "trace_events": "engine internals"}

DENY_PATTERNS = [
    (re.compile(r"\b(?:unlink|unlinkSync|rm|rmSync|rmdir|rmdirSync|truncate|truncateSync|chmod|chmodSync|"
                r"chown|chownSync)\s*\("), "Deletes or damages files: calls an fs delete or permission function"),
    (re.compile(r"\beval\s*\("), "Dynamic code: calls eval()"),
    (re.compile(r"\bnew\s+Function\b|(?<![\w.])Function\s*\("), "Dynamic code: builds code with Function"),
    (re.compile(r"(?<![\w.])import\s*\("), "Dynamic code: uses dynamic import()"),
    (re.compile(r"\bprocess\s*\.\s*(binding|_linkedBinding|dlopen|env|exit|kill|chdir|setuid|setgid|mainModule|"
                r"reallyExit|abort)\b"), "Sandbox escape: uses a dangerous process API"),
    (re.compile(r"\bprocess\s*\["), "Sandbox escape: indexes into process"),
    (re.compile(r"\bconstructor\s*\.\s*constructor\b|\[\s*['\"]constructor['\"]\s*\]"),
     "Sandbox escape: reaches the Function constructor"),
    (re.compile(r"__proto__|Object\s*\.\s*setPrototypeOf\s*\(\s*(globalThis|global)\b"),
     "Sandbox escape: changes prototypes of globals"),
    (re.compile(r"\b(globalThis|global)\s*\["), "Sandbox escape: indexes into the global object"),
    (re.compile(r"\brequire\s*\(\s*[^'\"`\s)]"), "Sandbox escape: requires a module by a computed name"),
    (re.compile(r"\brequire\s*\.\s*(cache|resolve|main)\b|\bmodule\s*\.\s*constructor\b"),
     "Sandbox escape: touches the module loader"),
]
APPROVAL_PATTERNS = [
    (re.compile(r"\bfetch\s*\("), "Network access: calls fetch"),
    (re.compile(r"\b(XMLHttpRequest|WebSocket|EventSource)\b"), "Network access: uses a browser network API"),
]
STRING_RE = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""", re.S)
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


_ENDLESS_JS = re.compile(r"\bwhile\s*\(\s*(?:true|1|!0|!false)\s*\)|\bfor\s*\(\s*;\s*;\s*\)|\bdo\b")


def find_infinite_loops_js(code: str) -> list[str]:
    """`while (true)`, `for (;;)` and `do {} while (true)` loops whose body has no break, return or throw."""
    src = _blank_strings(_strip_comments(code or ""))
    found = []
    for m in _ENDLESS_JS.finditer(src):
        line = src.count("\n", 0, m.start()) + 1
        start = src.find("{", m.end())
        if start == -1 or src[m.end():start].strip():   # single-statement loop without braces
            body = src[m.end():src.find(";", m.end()) + 1]
        else:
            depth, i = 0, start
            for i in range(start, len(src)):
                depth += src[i] == "{"
                depth -= src[i] == "}"
                if depth == 0:
                    break
            body = src[start:i + 1]
        if m.group(0) == "do":
            after = src[start + len(body):start + len(body) + 40] if start != -1 else ""
            if not re.match(r"\s*while\s*\(\s*(?:true|1|!0|!false)\s*\)", after):
                continue
        if not re.search(r"\b(break|return|throw)\b|process\.exit", body):
            found.append(f"Infinite loop: the loop on line {line} never breaks, returns or throws, so it would "
                         f"never stop")
    return list(dict.fromkeys(found))        # a do { } while (true) is matched twice on the same line


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
                approve.append(f"File access: loads local file {name}")
        elif base in DENY_MODULES:
            deny.append(f"Operating-system access: requires {name} ({DENY_MODULES[base]})")
        elif base in FILE_MODULES:
            approve.append(f"File access: requires {name}")
        elif base in NETWORK_MODULES:
            approve.append(f"Network access: requires {name}")
        elif base not in SAFE_MODULES:
            approve.append(f"Unknown module: requires {name} (not on the safe list)")
    for rx, why in DENY_PATTERNS:
        if rx.search(code_only):
            deny.append(why)
    for rx, why in APPROVAL_PATTERNS:
        if rx.search(code_only):
            approve.append(why)
    for m in STRING_RE.finditer(src):                 # text inside strings: shell commands and web addresses
        text = m.group(2)
        shell = DANGEROUS_SHELL.search(text)
        if shell:
            deny.append(f"Dangerous shell command: the code contains \"{shell.group(0).strip(' ;&|`$(')}\"")
        url = URL_IN_TEXT.search(text)
        if url:
            approve.append(f"Network access: contains the web address {url.group(0)[:60]}")
    deny += find_infinite_loops_js(code)
    if deny:
        return PolicyResult(decision="deny", reasons=sorted(set(deny)))
    if approve:
        return PolicyResult(decision="needs_approval", reasons=sorted(set(approve)))
    return PolicyResult(decision="allow", reasons=["no file, OS, network or dynamic-code access found"])
