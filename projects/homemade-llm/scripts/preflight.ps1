# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:09 UTC | BEGIN]]
# Homemade LLM preflight (M0.1). Read-only by default.
#   .\preflight.ps1          probe drives, tools, ports and layout; print a summary; writes nothing
#   .\preflight.ps1 -Report  also write a JSON report to C:\llm\reports\ (never committed)
#   .\preflight.ps1 -Init    create the C:\llm layout, G:\llm-mirror and E:\llm-archive; implies -Report
# Exit 0 = no FAIL rows; 1 = at least one FAIL.
[CmdletBinding()]
param(
    [switch]$Init,
    [switch]$Report,
    [double]$FloorGB = 150,   # refuse jobs when C: free space is below this (plan D-003)
    [double]$HotCapGB = 120,  # C:\llm size cap
    [string]$HotRoot = 'C:\llm'  # overridable so test-preflight.ps1 can use a scratch root
)
$ErrorActionPreference = 'Stop'

$HotDirs = 'hf', 'datasets', 'runs', 'models', 'seeds', 'secrets', 'reports'
$Mirror  = 'G:\llm-mirror'
$Archive = 'E:\llm-archive'
$Ports   = [ordered]@{ 1234 = 'LM Studio'; 3000 = 'Open WebUI'; 8080 = 'orchestrator (SearXNG must not use it)'; 8088 = 'SearXNG' }

$rows = [System.Collections.Generic.List[object]]::new()
function Add-Row([string]$Check, [ValidateSet('OK', 'WARN', 'FAIL', 'INFO')][string]$Status, [string]$Detail) {
    $rows.Add([pscustomobject]@{ check = $Check; status = $Status; detail = $Detail })
}
# Raw bytes against the floor; test-preflight.ps1 loads this function directly to pin the 149.96-vs-150 boundary.
function Test-BelowFloor([double]$FreeBytes, [double]$FloorGB) { $FreeBytes -lt $FloorGB * 1GB }

# Native tools rarely throw; a nonzero exit code discards any partial stdout so it cannot read as OK.
function Invoke-Quiet([scriptblock]$Block) {
    try {
        $global:LASTEXITCODE = 0
        $out = (& $Block 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { $null } else { $out }
    } catch { $null }
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
            $status = if (Test-BelowFloor $v.SizeRemaining $FloorGB) { 'FAIL' } else { 'OK' }
            Add-Row 'drive C (hot)' $status "$detail; floor $FloorGB GB"
        }
        'archive' {
            # exFAT is fine for cold copies but never for git, DVC, or HF caches (D-003).
            # FAT/FAT32 cannot hold files over 4 GB, and the archive receives multi-GB model blobs.
            $status = if ($v.FileSystem -in 'exFAT', 'NTFS') { 'OK' } elseif ($v.FileSystem -match '^FAT') { 'FAIL' } else { 'WARN' }
            Add-Row 'drive E (archive)' $status "$detail; cold archive only$(if ($v.FileSystem -eq 'exFAT') { ' (exFAT: no git/DVC/HF cache)' })"
        }
        default {
            # The mirror must be NTFS (D-003); D: is optional overflow, so a non-NTFS D: only warns.
            $status = if ($v.FileSystem -eq 'NTFS') { 'OK' } elseif ($role -eq 'mirror') { 'FAIL' } else { 'WARN' }
            Add-Row "drive $letter ($role)" $status $detail
        }
    }
}

# Hot-tier size against the cap.
if (Test-Path $HotRoot) {
    $scanErrors = $null
    $bytes = [double](Get-ChildItem $HotRoot -Recurse -File -Force -ErrorAction SilentlyContinue -ErrorVariable scanErrors | Measure-Object Length -Sum).Sum
    $detail = "$([math]::Round($bytes / 1GB, 2)) GB of $HotCapGB GB cap"
    # Fail closed: unreadable entries could hold enough to break the cap, so an incomplete scan is FAIL.
    if ($bytes -gt $HotCapGB * 1GB) { Add-Row 'C:\llm size' 'FAIL' $detail }
    elseif ($scanErrors) { Add-Row 'C:\llm size' 'FAIL' "$detail (incomplete: $($scanErrors.Count) unreadable entries; cap not established)" }
    else { Add-Row 'C:\llm size' 'OK' $detail }
}

