# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:13 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 07:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:09 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 01:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:39 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 21:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | BEGIN]]
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
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


ANSI_CONTROL_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Probes run under the same strictness the script itself declares.
POWERSHELL_PRELUDE = "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"


def _powershell_shell() -> str | None:
    return shutil.which("pwsh") or shutil.which("pwsh.exe")


def _windows_powershell_shell() -> str | None:
    """Locate Windows PowerShell 5.1, which behaves differently from pwsh 7."""

    return shutil.which("powershell") or shutil.which("powershell.exe")


PS_SCOPE_HELPERS = """
function Test-PsTopLevel {
    param([System.Management.Automation.Language.Ast]$Node)

    # Unconditionally executed when the script runs. A definition nested in a
    # function, an if, a loop or a switch may never execute, so copying it into
    # a probe proves nothing about the script. A try body always runs, but its
    # catch and finally blocks do not, so those are rejected too. This script
    # wraps its own body in a try, which is why a blanket try rejection would
    # match nothing at all.
    $child = $Node
    $parent = $Node.Parent
    while ($parent) {
        if ($parent -is [System.Management.Automation.Language.FunctionDefinitionAst] -or
            $parent -is [System.Management.Automation.Language.IfStatementAst] -or
            $parent -is [System.Management.Automation.Language.LoopStatementAst] -or
            $parent -is [System.Management.Automation.Language.SwitchStatementAst]) {
            return $false
        }
        if ($parent -is [System.Management.Automation.Language.TryStatementAst] -and
            -not [object]::ReferenceEquals($parent.Body, $child)) {
            return $false
        }
        $child = $parent
        $parent = $parent.Parent
    }
    return $true
}

function Get-ScriptAst {
    param([string]$Path)

    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$null, [ref]$errors)
    if ($errors.Count -gt 0) { throw "the script under test does not parse" }
    return $ast
}

function Import-TopLevelFunction {
    param([System.Management.Automation.Language.Ast]$Ast, [string]$Name)

    $matches = @($Ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $n.Name -eq $Name
    }, $true) | Where-Object { Test-PsTopLevel -Node $_ })
    if ($matches.Count -ne 1) {
        throw "expected exactly one unconditional $Name, found $($matches.Count)"
    }
    return $matches[0].Extent.Text
}

function Get-LiveFinalizerBody {
    param([System.Management.Automation.Language.Ast]$Ast)

    $matches = @($Ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.TryStatementAst] -and $n.Finally
    }, $true) | Where-Object { Test-PsTopLevel -Node $_ })
    if ($matches.Count -ne 1) {
        throw "expected exactly one unconditional finalizer, found $($matches.Count)"
    }
    $body = $matches[0].Finally.Extent.Text
    return $body.Substring(1, $body.Length - 2)
}
"""


def _ps_literal(value: str) -> str:
    """Quote a string as a PowerShell single-quoted literal."""

    return "'" + value.replace("'", "''") + "'"


# Loads the launcher's own route functions into the probe session. Extracting
# them from the shipped script is the point: a copy pasted into the test would
# pass while the launcher's copy was broken.
SOURCE_HELPER = """
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$launcher = Get-Content -LiteralPath %s -Raw
foreach ($name in 'Read-DotEnv', 'ConvertTo-ComposeValue', 'Assert-NoProfileCollisions', 'Get-N8nIntakeWebhookPath', 'Get-N8nServiceEndpoint', 'Assert-N8nRouteMatchesWorkflow') {
    $start = $launcher.IndexOf("function $name {")
    if ($start -lt 0) { throw "the launcher no longer defines $name" }
    $end = $launcher.IndexOf("`nfunction ", $start + 10)
    Invoke-Expression $launcher.Substring($start, $end - $start)
}
""" % _ps_literal(str(ROOT / "scripts" / "windows-isolated-live-validation.ps1"))


def _run_powershell(probe: str, shell: str | None = None) -> str:
    """Run a deterministic PowerShell probe and return its normalized stdout.

    Defaults to PowerShell 7. Pass a 5.1 path for behavior that differs between
    editions, such as empty environment values, where a 7-only probe would pass
    against a defect that only bites on 5.1.
    """

    shell = shell or _powershell_shell()
    if shell is None:
        raise unittest.SkipTest("PowerShell is unavailable in this environment")
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


# Values a disposable copy of the launcher needs to get past its configuration
# gates. Synthetic and local: no profile, credential or endpoint is real.
FIXTURE_PROFILE_NAMES = (
    "ORCHESTRATOR_SHARED_SECRET",
    "OPENAI_API_KEY",
    "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN",
    "CLAUDE_CALLBACK_SECRET",
    "CURSOR_API_KEY",
    "N8N_ENCRYPTION_KEY",
    "N8N_POSTGRES_PASSWORD",
    "SLACK_APP_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_DIRECTOR_CHANNEL_ID",
    "SLACK_GATEWAY_SECRET",
    "TRUSTED_PROVIDER_COST_RANKS",
)

# The identity block no longer forces USAGE_CHECK_PROVIDERS; the profile owns
# it and Get-ValidatedUsageCheckProviders validates it before any paid
# dispatch. A synthetic placeholder is rejected there, so the fixture
# declares the real mandatory set instead.
FIXTURE_PROFILE_LITERALS = {
    "USAGE_CHECK_PROVIDERS": "openai,claude,cursor",
}

# Docker, the ACL call and every HTTP request are replaced, so the whole script
# can run end to end without a daemon, a network or a real deployment.
FULL_LAUNCHER_PROBE_STUBS = """
$global:probeDocker = New-Object System.Collections.Generic.List[string]
function global:docker {
    $global:probeDocker.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args -contains '--entrypoint') { Write-Output '0' }
}
function global:icacls.exe { $global:LASTEXITCODE = 0 }
function global:Invoke-RestMethod {
    param($Uri, $Method, $TimeoutSec, $Headers)

    if ($Uri -match '/health$') {
        return [pscustomobject]@{
            providers = [pscustomobject]@{
                openai_director = $true; claude = $true; cursor = $true
            }
            execution_profile = [pscustomobject]@{
                projects_configured = $true
                repositories_configured = $true
                starting_refs_configured = $true
                claude_callback_configured = $true
            }
        }
    }
    if ($Uri -match '/v1/usage-checks/run$') {
        return [pscustomobject]@{
            status = 'not_due'
            scheduled_providers = @()
            cooling_down_providers = @('claude', 'cursor', 'openai')
        }
    }
    if ($Uri -match '/v1/usage-checks/latest$') {
        return @('claude', 'cursor', 'openai') | ForEach-Object {
            [pscustomobject]@{
                provider = $_
                status = 'ok'
                total_tokens = 0
                checked_at = [DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
            }
        }
    }
    throw 'the probe reached an unexpected endpoint'
}
function global:Get-ProbeEnvironment {
    $snapshot = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $snapshot[[string]$entry.Key] = [string]$entry.Value
    }
    return $snapshot
}
# Denying the compiler can also cost the probe a cmdlet that is not loaded yet,
# because module autoload on Windows PowerShell 5.1 may compile on its way in.
# Get-FileHash is the one this fixture used to reach for, so it is denied here
# on purpose: the profile comparison below has to stand on framework calls
# alone, and a local pass now proves that independence rather than assuming it.
function global:Get-FileHash { throw 'synthetic Get-FileHash denial' }
function global:Get-ProbeFileContent {
    param([Parameter(Mandatory = $true)][string]$Path)

    # Exact bytes, no cmdlet, no encoding guess: enough to prove the profile was
    # not rewritten, and comparable as an ordinal string.
    return [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($Path))
}
"""

