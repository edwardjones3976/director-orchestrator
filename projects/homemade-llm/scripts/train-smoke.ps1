# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
# M0.3: run train/smoke_train.py inside the pinned Unsloth image.
#   .\train-smoke.ps1                                   default pinned base
#   .\train-smoke.ps1 -Base <hf repo> -Revision <sha>   override (both forwarded into the container)
# Refuses to start when preflight reports a FAIL (C: floor, hot-tier cap, GPU, Docker) or when LM Studio
# cannot release the GPU. Mounts only the four C:\llm folders the job needs: never secrets, seeds or datasets.
[CmdletBinding()]
param(
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$HotRoot = 'C:\llm',
    [string]$Base = 'unsloth/qwen3-0.6b-unsloth-bnb-4bit',
    [string]$Revision = 'e86f5289a2ca40ab7114071959dc2079cf97cb3c',
    [double]$TimeoutMinutes = 30   # whole container: download + training + export (a normal run is ~5 min)
)
$ErrorActionPreference = 'Stop'

& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'preflight.ps1') -HotRoot $HotRoot | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Error 'preflight reported a FAIL; not starting training' }

# Free the 12 GB before training; a failed unload means the GPU may still be shared, so stop.
if (Get-Command lms -ErrorAction SilentlyContinue) {
    lms unload --all 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Error "lms unload --all failed (exit $LASTEXITCODE); not starting training" }
}

$mounts = @()
foreach ($pair in @(@('hf', 'hf'), @('runs', 'runs'), @('models\smoke', 'models/smoke'), @('reports', 'reports'))) {
    $hostDir = Join-Path $HotRoot $pair[0]
    New-Item -ItemType Directory -Force $hostDir | Out-Null
    $mounts += @('-v', "${hostDir}:/workspace/$($pair[1])")
}
$code = (Resolve-Path (Join-Path $PSScriptRoot '..\train')).Path
# A named container in a background job, so a stalled download, CUDA call or export can be stopped:
# on timeout `docker stop` ends it and --rm removes it, releasing the GPU.
$name = 'hl-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$dockerArgs = @('run', '--rm', '--name', $name, '--gpus', 'all', '--shm-size', '4g') + $mounts + @(
    '-v', "${code}:/code:ro", '-e', "SMOKE_BASE=$Base", '-e', "SMOKE_REVISION=$Revision",
    '--entrypoint', 'python', $Image, '/code/smoke_train.py')
$job = Start-Job -ScriptBlock { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $dockerArgs)
if (-not (Wait-Job $job -Timeout ([int]($TimeoutMinutes * 60)))) {
    # Every cleanup step is bounded, including the docker CLI calls themselves (a stalled daemon can hang
    # them): each runs in its own job with a deadline, so the script can never hang past the timeout.
    function Invoke-Bounded([string[]]$A, [int]$Seconds) {
        $j = Start-Job { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $A)
        $r = if (Wait-Job $j -Timeout $Seconds) { @(Receive-Job $j) } else { @('__EXIT__timeout') }
        Remove-Job $j -Force
        $r
    }
    if (@(Invoke-Bounded @('stop', '--time', '20', $name) 40)[-1] -ne '__EXIT__0') { Invoke-Bounded @('kill', $name) 20 | Out-Null }
    if (Wait-Job $job -Timeout 60) { Receive-Job $job | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host }
    Remove-Job $job -Force
    $ps = @(Invoke-Bounded @('ps', '-a', '--filter', "name=^$name$", '--format', '{{.Names}}') 20)
    $state = if ($ps[-1] -ne '__EXIT__0') { 'state UNKNOWN (docker did not answer); check: docker ps -a' }
             elseif ($ps | Where-Object { $_ -eq $name }) { 'could NOT be removed; run: docker rm -f ' + $name }
             else { 'stopped and removed' }
    Write-Error "training exceeded ${TimeoutMinutes} min; container $name $state"
}
$out = @(Receive-Job $job); Remove-Job $job
$out | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
$exitLine = $out | Where-Object { $_ -match '^__EXIT__' } | Select-Object -Last 1
exit $(if ($exitLine) { [int]($exitLine -replace '^__EXIT__', '') } else { 1 })
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
