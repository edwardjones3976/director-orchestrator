# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:10 UTC | BEGIN]]
from __future__ import annotations

import shutil
import subprocess
import tempfile
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
        self.assertIn("SourceRemoteName", text)
        self.assertIn("remote get-url $SourceRemoteName", text)
        self.assertIn(
            "git ls-remote --exit-code --refs -- $sourceRemoteUrl $remoteRef",
            text,
        )
        self.assertIn(
            "--single-branch -- $sourceRemoteUrl $targetPath",
            text,
        )
        self.assertNotIn("--single-branch -- $sourcePath $targetPath", text)
        self.assertNotIn("origin.cursor.com", text)
        self.assertIn("ExpectedCommit", text)
        self.assertIn("ExpectedTree", text)
        self.assertNotIn("reset --hard", text)
        self.assertNotIn("git clean", text)
        self.assertNotIn("Remove-Item", text)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_snapshot_creation_binds_content_to_the_selected_remote(self) -> None:
        commit = "a" * 40
        tree = "b" * 40
        github_url = "https://github.com/example/demo.git"
        cursor_url = "https://origin.cursor.com/example/demo.git"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            target = root / "snapshot"
            rejected_target = root / "rejected"
            mismatched_target = root / "mismatched"
            missing_target = root / "missing"
            wrong_repository_target = root / "wrong-repository"
            unicode_target = root / "unicode"
            unicode_space_target = root / "unicode-space"
            harness = r'''
$ErrorActionPreference = "Stop"
$global:gitCalls = [System.Collections.Generic.List[string]]::new()
function global:git {
    $arguments = @($args)
    $global:gitCalls.Add(($arguments -join [char]31))
    $global:LASTEXITCODE = 0
    if ($arguments -contains "get-url") {
        if ($arguments[-1] -eq "github") { Write-Output "__GITHUB_URL__"; return }
        if ($arguments[-1] -eq "origin") {
            if ([IO.Path]::GetFullPath($arguments[1]) -eq [IO.Path]::GetFullPath("__SOURCE__")) {
                Write-Output "__CURSOR_URL__"
            } else {
                Write-Output "__GITHUB_URL__"
            }
            return
        }
        if ($arguments[-1] -eq "missing") { $global:LASTEXITCODE = 2; return }
        if ($arguments[-1] -eq "unicode") { Write-Output "https://gıthub.com/example/demo.git"; return }
        if ($arguments[-1] -eq "unicode-space") {
            Write-Output "$([char]0x00A0)https://github.com/example/demo.git$([char]0x2003)"
            return
        }
    }
    if ($arguments[0] -eq "ls-remote") {
        Write-Output "__COMMIT__`trefs/heads/main"
        return
    }
    if ($arguments[0] -eq "clone") {
        New-Item -ItemType Directory -Path (Join-Path $arguments[-1] ".git") -Force | Out-Null
        return
    }
    if ($arguments -contains "set-url" -or $arguments -contains "status") { return }
    if ($arguments -contains "rev-parse") {
        if ($arguments[-1] -eq 'HEAD^{tree}') { Write-Output "__TREE__" } else { Write-Output "__COMMIT__" }
        return
    }
    throw "Unexpected fake git invocation: $($arguments -join ' ')"
}

& "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName github `
    -Target "__TARGET__" -ExpectedRepository example/demo -StartingRef main `
    -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
$rejected = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -Target "__REJECTED__" `
        -ExpectedRepository example/demo -StartingRef main `
        -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "does not match the repository allowlist") { throw }
    $rejected = $true
}
if (-not $rejected) { throw "Cursor origin was not rejected." }
if (Test-Path -LiteralPath "__REJECTED__") { throw "Rejected origin created a target." }
$mismatched = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName github `
        -Target "__MISMATCHED__" -ExpectedRepository example/demo -StartingRef main `
        -ExpectedCommit "cccccccccccccccccccccccccccccccccccccccc" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "does not match the frozen authority") { throw }
    $mismatched = $true
}
if (-not $mismatched) { throw "A mismatched remote head was not rejected." }
if (Test-Path -LiteralPath "__MISMATCHED__") { throw "A mismatched remote head created a target." }
$missing = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName missing `
        -Target "__MISSING__" -ExpectedRepository example/demo -StartingRef main `
        -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "no verifiable selected remote") { throw }
    $missing = $true
}
if (-not $missing) { throw "A missing selected remote was not rejected." }
if (Test-Path -LiteralPath "__MISSING__") { throw "A missing remote created a target." }
$wrongRepository = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName github `
        -Target "__WRONG_REPOSITORY__" -ExpectedRepository example/other -StartingRef main `
        -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "does not match the repository allowlist") { throw }
    $wrongRepository = $true
}
if (-not $wrongRepository) { throw "A wrong repository was not rejected." }
if (Test-Path -LiteralPath "__WRONG_REPOSITORY__") { throw "A wrong repository created a target." }
$unicode = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName unicode `
        -Target "__UNICODE__" -ExpectedRepository example/demo -StartingRef main `
        -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "does not match the repository allowlist") { throw }
    $unicode = $true
}
if (-not $unicode) { throw "A Unicode GitHub lookalike was not rejected." }
if (Test-Path -LiteralPath "__UNICODE__") { throw "A Unicode lookalike created a target." }
$unicodeSpace = $false
try {
    & "__SCRIPT__" -Action Create -Source "__SOURCE__" -SourceRemoteName unicode-space `
        -Target "__UNICODE_SPACE__" -ExpectedRepository example/demo -StartingRef main `
        -ExpectedCommit "__COMMIT__" -ExpectedTree "__TREE__"
} catch {
    if ($_.Exception.Message -notmatch "does not match the repository allowlist") { throw }
    $unicodeSpace = $true
}
if (-not $unicodeSpace) { throw "Unicode edge whitespace was not rejected." }
if (Test-Path -LiteralPath "__UNICODE_SPACE__") { throw "Unicode edge whitespace created a target." }
$global:gitCalls | ForEach-Object { Write-Output $_ }
'''
            replacements = {
                "__SCRIPT__": str(ROOT / "scripts" / "windows-primary-snapshot.ps1"),
                "__SOURCE__": str(source),
                "__TARGET__": str(target),
                "__REJECTED__": str(rejected_target),
                "__MISMATCHED__": str(mismatched_target),
                "__MISSING__": str(missing_target),
                "__WRONG_REPOSITORY__": str(wrong_repository_target),
                "__UNICODE__": str(unicode_target),
                "__UNICODE_SPACE__": str(unicode_space_target),
                "__GITHUB_URL__": github_url,
                "__CURSOR_URL__": cursor_url,
                "__COMMIT__": commit,
                "__TREE__": tree,
            }
            for marker, value in replacements.items():
                harness = harness.replace(marker, value.replace('"', '`"'))

            completed = subprocess.run(
                ("pwsh", "-NoProfile", "-NonInteractive", "-Command", harness),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((target / ".git").is_dir())
            self.assertFalse(rejected_target.exists())
            self.assertFalse(mismatched_target.exists())
            self.assertFalse(missing_target.exists())
            self.assertFalse(wrong_repository_target.exists())
            self.assertFalse(unicode_target.exists())
            self.assertFalse(unicode_space_target.exists())
            calls = [
                line.split(chr(31))
                for line in completed.stdout.splitlines()
                if chr(31) in line
            ]
            self.assertIn(
                ["-C", str(source), "remote", "get-url", "github"],
                calls,
            )
            self.assertIn(
                [
                    "ls-remote",
                    "--exit-code",
                    "--refs",
                    github_url,
                    "refs/heads/main",
                ],
                calls,
            )
            clone = next(arguments for arguments in calls if arguments[0] == "clone")
            self.assertIn(github_url, clone)
            self.assertNotIn(str(source), clone)

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


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | END]]
