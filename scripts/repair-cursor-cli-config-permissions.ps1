# Repair ~/.cursor/cli-config.json after MCP retirement (ADMIN-RESOURCES-120H lineage).
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RequestId = 'ADMIN-RESOURCES-120H-20261027'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = 'py'
    $PythonArgs = @('-3.12')
} else {
    $PythonArgs = @()
}

$CursorConfig = Join-Path $env:USERPROFILE '.cursor\cli-config.json'
$McpJson = Join-Path $env:USERPROFILE '.cursor\mcp.json'
$ArtifactRoot = Join-Path $env:USERPROFILE ".codex\artifacts\cursor-cli-config-repair-20261027"
$Receipt = Join-Path $ArtifactRoot 'receipt.json'
$BackupDir = Join-Path $ArtifactRoot 'backups'

if (-not (Test-Path -LiteralPath $McpJson)) {
    throw "Missing mcp.json: $McpJson"
}
if (-not (Test-Path -LiteralPath $CursorConfig)) {
    throw "Missing cli-config.json: $CursorConfig"
}

$running = Get-Process -Name 'cursor-agent' -ErrorAction SilentlyContinue
if ($running) {
    throw 'cursor-agent is running; quit all CLI sessions before applying (avoids overwrite on exit).'
}

New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

$RepairScript = Join-Path $RepoRoot 'scripts\cursor_cli_config_repair.py'
$repairArgs = @(
    $RepairScript,
    '--cli-config', $CursorConfig,
    '--mcp-json', $McpJson,
    '--receipt', $Receipt,
    '--backup-dir', $BackupDir,
    '--apply'
)

Push-Location $RepoRoot
try {
    if ($Python -eq 'py') {
        & $Python @PythonArgs @repairArgs 2>&1
    } else {
        & $Python @repairArgs 2>&1
    }
    if ($LASTEXITCODE -ne 0) { throw "repair failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

# Optional read-only foreman-register probe (requires new cursor-agent session + MCP).
$Agent = Join-Path $env:LOCALAPPDATA 'cursor-agent\cursor-agent.ps1'
$RegisterReceipt = Join-Path $ArtifactRoot 'foreman-register-list.json'
if (Test-Path -LiteralPath $Agent) {
    Write-Host "Next: start a new cursor-agent session, then run a read-only register_list call."
    Write-Host "Record output under: $RegisterReceipt"
} else {
    Write-Host "cursor-agent not installed; skip register_list until CLI is available."
}

Write-Host "receipt=$Receipt request_id=$RequestId"
