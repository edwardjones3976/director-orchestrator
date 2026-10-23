# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 05:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 03:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:42 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 06:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
"""Offline execution of the real rollback branch and host-bridge preflight."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from director_orchestrator.host_provider_bridge import (
    _LANE_AUTH_ENV,
    SUPPORTED_HOST_LANES,
)


def _g67b_diag(category, observe):
    """Best-effort sanitized observations; never expose an exception or affect checks.

    Only a fixed-pattern label and boolean values are ever written. Under
    GitHub Actions they go to the job summary, which the parallel runner's
    redacted log does not filter; elsewhere they go to stderr.
    """
    def emit(line):
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if target:
            with open(target, "a", encoding="utf-8") as summary:
                summary.write(line + "\n")
        else:
            print(line, file=sys.__stderr__, flush=True)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,96}", category):
            raise ValueError
        payload = observe()
        if payload is None:
            return
        if not payload or not all(isinstance(key, str) and re.fullmatch(r"[a-z0-9_]{1,48}", key)
                                  and isinstance(value, bool) for key, value in payload.items()):
            raise ValueError
        emit("WO-G67B-DIAG " + category + " " + " ".join(
            key + "=" + ("true" if value else "false") for key, value in sorted(payload.items())))
    except Exception:
        try:
            emit("WO-G67B-DIAG diag_error " + (category if re.fullmatch(r"[A-Za-z0-9_]{1,96}", category) else "invalid"))
        except Exception:
            pass


def _g67b_tempdir(tmp):
    path = Path(tmp)
    return {
        "tempdir_changes_under_resolve": str(path) != str(path.resolve()),
        "tempdir_has_tilde_component": any("~" in part for part in path.parts),
    }


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
# The Z.ai lane's bridge entry shape: [executable, GLM model, max turns, write mode, key-ID digest].
ZAI_BRIDGE_ENTRY = [sys.executable, "glm-5.3-flash", "30", "read-only", "sha256:" + "0" * 64]
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
# The parameter block is not extracted; route native calls to the fixture below.
$DockerExecutable = 'docker'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
foreach ($name in @('Set-DotEnvValue', 'Write-Switches', 'Invoke-Compose', 'Get-ServiceContainer',
    'Get-ServiceImageId', 'Assert-ServiceImage', 'Get-PriorImagesPath', 'Read-PriorImages')) {
    $node = $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (@($node).Count -ne 1) { throw 'expected one production helper' }
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$block = $ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$Action -eq "Rollback"'}, $true)
if (@($block).Count -ne 1) { throw 'expected one production rollback branch' }
$script:environmentPath = $EnvironmentPath
$script:values = @{}
$script:ComposeProject = 'offline-cutover-test'
$script:runtimeImageId = 'sha256:' + ('c' * 64)
$script:images = @{ orchestrator = $script:runtimeImageId; slackbot = $script:runtimeImageId }
$env:DIRECTOR_RUNTIME_IMAGE = $script:runtimeImageId
$databaseIdentity = 'offline-identity'; $databaseInstanceId = 'offline-instance'
function Record-Event($Event, $Image) {
    $state = @{}
    foreach ($line in Get-Content -LiteralPath $EnvironmentPath) {
        $parts = $line.Split('=', 2); $state[$parts[0]] = $parts[1]
    }
    @{event=$Event; state=$state; image=$Image} | ConvertTo-Json -Compress | Add-Content -LiteralPath $EventsPath
}
function Stop-And-Prove-AllIntake { Record-Event 'stopped' }
function Start-Sleep { param($Seconds) if($Seconds -ne 65){throw 'unexpected lease wait'}; Record-Event 'wait' }
function Wait-Health { Record-Event 'health'; @{langgraph_primary=@{}} }
function docker {
    $arguments = @($args)
    if ($arguments[0] -eq 'ps') {
        $selected = @($arguments | Where-Object { $_ -like 'label=com.docker.compose.service=*' })
        if ($selected.Count -ne 1 -or $arguments -notcontains "label=com.docker.compose.project=$($script:ComposeProject)") {
            throw 'unexpected container selection'
        }
        $global:LASTEXITCODE = 0
        return $selected[0].Split('=')[-1] + '-container'
    }
    if ($arguments[0] -eq 'inspect') {
        $service = ([string]$arguments[-1]).Replace('-container', '')
        Record-Event "readback-$service" $script:images[$service]
        $global:LASTEXITCODE = 0
        return $script:images[$service]
    }
    if ($arguments[0] -ne 'compose') { throw 'unexpected Docker operation' }
    if ($arguments -contains 'run') {
        if ($arguments[-2] -ne '-c') { throw 'unexpected helper command' }
        $codePath = Join-Path (Split-Path $EnvironmentPath) 'rollback-command.py'
        [IO.File]::WriteAllText($codePath, $arguments[-1])
        & $Python $Runner $codePath $Scenario $EnvironmentPath $EventsPath
        $global:LASTEXITCODE = $LASTEXITCODE
        return
    }
    $service = [string]$arguments[-1]
    if ($arguments -contains 'start') {
        if ($service -ne 'slackbot') { throw 'unexpected Compose start' }
        Record-Event 'reopen' $script:images[$service]
        $global:LASTEXITCODE = 0
        return
    }
    $name = @{ orchestrator = 'restart'; n8n = 'n8n'; slackbot = 'create-slackbot' }[$service]
    if ($arguments -notcontains 'up' -or -not $name) { throw 'unexpected Compose operation' }
    if ($service -eq 'slackbot' -and $arguments -notcontains '--no-start') { throw 'gateway started before its readback' }
    Record-Event $name $env:DIRECTOR_RUNTIME_IMAGE
    if ($service -ne 'n8n' -and -not ($Scenario -eq "wrong-$service")) {
        $script:images[$service] = $env:DIRECTOR_RUNTIME_IMAGE
    }
    $global:LASTEXITCODE = 0
}
$Action = 'Rollback'
& ([scriptblock]::Create($block[0].Extent.Text))
'''


PRIOR_ORCHESTRATOR = "sha256:" + "a" * 64
PRIOR_GATEWAY = "sha256:" + "b" * 64
CANDIDATE_IMAGE = "sha256:" + "c" * 64

# The production activation branch and every image helper execute; only intake
# proofs, evidence, inventory, health and Docker are substituted, so no service,
# database or provider is reachable.
ACTIVATION_HARNESS = r'''
param($Source, $EnvironmentPath, $EventsPath, $Action, $HealthJson, $ImagesJson, $Scenario)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# The parameter block is not extracted; route native calls to the fixture below.
$DockerExecutable = 'docker'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
foreach ($name in @('Set-DotEnvValue', 'Write-Switches', 'Invoke-Compose', 'Get-ServiceContainer',
    'Get-ServiceImageId', 'Assert-ServiceImage', 'Get-PriorImagesPath', 'Read-PriorImages', 'Save-PriorImages',
    'Read-DotEnv', 'Assert-OfflineHandoffProfile', 'Test-ActivatedChain', 'Get-OfflineAttemptPath')) {
    $node = $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (@($node).Count -ne 1) { throw 'expected one production helper' }
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$block = $ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$Action -in @("Activate", "ActivateOffline")'}, $true)
if (@($block).Count -ne 1) { throw 'expected one production activation branch' }
$script:environmentPath = $EnvironmentPath
$script:values = Read-DotEnv $EnvironmentPath
$script:acceptanceMode = if ($Scenario -like 'mode-*') { $Scenario.Substring(5) } else { 'provider-v2' }
$script:ComposeProject = 'offline-activation-test'
$script:runtimeImageId = 'sha256:' + ('c' * 64)
$runtimeImageId = $script:runtimeImageId
$env:DIRECTOR_RUNTIME_IMAGE = $script:runtimeImageId
$script:images = @{}
foreach ($entry in ($ImagesJson | ConvertFrom-Json).PSObject.Properties) { $script:images[$entry.Name] = [string]$entry.Value }
function Record-Event($Event, $Image) {
    @{event=$Event; image=$Image} | ConvertTo-Json -Compress | Add-Content -LiteralPath $EventsPath
}
function Stop-And-Prove-AllIntake { Record-Event 'intake-stopped' }
function Assert-PrimaryQuiescent { param([switch]$AfterStop, [switch]$WritersOnly)
    $event = if ($WritersOnly) { 'writers-idle-after-stop' } elseif ($AfterStop) { 'quiescent-after-stop' } else { 'quiescent-before-stop' }
    Record-Event $event
    if ($Scenario -like "*busy-$event") { throw "Activation refused: the primary plane is not quiescent" } }
function Get-LegacyInventory { Record-Event 'inventory'; @('1', ('d' * 64)) }
function Assert-RunningCandidate { param([string]$AsgiApp) Record-Event "candidate-$AsgiApp" }
function Wait-Health { Record-Event 'health'; [pscustomobject]@{ langgraph_primary = ($HealthJson | ConvertFrom-Json) } }
function docker {
    $arguments = @($args)
    if ($arguments[0] -eq 'ps') {
        $selected = @($arguments | Where-Object { $_ -like 'label=com.docker.compose.service=*' })
        if ($selected.Count -ne 1 -or $arguments -notcontains "label=com.docker.compose.project=$($script:ComposeProject)") {
            throw 'unexpected container selection'
        }
        $global:LASTEXITCODE = 0
        $service = $selected[0].Split('=')[-1]
        # A service with no image entry has no container at all.
        if (-not $script:images.ContainsKey($service)) { return }
        return $service + '-container'
    }
    if ($arguments[0] -eq 'inspect') {
        $service = ([string]$arguments[-1]).Replace('-container', '')
        Record-Event "readback-$service" $script:images[$service]
        $global:LASTEXITCODE = 0
        return $script:images[$service]
    }
    if (($arguments[0..4] -join '|') -cne "compose|--project-name|$($script:ComposeProject)|--env-file|$EnvironmentPath" -or
        $arguments[5] -cnotin @('up', 'start')) { throw 'unexpected Docker operation' }
    $service = [string]$arguments[-1]
    if ($arguments[5] -ceq 'start') {
        if ($service -ne 'slackbot') { throw 'unexpected Compose start' }
        Record-Event "start-$service" $script:images[$service]
        $global:LASTEXITCODE = 0
        return
    }
    if ($service -eq 'slackbot' -and $arguments -notcontains '--no-start') { throw 'gateway started before its readback' }
    Record-Event "up-$service" $env:DIRECTOR_RUNTIME_IMAGE
    if ($service -ne 'n8n' -and -not ($Scenario -like '*wrong-gateway' -and $service -eq 'slackbot')) {
        $script:images[$service] = $env:DIRECTOR_RUNTIME_IMAGE
    }
    $global:LASTEXITCODE = 0
}
function Invoke-Evidence { param([string]$Command, [string]$Stage, [string]$FactsJson, [int]$LegacyTaskCount, [string]$LegacyInventoryDigest)
    Record-Event ("evidence-$Command" + $(if ($Stage) { "-$Stage" } else { '' }))
    if ($Command -eq 'require-activated' -and $Scenario -notlike 'activated*') { throw 'activation chain is not ACTIVATED' }
    if ($Scenario -eq "fail-$Command") { throw "evidence $Command refused" } }
function Assert-AllIntakeStopped { Record-Event 'intake-proved-stopped'
    if ($Scenario -eq 'activated-running') { throw 'Authenticated intake surface remains active: orchestrator' } }
function Assert-PrimaryOwnsIntake { Record-Event 'primary-owns-intake'
    if ($Scenario -eq 'activated-rolled-back') { throw 'Activation resume refused: new intake is no longer owned by the primary' } }
& ([scriptblock]::Create($block[0].Extent.Text))
'''


# Reuse the AST execution approach above. Only Docker is substituted: the
# production evidence CLI authenticates a fixture journal, and the production
# inventory query reads a real, temporary legacy SQLite database.
OFFLINE_HANDOFF_HARNESS = r'''
param($Source, $EnvironmentPath, $EventsPath, $Python, $IdentityPath, $Scenario)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# The parameter block is not extracted; route native calls to the fixture below.
$DockerExecutable = 'docker'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
foreach ($name in @('Read-DotEnv', 'Assert-OfflineHandoffProfile', 'Invoke-Compose',
    'Invoke-Evidence', 'Get-LegacyInventory', 'Stop-And-Prove-AllIntake', 'Assert-AllIntakeStopped')) {
    $node = $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (@($node).Count -ne 1) { throw 'expected one production helper' }
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$identity = Get-Content -LiteralPath $IdentityPath -Raw | ConvertFrom-Json
$script:environmentPath = $EnvironmentPath
$script:values = Read-DotEnv $EnvironmentPath
$script:acceptanceMode = $script:values['LANGGRAPH_PRIMARY_ACCEPTANCE_MODE']
$script:ComposeProject = $identity.compose_project
$script:candidateCommit = $identity.candidate_commit
$script:candidateTree = $identity.candidate_tree
$script:databaseIdentity = $identity.database_identity
$script:databaseInstanceId = $identity.database_instance_id
$script:proofs = 0
function Record-Event($Event) { $Event | Add-Content -LiteralPath $EventsPath }
function docker {
    $arguments = @($args)
    if (($arguments[0..4] -join '|') -cne "compose|--project-name|$ComposeProject|--env-file|$EnvironmentPath") {
        throw 'unexpected Docker selection'
    }
    switch -CaseSensitive ($arguments[5]) {
        'stop' {
            if ($arguments.Count -ne 9 -or ($arguments[6..8] -join '|') -cne 'slackbot|n8n|orchestrator') { throw 'unexpected stop' }
            Record-Event 'stop'
        }
        'ps' {
            if (($arguments[6..9] -join '|') -cne '--status|running|-q|slackbot' -and
                ($arguments[6..9] -join '|') -cne '--status|running|-q|n8n' -and
                ($arguments[6..9] -join '|') -cne '--status|running|-q|orchestrator') { throw 'unexpected observation' }
            $service = $arguments[-1]
            if ($service -eq 'slackbot') { $script:proofs++ }
            Record-Event "proof-$script:proofs-$service"
            if ($Scenario -eq "active-$service" -or ($Scenario -eq 'reopened' -and $script:proofs -eq 2 -and $service -eq 'n8n')) {
                'still-running-fixture'
            }
        }
        'run' {
            if (($arguments[6..13] -join '|') -cne '--rm|--no-deps|--pull|never|-T|--entrypoint|python|orchestrator') { throw 'unexpected utility flags' }
            $command = $arguments[14..($arguments.Count - 1)]
            if ($command[0] -eq '-m' -and $command[1] -eq 'director_orchestrator.primary_activation_evidence' -and
                $command[2] -cin @('require-staged', 'bind-legacy-inventory', 'require-ready')) {
                if ($command[3] -cne '--artifact' -or $command[4] -cne '/data/langgraph-primary-activation-evidence.json') {
                    throw 'unexpected evidence artifact'
                }
                # Translate the container path at the fixture transport boundary.
                $command[4] = Join-Path (Split-Path -Parent $EnvironmentPath) 'activation-evidence.json'
                Record-Event $command[2]
            } elseif ($command[0] -eq '-c' -and $command.Count -eq 2) {
                Record-Event 'inventory'
            } else { throw 'unexpected utility; activation and provider validation forbidden' }
            & $Python @command
            $global:LASTEXITCODE = $LASTEXITCODE
            return
        }
        default { throw 'service start/build or unexpected Docker command forbidden' }
    }
    $global:LASTEXITCODE = 0
}
function Write-Switches { throw 'offline handoff must preserve configuration' }
function Wait-Health { throw 'offline handoff must not probe live runtime' }
$Action = if ($Scenario -eq 'lowercase') { 'prepareofflinehandoff' } else { 'PrepareOfflineHandoff' }
# Execute both the initial profile guard and the final production branch.
$blocks = $ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Clauses[0].Item1.Extent.Text -ceq '$Action -eq "PrepareOfflineHandoff"'
}
if (@($blocks).Count -ne 2) { throw 'expected profile guard and handoff branch' }
foreach ($block in $blocks) { & ([scriptblock]::Create($block.Extent.Text)) }
'''


@unittest.skipUnless(PWSH, "PowerShell is required for offline handoff regressions")
class OfflineHandoffRegressionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_acceptance_journal import AcceptanceJournalTests
        self.fixture = AcceptanceJournalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.legacy = self.root / "legacy.sqlite3"
        with sqlite3.connect(self.legacy) as connection:
            connection.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT)")
            connection.execute("INSERT INTO tasks VALUES ('legacy-task', 'OWNER_DECISION_REQUIRED')")

    def _restore(self):
        from director_orchestrator.primary_activation_evidence import ActivationStage, canonical_facts_digest
        self.fixture.accept()
        for stage, facts in (
            (ActivationStage.MIGRATED, {}),
            (ActivationStage.BACKED_UP, {"backup_digest": "sha256:" + "1" * 64}),
            (ActivationStage.RESTORE_PROVED, {"backup_digest": "sha256:" + "1" * 64,
                "required_control_plane_tables": 17, "checkpoint_tables": 4, "schema_evidence_rows": 1}),
        ):
            self.fixture.store.record(self.fixture.identity, stage,
                evidence_digest=canonical_facts_digest(facts), facts=facts)

    def _handoff(self, *, overrides=None, identity=None, scenario="success"):
        identity = identity or self.fixture.identity
        values = dict(ALLOW_PAID_RUNS="false", LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false",
            DIRECTOR_PROVIDER_VALIDATION_ENABLED="false", LANGGRAPH_PRIMARY_ACCEPTANCE_MODE="provider-v2",
            APP_ENV=identity.profile_id, LANGGRAPH_PRIMARY_POSTGRES_SCHEMA=identity.postgres_schema,
            LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE=identity.checkpoint_namespace)
        values.update(overrides or {})
        environment = self.root / "fixture.env"
        environment.write_text("".join(f"{name}={value}\n" for name, value in values.items() if value is not None))
        before = environment.read_bytes()
        identity_path = self.root / "identity.json"
        identity_path.write_text(json.dumps(asdict(identity)))
        events = self.root / "events.txt"
        events.write_text("")
        harness = self.root / "offline-handoff.ps1"
        harness.write_text(OFFLINE_HANDOFF_HARNESS)
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
            str(ROOT / "scripts/windows-langgraph-primary-control.ps1"), str(environment), str(events),
            sys.executable, str(identity_path), scenario], capture_output=True, text=True, timeout=60,
            env=dict(os.environ, SQLITE_PATH=str(self.legacy)))
        self.assertEqual(environment.read_bytes(), before)
        state = self.fixture.store.load()
        self.assertNotIn("ACTIVATED", [item["stage"] for item in state["stages"]])
        return result, events.read_text(encoding="utf-8-sig").splitlines(), state

    def test_missing_acceptance_or_restore_stops_before_inventory(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                if accepted:
                    self.fixture.accept()
                result, events, state = self._handoff()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("prerequisites are incomplete", result.stderr)
                self.assertEqual(events, ["require-staged"])
                self.assertNotIn("legacy_inventory", state)

    def test_requires_explicit_paid_off_profile_and_selected_generation_mode(self):
        for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "DIRECTOR_PROVIDER_VALIDATION_ENABLED"):
            for value in (None, "true", "0"):
                with self.subTest(name=name, value=value):
                    result, events, _ = self._handoff(overrides={name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires explicit false", result.stderr)
                    self.assertEqual(events, [])
        result, events, _ = self._handoff(overrides={"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "github-v1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("selected provider-v2 generation", result.stderr)
        self.assertEqual(events, [])
        # PowerShell ValidateSet accepts action spelling case-insensitively.
        result, events, _ = self._handoff(overrides={"ALLOW_PAID_RUNS": "true"}, scenario="lowercase")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires explicit false", result.stderr)
        self.assertEqual(events, [])

    def test_stale_candidate_or_database_identity_cannot_bind_inventory(self):
        self._restore()
        for identity in (replace(self.fixture.identity, candidate_commit="3" * 40),
                         replace(self.fixture.identity, database_instance_id="33333333-3333-4333-8333-333333333333")):
            with self.subTest(identity=identity):
                result, events, state = self._handoff(identity=identity)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unavailable or stale", result.stderr)
                self.assertEqual(events, ["require-staged"])
                self.assertNotIn("legacy_inventory", state)

    def test_active_intake_blocks_capture_and_reopened_intake_blocks_success(self):
        self._restore()
        for service in ("slackbot", "n8n", "orchestrator"):
            with self.subTest(service=service):
                result, events, state = self._handoff(scenario="active-" + service)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("surface remains active", result.stderr)
                self.assertNotIn("inventory", events)
                self.assertNotIn("legacy_inventory", state)
        result, events, _ = self._handoff(scenario="reopened")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("surface remains active", result.stderr)
        self.assertIn("require-ready", events)
        self.assertNotIn("Offline handoff inventory prepared", result.stdout)

    def test_same_inventory_retry_succeeds_without_activation_or_service_start(self):
        self._restore()
        stages = self.fixture.store.load()["stages"]
        for _ in range(2):
            result, events, state = self._handoff()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events, ["require-staged", "stop", "proof-1-slackbot", "proof-1-n8n",
                "proof-1-orchestrator", "inventory", "bind-legacy-inventory", "require-ready",
                "proof-2-slackbot", "proof-2-n8n", "proof-2-orchestrator"])
            self.assertEqual(state["stages"], stages)
            expected = hashlib.sha256(b'[["legacy-task","OWNER_DECISION_REQUIRED"]]').hexdigest()
            self.assertEqual(state["legacy_inventory"], {"digest": expected, "task_count": 1, "owner": "legacy"})
            self.assertIn("No runtime was started or activated", result.stdout)

    def test_changed_inventory_is_refused_without_replacing_bound_inventory(self):
        self._restore()
        result, _, state = self._handoff()
        self.assertEqual(result.returncode, 0, result.stderr)
        held = state["legacy_inventory"]
        for mutation in ("UPDATE tasks SET status='COMPLETED'",
                         "INSERT INTO tasks VALUES ('another-task', 'QUEUED')"):
            with self.subTest(mutation=mutation):
                with sqlite3.connect(self.legacy) as connection:
                    connection.execute(mutation)
                result, events, state = self._handoff()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("inventory is immutable", result.stderr)
                self.assertNotIn("require-ready", events)
                self.assertEqual(state["legacy_inventory"], held)


@unittest.skipUnless(PWSH, "PowerShell is required for offline control regressions")
class WindowsCutoverRegressionTests(unittest.TestCase):
    def test_launcher_lane_sets_match_the_python_protocol(self):
        source = (ROOT / "scripts/windows-host-provider-bridge.ps1").read_text(encoding="utf-8")
        for pattern, expected in (
            (r'\$provider -notin @\(([^)]+)\)', SUPPORTED_HOST_LANES),
            # The launcher forwards only these lane credentials from the profile.
            (r'\$laneAuthNames = @\(([^)]+)\)', frozenset().union(*_LANE_AUTH_ENV.values())),
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
            events.write_text("")
            prior = Path(str(environment) + ".prior-images.unselected." + "c" * 64 + ".json")
            if scenario == "invalid-record":
                prior.write_text(json.dumps({"version": "director-prior-images/v1"}))
            elif scenario != "missing-record":
                prior.write_text(json.dumps({
                    "version": "director-prior-images/v1", "compose_project": "offline-cutover-test",
                    "candidate_image_id": CANDIDATE_IMAGE, "orchestrator_image_id": PRIOR_ORCHESTRATOR,
                    "gateway_image_id": PRIOR_GATEWAY}))
            harness = root / "rollback.ps1"
            harness.write_text(ROLLBACK_HARNESS, encoding="utf-8")
            runner = root / "database_stub.py"
            runner.write_text(DATABASE_STUB, encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
                 str(ROOT / "scripts/windows-langgraph-primary-control.ps1"),
                 str(environment), str(events), sys.executable, str(runner), scenario],
                capture_output=True, text=True, timeout=300,
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
                # The gateway is created stopped and read back before n8n or Slack reopen.
                self.assertEqual([event["event"] for event in events],
                                 ["stopped", "wait", "database", "cas", "closed", "restart",
                                  "readback-orchestrator", "health", "create-slackbot", "readback-slackbot",
                                  "n8n", "reopen"])
                images = {event["event"]: event["image"] for event in events if event.get("image")}
                # Each service returns to its own recorded prior image, not the candidate.
                self.assertEqual(images["restart"], PRIOR_ORCHESTRATOR)
                self.assertEqual(images["readback-orchestrator"], PRIOR_ORCHESTRATOR)
                self.assertEqual(images["create-slackbot"], PRIOR_GATEWAY)
                self.assertEqual(images["readback-slackbot"], PRIOR_GATEWAY)
                self.assertEqual(images["reopen"], PRIOR_GATEWAY)
                self.assertNotIn(CANDIDATE_IMAGE, {images["restart"], images["create-slackbot"]})
                self.assertIn("recorded prior orchestrator and gateway images", result.stdout)
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
                    self.assertTrue(set(names).isdisjoint({"restart", "health", "create-slackbot", "n8n", "reopen"}))
                    self.assertEqual(final["LANGGRAPH_PRIMARY_ENABLED"], "true")
                    self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"],
                                     str(simulation).lower())
                    for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
                                 "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"):
                        self.assertEqual(final[name], "false")

    def test_rollback_without_prior_image_record_still_returns_intake_to_legacy(self):
        result, events, final = self._rollback(simulation=False, scenario="missing-record")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([event["event"] for event in events],
                         ["stopped", "wait", "database", "cas", "closed", "restart",
                          "readback-orchestrator", "health", "create-slackbot", "readback-slackbot",
                          "n8n", "reopen"])
        # No image restore: both services are verified on the candidate image.
        for event in events:
            if event.get("image"):
                self.assertEqual(event["image"], CANDIDATE_IMAGE, event["event"])
        self.assertIn("no image was restored", result.stdout)
        for name in SWITCHES:
            self.assertEqual(final[name], "false")

    def test_rollback_with_an_invalid_prior_image_record_refuses_before_stopping(self):
        result, events, final = self._rollback(simulation=False, scenario="invalid-record")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prior-image record is invalid", result.stderr)
        self.assertEqual(events, [])
        self.assertEqual(final["ALLOW_PAID_RUNS"], "true")

    def test_rollback_gateway_readback_mismatch_closes_all_intake(self):
        result, events, final = self._rollback(simulation=False, scenario="wrong-slackbot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("slackbot image differs", result.stderr)
        self.assertIn("intake remain stopped", result.stderr)
        names = [event["event"] for event in events]
        self.assertEqual(names[-3:], ["create-slackbot", "readback-slackbot", "stopped"])
        self.assertEqual(events[-2]["image"], CANDIDATE_IMAGE)
        # The refused gateway was never started and n8n never reopened.
        self.assertNotIn("reopen", names)
        self.assertNotIn("n8n", names)
        self.assertEqual(final["ALLOW_PAID_RUNS"], "false")

    def test_rollback_orchestrator_readback_mismatch_closes_all_intake(self):
        result, events, _ = self._rollback(simulation=False, scenario="wrong-orchestrator")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("orchestrator image differs", result.stderr)
        self.assertIn("intake remain stopped", result.stderr)
        names = [event["event"] for event in events]
        self.assertEqual(names[-3:], ["restart", "readback-orchestrator", "stopped"])
        self.assertTrue(set(names).isdisjoint({"health", "create-slackbot", "n8n", "reopen"}))

    def _activate(self, root, *, action="Activate", health=None, images=None, scenario="success"):
        environment = root / "fixture.env"
        if not environment.exists():
            environment.write_text("".join(f"{name}=false\n" for name in SWITCHES)
                                   + "DIRECTOR_PROVIDER_VALIDATION_ENABLED=false\nPRESERVED=fixture-value\n")
        primary = dict.fromkeys(("primary_graph_constructed", "primary_router_wired", "checkpoint_backend_postgres",
            "checkpoint_schema_current", "claim_store_ready", "effect_ledger_ready", "writer_lease_ready",
            "reconciliation_worker_ready", "primary_runtime_active", "paid_off_primary_intake_active",
            "legacy_new_intake_disabled"), True)
        primary.update(new_intake_owner="langgraph", ambiguous_effect_count=0, intake_fencing_generation=3,
                       provider_activation={"combined_live_ready": True})
        primary.update(health or {})
        events = root / "events.jsonl"
        events.write_text("")
        harness = root / "activation.ps1"
        harness.write_text(ACTIVATION_HARNESS, encoding="utf-8")
        result = subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
             str(ROOT / "scripts/windows-langgraph-primary-control.ps1"), str(environment), str(events), action,
             json.dumps(primary), json.dumps({"orchestrator": PRIOR_ORCHESTRATOR, "slackbot": PRIOR_GATEWAY} if images is None else images),
             scenario],
            capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
        )
        observed = [json.loads(line) for line in events.read_text(encoding="utf-8-sig").splitlines()]
        final = dict(line.split("=", 1) for line in environment.read_text().splitlines())
        self.assertEqual(final["PRESERVED"], "fixture-value")
        self.assertEqual(list(root.glob("*.tmp")), [])
        prior = root / ("fixture.env.prior-images.unselected." + "c" * 64 + ".json")
        return result, observed, final, prior

    def test_paid_activation_records_activated_on_the_verified_gateway_before_reopening_intake(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, prior = self._activate(Path(temporary))
            self.assertEqual(result.returncode, 0, result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[:8], ["evidence-require-activated", "quiescent-before-stop",
                                         "intake-stopped", "quiescent-after-stop",
                                         "inventory", "evidence-bind-legacy-inventory",
                                         "evidence-require-ready", "readback-orchestrator"])
            self.assertLess(names.index("readback-slackbot"), names.index("up-orchestrator"))
            # Created stopped, read back, ACTIVATED recorded, and only then n8n and Slack start.
            self.assertEqual(names[-5:], ["up-slackbot", "readback-slackbot", "evidence-record-ACTIVATED",
                                          "up-n8n", "start-slackbot"])
            self.assertEqual(events[-4]["image"], CANDIDATE_IMAGE)
            self.assertEqual(events[-1]["image"], CANDIDATE_IMAGE)
            self.assertEqual(json.loads(prior.read_text())["orchestrator_image_id"], PRIOR_ORCHESTRATOR)
            self.assertEqual(json.loads(prior.read_text())["gateway_image_id"], PRIOR_GATEWAY)
            for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
                         "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED", "LANGGRAPH_PRIMARY_ENABLED"):
                self.assertEqual(final[name], "true")
            self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], "false")
            self.assertIn("authenticated intake was reopened", result.stdout)

    def test_paid_activation_without_provider_readiness_unwinds_before_intake(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(
                Path(temporary), health={"provider_activation": {"combined_live_ready": False}})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unwound", result.stderr)
            names = [event["event"] for event in events]
            self.assertNotIn("evidence-record-ACTIVATED", names)
            self.assertNotIn("up-slackbot", names)
            self.assertEqual(names[-1], "intake-stopped")
            for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
                         "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"):
                self.assertEqual(final[name], "false")
            self.assertEqual(final["LANGGRAPH_PARALLEL_KILL_SWITCH"], "true")

    def test_paid_activation_refuses_a_busy_primary_plane_before_any_switch(self):
        # Work approved under one mode must never be recovered by the other
        # mode's dispatcher, so both activations refuse a busy plane.
        for action in ("Activate", "ActivateOffline"):
            for event, expected in (("quiescent-before-stop", ["quiescent-before-stop"]),
                                    ("quiescent-after-stop",
                                     ["quiescent-before-stop", "intake-stopped", "quiescent-after-stop"])):
                with self.subTest(action=action, event=event), tempfile.TemporaryDirectory() as temporary:
                    self._assert_busy_plane_refused(Path(temporary), action, event, expected)

    def _assert_busy_plane_refused(self, root, action, event, expected):
                result, events, final, prior = self._activate(root, action=action, scenario=f"busy-{event}")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not quiescent", result.stderr)
                # Paid Activate first asks (read-only) whether the chain is resumable.
                prefix = ["evidence-require-activated"] if action == "Activate" else []
                self.assertEqual([item["event"] for item in events], prefix + expected)
                self.assertFalse(prior.exists())
                self.assertEqual(final["ALLOW_PAID_RUNS"], "false")
                self.assertEqual(final["LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"], "false")

    def test_activated_chain_resumes_only_the_start_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, prior = self._activate(Path(temporary), scenario="activated")
            self.assertEqual(result.returncode, 0, result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[:5], ["evidence-require-activated", "intake-proved-stopped",
                                         "primary-owns-intake", "intake-stopped", "writers-idle-after-stop"])
            for skipped in ("quiescent-before-stop", "inventory", "evidence-bind-legacy-inventory",
                            "evidence-require-ready", "evidence-record-ACTIVATED"):
                self.assertNotIn(skipped, names)
            # Same readbacks and health proof, then intake reopens last.
            self.assertLess(names.index("up-orchestrator"), names.index("candidate-director_orchestrator.main:app"))
            self.assertIn("health", names)
            self.assertEqual(names[-4:], ["up-slackbot", "readback-slackbot", "up-n8n", "start-slackbot"])
            self.assertEqual(events[-3]["image"], CANDIDATE_IMAGE)
            self.assertEqual(final["ALLOW_PAID_RUNS"], "true")
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "true")
            self.assertFalse(prior.exists())
            self.assertIn("Nothing was re-recorded", result.stdout)

    def test_activated_chain_resume_still_refuses_a_mismatched_readback_or_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary), scenario="activated-wrong-gateway")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("slackbot image differs", result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[-3:], ["up-slackbot", "readback-slackbot", "intake-stopped"])
            self.assertNotIn("up-n8n", names)
            self.assertNotIn("start-slackbot", names)
            self.assertNotIn("evidence-record-ACTIVATED", names)
            self.assertEqual(final["ALLOW_PAID_RUNS"], "false")
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary), scenario="activated-rolled-back")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no longer owned by the primary", result.stderr)
            self.assertEqual([event["event"] for event in events],
                             ["evidence-require-activated", "intake-proved-stopped", "primary-owns-intake"])
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")

    def test_activated_resume_refuses_a_foreign_live_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary),
                                                      scenario="activated-busy-writers-idle-after-stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not quiescent", result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[-1], "writers-idle-after-stop")
            self.assertNotIn("up-orchestrator", names)
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")

    def test_failed_offline_activation_is_resumable_without_stranding_its_work(self):
        offline_ok = {"provider_activation": {"combined_live_ready": False},
                      "primary_runtime_active": False, "paid_off_primary_intake_active": True}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / ("fixture.env.offline-attempt.unselected." + "c" * 64 + ".json")
            # The runtime starts with intake on and then fails its health proof.
            result, _, final, _ = self._activate(root, action="ActivateOffline", health={
                **offline_ok, "primary_runtime_active": True})
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")
            # Its admitted offline work would fail full quiescence; the retry
            # needs only no foreign live writer.
            result, events, final, _ = self._activate(root, action="ActivateOffline", health=offline_ok,
                                                      scenario="busy-quiescent-before-stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[:2], ["intake-stopped", "writers-idle-after-stop"])
            self.assertNotIn("quiescent-before-stop", names)
            self.assertLess(names.index("evidence-require-ready"), names.index("up-orchestrator"))
            self.assertFalse(marker.exists())
            # A foreign live writer still refuses the retry.
            marker.write_text("director-offline-attempt/v1")
            result, events, _, _ = self._activate(root, action="ActivateOffline", health=offline_ok,
                                                  scenario="busy-writers-idle-after-stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([event["event"] for event in events], ["intake-stopped", "writers-idle-after-stop"])
            # A paid Activate that proves quiescence ends the offline attempt.
            result, _, _, _ = self._activate(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())

    def test_healthy_activated_deployment_is_refused_without_stopping_anything(self):
        # A running ACTIVATED stack is not a failed start: re-running Activate
        # must not stop it mid-task.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture.env").write_text("".join(f"{name}=true\n" for name in SWITCHES)
                                              + "DIRECTOR_PROVIDER_VALIDATION_ENABLED=false\nPRESERVED=fixture-value\n")
            before = (root / "fixture.env").read_bytes()
            result, events, final, _ = self._activate(root, scenario="activated-running")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already ACTIVATED", result.stderr)
            self.assertIn("Nothing was stopped", result.stderr)
            self.assertEqual([event["event"] for event in events],
                             ["evidence-require-activated", "intake-proved-stopped"])
            self.assertEqual((root / "fixture.env").read_bytes(), before)

    @unittest.skipUnless(PWSH, "PowerShell is required")
    def test_quiescence_check_runs_the_metrics_query_and_refuses_clearly(self):
        harness = r"""
