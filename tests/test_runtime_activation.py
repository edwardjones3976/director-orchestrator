# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Fail-closed activation of the packet coordinator and Completion Gate store."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.langgraph_runtime import (
    LangGraphPacketRuntime,
    PacketBoundaryAdapter,
    RuntimeContractError,
    RuntimeExecutionMode,
    WriterOwnershipToken,
)
from director_orchestrator.packet_contracts import load_json_document
from director_orchestrator.runtime_activation import (
    RuntimeActivation,
    RuntimeActivationError,
)


WORK_PACKET_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "schemas"
    / "model-work-packet-v1.example.json"
)


class StubPacketTaskRouter:
    """Minimal wired router: the three artifacts `run_packet` requires."""

    def __init__(self, *, provider: str = "claude") -> None:
        self.packet = load_json_document(WORK_PACKET_EXAMPLE)
        self.token = WriterOwnershipToken.issue(
            task_id=self.packet["task_id"], owner_id="langgraph-packet-runtime"
        )
        self.adapter = PacketBoundaryAdapter(provider, _UnusedTransport())

    def work_packet(self, record, assignment):
        return self.packet

    def writer_token(self, record):
        return self.token

    def boundary_adapter(self, provider: str) -> PacketBoundaryAdapter:
        return self.adapter


class _UnusedTransport:
    def send(self, work_packet_json, *, context):  # pragma: no cover - not dispatched
        raise AssertionError("activation tests never contact a provider")


