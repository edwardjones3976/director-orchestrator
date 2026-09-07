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
"""


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
$profileBefore = (Get-FileHash -LiteralPath $profileFile -Algorithm SHA256).Hash
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
$profileAfter = (Get-FileHash -LiteralPath $profileFile -Algorithm SHA256).Hash
Write-Output ('seedPresent|' + $seedPresent)
Write-Output ('seedLength|' + ([string]$seedValue).Length)
Write-Output ('threw|' + $threw)
Write-Output ('paidPresent|' + (Test-Path -LiteralPath 'Env:ALLOW_PAID_RUNS'))
Write-Output ('paidLength|' + ([string]$paid).Length)
Write-Output ('paidValue|' + [string]$paid)
Write-Output ('dockerCalls|' + $global:probeDocker.Count)
Write-Output ('composeCalls|' + @($global:probeDocker | Where-Object {{ $_ -like 'compose*' }}).Count)
Write-Output ('changedEnv|' + ($changed -join ','))
Write-Output ('profileUnchanged|' + ($profileBefore -eq $profileAfter))
"""
            return dict(
                line.strip().split("|", 1)
                for line in _run_powershell(probe, shell=shell).splitlines()
                if "|" in line
            )

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

    def test_wrapper_selects_api_only_long_table_canary(self):
        self.assertIn('ProfileName = "long-table"', self.wrapper)
        self.assertIn("ApiOnly = $true", self.wrapper)
        self.assertNotIn("docker compose", self.wrapper)

    def test_identity_ports_repository_and_stores_are_isolated(self):
        expected = (
            "APP_ENV=long-table",
            "DIRECTOR_COMPOSE_PROJECT_NAME=long-table-director",
            "DIRECTOR_HOST_PORT=38080",
            "N8N_HOST_PORT=35678",
            "SQLITE_PATH=/data/long-table.sqlite3",
            "PROJECT_ALLOWLIST=long-table",
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
