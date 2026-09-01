# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 08:44 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 08:14 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | BEGIN]]
from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from director_orchestrator.packet_contracts import (
    build_replay_run,
    canonical_json_bytes,
    document_digest,
    load_json_document,
    validate_packet,
    verify_replay_run,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs" / "schemas"
LEDGER_SCHEMA = SCHEMAS / "director-gate-ledger-v1.schema.json"
LEDGER_EXAMPLE = SCHEMAS / "director-gate-ledger-v1.example.json"
RESULT_SCHEMA = SCHEMAS / "director-gate-result-v1.schema.json"
RESULT_EXAMPLE = SCHEMAS / "director-gate-result-v1.example.json"


def domain_digest(domain: str, value: Any) -> str:
    payload = domain.encode("utf-8") + b"\0" + canonical_json_bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def object_without(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def expected_output_matches(expected: dict[str, Any], decisive_output: str) -> bool:
    matcher = expected["matcher"]
    value = expected["value"]
    if matcher == "NONE":
        return value is None
    if not expected["case_sensitive"]:
        decisive_output = decisive_output.casefold()
        value = value.casefold()
    if matcher == "EXACT":
        return decisive_output == value
    if matcher == "CONTAINS":
        return value in decisive_output
    raise AssertionError(f"unsupported matcher: {matcher}")


def sanitized_manual_evidence_value(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "sanitized_claim": evidence["sanitized_claim"],
        "sanitized_location": evidence["sanitized_location"],
    }


def sanitization_record_value(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        field: evidence[field]
        for field in (
            "sanitized_text_digest",
            "sanitization_source",
            "redaction_status",
            "redaction_policy_id",
            "redaction_policy_digest",
            "redaction_count",
            "redaction_categories",
            "secret_scan_status",
            "secret_scanner_id",
            "secret_scanner_digest",
            "secret_finding_count",
            "secret_finding_categories",
        )
    }


def manual_artifact_binding_value(result: dict[str, Any]) -> dict[str, Any]:
    actor = result["evidence_actor"]
    worktree = result["worktree_evidence"]
    return {
        "repository": result["repository"],
        "gate_id": result["gate_id"],
        "gate_definition_digest": result["gate_definition_digest"],
        "expected_head_sha": result["expected_head_sha"],
        "executed_sha": result["executed_sha"],
        "worktree_before_head_sha": worktree["before"]["head_sha"],
        "worktree_after_head_sha": worktree["after"]["head_sha"],
        "authenticated_principal": actor["authenticated_principal"],
        "provider_run_reference": actor["provider_run_reference"],
    }


def semantic_binding_errors(
    ledger: dict[str, Any],
    result: dict[str, Any],
    *,
    authenticated_principal_roles: dict[str, set[str]] | None = None,
    authenticated_sanitization_record_digests: set[str] | None = None,
    authenticated_artifact_binding_digests: set[str] | None = None,
) -> list[str]:
    """Reference PR-1 assertions; production binding is deliberately deferred."""

    errors: list[str] = []
    equal_fields = (
        "task_id",
        "assignment_id",
        "work_packet_digest",
        "authority_snapshot_digest",
        "repository",
        "ledger_series_id",
        "ledger_id",
        "ledger_revision",
        "ledger_digest",
        "ledger_state",
        "expected_head_sha",
        "logical_producer",
        "material_authors",
    )
    for field in equal_fields:
        if result.get(field) != ledger.get(field):
            errors.append(f"{field} mismatch")

    if result.get("executed_sha") != ledger.get("expected_head_sha"):
        errors.append("executed_sha mismatch")

    gates = {
        gate["gate_id"]: gate
        for gate in ledger.get("director_gates", []) + ledger.get("producer_added_gates", [])
    }
    gate = gates.get(result.get("gate_id"))
    if gate is None:
        errors.append("unknown gate_id")
        return errors
    for field in ("gate_type", "gate_definition_digest"):
        if result.get(field) != gate.get(field):
            errors.append(f"{field} mismatch")

    actor = result.get("evidence_actor", {})
    if result.get("evidence_role") == "INDEPENDENT_REVIEWER":
        authors = {
            item.get("authenticated_principal") for item in ledger.get("material_authors", [])
        }
        if actor.get("authenticated_principal") in authors:
            errors.append("independent actor is a material author")

    if gate.get("gate_type") == "RUNNABLE":
        runner = result.get("resolved_runner", {})
        for field in (
            "runner_id",
            "runner_definition_digest",
            "parameter_profile_id",
            "parameter_profile_digest",
        ):
            if runner.get(field) != gate.get(field):
                errors.append(f"resolved runner {field} mismatch")
        if runner.get("registry_digest") != ledger.get("runner_registry_digest"):
            errors.append("runner registry mismatch")
        worktree = result.get("worktree_evidence", {})
        if worktree.get("repository_relative_working_directory") != gate.get(
            "working_directory"
        ):
            errors.append("working directory mismatch")
        for phase in ("before", "after"):
            if worktree.get(phase, {}).get("head_sha") != ledger.get("expected_head_sha"):
                errors.append(f"worktree {phase} head mismatch")
        output = result.get("output_evidence", {})
        exit_code_matches = result.get("exit_code") == gate.get("expected_exit_code")
        if result.get("exit_code_matches_expected") != exit_code_matches:
            errors.append("exit code comparison mismatch")
        output_matches = expected_output_matches(
            gate.get("expected_output", {}), output.get("decisive_output", "")
        )
        if result.get("expected_output_match") != output_matches:
            errors.append("expected output comparison mismatch")
        if output.get("output_ceiling_bytes") != gate.get("output_limit_bytes"):
            errors.append("output ceiling mismatch")
        expected_excerpt_digest = domain_digest(
            "director-gate-sanitized-excerpt/v1",
            output.get("decisive_output", ""),
        )
        if output.get("sanitized_excerpt_digest") != expected_excerpt_digest:
            errors.append("sanitized excerpt digest mismatch")
        if output.get("stdout_bytes_captured", 0) + output.get(
            "stderr_bytes_captured", 0
        ) != output.get("total_output_bytes"):
            errors.append("captured byte accounting mismatch")
        if output.get("persisted_output_bytes", 0) + output.get(
            "omitted_output_bytes", 0
        ) != output.get("total_output_bytes"):
            errors.append("persisted byte accounting mismatch")
        if output.get("persisted_output_bytes", 0) > output.get(
            "output_ceiling_bytes", 0
        ):
            errors.append("persisted output exceeds ceiling")
        if bool(output.get("omitted_output_bytes")) != bool(output.get("truncated")):
            errors.append("truncation metadata mismatch")
        if result.get("status") == "PASS" and (
            output.get("secret_finding_count") != 0
            or output.get("secret_finding_categories")
        ):
            errors.append("PASS retains post-redaction secret findings")

    if gate.get("gate_type") == "MANUAL":
        evaluation = result.get("evaluation", {})
        evaluator_role = evaluation.get("eligible_evaluator_role")
        recorded_roles = set(actor.get("authenticated_roles", []))
        authenticated_roles = (authenticated_principal_roles or {}).get(
            actor.get("authenticated_principal"), set()
        )
        if recorded_roles != authenticated_roles:
            errors.append("authenticated evaluator roles mismatch")
        role_is_eligible = (
            evaluator_role in gate.get("eligible_evaluator_roles", [])
            and evaluator_role in authenticated_roles
        )
        if evaluation.get("evaluator_eligible") != role_is_eligible:
            errors.append("manual evaluator eligibility mismatch")
        if not role_is_eligible:
            errors.append("manual evaluator role is not eligible")

        authors = {
            item.get("authenticated_principal") for item in ledger.get("material_authors", [])
        }
        actor_is_independent = actor.get("authenticated_principal") not in authors
        if evaluation.get("evaluator_independent_of_material_authors") != actor_is_independent:
            errors.append("manual evaluator independence mismatch")
        if gate.get("independence_required") and not actor_is_independent:
            errors.append("manual gate requires an independent evaluator")

        expected_artifact_binding_digest = domain_digest(
            "director-gate-manual-artifact-binding/v1",
            manual_artifact_binding_value(result),
        )
        artifact_binding_digest = evaluation.get("artifact_binding_digest")
        if artifact_binding_digest != expected_artifact_binding_digest:
            errors.append("manual artifact binding digest mismatch")
        if artifact_binding_digest not in (
            authenticated_artifact_binding_digests or set()
        ):
            errors.append("manual artifact binding is not authenticated")

        for index, evidence in enumerate(result.get("manual_evidence", [])):
            expected_digest = domain_digest(
                "director-gate-sanitized-manual-evidence/v1",
                sanitized_manual_evidence_value(evidence),
            )
            if evidence.get("sanitized_text_digest") != expected_digest:
                errors.append(f"manual evidence {index} sanitized text digest mismatch")
            expected_record_digest = domain_digest(
                "director-gate-sanitization-record/v1",
                sanitization_record_value(evidence),
            )
            if evidence.get("sanitization_record_digest") != expected_record_digest:
                errors.append(f"manual evidence {index} sanitization record digest mismatch")
            if evidence.get("sanitization_record_digest") not in (
                authenticated_sanitization_record_digests or set()
            ):
                errors.append(f"manual evidence {index} sanitization record is not authenticated")
            if result.get("status") == "PASS" and (
                evidence.get("sanitization_source") != "DIRECTOR_AUTHENTICATED_PIPELINE"
                or evidence.get("raw_text_persisted") is not False
                or evidence.get("redaction_status") != "PASSED"
                or evidence.get("secret_scan_status") != "PASSED"
                or evidence.get("secret_finding_count") != 0
                or evidence.get("secret_finding_categories")
            ):
                errors.append(f"manual evidence {index} lacks safe authenticated persistence")

    if gate.get("gate_type") == "EXTERNAL_CI":
        binding = gate.get("ci_binding", {})
        observation = result.get("ci_observation", {})
        for field in (
            "repository",
            "workflow_path",
            "workflow_blob_sha",
            "workflow_definition_digest",
            "required_check_name",
            "trusted_app_id",
            "trusted_app_slug",
        ):
            observed_field = "check_name" if field == "required_check_name" else field
            if observation.get(observed_field) != binding.get(field):
                errors.append(f"CI {field} mismatch")
        if observation.get("observed_head_sha") != ledger.get("expected_head_sha"):
            errors.append("CI observed head mismatch")
    return errors


class CompletionGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger_schema = load_json_document(LEDGER_SCHEMA)
        cls.result_schema = load_json_document(RESULT_SCHEMA)
        cls.ledger_validator = Draft202012Validator(
            cls.ledger_schema,
            format_checker=FormatChecker(),
        )
        cls.result_validator = Draft202012Validator(
            cls.result_schema,
            format_checker=FormatChecker(),
        )

    def setUp(self) -> None:
        self.ledger = load_json_document(LEDGER_EXAMPLE)
        self.result = load_json_document(RESULT_EXAMPLE)

    def assertValid(self, validator: Draft202012Validator, document: Any) -> None:
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def assertInvalid(self, validator: Draft202012Validator, document: Any) -> None:
        self.assertTrue(list(validator.iter_errors(document)), "document unexpectedly validated")

    def manual_result(self) -> dict[str, Any]:
        result = copy.deepcopy(self.result)
        for field in (
            "resolved_runner",
            "environment_fingerprint",
            "exit_code",
            "exit_code_matches_expected",
            "expected_output_match",
            "output_evidence",
        ):
            result.pop(field)
        gate = self.ledger["director_gates"][1]
        manual_evidence = {
            "sanitized_claim": "The exact candidate was reviewed independently.",
            "sanitized_location": "https://example.invalid/review/1",
            "sanitized_text_digest": domain_digest(
                "director-gate-sanitized-manual-evidence/v1",
                {
                    "sanitized_claim": "The exact candidate was reviewed independently.",
                    "sanitized_location": "https://example.invalid/review/1",
                },
            ),
            "evidence_digest": "sha256:" + "3" * 64,
            "raw_text_persisted": False,
            "sanitization_source": "DIRECTOR_AUTHENTICATED_PIPELINE",
            "redaction_status": "PASSED",
            "redaction_policy_id": "director-output-redaction-v1",
            "redaction_policy_digest": "sha256:" + "1" * 64,
            "redaction_count": 0,
            "redaction_categories": [],
            "secret_scan_status": "PASSED",
            "secret_scanner_id": "director-secret-scanner-v1",
            "secret_scanner_digest": "sha256:" + "2" * 64,
            "secret_finding_count": 0,
            "secret_finding_categories": [],
        }
        manual_evidence["sanitization_record_digest"] = domain_digest(
            "director-gate-sanitization-record/v1",
            sanitization_record_value(manual_evidence),
        )
        result.update(
            {
                "gate_id": gate["gate_id"],
                "gate_definition_digest": gate["gate_definition_digest"],
                "gate_type": "MANUAL",
                "manual_evidence": [manual_evidence],
                "evaluation": {
                    "eligible_evaluator_role": "INDEPENDENT_PROVIDER_REVIEWER",
                    "evaluator_eligible": True,
                    "evaluator_independent_of_material_authors": True,
                    "exact_artifact_verified": True,
                    "artifact_binding_digest": "sha256:" + "4" * 64,
                },
            }
        )
        result["evaluation"]["artifact_binding_digest"] = domain_digest(
            "director-gate-manual-artifact-binding/v1",
            manual_artifact_binding_value(result),
        )
        return result

    def external_ci_result(self) -> dict[str, Any]:
        result = copy.deepcopy(self.result)
        for field in (
            "resolved_runner",
            "environment_fingerprint",
            "worktree_evidence",
            "exit_code",
            "exit_code_matches_expected",
            "expected_output_match",
            "output_evidence",
        ):
            result.pop(field)
        gate = self.ledger["director_gates"][2]
        result.update(
            {
                "gate_id": gate["gate_id"],
                "gate_definition_digest": gate["gate_definition_digest"],
                "gate_type": "EXTERNAL_CI",
                "evidence_actor": {
                    "identity_kind": "EXTERNAL_CI",
                    "authenticated_principal": "github-app-15368",
                    "logical_provider": "github-actions",
                    "system_label": "GitHub Actions",
                    "evidence_source": "GITHUB_API_READBACK",
                    "provider_run_reference": "github-check-run-123",
                    "authentication_record_digest": "sha256:" + "5" * 64,
                    "authenticated_roles": ["TRUSTED_EXTERNAL_CI"],
                },
                "evidence_role": "TRUSTED_EXTERNAL_CI",
                "reverification": {
                    "mode": "ORIGINAL",
                    "previous_result_digest": None,
                    "repository_verified": True,
                    "ledger_digest_verified": True,
                    "exact_sha_verified": True,
                    "material_author_exclusion_verified": True,
                },
                "ci_observation": {
                    "provider": "GITHUB",
                    "repository": self.ledger["repository"],
                    "workflow_path": ".github/workflows/ci.yml",
                    "workflow_blob_sha": "3" * 40,
                    "workflow_definition_digest": "sha256:" + "4" * 64,
                    "check_run_id": 123,
                    "workflow_run_id": 456,
                    "check_suite_id": 789,
                    "check_name": "Python 3.12 (ubuntu-latest)",
                    "observed_head_sha": self.ledger["expected_head_sha"],
                    "conclusion": "SUCCESS",
                    "url": "https://github.com/example/checks/123",
                    "completed_at": "2026-08-24T05:20:08Z",
                    "fetched_at": "2026-08-24T05:20:10Z",
                    "trusted_app_id": 15368,
                    "trusted_app_slug": "github-actions",
                    "trusted_app_verified": True,
                    "authenticated_readback": True,
                    "response_digest": "sha256:" + "6" * 64,
                    "observation_digest": "sha256:" + "7" * 64,
                },
            }
        )
        return result

    def test_schemas_are_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(self.ledger_schema)
        Draft202012Validator.check_schema(self.result_schema)

    def test_examples_validate(self):
        self.assertValid(self.ledger_validator, self.ledger)
        self.assertValid(self.result_validator, self.result)

    def test_every_required_ledger_field_fails_closed_when_missing(self):
        for field in self.ledger_schema["required"]:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.ledger)
                candidate.pop(field)
                self.assertInvalid(self.ledger_validator, candidate)

    def test_every_required_result_field_fails_closed_when_missing(self):
        required = self.result_schema["$defs"]["commonResult"]["required"]
        for field in required:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.result)
                candidate.pop(field)
                self.assertInvalid(self.result_validator, candidate)

    def test_unknown_top_level_and_nested_fields_fail_closed(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["merge_authorized"] = True
        self.assertInvalid(self.ledger_validator, ledger)

        result = copy.deepcopy(self.result)
        result["output_evidence"]["raw_secret_digest"] = "sha256:" + "a" * 64
        self.assertInvalid(self.result_validator, result)

    def test_planned_and_head_bound_lifecycle_is_immutable_by_shape(self):
        planned = copy.deepcopy(self.ledger)
        planned.update(
            {
                "ledger_id": "gate-ledger-DIR-COMPLETION-EXAMPLE-1-r1",
                "ledger_revision": 1,
                "predecessor_ledger_digest": None,
                "ledger_state": "PLANNED",
                "expected_head_sha": None,
                "candidate_result_digest": None,
                "material_authors": [],
            }
        )
        self.assertValid(self.ledger_validator, planned)

        planned["expected_head_sha"] = "2" * 40
        self.assertInvalid(self.ledger_validator, planned)

        head_bound = copy.deepcopy(self.ledger)
        head_bound["candidate_result_digest"] = None
        self.assertInvalid(self.ledger_validator, head_bound)

        post_hoc = copy.deepcopy(self.ledger)
        post_hoc.update(
            {
                "ledger_revision": 1,
                "predecessor_ledger_digest": None,
                "ledger_state": "HEAD_BOUND",
            }
        )
        self.assertInvalid(self.ledger_validator, post_hoc)

    def test_revision_one_has_no_predecessor_and_successors_require_one(self):
        revision_one = copy.deepcopy(self.ledger)
        revision_one["ledger_revision"] = 1
        revision_one["predecessor_ledger_digest"] = "sha256:" + "a" * 64
        self.assertInvalid(self.ledger_validator, revision_one)

        successor = copy.deepcopy(self.ledger)
        successor["predecessor_ledger_digest"] = None
        self.assertInvalid(self.ledger_validator, successor)

    def test_director_and_producer_gate_namespaces_and_authors_are_separate(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["director_gates"][0]["gate_author"].update(
            {
                "role": "PRODUCER",
                "authenticated_proposal_digest": "sha256:" + "a" * 64,
            }
        )
        self.assertInvalid(self.ledger_validator, ledger)

        producer_gate = copy.deepcopy(self.ledger["director_gates"][0])
        producer_gate["gate_id"] = "producer.claude.additional-contract-test"
        producer_gate["gate_author"].update(
            {
                "role": "PRODUCER",
                "provider_id": "claude",
                "authenticated_proposal_digest": "sha256:" + "b" * 64,
            }
        )
        ledger = copy.deepcopy(self.ledger)
        ledger["producer_added_gates"] = [producer_gate]
        self.assertValid(self.ledger_validator, ledger)

    def test_gate_variants_reject_raw_commands_and_wrong_evidence_kinds(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["director_gates"][0]["command"] = "python -m unittest"
        self.assertInvalid(self.ledger_validator, ledger)

        ledger = copy.deepcopy(self.ledger)
        ledger["director_gates"][0]["arguments"] = [
            "-c",
            "import os; os.system('arbitrary')",
        ]
        self.assertInvalid(self.ledger_validator, ledger)

        ledger = copy.deepcopy(self.ledger)
        ledger["director_gates"][1]["runner_id"] = "python_unittest"
        self.assertInvalid(self.ledger_validator, ledger)

        ledger = copy.deepcopy(self.ledger)
        ledger["director_gates"][2]["arguments"] = ["--unsafe"]
        self.assertInvalid(self.ledger_validator, ledger)

    def test_repository_owned_script_requires_exact_binding(self):
        ledger = copy.deepcopy(self.ledger)
        gate = ledger["director_gates"][0]
        gate["runner_id"] = "repository_owned_script"
        self.assertInvalid(self.ledger_validator, ledger)

        gate["script_binding"] = {
            "path": "scripts/check_completion.py",
            "blob_sha": "1" * 40,
            "content_digest": "sha256:" + "2" * 64,
            "bound_head_sha": ledger["expected_head_sha"],
            "approval_digest": "sha256:" + "3" * 64,
        }
        self.assertValid(self.ledger_validator, ledger)

        gate["runner_id"] = "python_unittest"
        self.assertInvalid(self.ledger_validator, ledger)

    def test_path_contract_rejects_absolute_parent_and_backslash_paths(self):
        for path in (
            "C:/repo",
            "/repo",
            "../repo",
            "docs/../private",
            "docs\\file",
            ".git/config",
            "docs/file.txt:stream",
            "CON",
            "docs/NUL.txt",
            "docs/trailing. ",
            "docs//file.txt",
            "docs/",
        ):
            with self.subTest(path=path):
                ledger = copy.deepcopy(self.ledger)
                ledger["path_scope"] = [path]
                self.assertInvalid(self.ledger_validator, ledger)

                result = copy.deepcopy(self.result)
                result["worktree_evidence"][
                    "repository_relative_working_directory"
                ] = path
                self.assertInvalid(self.result_validator, result)

        self.assertEqual(
            self.ledger_schema["$defs"]["repositoryRelativePath"],
            self.result_schema["$defs"]["repositoryRelativePath"],
        )

        ledger = copy.deepcopy(self.ledger)
        ledger["repository"] = "../outside"
        self.assertInvalid(self.ledger_validator, ledger)

    def test_at_least_one_director_gate_must_be_required(self):
        ledger = copy.deepcopy(self.ledger)
        for gate in ledger["director_gates"]:
            gate["requiredness"] = "ADVISORY"
        self.assertInvalid(self.ledger_validator, ledger)

    def test_manual_and_external_ci_results_are_discriminated(self):
        manual = self.manual_result()
        external_ci = self.external_ci_result()
        self.assertValid(self.result_validator, manual)
        self.assertValid(self.result_validator, external_ci)

        manual["resolved_runner"] = copy.deepcopy(self.result["resolved_runner"])
        self.assertInvalid(self.result_validator, manual)

        external_ci["exit_code"] = 0
        self.assertInvalid(self.result_validator, external_ci)

    def test_pass_requires_clean_state_and_safe_output(self):
        mutations = (
            ("exit_code_matches_expected", False),
            ("expected_output_match", False),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                result = copy.deepcopy(self.result)
                result[field] = value
                self.assertInvalid(self.result_validator, result)

        for field in ("redaction_status", "secret_scan_status"):
            with self.subTest(field=field):
                result = copy.deepcopy(self.result)
                result["output_evidence"][field] = "FAILED"
                self.assertInvalid(self.result_validator, result)

        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                result = copy.deepcopy(self.result)
                result["worktree_evidence"][phase]["clean"] = False
                self.assertInvalid(self.result_validator, result)

        result = copy.deepcopy(self.result)
        result["output_evidence"]["raw_output_persisted"] = True
        self.assertInvalid(self.result_validator, result)

        result = copy.deepcopy(self.result)
        result["output_evidence"]["secret_finding_count"] = 1
        result["output_evidence"]["secret_finding_categories"] = ["api-key"]
        self.assertInvalid(self.result_validator, result)

        result = copy.deepcopy(self.result)
        result["output_evidence"]["truncated"] = False
        result["output_evidence"]["omitted_output_bytes"] = 1
        self.assertInvalid(self.result_validator, result)

    def test_external_ci_pass_requires_success_and_authenticated_trusted_readback(self):
        for field, value in (
            ("conclusion", "SKIPPED"),
            ("trusted_app_verified", False),
            ("authenticated_readback", False),
        ):
            with self.subTest(field=field):
                result = self.external_ci_result()
                result["ci_observation"][field] = value
                self.assertInvalid(self.result_validator, result)

    def test_manual_pass_requires_eligible_independent_exact_artifact_review(self):
        for field in (
            "evaluator_eligible",
            "evaluator_independent_of_material_authors",
            "exact_artifact_verified",
        ):
            with self.subTest(field=field):
                result = self.manual_result()
                result["evaluation"][field] = False
                self.assertInvalid(self.result_validator, result)

        result = self.manual_result()
        result["evidence_role"] = "PRODUCER"
        self.assertInvalid(self.result_validator, result)

        result = copy.deepcopy(self.result)
        result["evidence_role"] = "OWNER"
        self.assertInvalid(self.result_validator, result)

    def test_manual_pass_requires_sanitized_scanned_evidence(self):
        for field, value in (
            ("redaction_status", "FAILED"),
            ("secret_scan_status", "FAILED"),
            ("raw_text_persisted", True),
            ("secret_finding_count", 1),
        ):
            with self.subTest(field=field):
                result = self.manual_result()
                result["manual_evidence"][0][field] = value
                self.assertInvalid(self.result_validator, result)

        result = self.manual_result()
        result["manual_evidence"][0]["secret_finding_categories"] = ["api-key"]
        self.assertInvalid(self.result_validator, result)

    def test_abandoned_requires_structured_fail_closed_evidence(self):
        result = copy.deepcopy(self.result)
        result["status"] = "ABANDONED"
        self.assertInvalid(self.result_validator, result)

        result["abandonment"] = {
            "reason_code": "TOOL_UNAVAILABLE",
            "actor": copy.deepcopy(result["evidence_actor"]),
            "recorded_at": "2026-08-24T05:21:00Z",
            "attempts": 3,
            "last_evidence_digest": "sha256:" + "8" * 64,
            "lost_coverage": ["The contract test could not be re-run."],
            "next_disposition": "BLOCKS_ACCEPTANCE",
            "abandonment_policy_digest": "sha256:" + "9" * 64,
            "policy_permitted": True,
        }
        self.assertValid(self.result_validator, result)

        result["status"] = "PASS"
        self.assertInvalid(self.result_validator, result)

    def test_independent_reverification_requires_prior_result_and_verified_bindings(self):
        result = copy.deepcopy(self.result)
        result["reverification"]["previous_result_digest"] = None
        self.assertInvalid(self.result_validator, result)

        result = copy.deepcopy(self.result)
        result["reverification"]["exact_sha_verified"] = False
        self.assertInvalid(self.result_validator, result)

    def test_dependency_evidence_can_reference_only_exact_pass_results(self):
        result = copy.deepcopy(self.result)
        result["dependency_results"] = [
            {
                "gate_id": "director.preflight",
                "status": "FAIL",
                "result_digest": "sha256:" + "a" * 64,
            }
        ]
        self.assertInvalid(self.result_validator, result)

    def test_checked_digest_scopes_match_exactly(self):
        for gate in self.ledger["director_gates"]:
            self.assertEqual(
                gate["gate_definition_digest"],
                domain_digest(
                    "director-gate-definition/v1",
                    object_without(gate, "gate_definition_digest"),
                ),
            )
        self.assertEqual(
            self.ledger["director_gate_set_digest"],
            domain_digest("director-gate-set/v1", self.ledger["director_gates"]),
        )
        self.assertEqual(
            self.ledger["producer_gate_set_digest"],
            domain_digest("producer-gate-set/v1", self.ledger["producer_added_gates"]),
        )
        self.assertEqual(
            self.ledger["ledger_digest"],
            domain_digest(
                "director-gate-ledger/v1",
                object_without(self.ledger, "ledger_digest"),
            ),
        )
        self.assertEqual(
            self.result["result_digest"],
            domain_digest(
                "director-gate-result/v1",
                object_without(self.result, "result_digest"),
            ),
        )

    def test_object_key_order_is_stable_but_gate_order_is_bound(self):
        reordered = dict(reversed(list(self.ledger.items())))
        self.assertEqual(
            domain_digest("director-gate-ledger/v1", object_without(self.ledger, "ledger_digest")),
            domain_digest("director-gate-ledger/v1", object_without(reordered, "ledger_digest")),
        )

        changed = copy.deepcopy(self.ledger)
        changed["director_gates"][0], changed["director_gates"][1] = (
            changed["director_gates"][1],
            changed["director_gates"][0],
        )
        self.assertNotEqual(
            self.ledger["director_gate_set_digest"],
            domain_digest("director-gate-set/v1", changed["director_gates"]),
        )

    def test_security_relevant_mutations_change_the_bound_digest(self):
        baseline = domain_digest(
            "director-gate-ledger/v1",
            object_without(self.ledger, "ledger_digest"),
        )
        mutations = []
        changed = copy.deepcopy(self.ledger)
        changed["expected_head_sha"] = "9" * 40
        mutations.append(changed)
        changed = copy.deepcopy(self.ledger)
        changed["director_gates"][0]["requiredness"] = "ADVISORY"
        mutations.append(changed)
        changed = copy.deepcopy(self.ledger)
        changed["director_gates"][0]["timeout_seconds"] += 1
        mutations.append(changed)
        changed = copy.deepcopy(self.ledger)
        changed["material_authors"][0]["run_reference"] = "different-run"
        mutations.append(changed)
        for candidate in mutations:
            self.assertNotEqual(
                baseline,
                domain_digest(
                    "director-gate-ledger/v1",
                    object_without(candidate, "ledger_digest"),
                ),
            )

        result_changed = copy.deepcopy(self.result)
        result_changed["output_evidence"]["redaction_count"] = 1
        self.assertNotEqual(
            self.result["result_digest"],
            domain_digest(
                "director-gate-result/v1",
                object_without(result_changed, "result_digest"),
            ),
        )

    def test_result_links_to_one_gate_on_the_same_current_head(self):
        gate = self.ledger["director_gates"][0]
        self.assertEqual(self.result["task_id"], self.ledger["task_id"])
        self.assertEqual(self.result["assignment_id"], self.ledger["assignment_id"])
        self.assertEqual(self.result["work_packet_digest"], self.ledger["work_packet_digest"])
        self.assertEqual(
            self.result["authority_snapshot_digest"],
            self.ledger["authority_snapshot_digest"],
        )
        self.assertEqual(self.result["ledger_digest"], self.ledger["ledger_digest"])
        self.assertEqual(self.result["gate_id"], gate["gate_id"])
        self.assertEqual(self.result["gate_definition_digest"], gate["gate_definition_digest"])
        self.assertEqual(self.result["expected_head_sha"], self.ledger["expected_head_sha"])
        self.assertEqual(self.result["executed_sha"], self.ledger["expected_head_sha"])
        self.assertEqual([], semantic_binding_errors(self.ledger, self.result))

    def test_semantic_binding_rejects_cross_document_substitution(self):
        mutations: list[dict[str, Any]] = []
        changed = copy.deepcopy(self.result)
        changed["executed_sha"] = "9" * 40
        mutations.append(changed)
        changed = copy.deepcopy(self.result)
        changed["worktree_evidence"]["before"]["head_sha"] = "9" * 40
        mutations.append(changed)
        changed = copy.deepcopy(self.result)
        changed["resolved_runner"]["runner_id"] = "pip_check"
        mutations.append(changed)
        changed = copy.deepcopy(self.result)
        changed["repository"] = "attacker/fork"
        mutations.append(changed)
        for candidate in mutations:
            self.assertTrue(semantic_binding_errors(self.ledger, candidate))

        external = self.external_ci_result()
        self.assertEqual([], semantic_binding_errors(self.ledger, external))
        external["ci_observation"]["observed_head_sha"] = "9" * 40
        self.assertIn("CI observed head mismatch", semantic_binding_errors(self.ledger, external))

    def test_semantic_binding_rejects_self_review_and_bad_output_accounting(self):
        self_review = copy.deepcopy(self.result)
        self_review["evidence_actor"]["authenticated_principal"] = self_review[
            "material_authors"
        ][0]["authenticated_principal"]
        self.assertIn(
            "independent actor is a material author",
            semantic_binding_errors(self.ledger, self_review),
        )

        contradictory = copy.deepcopy(self.result)
        contradictory["output_evidence"]["total_output_bytes"] = 999
        self.assertTrue(semantic_binding_errors(self.ledger, contradictory))

    def test_semantic_binding_derives_runnable_assertions_from_the_ledger(self):
        mutations: list[tuple[str, dict[str, Any]]] = []
        changed = copy.deepcopy(self.result)
        changed["exit_code"] = 1
        mutations.append(("exit code comparison mismatch", changed))
        changed = copy.deepcopy(self.result)
        changed["output_evidence"]["decisive_output"] = "not the expected output"
        changed["output_evidence"]["sanitized_excerpt_digest"] = domain_digest(
            "director-gate-sanitized-excerpt/v1",
            changed["output_evidence"]["decisive_output"],
        )
        mutations.append(("expected output comparison mismatch", changed))
        changed = copy.deepcopy(self.result)
        changed["output_evidence"]["output_ceiling_bytes"] = 1024
        mutations.append(("output ceiling mismatch", changed))
        changed = copy.deepcopy(self.result)
        changed["output_evidence"]["sanitized_excerpt_digest"] = "sha256:" + "0" * 64
        mutations.append(("sanitized excerpt digest mismatch", changed))

        for expected_error, candidate in mutations:
            with self.subTest(expected_error=expected_error):
                self.assertIn(expected_error, semantic_binding_errors(self.ledger, candidate))

    def test_semantic_binding_enforces_authenticated_manual_evaluator_role(self):
        result = self.manual_result()
        roles = {
            result["evidence_actor"]["authenticated_principal"]: set(
                result["evidence_actor"]["authenticated_roles"]
            )
        }
        records = {result["manual_evidence"][0]["sanitization_record_digest"]}
        artifact_bindings = {result["evaluation"]["artifact_binding_digest"]}
        self.assertEqual(
            [],
            semantic_binding_errors(
                self.ledger,
                result,
                authenticated_principal_roles=roles,
                authenticated_sanitization_record_digests=records,
                authenticated_artifact_binding_digests=artifact_bindings,
            ),
        )

        untrusted_errors = semantic_binding_errors(self.ledger, result)
        self.assertIn("authenticated evaluator roles mismatch", untrusted_errors)
        self.assertIn(
            "manual evidence 0 sanitization record is not authenticated",
            untrusted_errors,
        )
        self.assertIn("manual artifact binding is not authenticated", untrusted_errors)

        restricted = copy.deepcopy(self.ledger)
        restricted["director_gates"][1]["eligible_evaluator_roles"] = ["OWNER"]
        errors = semantic_binding_errors(
            restricted,
            result,
            authenticated_principal_roles=roles,
            authenticated_sanitization_record_digests=records,
            authenticated_artifact_binding_digests=artifact_bindings,
        )
        self.assertIn("manual evaluator role is not eligible", errors)

        unauthenticated_role = self.manual_result()
        unauthenticated_role["evidence_actor"]["authenticated_roles"] = ["CODEX_SECURITY"]
        codex_security_only = {
            unauthenticated_role["evidence_actor"]["authenticated_principal"]: {
                "CODEX_SECURITY"
            }
        }
        errors = semantic_binding_errors(
            self.ledger,
            unauthenticated_role,
            authenticated_principal_roles=codex_security_only,
            authenticated_sanitization_record_digests=records,
            authenticated_artifact_binding_digests=artifact_bindings,
        )
        self.assertIn("manual evaluator role is not eligible", errors)

        tampered_evidence = self.manual_result()
        tampered_evidence["manual_evidence"][0]["sanitized_claim"] = "different"
        errors = semantic_binding_errors(
            self.ledger,
            tampered_evidence,
            authenticated_principal_roles=roles,
            authenticated_sanitization_record_digests=records,
            authenticated_artifact_binding_digests=artifact_bindings,
        )
        self.assertIn("manual evidence 0 sanitized text digest mismatch", errors)

        tampered_binding = self.manual_result()
        tampered_binding["evaluation"]["artifact_binding_digest"] = "sha256:" + "0" * 64
        errors = semantic_binding_errors(
            self.ledger,
            tampered_binding,
            authenticated_principal_roles=roles,
            authenticated_sanitization_record_digests=records,
            authenticated_artifact_binding_digests=artifact_bindings,
        )
        self.assertIn("manual artifact binding digest mismatch", errors)

    def test_example_gate_graph_is_deterministic_and_non_weakening(self):
        director = self.ledger["director_gates"]
        gate_ids = [gate["gate_id"] for gate in director]
        orders = [gate["order"] for gate in director]
        self.assertEqual(len(gate_ids), len(set(gate_ids)))
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))
        known = set(gate_ids)
        for gate in director:
            self.assertTrue(gate["gate_id"].startswith("director."))
            for dependency in gate["dependencies"]:
                self.assertIn(dependency["gate_id"], known)
                self.assertEqual(dependency["required_status"], "PASS")
                self.assertTrue(dependency["gate_id"].startswith("director."))

    def test_example_reviewer_is_not_a_material_author(self):
        reviewer = self.result["evidence_actor"]["authenticated_principal"]
        authors = {
            author["authenticated_principal"] for author in self.result["material_authors"]
        }
        self.assertNotIn(reviewer, authors)

    def test_sanitized_output_framing_digest_is_unambiguous(self):
        stdout = b"OK"
        stderr = b""
        framed = (
            b"stdout\0"
            + len(stdout).to_bytes(8, "big")
            + stdout
            + b"stderr\0"
            + len(stderr).to_bytes(8, "big")
            + stderr
        )
        expected = "sha256:" + hashlib.sha256(
            b"director-gate-sanitized-output/v1\0" + framed
        ).hexdigest()
        output = self.result["output_evidence"]
        self.assertEqual(
            output["sanitized_excerpt_digest"],
            domain_digest("director-gate-sanitized-excerpt/v1", output["decisive_output"]),
        )
        self.assertEqual(output["full_output_digest"], expected)
        self.assertEqual(output["total_output_bytes"], 2)
        self.assertEqual(output["persisted_output_bytes"] + output["omitted_output_bytes"], 2)

    def test_json_contracts_have_no_contribution_tags(self):
        for path in (LEDGER_SCHEMA, LEDGER_EXAMPLE, RESULT_SCHEMA, RESULT_EXAMPLE):
            with self.subTest(path=path.name):
                self.assertNotIn("[[", path.read_text(encoding="utf-8"))

    def test_packet_contract_fingerprints_are_explicitly_bound(self):
        # Pin the exact work-packet authority and assignment-specific retry
        # budget contract so later drift cannot pass silently.
        expected = {
            "model-work-packet-v1.schema.json": "sha256:8eaa923240bf2b2b57e2ad7081045c50d6abc5a334680e2b250b3b4add0f998d",
            "model-work-packet-v1.example.json": "sha256:de50d90969eda8eae71807479f1fa3cac80254dfdf2656c79474206aea0f663e",
            "model-result-packet-v1.schema.json": "sha256:8fec656c0fea5efcc436fe6385416dd521b3dcc2d5e627608ad7198b95961cdb",
            "model-result-packet-v1.example.json": "sha256:8a761ded8fbbd7033fbd2fba90c0e48c311ccae26a063629e1a1c6cfcc43d3a6",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(document_digest(load_json_document(SCHEMAS / filename)), digest)

        work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        validate_packet("work", work)
        validate_packet("result", result)
        replay = build_replay_run(work, result)
        self.assertEqual(verify_replay_run(replay).document, replay)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 08:14 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 08:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
