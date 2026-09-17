# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | BEGIN]]
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
import atexit
import importlib.util
import json
import os
import shutil
import tempfile
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
# Entitlement prerequisites are real files at startup (an existing DSN file of
# at most 65536 bytes; three secret files of at least 32 bytes), so the
# complete fixture provides real files in a private temporary directory.
_ENTITLEMENT_DIR = Path(tempfile.mkdtemp(prefix="preflight-entitlement-"))
atexit.register(shutil.rmtree, _ENTITLEMENT_DIR, True)


# The one connection target the complete fixture uses for both the primary
# store and the entitlement DSN file: startup parses both with
# _parse_connection_target (explicit port, user, dbname, a verifying sslmode
# with sslrootcert) and requires the same database and role.
FIXTURE_DSN = "postgresql://fixtureuser:fixturepassword@db:5432/primary?sslmode=verify-full&sslrootcert=/fixture-ca.pem"


def _entitlement_file(name: str, content: str) -> str:
    path = _ENTITLEMENT_DIR / name
    path.write_text(content, encoding="utf-8")
    return str(path)


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
    "langgraph_primary_postgres_dsn": FIXTURE_DSN,
    "repository_snapshot_root": "/fixture-snapshot-root",
    "cursor_api_key": "cursorapikeyfixture",
    "entitlement_postgres_dsn_file": _entitlement_file(
        "dsn", FIXTURE_DSN
    ),
    "entitlement_trusted_verifier_secret_file": _entitlement_file(
        "trusted-verifier", "trustedverifierfixture" + "9abcdefghijklmnop" * 2
    ),
    "entitlement_route_verifier_secret_file": _entitlement_file(
        "route-verifier", "routeverifierfixture" + "9abcdefghijklmnop" * 2
    ),
    "entitlement_owner_authority_secret_file": _entitlement_file(
        "owner-authority", "ownerauthorityfixture" + "9abcdefghijklmnop" * 2
    ),
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

# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]]
    def test_an_enabled_credentialed_lane_missing_from_the_roster_is_reported(self):
        settings = replace(
            _complete(), grok_enabled=True, grok_api_key="fixture-key",
            primary_team=("openai", "claude", "cursor"),
            usage_check_providers=("openai", "claude", "cursor"),
        )
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "provider roster"]
        self.assertEqual(len(problems), 1)
        self.assertIn("GROK_ENABLED is enabled and XAI_API_KEY is configured", problems[0])
        self.assertIn("PRIMARY_TEAM and USAGE_CHECK_PROVIDERS", problems[0])
        self.assertNotIn("fixture-key", problems[0])
        self.assertEqual(
            PREFLIGHT.variables(problems[0]),
            ("GROK_ENABLED", "XAI_API_KEY", "PRIMARY_TEAM", "USAGE_CHECK_PROVIDERS"),
        )

    def test_a_lane_missing_from_one_roster_list_still_names_all_four_variables(self):
        settings = replace(
            _complete(), grok_enabled=True, grok_api_key="fixture-key",
            primary_team=("openai", "claude", "cursor", "grok"),
            usage_check_providers=("openai", "claude", "cursor"),
        )
        problems = [p for s, p in PREFLIGHT.unmet(settings) if s == "provider roster"]
        self.assertEqual(len(problems), 1)
        self.assertIn("absent from USAGE_CHECK_PROVIDERS:", problems[0])
        # Order follows first mention in the text; the set is what matters.
        self.assertEqual(
            set(PREFLIGHT.variables(problems[0])),
            {"GROK_ENABLED", "XAI_API_KEY", "PRIMARY_TEAM", "USAGE_CHECK_PROVIDERS"},
        )

    def test_every_roster_problem_names_its_variables(self):
        # _bare() leaves every lane disabled, so the generic every-problem test
        # never reaches this stage; drive it with a drifted environment.
        drifted = replace(
            _complete(), grok_enabled=True, grok_api_key="fixture-key",
            perplexity_enabled=True, perplexity_api_key="fixture-key",
            primary_team=("openai",), usage_check_providers=("claude",),
        )
        problems = [p for s, p in PREFLIGHT.unmet(drifted) if s == "provider roster"]
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertTrue(PREFLIGHT.variables(problem), problem)
            self.assertNotIn("fixture-key", problem)

    def test_a_rostered_or_disabled_lane_raises_no_roster_problem(self):
        rostered = replace(
            _complete(), grok_enabled=True, grok_api_key="fixture-key",
            primary_team=("openai", "claude", "cursor", "grok"),
            usage_check_providers=("openai", "claude", "cursor", "grok"),
        )
        disabled = replace(_complete(), grok_enabled=False, grok_api_key="fixture-key")
        no_key = replace(_complete(), grok_enabled=True, grok_api_key="")
        for settings in (rostered, disabled, no_key):
            self.assertEqual([p for s, p in PREFLIGHT.unmet(settings) if s == "provider roster"], [])
# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]]

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
        self.assertIn("  EFFECT_GATEWAY_GITHUB_APP_SYNTAX_OK: true", lines)
        self.assertIn("  EFFECT_GATEWAY_ANY_CONFIGURED: true", lines)
        self.assertIn("  HOST_BRIDGE_ENDPOINT_CONFIGURED: false", lines)
        self.assertIn("  HOST_BRIDGE_SECRET_CONFIGURED: false", lines)

        no_slack = PREFLIGHT.report(_complete(slack_bot_token=""), [])
        self.assertIn("  EFFECT_GATEWAY_SLACK_CONFIGURED: false", no_slack)
        self.assertIn("  EFFECT_GATEWAY_ANY_CONFIGURED: false", no_slack)

    def test_incomplete_subsystems_produce_one_problem_each(self):
        settings = _complete(
            entitlement_postgres_dsn_file="",
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

    def test_dependent_entitlement_checks_run_while_the_plane_is_disabled(self):
        # One pass reports every prerequisite: the switch AND the file the
        # operator would otherwise discover one enable later.
        problems = self._entitlement_problems(
            entitlement_control_plane_enabled=False, entitlement_postgres_dsn_file=""
        )
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("ENTITLEMENT_CONTROL_PLANE_ENABLED" in p for p in problems))
        self.assertTrue(any("ENTITLEMENT_POSTGRES_DSN_FILE" in p for p in problems))

    def _entitlement_problems(self, **overrides):
        problems = PREFLIGHT.unmet(_complete(**overrides))
        return [p for s, p in problems if s == "entitlement control plane"]

    def test_each_entitlement_secret_file_is_required_by_name(self):
        for attribute, name in (
            ("entitlement_trusted_verifier_secret_file", "ENTITLEMENT_TRUSTED_VERIFIER_SECRET_FILE"),
            ("entitlement_route_verifier_secret_file", "ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE"),
            ("entitlement_owner_authority_secret_file", "ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE"),
        ):
            with self.subTest(attribute=attribute):
                problems = self._entitlement_problems(**{attribute: ""})
                self.assertEqual(len(problems), 1)
                self.assertIn(name, problems[0])

    def test_entitlement_files_must_exist_within_startup_bounds(self):
        # A configured path is not a file: startup reads these, so the
        # preflight checks existence and size (never the contents).
        missing = str(_ENTITLEMENT_DIR / "does-not-exist")
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=missing)
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])
        report = PREFLIGHT.report(_complete(entitlement_postgres_dsn_file=missing), [])
        self.assertIn("  ENTITLEMENT_POSTGRES_DSN_FILE_PRESENT: false", report)

        oversized = _entitlement_file("dsn-oversized", "x" * 65_537)
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=oversized)
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])

        short = _entitlement_file("short-secret", "tooshort")
        problems = self._entitlement_problems(entitlement_route_verifier_secret_file=short)
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE", problems[0])
        for problem in problems:
            self.assertNotIn(short, problem)

    def test_entitlement_secret_contents_are_validated_like_startup(self):
        # _read_secret_file / _validate_secret_material: stripped UTF-8 text,
        # at least 32 bytes, at least 16 distinct byte values. The material is
        # never echoed.
        cases = {
            "blank": " " * 40,
            "low-entropy": "a" * 40,
            "short-after-strip": "  " + "9abcdefghijklmnop" + "  ",
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                path = _entitlement_file(f"secret-{label}", content)
                problems = self._entitlement_problems(entitlement_owner_authority_secret_file=path)
                self.assertEqual(len(problems), 1)
                self.assertIn("ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE", problems[0])
                self.assertNotIn(path, problems[0])
                self.assertNotIn(content.strip() or "blank", problems[0])
        invalid = _ENTITLEMENT_DIR / "secret-invalid-utf8"
        invalid.write_bytes(b"\xff\xfe" + b"x" * 40)
        problems = self._entitlement_problems(entitlement_owner_authority_secret_file=str(invalid))
        self.assertEqual(len(problems), 1)
        self.assertIn("UTF-8", problems[0])

    def test_entitlement_dsn_contents_are_checked_before_startup_parses_them(self):
        # _parse_connection_target refuses empty input and line breaks before
        # psycopg ever sees it; the preflight applies those two rules and
        # leaves the full parse to startup.
        empty = _entitlement_file("dsn-empty", "   \n")
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=empty)
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])
        self.assertIn("empty", problems[0])
        multiline = _entitlement_file("dsn-multiline", FIXTURE_DSN + "\nhost=other")
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=multiline)
        self.assertEqual(len(problems), 1)
        self.assertIn("one line", problems[0])
        trailing_newline = _entitlement_file("dsn-trailing", FIXTURE_DSN + "\n")
        self.assertEqual(self._entitlement_problems(entitlement_postgres_dsn_file=trailing_newline), [])

    def test_entitlement_dsn_is_parsed_and_compared_exactly_as_startup_does(self):
        # _parse_connection_target and the storage-identity digest are the
        # same functions configured_provider_budget_routing applies; the
        # contents never reach a problem string.
        junk = _entitlement_file("dsn-junk", "not-a-postgres-dsn")
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=junk)
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])
        self.assertIn("startup accepts", problems[0])
        self.assertNotIn("not-a-postgres-dsn", problems[0])

        weak = _entitlement_file("dsn-weak", FIXTURE_DSN.replace("verify-full", "require"))
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=weak)
        self.assertEqual(len(problems), 1)
        self.assertIn("startup accepts", problems[0])

        other = _entitlement_file("dsn-other-db", FIXTURE_DSN.replace("/primary?", "/other?"))
        problems = self._entitlement_problems(entitlement_postgres_dsn_file=other)
        self.assertEqual(len(problems), 1)
        self.assertIn("share database and role", problems[0])
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])
        self.assertIn("LANGGRAPH_PRIMARY_POSTGRES_DSN", problems[0])
        self.assertNotIn("fixturepassword", "\n".join(problems))

        problems = self._entitlement_problems(langgraph_primary_postgres_dsn="postgresql://fixtureuser:fixturepassword@db/primary")
        self.assertEqual(len(problems), 1)
        self.assertIn("LANGGRAPH_PRIMARY_POSTGRES_DSN", problems[0])
        self.assertNotIn("fixturepassword", problems[0])

    def test_unresolvable_home_prefix_reads_as_an_unavailable_file(self):
        # Path.expanduser raises RuntimeError for ~missing-user; that must be
        # reported as a missing file, not escape as a traceback.
        problems = self._entitlement_problems(
            entitlement_route_verifier_secret_file="~preflight-no-such-user-fixture/secret"
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE", problems[0])
        report = PREFLIGHT.report(
            _complete(entitlement_route_verifier_secret_file="~preflight-no-such-user-fixture/secret"), []
        )
        self.assertIn("  ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE_PRESENT: false", report)

    def test_entitlement_schema_uses_the_runtime_ascii_pattern(self):
        # A \\d pattern would admit Unicode digits that entitlement_control_plane rejects.
        problems = self._entitlement_problems(entitlement_postgres_schema="example_v\u0661")
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_SCHEMA", problems[0])
        self.assertEqual(self._entitlement_problems(entitlement_postgres_schema="example_v1"), [])

    def test_filesystem_errors_read_as_unavailable_files_and_never_escape(self):
        # A permission failure or a removal between the two metadata calls must
        # neither abort the report nor print the configured path.
        from pathlib import Path

        for method in ("is_file", "stat"):
            with self.subTest(method=method):
                def refuse(self_, *args, **kwargs):
                    raise PermissionError(13, "denied", "/never-printed")
                with patch.object(Path, method, refuse):
                    all_problems = PREFLIGHT.unmet(_complete())
                    report = PREFLIGHT.report(_complete(), all_problems)
                problems = [p for s, p in all_problems if s == "entitlement control plane"]
                self.assertEqual(len(problems), 4)
                self.assertTrue(all("must name an existing file" in p for p in problems))
                self.assertIn("  ENTITLEMENT_POSTGRES_DSN_FILE_PRESENT: false", report)
                self.assertNotIn("never-printed", "\n".join(report))
                self.assertNotIn(SECRETS["entitlement_postgres_dsn_file"], "\n".join(report))

    def test_dsn_path_is_not_expanded_but_secret_paths_are(self):
        # configured_provider_budget_routing opens Path(dsn_file) literally;
        # _read_secret_file expands ~. The preflight mirrors each caller.
        home = {"HOME": str(_ENTITLEMENT_DIR), "USERPROFILE": str(_ENTITLEMENT_DIR)}
        with patch.dict(os.environ, home):
            problems = self._entitlement_problems(
                entitlement_postgres_dsn_file="~/dsn",
                entitlement_trusted_verifier_secret_file="~/trusted-verifier",
            )
        self.assertEqual(len(problems), 1)
        self.assertIn("ENTITLEMENT_POSTGRES_DSN_FILE", problems[0])

    def test_github_app_credential_syntax_is_checked_like_startup(self):
        def gateway_problems(**overrides):
            problems = PREFLIGHT.unmet(_complete(**overrides))
            return [p for s, p in problems if s == "effect gateways"]

        valid = dict(
            oracle_automerge_github_app_id="12345",
            oracle_automerge_github_installation_id="67890",
            oracle_automerge_github_app_private_key_b64="QUJDRA==",
        )
        self.assertEqual(gateway_problems(**valid), [])
        report = PREFLIGHT.report(_complete(**valid), [])
        self.assertIn("  EFFECT_GATEWAY_GITHUB_CONFIGURED: true", report)
        self.assertIn("  EFFECT_GATEWAY_GITHUB_APP_SYNTAX_OK: true", report)

        # An incomplete App configuration is skipped by the effect registry when
        # Slack is configured (startup builds Slack only), so it is not a problem.
        partial = dict(valid, oracle_automerge_github_installation_id="", oracle_automerge_github_app_private_key_b64="")
        self.assertEqual(gateway_problems(**partial), [])
        report = PREFLIGHT.report(_complete(**partial), [])
        self.assertIn("  EFFECT_GATEWAY_GITHUB_CONFIGURED: false", report)
        self.assertIn("  EFFECT_GATEWAY_GITHUB_APP_SYNTAX_OK: true", report)

        for broken in (
            dict(valid, oracle_automerge_github_app_id="app-id"),
            dict(valid, oracle_automerge_github_installation_id="12 34"),
            dict(valid, oracle_automerge_github_app_private_key_b64="not base64!"),
            dict(valid, oracle_automerge_github_app_private_key_b64="QUJDR"),
        ):
            with self.subTest(broken=sorted(k for k in broken if broken[k] != valid[k])):
                problems = gateway_problems(**broken)
                self.assertEqual(len(problems), 1)
                self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_ID", problems[0])
                self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64", problems[0])
                report = PREFLIGHT.report(_complete(**broken), [])
                self.assertIn("  EFFECT_GATEWAY_GITHUB_APP_SYNTAX_OK: false", report)


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
                # Pure parsing and digest helpers, the same ones startup
                # applies to the entitlement DSN; the assertion below pins
                # that nothing in this file can reach for a connection.
                "psycopg.conninfo",
                "director_orchestrator.entitlement_control_plane",
                "director_orchestrator.langgraph_postgres",
            },
        )
        names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        for forbidden in ("connect", "connection", "Connection", "ConnectionPool", "Pool", "cursor", "execute"):
            self.assertNotIn(forbidden, names | attributes, forbidden)

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
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]]
