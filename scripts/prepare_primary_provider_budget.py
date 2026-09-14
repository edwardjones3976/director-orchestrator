# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 19:08 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | BEGIN]]
"""Prepare an unsigned primary-provider-budget/v1 intent, or structurally
inspect an already-signed document.

Neither action reads a secret, opens a database connection, or calls a
provider. ``prepare`` (the default) is a pure function of its explicit
arguments; ``inspect`` only checks that a supplied document has the shape of
a ``ProviderBudget`` -- it does not verify the signature (no secret is read
here) and it registers nothing. Turning a prepared intent into a real signed
``ProviderBudget``, and importing one, are both separately authorized
operations that this script does not perform.

Usage:

    PYTHONPATH=src python scripts/prepare_primary_provider_budget.py \\
        --grant-digest <64-hex> --provider-id openai --project-id uppercut \\
        --task-id task-one --day 2026-01-01 --task-limit-microusd 500000 \\
        --daily-limit-microusd 1000000 --attempt-reservation-microusd 100000 \\
        --token-limit 1000

    PYTHONPATH=src python scripts/prepare_primary_provider_budget.py \\
        --action inspect --input signed-budget.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from director_orchestrator.antigravity_team import ProviderBudget, TeamAuthorizationError
from director_orchestrator.entitlement_contracts import document_digest
from director_orchestrator.primary_budget_producer import (
    BudgetProducerStatus,
    prepare_unsigned_budget_intent,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--action", choices=("prepare", "inspect"), default="prepare",
        help=(
            "prepare (default): build an unsigned intent. "
            "inspect: check an already-signed document's shape."
        ),
    )
    prepare = parser.add_argument_group("prepare")
    prepare.add_argument("--grant-digest")
    prepare.add_argument("--provider-id")
    prepare.add_argument("--project-id")
    prepare.add_argument("--task-id")
    prepare.add_argument("--day", help="UTC calendar day, YYYY-MM-DD")
    prepare.add_argument("--task-limit-microusd", type=int)
    prepare.add_argument("--daily-limit-microusd", type=int)
    prepare.add_argument("--attempt-reservation-microusd", type=int)
    prepare.add_argument("--token-limit", type=int)
    inspect_group = parser.add_argument_group("inspect")
    inspect_group.add_argument(
        "--input", help="path to a JSON document, or '-' for stdin"
    )
    return parser


PREPARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("--grant-digest", "grant_digest"),
    ("--provider-id", "provider_id"),
    ("--project-id", "project_id"),
    ("--task-id", "task_id"),
    ("--day", "day"),
    ("--task-limit-microusd", "task_limit_microusd"),
    ("--daily-limit-microusd", "daily_limit_microusd"),
    ("--attempt-reservation-microusd", "attempt_reservation_microusd"),
    ("--token-limit", "token_limit"),
)


def _run_prepare(args: argparse.Namespace) -> dict[str, Any]:
    missing = [
        flag for flag, attribute in PREPARE_FIELDS
        if getattr(args, attribute) is None
    ]
    if missing:
        raise ValueError(
            "missing required prepare arguments: " + ", ".join(missing)
        )
    intent = prepare_unsigned_budget_intent(
        **{attribute: getattr(args, attribute) for _, attribute in PREPARE_FIELDS}
    )
    return {
        "status": BudgetProducerStatus.PREPARED.value,
        "document": intent.canonical_document(),
        "intent_digest": intent.digest,
        "note": (
            "UNSIGNED. This document is not a ProviderBudget and confers no "
            "authority; it has no owner_signature field at all."
        ),
    }


def _run_inspect(args: argparse.Namespace) -> dict[str, Any]:
    if not args.input:
        raise ValueError("inspect requires --input")
    raw = (
        sys.stdin.read()
        if args.input == "-"
        else Path(args.input).read_text(encoding="utf-8")
    )
    document = json.loads(raw)
    budget = ProviderBudget.model_validate(document)
    return {
        "status": "STRUCTURALLY_VALID_UNVERIFIED",
        "schema_version": budget.schema_version,
        "provider_id": budget.provider_id,
        "project_id": budget.project_id,
        "task_id": budget.task_id,
        "day": budget.day,
        "signing_document_digest": document_digest(budget.signing_document()),
        "note": (
            "Structural shape only: no signature was verified, no secret "
            "was read, no database was touched, and nothing was registered."
        ),
    }


def _safe_error_message(exc: Exception) -> str:
    """A safe, categorical summary. Never the raw exception text.

    ``ValidationError.__str__`` includes each offending field's actual input
    value by default; a malformed or forged signed document is exactly the
    kind of input this CLI must never echo back. Only ``ValidationError`` is
    reported this way: the error type and, for a known model field, its
    field location, or the generic ``<extra_field>`` location for an unknown
    top-level property -- never the offending value either way. Every other
    caught exception (``ValueError``, ``TeamAuthorizationError``,
    ``OSError``, ``json.JSONDecodeError``) is returned as its own
    ``str(exc)``, unredacted: an ``OSError`` reading ``--input`` can include
    the caller-supplied path.
    """
    if isinstance(exc, ValidationError):
        details = []
        for error in exc.errors(
            include_url=False, include_context=False, include_input=False
        ):
            error_type = error.get("type", "invalid")
            if error_type == "extra_forbidden":
                # Pydantic's own loc for this error IS the unknown key the
                # caller supplied, so joining loc here would echo exactly
                # the untrusted input this function exists to withhold.
                location = "<extra_field>"
            else:
                location = ".".join(str(part) for part in error.get("loc", ())) or "<document>"
            details.append(f"{location}: {error_type}")
        return "invalid document structure (" + "; ".join(details) + ")"
    return str(exc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = (
            _run_prepare(args) if args.action == "prepare" else _run_inspect(args)
        )
    except (
        ValueError,
        TeamAuthorizationError,
        ValidationError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {_safe_error_message(exc)}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 19:08 UTC | END]]
