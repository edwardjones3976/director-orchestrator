# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]]
"""Operator CLI for the external engineering ledger. Never calls a provider."""
import argparse
import json
from pathlib import Path
import sqlite3

from director_orchestrator.engineering_workload_ledger import (
    EngineeringWorkloadLedger, LedgerError, human_status, policy_preview,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="One operator-controlled journal for this team")
    parser.add_argument("--human", action="store_true", help="Human-readable status instead of JSON")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    for name in ("bootstrap", "enroll-route", "reserve", "policy-preview"):
        commands.add_parser(name).add_argument("--input", type=Path, required=True)
    begin = commands.add_parser("begin-attempt")
    begin.add_argument("assignment_id")
    begin.add_argument("--transport", required=True)
    begin.add_argument("--new-information", type=Path)
    outcome = commands.add_parser("record-outcome")
    outcome.add_argument("assignment_id")
    outcome.add_argument("--attempt", required=True, type=int)
    outcome.add_argument("--input", required=True, type=Path)
    settle = commands.add_parser("settle")
    settle.add_argument("assignment_id")
    settle.add_argument("--outcome", required=True)
    settle.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)

    def read(path):
        if path.stat().st_size > 2 * 1024 * 1024:
            raise LedgerError("input_exceeds_2MiB")
        return json.loads(path.read_text(encoding="utf-8-sig"))

    try:
        if args.action == "policy-preview":
            result = policy_preview(read(args.input))
        else:
            if args.db is None:
                parser.error("--db is required except for policy-preview")
            ledger = EngineeringWorkloadLedger(args.db)
            if args.action == "status":
                result = ledger.status()
            elif args.action in {"bootstrap", "reserve", "enroll-route"}:
                result = getattr(ledger, args.action.replace("-", "_"))(read(args.input))
            elif args.action == "begin-attempt":
                result = ledger.begin_attempt(args.assignment_id, transport=args.transport,
                    new_information_evidence=read(args.new_information) if args.new_information else None)
            elif args.action == "record-outcome":
                result = ledger.record_outcome(args.assignment_id, args.attempt, read(args.input))
            else:
                result = ledger.settle(args.assignment_id, args.outcome, read(args.evidence))
        print(human_status(result) if args.human and "history_mode" in result else json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (LedgerError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        # Do not echo file contents, private assignments, or provider credentials.
        message = str(exc) if isinstance(exc, LedgerError) else type(exc).__name__
        print(json.dumps({"error": message, "dispatch_permitted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]]