param($Source, $Mode, $Fail, $CodePath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'control script parse failed' }
$node = @($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-PrimaryQuiescent'}, $true))
if ($node.Count -ne 1) { throw 'expected one production helper' }
. ([scriptblock]::Create($node[0].Extent.Text))
$script:databaseIdentity = 'storage-fixture'; $script:databaseInstanceId = 'instance-fixture'
function Invoke-Compose { 'ARGS=' + ($args -join ' ')
    if ($CodePath) { [IO.File]::WriteAllText($CodePath, [string]$args[-1]) }
    if ($Fail -eq 'yes') { throw 'Docker Compose command failed.' } }
if ($Mode -eq 'after') { Assert-PrimaryQuiescent -AfterStop }
elseif ($Mode -eq 'writers') { Assert-PrimaryQuiescent -AfterStop -WritersOnly }
else { Assert-PrimaryQuiescent }
'passed'
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "quiescence.ps1"
            path.write_text(harness, encoding="utf-8")

            def run(mode, fail, code_path=""):
                return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(path),
                                       str(ROOT / "scripts/windows-langgraph-primary-control.ps1"), mode, fail,
                                       code_path],
                                      capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)

            before = run("before", "no")
            self.assertEqual(before.returncode, 0, before.stderr)
            self.assertIn("primary_quiescence_metrics", before.stdout)
            self.assertIn("m.rollback_safe", before.stdout)
            self.assertIn("accepted_instance_id='instance-fixture'", before.stdout)
            after = run("after", "no")
            self.assertEqual(after.returncode, 0, after.stderr)
            # Writer leases stay in the post-stop predicate; only the wait differs.
            self.assertIn("m.rollback_safe", after.stdout)
            self.assertIn("end=time.monotonic()+75", after.stdout)
            self.assertIn("end=time.monotonic()+0", before.stdout)
            # Execute the exact generated program against a stub control plane.
            stub = Path(temporary) / "stub" / "director_orchestrator"
            stub.mkdir(parents=True)
            (stub / "__init__.py").write_text("")
            (stub / "config.py").write_text(
                "class Settings:\n    langgraph_primary_postgres_dsn = 'stub'\n"
                "    langgraph_primary_postgres_schema = 'stub'\n"
                "    langgraph_primary_allow_insecure_local_postgres = True\n")
            (stub / "langgraph_postgres.py").write_text(
                "import json, os\nfrom types import SimpleNamespace\n"
                "_rows = json.loads(os.environ['QUIESCENCE_ROWS'])\n"
                "def _metrics():\n    row = _rows.pop(0) if len(_rows) > 1 else _rows[0]\n"
                "    names = ('active_provider_dispatch_count', 'active_assignment_count', 'live_writer_claim_count',"
                " 'unresolved_reconciliation_count', 'unsettled_claim_count')\n"
                "    values = dict(zip(names, row))\n"
                "    return SimpleNamespace(rollback_safe=not any(row), **values)\n"
                "def open_postgres_control_plane(*args, **kwargs):\n"
                "    return SimpleNamespace(claim_store=SimpleNamespace(primary_quiescence_metrics=_metrics),"
                " close=lambda: None)\n")

            def execute(mode, rows):
                code = Path(temporary) / f"code-{mode}.py"
                self.assertEqual(run(mode, "no", str(code)).returncode, 0)
                return subprocess.run([sys.executable, "-c", code.read_text()], capture_output=True, text=True,
                                      timeout=120, stdin=subprocess.DEVNULL,
                                      env={**os.environ, "PYTHONPATH": str(stub.parent),
                                           "QUIESCENCE_ROWS": json.dumps(rows)}).returncode

            lease, clear, lease_and_claim = [0, 0, 1, 0, 0], [0, 0, 0, 0, 0], [0, 0, 1, 0, 1]
            # Before the stop a live lease refuses at once; after it, a lease
            # that lapses within the bound passes, and a lease beside any other
            # unfinished work refuses without waiting.
            self.assertEqual(execute("before", [lease]), 3)
            self.assertEqual(execute("after", [lease, lease, clear]), 0)
            self.assertEqual(execute("after", [lease_and_claim]), 3)
            self.assertEqual(execute("before", [clear]), 0)
            # A same-mode retry tolerates its own unfinished work but waits out,
            # then refuses on, a live writer lease.
            claim_only = [0, 0, 0, 0, 1]
            self.assertEqual(execute("writers", [claim_only]), 0)
            self.assertEqual(execute("writers", [lease_and_claim, claim_only]), 0)
            for mode, state in (("before", "Nothing was stopped"), ("after", "Intake remains stopped")):
                refused = run(mode, "yes")
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("not quiescent", refused.stderr)
                self.assertIn(state, refused.stderr)
                self.assertNotIn("passed", refused.stdout)

    def test_offline_activation_requires_the_offline_handoff_profile_before_anything_stops(self):
        # PrepareOfflineHandoff's profile guard also gates ActivateOffline.
        health = {"provider_activation": {"combined_live_ready": False},
                  "primary_runtime_active": False, "paid_off_primary_intake_active": True}
        cases = {
            "github-v1 acceptance": ("mode-github-v1", None, "provider-v2 generation"),
            "paid runs on": ("success", ("ALLOW_PAID_RUNS", "true"), "explicit false"),
            "validation on": ("success", ("DIRECTOR_PROVIDER_VALIDATION_ENABLED", "true"), "explicit false"),
            "validation unset": ("success", ("DIRECTOR_PROVIDER_VALIDATION_ENABLED", None), "explicit false"),
        }
        for case, (scenario, override, message) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                values = dict.fromkeys(SWITCHES, "false")
                values.update(DIRECTOR_PROVIDER_VALIDATION_ENABLED="false", PRESERVED="fixture-value")
                if override is not None:
                    name, value = override
                    if value is None:
                        values.pop(name)
                    else:
                        values[name] = value
                (root / "fixture.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
                result, events, final, prior = self._activate(root, action="ActivateOffline", health=health,
                                                              scenario=scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(events, [])
                self.assertFalse(prior.exists())
                self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], "false")
                self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")

    def test_offline_activation_needs_no_provider_readiness_and_records_no_activated(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, prior = self._activate(Path(temporary), action="ActivateOffline", health={
                "provider_activation": {"combined_live_ready": False},
                "primary_runtime_active": False, "paid_off_primary_intake_active": True})
            self.assertEqual(result.returncode, 0, result.stderr)
            names = [event["event"] for event in events]
            self.assertNotIn("evidence-record-ACTIVATED", names)
            # Offline activation keeps the ACCEPTED..RESTORE_PROVED gate before any start.
            self.assertEqual(names[:6], ["quiescent-before-stop", "intake-stopped", "quiescent-after-stop",
                                         "inventory", "evidence-bind-legacy-inventory", "evidence-require-ready"])
            self.assertLess(names.index("evidence-require-ready"), names.index("up-orchestrator"))
            self.assertEqual(names[-4:], ["up-slackbot", "readback-slackbot", "up-n8n", "start-slackbot"])
            self.assertEqual(events[-3]["image"], CANDIDATE_IMAGE)
            self.assertEqual(final["ALLOW_PAID_RUNS"], "false")
            self.assertEqual(final["LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"], "false")
            self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], "true")
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "true")
            self.assertEqual(final["LANGGRAPH_PRIMARY_ENABLED"], "true")
            self.assertEqual(final["LANGGRAPH_PARALLEL_KILL_SWITCH"], "true")
            self.assertTrue(prior.exists())
            self.assertIn("no ACTIVATED stage was recorded", result.stdout)

    def test_offline_activation_without_ready_evidence_starts_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, prior = self._activate(Path(temporary), action="ActivateOffline",
                                                          scenario="fail-require-ready")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("evidence require-ready refused", result.stderr)
            self.assertEqual([event["event"] for event in events],
                             ["quiescent-before-stop", "intake-stopped", "quiescent-after-stop",
                              "inventory", "evidence-bind-legacy-inventory", "evidence-require-ready"])
            self.assertFalse(prior.exists())
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")
            self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], "false")

    def test_paid_activation_failed_activated_write_unwinds_with_slack_and_n8n_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary), scenario="fail-record")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unwound", result.stderr)
            names = [event["event"] for event in events]
            self.assertEqual(names[-4:], ["up-slackbot", "readback-slackbot", "evidence-record-ACTIVATED",
                                          "intake-stopped"])
            self.assertNotIn("up-n8n", names)
            self.assertNotIn("start-slackbot", names)
            for name in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
                         "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"):
                self.assertEqual(final[name], "false")

    def test_offline_activation_refuses_a_live_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary), action="ActivateOffline", health={
                "provider_activation": {"combined_live_ready": False}, "primary_runtime_active": True})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unwound", result.stderr)
            self.assertNotIn("up-slackbot", [event["event"] for event in events])
            self.assertEqual(final["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], "false")
            self.assertEqual(final["LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED"], "false")

    def test_activation_gateway_readback_mismatch_closes_all_intake(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, events, final, _ = self._activate(Path(temporary), scenario="wrong-gateway")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("slackbot image differs", result.stderr)
            names = [event["event"] for event in events]
            self.assertNotIn("evidence-record-ACTIVATED", names)
            self.assertEqual(names[-3:], ["up-slackbot", "readback-slackbot", "intake-stopped"])
            self.assertNotIn("up-n8n", names)
            self.assertNotIn("start-slackbot", names)
            self.assertEqual(final["ALLOW_PAID_RUNS"], "false")

    def test_activation_without_prior_containers_proceeds_with_no_record(self):
        # A fresh stack (or one after compose down) has no container to record;
        # a re-run then sees candidate containers, which are not prior images.
        for images in ({}, {"orchestrator": PRIOR_ORCHESTRATOR},
                       {"orchestrator": CANDIDATE_IMAGE, "slackbot": CANDIDATE_IMAGE}):
            with self.subTest(images=images), tempfile.TemporaryDirectory() as temporary:
                result, events, _, prior = self._activate(Path(temporary), images=images)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(prior.exists())
                self.assertEqual([event["event"] for event in events][-2:], ["up-n8n", "start-slackbot"])

    def test_second_activation_keeps_the_first_prior_image_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _, prior = self._activate(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            recorded = prior.read_bytes()
            result, events, _, _ = self._activate(
                root, images={"orchestrator": CANDIDATE_IMAGE, "slackbot": CANDIDATE_IMAGE})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(prior.read_bytes(), recorded)
            self.assertEqual(json.loads(recorded)["gateway_image_id"], PRIOR_GATEWAY)

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
                capture_output=True, text=True, timeout=300,
            )
            self.assertFalse(marker.exists(), "preflight invoked the configured provider")
            self.assertFalse((root / "never-opened.sqlite3").exists())
            self.assertEqual(environment.read_bytes(), before)
            return result

    def test_preflight_accepts_python_supported_lanes_without_dispatch(self):
        # Every supported Python lane is checked individually, including Cursor.
        for lane in sorted(SUPPORTED_HOST_LANES):
            with self.subTest(lane=lane):
                result = self._preflight({lane: ZAI_BRIDGE_ENTRY if lane == "zai" else None})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("no provider was invoked", result.stdout)

    def test_preflight_refuses_a_malformed_zai_entry_before_installation(self):
        malformed = (
            ZAI_BRIDGE_ENTRY[:2],
            [ZAI_BRIDGE_ENTRY[0], "claude-opus-5", *ZAI_BRIDGE_ENTRY[2:]],
            [*ZAI_BRIDGE_ENTRY[:2], "101", *ZAI_BRIDGE_ENTRY[3:]],
            [*ZAI_BRIDGE_ENTRY[:3], "bypass", ZAI_BRIDGE_ENTRY[4]],
            [*ZAI_BRIDGE_ENTRY[:4], "key-id"],
        )
        for argv in malformed:
            with self.subTest(argv=argv[1:]):
                result = self._preflight({"zai": argv})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("zai command must be", result.stderr + result.stdout)

    def test_run_forwards_validation_profile_and_clears_stale_parent_state(self):
        keys = [
            "DIRECTOR_PROVIDER_VALIDATION_ENABLED",
            "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN",
            "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
            "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN",
            "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON",
            "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE",
            "DIRECTOR_PROVIDER_VALIDATION_TREE",
            "DIRECTOR_PROVIDER_VALIDATION_GENERATION",
            "DIRECTOR_PROVIDER_VALIDATION_TEAM_CONFIG",
            "DIRECTOR_PROVIDER_VALIDATION_OPENAI_RESPONSES_CONFIG",
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256",
            "DIRECTOR_HOST_PROVIDER_WORKSPACE",
            "DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES",
            "XAI_API_KEY",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = root / "harmless_provider.py"
            provider.write_text("raise SystemExit(0)\n")
            argv = [sys.executable, str(provider)]
            command_map = {lane: ZAI_BRIDGE_ENTRY if lane == "zai" else argv for lane in SUPPORTED_HOST_LANES}
            capture = root / "child-environment.json"
            restored = root / "restored-environment.json"
            stub = root / "bridge_python.ps1"
            stub.write_text(
                f"""
$ErrorActionPreference = 'Stop'
if ($args -contains '-m' -and $args[$args.IndexOf('-m') + 1] -eq 'director_orchestrator.host_provider_bridge_server') {{
  $payload = @{{}}
  $names = $env:BRIDGE_ENV_CAPTURE_KEYS | ConvertFrom-Json
  foreach ($name in $names) {{
    $payload[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
  }}
  Set-Content -LiteralPath '{capture}' -Value ($payload | ConvertTo-Json -Compress) -Encoding utf8
  $global:LASTEXITCODE = 0
  return
}}
& $env:REAL_PYTHON @args
""",
                encoding="utf-8",
            )
            environment = root / "fixture.env"
            values = {
                "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "offline-fixture-secret-" + "x" * 32,
                "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps(command_map),
                "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE": str(root / "bridge.sqlite3"),
                "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN": "never-connected",
                "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "offline_fixture",
                "DIRECTOR_PROVIDER_VALIDATION_ENABLED": "true",
                "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN": "postgresql://fixture.invalid/validation",
                "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY": "validation-capture-key-" + "y" * 24,
                "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN": "validation-owner-token-" + "z" * 24,
                "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON": json.dumps({"openai": ["account.json"]}),
                "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE": "f" * 40,
                "DIRECTOR_PROVIDER_VALIDATION_TREE": "a" * 40,
                "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "b" * 64,
                "DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES": "XAI_API_KEY",
                "XAI_API_KEY": "profile-xai-fixture",
            }
            environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            parent = dict.fromkeys(keys, "stale-parent-value")
            parent["DIRECTOR_PROVIDER_VALIDATION_TREE"] = "c" * 40
            harness = root / "run.ps1"
            harness.write_text(
                "& $env:BRIDGE_SCRIPT -Action Run -EnvironmentFile $env:BRIDGE_PROFILE "
                "-Workspace $env:BRIDGE_WORKSPACE -Python $env:BRIDGE_STUB\n"
                "$restored = @{}\n"
                "foreach ($name in ($env:BRIDGE_ENV_CAPTURE_KEYS | ConvertFrom-Json)) {\n"
                "  $restored[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')\n}\n"
                f"Set-Content -LiteralPath '{restored}' -Value ($restored | ConvertTo-Json -Compress) -Encoding utf8\n",
                encoding="utf-8",
            )
            for enabled in (True, False):
                profile = values if enabled else {k: v for k, v in values.items() if k not in keys}
                profile = {**profile, "DIRECTOR_PROVIDER_VALIDATION_ENABLED": str(enabled).lower()}
                environment.write_text("".join(f"{key}={value}\n" for key, value in profile.items()))
                result = subprocess.run(
                    [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, **parent, "REAL_PYTHON": sys.executable,
                         "BRIDGE_ENV_CAPTURE_KEYS": json.dumps(keys),
                         "BRIDGE_SCRIPT": str(ROOT / "scripts/windows-host-provider-bridge.ps1"),
                         "BRIDGE_PROFILE": str(environment), "BRIDGE_WORKSPACE": str(root), "BRIDGE_STUB": str(stub)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads(capture.read_text(encoding="utf-8-sig"))
                case = "enabled" if enabled else "disabled"
                _g67b_diag("windows_tempdir_" + case, lambda: _g67b_tempdir(temporary))
                # Emit every key before the first comparison can fail. keys is the
                # literal allowlist above; no environment-derived names are emitted.
                for diagnostic_name in keys:
                    def key_observations(name=diagnostic_name):
                        expected_copy = str(root.resolve()) if name == "DIRECTOR_HOST_PROVIDER_WORKSPACE" else profile.get(name)
                        if name == "XAI_API_KEY" and expected_copy is None:
                            expected_copy = parent[name]
                        actual_copy = observed.get(name) or None
                        payload = {"profile_present": name in profile,
                                   "observed_present": actual_copy is not None,
                                   "equal": actual_copy == expected_copy}
                        if name == "DIRECTOR_HOST_PROVIDER_WORKSPACE":
                            payload.update(equal_raw=actual_copy == expected_copy,
                                           equal_after_canonicalize=Path(actual_copy).resolve() == Path(expected_copy).resolve())
                        return payload
                    _g67b_diag("windows_" + case + "_" + diagnostic_name, key_observations)
                    _g67b_diag("windows_parent_" + case + "_" + diagnostic_name,
                               lambda name=diagnostic_name: {"parent_restoration_equal":
                                   json.loads(restored.read_text(encoding="utf-8-sig")).get(name) == parent[name]})
                for name in keys:
                    expected = str(root.resolve()) if name == "DIRECTOR_HOST_PROVIDER_WORKSPACE" else profile.get(name)
                    if name == "XAI_API_KEY" and expected is None:
                        # An unlisted credential is left for the Python pin to drop.
                        expected = parent[name]
                    self.assertEqual(observed.get(name) or None, expected, name)
                self.assertEqual(json.loads(restored.read_text(encoding="utf-8-sig")), parent)

    def test_preflight_rejects_enabled_validation_without_required_profile_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = root / "harmless_provider.py"
            provider.write_text("raise SystemExit(0)\n")
            argv = [sys.executable, str(provider)]
            environment = root / "fixture.env"
            values = {
                "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "offline-fixture-secret-" + "x" * 32,
                "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps({"cursor": argv}),
                "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE": str(root / "bridge.sqlite3"),
                "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN": "never-connected",
                "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "offline_fixture",
                "DIRECTOR_PROVIDER_VALIDATION_ENABLED": "true",
            }
            environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            result = subprocess.run(
                [PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/windows-host-provider-bridge.ps1"),
                 "-Action", "Preflight", "-EnvironmentFile", str(environment), "-Workspace", str(root),
                 "-Python", sys.executable],
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Provider validation is enabled but incomplete", result.stderr)
            values.update(
                DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN="postgresql://fixture.invalid/validation",
                DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY="capture-" + "c" * 40,
                DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN="owner-" + "o" * 40,
                DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON='{"claude":["account.json"]}',
                DIRECTOR_PROVIDER_VALIDATION_CANDIDATE="a" * 40,
                DIRECTOR_PROVIDER_VALIDATION_TREE="b" * 40,
                LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256="c" * 64,
            )
            malformed = (
                {"DIRECTOR_PROVIDER_VALIDATION_TREE": "wrong"},
                {"DIRECTOR_PROVIDER_VALIDATION_GENERATION": "d" * 64},
                {"DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY": values["DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"]},
                {"DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON": '{"claude":"not-an-array"}'},
                # Only a known lane credential that this profile sets may be forwarded.
                {"DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES": "OPENAI_API_KEY", "OPENAI_API_KEY": "fixture"},
                {"DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES": "XAI_API_KEY"},
            )
            for change in malformed:
                environment.write_text("".join(f"{k}={v}\n" for k, v in {**values, **change}.items()))
                with self.subTest(change=change):
                    result = subprocess.run(
                        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/windows-host-provider-bridge.ps1"),
                         "-Action", "Preflight", "-EnvironmentFile", str(environment), "-Workspace", str(root),
                         "-Python", sys.executable], capture_output=True, text=True, timeout=300,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("preflight passed", result.stdout)

    def test_preflight_preserves_unsupported_and_invalid_command_refusals(self):
        cases = [
            ({"microsoft-365-copilot": None}, "unallowlisted provider"),
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
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 06:12 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 03:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 05:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | END]]
