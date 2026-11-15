"""FastAPI app. Sign in with POST /session, then send the token as X-Session-Token on every call.
Endpoints: /health /users /session /me /submit /resume /execute /runs /feedback /gate.
Run with one worker and no --reload (paused approvals and sessions live in memory)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import BACKEND_DIR, get_config, groq_key, llm_mode
from guard.identity import Identity, UnknownUser
from guard.session import SESSIONS, NotSignedIn, acting_as
from guard.mask import mask
from llm.backends import GroqBackend
from llm.client import METER
from llm.reliability import BREAKERS
from runs import NotFound
from sandbox import isolation_layers
import service

service.setup_logging()
log = logging.getLogger("codesmith.main")
STARTUP: dict = {"models_checked": False, "missing_models": []}


@asynccontextmanager
async def lifespan(_app):
    check_models()
    yield


app = FastAPI(title="CodeSmith by 404", lifespan=lifespan)
# Only the local frontend may call the API (any localhost port, so a busy 5173 is not a blocker).
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Session-Token"])


def check_models() -> None:
    """Fail fast if a configured model id is gone (only when a key is present and mode is live/record)."""
    service.mask_all_log_handlers()   # uvicorn's own handlers exist now: make them mask PII too
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


# Request bodies carry no user id: who the caller is comes only from the session token (see session_identity).
class SessionIn(BaseModel):
    user_id: str


class SubmitIn(BaseModel):
    text: str
    controls: dict = {}


class ResumeIn(BaseModel):
    thread_id: str
    decision: str


class ExecuteIn(BaseModel):
    thread_id: str
    snippet: str = ""


class FeedbackIn(BaseModel):
    thread_id: str
    text: str
    note: str = ""


def _call(fn, *args):
    try:
        return fn(*args)
    except service.ServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def session_identity(x_session_token: str | None = Header(default=None)) -> Identity:
    """The signed-in caller, from the session token only. Never from a body, query string or message."""
    try:
        return SESSIONS.resolve(get_config(), x_session_token)
    except NotSignedIn as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_mode": llm_mode(), "key_loaded": bool(groq_key()),
            "models": get_config()["models"], "sandbox": isolation_layers(),
            "breakers": BREAKERS.snapshot(), "meter": METER.snapshot(), **STARTUP}


@app.get("/users")
def users() -> list[dict]:
    return [{"user_id": uid, "name": u.get("name", uid), "role": u["role"]}
            for uid, u in get_config()["users"].items()]


@app.post("/session")
def sign_in(body: SessionIn) -> dict:
    """Sign in as a user listed in config.yaml (a demo stand-in for real authentication)."""
    try:
        token, ident = SESSIONS.create(get_config(), body.user_id)
    except UnknownUser as exc:
        raise HTTPException(status_code=403, detail=f"Unknown user {body.user_id!r}. Only accounts listed in "
                                                    f"config.yaml can sign in.") from exc
    return {"token": token, "user": {"user_id": ident.user_id, "name": ident.name, "role": ident.role}}


@app.get("/me")
def me(ident: Identity = Depends(session_identity)) -> dict:
    return {"user_id": ident.user_id, "name": ident.name, "role": ident.role}


@app.post("/submit")
def submit(body: SubmitIn, ident: Identity = Depends(session_identity)) -> dict:
    return _call(service.submit, ident.user_id, body.text, body.controls)


@app.post("/resume")
def resume(body: ResumeIn, ident: Identity = Depends(session_identity)) -> dict:
    return _call(service.resume, body.thread_id, ident.user_id, body.decision)


@app.post("/execute")
def execute(body: ExecuteIn, ident: Identity = Depends(session_identity)) -> dict:
    """Run the generated code of a thread with the caller's snippet; returns the sandbox output."""
    return _call(service.execute, body.thread_id, ident.user_id, body.snippet)


@app.get("/runs/{run_id}")
def get_run(run_id: str, ident: Identity = Depends(session_identity)) -> dict:
    """One run, only for its owner (or an admin). Missing and not-yours give the same 404."""
    with acting_as(ident):
        try:
            return service.get_my_run(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/runs")
def list_runs(ident: Identity = Depends(session_identity)) -> list[dict]:
    """The caller's own runs, newest first (an admin sees every run). The UI history comes from here."""
    with acting_as(ident):
        return service.list_my_runs()


@app.post("/feedback")
def feedback(body: FeedbackIn, ident: Identity = Depends(session_identity)) -> dict:
    """A thumbs-down becomes a candidate golden case (masked) in evals/feedback.jsonl."""
    path = BACKEND_DIR / "evals" / "feedback.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "user_id": ident.user_id, "thread_id": body.thread_id,
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
