# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | BEGIN]]
"""Owner-run HTTP diagnostic and signed terminal adjudication; never opens a DB."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from urllib.parse import urlsplit

import httpx

from director_orchestrator.primary_commands import build_primary_ingress_proof
from director_orchestrator.primary_parallel import (
    CancelledPlanReconciliationRequest, CancelledPlanReconciliationResponse,
)


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
FIELDS = {"provider_id", "deployment_id", "dependencies", "write_surfaces", "retry_limit", "allocation_profile"}


def safe_diagnostic(raw: dict) -> dict:
    """Reject unexpected response shape instead of echoing untrusted values."""
    result = {}
    for key in ("projection_digest", "projection_plan_digest", "batch_plan_digest", "diagnostic_digest"):
        value = raw[key]
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise ValueError("invalid diagnostic digest")
        result[key] = value
    for key in ("terminal_cancelled", "kill_switch_engaged", "digests_differ", "sequence_sets_differ",
                "historical_digest_preimages_available", "adjudication_recorded", "terminal_binding_consistent"):
        if type(raw[key]) is not bool:
            raise ValueError("invalid diagnostic boolean")
        result[key] = raw[key]
    if type(raw["fencing_generation"]) is not int or raw["fencing_generation"] < 1:
        raise ValueError("invalid diagnostic generation")
    result["fencing_generation"] = raw["fencing_generation"]
    for key in ("pending_assignment_count", "active_or_ambiguous_assignment_count"):
        if type(raw[key]) is not int or raw[key] < 0:
            raise ValueError("invalid diagnostic count")
        result[key] = raw[key]
    result["surviving_assignment_comparison"] = []
    for item in raw["surviving_assignment_comparison"]:
        if type(item["sequence"]) is not int or item["sequence"] < 1:
            raise ValueError("invalid comparison sequence")
        safe = {"sequence": item["sequence"]}
        for key in ("different_fields", "unavailable_fields"):
            if not isinstance(item[key], list) or any(value not in FIELDS for value in item[key]):
                raise ValueError("invalid comparison fields")
            safe[key] = item[key]
        result["surviving_assignment_comparison"].append(safe)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--apply", action="store_true", help="Show diagnostic, then ask for owner adjudication before signing")
    args = parser.parse_args(argv)
    submitted = False
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", args.task_id):
            raise ValueError("invalid task identity")
        base = os.environ.get("DIRECTOR_PRIMARY_URL", "http://127.0.0.1:8080").rstrip("/")
        url = urlsplit(base)
        if (url.username or url.password or url.query or url.fragment or url.path
                or url.scheme not in {"http", "https"}
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("invalid service URL")
        bearer = os.environ["ORCHESTRATOR_SHARED_SECRET"]
        if not bearer:
            raise ValueError("missing authentication")
        prefix = f"/v1/primary/tasks/{args.task_id}/cancelled-plan"
        with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {bearer}"},
                          timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(prefix + "/diagnostic")
            response.raise_for_status()
            diagnostic = safe_diagnostic(response.json())
            print(json.dumps(diagnostic, indent=2, sort_keys=True))
            print("Field comparisons describe surviving records. Historical digest preimages are unavailable; the origin is not proven.")
            if diagnostic["terminal_binding_consistent"]:
                print("Terminal adjudication is already consistent; no command sent.")
                return 0
            if not args.apply:
                return 0
            if not all(diagnostic[key] for key in ("terminal_cancelled", "kill_switch_engaged", "digests_differ")):
                print("Task is outside reconciliation scope; no command sent.")
                return 1
            confirmation = input("To retain both historical digests and close only unstarted assignments, type the diagnostic_digest: ").strip()
            if confirmation != diagnostic["diagnostic_digest"]:
                print("No matching owner adjudication; no command sent.")
                return 1
            request = CancelledPlanReconciliationRequest(
                task_id=args.task_id, command_id="cancelled-plan-" + uuid.uuid4().hex,
                actor_id=os.environ["SLACK_OWNER_USER_ID"],
                expected_projection_digest=diagnostic["projection_digest"],
                expected_fencing_generation=diagnostic["fencing_generation"],
                expected_projection_plan_digest=diagnostic["projection_plan_digest"],
                expected_batch_plan_digest=diagnostic["batch_plan_digest"],
                expected_diagnostic_digest=diagnostic["diagnostic_digest"],
                adjudication="retain_both_digests_close_unstarted")
            path = prefix + "/reconcile"
            proof = build_primary_ingress_proof(os.environ["SLACK_GATEWAY_SECRET"], bearer, request,
                path=path, proof_field="gateway_proof", channel_id=os.environ["SLACK_DIRECTOR_CHANNEL_ID"])
            request = request.model_copy(update={"gateway_proof": proof})
            submitted = True
            response = client.post(path, json=request.model_dump(mode="json"))
            response.raise_for_status()
            result = CancelledPlanReconciliationResponse.model_validate(response.json())
            print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
            print("Wait for the next server reconciliation pass and inspect authenticated readiness.")
            return 0
    except (Exception, KeyboardInterrupt):
        print("Command outcome is unconfirmed; inspect the diagnostic before another action. No automatic retry."
              if submitted else "Diagnostic or signing could not complete; no command sent.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | END]]
