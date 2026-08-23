"""Default-on and isolation contracts for the LangGraph proof lanes."""

# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings, langgraph_resume_claim_path


ROOT = Path(__file__).resolve().parents[1]


class LangGraphConfigurationTests(unittest.TestCase):
    def base_settings(self) -> Settings:
        return replace(
            Settings(),
            sqlite_path="data/director.sqlite3",
            langgraph_shadow_enabled=False,
            langgraph_approval_resume_enabled=False,
            langgraph_checkpoint_path="",
            langgraph_checkpoint_namespace="",
        )

    def test_proof_lanes_default_on_with_isolated_configuration(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "profile-a",
                "SQLITE_PATH": "data/profile-a.sqlite3",
            },
            clear=True,
        ):
            settings = replace(Settings(), sqlite_path="data/profile-a.sqlite3")

        settings.validate_langgraph_configuration()
        readiness = settings.langgraph_shadow_readiness()

        self.assertTrue(readiness["shadow_enabled"])
        self.assertTrue(readiness["approval_resume_enabled"])
        self.assertTrue(readiness["configuration_valid"])
        self.assertFalse(readiness["runtime_active"])
        self.assertTrue(readiness["legacy_sole_writer"])
        self.assertEqual(
            settings.langgraph_checkpoint_path,
            "data/profile-a.langgraph-shadow.sqlite3",
        )
        self.assertEqual(
            settings.langgraph_checkpoint_namespace,
            "director-profile-a-shadow-v1",
        )

    def test_default_on_fallbacks_are_unique_per_existing_profile(self):
        observed: set[tuple[str, str]] = set()
        for profile in ("local", "project-uppercut", "edge-oracle"):
            with self.subTest(profile=profile), patch.dict(
                os.environ,
                {
                    "APP_ENV": profile,
                    "SQLITE_PATH": f"data/{profile}.sqlite3",
                },
                clear=True,
            ):
                settings = replace(Settings(), sqlite_path=f"data/{profile}.sqlite3")
                settings.validate_langgraph_configuration()
                observed.add(
                    (
                        settings.langgraph_checkpoint_path,
                        settings.langgraph_checkpoint_namespace,
                    )
                )
        self.assertEqual(len(observed), 3)

    def test_enabled_lane_requires_checkpoint_path(self):
        settings = replace(self.base_settings(), langgraph_shadow_enabled=True)

        with self.assertRaisesRegex(ValueError, "LANGGRAPH_CHECKPOINT_PATH"):
            settings.validate_langgraph_configuration()

    def test_enabled_lane_requires_checkpoint_namespace(self):
        settings = replace(
            self.base_settings(),
            langgraph_approval_resume_enabled=True,
            langgraph_checkpoint_path="data/langgraph-shadow.sqlite3",
        )

        with self.assertRaisesRegex(ValueError, "LANGGRAPH_CHECKPOINT_NAMESPACE"):
            settings.validate_langgraph_configuration()

    def test_enabled_lane_rejects_legacy_database_even_when_spelled_differently(self):
        settings = replace(
            self.base_settings(),
            langgraph_shadow_enabled=True,
            langgraph_checkpoint_path="data/temporary/../director.sqlite3",
            langgraph_checkpoint_namespace="director-shadow-v1",
        )

        with self.assertRaisesRegex(ValueError, "must differ from SQLITE_PATH"):
            settings.validate_langgraph_configuration()

    def test_enabled_lane_rejects_hardlinked_legacy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy.sqlite3"
            checkpoint = Path(directory) / "checkpoint.sqlite3"
            legacy.write_bytes(b"legacy")
            try:
                os.link(legacy, checkpoint)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            self.assertTrue(os.path.samefile(legacy, checkpoint))
            settings = replace(
                self.base_settings(),
                langgraph_shadow_enabled=True,
                sqlite_path=str(legacy),
                langgraph_checkpoint_path=str(checkpoint),
                langgraph_checkpoint_namespace="hardlink-proof",
            )

            with self.assertRaisesRegex(ValueError, "must differ from SQLITE_PATH"):
                settings.validate_langgraph_configuration()

    def test_enabled_lane_rejects_padded_hardlink_path(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy.sqlite3"
            checkpoint = Path(directory) / "checkpoint.sqlite3"
            legacy.write_bytes(b"legacy")
            try:
                os.link(legacy, checkpoint)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            settings = replace(
                self.base_settings(),
                langgraph_shadow_enabled=True,
                sqlite_path=f"  {legacy}  ",
                langgraph_checkpoint_path=str(checkpoint),
                langgraph_checkpoint_namespace="padded-hardlink-proof",
            )

            self.assertFalse(
                settings.langgraph_shadow_readiness()["checkpoint_path_isolated"]
            )
            with self.assertRaisesRegex(ValueError, "must differ from SQLITE_PATH"):
                settings.validate_langgraph_configuration()

    def test_enabled_lane_rejects_claim_database_hardlinked_to_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy.sqlite3"
            checkpoint = Path(directory) / "checkpoint.sqlite3"
            claim = Path(langgraph_resume_claim_path(str(checkpoint)))
            legacy.write_bytes(b"legacy")
            checkpoint.write_bytes(b"checkpoint")
            try:
                os.link(legacy, claim)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            settings = replace(
                self.base_settings(),
                langgraph_approval_resume_enabled=True,
                sqlite_path=str(legacy),
                langgraph_checkpoint_path=str(checkpoint),
                langgraph_checkpoint_namespace="claim-hardlink-proof",
            )

            with self.assertRaisesRegex(ValueError, "claim database"):
                settings.validate_langgraph_configuration()

    def test_enabled_lane_rejects_claim_database_hardlinked_to_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy.sqlite3"
            checkpoint = Path(directory) / "checkpoint.sqlite3"
            claim = Path(langgraph_resume_claim_path(str(checkpoint)))
            legacy.write_bytes(b"legacy")
            checkpoint.write_bytes(b"checkpoint")
            try:
                os.link(checkpoint, claim)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            settings = replace(
                self.base_settings(),
                langgraph_approval_resume_enabled=True,
                sqlite_path=str(legacy),
                langgraph_checkpoint_path=str(checkpoint),
                langgraph_checkpoint_namespace="claim-checkpoint-hardlink-proof",
            )

            with self.assertRaisesRegex(ValueError, "claim database"):
                settings.validate_langgraph_configuration()

    def test_isolated_enabled_configuration_is_valid_but_not_runtime_active(self):
        settings = replace(
            self.base_settings(),
            langgraph_shadow_enabled=True,
            langgraph_approval_resume_enabled=True,
            langgraph_checkpoint_path="data/langgraph-shadow.sqlite3",
            langgraph_checkpoint_namespace="director-shadow-v1",
        )

        settings.validate_langgraph_configuration()
        readiness = settings.langgraph_shadow_readiness()

        self.assertTrue(readiness["configuration_valid"])
        self.assertTrue(readiness["checkpoint_path_isolated"])
        self.assertFalse(readiness["runtime_active"])
        self.assertTrue(readiness["legacy_sole_writer"])

    def test_every_example_profile_is_default_on_and_uses_unique_state(self):
        profiles = {
            "local": ROOT / ".env.example",
            "uppercut": ROOT / ".env.uppercut.example",
            "oracle": ROOT / ".env.edge-oracle.example",
        }
        checkpoint_paths: set[str] = set()
        namespaces: set[str] = set()

        for name, path in profiles.items():
            values = dict(
                line.split("=", 1)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line
            )
            with self.subTest(profile=name):
                self.assertEqual(values["LANGGRAPH_SHADOW_ENABLED"], "true")
                self.assertEqual(values["LANGGRAPH_APPROVAL_RESUME_ENABLED"], "true")
                self.assertNotEqual(
                    values["LANGGRAPH_CHECKPOINT_PATH"], values["SQLITE_PATH"]
                )
                self.assertTrue(values["LANGGRAPH_CHECKPOINT_NAMESPACE"])
            checkpoint_paths.add(values["LANGGRAPH_CHECKPOINT_PATH"])
            namespaces.add(values["LANGGRAPH_CHECKPOINT_NAMESPACE"])

        self.assertEqual(len(checkpoint_paths), len(profiles))
        self.assertEqual(len(namespaces), len(profiles))

    def test_dependencies_are_exactly_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("langgraph==1.2.10", requirements.splitlines())
        self.assertIn("langgraph-checkpoint-sqlite==3.1.1", requirements.splitlines())

    def test_main_validates_and_reports_configuration_without_importing_langgraph(self):
        source = (ROOT / "src" / "director_orchestrator" / "main.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }

        self.assertLess(
            source.index("settings.validate_langgraph_configuration()"),
            source.index("store = TaskStore(settings.sqlite_path)"),
        )
        self.assertIn('"langgraph": settings.langgraph_shadow_readiness()', source)
        self.assertFalse(any(name.startswith("langgraph") for name in imported_modules))


# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]


if __name__ == "__main__":
    unittest.main()
