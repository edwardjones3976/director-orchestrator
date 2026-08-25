# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | BEGIN]]
from __future__ import annotations

import copy
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
from director_orchestrator.process_isolation import trusted_neutral_cwd


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

    def test_store_preserves_prior_pass_digest_for_dependent_append_and_replay(self):
        first_gate = self.ledger["director_gates"][0]
        dependent_gate = copy.deepcopy(first_gate)
        dependent_gate["gate_id"] = "director.dependent-contract-test"
        dependent_gate["order"] = 2
        dependent_gate["dependencies"] = [
            {"gate_id": first_gate["gate_id"], "required_status": "PASS"}
        ]
        rebind_gate(dependent_gate)
        self.ledger["director_gates"].append(dependent_gate)
        rebind_ledger(self.ledger)

        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        first_gate = self.ledger["director_gates"][0]
        dependent_gate = self.ledger["director_gates"][1]
        first_result = copy.deepcopy(self.result)
        first_result["ledger_digest"] = self.ledger["ledger_digest"]
        first_result["gate_definition_digest"] = first_gate["gate_definition_digest"]
        rebind_result(first_result)

        later_result = copy.deepcopy(first_result)
        later_result["result_id"] = "RESULT-222222222222"
        later_result["attempt_id"] = "ATTEMPT-222222222222"
        later_result["attempt_number"] = 2
        rebind_result(later_result)

        dependent_result = copy.deepcopy(first_result)
        dependent_result["result_id"] = "RESULT-333333333333"
        dependent_result["attempt_id"] = "ATTEMPT-333333333333"
        dependent_result["gate_id"] = dependent_gate["gate_id"]
        dependent_result["gate_definition_digest"] = dependent_gate[
            "gate_definition_digest"
        ]
        dependent_result["dependency_results"] = [
            {
                "gate_id": first_gate["gate_id"],
                "status": "PASS",
                "result_digest": first_result["result_digest"],
            }
        ]
        rebind_result(dependent_result)

        with tempfile.TemporaryDirectory() as temporary:
            store = CompletionGateStore(str(Path(temporary) / "completion.sqlite3"))
            store.append_ledger(planned, context=self.context, runner_registry=self.registry)
            ledger = store.append_ledger(
                self.ledger, context=self.context, runner_registry=self.registry
            )
            store.append_result(first_result, verified_ledger=ledger, context=self.context)
            store.append_result(later_result, verified_ledger=ledger, context=self.context)
            appended = store.append_result(
                dependent_result, verified_ledger=ledger, context=self.context
            )
            replayed = store.append_result(
                dependent_result, verified_ledger=ledger, context=self.context
            )
            status = store.completion_status(ledger, context=self.context)

        self.assertEqual(appended.digest, replayed.digest)
        self.assertTrue(status["gate_evidence_complete"])
        self.assertEqual(
            sorted((first_gate["gate_id"], dependent_gate["gate_id"])),
            status["passed"],
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
            with patch.object(
                self.registry, "_run_bounded", wraps=self.registry._run_bounded
            ) as bounded:
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
        self.assertEqual(trusted_neutral_cwd(), bounded.call_args.kwargs["cwd"])

    def test_module_runner_cannot_execute_repository_search_path_shadow(self):
        self.profile = ParameterProfile.build(
            "repository-shadow", ("-m", "compileall", "-q", ".")
        )
        self.definition = RunnerDefinition.build(
            "python_unittest", sys.executable, (self.profile,),
            maximum_timeout_seconds=300,
        )
        self.registry = RunnerRegistry((self.definition,))
        self._single_runnable_gate()
        gate = self.ledger["director_gates"][0]
        gate["parameter_profile_id"] = self.profile.profile_id
        rebind_gate(gate)
        rebind_ledger(self.ledger)
        with tempfile.TemporaryDirectory() as temporary:
            repository, _ = self._proof_repository(temporary)
            marker = Path(temporary) / "repository-module-executed.txt"
            (repository / "compileall.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n",
                encoding="utf-8",
            )
            subprocess.run(("git", "add", "compileall.py"), cwd=repository, check=True)
            subprocess.run(
                ("git", "commit", "-q", "-m", "shadow"), cwd=repository, check=True
            )
            self.ledger["expected_head_sha"] = self._git_head(repository)
            planned = self.planned_predecessor()
            self.bind_predecessor(planned)
            verified = validate_gate_ledger(
                self.ledger,
                context=self._context(),
                predecessor=planned,
                runner_registry=self.registry,
            )

            with self.assertRaisesRegex(CompletionGateError, "isolated completion worker"):
                self.registry.execute(
                    verified,
                    "director.contract-tests",
                    repository_root=repository,
                    enabled=True,
                    isolated_proof_enabled=True,
                )
            self.assertFalse(marker.exists())

    def test_git_probe_uses_explicit_repository_from_neutral_cwd(self):
        expected_head = "a" * 40
        completed = type(
            "Completed", (), {"returncode": 0, "stdout": expected_head + "\n"}
        )()
        repository = Path("C:/untrusted-repository")
        with patch(
            "director_orchestrator.completion_gates.subprocess.run",
            return_value=completed,
        ) as run:
            observed = RunnerRegistry._git_head(repository)

        self.assertEqual(expected_head, observed)
        self.assertEqual(
            ("git", "-C", str(repository), "rev-parse", "HEAD"),
            run.call_args.args[0],
        )
        self.assertEqual(trusted_neutral_cwd(), run.call_args.kwargs["cwd"])

    def test_bounded_runner_timeout_terminates_descendant_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "descendant-survived.txt"
            child = (
                "import pathlib,sys,time; time.sleep(2); "
                "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]); "
                "time.sleep(30)"
            )

            _, timed_out, exceeded, _, _ = self.registry._run_bounded(
                (sys.executable, "-c", parent, str(marker), child),
                cwd=Path(temporary),
                environment=os.environ.copy(),
                timeout_seconds=1,
                output_limit_bytes=4096,
            )
            time.sleep(2.25)

            self.assertTrue(timed_out)
            self.assertFalse(exceeded)
            self.assertFalse(marker.exists())

    def test_bounded_runner_output_overflow_terminates_descendant_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "overflow-descendant-survived.txt"
            child = (
                "import pathlib,sys,time; time.sleep(2); "
                "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]); "
                "sys.stdout.write('x'*65536); sys.stdout.flush(); time.sleep(30)"
            )

            _, timed_out, exceeded, stdout, stderr = self.registry._run_bounded(
                (sys.executable, "-c", parent, str(marker), child),
                cwd=Path(temporary),
                environment=os.environ.copy(),
                timeout_seconds=10,
                output_limit_bytes=128,
            )
            time.sleep(2.25)

            self.assertFalse(timed_out)
            self.assertTrue(exceeded)
            self.assertLessEqual(len(stdout) + len(stderr), 128)
            self.assertFalse(marker.exists())

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

    # [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | BEGIN]]
    def _script_repository(self, temporary: str):
        """A committed repository whose gate is bound to a repository-owned script."""

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
        (repository / "scripts").mkdir()
        script = repository / "scripts" / "check_completion.py"
        script.write_bytes(b"print('OK')\n")
        subprocess.run(("git", "add", "-A"), cwd=repository, check=True)
        subprocess.run(("git", "commit", "-q", "-m", "script"), cwd=repository, check=True)

        definition = RunnerDefinition.build(
            "repository_owned_script",
            sys.executable,
            (self.profile,),
            maximum_timeout_seconds=300,
        )
        self.registry = RunnerRegistry((definition,))
        head = self._git_head(repository)
        blob_sha = subprocess.check_output(
            ("git", "rev-parse", "HEAD:scripts/check_completion.py"),
            cwd=repository, text=True,
        ).strip()
        approval_digest = "sha256:" + "a" * 64

        gate = self.ledger["director_gates"][0]
        gate["runner_id"] = "repository_owned_script"
        gate["runner_definition_digest"] = definition.definition_digest
        gate["script_binding"] = {
            "path": "scripts/check_completion.py",
            "blob_sha": blob_sha,
            "content_digest": "sha256:" + hashlib.sha256(script.read_bytes()).hexdigest(),
            "bound_head_sha": head,
            "approval_digest": approval_digest,
        }
        self.ledger["runner_registry_digest"] = self.registry.registry_digest
        self.ledger["expected_head_sha"] = head
        rebind_ledger(self.ledger)

        planned = self.planned_predecessor()
        self.bind_predecessor(planned)
        self.context = self._context()
        verified = validate_gate_ledger(
            self.ledger,
            context=self.context,
            predecessor=planned,
            runner_registry=self.registry,
        )
        return repository, script, verified, frozenset({approval_digest})

    def test_a_hardlinked_repository_script_is_rejected(self):
        """A second name for the script's inode is refused before execution.

        A hard link has no distinguishable path component, so the containment walk
        cannot see it: the Git blob, the content digest, and the resolved path all
        still match. Only the link count distinguishes a repository-owned script
        from one whose bytes an alias outside the repository can still replace.
        """

        with tempfile.TemporaryDirectory() as temporary:
            repository, script, verified, approvals = self._script_repository(temporary)

            def execute():
                return self.registry.execute(
                    verified,
                    "director.contract-tests",
                    repository_root=repository,
                    enabled=True,
                    isolated_proof_enabled=True,
                    authenticated_script_approval_digests=approvals,
                )

            # Negative control: the identical gate passes while the script has one
            # name, so the rejection below is caused by the hard link alone.
            self.assertEqual("PASS", execute().status)

            alias = Path(temporary) / "outside-alias.py"
            try:
                os.link(script, alias)
            except (OSError, NotImplementedError, AttributeError) as exc:
                self.skipTest(f"this platform cannot create hard links: {exc}")
            if script.lstat().st_nlink < 2:
                self.skipTest("this platform does not report hard-link counts")

            with self.assertRaisesRegex(CompletionGateError, "hard link"):
                execute()
    # [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | END]]

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
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | END]]
