"""Rules suite: 20 hard and 20 soft test cases for the central rules (rules.py).

  python -m evals.rules_suite          # prints failures and the totals; exit 1 when below target

The suite passes only when 100% of the hard cases and at least 85% of the soft cases pass. It needs no
model and no network: rules run in code. evals.run_gate runs it too, so CI and the Quality page show it.

A case gives a prompt (checked as a request from `user`) or a piece of code (checked by the code policy),
and what to expect:
  {"blocked": true|false}              did a hard rule refuse the prompt?
  {"rule": "<id>", "status": "..."}    the status of one named rule (pass | fail | approval | n/a)
  {"masked": ["<raw value>", ...]}     these raw values never reach the text a model would see
  {"soft_ok": true|false}              is the prompt's soft score at or above the 85% target?
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import get_config  # noqa: E402
from guard.identity import get_identity  # noqa: E402
from guard.mask import find_pii, mask  # noqa: E402
from policy import evaluate_code_policy  # noqa: E402
from rules import SOFT_TARGET, check_code, check_prompt, summarize  # noqa: E402

CASES = HERE / "rules_cases.json"


def run_case(case: dict) -> tuple[bool, str]:
    cfg = get_config()
    exp = case["expect"]
    if "code" in case:
        policy = evaluate_code_policy(case["code"])
        results = [r.to_dict() for r in check_code({"code": case["code"], "policy": policy.model_dump(),
                                                     "language": "python"})]
        report = summarize(results)
        masked_text = case["code"]
        blocked = report["summary"]["hard_failed"] > 0
    else:
        masked_text, findings = find_pii(case["prompt"])
        report = check_prompt(masked_text.strip(), [f.to_dict() for f in findings],
                              get_identity(cfg, case.get("user", "student_a")), cfg).to_dict()
        blocked = not report["summary"]["hard_ok"]
    by_id = {r["id"]: r for r in report["hard"] + report["soft"]}
    problems = []
    if "blocked" in exp and blocked != exp["blocked"]:
        failed = [r["id"] for r in report["hard"] if r["status"] == "fail"]
        problems.append(f"blocked={blocked}, expected {exp['blocked']} (failed: {failed or 'none'})")
    if "rule" in exp:
        got = by_id.get(exp["rule"], {}).get("status", "missing")
        if got != exp["status"]:
            problems.append(f"{exp['rule']} is {got}, expected {exp['status']} "
                            f"({by_id.get(exp['rule'], {}).get('detail', '')})")
    for raw in exp.get("masked", []):
        if raw in masked_text:
            problems.append("a raw value reached the model text")
    if "soft_ok" in exp and report["summary"]["soft_ok"] != exp["soft_ok"]:
        s = report["summary"]
        problems.append(f"soft score {s['soft_passed']}/{s['soft_total']}, expected soft_ok={exp['soft_ok']}")
    return not problems, mask("; ".join(problems) or "ok")


def run_suite() -> dict:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results = []
    for c in cases:
        try:
            ok, detail = run_case(c)
        except Exception as exc:  # a crash is a failure, never a pass
            ok, detail = False, f"error: {type(exc).__name__}: {mask(str(exc))[:200]}"
        results.append({"id": c["id"], "tier": c["tier"], "area": "Rules", "passed": ok, "detail": detail,
                        "input": mask(c.get("prompt") or c.get("code") or "")[:160]})
    hard = [r for r in results if r["tier"] == "hard"]
    soft = [r for r in results if r["tier"] == "soft"]
    needed = math.ceil(SOFT_TARGET * len(soft))
    hard_ok, soft_ok = sum(r["passed"] for r in hard), sum(r["passed"] for r in soft)
    return {"passed": hard_ok == len(hard) and soft_ok >= needed,
            "hard": {"passed": hard_ok, "total": len(hard)},
            "soft": {"passed": soft_ok, "total": len(soft), "needed": needed},
            "failing": [r["id"] for r in results if not r["passed"]], "cases": results}


def main() -> None:
    s = run_suite()
    for r in s["cases"]:
        if not r["passed"]:
            print(f"FAIL {r['id']:<5} {r['detail']}")
    print(f"rules: hard {s['hard']['passed']}/{s['hard']['total']} (need all)  "
          f"soft {s['soft']['passed']}/{s['soft']['total']} (need {s['soft']['needed']})  "
          f"-> {'GREEN' if s['passed'] else 'RED'}")
    sys.exit(0 if s["passed"] else 1)


if __name__ == "__main__":
    main()
