# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | BEGIN]]
from __future__ import annotations

import copy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from director_orchestrator.completion_gates import (
    AuthenticatedGateContext,
    CompletionGateError,
    ParameterProfile,
    RunnerDefinition,
    RunnerRegistry,
    aggregate_completion_eligibility,
    completion_runtime_readiness,
    domain_digest,
    validate_gate_ledger,
    validate_gate_result,
)
from director_orchestrator.completion_store import CompletionGateStore
from director_orchestrator.packet_contracts import load_json_document


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs" / "schemas"
LEDGER_EXAMPLE = SCHEMAS / "director-gate-ledger-v1.example.json"
RESULT_EXAMPLE = SCHEMAS / "director-gate-result-v1.example.json"


def without(value: dict, field_name: str) -> dict:
    return {key: copy.deepcopy(item) for key, item in value.items() if key != field_name}


def rebind_gate(gate: dict) -> None:
    gate["gate_definition_digest"] = domain_digest(
        "director-gate-definition/v1", without(gate, "gate_definition_digest")
    )


def rebind_ledger(ledger: dict) -> None:
    for gate in ledger["director_gates"] + ledger["producer_added_gates"]:
        rebind_gate(gate)
    ledger["director_gate_set_digest"] = domain_digest(
        "director-gate-set/v1", ledger["director_gates"]
    )
    ledger["producer_gate_set_digest"] = domain_digest(
        "producer-gate-set/v1", ledger["producer_added_gates"]
    )
    ledger["ledger_digest"] = domain_digest(
        "director-gate-ledger/v1", without(ledger, "ledger_digest")
    )


def rebind_result(result: dict) -> None:
    result["result_digest"] = domain_digest(
        "director-gate-result/v1", without(result, "result_digest")
    )


class CompletionGateRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = load_json_document(LEDGER_EXAMPLE)
        self.result = load_json_document(RESULT_EXAMPLE)
        self.profile = ParameterProfile.build(
            "completion-gate-contract-tests", ("-c", "print('OK')")
        )
        self.definition = RunnerDefinition.build(
            "python_unittest", sys.executable, (self.profile,), maximum_timeout_seconds=300
        )
        self.registry = RunnerRegistry((self.definition,))
        self._single_runnable_gate()
        self.context = self._context()

    def _single_runnable_gate(self) -> None:
        self.ledger["director_gates"] = [self.ledger["director_gates"][0]]
        self.ledger["producer_added_gates"] = []
        gate = self.ledger["director_gates"][0]
        gate["runner_definition_digest"] = self.definition.definition_digest
        gate["parameter_profile_digest"] = self.profile.digest
        gate["working_directory"] = "."
        gate["timeout_seconds"] = 30
        gate["output_limit_bytes"] = 4096
        self.ledger["runner_registry_digest"] = self.registry.registry_digest
        rebind_ledger(self.ledger)

        self.result["ledger_digest"] = self.ledger["ledger_digest"]
        self.result["gate_definition_digest"] = gate["gate_definition_digest"]
        self.result["resolved_runner"]["registry_digest"] = self.registry.registry_digest
        self.result["resolved_runner"]["runner_definition_digest"] = self.definition.definition_digest
        self.result["resolved_runner"]["parameter_profile_digest"] = self.profile.digest
        self.result["resolved_runner"]["runner_implementation_digest"] = self.definition.implementation_digest
        self.result["resolved_runner"]["executable_fingerprint"] = self.definition.executable_fingerprint
        self.result["resolved_runner"]["executable_version"] = self.definition.executable_version
        self.result["resolved_runner"]["argv_digest"] = domain_digest(
            "director-runner-argv/v1",
            [self.definition.executable, *self.profile.argv],
        )
        self.result["resolved_runner"]["sanitized_environment_profile_digest"] = (
            self.definition.environment_profile_digest
        )
        self.result["output_evidence"]["output_ceiling_bytes"] = gate["output_limit_bytes"]
        rebind_result(self.result)

    def _context(self) -> AuthenticatedGateContext:
        actor = self.result["evidence_actor"]
        return AuthenticatedGateContext(
            issuance_record_digests=frozenset(
                {self.ledger["issued_by"]["issuance_record_digest"]}
            ),
            authentication_record_digests=frozenset(
                {actor["authentication_record_digest"]}
            ),
            principal_roles={
                actor["authenticated_principal"]: frozenset(actor["authenticated_roles"])
            },
            output_stream_digests=frozenset(
                {self.result["output_evidence"]["full_output_digest"]}
            ),
            validation_time=datetime(2026, 8, 24, 5, 20, 11, tzinfo=timezone.utc),
        )

    def planned_predecessor(self) -> dict:
        planned = copy.deepcopy(self.ledger)
        planned["ledger_id"] = planned["ledger_id"].replace("-r2", "-r1")
        planned["ledger_revision"] = 1
        planned["ledger_state"] = "PLANNED"
        planned["predecessor_ledger_digest"] = None
        planned["expected_head_sha"] = None
        planned["candidate_result_digest"] = None
        planned["material_authors"] = []
        rebind_ledger(planned)
        return planned

    def bind_predecessor(self, planned: dict) -> None:
        self.ledger["predecessor_ledger_digest"] = planned["ledger_digest"]
        rebind_ledger(self.ledger)
        self.result["ledger_digest"] = self.ledger["ledger_digest"]
        rebind_result(self.result)

    def verified_head(self):
        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        return validate_gate_ledger(
            self.ledger,
            context=self.context,
            predecessor=planned,
            runner_registry=self.registry,
        )

    def test_feature_flags_fail_closed_and_expose_no_completion_authority(self):
        disabled = completion_runtime_readiness(
            enabled=False,
            isolated_proof_enabled=False,
            store_configured=True,
            runner_registry=self.registry,
        )
        self.assertFalse(disabled["ready_for_isolated_proof"])
        self.assertFalse(disabled["completion_authority"])
        self.assertIn("runtime_disabled", disabled["reasons"])

    def test_semantic_validation_requires_authenticated_issuance_and_registry(self):
        with self.assertRaisesRegex(CompletionGateError, "issuance"):
            validate_gate_ledger(
                self.ledger,
                context=AuthenticatedGateContext(),
                runner_registry=self.registry,
            )
        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        verified = validate_gate_ledger(
            self.ledger,
            context=self.context,
            predecessor=planned,
            runner_registry=self.registry,
        )
        changed = copy.deepcopy(self.ledger)
        changed["director_gates"][0]["runner_id"] = "pip_check"
        rebind_ledger(changed)
        changed_predecessor = copy.deepcopy(planned)
        changed_predecessor["director_gates"][0]["runner_id"] = "pip_check"
        rebind_ledger(changed_predecessor)
        changed["predecessor_ledger_digest"] = changed_predecessor["ledger_digest"]
        rebind_ledger(changed)
        with self.assertRaisesRegex(CompletionGateError, "unknown runner"):
            validate_gate_ledger(
                changed,
                context=self.context,
                predecessor=changed_predecessor,
                runner_registry=self.registry,
            )
        self.assertEqual(self.ledger["ledger_digest"], verified.digest)

    def test_result_validation_derives_assertions_and_authenticates_full_stream(self):
        verified_ledger = self.verified_head()
        verified_result = validate_gate_result(
            verified_ledger, self.result, context=self.context
        )
        self.assertEqual(self.result["result_digest"], verified_result.digest)

        changed = copy.deepcopy(self.result)
        changed["exit_code"] = 1
        rebind_result(changed)
        with self.assertRaisesRegex(CompletionGateError, "exit-code"):
            validate_gate_result(verified_ledger, changed, context=self.context)

        unauthenticated_output = copy.deepcopy(self.result)
        unauthenticated_output["output_evidence"]["full_output_digest"] = "sha256:" + "0" * 64
        rebind_result(unauthenticated_output)
        with self.assertRaisesRegex(CompletionGateError, "output stream"):
            validate_gate_result(
                verified_ledger, unauthenticated_output, context=self.context
            )

    def test_append_only_store_rejects_mutation_and_reports_evidence_only(self):
        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "completion.sqlite3")
            store = CompletionGateStore(path)
            store.append_ledger(
                planned, context=self.context, runner_registry=self.registry
            )
            ledger = store.append_ledger(
                self.ledger, context=self.context, runner_registry=self.registry
            )
            result = store.append_result(
                self.result, verified_ledger=ledger, context=self.context
            )
            status = store.completion_status(ledger, context=self.context)
            self.assertTrue(status["gate_evidence_complete"])
            # The fixture result carries INDEPENDENT_REVIEWER evidence, so the
            # assignment-scoped claim is authorized. Project completion is a
            # different authority and stays false regardless of gate evidence.
            self.assertTrue(status["assignment_acceptance_authorized"])
            self.assertFalse(status["project_completion_authorized"])
            self.assertEqual([result.document["gate_id"]], status["passed"])

            connection = sqlite3.connect(path)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "UPDATE completion_gate_results SET status = 'FAIL'"
                    )
            finally:
                connection.close()

    def test_store_is_idempotent_but_identity_reuse_with_new_content_fails(self):
        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        with tempfile.TemporaryDirectory() as temporary:
            store = CompletionGateStore(str(Path(temporary) / "completion.sqlite3"))
            store.append_ledger(planned, context=self.context, runner_registry=self.registry)
            ledger = store.append_ledger(
                self.ledger, context=self.context, runner_registry=self.registry
            )
            first = store.append_result(
                self.result, verified_ledger=ledger, context=self.context
            )
            replay = store.append_result(
                self.result, verified_ledger=ledger, context=self.context
            )
            self.assertEqual(first.digest, replay.digest)
            changed = copy.deepcopy(self.result)
            changed["attempt_number"] = 2
            rebind_result(changed)
            with self.assertRaisesRegex(CompletionGateError, "different evidence"):
                store.append_result(
                    changed, verified_ledger=ledger, context=self.context
                )

    def test_runner_is_allowlisted_shell_free_and_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            subprocess.run(("git", "init", "-q"), cwd=repository, check=True)
            subprocess.run(("git", "config", "user.email", "completion@example.invalid"), cwd=repository, check=True)
            subprocess.run(("git", "config", "user.name", "Completion Gate Test"), cwd=repository, check=True)
            (repository / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            subprocess.run(("git", "add", "fixture.txt"), cwd=repository, check=True)
            subprocess.run(("git", "commit", "-q", "-m", "fixture"), cwd=repository, check=True)
            self.ledger["expected_head_sha"] = self._git_head(repository)
            planned = self.planned_predecessor()
            self.bind_predecessor(planned)
            self.context = self._context()
            verified = validate_gate_ledger(
                self.ledger,
                context=self.context,
                predecessor=planned,
                runner_registry=self.registry,
            )
            with self.assertRaisesRegex(CompletionGateError, "disabled"):
                self.registry.execute(
                    verified, "director.contract-tests", repository_root=repository
                )
            observation = self.registry.execute(
                verified,
                "director.contract-tests",
                repository_root=repository,
                enabled=True,
                isolated_proof_enabled=True,
            )
        self.assertEqual("PASS", observation.status)
        self.assertEqual(0, observation.exit_code)
        self.assertEqual((), observation.failure_reasons)
        self.assertEqual(observation.before_head_sha, observation.after_head_sha)

    def _proof_repository(self, temporary: str):
        """Build a committed git repository bound to the head-bound ledger."""

        repository = Path(temporary) / "repository"
        repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=repository, check=True)
        subprocess.run(
            ("git", "config", "user.email", "completion@example.invalid"),
            cwd=repository, check=True,
        )
        subprocess.run(
            ("git", "config", "user.name", "Completion Gate Test"),
            cwd=repository, check=True,
        )
        (repository / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(("git", "add", "fixture.txt"), cwd=repository, check=True)
        subprocess.run(("git", "commit", "-q", "-m", "fixture"), cwd=repository, check=True)
        self.ledger["expected_head_sha"] = self._git_head(repository)
        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        verified = validate_gate_ledger(
            self.ledger,
            context=self.context,
            predecessor=planned,
            runner_registry=self.registry,
        )
        return repository, verified

    def test_a_linked_working_directory_is_rejected_before_execution(self):
        """`resolve()` follows links, so containment alone is not enough.

        A link that points back inside the repository still passes a resolved
        containment check, and on Windows a junction is not a symbolic link at
        all. The component walk must refuse the link itself.
        """

        with tempfile.TemporaryDirectory() as temporary:
            repository, verified = self._proof_repository(temporary)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            inside = repository / "inside"
            inside.mkdir()

            for label, target in (("escaping", outside), ("contained", inside)):
                link = repository / f"link-{label}"
                try:
                    link.symlink_to(target, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self.skipTest("this environment cannot create directory links")
                gate = verified.document["director_gates"][0]
                gate["working_directory"] = link.name
                rebind_ledger(verified.document)
                with self.subTest(link=label):
                    with self.assertRaisesRegex(
                        CompletionGateError, "symbolic link|reparse point"
                    ):
                        self.registry.execute(
                            verified,
                            "director.contract-tests",
                            repository_root=repository,
                            enabled=True,
                            isolated_proof_enabled=True,
                        )

    def test_a_working_directory_outside_the_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, verified = self._proof_repository(temporary)
            gate = verified.document["director_gates"][0]
            # `_SAFE_RELATIVE_PATH` forbids "..", so an absolute-looking or
            # traversing spelling must fail canonicalization before any lstat.
            for spelling in ("../outside", "/etc", "nonexistent"):
                gate["working_directory"] = spelling
                rebind_ledger(verified.document)
                with self.subTest(spelling=spelling):
                    with self.assertRaises(CompletionGateError):
                        self.registry.execute(
                            verified,
                            "director.contract-tests",
                            repository_root=repository,
                            enabled=True,
                            isolated_proof_enabled=True,
                        )

    def test_aggregate_rejects_planned_or_partial_evidence(self):
        planned = self.planned_predecessor()
        planned_verified = validate_gate_ledger(
            planned, context=self.context, runner_registry=self.registry
        )
        status = aggregate_completion_eligibility(planned_verified, ())
        self.assertFalse(status["gate_evidence_complete"])
        self.assertFalse(status["project_completion_authorized"])

    @staticmethod
    def _git_head(repository: Path = ROOT) -> str:
        return subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=repository, text=True
        ).strip()


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
