# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | BEGIN]]
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import rfc8785
from jsonschema import Draft202012Validator


JsonObject = dict[str, Any]
_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "docs" / "schemas"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_RELATIVE_PATH = re.compile(r"^(?:\.|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*)$")
_SECRET_PATTERNS = (
    ("OPENAI_STYLE_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b")),
    ("BEARER_TOKEN", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}")),
)
_MAX_DOCUMENT_BYTES = 1_048_576
#: Windows FILE_ATTRIBUTE_REPARSE_POINT. Junctions and mount points set this
#: without being symbolic links, so `S_ISLNK` alone does not detect them.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class CompletionGateError(ValueError):
    """Completion-gate evidence failed closed validation."""


@dataclass(frozen=True)
class AuthenticatedGateContext:
    issuance_record_digests: frozenset[str] = frozenset()
    authentication_record_digests: frozenset[str] = frozenset()
    principal_roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    sanitization_record_digests: frozenset[str] = frozenset()
    artifact_binding_digests: frozenset[str] = frozenset()
    output_stream_digests: frozenset[str] = frozenset()
    external_ci_response_digests: frozenset[str] = frozenset()
    validation_time: datetime | None = None
    maximum_result_age_seconds: int = 86_400


@dataclass(frozen=True)
class VerifiedGateLedger:
    document: JsonObject
    digest: str
    runner_registry: "RunnerRegistry | None" = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedGateResult:
    document: JsonObject
    digest: str


@dataclass(frozen=True)
class ParameterProfile:
    profile_id: str
    argv: tuple[str, ...]
    digest: str

    @classmethod
    def build(cls, profile_id: str, argv: Sequence[str]) -> "ParameterProfile":
        if not profile_id or any("\x00" in part for part in argv):
            raise CompletionGateError("invalid parameter profile")
        frozen_argv = tuple(argv)
        return cls(
            profile_id=profile_id,
            argv=frozen_argv,
            digest=domain_digest(
                "director-runner-parameter-profile/v1",
                {"profile_id": profile_id, "argv": list(frozen_argv)},
            ),
        )