class _IsolatedSettings:
    """Shared temporary-directory settings builder for both suites."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def settings(self, **overrides) -> Settings:
        fields = {
            "sqlite_path": str(self.root / "director.sqlite3"),
            "langgraph_checkpoint_path": str(self.root / "shadow.sqlite3"),
            "langgraph_checkpoint_namespace": "director-test-shadow-v1",
            "langgraph_runtime_checkpoint_path": str(self.root / "runtime.sqlite3"),
            "langgraph_runtime_ledger_path": str(self.root / "runtime-ledger.sqlite3"),
            "langgraph_runtime_checkpoint_namespace": "director-test-runtime-v1",
            "langgraph_runtime_writer_owner_id": "langgraph-packet-runtime",
            "completion_gate_store_path": str(self.root / "gates.sqlite3"),
            "project_allowlist": ("demo",),
            "repository_allowlist": ("example/demo",),
            "allowed_starting_refs": ("main",),
            "claude_callback_secret": "callback-secret",
        }
        fields.update(overrides)
        return replace(Settings(), **fields)


class ActivationSettingsTests(_IsolatedSettings, unittest.TestCase):
    def test_the_coordinator_is_off_by_default(self):
        readiness = self.settings().langgraph_runtime_readiness()

        self.assertFalse(readiness["runtime_enabled"])
        self.assertFalse(readiness["runtime_active"])
        self.assertFalse(readiness["live_provider_dispatch"])
        # Configuration validity is independent of the switch, so an operator can
        # verify isolation before turning anything on.
        self.assertTrue(readiness["configuration_valid"])
        self.assertTrue(readiness["stores_isolated"])

    def test_enabling_the_runtime_alone_never_enables_live_dispatch(self):
        readiness = self.settings(
            langgraph_runtime_enabled=True, allow_paid_runs=True
        ).langgraph_runtime_readiness()

        self.assertTrue(readiness["runtime_active"])
        self.assertFalse(readiness["live_dispatch_enabled"])
        self.assertFalse(readiness["live_provider_dispatch"])

    def test_live_dispatch_requires_every_switch_together(self):
        base = {
            "langgraph_runtime_enabled": True,
            "langgraph_runtime_live_dispatch_enabled": True,
            "allow_paid_runs": True,
        }
        self.assertTrue(
            self.settings(**base).langgraph_runtime_readiness()["live_provider_dispatch"]
        )
        for missing in base:
            with self.subTest(missing=missing):
                readiness = self.settings(
                    **{**base, missing: False}
                ).langgraph_runtime_readiness()
                self.assertFalse(readiness["live_provider_dispatch"])

        # An unconfigured execution profile also fails closed.
        readiness = self.settings(**base, project_allowlist=()).langgraph_runtime_readiness()
        self.assertFalse(readiness["live_provider_dispatch"])

    def test_a_shared_store_fails_startup_closed(self):
        shared = str(self.root / "director.sqlite3")
        collisions = {
            "runtime_checkpoint_equals_legacy": {
                "langgraph_runtime_checkpoint_path": shared
            },
            "runtime_ledger_equals_legacy": {"langgraph_runtime_ledger_path": shared},
            "runtime_ledger_equals_runtime_checkpoint": {
                "langgraph_runtime_ledger_path": str(self.root / "runtime.sqlite3")
            },
            "runtime_checkpoint_equals_shadow": {
                "langgraph_runtime_checkpoint_path": str(self.root / "shadow.sqlite3")
            },
        }
        for label, overrides in collisions.items():
            with self.subTest(case=label):
                settings = self.settings(langgraph_runtime_enabled=True, **overrides)
                self.assertFalse(
                    settings.langgraph_runtime_readiness()["stores_isolated"]
                )
                with self.assertRaises(ValueError):
                    settings.validate_langgraph_runtime_configuration()

    def test_a_shared_checkpoint_namespace_fails_startup_closed(self):
        settings = self.settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_checkpoint_namespace="director-test-shadow-v1",
        )
        with self.assertRaises(ValueError):
            settings.validate_langgraph_runtime_configuration()

    def test_a_disabled_runtime_never_fails_startup(self):
        settings = self.settings(
            langgraph_runtime_checkpoint_path="", langgraph_runtime_ledger_path=""
        )
        settings.validate_langgraph_runtime_configuration()
        self.assertFalse(settings.langgraph_runtime_readiness()["runtime_active"])

    def test_completion_gate_readiness_never_claims_authority(self):
        readiness = self.settings(
            completion_gates_enabled=True,
            completion_gate_isolated_proof_enabled=True,
        ).completion_gate_readiness()

        self.assertTrue(readiness["evidence_recording_active"])
        self.assertTrue(readiness["execution_active"])
        # Gate evidence is never acceptance, merge, deployment, or completion.
        self.assertFalse(readiness["completion_authority"])

    def test_gate_execution_requires_its_own_isolated_proof_switch(self):
        readiness = self.settings(completion_gates_enabled=True).completion_gate_readiness()
        self.assertTrue(readiness["evidence_recording_active"])
        self.assertFalse(readiness["execution_active"])

    def test_a_gate_store_sharing_another_database_fails_startup_closed(self):
        settings = self.settings(
            completion_gates_enabled=True,
            completion_gate_store_path=str(self.root / "director.sqlite3"),
        )
        self.assertFalse(settings.completion_gate_readiness()["store_isolated"])
        with self.assertRaises(ValueError):
            settings.validate_completion_gate_configuration()

    def test_shadow_readiness_stops_claiming_a_sole_legacy_writer(self):
        active = self.settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            allow_paid_runs=True,
        ).langgraph_shadow_readiness()
        self.assertTrue(active["runtime_active"])
        self.assertFalse(active["legacy_sole_writer"])

        idle = self.settings().langgraph_shadow_readiness()
        self.assertFalse(idle["runtime_active"])
        self.assertTrue(idle["legacy_sole_writer"])


class RuntimeActivationTests(_IsolatedSettings, unittest.TestCase):
    def test_nothing_is_constructed_while_both_features_are_off(self):
        activation = RuntimeActivation(self.settings())
        self.addCleanup(activation.close)

        self.assertIsNone(activation.runtime)
        self.assertIsNone(activation.completion_store)
        self.assertIsNone(activation.single_writer_conflict())
        # A disabled feature must not create its database as a side effect.
        self.assertFalse((self.root / "runtime.sqlite3").exists())
        self.assertFalse((self.root / "gates.sqlite3").exists())

    def test_an_activated_coordinator_reports_an_honest_readiness_surface(self):
        activation = RuntimeActivation(
            self.settings(langgraph_runtime_enabled=True, completion_gates_enabled=True)
        )
        self.addCleanup(activation.close)

        readiness = activation.readiness()
        runtime = readiness["langgraph_runtime"]
        self.assertTrue(runtime["coordinator_constructed"])
        self.assertTrue(runtime["runtime_active"])
        self.assertTrue(runtime["checkpoint_namespace_matches"])
        self.assertFalse(runtime["live_provider_dispatch"])
        self.assertTrue(readiness["completion_gates"]["evidence_recording_active"])
        self.assertFalse(readiness["completion_gates"]["completion_authority"])

        # No path, namespace value, secret, or allowlist entry is exposed.
        surfaced = [
            value
            for section in readiness.values()
            for value in section.values()
            if isinstance(value, str)
        ]
        for value in surfaced:
            self.assertNotIn(str(self.root), value)

    def test_the_legacy_engine_surrenders_dispatch_to_an_active_coordinator(self):
        activation = RuntimeActivation(
            self.settings(
                langgraph_runtime_enabled=True,
                langgraph_runtime_live_dispatch_enabled=True,
                allow_paid_runs=True,
            ),
            packet_task_router=StubPacketTaskRouter(),
        )
        self.addCleanup(activation.close)

        conflict = activation.single_writer_conflict()
        self.assertIsNotNone(conflict)
        self.assertIn("will not dispatch", conflict)
        self.assertTrue(activation.coordinator_owns_dispatch())
        self.assertIsNotNone(activation.packet_task_router)

    # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
    def test_live_dispatch_without_a_router_refuses_to_activate(self):
        # Enabling the live lane with nothing able to convert an intake task into
        # a work packet would leave no engine executing anything. Legacy ownership
        # is preserved by refusing the configuration outright.
        with self.assertRaises(RuntimeActivationError) as caught:
            RuntimeActivation(
                self.settings(
                    langgraph_runtime_enabled=True,
                    langgraph_runtime_live_dispatch_enabled=True,
                    allow_paid_runs=True,
                )
            )
        self.assertIn("no packet task router is wired", str(caught.exception))

    def test_a_router_is_not_required_while_live_dispatch_is_off(self):
        activation = RuntimeActivation(
            self.settings(langgraph_runtime_enabled=True, allow_paid_runs=True)
        )
        self.addCleanup(activation.close)

        self.assertIsNotNone(activation.runtime)
        self.assertFalse(activation.coordinator_owns_dispatch())
        # The legacy engine keeps dispatch ownership, so nothing stops.
        self.assertIsNone(activation.single_writer_conflict())
    # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]

    def test_an_invalid_enabled_configuration_refuses_to_activate(self):
        with self.assertRaises(ValueError):
            RuntimeActivation(
                self.settings(
                    langgraph_runtime_enabled=True,
                    langgraph_runtime_ledger_path=str(self.root / "runtime.sqlite3"),
                )
            )

    def test_an_activated_coordinator_requires_the_v1_packet_boundary(self):
        activation = RuntimeActivation(
            self.settings(
                langgraph_runtime_enabled=True,
                langgraph_runtime_live_dispatch_enabled=True,
                allow_paid_runs=True,
            ),
            packet_task_router=StubPacketTaskRouter(),
        )
        self.addCleanup(activation.close)
        runtime = activation.runtime
        assert isinstance(runtime, LangGraphPacketRuntime)
        self.assertTrue(runtime.require_packet_boundary_adapter)

        class BareAdapter:
            provider = "claude"

            def dispatch(self, work_packet, *, context):  # pragma: no cover - refused
                raise AssertionError("dispatch must not be reached")

        from director_orchestrator.packet_contracts import load_json_document

        work = load_json_document(
            Path(__file__).resolve().parents[1]
            / "docs"
            / "schemas"
            / "model-work-packet-v1.example.json"
        )
        token = WriterOwnershipToken.issue(
            task_id=work["task_id"], owner_id="langgraph-packet-runtime"
        )
        with self.assertRaises(RuntimeContractError):
            runtime.run_packet(
                work,
                provider="claude",
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                adapter=BareAdapter(),
                writer_token=token,
            )


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
