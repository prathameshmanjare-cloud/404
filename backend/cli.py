"""Console runner for the same service functions the API uses.

  python cli.py --user user_1 "Write a Python function that checks if a string is a palindrome"
  python cli.py --user user_1 --chaos "Write a palindrome checker"
Paused runs live in this process's memory, so approval is asked for right here:
  python cli.py --user user_1 "Write a function that reads a text file and counts its lines"
  -> awaiting_approval, then: approver user id? admin   decision? approve
"""
from __future__ import annotations

import argparse
import json
import logging

import service


def show(reply: dict) -> None:
    print(f"\nstatus: {reply['status']}   tier: {reply['tier']}   thread: {reply['thread_id']}")
    for key in ("code", "tests", "sql"):
        if reply.get(key):
            print(f"\n--- {key} ---\n{reply[key]}")
    if reply.get("ticket"):
        print("\n--- ticket ---\n" + json.dumps(reply["ticket"], indent=2))
    print("\n--- report ---\n" + (reply.get("report") or ""))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("text", nargs="?")
    p.add_argument("--user", default="user_1")
    p.add_argument("--chaos", action="store_true")
    p.add_argument("--outage", action="store_true")
    p.add_argument("--replay", action="store_true")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    service.setup_logging(logging.WARNING if a.quiet else logging.INFO)
    try:
        if not a.text:
            p.error("text is required")
        reply = service.submit(a.user, a.text, {"chaos": a.chaos, "outage": a.outage, "replay": a.replay})
        show(reply)
        while reply["status"] == "awaiting_approval":
            approver = input("\nApprover user id (e.g. admin): ").strip()
            decision = input("Decision [approve/reject]: ").strip().lower()
            try:
                reply = service.resume(reply["thread_id"], approver, decision)
            except service.ServiceError as exc:
                print(f"refused: {exc}")
                continue
            show(reply)
    except service.ServiceError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
