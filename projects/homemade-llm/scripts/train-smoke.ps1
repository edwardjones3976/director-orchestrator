# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
# M0.3: run train/smoke_train.py inside the pinned Unsloth image with C:\llm mounted as /workspace.
#   .\train-smoke.ps1
# Refuses to start when preflight reports a FAIL (C: floor, hot-tier cap, GPU, Docker). Frees the GPU
# first with `lms unload --all` so LM Studio and training do not share the 12 GB.
[CmdletBinding()]
param(
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$HotRoot = 'C:\llm'
)
$ErrorActionPreference = 'Stop'

& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'preflight.ps1') -HotRoot $HotRoot | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Error 'preflight reported a FAIL; not starting training' }

if (Get-Command lms -ErrorAction SilentlyContinue) { lms unload --all 2>&1 | Out-Host }

$code = (Resolve-Path (Join-Path $PSScriptRoot '..\train')).Path
& docker run --rm --gpus all --shm-size 4g `
    -v "${HotRoot}:/workspace" -v "${code}:/code:ro" `
    --entrypoint python $Image /code/smoke_train.py
exit $LASTEXITCODE
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
