# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | BEGIN]]
# Homemade LLM dataset secret scan (M0.9): gitleaks over every file in a dataset folder, content only.
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
# gitleaks skips symlinks silently, so anything behind a link inside the tree would scan "clean". Refuse any link
# (file or directory) rather than follow it, since following could also leave the tree.
$link = Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction Stop | Select-Object -First 1
if ($link) { Exit-Scan 2 "scan-dataset: refusing: link inside the dataset (would not be scanned): $($link.FullName)" }
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

# gitleaks also reads .gitleaksignore from its working directory by default (--gitleaks-ignore-path "."), so a caller's
# folder could suppress findings. Point that lookup at a fresh empty directory, and scan gitleaks:allow lines too.
$noIgnore = Join-Path ([IO.Path]::GetTempPath()) ("scan-dataset-noignore-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $noIgnore | Out-Null
# Never let gitleaks discover a config: --config comes first in its lookup, so the dataset's own .gitleaks.*
# (any name or extension its config library would try) is never read. The trusted config is the built-in rules only.
$trustedConfig = Join-Path $noIgnore 'trusted.toml'
Set-Content -LiteralPath $trustedConfig -Value "[extend]`nuseDefault = true" -Encoding utf8
# Scan content, not paths: in `dir` mode gitleaks applies its default global path allowlist (useDefault brings it in),
# so a folder whose name matches an allowlisted pattern (for example "gitleaks.toml-data") is skipped silently.
# Feeding each file through `gitleaks stdin` gives it no path to match; the wrapper keeps the file attribution.
# It runs from the empty scratch folder: stdin mode still reads .gitleaksignore from its working directory.
$all = [System.Collections.Generic.List[object]]::new()
$glCode = 0
try {
    foreach ($f in @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction Stop)) {
        $one = Join-Path $noIgnore 'one.json'
        if (Test-Path -LiteralPath $one) { Remove-Item -LiteralPath $one }
        $gl = @('stdin', '--no-banner', '--redact', '--report-format', 'json', '--report-path', "`"$one`"", '--exit-code', '3',
            '--gitleaks-ignore-path', "`"$noIgnore`"", '--ignore-gitleaks-allow', '--config', "`"$trustedConfig`"")
        $proc = Start-Process -FilePath $Gitleaks -ArgumentList $gl -RedirectStandardInput $f.FullName -WorkingDirectory $noIgnore -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput (Join-Path $noIgnore 'out.txt') -RedirectStandardError (Join-Path $noIgnore 'err.txt')
        if ($proc.ExitCode -eq 3) {
            if ($glCode -eq 0) { $glCode = 3 }
            $rel = $f.FullName.Substring($Path.TrimEnd('\').Length).TrimStart('\')
            foreach ($finding in @(Get-Content -LiteralPath $one -Raw | ConvertFrom-Json)) {
                $finding | Add-Member -NotePropertyName File -NotePropertyValue $rel -Force
                $all.Add($finding)
            }
        } elseif ($proc.ExitCode -ne 0) {
            $glCode = $proc.ExitCode   # a scanner error outranks findings: the scan is incomplete
            Write-Output "scan-dataset: gitleaks exit $($proc.ExitCode) on $($f.FullName)"
            break
        }
    }
} finally { [IO.Directory]::Delete($noIgnore, $true) }
ConvertTo-Json -InputObject @($all) -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding utf8
$count = $all.Count

$summary = "scan-dataset: scanned=$Path findings=$count report=$reportPath"
if ($glCode -eq 0) { Exit-Scan 0 $summary }
if ($glCode -eq 3) { Exit-Scan 1 $summary }
Exit-Scan 2 "$summary; gitleaks exit $glCode"
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | END]]
