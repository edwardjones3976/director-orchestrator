# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
# M2-T1: run train/train_adapter.py inside the pinned Unsloth image.
#   .\train-adapter.ps1 -Rows C:\llm\datasets\director-v1\accepted.jsonl
#       20-step dry run on the smoke base (the M0 pins).
#   .\train-adapter.ps1 -Rows <jsonl> -Winner
#       D-M2-6 probe on Qwen/Qwen3-14B at 40c069824f4251a91eefaf281ebe4c544efd3e18.
# Refuses a short revision, a winner probe pointed at any other base, a hot root
# off C:, a stored Hugging Face token in the mounted cache, and a second GPU job.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Rows,
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$HotRoot = 'C:\llm',
    [string]$Base = 'unsloth/qwen3-0.6b-unsloth-bnb-4bit',
    [string]$Revision = 'e86f5289a2ca40ab7114071959dc2079cf97cb3c',
    [string]$MergeSource = 'unsloth/qwen3-0.6b',
    [string]$MergeRevision = 'db4e90b8359f816f09bf5c944797ce116f26da3c',
    [int]$Steps = 20,
    [switch]$Winner,
    [switch]$Probe,
    [int]$ProbeRowsTotal = 96,
    [int]$ProbeSeqLen = 2048,
    [double]$TimeoutMinutes = 90
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')

$WinnerBase = 'Qwen/Qwen3-14B'
$WinnerRevision = '40c069824f4251a91eefaf281ebe4c544efd3e18'
if ($Winner) {
    foreach ($name in 'Base', 'Revision', 'MergeSource', 'MergeRevision') {
        if ($PSBoundParameters.ContainsKey($name)) { Write-Error "-Winner is pinned to $WinnerBase@$WinnerRevision; do not pass -$name" }
    }
    $Base = $WinnerBase
    $Revision = $WinnerRevision
    $MergeSource = $WinnerBase
    $MergeRevision = $WinnerRevision
}
if ($Steps -lt 1) { Write-Error '-Steps must be positive' }
if ($ProbeRowsTotal -lt 1) { Write-Error '-ProbeRowsTotal must be positive' }
if ($ProbeSeqLen -lt 1) { Write-Error '-ProbeSeqLen must be positive' }
if ($Probe -and -not $Winner) { Write-Error '-Probe requires -Winner (D-M1-F is pinned to the Qwen3-14B winner)' }
foreach ($r in $Revision, $MergeRevision) { if ($r -notmatch '^[0-9a-f]{40}$') { Write-Error "revision '$r' is not a full 40-hex commit" } }
$Rows = [IO.Path]::GetFullPath($Rows)
if ((Split-Path -Leaf $Rows) -ne 'accepted.jsonl') { Write-Error "trainer reads the validator's accepted.jsonl, not raw rows: $Rows" }
if (-not (Test-Path -LiteralPath $Rows -PathType Leaf)) { Write-Error "training rows not found: $Rows" }
if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Rows -Parent) 'report.json') -PathType Leaf)) { Write-Error "validator report.json is missing beside accepted.jsonl" }
if ($Rows -match '\\secrets\\') { Write-Error "refusing to mount a rows path under secrets: $Rows" }

$HotRoot = [IO.Path]::GetFullPath($HotRoot)
if (-not $HotRoot.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { Write-Error "HotRoot must be on C: (preflight gates only C:); got $HotRoot" }
foreach ($sub in '', 'hf', 'runs', 'models', 'models\m2', 'reports') {
    for ($p = Join-Path $HotRoot $sub; $p; $p = Split-Path $p -Parent) {
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "$p is a junction or symbolic link; HotRoot and its mounted folders must be real folders on C:"
        }
    }
}
foreach ($t in 'token', 'stored_tokens') {
    if (Test-Path -LiteralPath (Join-Path $HotRoot "hf\$t")) { Write-Error "$HotRoot\hf\$t exists (a stored Hugging Face credential); move it out of the mounted cache before training" }
}

