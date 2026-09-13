# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
"""Append one measured offline row, retaining a failure and then stopping."""
from __future__ import annotations

import argparse
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path

import jsonschema

from director_orchestrator.completion_scorecard import (
    OFFLINE_RUN_RECORD_FIELDS, evaluate_run_record,
)

SPEC = importlib.util.spec_from_file_location(
    "offline_trial_collector", Path(__file__).with_name("collect-offline-slack-trials.py"))
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
SCHEMAS = Path(__file__).resolve().parents[1] / "docs/schemas"


def read_json(path):
    with path.open("rb") as handle:
        raw = handle.read(collector.MAX_BYTES + 1)
    if len(raw) > collector.MAX_BYTES:
        raise ValueError("JSON exceeds byte bound")
    return json.loads(raw), collector.digest(raw)


def validate_row(row):
    schema, _ = read_json(SCHEMAS / "director-offline-trial-run-v1.schema.json")
    jsonschema.Draft202012Validator(schema).validate(row)
    # JSON encoders/decoders can otherwise accept non-finite numbers.
    collector.encode(row)
    if set(row) != OFFLINE_RUN_RECORD_FIELDS:
        raise ValueError("Run field shape differs from scorecard")


def row_from_receipt(receipt, rubric, rubric_digest, *, trial_index=1):
    schema, _ = read_json(SCHEMAS / "director-slack-trial-observations-v1.schema.json")
    jsonschema.Draft202012Validator(schema).validate(receipt)
    if not 1 <= trial_index <= len(receipt["trials"]):
        raise ValueError("Trial index outside receipt")
    trial = receipt["trials"][trial_index - 1]
    if not receipt.get("collection_failed") and receipt.get("rubric_sha256") != rubric_digest:
        raise ValueError("Receipt and rubric bytes differ")
    evaluation = collector.evaluate_rubric(trial, rubric, rubric_digest)
    if trial.get("rubric_evaluation") is not None and trial["rubric_evaluation"] != evaluation:
        raise ValueError("Stored rubric evaluation differs from recomputed evidence")
    stable = receipt.get("runtime_collection_binding_stable") is True
    runtime = receipt.get("collection_runtime_before") or {}
    stable = stable and runtime == receipt.get("collection_runtime_after")
    healthy = receipt.get("runtime_health_at_collection") or {}
    # Recompute timing from the receipt's source fields, never its summaries.
    wall = pause = machine = None
    messages = trial.get("terminal_messages") or []
    input_ts = trial.get("input_message_ts") or ""
    final_ts = (messages[0].get("ts") or "") if len(messages) == 1 else ""
    if (trial.get("task_record_bound") is True
            and trial.get("owner_account_input_matched") is True
            and trial.get("slack_thread_complete_at_read") is True
            and collector.TS.fullmatch(input_ts) and collector.TS.fullmatch(final_ts)):
        elapsed = Decimal(final_ts) - Decimal(input_ts)
        if elapsed >= 0:
            wall = float(elapsed)
        if (trial.get("final_receipt_matches_native_message") is True
                and trial.get("delivery_receipt_list_complete") is True):
            pause, machine = collector.owner_pause(
                trial.get("delivery_receipts") or [], wall, input_ts, final_ts)
    for field, measured in (("wall_seconds", wall), ("owner_pause_seconds", pause),
                            ("machine_seconds", machine)):
        if measured is not None and trial.get(field) is not None and trial[field] != measured:
            raise ValueError("Receipt timing summary contradicts source timestamps")
    zero_verified = (trial.get("operational_provider_attempts_verified") is True
        and trial.get("zero_calls_in_exposed_task_projection") is True
        and trial.get("all_recorded_outcomes_are_offline_doubles") is True
        and runtime.get("offline_configured") is True and runtime.get("paid_configured") is False
        and healthy.get("paid_runs_enabled") is False and stable)
    execution_bound = stable and trial.get("execution_image_binding_verified") is True
    row = {
        "run_id": trial.get("task_id") or "unresolved-thread:" + trial["thread_ts"],
        "mode": "offline",
        "actual_owner_event": trial.get("owner_account_input_matched") is True
            and trial.get("task_api_gateway_attested") is True and trial.get("task_record_bound") is True,
        "correctness_verified": evaluation["passed"],
        "task_count": 1 if trial.get("global_task_deduplication_verified") is True else None,
        "terminal_reply_count": trial.get("terminal_reply_count"),
        "wall_seconds": wall,
        "owner_pause_seconds": pause,
        "machine_seconds": machine,
        "ambiguous_effects": healthy.get("ambiguous_effect_count") if stable
            and healthy.get("reconciliation_hold_reason") is None else None,
        "delivery_ambiguous": False if trial.get("delivery_receipt_list_complete") is True
            and trial.get("final_receipt_matches_native_message") is True
            and all(r.get("state") in {"DELIVERED", "SUPERSEDED", "SUPPRESSED"}
                    for r in trial.get("delivery_receipts", [])) else None,
        "operational_provider_attempts": 0 if zero_verified else None,
        "actual_cost_usd": 0 if zero_verified else None,
        "cost_basis": "simulation_zero_calls" if zero_verified else None,
        "runtime_sha": runtime.get("runtime_head") if execution_bound else None,
        "image_id": runtime.get("image_id") if execution_bound else None,
        "simulation_label": collector.LABEL if trial.get("all_recorded_outcomes_are_offline_doubles") is True else None,
    }
    validate_row(row)
    return row


