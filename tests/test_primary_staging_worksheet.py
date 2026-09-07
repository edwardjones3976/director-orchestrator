# [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:05 UTC | BEGIN]]
"""Drift tests binding ``docs/primary-staging-worksheet.md`` to the code it describes.

The worksheet tells an operator how ``provider_activation_bindings_configured``
is computed and which variable names the primary control script demands. Both
statements are re-derived here from ``config.py``, ``primary_roster.py`` and the
script itself, so a code change that silently invalidates the worksheet fails a
test instead of misleading the next staging pass. Names only; no values.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.primary_roster import PRIMARY_TEAM, core_inventory_ready


ROOT = Path(__file__).resolve().parents[1]
WORKSHEET = ROOT / "docs" / "primary-staging-worksheet.md"
CONTROL_SCRIPT = ROOT / "scripts" / "windows-langgraph-primary-control.ps1"
CONFIG_SOURCE = ROOT / "src" / "director_orchestrator" / "config.py"
ACTIVATION_SOURCE = ROOT / "src" / "director_orchestrator" / "primary_provider_activation.py"
TRANSPORTS_SOURCE = ROOT / "src" / "director_orchestrator" / "primary_transports.py"

BINDING_FIELDS = (
    "workspace_id",
    "provider_version",
    "app_version",
    "runtime_adapter_version",
    "workspace_version",
    "binding_version",
)
SECTION_2 = "## Section 2"
INVENTORY = "## After Pass 2 - activation staging inventory (names only)"
NAME_PATTERN = re.compile(r'"([A-Z][A-Z0-9_]{2,})"')


def _binding(provider_id: str) -> dict[str, str]:
    return {
        field: f"{provider_id}-v{index}"
        for index, field in enumerate(BINDING_FIELDS, start=1)
    }


def _settings(
    bindings: dict[str, dict[str, str]],
    required: tuple[str, ...] | None = None,
) -> Settings:
    with patch.dict(
        os.environ,
        {"APP_ENV": "local", "SQLITE_PATH": "data/director.sqlite3"},
        clear=True,
    ):
        settings = Settings()
    overrides = {
        "langgraph_primary_provider_activation_bindings_json": json.dumps(bindings)
    }
    if required is not None:
        overrides["langgraph_primary_required_providers"] = required
    return replace(settings, **overrides)


def _bindings_configured(bindings, required=None) -> bool:
    readiness = _settings(bindings, required).langgraph_primary_configuration_readiness()
    return readiness["provider_activation_bindings_configured"]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
    end = start + len(heading) + following.start() if following else len(text)
    return text[start:end]


def _flat(text: str) -> str:
    """Collapse Markdown line wrapping so assertions read like prose."""

    return re.sub(r"\s+", " ", text)


def _script_names(block_start: str, block_end: str) -> set[str]:
    source = CONTROL_SCRIPT.read_text(encoding="utf-8")
    start = source.index(block_start)
    end = source.index(block_end, start)
    return set(NAME_PATTERN.findall(source[start:end]))


class CoreInventoryRuleTests(unittest.TestCase):
    def test_core_team_is_the_four_named_lanes(self):
        self.assertEqual(PRIMARY_TEAM, frozenset({"openai", "claude", "cursor", "grok"}))

    def test_core_inventory_needs_openai_and_three_core_lanes(self):
        self.assertTrue(core_inventory_ready(("openai", "claude", "cursor")))
        self.assertTrue(
            core_inventory_ready(("openai", "claude", "cursor", "grok", "antigravity"))
        )
        self.assertFalse(core_inventory_ready(("openai", "claude")))
        self.assertFalse(core_inventory_ready(("claude", "cursor", "grok")))
        self.assertFalse(core_inventory_ready(()))


class BindingSignalTests(unittest.TestCase):
    def test_core_pool_map_configures_the_signal_without_the_full_roster(self):
        core = {lane: _binding(lane) for lane in ("openai", "claude", "cursor")}
        self.assertTrue(_bindings_configured(core))
        four = {lane: _binding(lane) for lane in ("openai", "claude", "cursor", "grok")}
        self.assertTrue(_bindings_configured(four))

    def test_empty_map_is_present_but_not_configured(self):
        self.assertFalse(_bindings_configured({}))

    def test_map_without_openai_or_with_two_lanes_is_not_configured(self):
        self.assertFalse(
            _bindings_configured({lane: _binding(lane) for lane in ("claude", "cursor", "grok")})
        )
        self.assertFalse(
            _bindings_configured({lane: _binding(lane) for lane in ("openai", "claude")})
        )

    def test_bound_provider_outside_required_roster_is_not_configured(self):
        core = {lane: _binding(lane) for lane in ("openai", "claude", "cursor", "grok")}
        self.assertFalse(_bindings_configured(core, required=("openai", "claude", "cursor")))
        stray = {**core, "not-a-lane": _binding("not-a-lane")}
        self.assertFalse(_bindings_configured(stray))

    def test_empty_field_is_not_configured(self):
        core = {lane: _binding(lane) for lane in ("openai", "claude", "cursor")}
        core["cursor"] = {**core["cursor"], "binding_version": ""}
        self.assertFalse(_bindings_configured(core))

    def test_code_shape_matches_worksheet_claims(self):
        config_source = CONFIG_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "activation_bindings_configured = bound_pool.issubset(configured_pool)"
            " and core_inventory_ready(tuple(bound_pool))",
            config_source,
        )
        activation_source = ACTIVATION_SOURCE.read_text(encoding="utf-8")
        self.assertIn("app_live = app_live and readiness.app.live_ready", activation_source)
        self.assertIn(
            "adapter_live = adapter_live and readiness.runtime_adapter.live_ready",
            activation_source,
        )


class WorksheetWordingTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKSHEET.read_text(encoding="utf-8")

    def test_section_2_states_the_core_pool_rule(self):
        section = _flat(_section(self.text, SECTION_2))
        self.assertIn("`primary_roster.core_inventory_ready`", section)
        self.assertIn(
            "`openai` present and at least three of `openai`, `claude`, `cursor`, `grok`",
            section,
        )
        self.assertIn("core-pool-or-nothing", section)
        self.assertIn("AND-ed across all configured bindings", section)
        self.assertNotIn("exactly equal the required roster", self.text)
        self.assertNotIn("all-or-nothing", self.text)

    def test_inventory_lists_every_name_the_control_script_requires(self):
        inventory = _section(self.text, INVENTORY)
        always = _script_names("$required = @(", "$acceptanceMode =")
        by_mode = _script_names("switch -CaseSensitive ($acceptanceMode)", "default {")
        distinct = _script_names("foreach ($secret in @(", "))")
        self.assertGreaterEqual(len(always), 20)
        self.assertEqual(len(by_mode), 6)
        self.assertEqual(len(distinct), 8)
        self.assertTrue(distinct.issubset(always))
        for name in sorted(always | by_mode | distinct | {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE"}):
            self.assertIn(f"`{name}`", inventory, name)

    def test_inventory_defaults_match_config(self):
        inventory = _flat(_section(self.text, INVENTORY))
        config_source = CONFIG_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            'os.getenv("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")', config_source
        )
        self.assertIn(
            'os.getenv("LANGGRAPH_PRIMARY_OPENAI_TRANSPORT", "responses-api")', config_source
        )
        self.assertIn('"LANGGRAPH_PRIMARY_EXTERNAL_EFFECT_PULL_NUMBER", 0', config_source)
        self.assertIn("defaults to `github-v1` when absent", inventory)
        self.assertIn("`responses-api` when absent", inventory)
        self.assertIn("The default of 0 fails every GitHub effect closed", inventory)
        for name in (
            "ORACLE_AUTOMERGE_GITHUB_TOKEN",
            "ORACLE_AUTOMERGE_GITHUB_APP_ID",
            "ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID",
            "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64",
        ):
            self.assertIn(f'"{name}"', config_source)
            self.assertIn(f"`{name}`", inventory)

    def test_inventory_keeps_every_switch_off(self):
        inventory = _flat(_section(self.text, INVENTORY))
        for name in (
            "LANGGRAPH_PRIMARY_ENABLED",
            "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
            "ALLOW_PAID_RUNS",
        ):
            self.assertIn(f"`{name}`", inventory)
        self.assertIn(
            "stay false and `LANGGRAPH_PARALLEL_KILL_SWITCH` stays true", inventory
        )

    def test_inventory_states_the_conditional_openai_api_key(self):
        inventory = _flat(_section(self.text, INVENTORY))
        config_source = CONFIG_SOURCE.read_text(encoding="utf-8")
        transports_source = TRANSPORTS_SOURCE.read_text(encoding="utf-8")
        self.assertIn('os.getenv("OPENAI_API_KEY", "")', config_source)
        self.assertIn(
            'if openai_transport == "responses-api" and openai_api_key'
            ' and bool(observed_auth.get("openai", False)):',
            transports_source,
        )
        self.assertIn(
            'if provider_id == "openai" and openai_transport != "native-codex-host":',
            transports_source,
        )
        self.assertIn(
            "it is built only when `OPENAI_API_KEY` is set and openai authentication"
            " is observed",
            inventory,
        )
        self.assertIn("conditionally required by the selected transport", inventory)
        self.assertIn(
            "the host-bridge loop skips openai unless the transport is"
            " `native-codex-host`",
            inventory,
        )

    def test_inventory_denies_permission_to_run_without_pins(self):
        inventory = _flat(_section(self.text, INVENTORY))
        script = CONTROL_SCRIPT.read_text(encoding="utf-8")
        # Blank pins fail the script's own shape check, whichever version is held.
        self.assertIn(
            "Primary repository authority must have exact commit and tree pins.", script
        )
        self.assertIn(
            'if ([string]$values["LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA"] -notmatch', script
        )
        self.assertIn(
            "it is not permission to run the control script, activate, or dispatch"
            " without pins",
            inventory,
        )

    def test_inventory_separates_the_current_control_version(self):
        inventory = _flat(_section(self.text, INVENTORY))
        script = CONTROL_SCRIPT.read_text(encoding="utf-8")
        self.assertRegex(script, r'\[ValidateSet\("PrepareAcceptance",')
        self.assertIn(
            "`BootstrapDatabaseIdentity`, `SelectGeneration`, `StageRuntime`, and"
            " `ValidateProviders`",
            inventory,
        )
        self.assertIn(
            "it is not satisfied by, and not testable against, the script in this"
            " checkout",
            inventory,
        )
        self.assertIn(
            "Check the `-Action` ValidateSet of the script you actually hold", inventory
        )
        for name in (
            "DIRECTOR_RUNTIME_IMAGE",
            "DIRECTOR_RUNTIME_IMAGE_ID",
            "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256",
            "DIRECTOR_ASGI_APP",
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH",
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH",
            "DIRECTOR_PROVIDER_VALIDATION_MANIFEST",
            "DIRECTOR_PROVIDER_VALIDATION_GENERATION",
        ):
            self.assertIn(f"`{name}`", inventory, name)
        self.assertIn("director-primary-generation/v1", inventory)
        self.assertIn(
            "A canary is a real provider invocation and may be billable", inventory
        )

    def test_worksheet_records_names_only(self):
        # The worksheet may show a non-secret path example such as the snapshot
        # root, but never an assignment to a credential, allowlist, or identity.
        self.assertIsNone(
            re.search(
                r"^[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|DSN|ALLOWLIST|_ID|SHA256|_SHA)[A-Z0-9_]*=\S",
                self.text,
                re.MULTILINE,
            )
        )
        self.assertIsNone(re.search(r"\b[UC]0[A-Z0-9]{8,}\b", self.text))
        self.assertIsNone(
            re.search(r"gh[oprsu]_[A-Za-z0-9_]{30,}|sk-ant-|xox[baprs]-", self.text)
        )
        self.assertIsNone(re.search(r"[0-9a-f]{40}", self.text))


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | END]]
