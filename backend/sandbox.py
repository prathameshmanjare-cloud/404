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
        if line.lstrip().startswith("at ") and ("node:" in line or "_run.js" in line
                                                 or "_net_block.js" in line):
            continue  # Node's own stack frames
        if line.startswith("Node.js v"):
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept) + ("\n" if out.endswith("\n") else "")


def _execute(files: dict[str, str], launcher: str, timeout_s: float, max_output: int,
             command=None) -> RunResult:
    """Write the files into a fresh folder and run one command there under the sandbox limits.

    command(workdir) returns the argv; the default runs the Python launcher.
    """
    EXECUTIONS.bump()
    workdir = os.path.realpath(tempfile.mkdtemp(prefix="codeforge-run-"))
    try:
        for name, content in {**files, "_launcher.py" if command is None else "_unused": launcher}.items():
            if name == "_unused":
                continue
            with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
                fh.write(content)

        env = {"PATH": "/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONHASHSEED": "0", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8", "NODE_OPTIONS": "",
               "NO_COLOR": "1", "FORCE_COLOR": "0"}
        cmd = command(workdir) if command else [sys.executable, "-I", "_launcher.py"]
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


# ------------------------------------------------------------------ JavaScript (Node.js)
NODE_NET_BLOCK = """
const block = () => { throw new Error('network access is disabled in the sandbox'); };
const net = require('net');
net.connect = net.createConnection = block;
net.Socket.prototype.connect = block;
require('tls').connect = block;
for (const m of ['http', 'https']) { const h = require(m); h.request = h.get = block; }
require('http2').connect = block;
require('dgram').createSocket = block;
const dns = require('dns');
for (const k of ['lookup', 'resolve', 'resolve4', 'resolve6', 'resolveAny']) dns[k] = block;
dns.promises.lookup = dns.promises.resolve = block;
globalThis.fetch = block;
globalThis.WebSocket = undefined;
"""

# Runs main.js (the user's snippet) with the solution's exports in scope; prints the last value.
NODE_RUNNER = """
const util = require('node:util');
const vm = require('node:vm');
const fs = require('node:fs');
Object.assign(globalThis, require('./solution.js'));
const src = fs.readFileSync('./main.js', 'utf8');
(async () => {
  try {
    let value = vm.runInThisContext(src, { filename: 'main.js', displayErrors: false });
    if (value && typeof value.then === 'function') value = await value;
    if (value !== undefined) console.log(util.inspect(value, { depth: 4 }));
  } catch (err) {
    if (err && err.code === 'ERR_ACCESS_DENIED') {
      console.error('Sandbox: ' + err.message.replace('Use --allow-fs-read to manage permissions.', '')
        .replace('Use --allow-fs-write to manage permissions.', '').trim() + ' (only the run folder is accessible)');
    }
    console.error(err && err.stack ? err.stack : String(err));
    process.exitCode = 1;
  }
})();
"""

NODE_BIN = shutil.which("node")


@lru_cache(maxsize=1)
def node_permission_flag() -> str | None:
    """Node 22.13+/23.5+ use --permission; Node 20-22 early used --experimental-permission."""
    if not NODE_BIN:
        return None
    for flag in ("--permission", "--experimental-permission"):
        try:
            r = subprocess.run([NODE_BIN, flag, "-e", "0"], capture_output=True, timeout=10)
            if r.returncode == 0:
                return flag
        except Exception:
            pass
    return None


def _node_command(entry: list[str]):
    def build(workdir: str) -> list[str]:
        cmd = [NODE_BIN]
        flag = node_permission_flag()
        if flag:
            # No child processes or workers; files only inside the run folder.
            cmd += [flag, f"--allow-fs-read={workdir}", f"--allow-fs-write={workdir}"]
        return cmd + ["--require", "./_net_block.js"] + entry
    return build


def _node_missing() -> RunResult:
    return RunResult(exit_code=None, timed_out=False, sandbox="none",
                     output="Node.js is not installed on the server, so JavaScript cannot be run.")


def js_syntax_error(src: str) -> str | None:
    """`node --check` on the source; returns the first error line, or None when it parses."""
    if not NODE_BIN:
        return None
    workdir = tempfile.mkdtemp(prefix="codeforge-check-")
    try:
        path = os.path.join(workdir, "check.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        r = subprocess.run([NODE_BIN, "--check", path], capture_output=True, text=True, timeout=10,
                           env={"PATH": "/usr/bin:/bin"})
        if r.returncode == 0:
            return None
        lines = [ln for ln in r.stderr.splitlines() if "Error" in ln]
        return lines[0] if lines else "invalid JavaScript"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_js_tests(code: str, tests: str, timeout_s: float = 5, max_output: int = 4000) -> RunResult:
    """Runs the tests against the code with Node's built-in test runner."""
    if not NODE_BIN:
        return _node_missing()
    files = {"solution.js": code, "solution.test.js": tests, "_net_block.js": NODE_NET_BLOCK}
    result = _execute(files, "", timeout_s, max_output,
                      command=_node_command(["--test-reporter=spec", "solution.test.js"]))
    result.sandbox = result.sandbox.replace("socket-blocking launcher", "network-blocking preload") + \
        (", node permission model" if node_permission_flag() else "")
    return result


def run_js_snippet(code: str, snippet: str, timeout_s: float = 5, max_output: int = 4000) -> RunResult:
    """Runs the user's snippet with the module's exports in scope. With no snippet, runs the module."""
    if not NODE_BIN:
        return _node_missing()
    files = {"solution.js": code, "main.js": snippet, "_run.js": NODE_RUNNER, "_net_block.js": NODE_NET_BLOCK}
    entry = ["_run.js"] if snippet.strip() else ["solution.js"]
    result = _execute(files, "", timeout_s, max_output, command=_node_command(entry))
    result.sandbox = result.sandbox.replace("socket-blocking launcher", "network-blocking preload") + \
        (", node permission model" if node_permission_flag() else "")
    return result