# The two probes that execute the whole launcher depend on Windows-only platform
# APIs: the launcher resolves the current WindowsIdentity before it restricts the
# profile ACL, and the empty-value seed goes through kernel32. pwsh on Ubuntu
# supports neither, so those two are gated to Windows instead of being weakened
# into something that no longer exercises the real script. Everything else in
# this module is a static or PowerShell-only check and stays cross-platform.
WINDOWS_LAUNCHER_ONLY = unittest.skipUnless(
    sys.platform == "win32",
    "running the full launcher needs Windows: WindowsIdentity and kernel32 are "
    "unavailable to pwsh on a non-Windows host",
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

    def test_every_pinned_gate_is_bound_into_the_compose_environment(self):
        """A pinned gate must also be bound, or an inherited value wins.

        Compose reads the process environment ahead of --env-file, so a name the
        identity block writes to the profile but never binds is silently
        overridden by whatever the operator exported. An inherited
        ALLOW_PAID_RUNS=true then resolves a paid-closed profile to a paid-open
        container.
        """

        managed = self.script[
            self.script.index("$composeEnvironmentNames = @(") :
            self.script.index("$originalProcessEnvironment = @{}")
        ]
        bound = set(re.findall(r'"([A-Z0-9_]+)"', managed))

        identity_block = self.script[
            self.script.index("$identity = [ordered]@{") :
            self.script.index("$unboundIdentityNames = @(")
        ]
        pinned = set(re.findall(r"(?m)^\s*(?:\$identity\[\")?([A-Z0-9_]+)(?:\"\])?\s*=", identity_block))

        self.assertIn("ALLOW_PAID_RUNS", pinned)
        self.assertEqual(pinned - bound, set())

        # Drift must fail closed, so the guard has to actually throw.
        guard = self.script[
            self.script.index("$unboundIdentityNames = @(") :
            self.script.index("foreach ($entry in $identity.GetEnumerator())")
        ]
        self.assertIn("throw", guard)
        self.assertIn("an inherited value would win", guard)

    def test_switch_writes_rebind_the_compose_environment(self):
        """Opening a gate must reach the container, not just the profile file.

        Binding the closed gates once at staging is only half the contract. If a
        later switch write updates the file alone, the exported closed value
        keeps winning and authorized activation can never complete, because
        Compose still resolves the gate false. This drives the real functions
        through the closed -> open -> closed transition.
        """

        source = (ROOT / "scripts" / "windows-isolated-live-validation.ps1").read_text(
            encoding="utf-8"
        )
        lines = source.splitlines()

        def body(name: str) -> str:
            start = next(
                i for i, line in enumerate(lines) if line.startswith(f"function {name} ")
                or line.startswith(f"function {name}{{")
                or line == f"function {name} {{"
            )
            end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
            return chr(10).join(lines[start : end + 1])

        helpers = chr(10).join(
            body(name)
            for name in (
                "Read-DotEnv",
                "Set-DotEnvValue",
                "Write-Utf8NoBom",
                "Sync-ComposeEnvironment",
                "Write-ProfileSwitches",
            )
        )
        probe = helpers + """
$ErrorActionPreference = 'Stop'
$composeEnvironmentNames = @('ALLOW_PAID_RUNS', 'USAGE_CHECK_ENABLED', 'DIRECTOR_ENV_FILE')
$ProfileName = 'edge-oracle'
$profilePath = '.switch-rebind-probe.env'
Set-Content -LiteralPath $profilePath -Value "ALLOW_PAID_RUNS=false`nUSAGE_CHECK_ENABLED=false`n"
try {
    # An operator who exported the gate open must not win.
    [Environment]::SetEnvironmentVariable('ALLOW_PAID_RUNS', 'true', 'Process')
    Sync-ComposeEnvironment -Values (Read-DotEnv -Path $profilePath)
    Write-Host "staged|$([Environment]::GetEnvironmentVariable('ALLOW_PAID_RUNS','Process'))"
    Write-ProfileSwitches -PaidRuns $true -UsageChecks $true
    Write-Host "opened|$([Environment]::GetEnvironmentVariable('ALLOW_PAID_RUNS','Process'))"
    Write-ProfileSwitches -PaidRuns $false -UsageChecks $false
    Write-Host "closed|$([Environment]::GetEnvironmentVariable('ALLOW_PAID_RUNS','Process'))"
}
finally {
    [Environment]::SetEnvironmentVariable('ALLOW_PAID_RUNS', $null, 'Process')
    Remove-Item -LiteralPath $profilePath -Force -ErrorAction SilentlyContinue
}
"""
        observed = dict(
            line.strip().split("|", 1)
            for line in _run_powershell(probe).splitlines()
            if "|" in line
        )
        self.assertEqual(
            observed, {"staged": "false", "opened": "true", "closed": "false"}
        )

    def test_absent_gates_are_removed_rather_than_emptied(self):
        """Restoring absence must remove the name, not blank it.

        PowerShell 7 keeps the variable with an empty value when
        SetEnvironmentVariable is handed $null, and an exported empty value still
        beats --env-file. A gate the operator never set would then linger as an
        empty export and silently override the next profile.
        """

        source = (ROOT / "scripts" / "windows-isolated-live-validation.ps1").read_text(
            encoding="utf-8"
        )
        lines = source.splitlines()
        start = lines.index("function Clear-ProcessEnvironmentVariable {")
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")

        probe = chr(10).join(lines[start : end + 1]) + """
$ErrorActionPreference = 'Stop'
[Environment]::SetEnvironmentVariable('PR77_PROBE_GATE', 'inherited', 'Process')
Clear-ProcessEnvironmentVariable -Name 'PR77_PROBE_GATE'
Write-Host "removed|$(Test-Path -LiteralPath 'Env:PR77_PROBE_GATE')"
# Clearing a name that was never set must stay quiet.
Clear-ProcessEnvironmentVariable -Name 'PR77_PROBE_NEVER_SET'
Write-Host "absent|$(Test-Path -LiteralPath 'Env:PR77_PROBE_NEVER_SET')"
"""
        observed = dict(
            line.strip().split("|", 1)
            for line in _run_powershell(probe).splitlines()
            if "|" in line
        )
        # Behavior first: the name must actually be gone, not blanked.
        self.assertEqual(observed, {"removed": "False", "absent": "False"})
        # And no call site may go back to the blanking form.
        self.assertNotIn(
            '[Environment]::SetEnvironmentVariable($name, $null, "Process")', source
        )

    def test_inherited_empty_override_is_restored_exactly(self):
        """An inherited empty gate must come back, not vanish.

        An empty export still beats --env-file, so an inherited empty gate is a
        real paid-closed override. Every managed setter deletes the name when
        handed "", but the Win32 entry point separates an empty string from the
        null pointer that means delete, so restoration is possible on both
        editions and the override survives teardown.
        """

        source = (ROOT / "scripts" / "windows-isolated-live-validation.ps1").read_text(
            encoding="utf-8"
        )
        lines = source.splitlines()
        # The capability block now runs at startup, ahead of the snapshot, so the
        # probe takes it from there and pairs it with the real setter.
        cap_start = lines.index("$script:win32EnvironmentReady = $false")
        cap_stop = lines.index("$originalProcessEnvironment = @{}")
        fn_start = lines.index("function Set-ProcessEnvironmentVariableExact {")
        fn_stop = next(i for i in range(fn_start + 1, len(lines)) if lines[i] == "}")
        helper = chr(10).join(
            lines[cap_start:cap_stop] + lines[fn_start : fn_stop + 1]
        )

        probe = helper + """
$ErrorActionPreference = 'Stop'
[Environment]::SetEnvironmentVariable('PR77_EMPTY_GATE', 'inherited', 'Process')
Set-ProcessEnvironmentVariableExact -Name 'PR77_EMPTY_GATE' -Value ''
$value = [Environment]::GetEnvironmentVariable('PR77_EMPTY_GATE', 'Process')
Write-Output ("empty|" + (Test-Path -LiteralPath 'Env:PR77_EMPTY_GATE') + "|" + ([string]$value).Length)
Set-ProcessEnvironmentVariableExact -Name 'PR77_FULL_GATE' -Value 'false'
Write-Output ("full|" + (Test-Path -LiteralPath 'Env:PR77_FULL_GATE') + "|" + [Environment]::GetEnvironmentVariable('PR77_FULL_GATE', 'Process'))
"""
        shell = _windows_powershell_shell()
        if shell is None:
            self.skipTest("Windows PowerShell 5.1 is unavailable in this environment")
        observed = dict(
            (line.split("|", 1)[0].strip(), line.split("|", 1)[1])
            for line in _run_powershell(probe, shell=shell).splitlines()
            if "|" in line
        )
        self.assertEqual(observed["empty"], "True|0")
        self.assertEqual(observed["full"], "True|false")

    def test_restore_diagnostic_cannot_abort_teardown(self):
        """The fallback warning must never end the restoration loop.

        It runs in the exit path. An inherited -WarningAction Stop would
        otherwise turn it into a terminating error, replacing the original
        exception and leaving every later managed name unrestored.
        """

        source = (ROOT / "scripts" / "windows-isolated-live-validation.ps1").read_text(
            encoding="utf-8"
        )
        # Resolved by executable scope: a definition wrapped in if ($false)
        # would never run, so copying it into the probe would prove nothing.
        diagnostic = PS_SCOPE_HELPERS + """
$ast = Get-ScriptAst -Path '""" + (ROOT / "scripts" / "windows-isolated-live-validation.ps1").as_posix() + """'
. ([scriptblock]::Create((Import-TopLevelFunction -Ast $ast -Name 'Write-EnvironmentRestoreWarning')))
"""

        # And the restore loop must route through it rather than warning directly.
        restore = source[source.index("$original = $originalProcessEnvironment[$name]") :]
        restore = restore[: restore.index("Clear-ProcessEnvironmentVariable")]
        self.assertIn("Write-EnvironmentRestoreWarning", restore)
        self.assertNotIn("Write-Warning", restore)

        # Drive the script's own helper, not a copy of it: a copy stays quiet
        # while the real one is made terminating.
        probe = POWERSHELL_PRELUDE + diagnostic + """
$WarningPreference = 'Stop'
$completed = 0
foreach ($n in 'A', 'B', 'C') {
    Write-EnvironmentRestoreWarning -Message "probe $n"
    $completed++
}
Write-Output ("completed|" + $completed)
"""
        observed = dict(
            line.strip().split("|", 1)
            for line in _run_powershell(probe).splitlines()
            if "|" in line
        )
        self.assertEqual(observed, {"completed": "3"})

    def test_one_failed_restore_cannot_strand_the_others(self):
        """Teardown must survive a failure on any single name.

        The probe runs the script's own finalizer, and resolves it by executable
        scope: the one top-level try/finally, plus the helper definitions that
        actually run. Selecting by size or by name alone lets a longer dead
        try, or a definition wrapped in if ($false), stand in for the live code
        while the real finalizer strands every gate behind the first failure.
        """

        script = ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        source = script.read_text(encoding="utf-8")

        # Compilation happens once, before anything is mutated. Checking only
        # the loop body would miss a compile inside a helper the loop calls.
        self.assertEqual(source.count("Add-Type"), 1)
        self.assertLess(
            source.index("Add-Type"),
            source.index("$originalProcessEnvironment = @{}"),
        )

        probe = POWERSHELL_PRELUDE + PS_SCOPE_HELPERS + """
$ast = Get-ScriptAst -Path '""" + script.as_posix() + """'
foreach ($fname in 'Write-EnvironmentRestoreWarning',
                   'Set-ProcessEnvironmentVariableExact',
                   'Clear-ProcessEnvironmentVariable') {
    . ([scriptblock]::Create((Import-TopLevelFunction -Ast $ast -Name $fname)))
}
$body = Get-LiveFinalizerBody -Ast $ast

$originalLocation = Get-Location
$script:win32EnvironmentReady = $true
$composeEnvironmentNames = @('PR77_N1', 'PR77_BOOM', 'PR77_N3', 'PR77_N4')
$originalProcessEnvironment = @{}
foreach ($n in $composeEnvironmentNames) {
    $originalProcessEnvironment[$n] = [pscustomobject]@{ Present = $true; Value = "v-$n" }
    [Environment]::SetEnvironmentVariable($n, $null, 'Process')
}
# One name fails the way a denied compiler would.
function Set-ProcessEnvironmentVariableExact {
    param([string]$Name, [AllowEmptyString()][string]$Value)
    if ($Name -eq 'PR77_BOOM') { throw 'compiler unavailable' }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    return $true
}

$threw = 'no'
try { . ([scriptblock]::Create($body)) } catch { $threw = $_.Exception.Message }
foreach ($n in $composeEnvironmentNames) {
    Write-Output ($n + "|" + [Environment]::GetEnvironmentVariable($n, 'Process'))
}
Write-Output ("threw|" + $threw)
"""
        shells = [None] if _powershell_shell() else []
        windows_shell = _windows_powershell_shell()
        if windows_shell is not None:
            shells.append(windows_shell)
        self.assertTrue(shells, "no PowerShell available")
        for shell in shells:
            observed = dict(
                line.strip().split("|", 1)
                for line in _run_powershell(probe, shell=shell).splitlines()
                if "|" in line
            )
            # Individual values, not a count: only the failing name is missing,
            # and the finalizer itself never throws.
            self.assertEqual(
                observed,
                {
                    "PR77_N1": "v-PR77_N1",
                    "PR77_BOOM": "",
                    "PR77_N3": "v-PR77_N3",
                    "PR77_N4": "v-PR77_N4",
                    "threw": "no",
                },
            )

    def _available_powershell_shells(self) -> list[str | None]:
        shells: list[str | None] = [None] if _powershell_shell() else []
        windows_shell = _windows_powershell_shell()
        if windows_shell is not None:
            shells.append(windows_shell)
        self.assertTrue(shells, "no PowerShell available")
        return shells

    def _build_launcher_fixture(self, root: Path) -> Path:
        """Copy the launcher into a disposable tree with a synthetic profile."""

        (root / "scripts").mkdir()
        shutil.copyfile(
            ROOT / "scripts" / "windows-isolated-live-validation.ps1",
            root / "scripts" / "windows-isolated-live-validation.ps1",
        )
        (root / ".env.uppercut").write_text(
            "".join(
                f"{name}=synthetic-local-fixture-{name}\n"
                for name in FIXTURE_PROFILE_NAMES
            )
            + "".join(
                f"{name}={value}\n"
                for name, value in FIXTURE_PROFILE_LITERALS.items()
            ),
            encoding="utf-8",
        )
        return root

    def _run_full_launcher(self, seed: str, shell: str | None) -> dict[str, str]:
        """Run the unmodified launcher against stubs with the compiler denied."""

        with tempfile.TemporaryDirectory() as raw:
            fixture = self._build_launcher_fixture(Path(raw))
            probe = POWERSHELL_PRELUDE + FULL_LAUNCHER_PROBE_STUBS + f"""
$fixture = '{fixture.as_posix()}'
$launcher = Join-Path $fixture 'scripts/windows-isolated-live-validation.ps1'
$log = Join-Path $fixture 'run.log'
{seed}
$seedPresent = Test-Path -LiteralPath 'Env:ALLOW_PAID_RUNS'
$seedValue = [Environment]::GetEnvironmentVariable('ALLOW_PAID_RUNS', 'Process')
# Denied the way a locked-down session or a missing compiler denies it. The
# launcher compiles its native environment helper once at startup.
function global:Add-Type {{ throw 'synthetic compiler denial' }}
$before = Get-ProbeEnvironment
$profileFile = Join-Path $fixture '.env.uppercut'
$profileBefore = Get-ProbeFileContent -Path $profileFile
$threw = 'none'
try {{
    & $launcher -ProfileName project-uppercut -EnablePaidRuns -TimeoutSeconds 1 *> $log
}}
catch {{ $threw = $_.Exception.Message }}
$after = Get-ProbeEnvironment
$changed = @()
foreach ($name in @(@($before.Keys) + @($after.Keys) | Sort-Object -Unique)) {{
    if (-not $before.ContainsKey($name) -or
        -not $after.ContainsKey($name) -or
        $before[$name] -cne $after[$name]) {{
        $changed += $name
    }}
}}
$paid = [Environment]::GetEnvironmentVariable('ALLOW_PAID_RUNS', 'Process')
$profileAfter = Get-ProbeFileContent -Path $profileFile
Write-Output ('seedPresent|' + $seedPresent)
Write-Output ('seedLength|' + ([string]$seedValue).Length)
Write-Output ('threw|' + $threw)
Write-Output ('paidPresent|' + (Test-Path -LiteralPath 'Env:ALLOW_PAID_RUNS'))
Write-Output ('paidLength|' + ([string]$paid).Length)
Write-Output ('paidValue|' + [string]$paid)
Write-Output ('dockerCalls|' + $global:probeDocker.Count)
Write-Output ('composeCalls|' + @($global:probeDocker | Where-Object {{ $_ -like 'compose*' }}).Count)
Write-Output ('changedEnv|' + ($changed -join ','))
Write-Output ('profileUnchanged|' + ($profileBefore -ceq $profileAfter))
"""
            return dict(
                line.strip().split("|", 1)
                for line in _run_powershell(probe, shell=shell).splitlines()
                if "|" in line
            )

    @WINDOWS_LAUNCHER_ONLY
    def test_unrestorable_inherited_empty_gate_stops_before_any_mutation(self):
        """An empty gate that cannot be restored must stop the run at once.

        Startup compilation of the native helper is allowed to fail, but then an
        inherited empty ALLOW_PAID_RUNS cannot be put back. Running anyway
        rebinds the gate to false, opens it to true for the provider probes, and
        hands the caller back a paid-open true where an empty paid-closed
        override used to be. The whole launcher runs here against stubs with a
        genuinely empty Win32 value seeded, so the fix has to hold end to end.
        """

        seed = """
$signature = '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Unicode)] public static extern bool SetEnvironmentVariableW(string lpName, string lpValue);'
Add-Type -Namespace Pr116Probe -Name Seed -MemberDefinition $signature
if (-not [Pr116Probe.Seed]::SetEnvironmentVariableW('ALLOW_PAID_RUNS', '')) {
    throw 'could not seed an empty inherited override'
}
"""
        for shell in self._available_powershell_shells():
            with self.subTest(shell=shell or "pwsh"):
                observed = self._run_full_launcher(seed, shell)
                # Without a real empty inherited value the case proves nothing.
                self.assertEqual(observed["seedPresent"], "True")
                self.assertEqual(observed["seedLength"], "0")
                # It fails, and it says which name it could not restore.
                self.assertIn("ALLOW_PAID_RUNS", observed["threw"])
                self.assertIn("no service was started", observed["threw"])
                # The inherited empty override is still exactly that.
                self.assertEqual(observed["paidPresent"], "True")
                self.assertEqual(observed["paidLength"], "0")
                # Nothing was mutated and nothing was started.
                self.assertEqual(observed["changedEnv"], "")
                self.assertEqual(observed["dockerCalls"], "0")
                self.assertEqual(observed["composeCalls"], "0")
                self.assertEqual(observed["profileUnchanged"], "True")

    @WINDOWS_LAUNCHER_ONLY
    def test_denied_compilation_alone_does_not_fail_a_run_without_empties(self):
        """No inherited empty means no need for the native helper.

        The helper only matters for restoring an empty value. Failing every run
        that cannot compile it would break ordinary activation for no gain, so
        an inherited non-empty gate must still run and must still come back
        exactly as it arrived.
        """

        seed = "[Environment]::SetEnvironmentVariable('ALLOW_PAID_RUNS', 'true', 'Process')"
        for shell in self._available_powershell_shells():
            with self.subTest(shell=shell or "pwsh"):
                observed = self._run_full_launcher(seed, shell)
                self.assertEqual(observed["seedPresent"], "True")
                self.assertEqual(observed["threw"], "none")
                # The run proceeded through Compose rather than failing closed.
                self.assertGreater(int(observed["composeCalls"]), 0)
                # And the managed setter restored the non-empty value.
                self.assertEqual(observed["paidValue"], "true")

    def test_profile_is_separate_and_primary_env_is_read_only(self):
        self.assertIn('ProfileName = "project-uppercut"', self.wrapper)
        self.assertIn('ProfilePath = ".env.uppercut"', self.script)
        self.assertNotIn('Set-Content ".env"', self.script)
        self.assertNotIn('WriteAllText((Join-Path (Get-Location) ".env")', self.script)
        self.assertIn("SeedProviderCredentialsFromPrimaryEnv", self.script)

    def test_paid_activation_preserves_operator_cost_ranks_and_fails_closed_if_blank(self):
        self.assertIn('PRIMARY_WORKHORSE = "cheapest_capable"', self.script)
        self.assertNotIn('PRIMARY_WORKHORSE = "claude"', self.script)
        self.assertNotRegex(
            self.script,
            r'TRUSTED_PROVIDER_COST_RANKS\s*=\s*["\']\{',
        )
        paid_guard = self.script.index("if ($EnablePaidRuns) {")
        required_rank = self.script.index(
            'Assert-Configured -Values $values -Names @("TRUSTED_PROVIDER_COST_RANKS")',
            paid_guard,
        )
        paid_switch = self.script.index(
            "Write-ProfileSwitches -PaidRuns $true",
            required_rank,
        )
        self.assertLess(required_rank, paid_switch)
        for profile_name in (
            ".env.example",
            ".env.uppercut.example",
            ".env.edge-oracle.example",
            ".env.long-table.example",
        ):
            profile = (ROOT / profile_name).read_text(encoding="utf-8")
            self.assertIn("TRUSTED_PROVIDER_COST_RANKS=", profile)
            self.assertNotRegex(
                profile,
                r"(?m)^TRUSTED_PROVIDER_COST_RANKS=\s*\{",
            )

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
        self.assertIn("DIRECTOR_COMPOSE_PROJECT_NAME=project-uppercut-director", self.profile.splitlines())
        self.assertIn("DIRECTOR_HOST_PORT=18080", self.profile.splitlines())
        self.assertIn("N8N_HOST_PORT=15678", self.profile.splitlines())
        self.assertIn("SQLITE_PATH=/data/project-uppercut.sqlite3", self.profile.splitlines())

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
            self.fail("PowerShell 7 coverage is required for this deployment contract")
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

    def test_project_ownership_is_proved_before_any_service_is_stopped(self):
        """Stopping by Compose project name reaches whoever owns that name.

        The collision check ran after Stop-PreviouslyRunningServices, so a
        profile sharing another deployment's project name stopped that
        deployment and only then rejected the collision. Found by an
        independent review of #142, which measured the base issuing zero Docker
        calls and the head issuing stop and ps before the same rejection.
        """

        ownership = self.script.index(
            "Assert-NoProfileCollisions -TargetValues $selectedIdentity -Names"
        )
        stop = self.script.index(
            "Stop-PreviouslyRunningServices -ComposeProject $composeProject"
        )
        self.assertLess(ownership, stop)
        # Only fixed identity names are comparable this early: the secrets are
        # still placeholders and would read as a collision between any two
        # freshly copied profiles.
        window = self.script[ownership:stop]
        self.assertIn('"DIRECTOR_COMPOSE_PROJECT_NAME"', window)
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", window)

    def test_the_ownership_check_validates_the_identity_that_is_stopped(self):
        """Checking one name while stopping another proves nothing.

        The stop addresses $composeProject, which is the selected profile's
        fixed identity. The first version of this check read the profile file
        instead, so a file carrying a stale name had its stale name compared
        while a sibling's live project was stopped. Found by the second round
        of an independent review of #142, which measured zero Docker calls on
        the base against stop and ps on that head.
        """

        ownership = self.script.index("$selectedIdentity = @{")
        stop = self.script.index(
            "Stop-PreviouslyRunningServices -ComposeProject $composeProject"
        )
        self.assertLess(ownership, stop)
        window = self.script[ownership:stop]
        # The checked project name is the one the stop uses, not a file read.
        self.assertIn("DIRECTOR_COMPOSE_PROJECT_NAME = $composeProject", window)
        self.assertNotIn("Read-DotEnv", window)

    def test_a_rejected_ownership_check_leaves_the_rollback_inert(self):
        """The catch stopped the very project the check had just refused.

        A rejected collision means nothing of ours was started, so there is
        nothing to roll back -- but the handler ran anyway and stopped four
        services in the sibling's project. Guarding on a non-empty name was not
        enough, because the name is assigned before the check runs.
        """

        self.assertIn("$composeProjectOwned = $false", self.script)
        self.assertIn("$composeProjectOwned = $true", self.script)
        rollback = self.script.index(
            "stop $ProfileOwnedServices"
        )
        guard = self.script.rindex("if ($composeProjectOwned", 0, rollback)
        # The guard is the immediately enclosing condition, not one somewhere
        # earlier in the file.
        self.assertNotIn("}", self.script[guard:rollback].split("{", 1)[1])
        # Ownership is granted only after the check, never beside the
        # assignment of the name itself.
        self.assertLess(
            self.script.index("Assert-NoProfileCollisions -TargetValues $selectedIdentity"),
            self.script.index("$composeProjectOwned = $true"),
        )

    def test_full_stack_long_table_requires_the_n8n_route(self):
        """A full stack that starts n8n must not let Slack bypass it.

        slack_bot forwards to n8n only when N8N_DIRECTOR_WEBHOOK_URL is set;
        blank posts straight to the orchestrator. Profiles copied from the
        earlier template carry a blank value, so full-stack readiness was
        reported with n8n bypassed. Found by an independent review of #142.
        """

        self.assertIn(
            'if ($ProfileName -eq "long-table" -and -not $ApiOnly) {',
            self.script,
        )
        requirement = self.script.index(
            'Assert-Configured -Values $values -Names @("N8N_DIRECTOR_WEBHOOK_URL")'
        )
        guard = self.script.rindex(
            'if ($ProfileName -eq "long-table" -and -not $ApiOnly) {', 0, requirement
        )
        # The requirement sits inside the -not $ApiOnly guard, so the API-only
        # canary, which starts no n8n, is not asked for a route it never uses.
        self.assertNotIn("}", self.script[guard:requirement].split("{", 1)[1])

    def test_a_wrong_n8n_route_is_rejected_not_only_a_blank_one(self):
        """Non-blank is not correct. A wrong path is a permanent 404.

        Requiring the value to be set left a profile naming a path the imported
        workflow does not serve reporting full-stack readiness, with slack_bot
        posting to that path verbatim for the life of the deployment. The only
        HTTP check in the launcher is orchestrator health, so nothing else
        catches it. Found by the second round of an independent review of #142.

        This runs the two real functions out of the launcher rather than
        asserting on its source, because the defect was behavioural.
        """

        probe = SOURCE_HELPER + """
$root = %s
foreach ($url in @(
    'http://n8n:5678/webhook/director-intake',
    'http://n8n:5678/webhook/wrong-intake',
    'http://n8n:5678/webhook/Director-Intake',
    'http://n8n:5678/',
    'not-a-url'
)) {
    try { Assert-N8nRouteMatchesWorkflow -Url $url -RepositoryRoot $root; "ACCEPT" }
    catch { "REJECT" }
}
""" % _ps_literal(str(ROOT))
        verdicts = _run_powershell(probe).split()
        # Only the path the committed workflow actually serves is accepted, and
        # the comparison is case-sensitive because n8n webhook paths are.
        self.assertEqual(
            verdicts, ["ACCEPT", "REJECT", "REJECT", "REJECT", "REJECT"]
        )

        # A correct validator the launcher never calls protects nothing:
        # deleting the call site left this test green until this assertion was
        # added. It must sit inside the full-stack guard, so the API-only
        # canary, which starts no n8n, is not asked for a route it never uses.
        call = self.script.index("Assert-N8nRouteMatchesWorkflow -Url")
        guard = self.script.rindex(
            'if ($ProfileName -eq "long-table" -and -not $ApiOnly) {', 0, call
        )
        self.assertNotIn("}", self.script[guard:call].split("{", 1)[1])

    def test_seven_compose_spellings_resolve_to_the_sibling_name(self):
        """Compare what Compose resolves, not what the operator typed.

        Stripping only a matching start-and-end quote pair was not enough. A
        delta review of #142 confirmed against the real Compose parser that a
        quoted name with a trailing comment, an unquoted name with a comment,
        and an interpolation default all resolve to the same project, and each
        evaded the ownership guard -- producing stop and ps calls against the
        protected deployment.
        """

        probe = SOURCE_HELPER + """
foreach ($raw in @(
    'project-uppercut-director',
    '"project-uppercut-director"',
    "'project-uppercut-director'",
    '"project-uppercut-director" # sibling',
    'project-uppercut-director # sibling',
    '${MISSING:-project-uppercut-director}',
    '${MISSING-project-uppercut-director}'
)) { ConvertTo-ComposeValue -Raw $raw }
"""
        resolved = _run_powershell(probe).split()
        self.assertEqual(resolved, ["project-uppercut-director"] * 7)

    def test_set_variable_collision_and_compose_source_precedence(self):
        """Resolve shell overrides and prior lines in the explicit sibling file."""

        probe = SOURCE_HELPER + r"""
$root = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Push-Location $root
try {
    $profilePath = '.env.long-table'
    $target = @{ DIRECTOR_COMPOSE_PROJECT_NAME = 'long-table-director' }
    # An explicit --env-file replaces .env; this decoy must never win.
    Set-Content .env 'R5_NAME=wrong-default-file'
    foreach ($shell in @($true, $false)) {
        if ($shell) { $env:R5_NAME = 'long-table-director' }
        else { Remove-Item Env:R5_NAME -ErrorAction SilentlyContinue }
        foreach ($expression in @('${R5_NAME}', '${R5_NAME:-dummy}', '${R5_NAME-dummy}', '"${R5_NAME:-dummy}" # sibling')) {
            $fileValue = if ($shell) { 'wrong-file-value' } else { 'long-table-director' }
            Set-Content .env.uppercut @("R5_NAME=$fileValue", "DIRECTOR_COMPOSE_PROJECT_NAME=$expression")
            try {
                Assert-NoProfileCollisions -TargetValues $target -Names @('DIRECTOR_COMPOSE_PROJECT_NAME')
                'ACCEPTED'
            } catch { $_.Exception.Message }
        }
    }
} finally {
    Remove-Item Env:R5_NAME -ErrorAction SilentlyContinue
    Pop-Location
    Remove-Item -LiteralPath $root -Recurse -Force
}
"""
        self.assertEqual(_run_powershell(probe).splitlines(), [
            '.env.long-table collides with .env.uppercut on '
            'DIRECTOR_COMPOSE_PROJECT_NAME. No service was started.'
        ] * 8)

    def test_compose_empty_unset_and_literal_interpolation(self):
        """Empty is present: only colon-minus substitutes its default."""

        probe = SOURCE_HELPER + r"""
$environment = @{}
foreach ($present in @($false, $true)) {
    if ($present) { $environment['R5_EMPTY'] = '' }
    foreach ($raw in @('${R5_EMPTY}', '${R5_EMPTY:-default}', '${R5_EMPTY-default}')) {
        '[' + (ConvertTo-ComposeValue -Raw $raw -InterpolationEnvironment $environment) + ']'
    }
}
ConvertTo-ComposeValue -Raw "'`${R5_LITERAL}'" -InterpolationEnvironment @{}
ConvertTo-ComposeValue -Raw '${R5_EMPTY:-default}' -FileValues @{R5_EMPTY='file'} -InterpolationEnvironment @{R5_EMPTY=''}
ConvertTo-ComposeValue -Raw '${R5_EMPTY-default}' -FileValues @{R5_EMPTY=''} -InterpolationEnvironment @{} | ForEach-Object { '[' + $_ + ']' }
"""
        self.assertEqual(_run_powershell(probe).splitlines(), [
            '[]', '[default]', '[default]', '[]', '[default]', '[]', '${R5_LITERAL}',
            'default', '[]',
        ])

    def test_unsupported_compose_syntax_refuses_ownership(self):
        probe = SOURCE_HELPER + r"""
foreach ($raw in @('${R5_NAME:+long-table-director}', '${R5_NAME:-${OTHER}}', '"unterminated', '"escaped\nname"')) {
    try { ConvertTo-ComposeValue -Raw $raw | Out-Null; 'ACCEPTED' }
    catch { 'REFUSED' }
}
"""
        self.assertEqual(_run_powershell(probe).splitlines(), ['REFUSED'] * 4)

    def test_reading_a_profile_never_rewrites_a_credential(self):
        """Read-DotEnv also feeds the credential copy, so it must not normalise.

        A normalising version of it corrupted values on their way to Compose:
        the delta review measured CLAUDE_ROUTINE_TOKEN='synthetic # literal'
        arriving as "synthetic". Resolution belongs in ConvertTo-ComposeValue,
        which is used only to compare identities.
        """

        probe = SOURCE_HELPER + """
$file = Join-Path ([IO.Path]::GetTempPath()) ("c" + [Guid]::NewGuid().ToString("N") + ".env")
Set-Content -LiteralPath $file -Encoding ascii -Value @(
    "CLAUDE_ROUTINE_TOKEN='synthetic # literal'",
    'QUOTED="keep me"'
)
$values = Read-DotEnv -Path $file
Remove-Item -LiteralPath $file -Force
$values['CLAUDE_ROUTINE_TOKEN']
$values['QUOTED']
"""
        lines = [line for line in _run_powershell(probe).splitlines() if line.strip()]
        self.assertEqual(lines[0].strip(), "'synthetic # literal'")
        self.assertEqual(lines[1].strip(), '"keep me"')

    def test_the_collision_check_rejects_through_the_real_comparison(self):
        """Replacing the equality with $false left all 66 tests green.

        Exercise the throwing guard for each spelling, including interpolation
        after double-quote stripping, with and without a trailing comment.
        """

        probe = SOURCE_HELPER + """
$root = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root | Out-Null
Push-Location $root
try {
    $profilePath = '.env.long-table'
    Set-Content -LiteralPath $profilePath -Encoding ascii -Value @('X=1')
    $target = @{ DIRECTOR_COMPOSE_PROJECT_NAME = 'project-uppercut-director' }
    foreach ($raw in @(
        'project-uppercut-director',
        '"project-uppercut-director"',
        "'project-uppercut-director'",
        '"project-uppercut-director" # sibling',
        'project-uppercut-director # sibling',
        '${MISSING:-project-uppercut-director}',
        '${MISSING-project-uppercut-director}',
        '"${MISSING:-project-uppercut-director}"',
        '"${MISSING:-project-uppercut-director}" # sibling'
    )) {
        Set-Content -LiteralPath '.env.uppercut' -Encoding ascii -Value (
            'DIRECTOR_COMPOSE_PROJECT_NAME=' + $raw
        )
        try {
            Assert-NoProfileCollisions -TargetValues $target -Names @('DIRECTOR_COMPOSE_PROJECT_NAME')
            'ACCEPTED'
        } catch { $_.Exception.Message }
    }
} finally { Pop-Location; Remove-Item -LiteralPath $root -Recurse -Force }
"""
        self.assertEqual(
            _run_powershell(probe).splitlines(),
            [
                ".env.long-table collides with .env.uppercut on "
                "DIRECTOR_COMPOSE_PROJECT_NAME. No service was started."
            ] * 9,
        )

    def test_the_route_must_address_the_n8n_service_not_merely_the_right_path(self):
        """The right path on the wrong destination is still unreachable.

        slack_bot posts the intake, with its authorization header, to this URL
        verbatim from inside the Compose network. Checking only the path
        accepted port 5679 against a service listening on 5678, and accepted
        container loopback and unrelated hosts; a review of #142 drove a
        full-stack run to readiness that way.
        """

        probe = SOURCE_HELPER + """
$root = %s
$endpoint = Get-N8nServiceEndpoint -RepositoryRoot $root
"$($endpoint.Host):$($endpoint.Port)"
foreach ($url in @(
    'http://n8n:5678/webhook/director-intake',
    'http://n8n:5679/webhook/director-intake',
    'http://localhost:5678/webhook/director-intake',
    'http://127.0.0.1:5678/webhook/director-intake',
    'http://evil.example/webhook/director-intake',
    'https://n8n:5678/webhook/director-intake'
)) {
    try { Assert-N8nRouteMatchesWorkflow -Url $url -RepositoryRoot $root; "ACCEPT" }
    catch { "REJECT" }
}
""" % _ps_literal(str(ROOT))
        output = _run_powershell(probe).split()
        # The endpoint is read from compose, never restated in this test.
        self.assertEqual(output[0], "n8n:5678")
        # The last case is https against a service declaring N8N_PROTOCOL: http.
        # Accepting any http-or-https scheme let that through, and it is as
        # unreachable as the wrong port.
        self.assertEqual(
            output[1:],
            ["ACCEPT", "REJECT", "REJECT", "REJECT", "REJECT", "REJECT"],
        )

    def test_the_endpoint_is_read_from_compose_rather_than_constant(self):
        """Returning a constant 5678 left this and all 66 tests green.

        Asserting against the real compose file cannot distinguish a value that
        was read from one that happens to match. A delta review of #142 made
        exactly that mutation and nothing failed. This points the reader at a
        synthetic compose file declaring a different port and protocol, so only
        an implementation that actually reads answers correctly -- and it also
        covers the scoping defect, by declaring N8N_PORT under an earlier
        service.
        """

        probe = SOURCE_HELPER + """
$root = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    Set-Content -LiteralPath (Join-Path $root 'docker-compose.yml') -Encoding ascii -Value @(
        'services:',
        '  decoy:',
        '    environment:',
        '      N8N_PORT: 5679',
        '      N8N_PROTOCOL: http',
        '  n8n:',
        '    environment:',
        '      N8N_PORT: 7001',
        '      N8N_PROTOCOL: https'
    )
    $endpoint = Get-N8nServiceEndpoint -RepositoryRoot $root
    "$($endpoint.Scheme)://$($endpoint.Host):$($endpoint.Port)"
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
"""
        # Both values differ from the real service's http:5678 and the decoy's
        # http:5679, so constants and whole-document searches cannot pass.
        self.assertEqual(_run_powershell(probe).strip(), "https://n8n:7001")

    def test_the_expected_route_is_read_from_the_workflow_not_written_twice(self):
        """A second hardcoded copy of the path would drift from the first."""

        probe = SOURCE_HELPER + """
Get-N8nIntakeWebhookPath -RepositoryRoot %s
""" % _ps_literal(str(ROOT))
        self.assertEqual(_run_powershell(probe).strip(), "director-intake")
        self.assertNotIn('"/webhook/director-intake"', self.script)

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
            self.assertIn(line, self.profile.splitlines())

    def test_oracle_stages_paid_usage_and_merge_gates_closed(self):
        self.assertIn("ALLOW_PAID_RUNS=false", self.profile.splitlines())
        self.assertIn("USAGE_CHECK_ENABLED=false", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_ENABLED=false", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_KILL_SWITCH=true", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_REQUIRED_CHECK_APP=github-actions", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_PROJECT=edge-oracle", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_REPOSITORY=edwardjones3976/edge_engine", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_ID=", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID=", self.profile.splitlines())
        self.assertIn("ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64=", self.profile.splitlines())

    def test_oracle_uses_dedicated_secret_placeholders(self):
        for name in (
            "ORCHESTRATOR_SHARED_SECRET",
            "CLAUDE_CALLBACK_SECRET",
            "N8N_ENCRYPTION_KEY",
            "N8N_POSTGRES_PASSWORD",
            "ORACLE_AUTOMERGE_REVIEW_SECRET",
        ):
            self.assertIn(f"{name}=REPLACE_WITH_RANDOM_SECRET", self.profile.splitlines())
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
            "REPOSITORY_SNAPSHOT_HOST_PATH=<snapshot-dir>",
            "REPOSITORY_SNAPSHOT_ROOT=/repository",
        )
        for line in expected:
            self.assertIn(line, self.profile.splitlines())

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
            self.assertIn(line, self.profile.splitlines())

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
    def test_wrapper_defaults_to_full_stack_and_preserves_explicit_api_only_canary(self):
        """Execute the shipped wrapper with a capture-only sibling launcher."""

        with tempfile.TemporaryDirectory() as raw:
            scripts = Path(raw)
            wrapper = scripts / 'wrapper.ps1'
            wrapper.write_text(self.wrapper, encoding='utf-8')
            (scripts / 'windows-isolated-live-validation.ps1').write_text(
                "param([string]$ProfileName, [switch]$ApiOnly, [int]$TimeoutSeconds)\n"
                "\"$ProfileName|$([bool]$ApiOnly)|$TimeoutSeconds\"", encoding='utf-8',
            )
            probe = f"& {_ps_literal(str(wrapper))}\n& {_ps_literal(str(wrapper))} -ApiOnly -TimeoutSeconds 17"
            self.assertEqual(_run_powershell(probe).splitlines(), [
                'long-table|False|900', 'long-table|True|17',
            ])

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
        # Find the executable call, then run its real guard with a capture-only
        # initializer. The definition alone must not satisfy this contract.
        probe = POWERSHELL_PRELUDE + PS_SCOPE_HELPERS + "\n$ast = Get-ScriptAst " + _ps_literal(
            str(ROOT / "scripts" / "windows-isolated-live-validation.ps1")
        ) + r'''
$calls = @($ast.FindAll({
    param($n)
    $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Initialize-N8nIntakeWorkflow'
}, $true))
if ($calls.Count -ne 1) { throw "expected exactly one initializer call, found $($calls.Count)" }
$guard = $calls[0].Parent
while ($guard -and $guard -isnot [System.Management.Automation.Language.IfStatementAst]) {
    $guard = $guard.Parent
}
if (-not $guard -or -not (Test-PsTopLevel -Node $guard)) { throw 'initializer guard is not at script level' }
function Initialize-N8nIntakeWorkflow {
    param($ComposeProject, $ProfilePath, $RepositoryRoot)
    $script:received += "$ComposeProject|$ProfilePath|$RepositoryRoot"
}
$composeProject = 'probe-compose'
$profilePath = 'probe-profile'
$repoRoot = 'probe-repo'
foreach ($ProfileName in 'long-table', 'uppercut') {
    foreach ($ApiOnly in $false, $true) {
        $script:received = @()
        Invoke-Expression $guard.Extent.Text
        Write-Output ("$ProfileName|$ApiOnly|" + $received.Count + '|' + ($received -join ','))
    }
}
'''
        self.assertEqual(_run_powershell(probe).strip().splitlines(), [
            "long-table|False|1|probe-compose|probe-profile|probe-repo",
            "long-table|True|0|",
            "uppercut|False|0|",
            "uppercut|True|0|",
        ])
        self.assertIn("$repoRoot = Split-Path -Parent $PSScriptRoot", self.script)
    def test_full_stack_profile_binds_slack_n8n_and_optional_bridge_to_project_channel(self):
        for line in (
            "SLACK_DIRECTOR_CHANNEL_ID=REPLACE_WITH_SLACK_CHANNEL_ID",
            "N8N_DIRECTOR_WEBHOOK_URL=http://n8n:5678/webhook/director-intake",
            "CHATGPT_BRIDGE_CHANNEL_IDS=REPLACE_WITH_SLACK_CHANNEL_ID",
        ):
            self.assertIn(line, self.profile.splitlines())
        self.assertNotIn("C0BSH9SPPUH", (ROOT / ".env.long-table.example").read_text(encoding="utf-8"))
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
            self.assertIn(line, self.profile.splitlines())
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
    def test_gemini_prevalidation_requires_listing_and_credential_without_enable_flag(self):
        probe = POWERSHELL_PRELUDE + _function_source(
            self.script, "Get-ValidatedUsageCheckProviders"
        ) + r'''
$scenarios = [ordered]@{
    'unlisted' = @{ USAGE_CHECK_PROVIDERS = 'openai,claude,cursor'; GEMINI_API_KEY = 'synthetic' }
    'ready' = @{ USAGE_CHECK_PROVIDERS = 'openai,claude,cursor,gemini'; GEMINI_API_KEY = 'synthetic' }
    'missing' = @{ USAGE_CHECK_PROVIDERS = 'openai,claude,cursor,gemini' }
    'placeholder' = @{ USAGE_CHECK_PROVIDERS = 'openai,claude,cursor,gemini'; GEMINI_API_KEY = 'REPLACE_WITH_GEMINI_API_KEY' }
}
foreach ($entry in $scenarios.GetEnumerator()) {
    try {
        $providers = @(Get-ValidatedUsageCheckProviders -Values $entry.Value -ProfileLabel 'probe')
        Write-Output ($entry.Key + '|ok|' + ($providers -join ','))
    } catch {
        Write-Output ($entry.Key + '|error|' + $_.Exception.Message)
    }
}
'''
        results = _scenario_results(_run_powershell(probe))
        self.assertEqual(results['unlisted'], ('ok', 'claude,cursor,openai'))
        self.assertEqual(results['ready'], ('ok', 'claude,cursor,gemini,openai'))
        for name in ('missing', 'placeholder'):
            self.assertEqual(results[name][0], 'error', name)
            self.assertIn('GEMINI_API_KEY is not configured', results[name][1], name)

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
            "stop $ProfileOwnedServices | Out-Null",
            outer_catch,
        )
    def test_previous_activation_is_stopped_and_verified_before_every_preflight_step(self):
        self.assertEqual(1, self.script.count('$ProfileOwnedServices = @("orchestrator", "slackbot", "n8n", "n8n-db")'))
        call = (
            "Stop-PreviouslyRunningServices -ComposeProject $composeProject "
            '-ProfilePath $profilePath -Services $ProfileOwnedServices'
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
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 21:35 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:07 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:21 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 23:39 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:09 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:08 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 07:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:13 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:25 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:40 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:03 UTC | END]]
