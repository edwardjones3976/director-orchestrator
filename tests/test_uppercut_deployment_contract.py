from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UppercutDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (
            ROOT / "scripts" / "windows-uppercut-live-validation.ps1"
        ).read_text(encoding="utf-8")
        cls.compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        cls.profile = (ROOT / ".env.uppercut.example").read_text(encoding="utf-8")

    def test_profile_is_separate_and_primary_env_is_read_only(self):
        self.assertIn('$profilePath = ".env.uppercut"', self.script)
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
        closed = self.script.index('ALLOW_PAID_RUNS = "false"')
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

    def test_paid_validation_closes_slack_and_timestamps_before_api_start(self):
        paid_section = self.script.index("\ntry {", self.script.index("Write-ProfileSwitches -PaidRuns $true"))
        stop_slack = self.script.index("stop slackbot", paid_section)
        starts_at = self.script.index("$startedAt = [DateTimeOffset]::UtcNow", paid_section)
        starts_api = self.script.index("up -d --build --force-recreate orchestrator", paid_section)

        self.assertLess(stop_slack, starts_at)
        self.assertLess(starts_at, starts_api)


if __name__ == "__main__":
    unittest.main()
