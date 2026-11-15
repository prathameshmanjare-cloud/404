"""FastAPI app: /health /users /submit /resume /execute /gate /runs /feedback. Run with one worker, no --reload."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import BACKEND_DIR, get_config, groq_key, llm_mode
from guard.identity import UnknownUser, get_identity
from guard.mask import mask
from llm.backends import GroqBackend
from llm.client import METER
from llm.reliability import BREAKERS
from runs import RUNS, NotFound
from sandbox import isolation_layers
import service

service.setup_logging()
log = logging.getLogger("codeforge.main")
app = FastAPI(title="CodeForge")
# Only the local frontend may call the API (any localhost port, so a busy 5173 is not a blocker).
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

STARTUP: dict = {"models_checked": False, "missing_models": []}


@app.on_event("startup")
def check_models() -> None:
    """Fail fast if a configured model id is gone (only when a key is present and mode is live/record)."""
    if llm_mode() == "replay" or not groq_key():
        log.warning("model check skipped (mode=%s, key=%s)", llm_mode(), "set" if groq_key() else "missing")
        return
    try:
        available = set(GroqBackend().list_models())
    except Exception as exc:  # network trouble should not stop the demo; replay still works
        log.warning("could not list models: %s", exc)
        return
    wanted = set(get_config()["models"].values())
    missing = sorted(wanted - available)
    STARTUP.update(models_checked=True, missing_models=missing)
    if missing:
        raise RuntimeError(f"configured models not available on this key: {missing}; edit config.yaml")


class SubmitIn(BaseModel):
    user_id: str
    text: str
    controls: dict = {}


class ResumeIn(BaseModel):
    thread_id: str
    user_id: str
    decision: str


class ExecuteIn(BaseModel):
    thread_id: str
    user_id: str
    snippet: str = ""


class FeedbackIn(BaseModel):
    user_id: str
    thread_id: str
    text: str
    note: str = ""


def _call(fn, *args):
    try:
        return fn(*args)
    except service.ServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_mode": llm_mode(), "key_loaded": bool(groq_key()),
            "models": get_config()["models"], "sandbox": isolation_layers(),
            "breakers": BREAKERS.snapshot(), "meter": METER.snapshot(), **STARTUP}


@app.get("/users")
def users() -> list[dict]:
    return [{"user_id": uid, "name": u.get("name", uid), "role": u["role"]}
            for uid, u in get_config()["users"].items()]


@app.post("/submit")
def submit(body: SubmitIn) -> dict:
    return _call(service.submit, body.user_id, body.text, body.controls)


@app.post("/resume")
def resume(body: ResumeIn) -> dict:
    return _call(service.resume, body.thread_id, body.user_id, body.decision)


@app.post("/execute")
def execute(body: ExecuteIn) -> dict:
    """Run the generated code of a thread with the caller's snippet; returns the sandbox output."""
    return _call(service.execute, body.thread_id, body.user_id, body.snippet)


@app.get("/runs/{run_id}")
def get_run(run_id: str, user_id: str) -> dict:
    try:
        caller = get_identity(get_config(), user_id)
        run = RUNS.get_run(caller, run_id)
    except UnknownUser as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run.run_id, "owner": run.owner, "request": run.request, "status": run.status,
            "result": run.result}


@app.get("/runs")
def list_runs(user_id: str) -> list[dict]:
    try:
        caller = get_identity(get_config(), user_id)
    except UnknownUser as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [{"run_id": r.run_id, "owner": r.owner, "status": r.status, "request": r.request[:120]}
            for r in RUNS.list_runs(caller)]


@app.post("/feedback")
def feedback(body: FeedbackIn) -> dict:
    """A thumbs-down becomes a candidate golden case (masked) in evals/feedback.jsonl."""
    path = BACKEND_DIR / "evals" / "feedback.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "user_id": body.user_id, "thread_id": body.thread_id,
                             "text": mask(body.text), "note": mask(body.note)}) + "\n")
    return {"saved": True}


@app.post("/gate")
def gate() -> dict:
    """Runs the golden set in replay mode in a separate process and returns its JSON summary."""
    env = {**os.environ, "LLM_MODE": "replay"}
    proc = subprocess.run([sys.executable, "-m", "evals.run_gate", "--json"], cwd=BACKEND_DIR, env=env,
                          capture_output=True, text=True, timeout=600)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail=mask(proc.stderr[-1500:] or "gate produced no output"))
