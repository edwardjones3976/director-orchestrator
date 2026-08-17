from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UppercutDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrapper = (
            ROOT / "scripts" / "windows-uppercut-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.script = (
            ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        cls.profile = (ROOT / ".env.uppercut.example").read_text(encoding="utf-8")

    def test_profile_is_separate_and_primary_env_is_read_only(self):
        self.assertIn('ProfileName = "project-uppercut"', self.wrapper)
        self.assertIn('ProfilePath = ".env.uppercut"', self.script)
        self.assertNotIn('Set-Content ".env"', self.script)
        self.assertNotIn('WriteAllText((Join-Path (Get-Location) ".env")', self.script)
        self.assertIn("SeedProviderCredentialsFromPrimaryEnv", self.script)

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
        catch_block = self.script[self.script.rindex("\ncatch {") :]
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

    def test_collision_detection_covers_primary_and_both_isolated_profiles(self):
        self.assertIn('@(".env", ".env.uppercut", ".env.edge-oracle")', self.script)
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


if __name__ == "__main__":
    unittest.main()
