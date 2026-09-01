# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
"""Default-on and isolation contracts for the LangGraph proof lanes."""

# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings, langgraph_resume_claim_path


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def hardlink_identity(source: Path, target: Path):
    try:
        os.link(source, target)
    except (OSError, NotImplementedError, AttributeError):
        target.touch()
        original_samefile = os.path.samefile
        identity = frozenset(
            (os.path.normcase(str(source.resolve())), os.path.normcase(str(target.resolve())))
        )

        def samefile(left: str, right: str) -> bool:
            candidate = frozenset((os.path.normcase(left), os.path.normcase(right)))
            return candidate == identity or original_samefile(left, right)

        with patch("director_orchestrator.config.os.path.samefile", side_effect=samefile):
            yield
    else:
        yield


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
                self.fail(f"hard-link configuration coverage is required: {exc}")
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
                self.fail(f"hard-link configuration coverage is required: {exc}")
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
                self.fail(f"hard-link configuration coverage is required: {exc}")
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
                self.fail(f"hard-link configuration coverage is required: {exc}")
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
            "long-table": ROOT / ".env.long-table.example",
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
        self.assertLess(
            source.index("settings.validate_langgraph_primary_configuration()"),
            source.index("store = TaskStore(settings.sqlite_path)"),
        )
        self.assertIn('"langgraph": settings.langgraph_shadow_readiness()', source)
        self.assertIn('"langgraph_primary": (', source)
        self.assertIn("primary_default_activation.health()", source)
        self.assertIn('"primary_router_wired": False', source)
        self.assertIn(
            "max_files=settings.langgraph_primary_snapshot_max_files", source
        )
        primary_source = (
            ROOT / "src" / "director_orchestrator" / "primary_default.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "max_files=settings.langgraph_primary_snapshot_max_files",
            primary_source,
        )
        self.assertFalse(any(name.startswith("langgraph") for name in imported_modules))

    def test_primary_runtime_defaults_off_and_never_claims_observed_authority(self):
        settings = self.base_settings()

        settings.validate_langgraph_primary_configuration()
        readiness = settings.langgraph_primary_configuration_readiness()

        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["new_intake_requested"])
        self.assertFalse(readiness["primary_runtime_active"])
        self.assertFalse(readiness["legacy_new_intake_disabled"])

    def test_primary_snapshot_inventory_limit_defaults_to_512_and_reads_env(self):
        profile = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES=512", profile.splitlines())
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(512, Settings().langgraph_primary_snapshot_max_files)
        with patch.dict(
            os.environ, {"LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES": "711"}, clear=True
        ):
            configured = Settings()

        self.assertEqual(711, configured.langgraph_primary_snapshot_max_files)
        self.assertTrue(
            configured.langgraph_primary_configuration_readiness()[
                "snapshot_max_files_valid"
            ]
        )

    def test_primary_snapshot_inventory_limit_rejects_outside_hard_bounds(self):
        for invalid in (0, 4097, True):
            with self.subTest(invalid=invalid):
                settings = replace(
                    self.base_settings(),
                    langgraph_primary_snapshot_max_files=invalid,
                )
                readiness = settings.langgraph_primary_configuration_readiness()
                self.assertFalse(readiness["snapshot_max_files_valid"])
                self.assertFalse(readiness["configuration_valid"])
                with self.assertRaisesRegex(ValueError, "between 1 and 4096"):
                    settings.validate_langgraph_primary_configuration()

    def test_primary_new_intake_cannot_be_requested_while_runtime_is_disabled(self):
        settings = replace(
            self.base_settings(),
            langgraph_primary_enabled=False,
            langgraph_primary_new_intake_enabled=True,
        )

        with self.assertRaisesRegex(ValueError, "requires LANGGRAPH_PRIMARY_ENABLED"):
            settings.validate_langgraph_primary_configuration()

    def test_primary_live_dispatch_requires_paid_runs_and_exact_bindings(self):
        settings = replace(
            self.base_settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            langgraph_primary_postgres_dsn="postgresql://director@example.test/director",
            langgraph_primary_checkpoint_namespace="director-primary-v1",
            langgraph_primary_owner_token="x" * 32,
            repository_snapshot_root="C:/verified/default",
            repository_allowlist=("owner/director",),
            allowed_starting_refs=("main",),
            langgraph_primary_required_providers=("openai",),
        )

        with self.assertRaisesRegex(
            ValueError, "requires ALLOW_PAID_RUNS.*exact provider activation bindings"
        ):
            settings.validate_langgraph_primary_configuration()

    def test_primary_activation_binding_parser_is_exact_and_nonsecret(self):
        payload = {
            "openai": {
                "workspace_id": "default-director",
                "provider_version": "v1",
                "app_version": "v1",
                "runtime_adapter_version": "v1",
                "workspace_version": "v1",
                "binding_version": "v1",
            }
        }
        settings = replace(
            self.base_settings(),
            langgraph_primary_required_providers=("openai",),
            langgraph_primary_provider_activation_bindings_json=__import__("json").dumps(
                payload
            ),
        )

        bindings = settings.primary_provider_activation_bindings()

        self.assertEqual(("openai",), tuple(bindings))
        self.assertEqual("default-director", bindings["openai"].workspace_id)

        malformed = replace(
            settings,
            langgraph_primary_provider_activation_bindings_json='{"openai":{"token":"secret"}}',
        )
        with self.assertRaisesRegex(ValueError, "fields are invalid"):
            malformed.primary_provider_activation_bindings()

    def test_enabled_primary_runtime_requires_complete_isolated_configuration(self):
        settings = replace(
            self.base_settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_postgres_dsn="postgresql://director@example.test/director",
            langgraph_primary_postgres_schema="director_primary_v1",
            langgraph_primary_checkpoint_namespace="director-primary-v1",
            langgraph_primary_owner_token="x" * 32,
            langgraph_runtime_checkpoint_namespace="director-runtime-v1",
            repository_snapshot_root="C:/verified/default",
            repository_allowlist=("owner/director",),
            allowed_starting_refs=("main",),
        )

        settings.validate_langgraph_primary_configuration()
        readiness = settings.langgraph_primary_configuration_readiness()

        self.assertTrue(readiness["configuration_valid"])
        self.assertTrue(readiness["startup_allowed"])
        self.assertFalse(readiness["primary_runtime_active"])

    def test_primary_immutable_authority_pins_are_paired_exact_git_ids(self):
        enabled = replace(
            self.base_settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_postgres_dsn="postgresql://director@example.test/director",
            langgraph_primary_checkpoint_namespace="director-primary-v1",
            langgraph_primary_owner_token="x" * 32,
            repository_snapshot_root="C:/verified/default",
            repository_allowlist=("owner/director",),
            allowed_starting_refs=("main",),
            langgraph_primary_pinned_commit_sha="a" * 40,
            langgraph_primary_pinned_tree_sha="b" * 40,
        )

        enabled.validate_langgraph_primary_configuration()
        self.assertTrue(
            enabled.langgraph_primary_configuration_readiness()[
                "authority_pins_valid"
            ]
        )

        for malformed in (
            replace(enabled, langgraph_primary_pinned_tree_sha=""),
            replace(enabled, langgraph_primary_pinned_commit_sha="not-a-git-id"),
        ):
            with self.assertRaisesRegex(ValueError, "supplied together.*40-character"):
                malformed.validate_langgraph_primary_configuration()

    def test_enabled_primary_runtime_rejects_shared_namespace_and_short_token(self):
        settings = replace(
            self.base_settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_postgres_dsn="postgresql://director@example.test/director",
            langgraph_primary_postgres_schema="director_primary_v1",
            langgraph_primary_checkpoint_namespace="shared-v1",
            langgraph_runtime_checkpoint_namespace="shared-v1",
            langgraph_primary_owner_token="too-short",
            repository_snapshot_root="C:/verified/default",
            repository_allowlist=("owner/director",),
            allowed_starting_refs=("main",),
        )

        with self.assertRaisesRegex(ValueError, "differ from shadow.*32 characters"):
            settings.validate_langgraph_primary_configuration()


# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:25 UTC | END]]
