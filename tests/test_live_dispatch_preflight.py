# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 18:43 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:36 UTC | BEGIN]]
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
    "slack_bot_token": "xoxb-fixture-" + "8" * 32,
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
    "entitlement_postgres_dsn_file": "/fixture-entitlement-dsn",
    "entitlement_trusted_verifier_secret_file": "/fixture-entitlement-trusted-verifier",
    "entitlement_route_verifier_secret_file": "/fixture-entitlement-route-verifier",
    "entitlement_owner_authority_secret_file": "/fixture-entitlement-owner-authority",
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
        entitlement_control_plane_enabled=True,
        trusted_provider_cost_ranks_json=json.dumps({"cursor": 1, "claude": 2, "openai": 3}),
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
            "workload policy:",
            "langgraph primary:",
            "execution profile:",
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

    def test_workload_policy_prerequisite_is_reported(self):
        settings = replace(_complete(), trusted_provider_cost_ranks_json=json.dumps({"cursor": 1}))
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "workload policy"]
        self.assertTrue(any("TRUSTED_PROVIDER_COST_RANKS is missing a rank for selectable primary lane(s): claude, openai" in p for p in problems))

    def test_langgraph_shadow_lane_prerequisite_is_reported(self):
        settings = replace(_complete(), langgraph_shadow_enabled=True, langgraph_checkpoint_path="")
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "langgraph shadow lane"]
        self.assertTrue(any("LANGGRAPH_CHECKPOINT_PATH must be configured" in p for p in problems))

    def test_langgraph_primary_prerequisite_is_reported(self):
        problems = [p for s, p in PREFLIGHT.unmet(_bare()) if s == "langgraph primary"]
        self.assertTrue(any("LANGGRAPH_PRIMARY_OWNER_TOKEN must contain at least 32 characters" in p for p in problems))

    def test_parallel_dispatch_prerequisite_is_reported(self):
        settings = replace(_complete(), langgraph_parallel_external_dispatch_enabled=True, langgraph_parallel_initial_max_concurrency=1)
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "parallel dispatch"]
        self.assertTrue(any("caps must start at 2" in p for p in problems))

    def test_langgraph_runtime_prerequisite_is_reported(self):
        settings = replace(_complete(), langgraph_runtime_enabled=True, langgraph_runtime_checkpoint_path="")
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "langgraph runtime"]
        self.assertTrue(any("LANGGRAPH_RUNTIME_CHECKPOINT_PATH must be configured" in p for p in problems))

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

    def test_the_report_prints_subsystem_readiness_stages(self):
        lines = PREFLIGHT.report(_complete(), [])
        
        self.assertIn("  ENTITLEMENT_CONTROL_PLANE_ENABLED: true", lines)
        self.assertIn("  ENTITLEMENT_POSTGRES_DSN_FILE_PRESENT: true", lines)
        self.assertIn("  ENTITLEMENT_TRUSTED_VERIFIER_SECRET_FILE_PRESENT: true", lines)
        self.assertIn("  ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE_PRESENT: true", lines)
        self.assertIn("  ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE_PRESENT: true", lines)
        self.assertIn("  ENTITLEMENT_SCHEMA_CONFIGURED: true", lines)
        self.assertIn("  EFFECT_GATEWAY_SLACK_CONFIGURED: true", lines)
        self.assertIn("  EFFECT_GATEWAY_GITHUB_CONFIGURED: false", lines)
        self.assertIn("  EFFECT_GATEWAY_ANY_CONFIGURED: true", lines)
        self.assertIn("  HOST_BRIDGE_ENDPOINT_CONFIGURED: false", lines)
        self.assertIn("  HOST_BRIDGE_SECRET_CONFIGURED: false", lines)

        no_slack = PREFLIGHT.report(_complete(slack_bot_token=""), [])
        self.assertIn("  EFFECT_GATEWAY_SLACK_CONFIGURED: false", no_slack)
        self.assertIn("  EFFECT_GATEWAY_ANY_CONFIGURED: false", no_slack)

    def test_incomplete_subsystems_produce_one_problem_each(self):
        settings = _complete(
            entitlement_control_plane_enabled=True,
            entitlement_postgres_dsn_file="",
            entitlement_owner_authority_secret_file="/fixture-owner-authority-secret",
            langgraph_primary_host_bridge_endpoint="https://localhost",
            langgraph_primary_host_bridge_secret="",
        )
        problems = PREFLIGHT.unmet(settings)
        entitlement = [p for s, p in problems if s == "entitlement control plane"]
        self.assertEqual(len(entitlement), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", entitlement[0])
        
        bridge = [p for _, p in problems if "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET" in PREFLIGHT.variables(p)]
        self.assertEqual(len(bridge), 1)
        self.assertIn("configured together", bridge[0])

    def test_effect_gateway_missing_names_slack_bot_token(self):
        problems = PREFLIGHT.unmet(_complete(slack_bot_token=""))
        gateway = [p for s, p in problems if s == "effect gateways"]
        self.assertEqual(len(gateway), 1)
        self.assertIn("SLACK_BOT_TOKEN", gateway[0])

    def test_entitlement_owner_authority_missing_names_variable(self):
        problems = PREFLIGHT.unmet(_complete(entitlement_owner_authority_secret_file=""))
        entitlement = [p for s, p in problems if s == "entitlement control plane"]
        self.assertEqual(len(entitlement), 1)
        self.assertIn("ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE", entitlement[0])

    def test_entitlement_postgres_schema_invalid_names_variable(self):
        problems = PREFLIGHT.unmet(_complete(entitlement_postgres_schema="Bad-Schema"))
        entitlement = [p for s, p in problems if s == "entitlement control plane"]
        self.assertEqual(len(entitlement), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_SCHEMA", entitlement[0])

    def test_entitlement_control_plane_disabled_names_variable(self):
        problems = PREFLIGHT.unmet(_complete(entitlement_control_plane_enabled=False))
        entitlement = [p for s, p in problems if s == "entitlement control plane"]
        self.assertEqual(len(entitlement), 1)
        self.assertIn("ENTITLEMENT_CONTROL_PLANE_ENABLED", entitlement[0])


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
            {
                "__future__", "dataclasses", "pathlib", "re", "sys",
                "director_orchestrator.config",
            },
        )

    def test_pathlib_is_used_to_read_and_never_to_write(self):
        """pathlib earns its place by reading config.py, and nothing more.

        It arrived so the declared-variable set could be derived from the
        source instead of restated by hand. pathlib can also write, so
        admitting it to the import list above is only safe while every use is
        a read: this asserts that directly rather than trusting the intent.
        """

        attributes = {
            node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
        }
        # `replace` is deliberately absent from this list: str.replace and
        # dataclasses.replace are both used here, and an attribute name alone
        # cannot tell them from Path.replace. The names below have no such
        # collision, so their absence is meaningful.
        for mutating in (
            "write_text", "write_bytes", "mkdir", "unlink", "rmdir", "rename",
            "touch", "chmod", "symlink_to", "hardlink_to", "open",
        ):
            self.assertNotIn(mutating, attributes, mutating)
        self.assertIn("read_text", attributes)

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
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                val = node.func.value
                if isinstance(val, ast.Attribute) and val.attr == "environ" and getattr(val.value, "id", "") == "os":
                    self.assertNotIn(node.func.attr, {"update", "setdefault", "pop", "clear"})


# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | END]]


class ConstructionAndVariableDisclosureTests(unittest.TestCase):
    """Three ways a value reached the output, all found by a review of #140.

    This class was also called `SecretDisclosureTests`, which silently replaced
    the earlier class of that name at import: `-m unittest` collected 20 and
    dropped `test_no_configured_value_ever_appears_in_the_output`,
    `test_a_validator_that_leaked_a_value_would_be_redacted` and
    `test_the_report_prints_switch_names_with_booleans_only`, while a direct run
    collected 16 and dropped this class instead. Neither invocation ran every
    test, and the reported "20 pass" was the larger of two incomplete sets.
    """

    def test_a_construction_failure_never_quotes_the_value_that_caused_it(self):
        """Settings interpolates values into its own errors.

        A secret placed in an integer-typed variable came back verbatim in the
        conversion error, and redaction is unavailable on this path because it
        needs the Settings instance that just failed to build. The message is
        withheld entirely.
        """

        import subprocess
        import sys

        secret = "SYNTHETIC_SECRET_VALUE_1234567890"
        environment = {
            **os.environ,
            "LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES": secret,
            "ORCHESTRATOR_SHARED_SECRET": secret,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "live-dispatch-preflight.py")],
            capture_output=True, text=True, env=environment, cwd=str(ROOT), timeout=120,
        )
        self.assertNotIn(secret, completed.stdout)
        self.assertNotIn(secret, completed.stderr)
        self.assertIn("withheld", completed.stdout)
        self.assertNotEqual(completed.returncode, 0)

    def test_a_secret_shaped_like_a_variable_name_is_not_echoed_as_one(self):
        """Redaction cleaned the prose and the name list reprinted the token.

        `variables()` matched on shape, so an uppercase underscore-delimited
        secret survived as a reported "variable" after being redacted out of
        the message beside it.
        """

        found = PREFLIGHT.variables(
            "LANGGRAPH_PRIMARY_OWNER_TOKEN rejected SYNTHETIC_SECRET_VALUE_1234567890"
        )
        self.assertEqual(found, ("LANGGRAPH_PRIMARY_OWNER_TOKEN",))

    def test_narrowing_the_declared_set_would_drop_real_variables(self):
        """The first fix for the above silently stopped reporting real names.

        config.py reads through helpers as well as os.getenv, so matching only
        os.getenv dropped PROJECT_ALLOWLIST and its siblings from every report.
        """

        for name in (
            "PROJECT_ALLOWLIST",
            "REPOSITORY_ALLOWLIST",
            "ALLOWED_STARTING_REFS",
            "CLAUDE_CALLBACK_SECRET",
        ):
            self.assertIn(name, PREFLIGHT.DECLARED_VARIABLES, name)
            self.assertEqual(PREFLIGHT.variables(f"{name} must be configured"), (name,))

    def test_a_short_secret_is_redacted(self):
        """The floor was six characters, so a five-character secret printed."""

        settings = _complete(claude_callback_secret="s3crt")
        self.assertEqual(
            PREFLIGHT.redact("CLAUDE_CALLBACK_SECRET rejected s3crt", settings),
            "CLAUDE_CALLBACK_SECRET rejected <redacted>",
        )
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:36 UTC | END]]


