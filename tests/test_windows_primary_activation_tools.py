# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 18:21 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:10 UTC | BEGIN]]
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsPrimaryActivationToolTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (ROOT / "scripts" / name).read_text(encoding="utf-8")

    def test_snapshot_creation_is_exact_and_refuses_replacement(self) -> None:
        text = self.text("windows-primary-snapshot.ps1")
        self.assertIn("Snapshot target already exists", text)
        self.assertIn("--no-hardlinks", text)
        self.assertIn("ExpectedCommit", text)
        self.assertIn("ExpectedTree", text)
        self.assertNotIn("reset --hard", text)
        self.assertNotIn("git clean", text)
        self.assertNotIn("Remove-Item", text)

    def test_control_tool_has_separate_migration_backup_restore_and_fenced_rollback(self) -> None:
        text = self.text("windows-langgraph-primary-control.ps1")
        for action in (
            '"Preflight"',
            '"Accept"',
            '"Migrate"',
            '"Backup"',
            '"RestoreDrill"',
            '"Health"',
            '"Activate"',
            '"Rollback"',
        ):
            self.assertIn(action, text)
        self.assertIn("open_postgres_control_plane", text)
        self.assertIn("pg_dump", text)
        self.assertIn("pg_restore", text)
        self.assertIn("primary_activation_evidence", text)
        self.assertIn("required_control_plane_tables", text)
        self.assertIn("checkpoint_tables", text)
        self.assertIn("sha256sum -- $temporary", text)
        self.assertIn("Invoke-Evidence -Command verify-backup", text)
        self.assertIn('`"backup_digest`":`"sha256:$restoreDigest`"', text)
        restore_start = text.index('if ($Action -eq "RestoreDrill")')
        verify = text.index("Invoke-Evidence -Command verify-backup", restore_start)
        restore = text.index("pg_restore", restore_start)
        self.assertLess(verify, restore)
        self.assertIn("Invoke-Evidence -Command require-ready", text)
        self.assertIn("ambiguous effects block rollback", text)
        self.assertIn("q.rollback_safe", text)
        self.assertNotIn("assert m.ambiguous_effect_count", text)
        self.assertNotIn("assert q.rollback_safe", text)
        self.assertIn("Stop-And-Prove-AllIntake", text)
        self.assertIn('Invoke-Compose stop slackbot n8n orchestrator', text)
        self.assertIn("bind-legacy-inventory", text)
        self.assertIn("Get-GitHubAcceptanceFacts", text)
        self.assertIn("Get-ExactReviewArtifact", text)
        self.assertIn('schema_version = "director-activation-review/v1"', text)
        self.assertIn('[string]$Review.state -cne "APPROVED"', text)
        self.assertIn("authorAssociation", text)
        self.assertIn("ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON", text)
        self.assertIn("ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON", text)
        self.assertIn('allowlist must be a JSON array', text)
        self.assertIn("Pull request head does not match the exact activation candidate", text)
        self.assertIn("The pull request author cannot self-accept", text)
        self.assertIn("Invoke-Evidence -Command record -Stage ACCEPTED", text)
        self.assertIn("Assert-CanonicalAcceptanceArtifact", text)
        self.assertIn('[string]$artifact.version -cne "director-primary-acceptance-artifact/v1"', text)
        self.assertIn("Accept requires -AcceptanceArtifactPath from the independent evidence issuers", text)
        self.assertIn("-AcceptanceArtifactPath $verifiedArtifactPath", text)
        self.assertIn('"--acceptance-artifact", "/run/director-acceptance.json"', text)
        self.assertNotIn("-Stage ACCEPTED -EvidenceDigest", text)
        self.assertNotIn("HMACSHA256", text)
        self.assertNotIn("New-TemporaryFile", text)
        # Free-form comments and quoted markers are not review authority. Only an
        # exact canonical JSON body on an APPROVED trusted review can match.
        github_view = text[text.index("gh pr view") : text.index("$pull =", text.index("gh pr view"))]
        self.assertNotIn("comments", github_view)
        review_parser = text[text.index("function Get-ExactReviewArtifact") : text.index("function Get-GitHubAcceptanceFacts")]
        self.assertIn("[string]$Review.body -cne $canonical", review_parser)
        self.assertNotIn(".Contains(", review_parser)
        self.assertNotIn("DIRECTOR_SECURITY_REVIEW=ACCEPT", text)
        self.assertIn("database_instance_id", text)
        self.assertIn('"firecrawl", "github-copilot", "microsoft-365-copilot"', text)
        self.assertNotIn('"firecrawl", "github-copilot", "windows-copilot"', text)
        self.assertLess(
            text.index("Invoke-Compose up -d langgraph-primary-db"),
            text.index("$databaseBinding = Get-DatabaseBinding"),
        )
        self.assertIn("Primary activation failed and was unwound", text)
        self.assertIn("acquire_intake_authority(intake_owner='legacy'", text)
        self.assertLess(
            text.index("Stop-And-Prove-AllIntake", text.index('if ($Action -eq "Activate")')),
            text.index('LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "true"'),
        )
        activation = text[text.index('if ($Action -eq "Activate")'):text.index('if ($Action -eq "Rollback")')]
        self.assertLess(activation.index("bind-legacy-inventory"), activation.index("require-ready"))
        self.assertIn("finally { Write-Switches", activation)

    def test_host_wrapper_never_accepts_direct_provider_lanes(self) -> None:
        text = self.text("windows-host-provider-bridge.ps1")
        self.assertIn('@("cursor", "microsoft-365-copilot")', text)
        self.assertIn("Preflight", text)
        self.assertIn("no provider was invoked", text)

    def test_postgres_test_lane_mirrors_ci_and_refuses_foreign_containers(self) -> None:
        text = self.text("windows-postgres-test-lane.ps1")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for action in ('"Start"', '"Stop"', '"Status"', '"Dsn"', '"Test"'):
            self.assertIn(action, text)
        pattern = re.compile(r"postgres:17-alpine@sha256:[0-9a-f]{64}")
        self.assertEqual(len(pattern.findall(text)), 1)
        digests = set(pattern.findall(text)) | set(pattern.findall(compose)) | set(pattern.findall(workflow))
        self.assertEqual(len(digests), 1)
        self.assertIn('$databaseUser = "director_ci"', text)
        self.assertIn('$databaseName = "director_test"', text)
        self.assertIn("[int]$HostPort = 55433", text)
        self.assertIn('$dsn = "postgresql://${databaseUser}@127.0.0.1:${HostPort}/${databaseName}?sslmode=disable"', text)
        self.assertIn("postgresql://director_ci@127.0.0.1:55433/director_test?sslmode=disable", workflow)
        self.assertIn('--publish "127.0.0.1:${HostPort}:5432"', text)
        self.assertIn("POSTGRES_HOST_AUTH_METHOD=trust", text)
        self.assertIn("docker run --detach --rm", text)
        self.assertIn("pg_isready -h 127.0.0.1 -U $databaseUser -d $databaseName", text)
        self.assertIn("[DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)", text)
        self.assertIn("Net.Sockets.TcpClient", text)
        self.assertIn("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", text)
        self.assertIn("LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER", text)
        self.assertIn("director-orchestrator.postgres-test-lane", text)
        self.assertIn("was not created by this script", text)
        self.assertIn('--filter "publish=$HostPort"', text)
        self.assertIn("docker rm --force --volumes", text)
        for forbidden in (
            "docker compose", "docker start", "docker inspect", "langgraph-primary-db",
            "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD", "Read-DotEnv", "0.0.0.0", "Remove-Item",
            "-AsHashtable", "ToHexString", "$IsWindows", " && ", " || ", " ?? ",
        ):
            self.assertNotIn(forbidden, text)
        start = text.index("function Start-Lane")
        self.assertLess(text.index("Get-LaneContainer", start), text.index("docker run", start))
        self.assertLess(text.index("Wait-LaneReady", start), text.index("Export-LaneEnvironment", start))
        test_section = text[text.index('if ($Action -eq "Dsn")'):]
        self.assertIn("finally {", test_section)
        unittest_index = test_section.index("-m unittest")
        self.assertLess(unittest_index, test_section.index("Stop-Lane", unittest_index))
        self.assertIn("$originalProcessEnvironment", test_section)

    def test_postgres_test_lane_is_documented(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("\n## Local validation", 1)[1].split("\n## LangGraph transition planning", 1)[0]
        self.assertIn("windows-postgres-test-lane.ps1 -Action Start", section)
        self.assertIn("windows-postgres-test-lane.ps1 -Action Stop", section)
        self.assertIn("windows-postgres-test-lane.ps1 -Action Test", section)
        self.assertIn("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", section)
        self.assertIn("LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER", section)
        self.assertIn("postgresql://director_ci@127.0.0.1:55433/director_test?sslmode=disable", section)
        self.assertLess(section.index("-Action Start"), section.index("unittest discover"))
        self.assertLess(section.index("unittest discover"), section.index("-Action Stop"))
        operations = (ROOT / "docs" / "langgraph-primary-operations.md").read_text(encoding="utf-8")
        tooling = operations.split("## Windows Docker activation tooling", 1)[1]
        self.assertIn("scripts/windows-postgres-test-lane.ps1", tooling)
        self.assertIn("not the primary database", tooling)
        self.assertIn("activation evidence", tooling)

    def test_postgres_test_lane_skill_is_discoverable_and_complete(self) -> None:
        skill = (ROOT / ".agents" / "skills" / "postgres-test-lane" / "SKILL.md").read_text(encoding="utf-8")
        frontmatter, body = skill.split("---", 2)[1:]
        self.assertIn("name: postgres-test-lane", frontmatter)
        description_line = next(line for line in frontmatter.splitlines() if line.startswith("description:"))
        description = description_line[len("description:"):].strip().strip('"')
        # Codex lists a repository skill as "<name>: <description>" cut near 80
        # characters before any file is opened, so the triggers must lead.
        for token in ("Postgres", "tests", "windows-postgres-test-lane.ps1", "docker run"):
            self.assertIn(token, description[:80])
        self.assertIn("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", description)
        self.assertIn("report the gap", description)
        for phrase in (
            "when_to_use:",
            "run the tests",
            "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required",
            "docker run postgres",
            "55433",
            "no Docker",
            "argument-hint:",
        ):
            self.assertIn(phrase, frontmatter)
        for required in (
            "windows-postgres-test-lane.ps1 -Action Start",
            "windows-postgres-test-lane.ps1 -Action Stop",
            "windows-postgres-test-lane.ps1 -Action Test",
            "-TestNames tests.test_langgraph_postgres",
            "-Action Status",
            "-Action Dsn",
            "-Python",
            "-HostPort",
            "-ContainerName",
            "LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER=director-primary-test-pg",
            "postgresql://director_ci@127.0.0.1:55433/director_test?sslmode=disable",
            "director-orchestrator.postgres-test-lane",
            "hand-start",
            "never removes a container it did not create",
            "not the primary database",
            "not a shared provider workflow skill",
            "Cursor Cloud",
            "report the gap",
        ):
            self.assertIn(required, body)
        self.assertLess(body.index("-Action Start"), body.index("unittest discover"))
        self.assertLess(body.index("unittest discover"), body.index("-Action Stop"))
        # The skill names the forbidden act but never supplies a recipe or a digest copy.
        self.assertNotIn("docker run --detach", body)
        self.assertNotIn("sha256:", body)
        postgres_suites = {
            path.name
            for path in (ROOT / "tests").glob("test_*.py")
            if path.name != Path(__file__).name
            and "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(set(re.findall(r"`tests/(test_[a-z_]+\.py)`", body)), postgres_suites)

    def test_agents_rules_route_every_agent_to_the_postgres_test_lane(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        heading = "\n## Postgres-backed unit tests"
        cloud_heading = "\n## Cursor Cloud specific instructions"
        self.assertLess(agents.index(heading), agents.index(cloud_heading))
        section = agents.split(heading, 1)[1].split("\n## ", 1)[0]
        for required in (
            "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN",
            "scripts/windows-postgres-test-lane.ps1 -Action Start",
            "-Action Stop",
            "-Action Test",
            ".agents/skills/postgres-test-lane/SKILL.md",
            "hand-start",
            "docker run",
            "127.0.0.1:55433",
            "did not label",
            "-HostPort",
            "Cursor Cloud",
            "report the gap",
        ):
            self.assertIn(required, section)
        # The rule names the forbidden act but never supplies a recipe for it.
        self.assertNotIn("docker run --detach", section)
        self.assertNotIn("POSTGRES_HOST_AUTH_METHOD", section)
        cloud = agents.split(cloud_heading, 1)[1].split("\n## ", 1)[0]
        self.assertIn("Postgres-backed unit tests", cloud)
        self.assertIn("report the gap", cloud)

    def test_claude_code_sessions_import_the_agent_operating_rules(self) -> None:
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        visible = [line.strip() for line in claude.splitlines() if line.strip() and "[[" not in line]
        self.assertEqual(visible, ["@AGENTS.md"])


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 18:21 UTC | END]]