# Layout.
# A file sitting where a directory belongs is FAIL: nothing can be created beneath it.
$notDirs = @($HotDirs | Where-Object { Test-Path (Join-Path $HotRoot $_) -PathType Leaf })
$missing = @($HotDirs | Where-Object { -not (Test-Path (Join-Path $HotRoot $_)) })
if ($notDirs.Count) { Add-Row 'layout C:\llm' 'FAIL' "not directories: $($notDirs -join ', ')" }
elseif ($missing.Count) { Add-Row 'layout C:\llm' 'WARN' "missing: $($missing -join ', ') (run -Init)" }
else { Add-Row 'layout C:\llm' 'OK' 'all subdirectories present' }
foreach ($d in $Mirror, $Archive) {
    if (Test-Path $d -PathType Container) { Add-Row "layout $d" 'OK' 'present' }
    elseif (Test-Path $d) { Add-Row "layout $d" 'FAIL' 'exists but is not a directory' }
    else { Add-Row "layout $d" 'WARN' 'missing (run -Init)' }
}

# GPU and tools.
$gpu = Invoke-Quiet { nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader }
Add-Row 'GPU' ($(if ($gpu) { 'OK' } else { 'FAIL' })) ($(if ($gpu) { $gpu } else { 'nvidia-smi unavailable' }))

$dockerVer = Invoke-Quiet { docker version --format '{{.Server.Version}}' }
if ($dockerVer) {
    $runtimes = Invoke-Quiet { docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' }
    if ($runtimes) { Add-Row 'Docker' 'OK' "server $dockerVer; runtimes: $runtimes" }
    else { Add-Row 'Docker' 'FAIL' "server $dockerVer; runtime query (docker info) failed" }
} else { Add-Row 'Docker' 'FAIL' 'docker server not reachable (is Docker Desktop running?)' }

if (Get-Command lms -ErrorAction SilentlyContinue) {
    # lms prints its status line on stderr.
    $global:LASTEXITCODE = 0
    $server = try { (lms server status 2>&1 | Out-String).Trim() } catch { $null }
    $lmsExit = $LASTEXITCODE
    $line = if ($server) { ($server -split "`r?`n")[0] } else { 'installed; server status unknown' }
    # OK only on exit 0 with a positive "running" status; stopped, failed or unreadable is WARN (M0.4 starts the server).
    $ok = ($lmsExit -eq 0) -and ($line -match 'server is running')
    Add-Row 'LM Studio (lms)' ($(if ($ok) { 'OK' } else { 'WARN' })) ($(if ($lmsExit -ne 0) { "$line (exit $lmsExit)" } else { $line }))
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

$reports = Join-Path $HotRoot 'reports'
$writeReport = ($Report -or $Init) -and (Test-Path $reports)
if (($Report -or $Init) -and -not $writeReport) { Add-Row 'report' 'FAIL' "requested but $reports does not exist (run -Init)" }

$rows | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
$fails = @($rows | Where-Object status -eq 'FAIL').Count
Write-Output "preflight: $($rows.Count) checks, $fails FAIL, $(@($rows | Where-Object status -eq 'WARN').Count) WARN"

if ($writeReport) {
    # Millisecond stamp plus a random suffix; CreateNew refuses to overwrite an earlier report.
    $file = Join-Path $reports ("preflight-{0}-{1}.json" -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss-fff'), [guid]::NewGuid().ToString('N').Substring(0, 6))
    $json = [pscustomobject]@{ generated_utc = (Get-Date).ToUniversalTime().ToString('o'); init = [bool]$Init; checks = $rows } | ConvertTo-Json -Depth 4
    $stream = [IO.File]::Open($file, [IO.FileMode]::CreateNew)
    try { $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false)); $writer.Write($json); $writer.Flush() } finally { $stream.Dispose() }
    Write-Output "report: $file"
}
exit ([int]($fails -gt 0))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:09 UTC | END]]
