# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
"""Offline execution of the real rollback branch and host-bridge preflight."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from director_orchestrator.host_provider_bridge import (
    SUPPORTED_HOST_LANES,
    UNSUPPORTED_HOST_LANES,
)


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
SWITCHES = (
    "LANGGRAPH_PRIMARY_ENABLED",
    "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
    "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
    "ALLOW_PAID_RUNS",
)

# This process executes the script's actual inline Python against only these
# injected modules. No application import, database connection or DDL is possible.
DATABASE_STUB = r'''
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

code_path, scenario, environment_path, events_path = sys.argv[1:]
def state():
    return dict(line.split("=", 1) for line in Path(environment_path).read_text().splitlines())
def record(event, **extra):
    with open(events_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "state": state(), **extra}) + "\n")
class Store:
    def current_intake_authority(self):
        return SimpleNamespace(fencing_generation=7)
    def primary_control_plane_metrics(self):
        return SimpleNamespace(ambiguous_effect_count=int(scenario == "ambiguous"))
    def primary_quiescence_metrics(self):
        return SimpleNamespace(rollback_safe=scenario != "busy")
    def acquire_intake_authority(self, **kwargs):
        record("cas", arguments=kwargs)
        return SimpleNamespace(legacy_new_intake_disabled=scenario == "cas_failure")
def open_control_plane(*args, **kwargs):
    record("database")
    return SimpleNamespace(claim_store=Store(), close=lambda: record("closed"))
config = ModuleType("director_orchestrator.config")
config.Settings = lambda: SimpleNamespace(
    langgraph_primary_postgres_dsn="never-connected",
    langgraph_primary_postgres_schema="offline_fixture",
    langgraph_primary_allow_insecure_local_postgres=True,
    langgraph_primary_owner_token="offline-owner")
postgres = ModuleType("director_orchestrator.langgraph_postgres")
postgres.open_postgres_control_plane = open_control_plane
package = ModuleType("director_orchestrator")
package.__path__ = []
sys.modules.update({"director_orchestrator": package,
                   "director_orchestrator.config": config,
                   "director_orchestrator.langgraph_postgres": postgres})
exec(compile(Path(code_path).read_text(encoding="utf-8"), "<rollback-command>", "exec"))
'''

ROLLBACK_HARNESS = r'''
param($Source, $EnvironmentPath, $EventsPath, $Python, $Runner, $Scenario)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
foreach ($name in @('Set-DotEnvValue', 'Write-Switches', 'Invoke-Compose')) {
    $node = $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (@($node).Count -ne 1) { throw 'expected one production helper' }
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$block = $ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$Action -eq "Rollback"'}, $true)
if (@($block).Count -ne 1) { throw 'expected one production rollback branch' }
$script:environmentPath = $EnvironmentPath
$script:values = @{}
$script:ComposeProject = 'offline-cutover-test'
$databaseIdentity = 'offline-identity'; $databaseInstanceId = 'offline-instance'
function Record-Event($Event) {
    $state = @{}
    foreach ($line in Get-Content -LiteralPath $EnvironmentPath) {
        $parts = $line.Split('=', 2); $state[$parts[0]] = $parts[1]
    }
    @{event=$Event; state=$state} | ConvertTo-Json -Compress | Add-Content -LiteralPath $EventsPath
}
function Stop-And-Prove-AllIntake { Record-Event 'stopped' }
function Start-Sleep { param($Seconds) if($Seconds -ne 65){throw 'unexpected lease wait'}; Record-Event 'wait' }
function Wait-Health { Record-Event 'health'; @{langgraph_primary=@{}} }
function docker {
    $arguments = @($args)
    if ($arguments[0] -ne 'compose') { throw 'unexpected Docker operation' }
    if ($arguments -contains 'run') {
        if ($arguments[-2] -ne '-c') { throw 'unexpected helper command' }
        $codePath = Join-Path (Split-Path $EnvironmentPath) 'rollback-command.py'
        [IO.File]::WriteAllText($codePath, $arguments[-1])
        & $Python $Runner $codePath $Scenario $EnvironmentPath $EventsPath
        $global:LASTEXITCODE = $LASTEXITCODE
        return
    }
    if ($arguments -contains 'up' -and $arguments[-1] -eq 'orchestrator') {
        Record-Event 'restart'
    } elseif ($arguments -contains 'up' -and $arguments[-1] -eq 'slackbot') {
        Record-Event 'reopen'
    } else {
        throw 'unexpected Compose operation'
    }
    $global:LASTEXITCODE = 0
}
$Action = 'Rollback'
& ([scriptblock]::Create($block[0].Extent.Text))
'''


@unittest.skipUnless(PWSH, "PowerShell is required for offline control regressions")
class WindowsCutoverRegressionTests(unittest.TestCase):
    def test_launcher_lane_sets_match_the_python_protocol(self):
        source = (ROOT / "scripts/windows-host-provider-bridge.ps1").read_text(encoding="utf-8")
        for pattern, expected in (
            (r'\$provider -notin @\(([^)]+)\)', SUPPORTED_HOST_LANES),
            (r'foreach \(\$unsupported in @\(([^)]+)\)', UNSUPPORTED_HOST_LANES),
        ):
            match = re.search(pattern, source)
            self.assertIsNotNone(match)
            self.assertEqual(set(re.findall(r'"([^"]+)"', match.group(1))), set(expected))

    def _rollback(self, *, simulation: bool, scenario: str):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "fixture.env"
            initial = dict.fromkeys(SWITCHES, "true")
            initial["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"] = str(simulation).lower()
            initial["LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"] = str(not simulation).lower()
            initial["ALLOW_PAID_RUNS"] = str(not simulation).lower()
            initial["PRESERVED"] = "fixture-value"
            environment.write_text("".join(f"{key}={value}\n" for key, value in initial.items()))
            events = root / "events.jsonl"
            harness = root / "rollback.ps1"
            harness.write_text(ROLLBACK_HARNESS, encoding="utf-8")
            runner = root / "database_stub.py"
            runner.write_text(DATABASE_STUB, encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
                 str(ROOT / "scripts/windows-langgraph-primary-control.ps1"),
                 str(environment), str(events), sys.executable, str(runner), scenario],
                capture_output=True, text=True, timeout=20,
            )
            observed = [json.loads(line) for line in events.read_text(encoding="utf-8-sig").splitlines()]
            final = dict(line.split("=", 1) for line in environment.read_text().splitlines())
            self.assertEqual(list(root.glob("*.tmp")), [])
            self.assertEqual(final["PRESERVED"], "fixture-value")
            return result, observed, final

    def test_rollback_clears_simulation_after_cas_before_restart(self):
        for simulation in (True, False):
            with self.subTest(simulation=simulation):
                result, events, final = self._rollback(simulation=simulation, scenario="success")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([event["event"] for event in events],
                                 ["stopped", "wait", "database", "cas", "closed", "restart", "health", "reopen"])
                cas = next(event for event in events if event["event"] == "cas")
                self.assertEqual(cas["arguments"], {
                    "intake_owner": "legacy", "owner_token": "offline-owner", "expected_generation": 7})
                self.assertEqual(cas["state"]["LANGGRAPH_PRIMARY_ENABLED"], "true")
                self.assertEqual(cas["state"]["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"],
                                 str(simulation).lower())
                for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
                             "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"):
                    self.assertEqual(cas["state"][name], "false")
                restart = next(event for event in events if event["event"] == "restart")
                for name in SWITCHES:
                    self.assertEqual(restart["state"][name], "false")
                    self.assertEqual(final[name], "false")
                self.assertEqual(final["DIRECTOR_ASGI_APP"], "director_orchestrator.main:app")

    def test_rollback_refusals_do_not_restart_or_reopen_intake(self):
        reasons = {"ambiguous": "ambiguous effects block rollback",
                   "busy": "active provider dispatches",
                   "cas_failure": "legacy intake CAS did not complete"}
        for simulation in (True, False):
            for scenario, reason in reasons.items():
                with self.subTest(simulation=simulation, scenario=scenario):
                    result, events, final = self._rollback(simulation=simulation, scenario=scenario)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reason, result.stderr)
                    names = [event["event"] for event in events]
                    self.assertEqual(names[:3], ["stopped", "wait", "database"])
                    self.assertEqual("cas" in names, scenario == "cas_failure")
                    self.assertTrue(set(names).isdisjoint({"restart", "health", "reopen"}))
                    self.assertEqual(final["LANGGRAPH_PRIMARY_ENABLED"], "true")
                    self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"],
                                     str(simulation).lower())
                    for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
                                 "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"):
                        self.assertEqual(final[name], "false")

    def _preflight(self, commands):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "unexpected-provider-call"
            provider = root / "harmless_provider.py"
            provider.write_text("from pathlib import Path\nimport sys\nPath(sys.argv[1]).touch()\n")
            argv = [sys.executable, str(provider), str(marker)]
            command_map = {lane: argv if command is None else command for lane, command in commands.items()}
            environment = root / "fixture.env"
            values = {
                "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "offline-fixture-secret-" + "x" * 32,
                "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps(command_map),
                "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE": str(root / "never-opened.sqlite3"),
                "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN": "never-connected",
                "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "offline_fixture",
            }
            environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            before = environment.read_bytes()
            # Even an accidental fall-through into Run cannot launch the server.
            fake_python = root / "never_start_bridge.ps1"
            fake_python.write_text("throw 'Preflight attempted bridge startup'\n")
            result = subprocess.run(
                [PWSH, "-NoProfile", "-NonInteractive", "-File",
                 str(ROOT / "scripts/windows-host-provider-bridge.ps1"),
                 "-Action", "Preflight", "-EnvironmentFile", str(environment),
                 "-Workspace", str(root), "-Python", str(fake_python)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertFalse(marker.exists(), "preflight invoked the configured provider")
            self.assertFalse((root / "never-opened.sqlite3").exists())
            self.assertEqual(environment.read_bytes(), before)
            return result

    def test_preflight_accepts_python_supported_lanes_without_dispatch(self):
        # Every supported Python lane is checked individually, including Cursor.
        for lane in sorted(SUPPORTED_HOST_LANES):
            with self.subTest(lane=lane):
                result = self._preflight({lane: None})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("no provider was invoked", result.stdout)

    def test_preflight_preserves_unsupported_and_invalid_command_refusals(self):
        cases = [
            ({lane: None}, "cannot be enabled")
            for lane in sorted(UNSUPPORTED_HOST_LANES)
        ] + [
            ({"unknown-lane": None}, "unallowlisted provider"),
            ({"cursor": []}, "empty command"),
            ({"cursor": ["director-nonexistent-offline-fixture-executable"]}, "executable is unavailable"),
        ]
        for commands, reason in cases:
            with self.subTest(commands=commands):
                result = self._preflight(commands)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("preflight passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | END]]
