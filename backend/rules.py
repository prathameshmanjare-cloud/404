"""All rules in one place: every prompt passes through them before any model sees it.

Two tiers
---------
HARD rules are required, 100% of the time. If one fails, the request is refused in code and no model is
called (privacy, identity, no destructive or never-ending code, ...). The gate requires every hard test
case in evals/rules_cases.json to pass.

SOFT rules are quality guidance. They never block: each prompt gets a score (passed / applicable) and the
target is 85%. Below it, the response says what would make the request better. The gate requires at least
85% of the soft test cases to pass.

Two stages
----------
prompt  checked on the (masked) prompt, before any model call: check_prompt()
code    checked on the generated code after the policy gate: check_code(). The code policy itself lives in
        policy.py / policy_js.py; here its reasons are mapped onto the same named hard rules, so every
        response shows one list.

Each check returns a RuleResult; the whole thing is a RulesReport, attached to every reply as "rules".
The rule bodies call the existing implementations (guard.access, guard.mask, policy), so there is one
definition of each rule and one place that lists them all.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from guard.access import Denial, check_access_request
from guard.identity import Identity
from guard.mask import redact
from policy import check_request_intent
from state import PolicyResult

SOFT_TARGET = 0.85
MAX_PROMPT_CHARS = 4000

PASS, FAIL, APPROVAL, NA = "pass", "fail", "approval", "n/a"


@dataclass
class RuleResult:
    id: str
    name: str
    tier: str          # hard | soft
    stage: str         # prompt | code
    status: str        # pass | fail | approval (hard: waits for a person) | n/a (does not apply)
    detail: str
    tip: str = ""      # soft rules: what would make the request better

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PromptContext:
    """What the prompt rules may look at. The text is already masked; findings never hold values."""
    text: str
    findings: list[dict]
    identity: Identity
    config: dict
    # computed once and shared by the rules that need them
    access: Denial | None = None
    intent: PolicyResult | None = None


@dataclass
class Rule:
    id: str
    name: str
    tier: str
    stage: str
    description: str
    check: Callable[..., tuple[str, str]]   # -> (status, detail)
    tip: str = ""


# =====================================================================================================
# HARD prompt rules: any failure refuses the request before a model is called.
# =====================================================================================================
_INTENT_CATEGORIES = {
    "H_NO_FILE_DELETION": ("Deletes or damages files",),
    "H_NO_SYSTEM_COMMANDS": ("Runs system commands", "Dangerous shell command"),
    "H_NO_SECRET_ACCESS": ("Operating-system access",),
    "H_NO_INFINITE_LOOP": ("Infinite loop",),
    "H_NO_MALWARE": ("Harmful software",),
}


def _intent_rule(rule_id: str):
    def check(ctx: PromptContext):
        hits = [r for r in ctx.intent.reasons if r.startswith(_INTENT_CATEGORIES[rule_id])] \
            if ctx.intent.decision == "deny" else []
        return (FAIL, "; ".join(hits)) if hits else (PASS, "not asked for")
    return check


def _pii_masked(ctx: PromptContext):
    left = redact(ctx.text).findings          # masking again must find nothing: no raw value is left
    if left:
        return FAIL, "unmasked personal data is still in the prompt: " + ", ".join(sorted({f.type for f in left}))
    kinds = sorted({f["type"] for f in ctx.findings})
    return PASS, ("masked before any model call: " + ", ".join(kinds)) if kinds else "no personal data found"


def _identity(ctx: PromptContext):
    if ctx.access and ctx.access.kind in {"impersonation", "role"}:
        what = "claims another account" if ctx.access.kind == "impersonation" else "claims another role"
        return FAIL, f"the prompt {what} ({ctx.access.target}); identity comes only from the sign-in"
    return PASS, f"acting as {ctx.identity.name} ({ctx.identity.role}) from the sign-in"


def _cross_user(ctx: PromptContext):
    if ctx.access and ctx.access.kind in {"user_data", "group_data"}:
        return FAIL, f"the prompt asks for {ctx.access.target}'s data; everyone sees only their own"
    return PASS, "asks only about the caller's own data"


def _length(ctx: PromptContext):
    n = len(ctx.text)
    return (FAIL, f"{n} characters, over the {MAX_PROMPT_CHARS} limit") if n > MAX_PROMPT_CHARS \
        else (PASS, f"{n} characters")


# =====================================================================================================
# SOFT prompt rules: advice only. A rule that does not apply (e.g. "names a language" for a question)
# returns n/a and is left out of the score.
# =====================================================================================================
_CODE_REQUEST = re.compile(r"\b(write|create|implement|build|generate|code|program|script|function|method|class|"
                           r"query|sql|algorithm|snippet)\b", re.I)
_ACTION = re.compile(r"\b(write|create|implement|build|generate|make|explain|describe|fix|debug|convert|find|"
                     r"calculate|compute|check|sort|count|list|show|return|reverse|merge|parse|validate|compare|"
                     r"summari[sz]e|translate|refactor|optimi[sz]e|test|print|what|how|why|when|which|is|are|can|"
                     r"does|do|give|help|define|query|total)\b", re.I)
_LANGUAGE = re.compile(r"\b(python|javascript|js|node(\.js)?|typescript|sql|sqlite)\b", re.I)
_IO_WORDS = re.compile(r"\b(takes?|given|input|inputs|returns?|outputs?|prints?|parameters?|arguments?|args?|"
                       r"accepts?|list|array|string|strings|number|numbers|integer|int|float|dict|dictionary|"
                       r"text|file|table|n|nth|of\s+\w+|from\s+\w+)\b", re.I)
_VAGUE = re.compile(r"\b(something|stuff|whatever|anything|etc\.?|some\s+code|thing|things|kinda|sort\s+of)\b", re.I)
_MULTI_TASK = re.compile(r"\b(and\s+also|also\s+write|then\s+also|as\s+well\s+as\s+(?:write|create|build))\b", re.I)


def _is_code_request(text: str) -> bool:
    return bool(_CODE_REQUEST.search(text))


def _clear_action(ctx: PromptContext):
    return (PASS, "says what to do") if _ACTION.search(ctx.text) or ctx.text.strip().endswith("?") \
        else (FAIL, "no clear action or question")


def _specific(ctx: PromptContext):
    words = len(re.findall(r"\w+", ctx.text))
    return (PASS, f"{words} words") if words >= 5 else (FAIL, f"only {words} words")


def _concise(ctx: PromptContext):
    n = len(ctx.text)
    return (PASS, f"{n} characters") if n <= 1200 else (FAIL, f"{n} characters: long prompts are easy to misread")


def _language(ctx: PromptContext):
    if not _is_code_request(ctx.text):
        return NA, "not a code request"
    m = _LANGUAGE.search(ctx.text)
    return (PASS, f"names {m.group(1)}") if m else (FAIL, "no language named; Python is assumed")


def _io(ctx: PromptContext):
    if not _is_code_request(ctx.text):
        return NA, "not a code request"
    return (PASS, "describes inputs or outputs") if _IO_WORDS.search(ctx.text) \
        else (FAIL, "does not say what goes in or what comes out")


def _calm(ctx: PromptContext):
    shouting = [w for w in re.findall(r"\b[A-Z]{3,}\b", ctx.text)
                if w not in {"SQL", "API", "PAN", "JSON", "CSV", "HTML", "HTTP", "URL", "GCD", "LCM", "AND", "OR",
                             "NOT", "XML", "UUID", "SECRET", "AADHAAR", "EMAIL", "PHONE", "CARD", "NAME", "UPI"}
                and not re.search(rf"\[{w}(_\d+)?\]", ctx.text)]
    if len(shouting) >= 2 or "!!" in ctx.text:
        return FAIL, "shouting (capitals or !!) — the request is flagged for a human if it continues"
    return PASS, "calm tone"


def _vague(ctx: PromptContext):
    m = _VAGUE.search(ctx.text)
    return (FAIL, f"vague word '{m.group(0)}'") if m else (PASS, "no vague words")


def _single_task(ctx: PromptContext):
    numbered = len(re.findall(r"(?:^|\s)\d+[.)]\s+\w", ctx.text))
    if _MULTI_TASK.search(ctx.text) or numbered >= 3:
        return FAIL, "asks for several things at once"
    return PASS, "one task"


def _no_personal_data(ctx: PromptContext):
    if ctx.findings:
        return FAIL, "personal data was included (it was masked, but it is not needed)"
    return PASS, "no personal data shared"


PROMPT_RULES: tuple[Rule, ...] = (
    Rule("H_PII_MASKED", "Personal data masked before any model call", "hard", "prompt",
         "PAN, Aadhaar, cards, keys, phones, emails, ... are replaced before anything else runs", _pii_masked),
    Rule("H_IDENTITY_FROM_SIGN_IN", "Identity comes from the sign-in only", "hard", "prompt",
         "claiming another user or role in the text is refused", _identity),
    Rule("H_NO_CROSS_USER_DATA", "No data leaks between users", "hard", "prompt",
         "asking for another user's runs, code, history or tickets is refused", _cross_user),
    Rule("H_NO_FILE_DELETION", "No deleting files or wiping disks", "hard", "prompt",
         "requests whose goal is to delete files or format a disk are refused", _intent_rule("H_NO_FILE_DELETION")),
    Rule("H_NO_SYSTEM_COMMANDS", "No system or shell commands", "hard", "prompt",
         "running shell commands, sudo, rm -rf, shutdown, installs are refused", _intent_rule("H_NO_SYSTEM_COMMANDS")),
    Rule("H_NO_SECRET_ACCESS", "No reading credentials or system secrets", "hard", "prompt",
         "environment variables, saved passwords, cookies, keychain, processes", _intent_rule("H_NO_SECRET_ACCESS")),
    Rule("H_NO_INFINITE_LOOP", "No infinite loops", "hard", "prompt",
         "code that never stops is refused (asking what one is, is fine)", _intent_rule("H_NO_INFINITE_LOOP")),
    Rule("H_NO_MALWARE", "No malicious software", "hard", "prompt",
         "keyloggers, ransomware, credential stealing, DDoS", _intent_rule("H_NO_MALWARE")),
    Rule("H_PROMPT_LENGTH", f"Prompt at most {MAX_PROMPT_CHARS} characters", "hard", "prompt",
         "very long prompts are refused", _length),

    Rule("S_CLEAR_ACTION", "Says what to do", "soft", "prompt", "an action verb or a question", _clear_action,
         "Start with what you want: “Write…”, “Explain…”, “Fix…”."),
    Rule("S_SPECIFIC", "Specific enough (5+ words)", "soft", "prompt", "very short prompts are ambiguous", _specific,
         "Add a few words about what the code should do."),
    Rule("S_CONCISE", "Concise (at most 1200 characters)", "soft", "prompt", "one focused request", _concise,
         "Shorten the request to the essential task."),
    Rule("S_LANGUAGE", "Names the language", "soft", "prompt", "Python, JavaScript or SQL", _language,
         "Say which language you want, e.g. “in Python”."),
    Rule("S_INPUT_OUTPUT", "Describes inputs and outputs", "soft", "prompt", "what goes in, what comes out", _io,
         "Say what the function takes and returns, e.g. “takes a list of numbers and returns the largest”."),
    Rule("S_CALM_TONE", "Calm tone", "soft", "prompt", "no shouting", _calm,
         "Describe the problem calmly; if you need a person, ask for one."),
    Rule("S_NO_VAGUE_WORDS", "No vague words", "soft", "prompt", "no “something”, “stuff”, “etc.”", _vague,
         "Replace vague words with the exact thing you mean."),
    Rule("S_SINGLE_TASK", "One task per request", "soft", "prompt", "one thing at a time", _single_task,
         "Send one request per task."),
    Rule("S_NO_PERSONAL_DATA", "No personal data needed", "soft", "prompt", "do not paste IDs, keys or numbers",
         _no_personal_data, "Use made-up example values instead of real personal data."),
)


# =====================================================================================================
# Code-stage rules: the policy gate's decision, shown as the same named rules, plus soft quality checks.
# =====================================================================================================
_CODE_HARD = (
    ("H_CODE_PARSES", "Generated code parses", ("code does not parse",)),
    ("H_CODE_NO_INFINITE_LOOP", "No infinite loops in the code", ("Infinite loop",)),
    ("H_CODE_NO_OS_ACCESS", "No OS access or system commands in the code",
     ("Operating-system access", "Runs system commands", "Dangerous shell command")),
    ("H_CODE_NO_FILE_DELETION", "No deleting files in the code", ("Deletes or damages files",)),
    ("H_CODE_NO_DYNAMIC_CODE", "No eval/exec or sandbox escapes", ("Dynamic code", "Sandbox escape")),
)
_RISKY = ("File access", "Network access", "Console input", "Unknown module")


def check_code(state: dict) -> list[RuleResult]:
    """Hard and soft results for the generated code (empty when no code was generated)."""
    code = state.get("code")
    if not code:
        return []
    policy = state.get("policy") or {}
    reasons = policy.get("reasons") or []
    out = []
    for rid, name, prefixes in _CODE_HARD:
        hits = [r for r in reasons if r.startswith(prefixes)] if policy.get("decision") == "deny" else []
        out.append(RuleResult(rid, name, "hard", "code", FAIL if hits else PASS,
                              "; ".join(hits) if hits else "none found by the policy gate"))
    risky = [r for r in reasons if r.startswith(_RISKY)]
    approval = state.get("approval")
    if not risky:
        out.append(RuleResult("H_CODE_RISKY_ACCESS_APPROVED", "File or network access approved by a person", "hard",
                              "code", PASS, "no file, network or unknown-module access"))
    else:
        status = {"approved": PASS, "rejected": FAIL}.get(approval or "", APPROVAL)
        what = {"approved": "approved by an instructor", "rejected": "rejected by an instructor"}.get(
            approval or "", "waiting for an instructor")
        out.append(RuleResult("H_CODE_RISKY_ACCESS_APPROVED", "File or network access approved by a person", "hard",
                              "code", status, f"{what}: " + "; ".join(risky)))

    python = state.get("language", "python") == "python"
    run = state.get("run") or {}
    tests = state.get("tests") or ""
    soft = [
        ("S_CODE_TESTS_PASS", "Generated tests pass",
         (NA, "tests have not run") if not run else
         ((PASS, "all tests passed") if run.get("exit_code") == 0 else (FAIL, "some tests failed"))),
        ("S_CODE_ENOUGH_TESTS", "At least 2 tests",
         _count_tests(tests, python)),
        ("S_CODE_DOCSTRING", "Functions have docstrings", _docstrings(code) if python else (NA, "Python only")),
        ("S_CODE_TYPE_HINTS", "Functions have type hints", _hints(code) if python else (NA, "Python only")),
        ("S_CODE_SHORT", "Code under 150 lines",
         (PASS, f"{code.count(chr(10)) + 1} lines") if code.count("\n") < 150 else
         (FAIL, f"{code.count(chr(10)) + 1} lines")),
    ]
    out += [RuleResult(rid, name, "soft", "code", st, det) for rid, name, (st, det) in soft]
    return out


def _count_tests(tests: str, python: bool) -> tuple[str, str]:
    if not tests:
        return NA, "no tests"
    n = len(re.findall(r"^\s*def\s+test_", tests, re.M)) if python else \
        len(re.findall(r"\b(?:test|it)\s*\(", tests))
    n = n or len(re.findall(r"\bassert\b", tests))
    return (PASS, f"{n} tests") if n >= 2 else (FAIL, f"{n} test")


def _functions(code: str):
    try:
        return [n for n in ast.walk(ast.parse(code)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    except SyntaxError:
        return None


def _docstrings(code: str) -> tuple[str, str]:
    fns = _functions(code)
    if not fns:
        return NA, "no functions"
    missing = [f.name for f in fns if not ast.get_docstring(f)]
    return (FAIL, "missing in " + ", ".join(missing[:3])) if missing else (PASS, f"{len(fns)} documented")


def _hints(code: str) -> tuple[str, str]:
    fns = _functions(code)
    if not fns:
        return NA, "no functions"
    missing = [f.name for f in fns if f.returns is None or any(a.annotation is None for a in f.args.args
                                                                if a.arg not in {"self", "cls"})]
    return (FAIL, "missing in " + ", ".join(missing[:3])) if missing else (PASS, "all typed")


# =====================================================================================================
# The report
# =====================================================================================================
@dataclass
class RulesReport:
    results: list[RuleResult] = field(default_factory=list)
    access: Denial | None = None           # set when an identity / cross-user hard rule failed
    intent: PolicyResult | None = None     # the destructive-request decision the content rules used

    @property
    def hard_failures(self) -> list[RuleResult]:
        return [r for r in self.results if r.tier == "hard" and r.status == FAIL]

    def to_dict(self) -> dict:
        return summarize([r.to_dict() for r in self.results])


def summarize(results: list[dict]) -> dict:
    """Counts and the soft score; used for the prompt report and again once code rules are added."""
    hard = [r for r in results if r["tier"] == "hard"]
    soft = [r for r in results if r["tier"] == "soft" and r["status"] != NA]
    soft_ok = sum(r["status"] == PASS for r in soft)
    score = soft_ok / len(soft) if soft else 1.0
    return {
        "hard": [r for r in results if r["tier"] == "hard"],
        "soft": [r for r in results if r["tier"] == "soft"],
        "summary": {
            "hard_passed": sum(r["status"] == PASS for r in hard), "hard_total": len(hard),
            "hard_failed": sum(r["status"] == FAIL for r in hard),
            "hard_waiting": sum(r["status"] == APPROVAL for r in hard),
            "hard_ok": not any(r["status"] == FAIL for r in hard),
            "soft_passed": soft_ok, "soft_total": len(soft), "soft_score": round(score, 3),
            "soft_target": SOFT_TARGET, "soft_ok": score >= SOFT_TARGET,
            "soft_needed": math.ceil(SOFT_TARGET * len(soft)) if soft else 0,
        },
    }


def check_prompt(text: str, findings: list[dict], identity: Identity, config: dict) -> RulesReport:
    """Run every prompt rule on the masked prompt. Called before any model call."""
    ctx = PromptContext(text=text, findings=findings, identity=identity, config=config)
    ctx.access = check_access_request(text, identity, config)
    ctx.intent = check_request_intent(text)
    results = []
    for rule in PROMPT_RULES:
        status, detail = rule.check(ctx)
        results.append(RuleResult(rule.id, rule.name, rule.tier, rule.stage, status, detail,
                                  rule.tip if status == FAIL else ""))
    return RulesReport(results, ctx.access, ctx.intent)


def full_report(prompt_rules: dict | None, state: dict) -> dict:
    """The prompt report plus the code-stage rules, as attached to a reply."""
    results = list((prompt_rules or {}).get("hard", [])) + list((prompt_rules or {}).get("soft", []))
    results += [r.to_dict() for r in check_code(state)]
    return summarize(results)


def catalog() -> list[dict]:
    """Every rule, for documentation and the UI."""
    rows = [{"id": r.id, "name": r.name, "tier": r.tier, "stage": r.stage, "description": r.description}
            for r in PROMPT_RULES]
    rows += [{"id": rid, "name": name, "tier": "hard", "stage": "code", "description": ", ".join(p)}
             for rid, name, p in _CODE_HARD]
    rows.append({"id": "H_CODE_RISKY_ACCESS_APPROVED", "name": "File or network access approved by a person",
                 "tier": "hard", "stage": "code", "description": ", ".join(_RISKY)})
    return rows
