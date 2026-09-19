# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | BEGIN]]
"""Operator entry point for the Z.ai trial-only contact lane (owner directive #207).

status    readiness booleans, the public lane identity and trial-ledger metrics; never a
          key, path or configured value
dispatch  one bounded, tools-off Lite assignment from a v1 work packet file
evaluate  record the non-Z.ai evaluator's verdict for one recorded attempt
resolve   close an attempt that a killed process left unresolved, as AMBIGUOUS only

Only `ZAI_*` lines are read from `--env-file` (a repeated name is refused); other project
credentials are never exported. Startup validation runs first, so a widened configuration
stops here rather than reaching the provider.
"""
import argparse
import json
import os
import sys
from pathlib import Path


def _load_zai_env(path: Path) -> None:
    seen = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name.startswith("ZAI_"):
            continue
        if name in seen:
            raise SystemExit(json.dumps({"status": "REFUSED", "reason": f"{name} is bound more than once in {path.name}"}))
        seen.add(name)
        if name not in os.environ:
            os.environ[name] = value.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, help="dotenv file; only ZAI_* lines are read")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--work-packet", required=True, type=Path, help="v1 model work packet JSON")
    dispatch.add_argument("--purpose", required=True, help="one line for the trial ledger")
    dispatch.add_argument(
        "--recovery-basis",
        help="only for the single recovery after a recorded FAILED outcome: why the failure was temporary",
    )
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--attempt", required=True, help="attempt id printed by dispatch")
    evaluate.add_argument("--evaluator", required=True, help="the non-Z.ai lane or person who checked the work")
    evaluate.add_argument("--correctness", required=True, choices=("correct", "incorrect", "unverified"))
    evaluate.add_argument("--first-pass", action="store_true", help="accepted without any correction")
    evaluate.add_argument("--correction-count", type=int, default=0)
    evaluate.add_argument("--privacy-fit", required=True, choices=("fit", "unfit", "unassessed"))
    evaluate.add_argument(
        "--usefulness", required=True, choices=("accepted", "partially_useful", "unusable", "unassessed")
    )
    evaluate.add_argument("--notes", help="stored as a digest only")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--attempt", required=True, help="attempt id left without an outcome")
    resolve.add_argument("--reason", required=True, help="what interrupted the attempt (stored as the error type)")
    return parser


def _refused(reason: str) -> int:
    print(json.dumps({"status": "REFUSED", "reason": reason}))
    return 2


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.env_file:
        _load_zai_env(args.env_file)
    # Import after the environment is set: Settings reads it at import time.
    from director_orchestrator.config import Settings
    from director_orchestrator.zai_lane import (
        ZaiTrialLane,
        ZaiTrialLaneUnavailable,
        ZaiTrialLedger,
        ZaiTrialLedgerError,
    )
    from director_orchestrator.zai_trial import redact_bound_key

    settings = Settings()
    try:
        settings.validate_zai_trial_configuration()
        ledger = ZaiTrialLedger(settings.zai_trial_ledger_path) if settings.zai_trial_ledger_path else None
    except (ValueError, ZaiTrialLedgerError) as exc:
        # A widened setting, a ledger path that names another store, or a file that is
        # not this build's ledger: refuse as JSON before anything is read or written.
        return _refused(str(exc))

    if args.command == "status":
        print(json.dumps({
            "readiness": settings.zai_trial_readiness(),
            "ledger": ledger.status() if ledger is not None else None,
        }, indent=2))
        return 0

    if args.command in ("evaluate", "resolve"):
        if ledger is None:
            return _refused("ZAI_TRIAL_LEDGER_PATH is not configured")
        try:
            if args.command == "evaluate":
                ledger.record_evaluation(
                    args.attempt, evaluator=args.evaluator, correctness=args.correctness,
                    first_pass=args.first_pass, correction_count=args.correction_count,
                    privacy_fit=args.privacy_fit, usefulness=args.usefulness, notes=args.notes,
                )
            else:
                # An operator can only close an orphaned attempt as AMBIGUOUS: it counts
                # as a recorded failure and is never redispatched.
                ledger.record_outcome(
                    args.attempt, status="AMBIGUOUS", latency_ms=0,
                    error_type="operator_resolved:" + args.reason.strip()[:120],
                )
        except ZaiTrialLedgerError as exc:
            return _refused(str(exc))
        print(json.dumps({"status": "RECORDED", "attempt_id": args.attempt, "ledger": ledger.status()}, indent=2))
        return 0

    from director_orchestrator.langgraph_runtime import ProviderAdapterContext
    from director_orchestrator.packet_contracts import PacketContractError, document_digest, validate_packet

    try:
        work = validate_packet("work", json.loads(args.work_packet.read_text(encoding="utf-8")))
    except (OSError, ValueError, PacketContractError) as exc:
        return _refused(f"work packet unreadable or invalid: {type(exc).__name__}")
    context = ProviderAdapterContext(
        task_id=work["task_id"], assignment_id=work["assignment_id"],
        idempotency_key=work["idempotency_key"], work_packet_digest=document_digest(work),
        writer_token_digest="sha256:" + "0" * 64,
        timeout_seconds=min(int(work["limits"]["timeout_seconds"]), settings.zai_timeout_seconds),
        budget_usd=0, max_hops=1, max_revisions=1, allowed_tools=(), allowed_external_effects=(),
    )
    try:
        lane = ZaiTrialLane.from_settings(settings)
        dispatched = lane.dispatch(
            work, context=context, purpose=args.purpose, recovery_basis=args.recovery_basis
        )
    except (ZaiTrialLaneUnavailable, ZaiTrialLedgerError) as exc:
        return _refused(str(exc))
    except Exception as exc:  # provider output may be inside; report the type only
        print(json.dumps({
            "status": "FAILED", "error_type": type(exc).__name__,
            "detail": "attempt retained in the trial ledger and journal; no automatic retry",
        }))
        return 1
    outcome = dispatched.result.get("outcome")
    print(json.dumps({
        "status": "DISPATCHED", "attempt_id": dispatched.attempt_id,
        "latency_ms": dispatched.latency_ms, "result_status": dispatched.result["status"],
        "result_packet_digest": document_digest(dispatched.result),
        # The transport redacts its receipts; stdout gets the same treatment.
        "outcome": redact_bound_key(outcome, settings.zai_api_key) if settings.zai_api_key else outcome,
        "production_credit": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | END]]
