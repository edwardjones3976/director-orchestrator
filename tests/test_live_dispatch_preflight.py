# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | BEGIN]]
"""Contracts for the read-only live-dispatch preflight reporter."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings


ROOT = Path(__file__).resolve().parents[1]
BINDING_FIELDS = (
    "workspace_id",
    "provider_version",
    "app_version",
    "runtime_adapter_version",
    "workspace_version",
    "binding_version",
)
# Distinctive fixture values. None of them may appear in the output.
SECRETS = {
    "langgraph_primary_adapter_callback_secret": "adaptercallback" + "0" * 32,
    "langgraph_primary_independent_review_evidence_secret": "review" + "1" * 32,
    "langgraph_primary_ci_evidence_secret": "cievidence" + "2" * 32,
    "langgraph_primary_security_evidence_secret": "security" + "3" * 32,
    "langgraph_primary_effect_reconciliation_secret": "reconcile" + "4" * 32,
    "langgraph_primary_owner_token": "ownertoken" + "5" * 32,
    "claude_callback_secret": "claudecallback" + "6" * 32,
    "shared_secret": "sharedsecret" + "7" * 32,
    "langgraph_primary_postgres_dsn": "postgresql://fixtureuser:fixturepassword@db/primary",
    "repository_snapshot_root": "/fixture-snapshot-root",
    "cursor_api_key": "cursorapikeyfixture",
}


def _preflight():
    spec = importlib.util.spec_from_file_location(
        "live_dispatch_preflight", ROOT / "scripts" / "live-dispatch-preflight.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREFLIGHT = _preflight()


def _bare() -> Settings:
    """Settings as a deployment with nothing configured would build them."""

    with patch.dict(os.environ, {"SQLITE_PATH": "data/director.sqlite3"}, clear=True):
        return Settings()


def _complete(**overrides) -> Settings:
    """Settings with every live-dispatch prerequisite satisfied."""

    bindings = {
        lane: {field: f"{lane}-v1" for field in BINDING_FIELDS}
        for lane in ("openai", "claude", "cursor")
    }
    allowlist = {
        kind: {
            "issuers": [f"{kind}-issuer"],
            "principals": [f"{kind}-principal"],
        }
        for kind in ("independent_review", "ci", "security")
    }
    values = dict(
        allow_paid_runs=True,
        langgraph_primary_enabled=True,
        langgraph_primary_new_intake_enabled=True,
        langgraph_primary_live_provider_dispatch_enabled=True,
        trusted_provider_cost_ranks_json=json.dumps({"cursor": 1}),
        langgraph_primary_provider_activation_bindings_json=json.dumps(bindings),
        langgraph_primary_completion_evidence_actor_allowlist_json=json.dumps(
            allowlist
        ),
        project_allowlist=("fixture-project",),
        repository_allowlist=("fixture-owner/fixture-repository",),
        allowed_starting_refs=("main",),
        **SECRETS,
    )
    values.update(overrides)
    return replace(_bare(), **values)


class UnsetEnvironmentTests(unittest.TestCase):
    def test_every_prerequisite_is_reported_at_once(self):
        problems = PREFLIGHT.unmet(_bare())
        reported = {problem for _, problem in problems}
        for name in (
            "ALLOW_PAID_RUNS",
            "LANGGRAPH_PRIMARY_ENABLED",
            "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
        ):
            self.assertIn(f"{name} must be enabled", reported)
        joined = "\n".join(f"{stage}: {problem}" for stage, problem in problems)
        for expected in (
            "TRUSTED_PROVIDER_COST_RANKS",
            "provider activation bindings",
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
            "completion authority evidence secrets",
            "completion evidence actor allowlist",
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
            "LANGGRAPH_PRIMARY_POSTGRES_DSN",
            "LANGGRAPH_PRIMARY_OWNER_TOKEN",
            "REPOSITORY_SNAPSHOT_ROOT",
            "REPOSITORY_ALLOWLIST",
            "PROJECT_ALLOWLIST",
            "CLAUDE_CALLBACK_SECRET",
        ):
            self.assertIn(expected, joined)

    def test_exit_status_is_nonzero(self):
        with patch.object(PREFLIGHT, "Settings", lambda: _bare()):
            with patch("builtins.print"):
                self.assertEqual(PREFLIGHT.main(), 1)

    def test_every_reported_problem_names_its_variables(self):
        for _, problem in PREFLIGHT.unmet(_bare()):
            self.assertTrue(
                PREFLIGHT.variables(problem),
                f"no environment variable name for: {problem}",
            )


class CompleteEnvironmentTests(unittest.TestCase):
    def test_nothing_is_outstanding(self):
        self.assertEqual(PREFLIGHT.unmet(_complete()), [])

    def test_exit_status_is_zero(self):
        with patch.object(PREFLIGHT, "Settings", lambda: _complete()):
            with patch("builtins.print"):
                self.assertEqual(PREFLIGHT.main(), 0)

    def test_report_states_that_configuration_is_not_authority(self):
        lines = PREFLIGHT.report(_complete(), [])
        self.assertIn(
            "acceptance, evidence, and owner approval are separate gates.", lines
        )


class CouplingTests(unittest.TestCase):
    def test_paid_runs_alone_is_reported_as_the_crash_inducing_state(self):
        settings = _complete(
            langgraph_primary_live_provider_dispatch_enabled=False,
        )
        coupling = [
            problem
            for stage, problem in PREFLIGHT.unmet(settings)
            if stage == "paid/live coupling"
        ]
        self.assertEqual(len(coupling), 1)
        self.assertIn("ALLOW_PAID_RUNS is enabled", coupling[0])
        self.assertIn(
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED is disabled",
            coupling[0],
        )
        self.assertIn("the deployment is down", coupling[0])

    def test_paid_runs_off_reports_no_coupling_failure(self):
        settings = _complete(
            allow_paid_runs=False,
            langgraph_primary_live_provider_dispatch_enabled=False,
        )
        self.assertNotIn(
            "paid/live coupling", {stage for stage, _ in PREFLIGHT.unmet(settings)}
        )

    def test_offline_simulation_with_live_dispatch_is_mutually_exclusive(self):
        settings = _complete(langgraph_primary_offline_simulation_enabled=True)
        problems = [problem for _, problem in PREFLIGHT.unmet(settings)]
        self.assertIn(
            "offline simulation and live provider dispatch are mutually exclusive",
            problems,
        )
        self.assertEqual(
            PREFLIGHT.variables(
                "offline simulation and live provider dispatch are mutually exclusive"
            ),
            (
                "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
                "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
            ),
        )


class SecretDisclosureTests(unittest.TestCase):
    def _outputs(self, settings: Settings) -> str:
        return "\n".join(PREFLIGHT.report(settings, PREFLIGHT.unmet(settings)))

    def test_no_configured_value_ever_appears_in_the_output(self):
        for settings in (_complete(), _complete(langgraph_primary_enabled=False)):
            output = self._outputs(settings)
            for name, value in SECRETS.items():
                self.assertNotIn(value, output, name)
            for entry in settings.project_allowlist + settings.repository_allowlist:
                self.assertNotIn(entry, output)

    def test_a_validator_that_leaked_a_value_would_be_redacted(self):
        settings = _complete()
        leaked = f"LANGGRAPH_PRIMARY_OWNER_TOKEN is {settings.langgraph_primary_owner_token}"
        redacted = PREFLIGHT.redact(leaked, settings)
        self.assertNotIn(settings.langgraph_primary_owner_token, redacted)
        self.assertIn("LANGGRAPH_PRIMARY_OWNER_TOKEN", redacted)

    def test_the_report_prints_switch_names_with_booleans_only(self):
        lines = PREFLIGHT.report(_complete(), PREFLIGHT.unmet(_complete()))
        for line in lines:
            if line.startswith("  ") and ": " in line and line.strip()[0].isupper():
                self.assertRegex(line.strip(), r"^[A-Z][A-Z0-9_]+: (true|false)$")


class ReadOnlyTests(unittest.TestCase):
    """The reporter may not acquire a way to write, connect, or enable."""

    def setUp(self):
        self.tree = ast.parse(
            (ROOT / "scripts" / "live-dispatch-preflight.py").read_text(
                encoding="utf-8"
            )
        )

    def test_it_imports_nothing_that_can_write_connect_or_dispatch(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertEqual(
            imported,
            {"__future__", "dataclasses", "re", "sys", "director_orchestrator.config"},
        )

    def test_it_calls_nothing_that_opens_a_file_or_a_process(self):
        called = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(
            called & {"open", "exec", "eval", "compile", "__import__", "setattr"}
        )

    def test_it_never_assigns_to_the_environment_or_to_settings(self):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    self.assertNotIsInstance(target, ast.Subscript)
                    self.assertNotIsInstance(target, ast.Attribute)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | END]]
