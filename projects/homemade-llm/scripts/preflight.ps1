# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:09 UTC | BEGIN]]
# Homemade LLM preflight (M0.1). Read-only by default.
#   .\preflight.ps1          probe drives, tools, ports and layout; print a summary
#   .\preflight.ps1 -Init    also create the C:\llm layout, G:\llm-mirror and E:\llm-archive
# Exit 0 = no FAIL rows; 1 = at least one FAIL. The JSON report goes to C:\llm\reports\
# when that directory exists and is never committed.
[CmdletBinding()]
param(
    [switch]$Init,
    [double]$FloorGB = 150,   # refuse jobs when C: free space is below this (plan D-003)
    [double]$HotCapGB = 120   # C:\llm size cap
)
$ErrorActionPreference = 'Stop'

$HotRoot = 'C:\llm'
$HotDirs = 'hf', 'datasets', 'runs', 'models', 'seeds', 'secrets', 'reports'
$Mirror  = 'G:\llm-mirror'
$Archive = 'E:\llm-archive'
$Ports   = [ordered]@{ 1234 = 'LM Studio'; 3000 = 'Open WebUI'; 8080 = 'orchestrator (SearXNG must not use it)'; 8088 = 'SearXNG' }

$rows = [System.Collections.Generic.List[object]]::new()
function Add-Row([string]$Check, [ValidateSet('OK', 'WARN', 'FAIL', 'INFO')][string]$Status, [string]$Detail) {
    $rows.Add([pscustomobject]@{ check = $Check; status = $Status; detail = $Detail })
}
function Invoke-Quiet([scriptblock]$Block) {
    try { (& $Block 2>$null | Out-String).Trim() } catch { $null }
}

