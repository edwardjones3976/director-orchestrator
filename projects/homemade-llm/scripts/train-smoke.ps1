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
    [string]$Revision = 'e86f5289a2ca40ab7114071959dc2079cf97cb3c'
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
& docker run --rm --gpus all --shm-size 4g @mounts -v "${code}:/code:ro" `
    -e "SMOKE_BASE=$Base" -e "SMOKE_REVISION=$Revision" `
    --entrypoint python $Image /code/smoke_train.py
exit $LASTEXITCODE
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