$gpuLock = [System.Threading.Mutex]::new($false, 'Global\homemade-llm-gpu')
try { $held = $gpuLock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { Write-Error 'another GPU job (train-adapter, train-smoke or serve-smoke) is running; not starting' }
try {
    $poison = $GpuMarker
    if (Test-Path -LiteralPath $poison) { Write-Error "GPU is poisoned: a previous job was not confirmed stopped ($((Get-Content -LiteralPath $poison -Raw).Trim())). Verify it is gone, then delete $poison" }
    New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
    Set-Content -LiteralPath $GpuMarker -Value "train-adapter (pid $PID) GPU job in progress since $((Get-Date).ToUniversalTime().ToString('o'))"
    $markerWritten = $true

    $pf = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'preflight.ps1'), '-HotRoot', $HotRoot) 180)
    $pf | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    if ($pf[-1] -ne '__EXIT__0') { Write-Error "preflight did not pass ($($pf[-1] -replace '^__EXIT__', 'exit ')); not starting training" }
    if (Get-Command lms -ErrorAction SilentlyContinue) {
        $r = @(Invoke-Bounded 'lms' @('unload', '--all') 60)
        $r | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
        if ($r[-1] -ne '__EXIT__0') { Write-Error "lms unload --all failed ($($r[-1] -replace '^__EXIT__', 'exit ')); not starting training" }
    }

    $mounts = @()
    foreach ($pair in @(@('hf', 'hf'), @('runs', 'runs'), @('models\m2', 'models/m2'), @('reports', 'reports'))) {
        $hostDir = Join-Path $HotRoot $pair[0]
        New-Item -ItemType Directory -Force $hostDir | Out-Null
        $mounts += @('-v', "${hostDir}:/workspace/$($pair[1])")
    }
    $rowsDir = Split-Path $Rows -Parent
    $mounts += @('-v', "${rowsDir}:/workspace/rows:ro")
    $code = (Resolve-Path (Join-Path $PSScriptRoot '..\train')).Path
    $name = 'hl-adapter-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $runId = 'm2-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $dockerArgs = @('run', '--rm', '--name', $name, '--gpus', 'all', '--shm-size', '4g') + $mounts + @(
        '-v', "${code}:/code:ro",
        '-e', "ADAPTER_BASE=$Base", '-e', "ADAPTER_REVISION=$Revision", '-e', "ADAPTER_STEPS=$Steps",
        '-e', 'HF_HOME=/workspace/hf', '-e', 'HF_TOKEN_PATH=/nonexistent/hf-token',
        '--entrypoint', 'python', $Image, '/code/train_adapter.py',
        '--rows', ("/workspace/rows/" + (Split-Path $Rows -Leaf)),
        '--base', $Base, '--revision', $Revision, '--steps', "$Steps",
        '--report', "/workspace/reports/$runId.json")
    if ($Winner) { $dockerArgs += '--winner' }
    if ($Probe) {
        $dockerArgs += @('--probe', '--probe-rows-total', "$ProbeRowsTotal", '--probe-seq-len', "$ProbeSeqLen")
    }
    function Get-AdapterState {
        try {
            $ps = @(Invoke-Bounded 'docker' @('ps', '-a', '--filter', "name=^$name$", '--format', '{{.Names}}') 20)
            if ($ps[-1] -ne '__EXIT__0') { 'state UNKNOWN (docker did not answer); check: docker ps -a' }
            elseif ($ps | Where-Object { $_ -eq $name }) { 'could NOT be removed; run: docker rm -f ' + $name }
            else { 'stopped and removed' }
        } catch { "state UNKNOWN (check failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    function Stop-Adapter {
        try {
            if (@(Invoke-Bounded 'docker' @('stop', '--time', '20', $name) 40)[-1] -ne '__EXIT__0') { Invoke-Bounded 'docker' @('kill', $name) 20 | Out-Null }
            if (Wait-Job $job -Timeout 60) { Receive-Job $job | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host }
            Remove-Job $job -Force
            Get-AdapterState
        } catch { "state UNKNOWN (cleanup failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    try { Set-Content -LiteralPath $GpuMarker -Value "container $name launch in flight since $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
    $containerGone = $false
    $job = Start-Job -ScriptBlock { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $dockerArgs)
    $done = $false
    try {
        if (-not (Wait-Job $job -Timeout ([int]($TimeoutMinutes * 60)))) {
            $done = $true
            $state = Stop-Adapter
            $containerGone = $state -eq 'stopped and removed'
            Write-Error "training exceeded ${TimeoutMinutes} min; container $name $state"
        }
        $done = $true
        $state = Get-AdapterState
        $containerGone = $state -eq 'stopped and removed'
        if (-not $containerGone) { Write-Warning "docker run returned but container $name is $state" }
    } finally {
        if (-not $done) { $state = Stop-Adapter; $containerGone = $state -eq 'stopped and removed'; Write-Warning "interrupted; container $name $state" }
    }
    $out = @(Receive-Job $job); Remove-Job $job
    $out | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    $exitLine = $out | Where-Object { $_ -match '^__EXIT__' } | Select-Object -Last 1
    exit $(if ($exitLine) { [int]($exitLine -replace '^__EXIT__', '') } else { 1 })
} finally {
    if ($markerWritten) {
        if ($containerGone -eq $false) {
            try { Set-Content -LiteralPath $GpuMarker -Value "container $name not confirmed stopped at $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
            if (Test-Path -LiteralPath $GpuMarker) { Write-Warning "GPU poisoned: container $name was not confirmed stopped; see $GpuMarker" }
            else { $keepLock = $true }
        } elseif (-not $BoundedUnconfirmed) {
            try { Remove-Item -LiteralPath $GpuMarker -Force } catch { Write-Warning "container is gone but $GpuMarker could not be removed" }
        }
    }
    if (-not $keepLock) { $gpuLock.ReleaseMutex(); $gpuLock.Dispose() }
}
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
