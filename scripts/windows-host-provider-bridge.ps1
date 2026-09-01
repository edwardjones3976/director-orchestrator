# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("Preflight", "Run")][string]$Action,
    [Parameter(Mandatory = $true)][string]$EnvironmentFile,
    [Parameter(Mandatory = $true)][string]$Workspace,
    [string]$Python = ".venv-win\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-DotEnvNames {
    param([string]$Path)
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') { $values[$matches[1]] = $matches[2] }
    }
    return $values
}

$environmentPath = (Resolve-Path -LiteralPath $EnvironmentFile).Path
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$values = Read-DotEnvNames -Path $environmentPath
$required = @(
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
    "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON",
    "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE",
    "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN",
    "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA"
)
$missing = @($required | Where-Object {
    -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
    ([string]$values[$_]).StartsWith("REPLACE_WITH_")
})
if ($missing.Count -gt 0) {
    throw "Host bridge configuration is incomplete: $($missing -join ', '). No values were printed."
}
foreach ($otherSecret in @(
    "ORCHESTRATOR_SHARED_SECRET", "SLACK_GATEWAY_SECRET",
    "LANGGRAPH_PRIMARY_OWNER_TOKEN", "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET"
)) {
    if ($values.ContainsKey($otherSecret) -and
        [string]$values[$otherSecret] -ceq [string]$values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]) {
        throw "The host bridge secret must be distinct from $otherSecret."
    }
}

$commands = $values["DIRECTOR_HOST_PROVIDER_COMMANDS_JSON"] | ConvertFrom-Json -AsHashtable
foreach ($unsupported in @("cursor", "windows-copilot")) {
    if ($commands.ContainsKey($unsupported)) {
        throw "$unsupported cannot be enabled through the Windows host bridge."
    }
}
foreach ($provider in $commands.Keys) {
    if ($provider -notin @("claude", "grok", "antigravity", "github-copilot")) {
        throw "The host bridge command map contains an unallowlisted provider."
    }
    $argv = @($commands[$provider])
    if ($argv.Count -lt 1) { throw "The host bridge command map contains an empty command." }
    $executable = Get-Command ([string]$argv[0]) -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $executable) { throw "A configured host bridge executable is unavailable: $provider." }
}

Write-Host "Host bridge preflight passed for $($commands.Count) supported provider command(s); no provider was invoked."
if ($Action -eq "Preflight") { return }

$original = @{}
foreach ($name in $required + @(
    "DIRECTOR_HOST_PROVIDER_WORKSPACE", "DIRECTOR_HOST_PROVIDER_BIND",
    "DIRECTOR_HOST_PROVIDER_PORT", "DIRECTOR_HOST_PROVIDER_TIMEOUT_SECONDS"
)) {
    $original[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($values.ContainsKey($name)) {
        [Environment]::SetEnvironmentVariable($name, [string]$values[$name], "Process")
    }
}
[Environment]::SetEnvironmentVariable("DIRECTOR_HOST_PROVIDER_WORKSPACE", $workspacePath, "Process")
try {
    & $pythonPath -m director_orchestrator.host_provider_bridge_server
    if ($LASTEXITCODE -ne 0) { throw "The Windows host provider bridge exited unsuccessfully." }
}
finally {
    foreach ($entry in $original.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | END]]