if ($Init) {
    foreach ($d in $HotDirs) { New-Item -ItemType Directory -Force -Path (Join-Path $HotRoot $d) | Out-Null }
    foreach ($d in $Mirror, $Archive) {
        if (Test-Path (Split-Path $d -Qualifier)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    }
    Add-Row 'init' 'INFO' "created or kept $HotRoot\{$($HotDirs -join ',')}, $Mirror, $Archive (secrets ACL is a separate owner step)"
}

# Drives: C hot tier, G mirror, E cold archive, D optional overflow.
$roles = [ordered]@{ C = 'hot'; G = 'mirror'; E = 'archive'; D = 'overflow' }
foreach ($letter in $roles.Keys) {
    $v = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue
    $role = $roles[$letter]
    if (-not $v) { Add-Row "drive $letter ($role)" ($(if ($letter -eq 'D') { 'WARN' } else { 'FAIL' })) 'not present'; continue }
    # Compare raw bytes; round only for display, so 149.96 GB never passes a 150 GB floor.
    $detail = "$($v.FileSystem), $([math]::Round($v.SizeRemaining / 1GB, 1)) GB free of $([math]::Round($v.Size / 1GB, 1)) GB"
    switch ($role) {
        'hot' {
            $status = if ($v.SizeRemaining -lt $FloorGB * 1GB) { 'FAIL' } else { 'OK' }
            Add-Row 'drive C (hot)' $status "$detail; floor $FloorGB GB"
        }
        'archive' {
            # exFAT is fine for cold copies but never for git, DVC, or HF caches (D-003).
            Add-Row 'drive E (archive)' 'OK' "$detail; cold archive only$(if ($v.FileSystem -eq 'exFAT') { ' (exFAT: no git/DVC/HF cache)' })"
        }
        default {
            $status = if ($v.FileSystem -eq 'NTFS') { 'OK' } else { 'WARN' }
            Add-Row "drive $letter ($role)" $status $detail
        }
    }
}

# Hot-tier size against the cap.
if (Test-Path $HotRoot) {
    $scanErrors = $null
    $bytes = [double](Get-ChildItem $HotRoot -Recurse -File -Force -ErrorAction SilentlyContinue -ErrorVariable scanErrors | Measure-Object Length -Sum).Sum
    $detail = "$([math]::Round($bytes / 1GB, 2)) GB of $HotCapGB GB cap"
    # Over the cap is FAIL even on a partial scan; an incomplete scan under the cap cannot claim OK.
    if ($bytes -gt $HotCapGB * 1GB) { Add-Row 'C:\llm size' 'FAIL' $detail }
    elseif ($scanErrors) { Add-Row 'C:\llm size' 'WARN' "$detail (incomplete: $($scanErrors.Count) unreadable entries)" }
    else { Add-Row 'C:\llm size' 'OK' $detail }
}

# Layout.
$missing = @($HotDirs | Where-Object { -not (Test-Path (Join-Path $HotRoot $_)) })
if ($missing.Count) { Add-Row 'layout C:\llm' 'WARN' "missing: $($missing -join ', ') (run -Init)" }
else { Add-Row 'layout C:\llm' 'OK' 'all subdirectories present' }
foreach ($d in $Mirror, $Archive) {
    Add-Row "layout $d" ($(if (Test-Path $d) { 'OK' } else { 'WARN' })) ($(if (Test-Path $d) { 'present' } else { 'missing (run -Init)' }))
}

# GPU and tools.
$gpu = Invoke-Quiet { nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader }
Add-Row 'GPU' ($(if ($gpu) { 'OK' } else { 'FAIL' })) ($(if ($gpu) { $gpu } else { 'nvidia-smi unavailable' }))

$dockerVer = Invoke-Quiet { docker version --format '{{.Server.Version}}' }
if ($dockerVer) {
    $runtimes = Invoke-Quiet { docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' }
    Add-Row 'Docker' 'OK' "server $dockerVer; runtimes: $runtimes"
} else { Add-Row 'Docker' 'FAIL' 'docker server not reachable (is Docker Desktop running?)' }

if (Get-Command lms -ErrorAction SilentlyContinue) {
    # lms prints its status line on stderr.
    $server = try { (lms server status 2>&1 | Out-String).Trim() } catch { $null }
    $line = if ($server) { ($server -split "`r?`n")[0] } else { 'installed; server status unknown' }
    # OK only on a positive "running" status; stopped or unreadable is WARN (M0.4 starts the server).
    Add-Row 'LM Studio (lms)' ($(if ($line -match 'server is running') { 'OK' } else { 'WARN' })) $line
} else { Add-Row 'LM Studio (lms)' 'FAIL' 'lms not on PATH' }

# wsl.exe writes UTF-16; strip NULs before reading it.
$wsl = Invoke-Quiet { wsl.exe -l -v }
Add-Row 'WSL' 'INFO' ($(if ($wsl) { (($wsl -replace "`0", '') -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Skip 1) -join '; ' } else { 'not available' }))

Add-Row 'Tailscale' ($(if (Get-Command tailscale -ErrorAction SilentlyContinue) { 'OK' } else { 'WARN' })) ($(if (Get-Command tailscale -ErrorAction SilentlyContinue) { 'installed' } else { 'not installed (needed for M0.5)' }))

# Ports.
# Enumerate entries: an int index on an [ordered] dictionary is positional, not a key lookup.
foreach ($entry in $Ports.GetEnumerator()) {
    $p = $entry.Key
    $listen = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listen) {
        $proc = (Get-Process -Id $listen.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Add-Row "port $p" 'INFO' "in use by $proc on $($listen.LocalAddress) (expected: $($entry.Value))"
    } else { Add-Row "port $p" 'INFO' "free (planned: $($entry.Value))" }
}

$rows | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
$fails = @($rows | Where-Object status -eq 'FAIL').Count
Write-Output "preflight: $($rows.Count) checks, $fails FAIL, $(@($rows | Where-Object status -eq 'WARN').Count) WARN"

$reports = Join-Path $HotRoot 'reports'
if (Test-Path $reports) {
    $file = Join-Path $reports ("preflight-{0}.json" -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'))
    [pscustomobject]@{ generated_utc = (Get-Date).ToUniversalTime().ToString('o'); init = [bool]$Init; checks = $rows } |
        ConvertTo-Json -Depth 4 | Set-Content -Path $file -Encoding utf8
    Write-Output "report: $file"
}
exit ([int]($fails -gt 0))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:09 UTC | END]]