class RemainingDisclosurePathsTests(unittest.TestCase):
    """Three more ways a value escaped, found by the delta review of #140."""

    def test_an_import_time_failure_never_quotes_the_value(self):
        """config.py evaluates field defaults at class-definition time.

        A value that fails to convert therefore raises during the *import* of
        Settings, before main() can guard it, and the unhandled traceback
        printed that value on stderr. MAX_AGENT_HOPS was the reproduction.
        """

        import subprocess
        import sys

        secret = "SYNTHETIC_SECRET_VALUE_1234567890"
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "live-dispatch-preflight.py")],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
            env={
                **os.environ,
                "MAX_AGENT_HOPS": secret,
                "ORCHESTRATOR_SHARED_SECRET": secret,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.assertNotIn(secret, completed.stdout)
        self.assertNotIn(secret, completed.stderr)
        self.assertNotEqual(completed.returncode, 0)

    def test_a_one_or_two_character_secret_withholds_the_whole_message(self):
        """Substitution cannot redact "Z"; replacing every Z shreds the text.

        The length floor skipped them entirely, so they printed. The line is
        withheld instead -- pathological configuration, and a lost diagnostic
        beats a printed credential.
        """

        for tiny in ("Z", "Z9"):
            settings = _complete(claude_callback_secret=tiny)
            result = PREFLIGHT.redact(f"CLAUDE_CALLBACK_SECRET rejected {tiny}", settings)
            self.assertIn("withheld", result, tiny)
            self.assertNotIn(f"rejected {tiny}", result, tiny)

    def test_a_secret_equal_to_a_declared_variable_name_is_not_reprinted(self):
        """Variables were extracted from the raw problem, not the redacted one.

        A secret set literally to "PROJECT_ALLOWLIST" was scrubbed from the
        prose and then reprinted intact under "variables", because the name is
        genuinely declared. Extraction now runs on the redacted text.
        """

        settings = _complete(claude_callback_secret="PROJECT_ALLOWLIST")
        lines = PREFLIGHT.report(
            settings, [("test", "CLAUDE_CALLBACK_SECRET rejected PROJECT_ALLOWLIST")]
        )
        body = "\n".join(lines)
        self.assertIn("<redacted>", body)
        variables_line = [line for line in lines if "variables:" in line]
        self.assertEqual(len(variables_line), 1)
        self.assertNotIn("PROJECT_ALLOWLIST", variables_line[0])
        self.assertIn("CLAUDE_CALLBACK_SECRET", variables_line[0])
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:56 UTC | END]]




