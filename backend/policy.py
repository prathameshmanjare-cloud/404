"""evaluate_code_policy (R2): decides allow / needs_approval / deny for Python code. Pure function.

It parses the code with `ast` and NEVER runs it. Deny always wins over approval.

DENY (refused automatically, nothing runs):
  * operating-system access   import os / sys / subprocess / ctypes / ... (the PDF's "import os" rule)
  * system commands           os.system, popen, exec*, spawn*, fork, kill, subprocess
  * deleting files            os.remove / unlink / rmdir, shutil.rmtree / move, Path.unlink / rmdir,
                              truncate, chmod / chown (called, or merely referenced: rm = os.remove)
  * dangerous shell text      "sudo", "rm -rf", "mkfs", "shutdown", "chmod 777", ... anywhere in a string
  * dynamic code              eval, exec, compile, __import__, getattr, importlib, pickle, marshal, ...
  * sandbox escapes           __subclasses__, __globals__, __builtins__, frame internals, ...
  * infinite loops            `while True:` / endless iterators with no break, return or raise
NEEDS APPROVAL (a person decides before anything runs):
  * file access               open(), pathlib, io, tempfile, glob, shutil copy, zipfile, sqlite3, ...
  * network access            socket, requests, urllib, http, smtplib, ssl, asyncio, ...,
                              and any http:// or https:// address in the code
  * console input             input()
  * unknown modules           anything not on the safe list (fails closed)

This is a policy layer, not a security boundary; the sandbox is the second layer.
"""
from __future__ import annotations

import ast
import re

from state import PolicyResult

# ---------------------------------------------------------------- DENY
# Modules whose import alone is refused.
DENY_MODULES = {
    "os": "operating-system access", "sys": "interpreter internals", "posix": "operating-system access",
    "nt": "operating-system access", "subprocess": "runs other programs", "pty": "runs other programs",
    "ctypes": "raw memory access", "multiprocessing": "starts processes", "signal": "process signals",
    "resource": "process limits", "importlib": "loads code dynamically", "builtins": "interpreter internals",
    "marshal": "loads code dynamically", "pickle": "can execute code on load", "shelve": "can execute code on load",
    "code": "runs code dynamically", "codeop": "runs code dynamically", "inspect": "interpreter internals",
    "gc": "interpreter internals",
}
# Calls that run system commands (matched on the last part of the name: os.system, from os import system, ...).
SYSTEM_CALLS = {"system", "popen", "execv", "execve", "execl", "execle", "execlp", "execvp", "execvpe",
                "spawnl", "spawnv", "spawnve", "posix_spawn", "fork", "forkpty", "kill", "killpg"}
# Calls that delete or damage files (matched on the last part of the name).
DELETE_CALLS = {"unlink", "rmdir", "removedirs", "rmtree", "truncate", "chmod", "chown", "lchown"}
# Ambiguous names refused only when they come from os/shutil (list.remove and str.replace stay fine).
DELETE_QUALIFIED = {"os.remove", "os.rename", "os.replace", "shutil.move"}
# Builtins that run or reach arbitrary code; refused when used as a bare name, called or not.
DYNAMIC_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint", "getattr", "setattr", "delattr",
                    "globals", "locals", "vars"}
# Attributes used to escape a sandbox from inside Python.
ESCAPE_ATTRS = {"__subclasses__", "__globals__", "__builtins__", "__code__", "__bases__", "__mro__", "__dict__",
                "__getattribute__", "__reduce__", "__reduce_ex__", "f_globals", "f_locals", "f_back", "gi_frame"}
# Shell commands that are refused even inside a plain string ("sudo rm -rf /" in a variable is still refused).
DANGEROUS_SHELL = re.compile(
    r"\bsudo\b|\brm\s+-[a-z]*[rf][a-z]*\b|\bmkfs(\.\w+)?\b|"
    # shutdown / reboot / halt / poweroff only when written as a command, not as an English word in a docstring
    r"(?:^\s*|[;&|`$(]\s*)(?:shutdown|reboot|halt|poweroff)(?=\s*(?:$|[;&|)`]|-\w|now\b))|"
    r"\bchmod\s+(-R\s+)?[0-7]*777\b|\bchown\s+-R\b|\bdd\s+if=|>\s*/dev/sd[a-z]|:\(\)\s*\{\s*:\|:&\s*\};:|"
    r"\bformat\s+[a-z]:|\bdel\s+/[fsq]\b|\brmdir\s+/s\b|\bkill\s+-9\b|\bcurl\b[^\n]*\|\s*(ba)?sh\b",
    re.I)