@dataclass(frozen=True)
class RunnerDefinition:
    runner_id: str
    executable: str
    profiles: Mapping[str, ParameterProfile]
    maximum_timeout_seconds: int = 900
    implementation_digest: str = ""
    executable_fingerprint: str = ""
    executable_version: str = ""
    environment_profile_digest: str = ""
    definition_digest: str = ""

    @classmethod
    def build(
        cls,
        runner_id: str,
        executable: str | os.PathLike[str],
        profiles: Sequence[ParameterProfile],
        *,
        maximum_timeout_seconds: int = 900,
        executable_version: str | None = None,
    ) -> "RunnerDefinition":
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            raise CompletionGateError("runner executable must be absolute")
        if maximum_timeout_seconds < 1:
            raise CompletionGateError("runner timeout must be positive")
        mapped = {item.profile_id: item for item in profiles}
        if len(mapped) != len(profiles) or not mapped:
            raise CompletionGateError("runner profiles must be non-empty and unique")
        try:
            executable_bytes = executable_path.read_bytes()
        except OSError as exc:
            raise CompletionGateError("runner executable cannot be fingerprinted") from exc
        fingerprint = "sha256:" + hashlib.sha256(executable_bytes).hexdigest()
        implementation_digest = domain_digest(
            "director-runner-implementation/v1",
            {"implementation": "completion-gates-subprocess-v1", "shell": False},
        )
        environment_profile_digest = domain_digest(
            "director-runner-environment-profile/v1",
            {"inherited_names": ["COMSPEC", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"]},
        )
        version = executable_version or executable_path.name
        value = {
            "runner_id": runner_id,
            "executable": str(executable_path),
            "profiles": {key: mapped[key].digest for key in sorted(mapped)},
            "maximum_timeout_seconds": maximum_timeout_seconds,
            "implementation_digest": implementation_digest,
            "executable_fingerprint": fingerprint,
            "executable_version": version,
            "environment_profile_digest": environment_profile_digest,
        }
        return cls(
            runner_id=runner_id,
            executable=str(executable_path),
            profiles=mapped,
            maximum_timeout_seconds=maximum_timeout_seconds,
            implementation_digest=implementation_digest,
            executable_fingerprint=fingerprint,
            executable_version=version,
            environment_profile_digest=environment_profile_digest,
            definition_digest=domain_digest("director-runner-definition/v1", value),
        )


@dataclass(frozen=True)
class RunnerObservation:
    gate_id: str
    status: str
    exit_code: int | None
    timed_out: bool
    output_limit_exceeded: bool
    decisive_output: str
    output_evidence: JsonObject
    argv_digest: str
    runner_definition_digest: str
    parameter_profile_digest: str
    before_head_sha: str
    after_head_sha: str
    failure_reasons: tuple[str, ...]


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise CompletionGateError("value cannot be canonicalized") from exc


def domain_digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json_bytes(value)
    ).hexdigest()


def _without(value: Mapping[str, Any], field_name: str) -> JsonObject:
    return {key: copy.deepcopy(item) for key, item in value.items() if key != field_name}


@lru_cache(maxsize=2)
def _validator(kind: str) -> Draft202012Validator:
    filename = {
        "ledger": "director-gate-ledger-v1.schema.json",
        "result": "director-gate-result-v1.schema.json",
    }[kind]
    with (_SCHEMA_ROOT / filename).open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_schema(kind: str, document: JsonObject) -> None:
    if len(canonical_json_bytes(document)) > _MAX_DOCUMENT_BYTES:
        raise CompletionGateError(f"{kind} exceeds the {_MAX_DOCUMENT_BYTES}-byte limit")
    errors = sorted(_validator(kind).iter_errors(document), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise CompletionGateError(f"{kind} schema failure at {location}: {error.message}")


def _require_digest(actual: Any, expected: str, label: str) -> None:
    if not isinstance(actual, str) or not _SHA256.fullmatch(actual):
        raise CompletionGateError(f"{label} is malformed")
    if not hmac.compare_digest(actual, expected):
        raise CompletionGateError(f"{label} mismatch")


def _gate_map(ledger: Mapping[str, Any]) -> dict[str, JsonObject]:
    return {
        gate["gate_id"]: gate
        for gate in ledger.get("director_gates", []) + ledger.get("producer_added_gates", [])
    }


def _validate_gate_graph(ledger: JsonObject) -> None:
    gates = ledger["director_gates"] + ledger["producer_added_gates"]
    identifiers = [gate["gate_id"] for gate in gates]
    orders = [gate["order"] for gate in gates]
    if len(identifiers) != len(set(identifiers)):
        raise CompletionGateError("gate identifiers must be unique")
    if len(orders) != len(set(orders)) or orders != sorted(orders):
        raise CompletionGateError("gate order must be unique and increasing")
    if not any(gate["requiredness"] == "REQUIRED" for gate in ledger["director_gates"]):
        raise CompletionGateError("at least one required Director gate is required")

    known = set(identifiers)
    graph: dict[str, set[str]] = {}
    for gate in gates:
        dependencies = {item["gate_id"] for item in gate["dependencies"]}
        if not dependencies <= known or gate["gate_id"] in dependencies:
            raise CompletionGateError("gate dependency is missing or self-referential")
        graph[gate["gate_id"]] = dependencies
    remaining = {key: set(value) for key, value in graph.items()}
    while remaining:
        ready = {key for key, value in remaining.items() if not value}
        if not ready:
            raise CompletionGateError("gate dependency graph contains a cycle")
        remaining = {
            key: value - ready for key, value in remaining.items() if key not in ready
        }


def validate_gate_ledger(
    document: Mapping[str, Any],
    *,
    context: AuthenticatedGateContext,
    predecessor: Mapping[str, Any] | None = None,
    runner_registry: "RunnerRegistry | None" = None,
) -> VerifiedGateLedger:
    ledger = copy.deepcopy(dict(document))
    _validate_schema("ledger", ledger)
    _validate_gate_graph(ledger)

    for gate in ledger["director_gates"] + ledger["producer_added_gates"]:
        _require_digest(
            gate["gate_definition_digest"],
            domain_digest("director-gate-definition/v1", _without(gate, "gate_definition_digest")),
            f"gate definition digest for {gate['gate_id']}",
        )
    _require_digest(
        ledger["director_gate_set_digest"],
        domain_digest("director-gate-set/v1", ledger["director_gates"]),
        "Director gate-set digest",
    )
    _require_digest(
        ledger["producer_gate_set_digest"],
        domain_digest("producer-gate-set/v1", ledger["producer_added_gates"]),
        "producer gate-set digest",
    )
    expected_ledger_digest = domain_digest(
        "director-gate-ledger/v1", _without(ledger, "ledger_digest")
    )
    _require_digest(ledger["ledger_digest"], expected_ledger_digest, "ledger digest")

    issuance_digest = ledger["issued_by"]["issuance_record_digest"]
    if issuance_digest not in context.issuance_record_digests:
        raise CompletionGateError("Director issuance record is not authenticated")

    if ledger["ledger_state"] == "PLANNED":
        if ledger["ledger_revision"] != 1 or ledger["predecessor_ledger_digest"] is not None:
            raise CompletionGateError("PLANNED ledger must be the first revision")
        if ledger["expected_head_sha"] is not None or ledger["candidate_result_digest"] is not None:
            raise CompletionGateError("PLANNED ledger cannot bind a candidate head")
    else:
        if ledger["ledger_revision"] < 2 or not ledger["predecessor_ledger_digest"]:
            raise CompletionGateError("HEAD_BOUND ledger requires predecessor lineage")
        if not ledger["expected_head_sha"] or not ledger["candidate_result_digest"]:
            raise CompletionGateError("HEAD_BOUND ledger requires exact candidate bindings")
        if predecessor is None:
            raise CompletionGateError("HEAD_BOUND ledger requires authenticated predecessor readback")

    if predecessor is not None:
        if predecessor.get("ledger_series_id") != ledger["ledger_series_id"]:
            raise CompletionGateError("predecessor ledger series mismatch")
        if predecessor.get("ledger_revision") != ledger["ledger_revision"] - 1:
            raise CompletionGateError("ledger revision is not monotonic")
        if predecessor.get("ledger_digest") != ledger["predecessor_ledger_digest"]:
            raise CompletionGateError("predecessor ledger digest mismatch")
        if predecessor.get("director_gates") != ledger["director_gates"]:
            raise CompletionGateError("Director gates cannot be edited in a successor ledger")
        prior_producer = predecessor.get("producer_added_gates", [])
        if ledger["producer_added_gates"][: len(prior_producer)] != prior_producer:
            raise CompletionGateError("producer-added gates must be monotonic")

    if runner_registry is not None:
        runner_registry.assert_ledger_bindings(ledger)
    elif any(gate["gate_type"] == "RUNNABLE" for gate in _gate_map(ledger).values()):
        raise CompletionGateError("runnable ledger requires an authenticated runner registry")
    return VerifiedGateLedger(
        document=ledger,
        digest=expected_ledger_digest,
        runner_registry=runner_registry,
    )


def _expected_output_matches(expected: Mapping[str, Any], output: str) -> bool:
    matcher = expected["matcher"]
    expected_value = expected["value"]
    candidate = output
    if matcher == "NONE":
        return expected_value is None
    if not expected["case_sensitive"]:
        candidate = candidate.casefold()
        expected_value = expected_value.casefold()
    if matcher == "EXACT":
        return candidate == expected_value
    if matcher == "CONTAINS":
        return expected_value in candidate
    raise CompletionGateError("unsupported output matcher")


def _manual_binding_value(result: JsonObject) -> JsonObject:
    return {
        "repository": result["repository"],
        "gate_id": result["gate_id"],
        "gate_definition_digest": result["gate_definition_digest"],
        "expected_head_sha": result["expected_head_sha"],
        "executed_sha": result["executed_sha"],
        "worktree_before_head_sha": result["worktree_evidence"]["before"]["head_sha"],
        "worktree_after_head_sha": result["worktree_evidence"]["after"]["head_sha"],
        "authenticated_principal": result["evidence_actor"]["authenticated_principal"],
        "provider_run_reference": result["evidence_actor"]["provider_run_reference"],
    }


def validate_gate_result(
    verified_ledger: VerifiedGateLedger,
    document: Mapping[str, Any],
    *,
    context: AuthenticatedGateContext,
    dependency_results: Mapping[str, VerifiedGateResult] | None = None,
) -> VerifiedGateResult:
    result = copy.deepcopy(dict(document))
    _validate_schema("result", result)
    expected_result_digest = domain_digest(
        "director-gate-result/v1", _without(result, "result_digest")
    )
    _require_digest(result["result_digest"], expected_result_digest, "result digest")
    if context.validation_time is None or context.validation_time.tzinfo is None:
        raise CompletionGateError("authenticated validation time is required")
    if context.maximum_result_age_seconds < 1:
        raise CompletionGateError("result freshness limit must be positive")
    try:
        started_at = datetime.fromisoformat(result["started_at"].replace("Z", "+00:00"))
        completed_at = datetime.fromisoformat(result["completed_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CompletionGateError("result timestamps are invalid") from exc
    validation_time = context.validation_time.astimezone(timezone.utc)
    if started_at > completed_at or completed_at > validation_time:
        raise CompletionGateError("result timestamps are not monotonic")
    if (validation_time - completed_at).total_seconds() > context.maximum_result_age_seconds:
        raise CompletionGateError("result evidence is stale")
    ledger = verified_ledger.document
    for field_name in (
        "task_id", "assignment_id", "work_packet_digest", "authority_snapshot_digest",
        "repository", "ledger_series_id", "ledger_id", "ledger_revision", "ledger_digest",
        "ledger_state", "expected_head_sha", "logical_producer", "material_authors",
    ):
        if result.get(field_name) != ledger.get(field_name):
            raise CompletionGateError(f"result {field_name} does not match ledger")
    if ledger["ledger_state"] != "HEAD_BOUND" or result["executed_sha"] != ledger["expected_head_sha"]:
        raise CompletionGateError("result is not bound to the current HEAD_BOUND ledger")

    gate = _gate_map(ledger).get(result["gate_id"])
    if gate is None:
        raise CompletionGateError("result references an unknown gate")
    if result["gate_type"] != gate["gate_type"] or result["gate_definition_digest"] != gate["gate_definition_digest"]:
        raise CompletionGateError("result gate binding mismatch")

    actor = result["evidence_actor"]
    if actor["authentication_record_digest"] not in context.authentication_record_digests:
        raise CompletionGateError("evidence actor is not authenticated")
    authenticated_roles = set(context.principal_roles.get(actor["authenticated_principal"], frozenset()))
    if set(actor["authenticated_roles"]) != authenticated_roles:
        raise CompletionGateError("evidence actor roles do not match authenticated roles")
    author_principals = {item["authenticated_principal"] for item in ledger["material_authors"]}
    if result["evidence_role"] == "INDEPENDENT_REVIEWER" and actor["authenticated_principal"] in author_principals:
        raise CompletionGateError("independent reviewer is a material author")

    trusted_dependencies = dependency_results or {}
    declared = {item["gate_id"]: item["result_digest"] for item in result["dependency_results"]}
    expected_dependency_ids = {item["gate_id"] for item in gate["dependencies"]}
    if set(declared) != expected_dependency_ids or len(declared) != len(result["dependency_results"]):
        raise CompletionGateError("result dependency set does not exactly match the gate")
    for dependency in gate["dependencies"]:
        trusted = trusted_dependencies.get(dependency["gate_id"])
        if trusted is None or trusted.document["status"] != "PASS":
            raise CompletionGateError("required gate dependency lacks trusted PASS evidence")
        if declared.get(dependency["gate_id"]) != trusted.digest:
            raise CompletionGateError("dependency result digest mismatch")

    if gate["gate_type"] == "RUNNABLE":
        runner = result["resolved_runner"]
        for field_name in (
            "runner_id", "runner_definition_digest", "parameter_profile_id", "parameter_profile_digest"
        ):
            if runner[field_name] != gate[field_name]:
                raise CompletionGateError(f"resolved runner {field_name} mismatch")
        if runner["registry_digest"] != ledger["runner_registry_digest"]:
            raise CompletionGateError("runner registry mismatch")
        if verified_ledger.runner_registry is None:
            raise CompletionGateError("result validation requires the authenticated runner registry")
        definition = verified_ledger.runner_registry.definition(gate["runner_id"])
        profile = definition.profiles[gate["parameter_profile_id"]]
        expected_argv_digest = domain_digest(
            "director-runner-argv/v1", [definition.executable, *profile.argv]
        )
        runner_assertions = {
            "runner_implementation_digest": definition.implementation_digest,
            "executable_fingerprint": definition.executable_fingerprint,
            "executable_version": definition.executable_version,
            "argv_digest": expected_argv_digest,
            "sanitized_environment_profile_digest": definition.environment_profile_digest,
        }
        for field_name, expected_value in runner_assertions.items():
            if runner[field_name] != expected_value:
                raise CompletionGateError(f"resolved runner {field_name} mismatch")
        worktree = result["worktree_evidence"]
        if worktree["repository_relative_working_directory"] != gate["working_directory"]:
            raise CompletionGateError("runner working directory mismatch")
        if any(worktree[phase]["head_sha"] != ledger["expected_head_sha"] for phase in ("before", "after")):
            raise CompletionGateError("runner worktree head drifted")
        if result["status"] == "PASS" and any(
            not worktree[phase]["clean"] for phase in ("before", "after")
        ):
            raise CompletionGateError("runnable PASS requires a clean worktree")
        output = result["output_evidence"]
        if result["exit_code_matches_expected"] != (result["exit_code"] == gate["expected_exit_code"]):
            raise CompletionGateError("exit-code assertion mismatch")
        if result["expected_output_match"] != _expected_output_matches(gate["expected_output"], output["decisive_output"]):
            raise CompletionGateError("output matcher assertion mismatch")
        if output["output_ceiling_bytes"] != gate["output_limit_bytes"]:
            raise CompletionGateError("output ceiling mismatch")
        if output["stdout_bytes_captured"] + output["stderr_bytes_captured"] != output["total_output_bytes"]:
            raise CompletionGateError("captured output accounting mismatch")
        if output["persisted_output_bytes"] + output["omitted_output_bytes"] != output["total_output_bytes"]:
            raise CompletionGateError("persisted output accounting mismatch")
        if output["persisted_output_bytes"] > output["output_ceiling_bytes"]:
            raise CompletionGateError("persisted output exceeds ceiling")
        if bool(output["omitted_output_bytes"]) != output["truncated"]:
            raise CompletionGateError("output truncation assertion mismatch")
        _require_digest(
            output["sanitized_excerpt_digest"],
            domain_digest("director-gate-sanitized-excerpt/v1", output["decisive_output"]),
            "sanitized excerpt digest",
        )
        if output["full_output_digest"] not in context.output_stream_digests:
            raise CompletionGateError("complete sanitized output stream is not authenticated")
        if result["status"] == "PASS" and (
            output["redaction_status"] != "PASSED"
            or output["secret_scan_status"] != "PASSED"
            or output["secret_finding_count"] != 0
            or output["secret_finding_categories"]
        ):
            raise CompletionGateError("PASS result retains unsafe output")

    elif gate["gate_type"] == "MANUAL":
        evaluation = result["evaluation"]
        role = evaluation["eligible_evaluator_role"]
        eligible = role in gate["eligible_evaluator_roles"] and role in authenticated_roles
        if evaluation["evaluator_eligible"] != eligible or not eligible:
            raise CompletionGateError("manual evaluator is not eligible")
        independent = actor["authenticated_principal"] not in author_principals
        if evaluation["evaluator_independent_of_material_authors"] != independent:
            raise CompletionGateError("manual evaluator independence assertion mismatch")
        if gate["independence_required"] and not independent:
            raise CompletionGateError("manual gate requires an independent evaluator")
        expected_binding = domain_digest("director-gate-manual-artifact-binding/v1", _manual_binding_value(result))
        _require_digest(evaluation["artifact_binding_digest"], expected_binding, "manual artifact binding digest")
        if expected_binding not in context.artifact_binding_digests:
            raise CompletionGateError("manual artifact binding is not authenticated")
        for evidence in result["manual_evidence"]:
            expected_text = domain_digest(
                "director-gate-sanitized-manual-evidence/v1",
                {"sanitized_claim": evidence["sanitized_claim"], "sanitized_location": evidence["sanitized_location"]},
            )
            _require_digest(evidence["sanitized_text_digest"], expected_text, "manual sanitized text digest")
            record_fields = (
                "sanitized_text_digest", "sanitization_source", "redaction_status",
                "redaction_policy_id", "redaction_policy_digest", "redaction_count",
                "redaction_categories", "secret_scan_status", "secret_scanner_id",
                "secret_scanner_digest", "secret_finding_count", "secret_finding_categories",
            )
            expected_record = domain_digest(
                "director-gate-sanitization-record/v1",
                {field_name: evidence[field_name] for field_name in record_fields},
            )
            _require_digest(evidence["sanitization_record_digest"], expected_record, "manual sanitization record digest")
            if expected_record not in context.sanitization_record_digests:
                raise CompletionGateError("manual sanitization record is not authenticated")
            if result["status"] == "PASS" and (
                evidence["sanitization_source"] != "DIRECTOR_AUTHENTICATED_PIPELINE"
                or evidence["raw_text_persisted"] is not False
                or evidence["redaction_status"] != "PASSED"
                or evidence["secret_scan_status"] != "PASSED"
                or evidence["secret_finding_count"] != 0
                or evidence["secret_finding_categories"]
            ):
                raise CompletionGateError("manual PASS evidence is unsafe")

    else:
        binding = gate["ci_binding"]
        observation = result["ci_observation"]
        for field_name in (
            "repository", "workflow_path", "workflow_blob_sha", "workflow_definition_digest",
            "trusted_app_id", "trusted_app_slug",
        ):
            if observation[field_name] != binding[field_name]:
                raise CompletionGateError(f"external CI {field_name} mismatch")
        if observation["check_name"] != binding["required_check_name"]:
            raise CompletionGateError("external CI check name mismatch")
        if observation["observed_head_sha"] != ledger["expected_head_sha"]:
            raise CompletionGateError("external CI head mismatch")
        if observation["response_digest"] not in context.external_ci_response_digests:
            raise CompletionGateError("external CI readback is not authenticated")
        if result["status"] == "PASS" and (
            observation["conclusion"] != "SUCCESS"
            or not observation["authenticated_readback"]
            or not observation["trusted_app_verified"]
        ):
            raise CompletionGateError("external CI PASS is not trusted and successful")

    if result["status"] == "ABANDONED":
        policy = gate["abandonment_policy"]
        abandonment = result["abandonment"]
        if not policy["permitted"] or abandonment["reason_code"] not in policy["allowed_reason_codes"]:
            raise CompletionGateError("gate abandonment is not permitted")
        if not set(policy["required_gate_effects"]).issubset(abandonment["gate_effects"]):
            raise CompletionGateError("abandonment omits required effects")
    return VerifiedGateResult(document=result, digest=expected_result_digest)


#: Authorities a Completion Gate ledger can never observe for itself. Each is held
#: by a different actor, so none of them may be inferred from gate evidence.
PROJECT_COMPLETION_AUTHORITIES: tuple[str, ...] = (
    "independent_exact_sha_acceptance",
    "codex_security",
    "trusted_ci",
    "deterministic_merge_policy",
    "deployment_or_product_acceptance_when_applicable",
)


def aggregate_completion_eligibility(
    verified_ledger: VerifiedGateLedger,
    verified_results: Sequence[VerifiedGateResult],
) -> JsonObject:
    """Aggregate one assignment ledger's exact-head gate evidence.

    Two different questions are answered separately and must not be conflated.

    ``assignment_acceptance_authorized`` is an assignment-scoped claim: every
    REQUIRED gate in this ledger passed at this ledger's exact expected head, and
    at least one of those passes carries INDEPENDENT_REVIEWER evidence, which
    `validate_gate_result` has already proven came from a principal who is not a
    material author. That is the strongest statement one assignment ledger can
    make about itself.

    ``project_completion_authorized`` is always false here. Project completion
    spans multiple assignments and depends on authorities in
    `PROJECT_COMPLETION_AUTHORITIES` that live outside this store entirely. Use
    `aggregate_project_completion_eligibility` for that question; it fails closed
    unless every one of those authorities is supplied as authenticated evidence.
    """

    ledger = verified_ledger.document
    required = {
        gate["gate_id"]
        for gate in ledger["director_gates"] + ledger["producer_added_gates"]
        if gate["requiredness"] == "REQUIRED"
    }
    independently_reviewed = False
    if ledger["ledger_state"] != "HEAD_BOUND" or not required:
        evidence_complete = False
        reason = "ledger_not_eligible"
        passed_required: set[str] = set()
        missing = required
    else:
        bound = [
            result
            for result in verified_results
            if result.document["ledger_digest"] == verified_ledger.digest
            and result.document["executed_sha"] == ledger["expected_head_sha"]
            and result.document["status"] == "PASS"
        ]
        passed_required = {result.document["gate_id"] for result in bound}
        independently_reviewed = any(
            result.document["gate_id"] in required
            and result.document["evidence_role"] == "INDEPENDENT_REVIEWER"
            for result in bound
        )
        missing = required - passed_required
        evidence_complete = not missing
        reason = "all_required_gates_passed" if evidence_complete else "required_gate_evidence_missing"
    acceptance_authorized = evidence_complete and independently_reviewed
    if evidence_complete and not independently_reviewed:
        reason = "independent_reviewer_evidence_missing"
    return {
        "scope": "assignment",
        "assignment_id": ledger.get("assignment_id"),
        "ledger_digest": verified_ledger.digest,
        "eligible": evidence_complete,
        "gate_evidence_complete": evidence_complete,
        "independent_reviewer_evidence_present": independently_reviewed,
        "assignment_acceptance_authorized": acceptance_authorized,
        "project_completion_authorized": False,
        "passed": sorted(required & passed_required),
        "missing": sorted(missing),
        "reason": reason,
        "separate_required_authorities": list(PROJECT_COMPLETION_AUTHORITIES),
    }


def aggregate_project_completion_eligibility(
    assignment_statuses: Sequence[Mapping[str, Any]],
    *,
    required_assignment_ids: Sequence[str],
    authenticated_project_authorities: frozenset[str] = frozenset(),
) -> JsonObject:
    """Decide project completion across assignment ledgers, failing closed.

    Every declared assignment must have reached assignment acceptance on its own
    evidence, and every authority in `PROJECT_COMPLETION_AUTHORITIES` must be
    supplied as authenticated evidence by the caller. Gate evidence alone never
    satisfies any of them: they are held by an independent reviewer, Codex
    Security, trusted CI, the deterministic merge policy, and the owner.
    """

    declared = list(required_assignment_ids)
    if len(set(declared)) != len(declared):
        raise CompletionGateError("required assignment identifiers must be unique")
    if not declared:
        raise CompletionGateError("project completion requires declared assignments")
    accepted = {
        str(status.get("assignment_id"))
        for status in assignment_statuses
        if status.get("scope") == "assignment"
        and status.get("assignment_acceptance_authorized") is True
    }
    outstanding_assignments = sorted(set(declared) - accepted)
    unknown = sorted(
        {
            str(status.get("assignment_id"))
            for status in assignment_statuses
            if status.get("scope") == "assignment"
        }
        - set(declared)
    )
    outstanding_authorities = sorted(
        item
        for item in PROJECT_COMPLETION_AUTHORITIES
        if item not in authenticated_project_authorities
    )
    authorized = not outstanding_assignments and not outstanding_authorities and not unknown
    if unknown:
        reason = "undeclared_assignment_evidence_present"
    elif outstanding_assignments:
        reason = "assignment_acceptance_incomplete"
    elif outstanding_authorities:
        reason = "separate_project_authorities_outstanding"
    else:
        reason = "all_assignment_and_project_authorities_satisfied"
    return {
        "scope": "project",
        "project_completion_authorized": authorized,
        "accepted_assignments": sorted(accepted & set(declared)),
        "outstanding_assignments": outstanding_assignments,
        "undeclared_assignments": unknown,
        "outstanding_authorities": outstanding_authorities,
        "reason": reason,
    }


def completion_runtime_readiness(
    *,
    enabled: bool,
    isolated_proof_enabled: bool,
    store_configured: bool,
    runner_registry: "RunnerRegistry | None",
) -> JsonObject:
    """Small fail-closed readiness surface for a future API/config integration."""
    reasons: list[str] = []
    if not enabled:
        reasons.append("runtime_disabled")
    if not isolated_proof_enabled:
        reasons.append("isolated_proof_disabled")
    if not store_configured:
        reasons.append("evidence_store_unconfigured")
    if runner_registry is None:
        reasons.append("runner_registry_unconfigured")
    return {
        "enabled": enabled,
        "isolated_proof_enabled": isolated_proof_enabled,
        "store_configured": store_configured,
        "runner_registry_configured": runner_registry is not None,
        "ready_for_isolated_proof": not reasons,
        "completion_authority": False,
        "reasons": reasons,
    }


class RunnerRegistry:
    def __init__(self, definitions: Sequence[RunnerDefinition]):
        mapped = {item.runner_id: item for item in definitions}
        if len(mapped) != len(definitions) or not mapped:
            raise CompletionGateError("runner definitions must be non-empty and unique")
        self._definitions = mapped
        self.registry_digest = domain_digest(
            "director-runner-registry/v1",
            {
                "registry_id": "director-runner-registry",
                "registry_version": "1",
                "definitions": {key: mapped[key].definition_digest for key in sorted(mapped)},
            },
        )

    def definition(self, runner_id: str) -> RunnerDefinition:
        try:
            return self._definitions[runner_id]
        except KeyError as exc:
            raise CompletionGateError("unknown runner") from exc

    def assert_ledger_bindings(self, ledger: Mapping[str, Any]) -> None:
        if ledger["runner_registry_digest"] != self.registry_digest:
            raise CompletionGateError("ledger runner registry digest mismatch")
        for gate in _gate_map(ledger).values():
            if gate["gate_type"] != "RUNNABLE":
                continue
            definition = self._definitions.get(gate["runner_id"])
            if definition is None:
                raise CompletionGateError("ledger selects an unknown runner")
            if gate["runner_definition_digest"] != definition.definition_digest:
                raise CompletionGateError("ledger runner definition digest mismatch")
            profile = definition.profiles.get(gate["parameter_profile_id"])
            if profile is None or gate["parameter_profile_digest"] != profile.digest:
                raise CompletionGateError("ledger parameter profile binding mismatch")
            if gate["timeout_seconds"] > definition.maximum_timeout_seconds:
                raise CompletionGateError("ledger timeout exceeds runner maximum")

    def execute(
        self,
        verified_ledger: VerifiedGateLedger,
        gate_id: str,
        *,
        repository_root: str | os.PathLike[str],
        enabled: bool = False,
        isolated_proof_enabled: bool = False,
        authenticated_script_approval_digests: frozenset[str] = frozenset(),
    ) -> RunnerObservation:
        ledger = verified_ledger.document
        if not enabled or not isolated_proof_enabled:
            raise CompletionGateError("Completion Gate execution is disabled")
        if ledger["execution_profile"] != "ISOLATED_PROOF_LANE":
            raise CompletionGateError("only the isolated proof lane may execute")
        gate = _gate_map(ledger).get(gate_id)
        if gate is None or gate["gate_type"] != "RUNNABLE":
            raise CompletionGateError("gate is not a registered runnable gate")
        self.assert_ledger_bindings(ledger)
        definition = self._definitions[gate["runner_id"]]
        profile = definition.profiles[gate["parameter_profile_id"]]

        if not _SAFE_RELATIVE_PATH.fullmatch(gate["working_directory"]):
            raise CompletionGateError("runner working directory is not canonical")
        root = Path(repository_root).resolve(strict=True)
        workdir = self._contained_directory(root, gate["working_directory"])
        before_head = self._git_head(root)
        if before_head != ledger["expected_head_sha"]:
            raise CompletionGateError("repository head does not match the ledger")
        before_status = self._git_status(root)
        if before_status:
            raise CompletionGateError("runner requires a clean repository worktree")
        if gate["runner_id"] == "repository_owned_script":
            self._verify_repository_script(
                root,
                gate,
                expected_head_sha=ledger["expected_head_sha"],
                authenticated_script_approval_digests=authenticated_script_approval_digests,
            )

        argv = (definition.executable, *profile.argv)
        safe_environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
        }
        exit_code, timed_out, output_limit_exceeded, stdout_bytes, stderr_bytes = (
            self._run_bounded(
                argv,
                cwd=workdir,
                environment=safe_environment,
                timeout_seconds=gate["timeout_seconds"],
                output_limit_bytes=gate["output_limit_bytes"],
            )
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        after_head = self._git_head(root)
        after_status = self._git_status(root)

        stdout, stdout_categories = self._sanitize(stdout)
        stderr, stderr_categories = self._sanitize(stderr)
        categories = sorted(stdout_categories | stderr_categories)
        sanitized_stdout_bytes = stdout.encode("utf-8")
        sanitized_stderr_bytes = stderr.encode("utf-8")
        total = len(sanitized_stdout_bytes) + len(sanitized_stderr_bytes)
        ceiling = gate["output_limit_bytes"]
        persisted_stdout = sanitized_stdout_bytes[:ceiling]
        remaining = max(0, ceiling - len(persisted_stdout))
        persisted_stderr = sanitized_stderr_bytes[:remaining]
        persisted = len(persisted_stdout) + len(persisted_stderr)
        decisive_output = (persisted_stdout + persisted_stderr).decode("utf-8", errors="replace")
        framed = (
            b"stdout\0" + len(sanitized_stdout_bytes).to_bytes(8, "big") + sanitized_stdout_bytes
            + b"stderr\0" + len(sanitized_stderr_bytes).to_bytes(8, "big") + sanitized_stderr_bytes
        )
        full_digest = "sha256:" + hashlib.sha256(
            b"director-gate-sanitized-output/v1\0" + framed
        ).hexdigest()
        output_evidence: JsonObject = {
            "decisive_output": decisive_output,
            "sanitized_excerpt_digest": domain_digest("director-gate-sanitized-excerpt/v1", decisive_output),
            "full_output_digest": full_digest,
            "stdout_bytes_captured": len(sanitized_stdout_bytes),
            "stderr_bytes_captured": len(sanitized_stderr_bytes),
            "total_output_bytes": total,
            "persisted_output_bytes": persisted,
            "omitted_output_bytes": total - persisted,
            "output_ceiling_bytes": ceiling,
            "truncated": total > persisted,
            "redaction_status": "PASSED",
            "secret_scan_status": "PASSED" if not categories else "FAILED",
            "secret_finding_count": len(categories),
            "secret_finding_categories": categories,
        }
        reasons: list[str] = []
        if timed_out:
            reasons.append("timeout")
        if output_limit_exceeded:
            reasons.append("output_limit_exceeded")
        if exit_code != gate["expected_exit_code"]:
            reasons.append("unexpected_exit_code")
        if not _expected_output_matches(gate["expected_output"], decisive_output):
            reasons.append("expected_output_missing")
        if categories:
            reasons.append("secret_scan_failed")
        if before_head != after_head:
            reasons.append("repository_head_changed")
        if after_status:
            reasons.append("repository_worktree_changed")
        return RunnerObservation(
            gate_id=gate_id,
            status="PASS" if not reasons else "FAIL",
            exit_code=exit_code,
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            decisive_output=decisive_output,
            output_evidence=output_evidence,
            argv_digest=domain_digest("director-runner-argv/v1", list(argv)),
            runner_definition_digest=definition.definition_digest,
            parameter_profile_digest=profile.digest,
            before_head_sha=before_head,
            after_head_sha=after_head,
            failure_reasons=tuple(reasons),
        )

    @staticmethod
    def _reject_link(path: Path, label: str) -> os.stat_result:
        """Reject a symlink, junction, or any other reparse point at `path`.

        `Path.resolve()` follows links, so a containment check performed on the
        resolved path cannot see that a component was a link in the first place: a
        symlink inside the repository that points back inside it still passes,
        and on Windows a directory junction is not a symlink at all. Every
        component is inspected here with `lstat`, before resolution, so a redirected
        component is refused rather than silently traversed.
        """

        try:
            info = path.lstat()
        except OSError as exc:
            raise CompletionGateError(f"{label} cannot be authenticated") from exc
        if stat.S_ISLNK(info.st_mode):
            raise CompletionGateError(f"{label} must not be a symbolic link")
        # Windows junctions and other reparse points are not symlinks. The
        # attribute is absent on POSIX, where the symlink check above is complete.
        if getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise CompletionGateError(f"{label} must not be a reparse point")
        return info

    @staticmethod
    def _contained_directory(root: Path, relative: str) -> Path:
        """Resolve a repository-relative directory with no link escape anywhere."""

        RunnerRegistry._reject_link(root, "repository root")
        current = root
        parts = () if relative == "." else tuple(relative.split("/"))
        for part in parts:
            current = current / part
            RunnerRegistry._reject_link(current, "runner working directory")
        if not current.is_dir():
            raise CompletionGateError("runner working directory is not a directory")
        # Containment is asserted on the link-free path and again after resolution,
        # so neither spelling nor identity can leave the repository.
        resolved = current.resolve(strict=True)
        for candidate in (current, resolved):
            if candidate != root and root not in candidate.parents:
                raise CompletionGateError(
                    "runner working directory escapes the repository"
                )
        if resolved != root.resolve(strict=True) and resolved != current:
            raise CompletionGateError("runner working directory resolves elsewhere")
        return resolved

    @staticmethod
    def _contained_file(root: Path, relative: str) -> Path:
        """Resolve a repository-relative file with no link or hard-link escape.

        A hard link has no distinguishable path component, so it is caught by link
        count instead: a second name for the same inode could point at content
        outside the repository that Git never recorded.
        """

        if not _SAFE_RELATIVE_PATH.fullmatch(relative) or relative == ".":
            raise CompletionGateError("repository script path is not canonical")
        parts = tuple(relative.split("/"))
        directory = RunnerRegistry._contained_directory(root, "/".join(parts[:-1]) or ".")
        script = directory / parts[-1]
        info = RunnerRegistry._reject_link(script, "repository script")
        if not stat.S_ISREG(info.st_mode):
            raise CompletionGateError("repository script is not a regular file")
        if info.st_nlink != 1:
            raise CompletionGateError("repository script must not be a hard link")
        resolved = script.resolve(strict=True)
        if root not in resolved.parents or resolved != script:
            raise CompletionGateError("repository script escapes the repository")
        return resolved

    @staticmethod
    def _git_head(root: Path) -> str:
        completed = subprocess.run(
            ("git", "rev-parse", "HEAD"), cwd=root, shell=False, check=False,
            capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}\n?", completed.stdout):
            raise CompletionGateError("cannot authenticate repository head")
        return completed.stdout.strip()

    @staticmethod
    def _run_bounded(
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
        output_limit_bytes: int,
    ) -> tuple[int | None, bool, bool, bytes, bytes]:
        process = subprocess.Popen(
            tuple(argv),
            cwd=cwd,
            env=dict(environment),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        lock = threading.Lock()
        output_limit_exceeded = threading.Event()

        def drain(name: str, stream: Any) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    with lock:
                        captured = len(buffers["stdout"]) + len(buffers["stderr"])
                        remaining = max(0, output_limit_bytes - captured)
                        buffers[name].extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            output_limit_exceeded.set()
                            process.kill()
                            return
            finally:
                stream.close()

        threads = [
            threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            exit_code: int | None = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=5)
            exit_code = None
        for thread in threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in threads):
            process.kill()
            raise CompletionGateError("runner output drain did not terminate")
        return (
            exit_code,
            timed_out,
            output_limit_exceeded.is_set(),
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
        )

    @staticmethod
    def _git_status(root: Path) -> str:
        completed = subprocess.run(
            ("git", "status", "--porcelain=v2", "--untracked-files=all"),
            cwd=root, shell=False, check=False, capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0:
            raise CompletionGateError("cannot authenticate repository worktree status")
        return completed.stdout

    @staticmethod
    def _verify_repository_script(
        root: Path,
        gate: Mapping[str, Any],
        *,
        expected_head_sha: str,
        authenticated_script_approval_digests: frozenset[str],
    ) -> None:
        binding = gate.get("script_binding")
        if not isinstance(binding, dict):
            raise CompletionGateError("repository script lacks an exact binding")
        if binding["bound_head_sha"] != expected_head_sha:
            raise CompletionGateError("repository script head binding mismatch")
        if binding["approval_digest"] not in authenticated_script_approval_digests:
            raise CompletionGateError("repository script approval is not authenticated")
        script = RunnerRegistry._contained_file(root, binding["path"])
        blob = subprocess.run(
            ("git", "rev-parse", f"HEAD:{binding['path']}"),
            cwd=root, shell=False, check=False, capture_output=True, text=True, timeout=15,
        )
        if blob.returncode != 0 or blob.stdout.strip() != binding["blob_sha"]:
            raise CompletionGateError("repository script Git blob mismatch")
        content_digest = "sha256:" + hashlib.sha256(script.read_bytes()).hexdigest()
        if content_digest != binding["content_digest"]:
            raise CompletionGateError("repository script content digest mismatch")

    @staticmethod
    def _sanitize(value: str) -> tuple[str, set[str]]:
        categories: set[str] = set()
        sanitized = value
        for category, pattern in _SECRET_PATTERNS:
            sanitized, count = pattern.subn("[REDACTED]", sanitized)
            if count:
                categories.add(category)
        return sanitized, categories
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
