# [[Claude Code | Claude Fable 5 | 2026-08-31 | 10:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:54 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 09:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANSI_CONTROL_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Probes run under the same strictness the script itself declares.
POWERSHELL_PRELUDE = "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"


def _powershell_shell() -> str | None:
    return shutil.which("pwsh") or shutil.which("pwsh.exe")


def _run_powershell(probe: str) -> str:
    """Run a deterministic PowerShell 7 probe and return its normalized stdout."""

    shell = _powershell_shell()
    if shell is None:
        raise unittest.SkipTest("PowerShell 7 is unavailable in this environment")
    completed = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", probe],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    normalized = ANSI_CONTROL_SEQUENCE.sub("", completed.stdout)
    return "".join(
        character
        for character in normalized
        if character in "\r\n\t" or ord(character) >= 0x20
    )


def _function_source(script: str, name: str) -> str:
    """Return exactly one top-level PowerShell function definition from the script."""

    start = script.index(f"function {name} {{")
    end = script.find("\nfunction ", start + 1)
    return script[start:] if end == -1 else script[start:end]


def _scenario_results(output: str) -> dict[str, tuple[str, str]]:
    """Parse ``name|outcome|detail`` probe lines into a lookup."""

    results: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        if line.count("|") < 2:
            continue
        name, outcome, detail = line.strip().split("|", 2)
        results[name] = (outcome, detail)
    return results


class UppercutDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrapper = (
            ROOT / "scripts" / "windows-uppercut-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.script = (
            ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.bootstrap = (
            ROOT / "scripts" / "windows-bootstrap.ps1"
        ).read_text(encoding="utf-8")
        cls.compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        cls.profile = (ROOT / ".env.uppercut.example").read_text(encoding="utf-8")

    def test_profile_is_separate_and_primary_env_is_read_only(self):
        self.assertIn('ProfileName = "project-uppercut"', self.wrapper)
        self.assertIn('ProfilePath = ".env.uppercut"', self.script)
        self.assertNotIn('Set-Content ".env"', self.script)
        self.assertNotIn('WriteAllText((Join-Path (Get-Location) ".env")', self.script)
        self.assertIn("SeedProviderCredentialsFromPrimaryEnv", self.script)

    def test_legacy_profiles_gain_a_fresh_gateway_secret_without_printing_it(self):
        self.assertIn(
            "if ($envText -notmatch '(?m)^SLACK_GATEWAY_SECRET=')",
            self.bootstrap,
        )
        self.assertIn(
            'SLACK_GATEWAY_SECRET=" + (New-RandomSecret)',
            self.bootstrap,
        )
        self.assertIn(
            "if ($profileText -notmatch '(?m)^SLACK_GATEWAY_SECRET=')",
            self.script,
        )
        self.assertNotIn('Write-Host $envText', self.bootstrap)
        self.assertNotIn('Write-Host $profileText', self.script)

    def test_compose_project_ports_and_state_are_isolated(self):
        self.assertIn("name: ${DIRECTOR_COMPOSE_PROJECT_NAME:-director-orchestrator}", self.compose)
        self.assertIn("${DIRECTOR_HOST_PORT:-8080}:8080", self.compose)
        self.assertIn("${N8N_HOST_PORT:-5678}:5678", self.compose)
        self.assertIn("DIRECTOR_COMPOSE_PROJECT_NAME=project-uppercut-director", self.profile)
        self.assertIn("DIRECTOR_HOST_PORT=18080", self.profile)
        self.assertIn("N8N_HOST_PORT=15678", self.profile)
        self.assertIn("SQLITE_PATH=/data/project-uppercut.sqlite3", self.profile)

    def test_paid_gate_starts_closed_and_checks_store_before_opening(self):
        closed = self.script.index('ALLOW_PAID_RUNS = "false"', self.script.index("$identity"))
        store_probe = self.script.index("list_recoverable()")
        opens = self.script.index("Write-ProfileSwitches -PaidRuns $true")
        provider_probe = self.script.index('/v1/usage-checks/run')
        full_stack = self.script.rindex("up -d --build --remove-orphans")

        self.assertLess(closed, store_probe)
        self.assertLess(store_probe, opens)
        self.assertLess(opens, provider_probe)
        self.assertLess(provider_probe, full_stack)

    def test_skipping_checks_cannot_enable_paid_execution(self):
        self.assertIn("$EnablePaidRuns -and $SkipProviderChecks", self.script)
        self.assertIn("cannot be combined with -EnablePaidRuns", self.script)

    def test_failure_closes_paid_gate_and_keeps_slack_intake_offline(self):
        final_success = self.script.rindex('\nWrite-Host "LIVE VALIDATION PASSED.')
        paid_catch = self.script.rfind("\ncatch {", 0, final_success)
        catch_block = self.script[paid_catch:final_success]
        self.assertIn("Write-ProfileSwitches -PaidRuns $false", catch_block)
        self.assertIn("stop slackbot", catch_block)
        self.assertIn("$LASTEXITCODE -ne 0", catch_block)
        self.assertIn("stop orchestrator slackbot", catch_block)

    def test_paid_validation_closes_slack_and_timestamps_before_api_start(self):
        paid_section = self.script.index("\ntry {", self.script.index("Write-ProfileSwitches -PaidRuns $true"))
        stop_slack = self.script.index("stop slackbot", paid_section)
        starts_at = self.script.index("$startedAt = [DateTimeOffset]::UtcNow", paid_section)
        starts_api = self.script.index("up -d --build --force-recreate orchestrator", paid_section)

        self.assertLess(stop_slack, starts_at)
        self.assertLess(starts_at, starts_api)

    def test_usage_response_array_is_enumerated_before_filtering(self):
        latest_line = next(
            line for line in self.script.splitlines() if "$latest = @(Invoke-RestMethod" in line
        )
        self.assertIn("| ForEach-Object { $_ })", latest_line)

    def test_provider_probe_is_bound_to_one_exact_cycle_and_status_set(self):
        self.assertIn("$cycleId = [string]$accepted.cycle_id", self.script)
        self.assertIn("Assert-UsageProviderPartition", self.script)
        self.assertIn("Assert-CoolingProviderEvidence", self.script)
        self.assertIn("Assert-ScheduledCycleEvidence", self.script)
        self.assertIn("$_.cycle_id -ceq $cycleId", self.script)
        self.assertIn('[string]$_.provider -in $scheduledProviders', self.script)
        self.assertIn('[string]$_.status -notin @("ok", "prompted")', self.script)
        self.assertIn('$runStatus -ceq "not_due"', self.script)
        self.assertIn("Verifying recent successful evidence for:", self.script)
        self.assertNotIn("Skipping paid provider probes for this cycle.", self.script)
        self.assertNotIn("LIVE VALIDATION PASSED (cooldown/no-due)", self.script)
        self.assertIn(
            "only ok/prompted evidence is acceptable",
            self.script,
        )
        self.assertIn("Get-CompletionVerifiedProviders", self.script)
        self.assertIn(
            "requires authenticated terminal callback evidence",
            self.script,
        )
        self.assertGreaterEqual(
            self.script.count(
                "-CompletionVerifiedProviders $completionVerifiedProviders"
            ),
            3,
        )

    def test_mixed_case_long_table_requires_claude_completion_evidence(self):
        shell = shutil.which("pwsh") or shutil.which("pwsh.exe")
        if shell is None:
            self.skipTest("PowerShell 7 is unavailable in this environment")
        start = self.script.index("function Get-CompletionVerifiedProviders")
        end = self.script.index("function Assert-CoolingProviderEvidence", start)
        function_source = self.script[start:end]
        probe = (
            function_source
            + "\n$result = @(Get-CompletionVerifiedProviders -ProfileName 'Long-Table')"
            + "\nWrite-Output ($result.Count.ToString() + '|' + [string]$result[0])"
        )
        completed = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=probe,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        ansi_control_sequence = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
        normalized_stdout = ansi_control_sequence.sub("", completed.stdout)
        normalized_stdout = "".join(
            character
            for character in normalized_stdout
            if character in "\r\n\t" or ord(character) >= 0x20
        )
        self.assertEqual("1|claude", normalized_stdout.strip())

    def test_usage_cooldown_evidence_fails_closed_for_error_and_skipped(self):
        cooling_fn = self.script[
            self.script.index("function Assert-CoolingProviderEvidence") :
            self.script.index("function Assert-ScheduledCycleEvidence")
        ]
        self.assertIn('$status -notin @("ok", "prompted")', cooling_fn)
        self.assertIn("FromMinutes($CooldownWindowMinutes)", cooling_fn)
        self.assertIn("Ambiguous usage evidence", cooling_fn)
        self.assertIn("Missing latest usage record", cooling_fn)

        not_due = self.script[
            self.script.index('$runStatus -ceq "not_due"') :
            self.script.index('$runStatus -cne "accepted"')
        ]
        self.assertIn("Assert-CoolingProviderEvidence", not_due)
        self.assertIn("/v1/usage-checks/latest", not_due)
        self.assertIn("LIVE VALIDATION PASSED.", not_due)

        accepted = self.script[
            self.script.index("Bounded check cycle $cycleId accepted") :
            self.script.index("Provider checks passed, but the complete isolated stack")
        ]
        self.assertIn("Assert-ScheduledCycleEvidence", accepted)
        self.assertIn("Assert-CoolingProviderEvidence", accepted)

    def test_acl_precedes_every_secret_read_or_write(self):
        acl = self.script.index("& icacls.exe")
        profile_read = self.script.index("$profileText = Get-Content")
        secret_generation = self.script.index("New-RandomSecret", profile_read)
        self.assertLess(acl, profile_read)
        self.assertLess(acl, secret_generation)

    def test_process_environment_and_location_are_restored(self):
        self.assertIn("$originalLocation = Get-Location", self.script)
        self.assertIn("$originalProcessEnvironment", self.script)
        self.assertIn("finally {", self.script)
        self.assertIn("Set-Location $originalLocation", self.script)

    def test_collision_detection_covers_primary_and_all_isolated_profiles(self):
        self.assertIn(
            '@(".env", ".env.uppercut", ".env.edge-oracle", ".env.long-table")',
            self.script,
        )
        for name in (
            "DIRECTOR_COMPOSE_PROJECT_NAME",
            "DIRECTOR_HOST_PORT",
            "N8N_HOST_PORT",
            "SQLITE_PATH",
            "ORCHESTRATOR_SHARED_SECRET",
            "CLAUDE_CALLBACK_SECRET",
            "N8N_ENCRYPTION_KEY",
            "N8N_POSTGRES_PASSWORD",
            "SLACK_DIRECTOR_CHANNEL_ID",
            "SLACK_APP_TOKEN",
            "SLACK_BOT_TOKEN",
            "ORACLE_AUTOMERGE_REVIEW_SECRET",
            "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64",
        ):
            self.assertIn(f'"{name}"', self.script)

    def test_slackbot_receives_only_its_required_environment(self):
        slack_section = self.compose.split("  slackbot:", 1)[1].split("  n8n-db:", 1)[0]
        self.assertNotIn("env_file:", slack_section)
        self.assertIn("ORCHESTRATOR_SHARED_SECRET:", slack_section)
        self.assertIn("SLACK_APP_TOKEN:", slack_section)
        for forbidden in (
            "OPENAI_API_KEY", "CLAUDE_ROUTINE_TOKEN", "CURSOR_API_KEY",
            "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_GITHUB_TOKEN",
            "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64",
            "ORACLE_AUTOMERGE_REVIEW_SECRET",
        ):
            self.assertNotIn(forbidden, slack_section)


class EdgeOracleDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrapper = (
            ROOT / "scripts" / "windows-edge-oracle-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.script = (
            ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.profile = (ROOT / ".env.edge-oracle.example").read_text(encoding="utf-8")

    def test_wrapper_selects_oracle_without_copying_bootstrap_logic(self):
        self.assertIn('ProfileName = "edge-oracle"', self.wrapper)
        self.assertIn("windows-isolated-live-validation.ps1", self.wrapper)
        self.assertIn("ApiOnly", self.wrapper)
        self.assertNotIn("docker compose", self.wrapper)

    def test_api_only_activation_keeps_external_intake_and_paid_runs_closed(self):
        self.assertIn("$ApiOnly -and $EnablePaidRuns", self.script)
        api_only = self.script.index("if ($ApiOnly) {", self.script.index("if (-not $EnablePaidRuns)"))
        stop_optional = self.script.index("stop slackbot n8n n8n-db", api_only)
        start_api = self.script.index(
            "up -d --build --remove-orphans orchestrator", stop_optional
        )
        self.assertLess(stop_optional, start_api)
        self.assertIn("Slack and n8n remain stopped", self.script)

    def test_api_only_profile_does_not_require_slack_credentials(self):
        required = self.script.index("$requiredConfiguration = @(")
        optional_slack = self.script.index("if (-not $ApiOnly)", required)
        assertion = self.script.index("Assert-Configured", optional_slack)
        self.assertLess(required, optional_slack)
        self.assertLess(optional_slack, assertion)

    def test_oracle_identity_and_state_are_isolated(self):
        expected = (
            "APP_ENV=edge-oracle",
            "DIRECTOR_COMPOSE_PROJECT_NAME=edge-oracle-director",
            "DIRECTOR_HOST_PORT=28080",
            "N8N_HOST_PORT=25678",
            "SQLITE_PATH=/data/edge-oracle.sqlite3",
            "PROJECT_ALLOWLIST=edge-oracle",
            "REPOSITORY_ALLOWLIST=edwardjones3976/edge_engine",
            "ALLOWED_STARTING_REFS=main",
        )
        for line in expected:
            self.assertIn(line, self.profile)

    def test_oracle_stages_paid_usage_and_merge_gates_closed(self):
        self.assertIn("ALLOW_PAID_RUNS=false", self.profile)
        self.assertIn("USAGE_CHECK_ENABLED=false", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_ENABLED=false", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_KILL_SWITCH=true", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_REQUIRED_CHECK_APP=github-actions", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_PROJECT=edge-oracle", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_REPOSITORY=edwardjones3976/edge_engine", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_ID=", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID=", self.profile)
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64=", self.profile)

    def test_oracle_uses_dedicated_secret_placeholders(self):
        for name in (
            "ORCHESTRATOR_SHARED_SECRET",
            "CLAUDE_CALLBACK_SECRET",
            "N8N_ENCRYPTION_KEY",
            "N8N_POSTGRES_PASSWORD",
            "ORACLE_AUTOMERGE_REVIEW_SECRET",
        ):
            self.assertIn(f"{name}=REPLACE_WITH_RANDOM_SECRET", self.profile)
        self.assertIn("No values were printed", self.script)


class LongTableDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrapper = (
            ROOT / "scripts" / "windows-long-table-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.script = (
            ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.profile = (ROOT / ".env.long-table.example").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_wrapper_defaults_to_full_stack_and_preserves_explicit_api_only_canary(self):
        self.assertIn('ProfileName = "long-table"', self.wrapper)
        self.assertIn("[switch]$ApiOnly", self.wrapper)
        self.assertIn('[string]$ProviderCredentialSourceEnvPath = ""', self.wrapper)
        self.assertIn("if ($ApiOnly) { $arguments.ApiOnly = $true }", self.wrapper)
        self.assertIn(
            "$arguments.ProviderCredentialSourceEnvPath = $ProviderCredentialSourceEnvPath",
            self.wrapper,
        )
        self.assertNotRegex(self.wrapper, r"(?m)^\s*ApiOnly\s*=\s*\$true\s*$")
        self.assertNotIn("docker compose", self.wrapper)

    def test_provider_seed_source_is_explicit_and_never_prints_secret_values(self):
        self.assertIn("ProviderCredentialSourceEnvPath", self.script)
        self.assertIn("Resolve-Path -LiteralPath $providerCredentialSource", self.script)
        self.assertIn("values were not displayed", self.script)
        self.assertNotIn('Write-Host $primaryValues', self.script)
        seed_block = self.script.split("$providerCredentialSource =", 1)[1].split(
            "$identity =", 1
        )[0]
        self.assertNotIn('"GROK_CLI_EXECUTABLE"', seed_block)
        self.assertNotIn('"GROK_CLI_REPOSITORY_MAP_JSON"', seed_block)
        self.assertNotIn("GROK_CLI_ENABLED", seed_block)

    def test_full_stack_bootstraps_and_verifies_the_n8n_intake_workflow(self):
        initializer = self.script.split("function Initialize-N8nIntakeWorkflow", 1)[1]
        self.assertIn('workflowId = "directorIntake"', initializer)
        self.assertIn("ConvertFrom-Json", initializer)
        self.assertIn("$workflowDefinition.id -cne $workflowId", initializer)
        self.assertIn("up -d --wait --wait-timeout 120 n8n-db", initializer)
        self.assertIn("n8n import:workflow --input=/workflows/director-intake.json", initializer)
        self.assertIn("n8n publish:workflow --id=$workflowId", initializer)
        self.assertIn("n8n list:workflow --active=true --onlyId", initializer)
        self.assertIn(
            'if ($ProfileName -eq "long-table" -and -not $ApiOnly)', self.script
        )
        self.assertIn("Initialize-N8nIntakeWorkflow", self.script)
        self.assertIn("$repoRoot = Split-Path -Parent $PSScriptRoot", self.script)

    def test_identity_ports_repository_and_stores_are_isolated(self):
        expected = (
            "APP_ENV=long-table",
            "DIRECTOR_COMPOSE_PROJECT_NAME=long-table-director",
            "DIRECTOR_HOST_PORT=38080",
            "N8N_HOST_PORT=35678",
            "SQLITE_PATH=/data/long-table.sqlite3",
            "PROJECT_ALLOWLIST=long-table-bid-whist",
            "REPOSITORY_ALLOWLIST=edwardjones3976/long-table-bid-whist",
            "ALLOWED_STARTING_REFS=main",
            "LANGGRAPH_CHECKPOINT_PATH=/data/long-table-langgraph-shadow.sqlite3",
            "LANGGRAPH_RUNTIME_CHECKPOINT_PATH=/data/long-table-langgraph-runtime.sqlite3",
            "LANGGRAPH_RUNTIME_LEDGER_PATH=/data/long-table-langgraph-ledger.sqlite3",
            "COMPLETION_GATE_STORE_PATH=/data/long-table-completion-gates.sqlite3",
            "REPOSITORY_SNAPSHOT_HOST_PATH=C:/Users/edwar/.director-snapshots/long-table-main",
            "REPOSITORY_SNAPSHOT_ROOT=/repository",
        )
        for line in expected:
            self.assertIn(line, self.profile)

    def test_full_stack_profile_binds_slack_n8n_and_optional_bridge_to_project_channel(self):
        for line in (
            "SLACK_DIRECTOR_CHANNEL_ID=C0BSH9SPPUH",
            "N8N_DIRECTOR_WEBHOOK_URL=http://n8n:5678/webhook/director-intake",
            "CHATGPT_BRIDGE_CHANNEL_IDS=C0BSH9SPPUH",
        ):
            self.assertIn(line, self.profile)

    def test_profile_declares_every_provider_credential_and_safety_boundary(self):
        for line in (
            "OPENAI_API_KEY=",
            "CLAUDE_ROUTINE_URL=",
            "CURSOR_API_KEY=",
            "GEMINI_API_KEY=",
            "XAI_API_KEY=",
            "GROK_ENABLED=false",
            "GROK_CODE_MODEL=grok-build-0.1",
            "GROK_CLI_REPOSITORY_MAP_JSON={}",
            "PERPLEXITY_API_KEY=",
            "PERPLEXITY_ENABLED=false",
            "PERPLEXITY_ALLOW_REPOSITORY_WRITE=false",
            "PERPLEXITY_ALLOW_REVIEW_VERDICT=false",
        ):
            self.assertIn(line, self.profile)

    def test_canary_budget_and_all_activation_switches_start_closed(self):
        for line in (
            "ALLOW_PAID_RUNS=false",
            "MAX_TASK_BUDGET_USD=1.00",
            "MAX_DAILY_PROVIDER_BUDGET_USD=5.00",
            "LANGGRAPH_RUNTIME_ENABLED=false",
            "LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED=false",
            "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED=false",
            "COMPLETION_GATES_ENABLED=false",
            "COMPLETION_GATE_ISOLATED_PROOF_ENABLED=false",
            "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED=false",
            "LANGGRAPH_PARALLEL_KILL_SWITCH=true",
        ):
            self.assertIn(line, self.profile)

    def test_long_table_is_the_only_paid_api_only_canary(self):
        self.assertIn(
            '$ApiOnly -and $EnablePaidRuns -and $ProfileName -ne "long-table"',
            self.script,
        )
        self.assertIn(
            '$ProfileName -notin @("edge-oracle", "long-table")', self.script
        )
        paid_section = self.script[self.script.index("Write-ProfileSwitches -PaidRuns $true") :]
        self.assertIn("if ($ApiOnly)", paid_section)
        self.assertIn("stop slackbot n8n n8n-db", paid_section)
        self.assertIn(
            "up -d --build --remove-orphans --force-recreate orchestrator",
            paid_section,
        )

    def test_paid_activation_and_rollback_switch_the_complete_writer_set(self):
        switch_section = self.script[
            self.script.index("function Write-ProfileSwitches") :
            self.script.index("function Assert-Configured")
        ]
        for name in (
            "LANGGRAPH_RUNTIME_ENABLED",
            "LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED",
            "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED",
            "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED",
            "LANGGRAPH_PARALLEL_KILL_SWITCH",
        ):
            self.assertIn(name, switch_section)
        self.assertNotIn('Name "COMPLETION_GATES_ENABLED"', switch_section)
        self.assertNotIn('Name "COMPLETION_GATE_ISOLATED_PROOF_ENABLED"', switch_section)
        self.assertIn(
            "Write-ProfileSwitches -PaidRuns $false -UsageChecks $false -RuntimeGraduation $false",
            self.script,
        )
        self.assertIn(
            "Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $false",
            self.script,
        )
        self.assertIn(
            "Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $true",
            self.script,
        )
        identity_start = self.script.index("$identity = [ordered]@{")
        writer_closed = self.script.index(
            '$identity["LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED"] = "false"',
            identity_start,
        )
        profile_write = self.script.index("Write-Utf8NoBom -Path $profilePath", identity_start)
        self.assertLess(writer_closed, profile_write)

    def test_writer_secret_and_exact_snapshot_are_fail_closed(self):
        self.assertIn('"LANGGRAPH_RUNTIME_WRITER_SECRET"', self.script)
        self.assertIn('Assert-Configured -Values $values -Names @("LANGGRAPH_RUNTIME_WRITER_SECRET")', self.script)

    def test_long_table_readiness_uses_strict_booleans_and_passed_values(self):
        readiness = self.script[
            self.script.index("function ConvertTo-StrictBoolean") :
            self.script.index("function Assert-LongTableSnapshot")
        ]
        self.assertIn("worker_execution_active", readiness)
        self.assertNotIn("completion_gates.execution_active", readiness)
        self.assertIn('$Values["COMPLETION_GATE_ISOLATED_PROOF_ENABLED"]', readiness)
        self.assertIn('$text -ceq "true"', readiness)
        self.assertIn('$text -ceq "false"', readiness)
        self.assertIn("-Values $values", self.script)

    def test_snapshot_identity_matches_the_shipped_profile(self):
        snapshot_line = next(
            line
            for line in self.profile.splitlines()
            if line.startswith("REPOSITORY_SNAPSHOT_HOST_PATH=")
        )
        snapshot_path = snapshot_line.split("=", 1)[1]
        self.assertIn(
            f'[string]$Values["REPOSITORY_SNAPSHOT_HOST_PATH"] -cne "{snapshot_path}"',
            self.script,
        )

    def test_early_preflight_failure_stops_previously_running_writers(self):
        self.assertIn('catch {\n    $outerFailure = $_', self.script)
        self.assertIn(
            'stop orchestrator slackbot | Out-Null',
            self.script,
        )
        self.assertIn(
            'Write-ProfileSwitches -PaidRuns $false -UsageChecks $false -RuntimeGraduation $false',
            self.script,
        )
        self.assertIn("function Assert-LongTableSnapshot", self.script)
        self.assertIn("git -C $snapshotPath rev-parse HEAD", self.script)
        self.assertIn("git -C $snapshotPath remote get-url origin", self.script)
        self.assertIn("status --porcelain --untracked-files=all", self.script)
        self.assertIn("git -C $snapshotPath rev-parse origin/main", self.script)
        self.assertIn("$head -cne $remoteHead", self.script)
        self.assertIn('"LANGGRAPH_RUNTIME_WRITER_SECRET"', self.script)
        self.assertIn("reuses a Long Table internal secret", self.script)

    def test_compose_snapshot_mount_is_explicitly_read_only(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            "source: ${REPOSITORY_SNAPSHOT_HOST_PATH:-./.repository-snapshot-disabled}",
            compose,
        )
        self.assertIn("target: /repository", compose)
        self.assertIn("read_only: true", compose)
        self.assertTrue((ROOT / ".repository-snapshot-disabled" / "README.md").is_file())

    def test_runtime_image_contains_git_for_exact_snapshot_authentication(self):
        self.assertIn("apt-get install --yes --no-install-recommends git", self.dockerfile)
        resolver = (
            ROOT / "src" / "director_orchestrator" / "packet_transports.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"-c", f"safe.directory={self.root}"', resolver)

    def test_isolated_script_parses_without_syntax_errors(self):
        script_path = str(ROOT / "scripts" / "windows-isolated-live-validation.ps1")
        probe = (
            "$tokens = $null\n"
            "$scriptErrors = $null\n"
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script_path.replace(chr(39), chr(39) * 2)}', [ref]$tokens, [ref]$scriptErrors)\n"
            "$brokenErrors = $null\n"
            "$null = [System.Management.Automation.Language.Parser]::ParseInput("
            "'if ($x { ', [ref]$tokens, [ref]$brokenErrors)\n"
            'Write-Output ("script|" + @($scriptErrors).Count)\n'
            'Write-Output ("broken|" + @($brokenErrors).Count)\n'
        )
        lines = [line.strip() for line in _run_powershell(probe).splitlines() if "|" in line]
        counts = dict(line.split("|", 1) for line in lines)
        # The parser must prove it reports errors before its clean result counts.
        self.assertGreater(int(counts["broken"]), 0)
        self.assertEqual(0, int(counts["script"]))

    def test_identity_block_preserves_profile_usage_check_providers(self):
        identity_block = self.script[
            self.script.index("$identity = [ordered]@{") :
            self.script.index("foreach ($entry in $identity.GetEnumerator())")
        ]
        self.assertNotRegex(identity_block, r"(?m)^\s*USAGE_CHECK_PROVIDERS\s*=")
        self.assertNotIn('USAGE_CHECK_PROVIDERS = "openai,claude,cursor,grok"', self.script)
        self.assertRegex(self.profile, r"(?m)^USAGE_CHECK_PROVIDERS=openai,claude,cursor$")

    def test_usage_provider_prevalidation_precedes_paid_switches_and_dispatch(self):
        definition = self.script.index("function Get-ValidatedUsageCheckProviders")
        prevalidation = self.script.index(
            "$expectedUsageProviders = @(Get-ValidatedUsageCheckProviders "
            "-Values $values -ProfileLabel $profilePath)"
        )
        opens = self.script.index(
            "Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $false"
        )
        dispatch = self.script.index("/v1/usage-checks/run")
        partition = self.script.index("-ExpectedPrimary $expectedUsageProviders")
        self.assertLess(definition, prevalidation)
        self.assertLess(prevalidation, opens)
        self.assertLess(opens, dispatch)
        self.assertLess(dispatch, partition)
        # The validated values are the profile as written: no profile write sits
        # between the last read and the prevalidation.
        values_read = self.script.rindex("$values = Read-DotEnv -Path $profilePath", 0, prevalidation)
        self.assertEqual(-1, self.script.find("Write-Utf8NoBom", values_read, prevalidation))
        self.assertEqual(-1, self.script.find("Write-ProfileSwitches", values_read, prevalidation))
        self.assertEqual(1, self.script.count("Assert-UsageProviderPartition `"))
        self.assertIn("[Parameter(Mandatory = $true)][string[]]$ExpectedPrimary", self.script)
        self.assertNotIn('$ExpectedPrimary = @("claude", "cursor", "openai")', self.script)
        rules = _function_source(self.script, "Get-ValidatedUsageCheckProviders")
        self.assertIn('$mandatory = @("openai", "claude", "cursor")', rules)
        self.assertIn(
            '$known = @("openai", "claude", "cursor", "grok", "perplexity", "gemini")', rules
        )
        for name in (
            "GROK_ENABLED", "XAI_API_KEY", "PERPLEXITY_ENABLED", "PERPLEXITY_API_KEY",
            "GEMINI_API_KEY",
        ):
            self.assertIn(f'"{name}"', rules)
        self.assertIn('StartsWith("REPLACE_WITH_")', rules)
        self.assertIn("Group-Object -CaseSensitive", rules)

    def test_usage_provider_prevalidation_rules_are_deterministic(self):
        probe = (
            POWERSHELL_PRELUDE
            + _function_source(self.script, "Get-ValidatedUsageCheckProviders")
            + r"""
$scenarios = [ordered]@{
    "minimal" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor" }
    "spaced" = @{ USAGE_CHECK_PROVIDERS = " cursor , openai , claude " }
    "absent" = @{ OPENAI_API_KEY = "sk-test" }
    "blank" = @{ USAGE_CHECK_PROVIDERS = "" }
    "empty-entry" = @{ USAGE_CHECK_PROVIDERS = "openai,,claude,cursor" }
    "duplicate" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,openai" }
    "unknown" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,bard" }
    "mixed-case" = @{ USAGE_CHECK_PROVIDERS = "OpenAI,claude,cursor" }
    "missing-cursor" = @{ USAGE_CHECK_PROVIDERS = "openai,claude" }
    "grok-disabled" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,grok"; GROK_ENABLED = "false"; XAI_API_KEY = "xai-test" }
    "grok-placeholder" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,grok"; GROK_ENABLED = "true"; XAI_API_KEY = "REPLACE_WITH_XAI_API_KEY" }
    "grok-ready" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,grok"; GROK_ENABLED = "true"; XAI_API_KEY = "xai-test" }
    "perplexity-disabled" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,perplexity"; PERPLEXITY_API_KEY = "pplx-test" }
    "perplexity-ready" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,perplexity"; PERPLEXITY_ENABLED = "true"; PERPLEXITY_API_KEY = "pplx-test" }
    "gemini-blank" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,gemini"; GEMINI_API_KEY = "" }
    "gemini-ready" = @{ USAGE_CHECK_PROVIDERS = "openai,claude,cursor,gemini"; GEMINI_API_KEY = "gem-test" }
}
foreach ($entry in $scenarios.GetEnumerator()) {
    try {
        $result = @(Get-ValidatedUsageCheckProviders -Values $entry.Value -ProfileLabel "probe")
        Write-Output ($entry.Key + "|ok|" + ($result -join ","))
    }
    catch {
        Write-Output ($entry.Key + "|error|" + $_.Exception.Message)
    }
}
"""
        )
        output = _run_powershell(probe)
        results = _scenario_results(output)
        self.assertEqual(("ok", "claude,cursor,openai"), results["minimal"])
        self.assertEqual(("ok", "claude,cursor,openai"), results["spaced"])
        self.assertEqual(("ok", "claude,cursor,grok,openai"), results["grok-ready"])
        self.assertEqual(("ok", "claude,cursor,openai,perplexity"), results["perplexity-ready"])
        self.assertEqual(("ok", "claude,cursor,gemini,openai"), results["gemini-ready"])
        for name, fragment in (
            ("absent", "must declare USAGE_CHECK_PROVIDERS explicitly"),
            ("blank", "must declare USAGE_CHECK_PROVIDERS explicitly"),
            ("empty-entry", "empty provider entry"),
            ("duplicate", "duplicate providers: openai"),
            ("unknown", "unknown providers: bard"),
            ("mixed-case", "unknown providers: OpenAI"),
            ("missing-cursor", "omits mandatory primary providers: cursor"),
            ("grok-disabled", "GROK_ENABLED is not exactly true"),
            ("grok-placeholder", "XAI_API_KEY is not configured"),
            ("perplexity-disabled", "PERPLEXITY_ENABLED is not exactly true"),
            ("gemini-blank", "GEMINI_API_KEY is not configured"),
        ):
            outcome, detail = results[name]
            self.assertEqual("error", outcome, name)
            self.assertIn(fragment, detail, name)
        for secret in ("xai-test", "pplx-test", "gem-test", "sk-test"):
            self.assertNotIn(secret, output)

    def test_usage_partition_rejects_providers_outside_the_validated_set(self):
        probe = (
            POWERSHELL_PRELUDE
            + _function_source(self.script, "Get-UsageProviderNames")
            + "\n"
            + _function_source(self.script, "Assert-UsageProviderPartition")
            + r"""
$scenarios = [ordered]@{
    "primaries-only" = @{ Scheduled = @("openai"); Cooling = @("claude", "cursor"); Expected = @("claude", "cursor", "openai") }
    "unlisted-grok" = @{ Scheduled = @("openai", "grok"); Cooling = @("claude", "cursor"); Expected = @("claude", "cursor", "openai") }
    "listed-grok" = @{ Scheduled = @("openai", "grok"); Cooling = @("claude", "cursor"); Expected = @("claude", "cursor", "grok", "openai") }
    "missing-listed-grok" = @{ Scheduled = @("openai"); Cooling = @("claude", "cursor"); Expected = @("claude", "cursor", "grok", "openai") }
}
foreach ($entry in $scenarios.GetEnumerator()) {
    try {
        $partition = Assert-UsageProviderPartition -ScheduledProviders $entry.Value.Scheduled -CoolingDownProviders $entry.Value.Cooling -ExpectedPrimary $entry.Value.Expected
        Write-Output ($entry.Key + "|ok|" + ($partition.ScheduledProviders -join ",") + ";" + ($partition.CoolingDownProviders -join ","))
    }
    catch {
        Write-Output ($entry.Key + "|error|" + $_.Exception.Message)
    }
}
"""
        )
        results = _scenario_results(_run_powershell(probe))
        self.assertEqual(("ok", "openai;claude,cursor"), results["primaries-only"])
        self.assertEqual(("ok", "grok,openai;claude,cursor"), results["listed-grok"])
        self.assertEqual("error", results["unlisted-grok"][0])
        self.assertIn("Unknown providers in schedule response: grok", results["unlisted-grok"][1])
        self.assertEqual("error", results["missing-listed-grok"][0])
        self.assertIn("must partition expected primary providers", results["missing-listed-grok"][1])

    def test_credential_seeding_copies_only_named_keys(self):
        copier = _function_source(self.script, "Copy-ProviderCredentialValues")
        self.assertEqual(
            [
                "OPENAI_API_KEY", "CURSOR_API_KEY", "CLAUDE_ROUTINE_URL", "CLAUDE_ROUTINE_TOKEN",
                "GEMINI_API_KEY", "PERPLEXITY_API_KEY", "XAI_API_KEY",
            ],
            re.findall(r'"([A-Z0-9_]+)"', copier),
        )
        self.assertNotIn('-Value "true"', copier)
        seed_block = self.script.split("$providerCredentialSource =", 1)[1].split(
            "$identity =", 1
        )[0]
        self.assertIn(
            "Copy-ProviderCredentialValues -SourceValues $primaryValues -ProfileText $profileText",
            seed_block,
        )
        for forbidden in ("PERPLEXITY_ENABLED", "GROK_ENABLED", '-Value "true"'):
            self.assertNotIn(forbidden, seed_block)

    def test_credential_seeding_never_copies_or_synthesizes_enable_flags(self):
        probe = (
            POWERSHELL_PRELUDE
            + _function_source(self.script, "Set-DotEnvValue")
            + "\n"
            + _function_source(self.script, "Copy-ProviderCredentialValues")
            + r"""
$source = @{
    OPENAI_API_KEY = "sk-source"
    CURSOR_API_KEY = "cur-source"
    CLAUDE_ROUTINE_URL = "https://routine.example/source"
    CLAUDE_ROUTINE_TOKEN = "routine-source"
    GEMINI_API_KEY = "gem-source"
    PERPLEXITY_API_KEY = "pplx-source"
    XAI_API_KEY = "xai-source"
    GROK_ENABLED = "true"
    PERPLEXITY_ENABLED = "true"
    GROK_CLI_ENABLED = "true"
    USAGE_CHECK_PROVIDERS = "openai,claude,cursor,grok,perplexity"
    ORCHESTRATOR_SHARED_SECRET = "shared-source"
}
$targetText = @(
    "OPENAI_API_KEY=",
    "XAI_API_KEY=",
    "PERPLEXITY_API_KEY=",
    "GEMINI_API_KEY=",
    "GROK_ENABLED=false",
    "PERPLEXITY_ENABLED=false",
    "GROK_CLI_ENABLED=false",
    "USAGE_CHECK_PROVIDERS=openai,claude,cursor",
    "ORCHESTRATOR_SHARED_SECRET=target-secret"
) -join "`n"
$seeded = Copy-ProviderCredentialValues -SourceValues $source -ProfileText $targetText
Write-Output "BEGIN-SEEDED"
Write-Output $seeded
Write-Output "END-SEEDED"
"""
        )
        output = _run_powershell(probe)
        seeded = output.split("BEGIN-SEEDED", 1)[1].split("END-SEEDED", 1)[0]
        lines = [line.strip() for line in seeded.splitlines() if line.strip()]
        for expected in (
            "OPENAI_API_KEY=sk-source",
            "CURSOR_API_KEY=cur-source",
            "CLAUDE_ROUTINE_URL=https://routine.example/source",
            "CLAUDE_ROUTINE_TOKEN=routine-source",
            "GEMINI_API_KEY=gem-source",
            "PERPLEXITY_API_KEY=pplx-source",
            "XAI_API_KEY=xai-source",
            "GROK_ENABLED=false",
            "PERPLEXITY_ENABLED=false",
            "GROK_CLI_ENABLED=false",
            "USAGE_CHECK_PROVIDERS=openai,claude,cursor",
            "ORCHESTRATOR_SHARED_SECRET=target-secret",
        ):
            self.assertIn(expected, lines)
        for flag in ("GROK_ENABLED", "PERPLEXITY_ENABLED", "GROK_CLI_ENABLED"):
            self.assertEqual(1, sum(1 for line in lines if line.startswith(f"{flag}=")), flag)
            self.assertNotIn(f"{flag}=true", lines)
        self.assertNotIn("shared-source", output)

    def test_n8n_capability_preflight_precedes_every_workflow_mutation(self):
        self.assertLess(
            self.script.index("function Assert-N8nWorkflowPublishCapability"),
            self.script.index("function Initialize-N8nIntakeWorkflow"),
        )
        initializer = self.script.split("function Initialize-N8nIntakeWorkflow", 1)[1]
        preflight = initializer.index("run --rm --no-deps -T n8n --help")
        exit_check = initializer.index("$LASTEXITCODE -ne 0", preflight)
        capability = initializer.index("Assert-N8nWorkflowPublishCapability -HelpOutput $helpOutput")
        stop = initializer.index("stop n8n")
        database = initializer.index("up -d --wait --wait-timeout 120 n8n-db")
        listing = initializer.index("list:workflow --onlyId")
        unpublish = initializer.index("unpublish:workflow --id=$workflowId")
        importing = initializer.index("import:workflow --input=/workflows/director-intake.json")
        publish = initializer.index("n8n publish:workflow --id=$workflowId")
        readback = initializer.index("list:workflow --active=true --onlyId")
        self.assertLess(preflight, exit_check)
        self.assertLess(exit_check, capability)
        self.assertLess(capability, stop)
        self.assertLess(stop, database)
        self.assertLess(database, listing)
        self.assertLess(listing, unpublish)
        self.assertLess(unpublish, importing)
        self.assertLess(importing, publish)
        self.assertLess(publish, readback)
        self.assertEqual(1, initializer.count("--help"))
        capability_fn = _function_source(self.script, "Assert-N8nWorkflowPublishCapability")
        self.assertIn('@("publish:workflow", "unpublish:workflow")', capability_fn)
        self.assertIn("[Regex]::Escape($_)", capability_fn)
        self.assertIn("-cmatch $heading", capability_fn)
        self.assertNotIn("update:workflow", self.script)

    def test_n8n_capability_preflight_requires_both_exact_commands(self):
        probe = (
            POWERSHELL_PRELUDE
            + _function_source(self.script, "Assert-N8nWorkflowPublishCapability")
            + r"""
$esc = [char]27
$scenarios = [ordered]@{
    "pinned-help" = @("Available commands:", "  publish:workflow:", "    Publish a workflow", "  unpublish:workflow:", "    Unpublish a workflow", "  update:workflow:", "    [DEPRECATED]")
    "colored" = @(("  " + $esc + "[1mpublish:workflow:" + $esc + "[0m"), ("  " + $esc + "[1munpublish:workflow:" + $esc + "[0m"))
    "substring-only" = @("COMMANDS", "  unpublish:workflow:")
    "publish-only" = @("COMMANDS", "  publish:workflow:")
    "deprecated-only" = @("COMMANDS", "  update:workflow:", "    [DEPRECATED]")
    "glued" = @("COMMANDS", "  publish:workflow:unpublish:workflow:")
    "bare-tokens" = @("COMMANDS", "  publish:workflow", "  unpublish:workflow")
    "empty" = @()
}
foreach ($entry in $scenarios.GetEnumerator()) {
    try {
        Assert-N8nWorkflowPublishCapability -HelpOutput ([string[]]$entry.Value)
        Write-Output ($entry.Key + "|ok|")
    }
    catch {
        Write-Output ($entry.Key + "|error|" + $_.Exception.Message)
    }
}
"""
        )
        results = _scenario_results(_run_powershell(probe))
        self.assertEqual(("ok", ""), results["pinned-help"])
        self.assertEqual(("ok", ""), results["colored"])
        for name, fragment in (
            ("substring-only", "commands: publish:workflow."),
            ("publish-only", "commands: unpublish:workflow."),
            ("deprecated-only", "commands: publish:workflow, unpublish:workflow."),
            ("glued", "commands: unpublish:workflow."),
            ("bare-tokens", "commands: publish:workflow, unpublish:workflow."),
            ("empty", "commands: publish:workflow, unpublish:workflow."),
        ):
            outcome, detail = results[name]
            self.assertEqual("error", outcome, name)
            self.assertIn(fragment, detail, name)
            self.assertIn("No workflow was modified", detail, name)

    def test_compose_project_is_known_before_any_fallible_profile_validation(self):
        derivation = self.script.index(
            "$composeProject = [string]$selected.DIRECTOR_COMPOSE_PROJECT_NAME"
        )
        env_binding = self.script.index(
            '[Environment]::SetEnvironmentVariable("DIRECTOR_ENV_FILE", $profilePath, "Process")'
        )
        self.assertIn("$composeProject = $null", self.script)
        self.assertLess(self.script.index("$selected = $profiles[$ProfileName]"), derivation)
        self.assertLess(derivation, env_binding)
        for fallible in (
            "-SkipProviderChecks cannot be combined with -EnablePaidRuns",
            "Missing $profileTemplate",
            "& icacls.exe",
            "$profileText = Get-Content",
            "Assert-ProfileIdentity -Values $values",
            "Assert-LongTableSnapshot -Values $values",
            "Assert-NoProfileCollisions -TargetValues $values",
            "Assert-Configured -Values $values -Names $requiredConfiguration",
        ):
            self.assertLess(env_binding, self.script.index(fallible), fallible)
        # Inverse: the project is never re-derived from the profile after
        # validation, so rollback can never observe a null project name.
        self.assertNotIn('$composeProject = [string]$values[', self.script)
        self.assertEqual(1, self.script.count("$composeProject = [string]"))
        outer_catch = self.script[
            self.script.index("catch {\n    $outerFailure = $_") :
            self.script.rindex("finally {")
        ]
        self.assertIn(
            "docker compose --project-name $composeProject --env-file $profilePath "
            "stop orchestrator slackbot n8n n8n-db | Out-Null",
            outer_catch,
        )

    def test_previous_activation_is_stopped_and_verified_before_every_preflight_step(self):
        call = (
            "Stop-PreviouslyRunningServices -ComposeProject $composeProject "
            '-ProfilePath $profilePath -Services @("orchestrator", "slackbot")'
        )
        self.assertEqual(1, self.script.count(call))
        stop = self.script.index(call)
        self.assertLess(self.script.index("function Stop-PreviouslyRunningServices"), stop)
        # The profile exists before Compose is asked to load it, and nothing
        # fallible about the profile itself runs before the stop.
        self.assertLess(
            self.script.index("Copy-Item -LiteralPath $profileTemplate -Destination $profilePath"),
            stop,
        )
        preflight = {
            "acl": "& icacls.exe",
            "profile read": "$profileText = Get-Content",
            "identity": "Assert-ProfileIdentity -Values $values",
            "snapshot validation": "Assert-LongTableSnapshot -Values $values",
            "collisions": "Assert-NoProfileCollisions -TargetValues $values",
            "configuration": "Assert-Configured -Values $values -Names $requiredConfiguration",
            "compose config": "config --quiet",
            "n8n mutation": "Initialize-N8nIntakeWorkflow `",
            "image build": "$profilePath build orchestrator",
            "store inspection": "list_recoverable()",
            "provider prevalidation": "$expectedUsageProviders = @(Get-ValidatedUsageCheckProviders",
            "paid switches": (
                "Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $false"
            ),
        }
        for name, marker in preflight.items():
            self.assertLess(stop, self.script.index(marker), name)
        # Inverse: nothing between the stop and the intentional paid-closed
        # start brings the orchestrator or Slack intake back.
        closed_start = self.script.index("if (-not $EnablePaidRuns) {", stop)
        held = self.script[stop + len(call) : closed_start]
        for line in held.splitlines():
            if "docker compose" in line:
                self.assertIsNone(re.search(r"\b(up|start|restart|unpause)\b", line), line)
        # The only container start inside the n8n initializer is its database.
        # The initializer is the script's last function, so bound it by the
        # statement that follows it rather than by the next definition.
        initializer = self.script[
            self.script.index("function Initialize-N8nIntakeWorkflow") :
            self.script.index('if ($ApiOnly -and $ProfileName -notin @("edge-oracle", "long-table"))')
        ]
        self.assertEqual(
            ["up -d --wait --wait-timeout 120 n8n-db"],
            re.findall(r"up -d[^\n]*", initializer),
        )
        # Inverse: the paid path recreates the validated service only after
        # provider prevalidation and the paid-check switches, with no other
        # start in between.
        prevalidation = self.script.index(preflight["provider prevalidation"])
        paid_switches = self.script.index(preflight["paid switches"])
        paid_start = self.script.index("up -d --build --force-recreate orchestrator")
        self.assertLess(prevalidation, paid_switches)
        self.assertLess(paid_switches, paid_start)
        self.assertEqual(0, self.script.count(" up -d", prevalidation, paid_start))
        self.assertIn("stop slackbot", self.script[paid_switches:paid_start])

    def test_previous_activation_stop_fails_closed_and_verifies_after_stopping(self):
        probe = (
            POWERSHELL_PRELUDE
            + _function_source(self.script, "Stop-PreviouslyRunningServices")
            + r"""
$global:mode = ""
$global:calls = New-Object System.Collections.Generic.List[string]
function docker {
    $call = @($args | ForEach-Object { [string]$_ }) -join " "
    $global:calls.Add($call)
    if ($call -match " stop ") {
        $global:LASTEXITCODE = $(if ($global:mode -eq "stop-fails") { 1 } else { 0 })
        return
    }
    if ($call -match " ps ") {
        if ($global:mode -eq "verify-fails") { $global:LASTEXITCODE = 1; return }
        $global:LASTEXITCODE = 0
        if ($global:mode -eq "still-live") { Write-Output "0123456789ab"; Write-Output "" }
        if ($global:mode -eq "blank-lines") { Write-Output ""; Write-Output "   " }
        return
    }
    throw "unexpected docker call: $call"
}
foreach ($scenario in @("clean", "blank-lines", "stop-fails", "still-live", "verify-fails")) {
    $global:mode = $scenario
    $global:calls.Clear()
    try {
        Stop-PreviouslyRunningServices -ComposeProject "probe-project" -ProfilePath "probe.env" -Services @("orchestrator", "slackbot")
        Write-Output ($scenario + "|ok|")
    }
    catch {
        Write-Output ($scenario + "|error|" + $_.Exception.Message)
    }
    Write-Output ($scenario + "-calls|" + $global:calls.Count + "|" + ($global:calls -join ";"))
}
"""
        )
        results = _scenario_results(_run_powershell(probe))
        stop_call = (
            "compose --project-name probe-project --env-file probe.env stop orchestrator slackbot"
        )
        verify_call = (
            "compose --project-name probe-project --env-file probe.env ps --quiet "
            "--status running --status paused --status restarting orchestrator slackbot"
        )
        self.assertEqual(("ok", ""), results["clean"])
        self.assertEqual(("2", f"{stop_call};{verify_call}"), results["clean-calls"])
        self.assertEqual(("ok", ""), results["blank-lines"])
        outcome, detail = results["stop-fails"]
        self.assertEqual("error", outcome)
        self.assertIn(
            "Unable to stop the previously running orchestrator and slackbot services "
            "of Compose project probe-project",
            detail,
        )
        # Inverse: a failed stop is reported as such; verification never runs
        # afterwards to mask it.
        self.assertEqual(("1", stop_call), results["stop-fails-calls"])
        outcome, detail = results["still-live"]
        self.assertEqual("error", outcome)
        self.assertIn("still has 1 live orchestrator and slackbot container(s)", detail)
        self.assertIn("No preflight step was started", detail)
        self.assertEqual(("2", f"{stop_call};{verify_call}"), results["still-live-calls"])
        outcome, detail = results["verify-fails"]
        self.assertEqual("error", outcome)
        self.assertIn(
            "Unable to verify that the orchestrator and slackbot services of Compose "
            "project probe-project are stopped",
            detail,
        )
        self.assertEqual(("2", f"{stop_call};{verify_call}"), results["verify-fails-calls"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:34 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:15 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 09:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:54 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 10:18 UTC | END]]
