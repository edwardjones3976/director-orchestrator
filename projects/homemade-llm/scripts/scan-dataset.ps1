# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | BEGIN]]
# Homemade LLM dataset secret scan (M0.9): gitleaks over a dataset folder.
#   .\scan-dataset.ps1                          scan C:\llm\datasets (default)
#   .\scan-dataset.ps1 -Path D:\data -HotRoot C:\llm
# Exit 0 = clean; 1 = findings; 2 = missing tool/path or gitleaks error.
[CmdletBinding()]
param(
    [string]$Path = 'C:\llm\datasets',
    [string]$Gitleaks = 'C:\llm\tools\gitleaks\gitleaks.exe',
    [string]$HotRoot = 'C:\llm'
)
$ErrorActionPreference = 'Stop'

function Exit-Scan([int]$Code, [string]$Message) {
    if ($Message) { Write-Output $Message }
    exit $Code
}

if (-not (Test-Path -LiteralPath $Gitleaks)) {
    Exit-Scan 2 "scan-dataset: gitleaks not found at $Gitleaks (this script never downloads it)"
}
if (-not (Test-Path -LiteralPath $Path)) {
    Exit-Scan 2 "scan-dataset: scan path does not exist: $Path"
}
if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    Exit-Scan 2 "scan-dataset: scan path is not a directory: $Path"
}
if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Exit-Scan 2 "scan-dataset: refusing a junction or symlink scan root: $Path"
}

$reports = Join-Path $HotRoot 'reports'
if (-not (Test-Path -LiteralPath $reports -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $reports | Out-Null
}
$reportPath = Join-Path $reports ("scan-dataset-{0}.json" -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))

$global:LASTEXITCODE = 0
& $Gitleaks dir $Path --no-banner --redact --report-format json --report-path $reportPath --exit-code 1 2>&1 | Out-Host
$glCode = $LASTEXITCODE

$count = 0
if (Test-Path -LiteralPath $reportPath) {
    $raw = (Get-Content -LiteralPath $reportPath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($raw) {
        $parsed = $raw | ConvertFrom-Json
        if ($null -eq $parsed) { $count = 0 }
        elseif ($parsed -is [System.Array]) { $count = $parsed.Count }
        else { $count = 1 }
    }
}

$summary = "scan-dataset: scanned=$Path findings=$count report=$reportPath"
if ($glCode -eq 0) { Exit-Scan 0 $summary }
if ($glCode -eq 1) { Exit-Scan 1 $summary }
Exit-Scan 2 "$summary; gitleaks exit $glCode"
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | END]]
