# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | BEGIN]]
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
# The data under scan must not be able to configure its own scan. gitleaks auto-loads .gitleaks.toml from the
# scanned directory, honours .gitleaksignore, and skips lines marked gitleaks:allow; any of these in a dataset
# could silence real findings, so refuse the scan instead of trusting it. The config env vars are cleared too.
$suppress = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -Include '.gitleaks.toml', '.gitleaksignore' -ErrorAction Stop)
if ($suppress) { Exit-Scan 2 "scan-dataset: refusing: scanner config inside the dataset: $($suppress[0].FullName)" }
$marked = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction Stop | Select-String -SimpleMatch 'gitleaks:allow' -List | Select-Object -First 1
if ($marked) { Exit-Scan 2 "scan-dataset: refusing: inline gitleaks:allow marker in $($marked.Path)" }
Remove-Item Env:GITLEAKS_CONFIG, Env:GITLEAKS_CONFIG_TOML -ErrorAction SilentlyContinue

$reports = Join-Path $HotRoot 'reports'
if (-not (Test-Path -LiteralPath $reports -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $reports | Out-Null
}
$reportPath = Join-Path $reports ("scan-dataset-{0}.json" -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))

$global:LASTEXITCODE = 0
& $Gitleaks dir $Path --no-banner --redact --report-format json --report-path $reportPath --exit-code 3 2>&1 | Out-Host   # 3 = leaks; gitleaks itself exits 1 on errors
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
if ($glCode -eq 3) { Exit-Scan 1 $summary }
Exit-Scan 2 "$summary; gitleaks exit $glCode"
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | END]]
