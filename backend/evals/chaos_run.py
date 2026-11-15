"""Chaos run (R5): N requests with the chaos switch on; every reply must name its tier.

  python -m evals.chaos_run              # uses LLM_MODE from backend/.env (live needs the key)
  python -m evals.chaos_run --stub       # offline: a stub model behind the real retry/fallback code
  python -m evals.chaos_run --outage     # total outage instead of 60% chaos

Writes evals/chaos_log.txt.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import os  # noqa: E402
os.environ.setdefault("CODESMITH_DB", ":memory:")   # chaos requests don't fill real history

import service  # noqa: E402
from evals.run_gate import stub_for  # noqa: E402
from guard.quota import QUOTA  # noqa: E402
from llm.client import use_backend  # noqa: E402

REQUESTS = [
    "Write a Python function that checks if a string is a palindrome",
    "Write a function that returns the factorial of n",
    "Write a function that returns the n-th fibonacci number",
    "Write fizzbuzz up to n",
    "Write a function that sums a list using a list comprehension, and a tuple using a functional style",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--stub", action="store_true")
    p.add_argument("--outage", action="store_true")
    a = p.parse_args()
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    controls = {"outage": True} if a.outage else {"chaos": True}
    lines, tiers, answered = [], Counter(), 0
    ctx = use_backend(stub_for({})) if a.stub else None
    if ctx:
        ctx.__enter__()
    try:
        for i in range(a.n):
            QUOTA.reset()
            text = REQUESTS[i % len(REQUESTS)]
            start = time.time()
            reply = service.submit("student_a", text, controls)
            ok = reply["status"] in {"done", "awaiting_approval"} or (reply["status"] == "unavailable" and reply.get("ticket"))
            answered += bool(ok)
            tiers[reply["tier"]] += 1
            line = (f"{i + 1:>2} {reply['status']:<17} tier={reply['tier']:<9} "
                    f"{time.time() - start:5.1f}s  {text[:50]}")
            print(line)
            lines.append(line)
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
    summary = f"answered {answered}/{a.n}; tiers {json.dumps(dict(tiers))}; controls {json.dumps(controls)}"
    print(summary)
    (HERE / "chaos_log.txt").write_text("\n".join(lines + [summary]) + "\n", encoding="utf-8")
    sys.exit(0 if answered == a.n else 1)


if __name__ == "__main__":
    main()
