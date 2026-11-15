"""check_access_request (R1): refuse, in code and before any model call, messages that claim another
identity or ask for another user's data.

Identity only ever comes from the signed-in user (config.yaml). Typing "I am user_2" or "I'm the admin"
changes nothing, and asking for someone else's runs, code, history or tickets is refused with a clear
message. The model never sees such a request, so it cannot be talked into "helping".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from guard.identity import Identity


@dataclass(frozen=True)
class Denial:
    """Why a message was refused. kind: impersonation | role | user_data | group_data."""
    kind: str
    target: str   # who or what was targeted, ready to show: "User 2 (user_2)", "admin", "other users"

    def __str__(self) -> str:  # used in logs
        return f"{self.kind}: {self.target}"

DATA_WORDS = (r"runs?|history|code|data|requests?|questions?|tickets?|results?|answers?|outputs?|account|"
              r"messages?|submissions?|chats?|conversations?|files?|details|info(?:rmation)?|records?|"
              r"activity|logs?|prompts?|pan|aadhaar")
CLAIM_VERBS = (r"i\s*am|i'm|im|this\s+is|it'?s|log(?:ged)?\s*in\s+as|sign(?:ed)?\s*in\s+as|acting\s+as|"
               r"act\s+as|pretend(?:ing)?\s+(?:to\s+be|i'?m|i\s+am)|switch\s+(?:me\s+)?to|treat\s+me\s+as|"
               r"speaking\s+as|on\s+behalf\s+of|impersonate|become|make\s+me|give\s+me\s+(?:the\s+)?role\s+of")
GROUP_TARGETS = (r"another\s+user'?s?|other\s+users?'?s?|all\s+(?:the\s+)?users?'?s?|every\s*(?:one|body)'?s?|"
                 r"each\s+user'?s?|any\s+user'?s?|someone\s+else'?s?|other\s+people'?s?")


def _identity_names(config: dict) -> dict[str, set[str]]:
    """Every way a configured user can be named: user_2, user 2, user2, "User 2"; roles too."""
    names: dict[str, set[str]] = {}
    for uid, u in config.get("users", {}).items():
        forms = {uid.lower(), uid.lower().replace("_", " "), uid.lower().replace("_", ""), u.get("name", "").lower()}
        names[uid] = {f for f in forms if f}
    return names


def _role_words(config: dict) -> set[str]:
    roles = {u["role"].lower() for u in config.get("users", {}).values()}
    return roles | {"admin", "administrator", "instructor", "teacher", "superuser", "root", "moderator", "staff"}


def check_access_request(text: str, caller: Identity, config: dict) -> Denial | None:
    """Returns why to refuse, or None when the message does not try to reach other users' data."""
    t = " ".join((text or "").lower().split())
    others = {uid: forms for uid, forms in _identity_names(config).items() if uid != caller.user_id}
    other_names = sorted({f for forms in others.values() for f in forms}, key=len, reverse=True)
    generic_user = r"user[\s_-]?\d+"
    target = "|".join([re.escape(n) for n in other_names] + [generic_user])

    def is_self(match_text: str) -> bool:
        own = _identity_names(config).get(caller.user_id, set())
        return any(re.fullmatch(rf"(?:{re.escape(o)})", match_text.strip()) for o in own)

    def who(match_text: str) -> str:
        """Name the targeted user the way the UI shows users: "User 2 (user_2)"."""
        m = match_text.strip()
        for uid, forms in others.items():
            if m in forms:
                return config["users"][uid].get("name", uid)
        return "another user"

    # 1. Claiming to be another user ("I am user_2", "log in as user 2", "on behalf of User 2").
    for m in re.finditer(rf"\b(?:{CLAIM_VERBS})\s+(?:an?\s+|the\s+)?({target})\b", t):
        if not is_self(m.group(1)):
            if m.group(1).strip() in _role_words(config):   # "I'm the admin" is a role claim
                return Denial("role", m.group(1).strip())
            return Denial("impersonation", who(m.group(1)))

    # 2. Claiming a role the account does not have ("I'm an admin", "make me admin").
    for role in _role_words(config) - {caller.role}:
        if re.search(rf"\b(?:{CLAIM_VERBS})\s+(?:an?\s+|the\s+)?{re.escape(role)}\b", t):
            return Denial("role", role)

    # Admins may see every run (they approve code), so only identity claims apply to them.
    if caller.role == "admin":
        return None
    # A request to WRITE code that mentions users ("a SQL query that lists all users", "a function that
    # returns data for user 2") is a coding task, not a request for our users' data.
    coding = re.search(r"\b(?:write|create|implement|build|generate|make|code|design)\b.*\b(?:function|class|"
                       r"method|query|program|script|sql|api|endpoint|module|schema|table)\b", t)
    asking_about_users = rf"(?:{target}|{GROUP_TARGETS}|others|anyone\s+else)"
    activity = re.search(rf"\b(?:what|which|who)\b.{{0,20}}\b(?:did|has|have|are|is|was|were)\s+(?:the\s+)?"
                 rf"({asking_about_users})\s+(?:been\s+)?(?:ask|asked|asking|send|sent|sending|submit|submitted|write|"
                 rf"wrote|writing|request|requested|requesting|run|ran|running|do|did|doing|search|searched)", t)
    if activity:
        named = who(activity.group(1))
        return Denial("user_data", named) if named != "another user" else Denial("group_data", "other users")
    if coding:
        return None

    # 3. Asking for a named user's data ("user_2's runs", "history of user 2", "show user 2 code").
    for m in re.finditer(rf"\b({target})(?:'s|s'|')?\s+(?:last\s+|latest\s+|recent\s+|previous\s+|old\s+)?"
                         rf"(?:{DATA_WORDS})\b", t):
        if not is_self(m.group(1)):
            return Denial("user_data", who(m.group(1)))
    for m in re.finditer(rf"\b(?:{DATA_WORDS})\s+(?:of|for|from|by|belonging\s+to|submitted\s+by|owned\s+by)\s+"
                         rf"(?:the\s+)?({target})\b", t):
        if not is_self(m.group(1)):
            return Denial("user_data", who(m.group(1)))

    # 4. Asking for other users' or everyone's data ("all users' runs", "someone else's code").
    if re.search(rf"\b(?:{GROUP_TARGETS})\s+(?:\w+\s+){{0,2}}(?:{DATA_WORDS})\b", t) or \
            re.search(rf"\b(?:{DATA_WORDS})\s+(?:of|for|from|by)\s+(?:{GROUP_TARGETS})", t):
        return Denial("group_data", "other users")
    if re.search(r"\b(?:show|list|give|get|see|view|display|dump|export)\s+(?:me\s+)?(?:all|every)\s+"
                 r"(?:the\s+)?(?:runs|requests|tickets|histories|submissions|users)\b", t):
        return Denial("group_data", "all users")
    return None