def qualifies(row):
    if row["runtime_sha"] is None or row["image_id"] is None:
        return False
    return evaluate_run_record(row, mode="offline", runtime_sha=row["runtime_sha"],
                               image_id=row["image_id"])[0] is True


def append_row(path, row):
    validate_row(row)
    # Do not leave a lock on ordinary exceptions; a crash leaves a visible stop.
    lock = path.with_name(path.name + ".lock")
    handle = lock.open("xb")
    try:
        previous = []
        if path.exists():
            with path.open("rb") as source:
                raw = source.read(collector.MAX_BYTES + 1)
            if len(raw) > collector.MAX_BYTES or (raw and not raw.endswith(b"\n")):
                raise ValueError("Existing sequence is oversized or truncated")
            previous = [json.loads(line) for line in raw.splitlines()]
        if len(previous) >= 10:
            raise ValueError("Sequence already contains ten rows")
        seen = set()
        for prior in previous:
            validate_row(prior)
            if not qualifies(prior):
                raise ValueError("Sequence stopped after non-qualifying row; diagnose before a new sequence")
            if prior["run_id"] in seen:
                raise ValueError("Existing sequence contains duplicate runs")
            seen.add(prior["run_id"])
        if row["run_id"] in seen:
            raise ValueError("Run already recorded")
        if previous and any(row[key] != previous[0][key] for key in ("runtime_sha", "image_id")):
            # Unknown evidence is still recorded as a failing row, then stops.
            if row["runtime_sha"] is not None and row["image_id"] is not None:
                raise ValueError("Sequence runtime/image changed")
        with path.open("ab") as target:
            target.write(collector.encode(row) + b"\n")
            target.flush()
            os.fsync(target.fileno())
        return qualifies(row)
    finally:
        handle.close()
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--trial-index", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    append_attempted = False
    try:
        if args.output.resolve() in {args.receipt.resolve(), args.rubric.resolve()}:
            raise ValueError("Output cannot be an input file")
        receipt, receipt_digest = read_json(args.receipt)
        rubric, rubric_digest = collector.load_rubric(args.rubric)
        row = row_from_receipt(receipt, rubric, rubric_digest, trial_index=args.trial_index)
        append_attempted = True
        qualified = append_row(args.output, row)
        print(json.dumps({"appended": True, "qualified": qualified,
            "receipt_sha256": receipt_digest, "rubric_sha256": rubric_digest,
            "sequence_stopped": not qualified, "scorecard_mutations": 0}))
        return 0 if qualified else 2
    except OSError:
        # A storage error can occur after bytes were written. Never claim that
        # such an append did not happen or encourage an automatic retry.
        print(json.dumps({"appended": None if append_attempted else False,
            "details_withheld": True, "sequence_stopped": True}))
        return 1
    except (ValueError, KeyError, TypeError, AttributeError, jsonschema.ValidationError):
        print(json.dumps({"appended": False, "details_withheld": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:22 UTC | END]]
