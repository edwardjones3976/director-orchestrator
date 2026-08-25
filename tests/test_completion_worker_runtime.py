# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:14 UTC | BEGIN]]
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.completion_worker_runtime import (
    ClosedVocabularyWorkerSanitizer,
)
from director_orchestrator.completion_workers import CompletionWorkerError
from director_orchestrator.config import Settings
from director_orchestrator.runtime_activation import (
    RuntimeActivation,
    RuntimeActivationError,
)


class CompletionWorkerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def settings(self, **overrides) -> Settings:
        fields = {
            "sqlite_path": str(self.root / "director.sqlite3"),
            "langgraph_checkpoint_path": str(self.root / "shadow.sqlite3"),
            "langgraph_runtime_checkpoint_path": str(self.root / "runtime.sqlite3"),
            "langgraph_runtime_ledger_path": str(self.root / "runtime-ledger.sqlite3"),
            "completion_gate_store_path": str(self.root / "gates.sqlite3"),
            "completion_gate_worker_store_path": str(self.root / "workers.sqlite3"),
            "completion_gates_enabled": True,
            "completion_gate_isolated_proof_enabled": True,
            "completion_gate_workers_enabled": True,
            "completion_gate_execution_tiers": ("host", "container"),
            "completion_gate_host_broker_path": sys.executable,
            "completion_gate_container_runtime_path": sys.executable,
            "completion_gate_container_image": (
                "python:3.12-slim@sha256:" + "a" * 64
            ),
        }
        fields.update(overrides)
        return replace(Settings(), **fields)

    def test_verified_container_tier_constructs_with_an_isolated_store(self):
        activation = RuntimeActivation(
            self.settings(completion_gate_execution_tiers=("container",))
        )
        self.addCleanup(activation.close)

        runtime = activation.completion_worker_runtime
        self.assertIsNotNone(runtime)
        assert runtime is not None
        readiness = activation.readiness()["completion_gates"]
        self.assertTrue(readiness["worker_executor_constructed"])
        self.assertTrue(readiness["worker_execution_ready"])
        self.assertTrue(readiness["worker_tiers_ready"]["disposable_container"])
        self.assertFalse(readiness["completion_authority"])
        self.assertTrue((self.root / "workers.sqlite3").is_file())

        container = runtime.worker_registry.resolve("container.python_compileall.src-tests")
        self.assertNotIn("-c", container.fixed_argv)
        with self.assertRaises(CompletionWorkerError):
            runtime.worker_registry.resolve("host.python_unittest.discover-tests")
        with self.assertRaises(CompletionWorkerError):
            runtime.worker_registry.resolve("container.python_unittest.discover-tests")
        with self.assertRaises(CompletionWorkerError):
            runtime.worker_registry.resolve("container.pip_check.environment")
        self.assertRegex(container.image, r"@sha256:[0-9a-f]{64}$")
        self.assertEqual("none", container.network_mode)
        self.assertTrue(container.repository_read_only)
        self.assertTrue(container.drop_all_capabilities)

    def test_worker_switch_off_constructs_no_executor_or_store(self):
        settings = self.settings(completion_gate_workers_enabled=False)
        activation = RuntimeActivation(settings)
        self.addCleanup(activation.close)

        self.assertIsNone(activation.completion_worker_runtime)
        readiness = activation.readiness()["completion_gates"]
        self.assertFalse(readiness["worker_execution_ready"])
        self.assertFalse((self.root / "workers.sqlite3").exists())

    def test_missing_broker_and_unpinned_image_fail_startup_closed(self):
        missing = str(self.root / "missing-broker.exe")
        with self.assertRaises(ValueError):
            self.settings(completion_gate_host_broker_path=missing).validate_completion_gate_configuration()
        with self.assertRaises(ValueError):
            self.settings(
                completion_gate_container_image="python:3.12-slim"
            ).validate_completion_gate_configuration()

    def test_worker_store_collision_fails_before_construction(self):
        with self.assertRaises(ValueError):
            self.settings(
                completion_gate_worker_store_path=str(self.root / "gates.sqlite3")
            ).validate_completion_gate_configuration()

    def test_unverified_host_broker_fails_activation_closed(self):
        broker = self.root / "broker.exe"
        broker.write_bytes(b"fixture")
        settings = self.settings(completion_gate_host_broker_path=str(broker))
        settings.validate_completion_gate_configuration()
        with self.assertRaisesRegex(
            RuntimeActivationError, "worker tiers could not be activated"
        ):
            RuntimeActivation(settings)

    def test_sanitizer_retains_no_provider_or_process_prose(self):
        sanitizer = ClosedVocabularyWorkerSanitizer()
        output = sanitizer.sanitize(b"secret-value", b"private failure")
        self.assertEqual("WORKER_OUTPUT_WITHHELD_BY_POLICY", output.decisive_output)
        self.assertNotIn("secret", output.decisive_output)
        self.assertNotIn("private", output.decisive_output)
        self.assertRegex(output.output_digest, r"^sha256:[0-9a-f]{64}$")
        different = sanitizer.sanitize(b"other-value!", b"private failure")
        self.assertNotEqual(output.output_digest, different.output_digest)
        self.assertTrue(output.redaction_passed)
        self.assertTrue(output.secret_scan_passed)
        detected = sanitizer.sanitize(b"api_key=not-a-real-secret-value", b"")
        self.assertFalse(detected.secret_scan_passed)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | END]]