def refusal(caller: Identity, denial: Denial) -> dict:
    """A short headline plus a plain explanation, specific to what was attempted."""
    me = f"{caller.name} ({caller.user_id})"
    if denial.kind == "impersonation":
        title = f"Access denied: you can't act as {denial.target}"
        reason = (f"You are signed in as {me}. Who you are comes from your sign-in, not from what you type, "
                  f"so saying you are someone else doesn't change it.")
    elif denial.kind == "role":
        title = f"Access denied: you can't take the {denial.target} role"
        reason = (f"You are signed in as {me}, a {caller.role} account. Roles come from your account, not from "
                  f"what you type, so asking for or claiming a role doesn't change it.")
    elif denial.kind == "user_data":
        title = f"Access denied: you can't view {denial.target}'s data"
        reason = (f"Each user's requests, code and answers are private to them. You are signed in as {me}.")
    else:
        title = f"Access denied: you can't view {denial.target}' data" if denial.target.endswith("s") \
            else f"Access denied: you can't view {denial.target}'s data"
        reason = (f"Each user's requests, code and answers are private to them. You are signed in as {me}.")
    return {
        "kind": denial.kind,
        "title": title,
        "reason": reason,
        "allowed": "You can only see your own requests, code and answers. They are listed under Your history.",
        "assurance": "No other user's data was read, and nothing was sent to the AI model.",
    }


def refusal_message(caller: Identity, denial: Denial) -> str:
    r = refusal(caller, denial)
    return f"{r['title']}.\n{r['reason']}\n{r['allowed']}\n{r['assurance']}"
