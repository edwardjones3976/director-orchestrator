# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
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
if ([string]$values["DIRECTOR_HOST_PROVIDER_BIND"] -eq "0.0.0.0") {
    $required += @(
        "DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE",
        "DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE"
    )
}
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
    "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET"
)) {
    if ($values.ContainsKey($otherSecret) -and
        [string]$values[$otherSecret] -ceq [string]$values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]) {
        throw "The host bridge secret must be distinct from $otherSecret."
    }
}

$commands = $values["DIRECTOR_HOST_PROVIDER_COMMANDS_JSON"] | ConvertFrom-Json -AsHashtable
foreach ($provider in $commands.Keys) {
    if ($provider -notin @("openai", "claude", "cursor", "grok", "antigravity", "github-copilot")) {
        throw "The host bridge command map contains an unallowlisted provider."
    }
    $argv = @($commands[$provider])
    if ($argv.Count -lt 1) { throw "The host bridge command map contains an empty command." }
    $executable = Get-Command ([string]$argv[0]) -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $executable) { throw "A configured host bridge executable is unavailable: $provider." }
}

$validationEnabled = [string]$values["DIRECTOR_PROVIDER_VALIDATION_ENABLED"] -eq "true"
$validationNames = @(
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
    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"
)
if ($validationEnabled) {
    $validationRequired = @(
        "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN",
        "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
        "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN",
        "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON",
        "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE",
        "DIRECTOR_PROVIDER_VALIDATION_TREE"
    )
    $validationMissing = @($validationRequired | Where-Object {
        -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
        ([string]$values[$_]).StartsWith("REPLACE_WITH_")
    })
    if ($validationMissing.Count -gt 0) {
        throw "Provider validation is enabled but incomplete: $($validationMissing -join ', '). No values were printed."
    }
    foreach ($validationSecret in @(
        "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
        "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"
    )) {
        if ([string]$values[$validationSecret] -ceq [string]$values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]) {
            throw "Provider validation keys must be distinct from the host bridge secret."
        }
    }
}

Write-Host "Host bridge preflight passed for $($commands.Count) supported provider command(s); no provider was invoked."
if ($Action -eq "Preflight") { return }

$optional = @(
    "DIRECTOR_HOST_PROVIDER_WORKSPACE", "DIRECTOR_HOST_PROVIDER_BIND",
    "DIRECTOR_HOST_PROVIDER_PORT", "DIRECTOR_HOST_PROVIDER_TIMEOUT_SECONDS",
    "DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE", "DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE"
) + $validationNames
$original = @{}
foreach ($name in ($required + $optional | Select-Object -Unique)) {
    $original[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
foreach ($name in $validationNames) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
foreach ($name in $required + $optional) {
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
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:05 UTC | END]]