class CollectionIntegrityTests(unittest.TestCase):
    """Both invocations must collect the same tests, and all of them.

    This file has shipped two distinct collection defects. `unittest.main()`
    sat above two classes, so a direct run skipped them; and two classes shared
    the name `SecretDisclosureTests`, so the second replaced the first at import
    and discovery silently dropped three tests. `-m unittest` reported 20,
    a direct run reported 16, and neither was the whole file. A reported pass
    count is worthless when the two paths disagree.
    """

    def test_no_test_case_is_defined_below_the_runner(self):
        source = (ROOT / "tests" / "test_live_dispatch_preflight.py").read_text(
            encoding="utf-8"
        )
        runner = source.index('if __name__ == "__main__":')
        self.assertNotIn("\nclass ", source[runner:])

    def test_no_two_test_classes_share_a_name(self):
        import ast as _ast

        tree = _ast.parse(
            (ROOT / "tests" / "test_live_dispatch_preflight.py").read_text(
                encoding="utf-8"
            )
        )
        names = [
            node.name for node in tree.body
            if isinstance(node, _ast.ClassDef)
            and any(
                isinstance(base, _ast.Attribute) and base.attr == "TestCase"
                for base in node.bases
            )
        ]
        duplicates = {name for name in names if names.count(name) > 1}
        self.assertEqual(duplicates, set(), f"shadowed classes: {duplicates}")

        # No count is asserted and discovery is not re-invoked here. A
        # `loadTestsFromName` cross-check looked stronger and was not: under a
        # direct run the module is already `__main__`, so loading it again by
        # dotted name produces a second module object and the comparison
        # reports classes missing that are present. Together with the previous
        # test -- nothing defined below the runner -- these two AST facts are
        # what make the two invocations agree, and both hold whichever way the
        # file is run.


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 18:43 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]]