# ---------------------------------------------------------------- NEEDS APPROVAL
FILE_MODULES = {"pathlib", "io", "tempfile", "glob", "fnmatch", "shutil", "fileinput", "zipfile", "tarfile",
                "gzip", "bz2", "lzma", "sqlite3", "mmap", "filecmp"}
# Any http:// or https:// address in the code means it may reach the network, whatever library it uses.
URL_IN_TEXT = re.compile(r"\bhttps?://[^\s'\"]+", re.I)
NETWORK_MODULES = {"socket", "ssl", "requests", "urllib", "urllib3", "http", "httpx", "ftplib", "smtplib",
                   "poplib", "imaplib", "telnetlib", "asyncio", "aiohttp", "websocket", "websockets", "xmlrpc"}
FILE_CALLS = {"open", "Path", "read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "iterdir",
              "glob", "rglob", "touch", "copy", "copyfile", "copytree"}
# ---------------------------------------------------------------- ALLOW
# Imports known to be harmless; anything not here and not above fails closed to approval.
SAFE_MODULES = {"math", "cmath", "string", "re", "collections", "itertools", "functools", "operator",
                "typing", "dataclasses", "decimal", "fractions", "statistics", "random", "heapq",
                "bisect", "enum", "abc", "datetime", "json", "copy", "textwrap", "unicodedata",
                "__future__", "numbers", "array", "calendar", "pytest", "solution", "time",
                "hashlib", "base64", "uuid", "secrets", "zoneinfo", "contextlib", "pprint", "unittest",
                "graphlib", "difflib", "binascii", "struct", "csv", "html", "threading", "queue",
                "weakref", "types", "string"}


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    """Dotted name of an expression with import aliases resolved ("from os import remove as rm" -> os.remove)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
    elif isinstance(node, ast.Call):
        inner = _dotted(node.func, aliases)
        if inner:
            parts.append(inner)
    return ".".join(reversed(parts))


def _always_true(test: ast.AST) -> bool:
    """while True / while 1 / while "x" / while not False."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Constant):
        return not test.operand.value
    return False


def _endless_iterator(it: ast.AST) -> bool:
    """for x in itertools.count() / itertools.cycle(...) / iter(int, 1) / repeat(x) with no count."""
    if not isinstance(it, ast.Call):
        return False
    name = _dotted(it.func, {})
    short = name.split(".")[-1]
    if short in {"count", "cycle"}:
        return True
    if short == "repeat" and len(it.args) < 2 and not it.keywords:
        return True
    return short == "iter" and len(it.args) == 2
    

