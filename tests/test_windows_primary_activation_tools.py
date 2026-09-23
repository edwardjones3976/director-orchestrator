# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:40 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:17 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:52 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:19 UTC | BEGIN]]
# OpenAI continuation repairs attribution only here; prior material from
# Copilot branch head 510251c66a78251bd242e8f773a03c51bf65f99b is preserved.
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:27 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 17:28 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 12:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 01:54 UTC | BEGIN]]
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
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsPrimaryActivationToolTests(unittest.TestCase):
    def text(self, name: str) -> str:
        # read_text opens in text mode with newline=None, so a CRLF checkout
        # is already folded to LF before any assertion below sees it.
        return (ROOT / "scripts" / name).read_text(encoding="utf-8")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_control_requires_only_the_selected_acceptance_identity_configuration(self):
        script = self.text("windows-langgraph-primary-control.ps1")
        # Execute the actual required-field block without environment reads or Docker.
        selection = script[script.index("$required = @("):script.index("$missing = @($required")]
        for mode in (None, "github-v1", "owner-local-evidence-v1", "provider-v2", "unknown"):
            values = "$values = @{}" if mode is None else "$values = @{LANGGRAPH_PRIMARY_ACCEPTANCE_MODE='" + mode + "'}"
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference='Stop'; " + values + "\n" + selection + "\nConvertTo-Json -Compress -InputObject @($required)"],
                capture_output=True, text=True, timeout=300)
            if mode == "unknown":
                self.assertNotEqual(0, result.returncode)
                continue
            self.assertEqual(0, result.returncode, result.stderr)
            required = set(json.loads(result.stdout))
            for name in ("LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET", "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
                         "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET", "LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON"):
                self.assertIn(name, required)
            for name in ("LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON",
                         "LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON"):
                self.assertEqual(mode not in {"provider-v2", "provider-v3-two-party"}, name in required)
            for suffix in ("REGISTRY_PATH", "REGISTRY_SHA256", "JOURNAL_PATH", "REGISTRY_DIRECTORY"):
                needs_field = mode in {"provider-v2", "provider-v3-two-party"} or (mode == "owner-local-evidence-v1" and suffix == "JOURNAL_PATH")
                self.assertEqual(needs_field, "LANGGRAPH_PRIMARY_ACCEPTANCE_" + suffix in required)
            if mode == "owner-local-evidence-v1":
                self.assertIn("LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_PATH", required)
                self.assertIn("LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256", required)

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
        # The remote lookup selects the exact ref rather than trusting a row count.
        self.assertIn("$fields[1] -ceq $remoteRef", text)
        self.assertNotIn("$remoteRows.Count -ne 1", text)
        self.assertNotIn("--single-branch -- $sourcePath $targetPath", text)
        self.assertNotIn("origin.cursor.com", text)
        self.assertIn("ExpectedCommit", text)
        self.assertIn("ExpectedTree", text)
        self.assertNotIn("reset --hard", text)
        self.assertNotIn("git clean", text)
        self.assertNotIn("Remove-Item", text)

    def test_snapshot_clone_writes_a_working_tree_that_matches_its_blobs(self) -> None:
        # The snapshot is created on Windows and consumed by the Linux
        # orchestrator container through a read-only bind. A host with
        # core.autocrlf=true writes a CRLF working tree over LF blobs, which the
        # host's own git then hides, because it applies the same conversion when
        # it compares. The container applies none, sees every line as modified,
        # and refuses the authority in primary_default with "Default primary
        # repository authority must be clean".
        #
        # Measured on this repository at the time of the fix: without these
        # settings every one of 317 tracked files differed from its blob byte
        # for byte, while the script's own cleanliness check still reported the
        # snapshot clean. Certifying a snapshot the consumer rejects is worse
        # than failing to build one, so the settings are pinned here.
        text = self.text("windows-primary-snapshot.ps1")
        # `git clone -c` writes these into the new repository before checkout,
        # so the initial checkout uses them and later checkouts keep using them.
        # `git -c ... clone` would apply to the clone only and not persist.
        self.assertIn(
            "git clone -c core.autocrlf=false -c core.eol=lf --no-hardlinks",
            text,
        )

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
        # ls-remote tail-matches: a decoy branch answers the same pattern and
        # sorts before the real ref, so the script must select the exact ref.
        Write-Output "__DECOY__`trefs/heads/decoy/refs/heads/main"
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
                timeout=300,
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
        self.assertIn("[string]$artifact.version -cne $ExpectedVersion", text)
        self.assertIn(
            '-ExpectedVersion "director-primary-acceptance-artifact/owner-local-evidence-v1"',
            text,
        )
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
        self.assertIn('"firecrawl", "github-copilot"', text)
        self.assertNotIn('"firecrawl", "github-copilot", "windows-copilot"', text)
        self.assertNotIn("Invoke-Compose up -d langgraph-primary-db", text)
        self.assertNotIn("Invoke-Compose build orchestrator", text)
        self.assertIn("Get-DatabaseBinding -Bootstrap", text)
        self.assertIn("Invoke-Evidence -Command require-accepted", text)
        self.assertEqual(text.count("accepted_storage_identity='$databaseIdentity', accepted_instance_id='$databaseInstanceId'"), 2)
        self.assertIn("Get-ResolvedDeploymentDigest", text)
        self.assertIn("$env:DIRECTOR_RUNTIME_IMAGE = $runtimeImageId", text)
        self.assertIn('Assert-RunningCandidate -AsgiApp "director_orchestrator.primary_staging:app"', text)
        self.assertIn("Primary activation failed and was unwound", text)
        self.assertIn("acquire_intake_authority(intake_owner='legacy'", text)
        activate = 'if ($Action -in @("Activate", "ActivateOffline"))'
        self.assertLess(
            text.index("Stop-And-Prove-AllIntake", text.index(activate)),
            text.index('LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "true"'),
        )
        activation = text[text.index(activate):text.index('if ($Action -eq "Rollback")')]
        self.assertLess(activation.index("bind-legacy-inventory"), activation.index("require-ready"))
        self.assertIn("finally { Write-Switches", activation)
        # Services are found by Compose labels only; no stored ID is restarted.
        self.assertNotIn("docker start", text)
        container = text[text.index("function Get-ServiceContainer"):text.index("function Get-ServiceImageId")]
        self.assertIn("label=com.docker.compose.project=$script:ComposeProject", container)
        self.assertIn("label=com.docker.compose.service=$Service", container)
        # The gateway is created stopped and read back before anything starts it.
        self.assertIn("up --no-start --no-deps --no-build --force-recreate slackbot", activation)
        self.assertIn("Assert-ServiceImage -Service slackbot -Expected $runtimeImageId -Created", activation)
        self.assertNotIn("up -d --no-deps --no-build --force-recreate slackbot", text)
        # Rollback restores the separately recorded prior images; only a missing
        # record falls back to the candidate image, and says so.
        rollback = text[text.index('if ($Action -eq "Rollback")'):]
        self.assertLess(rollback.index("Read-PriorImages"), rollback.index("Stop-And-Prove-AllIntake"))
        self.assertEqual(re.findall(r"Assert-ServiceImage [^\n]*", rollback), [
            "Assert-ServiceImage -Service orchestrator -Expected $orchestratorImage",
            "Assert-ServiceImage -Service slackbot -Expected $gatewayImage -Created",
        ])
        self.assertEqual(rollback.count("$runtimeImageId"), 2)
        self.assertIn("[string]$prior.orchestrator_image_id } else { $runtimeImageId }", rollback)
        self.assertIn("[string]$prior.gateway_image_id } else { $runtimeImageId }", rollback)
        stage = text[text.index('if ($Action -eq "StageRuntime")'):text.index('if ($Action -eq "ValidateProviders")')]
        self.assertLess(stage.index("Save-PriorImages"),
                        stage.index("up -d --no-deps --no-build --force-recreate orchestrator"))
        digest = text[text.index("function Get-ResolvedDeploymentDigest"):text.index("function Assert-RunningCandidate")]
        self.assertNotIn("| & docker", digest)
        self.assertIn("Invoke-NativeBytes", digest)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_native_bytes_transport_is_exact(self) -> None:
        harness = r'''
param($Source, $Python, $InputPath, $OutputPath, $WorkDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
$node = @($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-NativeBytes'}, $true))
if ($node.Count -ne 1) { throw 'expected one production helper' }
. ([scriptblock]::Create($node[0].Extent.Text))
$echo = Invoke-NativeBytes -FilePath $Python -ArgumentList @('-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())') -InputBytes ([IO.File]::ReadAllBytes($InputPath))
[IO.File]::WriteAllBytes($OutputPath, $echo)
Set-Location -LiteralPath $WorkDirectory
$cwd = Invoke-NativeBytes -FilePath $Python -ArgumentList @('-c', 'import os,sys;sys.stdout.buffer.write(os.getcwd().encode())')
'cwd=' + [Text.Encoding]::UTF8.GetString($cwd)
try { $null = Invoke-NativeBytes -FilePath $Python -ArgumentList @('-c', 'import sys;sys.exit(3)'); 'exit=accepted' }
catch { 'exit=refused' }
'''
        payload = "config {\"é☃\": 1}\r\nlast".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness.ps1").write_text(harness, encoding="utf-8")
            (root / "input.bin").write_bytes(payload)
            work = root / "work"
            work.mkdir()
            result = subprocess.run(
                [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(root / "harness.ps1"),
                 str(ROOT / "scripts" / "windows-langgraph-primary-control.ps1"), sys.executable,
                 str(root / "input.bin"), str(root / "output.bin"), str(work)],
                capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual((root / "output.bin").read_bytes(), payload)
            observed = next(line for line in result.stdout.splitlines() if line.startswith("cwd="))
            self.assertEqual(Path(observed[4:]).resolve(), work.resolve())
            self.assertIn("exit=refused", result.stdout)
            self.assertNotIn("exit=accepted", result.stdout)

    def test_bridge_install_registers_a_supervised_task_without_starting_it(self) -> None:
        text = self.text("windows-host-provider-bridge.ps1")
        self.assertIn('[ValidateSet("Preflight", "Run", "CheckEnvironment", "Install")]', text)
        install = text[text.index('if ($Action -eq "Install")'):text.index("$optional = @(")]
        self.assertIn("[string]::IsNullOrWhiteSpace($InstallRoot)", install)
        self.assertLess(install.index("if (-not $pinnedEnvironmentFile)"), install.index("Install-BridgeRuntime"))
        self.assertIn("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE", install)
        self.assertLess(install.index("Get-CleanBridgeSource"), install.index("Install-BridgeRuntime"))
        self.assertIn('"$destination.source.json"', install)
        self.assertLess(install.index("Install-BridgeRuntime"), install.index("-Action CheckEnvironment"))
        self.assertLess(install.index("-Action CheckEnvironment"), install.index("Register-BridgeTask"))
        self.assertIn("no task was registered", install)
        register = text[text.index("function Register-BridgeTask"):text.index("$environmentPath = (Resolve-Path")]
        for required in ("-RestartCount", "-MultipleInstances IgnoreNew", "-LogonType Interactive", "-Action Run"):
            self.assertIn(required, register)
        for forbidden in ("Start-ScheduledTask", "Unregister-ScheduledTask", "schtasks", "-Password"):
            self.assertNotIn(forbidden, text)
        # The launcher runs the code beside it and never inherits a stale pinned file.
        self.assertIn('[Environment]::SetEnvironmentVariable("PYTHONPATH", (Join-Path (Split-Path -Parent $PSScriptRoot) "src"), "Process")', text)
        self.assertIn('foreach ($name in $validationNames + @("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE", '
                      '"DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES"))', text)
        # The clean-source check covers exactly the directories the manifest copies.
        manifest = text[text.index("function Get-BridgeRuntimeManifest"):text.index("function Get-CleanBridgeSource")]
        clean = text[text.index("function Get-CleanBridgeSource"):text.index("function Install-BridgeRuntime")]
        copied = re.search(r'foreach \(\$directory in @\(([^)]+)\)\)', manifest).group(1)
        self.assertIn(" ".join(re.findall(r'"([^"]+)"', copied)) + " scripts/windows-host-provider-bridge.ps1", clean)

    @unittest.skipUnless(shutil.which("pwsh") and shutil.which("git"), "PowerShell and git are required")
    def test_bridge_install_requires_a_clean_committed_runtime_source(self) -> None:
        # Only the clean-source helper executes against a throwaway repository.
        harness = r"""
param($Source, $Root)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'bridge script parse failed' }
$node = @($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-CleanBridgeSource'}, $true))
if ($node.Count -ne 1) { throw 'expected one production helper' }
. ([scriptblock]::Create($node[0].Extent.Text))
'source=' + ((Get-CleanBridgeSource -Root $Root) -join '|')
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "repository"
            files = {
                "src/director_orchestrator/__init__.py": "# package\n",
                "docs/schemas/packet.json": "{}\n",
                "docs/provider-profiles/claude.md": "profile\n",
                "prompts/director-system.md": "prompt\n",
                "scripts/windows-host-provider-bridge.ps1": "# launcher\n",
                "README.md": "outside the runtime\n",
                ".gitignore": "__pycache__/\n*.local\n",
            }
            for relative, content in files.items():
                (repository / relative).parent.mkdir(parents=True, exist_ok=True)
                (repository / relative).write_text(content, encoding="utf-8")
            (root / "harness.ps1").write_text(harness, encoding="utf-8")

            def git(*arguments):
                return subprocess.run(["git", "-C", str(repository), "-c", "core.autocrlf=false", *arguments],
                                      capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()

            git("init", "-q")
            git("add", "-A")
            git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "fixture")
            expected = git("rev-parse", "HEAD^{commit}") + "|" + git("rev-parse", "HEAD^{tree}")

            def check():
                return subprocess.run(
                    [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(root / "harness.ps1"),
                     str(ROOT / "scripts" / "windows-host-provider-bridge.ps1"), str(repository)],
                    capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                )

            # Bytecode caches and edits outside the runtime paths do not block.
            (repository / "src/director_orchestrator/__pycache__").mkdir()
            (repository / "src/director_orchestrator/__pycache__/module.cpython-312.pyc").write_bytes(b"\x00")
            (repository / "README.md").write_text("edited\n", encoding="utf-8")
            result = check()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("source=" + expected, result.stdout)

            dirty = {
                "modified": ("prompts/director-system.md", "edited prompt\n"),
                "untracked": ("src/director_orchestrator/extra.py", "VALUE = 1\n"),
                "ignored": ("docs/schemas/operator.local", "not committed\n"),
            }
            for case, (relative, content) in dirty.items():
                with self.subTest(case=case):
                    path = repository / relative
                    original = path.read_text(encoding="utf-8") if path.exists() else None
                    path.write_text(content, encoding="utf-8")
                    try:
                        result = check()
                        self.assertNotEqual(0, result.returncode)
                        self.assertIn("clean checkout of the bridge runtime files", result.stderr)
                        self.assertNotIn("source=", result.stdout)
                    finally:
                        if original is None:
                            path.unlink()
                        else:
                            path.write_text(original, encoding="utf-8")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_bridge_runtime_install_is_content_addressed_and_verified(self) -> None:
        # Only the manifest and copy helpers execute; the Install branch and
        # task registration are never run, even with mocks.
        harness = r"""
param($Source, $SourceRoot, $InstallRoot)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'bridge script parse failed' }
foreach ($name in @('Get-BridgeRuntimeManifest', 'Install-BridgeRuntime')) {
    $node = @($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true))
    if ($node.Count -ne 1) { throw 'expected one production helper' }
    . ([scriptblock]::Create($node[0].Extent.Text))
}
'destination=' + (Install-BridgeRuntime -SourceRoot $SourceRoot -InstallRoot $InstallRoot)
"""
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / "source", root / "install"
            files = {
                "src/director_orchestrator/__init__.py": b"# package\n",
                "src/director_orchestrator/nested/module.py": "VALUE = 'é'\r\n".encode("utf-8"),
                "docs/schemas/packet.json": b'{"type":"object"}',
                "docs/provider-profiles/claude.md": b"profile\n",
                "prompts/director-system.md": b"prompt\n",
                "scripts/windows-host-provider-bridge.ps1": b"# launcher\n",
            }
            excluded = {
                "src/director_orchestrator/__pycache__/module.cpython-312.pyc": b"\x00bytecode",
                "README.md": b"not runtime\n",
                "scripts/other.ps1": b"# not runtime\n",
            }
            for relative, content in {**files, **excluded}.items():
                (source / relative).parent.mkdir(parents=True, exist_ok=True)
                (source / relative).write_bytes(content)
            (root / "harness.ps1").write_text(harness, encoding="utf-8")

            def install():
                result = subprocess.run(
                    [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(root / "harness.ps1"),
                     str(ROOT / "scripts" / "windows-host-provider-bridge.ps1"), str(source), str(target)],
                    capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                )
                lines = [line for line in result.stdout.splitlines() if line.startswith("destination=")]
                return result, (Path(lines[-1][len("destination="):]) if lines else None)

            def address(contents):
                manifest = sorted(f"{hashlib.sha256(data).hexdigest()}  {relative}" for relative, data in contents.items())
                return hashlib.sha256(("\n".join(manifest) + "\n").encode("utf-8")).hexdigest()

            result, first = install()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(first, target / address(files))
            copied = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
            self.assertEqual(copied, files)
            self.assertFalse(any("__pycache__" in path.parts for path in first.rglob("*")))

            result, again = install()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(again, first)
            self.assertEqual(list(target.glob(".staging-*")), [])

            edited = dict(files, **{"prompts/director-system.md": b"edited prompt\n"})
            (source / "prompts/director-system.md").write_bytes(edited["prompts/director-system.md"])
            result, second = install()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(second, target / address(edited))
            self.assertNotEqual(second, first)
            self.assertEqual((first / "prompts/director-system.md").read_bytes(), files["prompts/director-system.md"])

            (source / "prompts/director-system.md").write_bytes(files["prompts/director-system.md"])
            (first / "docs/schemas/packet.json").write_bytes(b'{"type":"tampered"}')
            result, _ = install()
            self.assertNotEqual(0, result.returncode)
            self.assertIn("does not match its content address", result.stderr)

    def test_host_wrapper_preserves_unsupported_lane_rejection(self) -> None:
        text = self.text("windows-host-provider-bridge.ps1")
        self.assertIn("unallowlisted provider", text)
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
        readiness = text[
            text.index("function Test-LaneReady"):text.index("function Wait-LaneReady")
        ]
        self.assertIn(
            "$priorErrorActionPreference = $ErrorActionPreference", readiness
        )
        self.assertIn(
            "finally { $ErrorActionPreference = $priorErrorActionPreference }",
            readiness,
        )
        self.assertLess(
            readiness.index("$priorErrorActionPreference = $ErrorActionPreference"),
            readiness.index('$ErrorActionPreference = "Continue"'),
        )
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
        # Start-Lane runs inside the try, so a refused or unreachable lane still
        # restores the caller's working directory and environment.
        push_index = test_section.index("Push-Location")
        try_index = test_section.index("try {", push_index)
        start_index = test_section.index("Start-Lane", try_index)
        self.assertLess(push_index, try_index)
        self.assertLess(try_index, start_index)
        self.assertLess(start_index, unittest_index)
        self.assertLess(test_section.index("Stop-Lane", unittest_index), test_section.index("Pop-Location", unittest_index))
        # Stop-Lane can throw (a foreign container took the name, or docker rm
        # failed). That is warned rather than raised so it cannot mask the test
        # result, so the run must not then claim the lane was removed: it names
        # the surviving lane and exits non-zero.
        self.assertIn("$laneRemoved = $false", test_section)
        self.assertIn("try { Stop-Lane; $laneRemoved = $true } catch", test_section)
        self.assertIn("may still be running; rerun -Action Stop", test_section)
        self.assertIn("if (-not $laneRemoved) { throw", test_section)

    def test_stage_a_runbook_lists_every_control_action_and_the_scripted_offline_path(self) -> None:
        script = self.text("windows-langgraph-primary-control.ps1")
        actions = re.findall(r'"(\w+)"', re.search(
            r"\[ValidateSet\(([^)]*)\)\]\s*\[string\]\$Action", script).group(1))
        runbook = (ROOT / "docs" / "langgraph-primary-stage-a-runbook.md").read_text(encoding="utf-8")
        listed = runbook.split("`-Action` set is", 1)[1].split("Confirm the ValidateSet", 1)[0]
        self.assertEqual(re.findall(r"`(\w+)`", listed), actions)
        # Stage A is the scripted ActivateOffline action; the hand edit is only a fallback.
        execution = runbook.split("\n## Execution", 1)[1].split("\n## Verification", 1)[0]
        self.assertIn("-Action ActivateOffline", execution)
        self.assertIn("**Fallback only.**", execution)

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
        self.assertIn("@AGENTS.md", visible)
        self.assertIn("no standing director role", claude.lower())
        self.assertIn("docs/project-leadership.md", claude)


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
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:20 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 12:24 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 17:28 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:35 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:44 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:19 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:52 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | END]]
