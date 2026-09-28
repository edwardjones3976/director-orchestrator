# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
"""Both production construction sites build the boundary or fail closed.

The gate script's builder is exercised directly (no subprocess, no network):
an enabled, credentialed gate without complete boundary configuration yields
no service at all and an explicit message. The orchestrator's main.py wiring
is pinned structurally, because importing it would construct the app.
"""

from __future__ import annotations

import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator.config import Settings
from director_orchestrator.hotl_execution import MergeExecutionBoundary

ROOT = Path(__file__).resolve().parents[1]


def load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "oracle_merge_gate_script", ROOT / "scripts" / "oracle_merge_gate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OracleMergeGateConstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.gate = load_gate_module()
        self.public_key_file = Path(self.temp.name) / "observer.pub"
        self.public_key_file.write_bytes(
            Ed25519PrivateKey.generate().public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw
            )
        )

    def settings(self, **overrides) -> Settings:
        values = dict(
            sqlite_path=str(Path(self.temp.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_github_token="test-only-token",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_observer_endpoint="http://127.0.0.1:8901",
            oracle_observer_public_key_path=str(self.public_key_file),
            oracle_standing_merge_audit_path=str(
                Path(self.temp.name) / "standing.sqlite3"
            ),
            oracle_standing_merge_environment="test",
            oracle_standing_merge_assignment_id="ASSIGN-1",
        )
        values.update(overrides)
        return Settings(**values)

    def test_an_enabled_gate_builds_its_service_with_the_boundary(self):
        service, failure = self.gate.build_service(
            self.settings(), str(Path(self.temp.name) / "gate.sqlite3")
        )
        self.assertIsNone(failure)
        self.assertIsInstance(
            service._standing_merge_boundary, MergeExecutionBoundary
        )

    def test_an_enabled_gate_without_boundary_configuration_fails_closed(self):
        for field, value in (
            ("oracle_observer_endpoint", ""),
            ("oracle_observer_public_key_path", ""),
            ("oracle_standing_merge_audit_path", ""),
            ("oracle_standing_merge_environment", ""),
            ("oracle_standing_merge_assignment_id", ""),
        ):
            with self.subTest(missing=field):
                service, failure = self.gate.build_service(
                    self.settings(**{field: value}),
                    str(Path(self.temp.name) / "gate.sqlite3"),
                )
                self.assertIsNone(service)
                self.assertIn("failed closed", failure)
                self.assertIn("not configured", failure)

    def test_an_invalid_public_key_file_fails_closed(self):
        truncated = Path(self.temp.name) / "short.pub"
        truncated.write_bytes(b"\x00" * 12)  # length probe, not key material
        service, failure = self.gate.build_service(
            self.settings(oracle_observer_public_key_path=str(truncated)),
            str(Path(self.temp.name) / "gate.sqlite3"),
        )
        self.assertIsNone(service)
        self.assertIn("32-byte Ed25519", failure)

    def test_a_remote_observer_endpoint_fails_closed(self):
        service, failure = self.gate.build_service(
            self.settings(oracle_observer_endpoint="http://10.1.2.3:8901"),
            str(Path(self.temp.name) / "gate.sqlite3"),
        )
        self.assertIsNone(service)
        self.assertIn("failed closed", failure)

    def test_a_disabled_gate_never_builds_a_boundary_or_merges(self):
        service, failure = self.gate.build_service(
            self.settings(oracle_automerge_enabled=False),
            str(Path(self.temp.name) / "gate.sqlite3"),
        )
        self.assertIsNone(failure)
        self.assertIsNone(service._standing_merge_boundary)
        self.assertFalse(service.policy.enabled)

    def test_main_wiring_requires_the_boundary_for_an_enabled_gate(self):
        """main.py must pass the built boundary into OracleMergeService, with
        no path that constructs a boundary-free enabled merger."""

        source = (ROOT / "src" / "director_orchestrator" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("build_standing_merge_boundary(settings", source)
        self.assertIn("standing_merge_boundary=", source)
        # The gate script's service construction always names the boundary.
        gate_source = inspect.getsource(self.gate)
        self.assertIn("standing_merge_boundary=standing_boundary", gate_source)
        gate_service_source = inspect.getsource(self.gate.build_service)
        self.assertIn("build_standing_merge_boundary", gate_service_source)


if __name__ == "__main__":
    unittest.main()
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
