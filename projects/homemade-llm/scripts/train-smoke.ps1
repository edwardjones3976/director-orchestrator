# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
# M0.3: run train/smoke_train.py inside the pinned Unsloth image.
#   .\train-smoke.ps1                                   default pinned base
#   .\train-smoke.ps1 -Base <hf repo> -Revision <sha> -MergeSource <16-bit hf repo> -MergeRevision <sha>
#                                                       override: a base comes with its own merge-source pin
# Refuses to start when preflight reports a FAIL (C: floor, hot-tier cap, GPU, Docker) or when LM Studio
# cannot release the GPU. Mounts only the four C:\llm folders the job needs: never secrets, seeds or datasets.
[CmdletBinding()]
param(
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$HotRoot = 'C:\llm',
    [string]$Base = 'unsloth/qwen3-0.6b-unsloth-bnb-4bit',
    [string]$Revision = 'e86f5289a2ca40ab7114071959dc2079cf97cb3c',
    [string]$MergeSource = 'unsloth/qwen3-0.6b',                          # full-precision repo the GGUF merge uses
    [string]$MergeRevision = 'db4e90b8359f816f09bf5c944797ce116f26da3c',
    [double]$TimeoutMinutes = 30   # whole container: download + training + export (a normal run is ~5 min)
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')   # Invoke-Bounded: deadline + process-tree kill

# The merge source belongs to the base snapshot: overriding the base or its revision without the merge pin would record (or fail on) the default
# Qwen pin. Revisions must be full commits, so every input the GGUF is built from is immutable.
if (($PSBoundParameters.ContainsKey('Base') -or $PSBoundParameters.ContainsKey('Revision')) -and -not ($PSBoundParameters.ContainsKey('MergeSource') -and $PSBoundParameters.ContainsKey('MergeRevision'))) {
    Write-Error '-Base or -Revision needs a matching -MergeSource and -MergeRevision (the full-precision repo its GGUF merge uses)'
}
foreach ($r in $Revision, $MergeRevision) { if ($r -notmatch '^[0-9a-f]{40}$') { Write-Error "revision '$r' is not a full 40-hex commit" } }

# preflight's free-space floor and hot-tier cap gate drive C only, so the hot root must live there.
$HotRoot = [IO.Path]::GetFullPath($HotRoot)
if (-not $HotRoot.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { Write-Error "HotRoot must be on C: (preflight gates only C:); got $HotRoot" }
# The path string says C:, but a junction or symlink on it, on a mounted folder or anywhere inside one could
# land on another drive that preflight never gates; refuse rather than trying to resolve them.
foreach ($sub in '', 'hf', 'runs', 'models', 'models\smoke', 'reports') {
    for ($p = Join-Path $HotRoot $sub; $p; $p = Split-Path $p -Parent) {
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "$p is a junction or symbolic link; HotRoot and its mounted folders must be real folders on C:"
        }
    }
    # Inside a mount only native Windows links redirect host writes. The Linux symlinks the HF cache creates
    # through the bind mount are also reparse points, but have no LinkType and resolve inside the container.
    $dir = Join-Path $HotRoot $sub
    if ($sub -and (Test-Path -LiteralPath $dir)) {
        $link = Get-ChildItem -LiteralPath $dir -Recurse -Force -Attributes ReparsePoint -ErrorAction Stop |
            Where-Object { $_.LinkType -in 'Junction', 'SymbolicLink' } | Select-Object -First 1
        if ($link) { Write-Error "$($link.FullName) is a $($link.LinkType) inside a mounted folder; it could redirect writes off C:" }
    }
}
# The container's HF_HOME is the mounted <HotRoot>\hf, which is also Hugging Face's default token location:
# refuse to hand a stored credential to the network-enabled third-party image.
foreach ($t in 'token', 'stored_tokens') {
    if (Test-Path -LiteralPath (Join-Path $HotRoot "hf\$t")) { Write-Error "$HotRoot\hf\$t exists (a stored Hugging Face credential); move it out of the mounted cache before training" }
}

# One GPU job at a time, shared with serve-smoke.ps1, held from before the LM Studio unload until this process
# exits (after container teardown), so no serve probe or second training run can start on the same GPU.
$gpuLock = [System.Threading.Mutex]::new($false, 'Global\homemade-llm-gpu')
try { $held = $gpuLock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { Write-Error 'another GPU job (train-smoke or serve-smoke) is running; not starting' }
# Everything below holds the GPU lock; finally releases it on every exit (error, Ctrl+C, exit), which matters
# when this script runs inside a longer-lived PowerShell session rather than its own process.
try {

    # Bounded, because the GPU lock is already held: a stalled docker or lms probe inside preflight must not keep it.
    $pf = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'preflight.ps1'), '-HotRoot', $HotRoot) 180)
    $pf | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    if ($pf[-1] -ne '__EXIT__0') { Write-Error "preflight did not pass ($($pf[-1] -replace '^__EXIT__', 'exit ')); not starting training" }

    # Free the 12 GB before training; a failed unload means the GPU may still be shared, so stop.
    # Bounded: a stalled LM Studio daemon must not hang this run while it holds the GPU lock.
    if (Get-Command lms -ErrorAction SilentlyContinue) {
        $r = @(Invoke-Bounded 'lms' @('unload', '--all') 60)
        $r | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
        if ($r[-1] -ne '__EXIT__0') { Write-Error "lms unload --all failed ($($r[-1] -replace '^__EXIT__', 'exit ')); not starting training" }
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
        '-e', "SMOKE_MERGE_SOURCE=$MergeSource", '-e', "SMOKE_MERGE_REVISION=$MergeRevision",
        '-e', 'HF_TOKEN_PATH=/nonexistent/hf-token', '-e', 'HF_STORED_TOKENS_PATH=/nonexistent/hf-stored-tokens',
        '--entrypoint', 'python', $Image, '/code/smoke_train.py')
    # Every cleanup step is bounded, including the docker CLI calls themselves (a stalled daemon can hang them).
    # Stop-Smoke returns the container's final state.
    function Stop-Smoke {
        if (@(Invoke-Bounded 'docker' @('stop', '--time', '20', $name) 40)[-1] -ne '__EXIT__0') { Invoke-Bounded 'docker' @('kill', $name) 20 | Out-Null }
        if (Wait-Job $job -Timeout 60) { Receive-Job $job | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host }
        Remove-Job $job -Force
        $ps = @(Invoke-Bounded 'docker' @('ps', '-a', '--filter', "name=^$name$", '--format', '{{.Names}}') 20)
        if ($ps[-1] -ne '__EXIT__0') { 'state UNKNOWN (docker did not answer); check: docker ps -a' }
        elseif ($ps | Where-Object { $_ -eq $name }) { 'could NOT be removed; run: docker rm -f ' + $name }
        else { 'stopped and removed' }
    }
    $job = Start-Job -ScriptBlock { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $dockerArgs)
    $done = $false
    try {
        if (-not (Wait-Job $job -Timeout ([int]($TimeoutMinutes * 60)))) {
            $done = $true   # set first: if cleanup itself throws, finally must not run it a second time
            $state = Stop-Smoke
            Write-Error "training exceeded ${TimeoutMinutes} min; container $name $state"
        }
        $done = $true
    } finally {
        # Reached without $done only when the wait was interrupted (Ctrl+C or a stopped pipeline): disconnecting
        # the docker client does not stop the daemon-side container, so stop it here too.
        if (-not $done) { Write-Warning "interrupted; container $name $(Stop-Smoke)" }
    }
    $out = @(Receive-Job $job); Remove-Job $job
    $out | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    $exitLine = $out | Where-Object { $_ -match '^__EXIT__' } | Select-Object -Last 1
    exit $(if ($exitLine) { [int]($exitLine -replace '^__EXIT__', '') } else { 1 })
} finally {
    $gpuLock.ReleaseMutex(); $gpuLock.Dispose()
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