def _can_leave(body: list[ast.stmt]) -> bool:
    """True if the loop body has a break (for THIS loop), return, raise or exit call."""
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Return, ast.Raise)):
            return True
        if isinstance(node, ast.Break):
            return True
        if isinstance(node, ast.Call) and _dotted(node.func, {}).split(".")[-1] in {"exit", "_exit", "quit"}:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue                                   # a return in a nested function does not leave the loop
        if isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
            # a break inside a nested loop only leaves that loop; returns/raises still count
            stack.extend(n for n in ast.walk(node) if isinstance(n, (ast.Return, ast.Raise)))
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def find_infinite_loops(code: str) -> list[str]:
    """Loops that can never end: `while True:` (or an endless iterator) with no break, return or raise."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and _always_true(node.test) and not _can_leave(node.body):
            found.append(f"Infinite loop: the `while {ast.unparse(node.test)}` loop on line {node.lineno} never "
                         f"breaks, returns or raises, so it would never stop")
        elif isinstance(node, (ast.For, ast.AsyncFor)) and _endless_iterator(node.iter) and not _can_leave(node.body):
            found.append(f"Infinite loop: the loop over `{ast.unparse(node.iter)}` on line {node.lineno} never "
                         f"breaks, returns or raises, so it would never stop")
    return found


def evaluate_code_policy(code: str) -> PolicyResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return PolicyResult(decision="deny", reasons=[f"code does not parse: {exc.msg} (line {exc.lineno})"])

    deny: list[str] = []
    approve: list[str] = []
    aliases: dict[str, str] = {}
    # Docstrings are documentation, not data the code uses: a link in one is not network access.
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
                  and isinstance(n.body[0].value.value, str)}

    def check_import(module: str) -> None:
        root = module.split(".")[0]
        if root in DENY_MODULES:
            deny.append(f"Operating-system access: imports {module} ({DENY_MODULES[root]})")
        elif root in FILE_MODULES:
            approve.append(f"File access: imports {module}")
        elif root in NETWORK_MODULES:
            approve.append(f"Network access: imports {module}")
        elif root not in SAFE_MODULES:
            approve.append(f"Unknown module: imports {module} (not on the safe list)")

    def check_name(name: str, called: bool) -> None:
        """A function used by name: os.system(...), or passed around: rm = os.remove."""
        short = name.split(".")[-1]
        tail2 = ".".join(name.split(".")[-2:])
        how = "calls" if called else "uses"
        if short in SYSTEM_CALLS and "." in name or (called and short in SYSTEM_CALLS):
            deny.append(f"Runs system commands: {how} {name}")
        elif short in DELETE_CALLS or tail2 in DELETE_QUALIFIED:
            deny.append(f"Deletes or damages files: {how} {name}")
        elif called and short in FILE_CALLS:
            approve.append(f"File access: calls {name}")
        elif called and short == "input":
            approve.append("Console input: calls input()")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                check_import(a.name)
                aliases[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            check_import(module)
            for a in node.names:
                aliases[a.asname or a.name] = f"{module}.{a.name}"
        elif isinstance(node, ast.Call):
            name = _dotted(node.func, aliases)
            if name in DYNAMIC_BUILTINS:
                deny.append(f"Dynamic code: calls {name}()")
            else:
                check_name(name, called=True)
        elif isinstance(node, ast.Attribute):
            if node.attr in ESCAPE_ATTRS:
                deny.append(f"Sandbox escape: uses {node.attr}")
            elif isinstance(node.ctx, ast.Load):
                check_name(_dotted(node, aliases), called=False)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in DYNAMIC_BUILTINS:
                deny.append(f"Dynamic code: uses {node.id}()")
            elif node.id in {"__builtins__", "__loader__", "__spec__"}:
                deny.append(f"Sandbox escape: uses {node.id}")
            elif node.id in aliases:                      # "from os import remove as rm; f = rm"
                check_name(aliases[node.id], called=False)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = DANGEROUS_SHELL.search(node.value)
            if m:
                deny.append(f"Dangerous shell command: the code contains \"{m.group(0).strip(' ;&|`$(')}\"")
            url = URL_IN_TEXT.search(node.value) if id(node) not in docstrings else None
            if url:
                approve.append(f"Network access: contains the web address {url.group(0)[:60]}")

    deny += find_infinite_loops(code)
    # A call is also visited as an attribute/name; keep one reason per thing, preferring "calls".
    deny = _dedupe(deny)
    approve = _dedupe(approve)
    if deny:
        return PolicyResult(decision="deny", reasons=deny)
    if approve:
        return PolicyResult(decision="needs_approval", reasons=approve)
    return PolicyResult(decision="allow", reasons=["no file, OS, network or dynamic-code access found"])


def _dedupe(reasons: list[str]) -> list[str]:
    calls = {r.replace(": calls ", ": uses ") for r in reasons if ": calls " in r}
    out = []
    for r in reasons:
        if r in calls or r in out:          # an "uses X" that also appears as "calls X"
            continue
        out.append(r)
    return sorted(out)


# =====================================================================================================
# check_request_intent: the same rules applied to the REQUEST, before any model writes code.
# "Delete every file in my home folder" is refused here in code, whatever the model would have written.
# =====================================================================================================
_DELETE_VERB = re.compile(r"\b(delet\w*|remov\w*|eras\w*|wip\w*|destroy\w*|shred\w*|clear\w* out|empty\w*|"
                          r"purg\w*|nuk\w*|rm)\b", re.I)
_FILE_TARGET = re.compile(r"\b(files?|folders?|director(y|ies)|home|disk|drive|partition|file ?system|"
                          r"everything on|root)\b", re.I)
# Words that show the thing being removed is data, not files ("remove empty strings from a list of file names").
_DATA_WORDS = re.compile(r"\b(strings?|elements?|items?|duplicates?|vowels?|characters?|chars?|nodes?|keys?|"
                         r"entries|entry|words?|spaces?|whitespace|lines?|rows?|values?|numbers?|digits?|lists?|"
                         r"arrays?|names?|extensions?|punctuation|zeros?|nulls?|nones?)\b", re.I)
_DESTRUCTIVE_REQUEST = [
    (re.compile(r"\b(format\w*|wip\w*|eras\w*)\b[^.?!]{0,30}\b(disk|drive|partition|hard ?drive|ssd|c:)", re.I),
     "Deletes or damages files: the request asks to format or wipe a disk"),
    (re.compile(r"\b(shut\w*\s*down|reboot\w*|restart\w*|power\w* off|turn\w* off)\b[^.?!]{0,30}\b(computer|"
                r"machine|server|system|pc|laptop|host)\b", re.I),
     "Runs system commands: the request asks to shut down or restart a machine"),
    (re.compile(r"\bkill\w*\b[^.?!]{0,20}\b(all|every)\b[^.?!]{0,20}\bprocess", re.I),
     "Runs system commands: the request asks to kill processes"),
    (re.compile(r"\b(run\w*|execut\w*)\b[^.?!]{0,30}\b(shell|terminal|bash|cmd|system|os) commands?", re.I),
     "Runs system commands: the request asks to run shell commands"),
    (re.compile(r"\b(pip3?|npm|yarn|apt(-get)?|brew|yum|dnf|choco|winget)\s+install\b|\binstall\w*\b[^.?!]{0,25}"
                r"\b(packages?|software|programs?|librar(y|ies)|apps?|drivers?)\b", re.I),
     "Runs system commands: the request asks to install software"),
    (re.compile(r"\b(list|show|get|read|print|dump|see)\w*\b[^.?!]{0,25}\b(running\s+)?(processes|services)\b", re.I),
     "Operating-system access: the request asks to inspect the machine's processes"),
    (re.compile(r"/etc/(passwd|shadow|hosts)\b|\b(environment\s+variables?|env\s+vars?|ssh\s+keys?|\.ssh\b|"
                r"private\s+keys?|saved\s+passwords?|browser\s+(cookies|passwords|history)|keychain|registry\s+keys?)", re.I),
     "Operating-system access: the request asks to read system secrets or settings"),
    # Only when code is being asked for: "explain what an infinite loop is" is a fine question.
    (re.compile(r"^(?=.*\b(write|create|make|build|generate|code|implement|give\s+me|program|script|function|"
                r"snippet)\b).*(\b(infinite|endless|never[- ]?ending|non[- ]?stop)\s+(loop|recursion|process)\b|"
                r"\bloops?\s+forever\b|\bruns?\s+forever\b|\bnever\s+(stops|ends|terminates)\b|"
                r"\bwhile\s*\(?\s*true\b|\bfork\s*bomb\b)", re.I | re.S),
     "Infinite loop: the request asks for code that never stops"),
    (re.compile(r"\b(key\s*logger|ransomware|virus|malware|trojan|worm|ddos|botnet|crypto\s*miner|spyware|"
                r"steal\w*|exfiltrat\w*|brute[- ]?forc\w*)\b", re.I),
     "Harmful software: the request asks for malicious code"),
]

# What to ask instead, per category: shown with every denial so the user knows the safe alternative.
ADVICE = {
    "Deletes or damages files": "Ask for a function that lists what it would delete (a dry run), or one that works "
                                "on a list of file names you pass in, without touching your disk.",
    "Runs system commands": "Ask for a function that builds the command as text for you to run yourself, or does "
                            "the work in plain Python or JavaScript.",
    "Dangerous shell command": "Ask for a function that builds the command as text for you to run yourself; "
                               "CodeSmith never runs shell commands.",
    "Operating-system access": "Ask for a function that takes the values it needs as arguments instead of reading "
                               "them from the computer.",
    "Infinite loop": "Ask for a loop with a clear stop condition, such as a maximum number of steps.",
    "Dynamic code": "Ask for the logic written out directly, without eval or exec.",
    "Sandbox escape": "Ask for the logic written out directly, without reaching into interpreter internals.",
    "Harmful software": "CodeSmith can't help with this.",
}


def advice_for(reasons: list[str]) -> list[str]:
    """One suggestion per category found in the reasons ("Deletes or damages files: ..." -> its advice)."""
    out = []
    for r in reasons:
        tip = ADVICE.get(r.split(":", 1)[0].split(" (")[0])
        if tip and tip not in out:
            out.append(tip)
    return out


def _asks_to_delete_files(text: str) -> bool:
    for verb in _DELETE_VERB.finditer(text):
        window = text[verb.end():verb.end() + 60]
        target = _FILE_TARGET.search(window)
        if not target or _DATA_WORDS.search(window[:target.start()]):
            continue
        following = re.match(r"\s*(\w+)", window[target.end():])          # "file extensions", "file names"
        if following and (_DATA_WORDS.fullmatch(following.group(1)) or following.group(1).lower() in {"path", "paths"}):
            continue
        return True
    return False


def check_request_intent(text: str) -> PolicyResult:
    """Deny a request whose stated goal is destructive, before any code is generated. Pure function."""
    reasons = [why for rx, why in _DESTRUCTIVE_REQUEST if rx.search(text or "")]
    if _asks_to_delete_files(text or ""):
        reasons.append("Deletes or damages files: the request asks to delete files or folders")
    shell = DANGEROUS_SHELL.search(text or "")
    if shell:
        reasons.append(f"Dangerous shell command: the request contains \"{shell.group(0).strip(' ;&|`$(')}\"")
    if reasons:
        return PolicyResult(decision="deny", reasons=sorted(set(reasons)))
    return PolicyResult(decision="allow", reasons=["the request does not ask for anything destructive"])
