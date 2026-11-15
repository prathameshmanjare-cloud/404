"""Typed graph state and the structured objects that move between nodes."""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Route = Literal["python_function", "javascript_function", "sql", "general", "other"]
Language = Literal["python", "javascript"]
Tier = Literal["primary", "fallback", "template", "none"]
Decision = Literal["allow", "needs_approval", "deny"]


class Controls(BaseModel):
    chaos: bool = False
    outage: bool = False
    replay: bool = False


class Triage(BaseModel):
    task: str = "unknown"
    language: str = "python"
    sentiment: Literal["angry", "unhappy", "neutral", "positive"] = "neutral"
    sentiment_score: float = 0.0  # -1 (furious) .. 1 (happy)
    urgency: Literal["low", "normal", "high"] = "normal"
    wants_human: bool = False


class Param(BaseModel):
    name: str
    type: str = "Any"


class Requirements(BaseModel):
    function_name: str
    parameters: list[Param] = Field(default_factory=list)
    returns: str = "Any"
    description: str = ""
    edge_cases: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)


class PolicyResult(BaseModel):
    decision: Decision
    reasons: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    exit_code: int | None
    timed_out: bool = False
    output: str = ""
    sandbox: str = ""  # which isolation layers were used


class Ticket(BaseModel):
    ticket_id: str
    user_id: str
    priority: Literal["P1", "P2", "P3"]
    sla_hours: int
    reason: str
    handoff_note: str
    request: str  # masked


class CodeSmithState(TypedDict, total=False):
    thread_id: str
    user_id: str                       # from config, never from the message
    role: Literal["user", "admin"]
    request: str                       # already masked
    controls: dict
    route: Route | None
    language: Language                 # of the generated code
    pii: list[dict]                    # findings from guard.mask.find_pii (types and checks, never values)
    answer: str | None                 # text answer for general requests
    triage: dict | None                # Triage
    requirements: dict | None          # Requirements
    code: str | None
    tests: str | None
    sql: str | None
    policy: dict | None                # PolicyResult
    approval: Literal["pending", "approved", "rejected"] | None
    approver: str | None
    idem_key: str | None
    run: dict | None                   # RunResult
    attempts: int                      # repair attempts
    tier: Tier
    ticket: dict | None                # Ticket
    status: str
    report: str | None
    notes: list[str]
