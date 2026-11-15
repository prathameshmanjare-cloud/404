"""run_in_sandbox (R2): one separate process per run.

Layers:
  * a fresh temp directory as the working directory, deleted afterwards
  * a stripped environment (no GROQ_API_KEY or any other secret reaches generated code)
  * a wall-clock timeout that kills the whole process group
  * CPU, file-size and process limits (Unix only)
  * no network: `unshare -rn` on Linux where it works, plus a launcher that blocks sockets
"""
from __future__ import annotations

import ast
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from functools import lru_cache

from state import RunResult

_NET_BLOCK = '''
import socket, sys
def _blocked(*a, **k):
    raise OSError("network access is disabled in the sandbox")
class _NoSocket(socket.socket):
    def __init__(self, *a, **k):
        _blocked()
socket.socket = _NoSocket
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
sys.path.insert(0, ".")
'''

LAUNCHER = _NET_BLOCK + '''
import pytest
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "--no-header", "-o", "console_output_style=classic", "."]))
'''

# Runs main.py (the user's snippet, which imports the solution) as a script.
RUN_LAUNCHER = _NET_BLOCK + '''
import runpy
runpy.run_path("main.py", run_name="__main__")
'''


class SandboxCounter:
    """Counts real sandbox executions (the idempotency check reads it)."""

    def __init__(self) -> None:
        self.count = 0
        self._lock = threading.Lock()

    def bump(self) -> None:
        with self._lock:
            self.count += 1


EXECUTIONS = SandboxCounter()


@lru_cache(maxsize=1)
def unshare_available() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        return False
    try:
        r = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def isolation_layers() -> str:
    layers = ["temp dir", "stripped env", "timeout", "socket-blocking launcher"]
    if os.name == "posix":
        layers.append("rlimits")
    if unshare_available():
        layers.append("unshare -n")
    return ", ".join(layers)


def _limits() -> None:  # runs in the child before exec (posix only)
    import resource
    os.setsid()
    for res, val in ((resource.RLIMIT_CPU, 10), (resource.RLIMIT_FSIZE, 10 * 1024 * 1024)):
        try:
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass


def clean_output(output: str, workdir: str) -> str:
    """Make tracebacks readable: drop sandbox temp paths and the launcher's own frames."""
    out = output
    for root in sorted({os.path.realpath(workdir), workdir}, key=len, reverse=True):
        out = out.replace(root + "/./", "").replace(root + "/", "").replace(root, ".")
    kept: list[str] = []
    skipping = False
    for line in out.splitlines():
        if line.startswith('  File "'):
            skipping = "runpy" in line or "_launcher.py" in line or "<frozen runpy>" in line
        elif not line.startswith("    "):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept) + ("\n" if out.endswith("\n") else "")


def _execute(files: dict[str, str], launcher: str, timeout_s: float, max_output: int) -> RunResult:
    EXECUTIONS.bump()
    workdir = tempfile.mkdtemp(prefix="codeforge-run-")
    try:
        for name, content in {**files, "_launcher.py": launcher}.items():
            with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
                fh.write(content)

        env = {"PATH": "/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONHASHSEED": "0", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
        cmd = [sys.executable, "-I", "_launcher.py"]
        if unshare_available():
            cmd = ["unshare", "-rn"] + cmd
        posix = os.name == "posix"
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, preexec_fn=_limits if posix else None, text=True)
        try:
            out, _ = proc.communicate(timeout=timeout_s)
            out = clean_output(out, workdir)
            return RunResult(exit_code=proc.returncode, timed_out=False, output=out[-max_output:],
                             sandbox=isolation_layers())
        except subprocess.TimeoutExpired:
            if posix:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:  # pragma: no cover
                proc.kill()
            out, _ = proc.communicate()
            out = clean_output(out or "", workdir)
            return RunResult(exit_code=None, timed_out=True,
                             output=(out or "")[-max_output:] + f"\n[killed after {timeout_s}s timeout]",
                             sandbox=isolation_layers())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_in_sandbox(code: str, tests: str, timeout_s: float = 5, max_output: int = 4000) -> RunResult:
    """Runs the tests against the code with pytest."""
    return _execute({"solution.py": code, "test_solution.py": tests}, LAUNCHER, timeout_s, max_output)


def build_script(snippet: str) -> str:
    """The user's snippet with the solution imported; a final bare expression is printed, like a REPL."""
    tree = ast.parse(snippet or "")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        is_print = isinstance(last.value, ast.Call) and getattr(last.value.func, "id", "") == "print"
        if not is_print:
            # Like a Python shell: show the last value unless it is None.
            tree.body[-1:] = ast.parse("__value = None\nif __value is not None:\n    print(repr(__value))").body
            tree.body[-2].value = last.value
    ast.fix_missing_locations(tree)
    return "from solution import *\n\n" + ast.unparse(tree) + "\n"


def run_snippet(code: str, snippet: str, timeout_s: float = 5, max_output: int = 4000) -> RunResult:
    """Runs the user's snippet against the generated code. With no snippet, runs the code as a script."""
    script = build_script(snippet) if snippet.strip() else code
    return _execute({"solution.py": code, "main.py": script}, RUN_LAUNCHER, timeout_s, max_output)
