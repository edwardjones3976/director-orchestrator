# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | BEGIN]]
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
    # A previous run could not confirm its container was gone and left this marker (see train-smoke.ps1). It is a
    # file, not a held mutex, because .NET mutexes are re-entrant: a mutex kept by that run would let the next run
    # on the same thread straight back in. Delete the marker only after checking `docker ps -a`.
    $poison = $GpuMarker
    if (Test-Path -LiteralPath $poison) { Write-Error "GPU is poisoned: a previous job, container or command was not confirmed stopped ($((Get-Content -LiteralPath $poison -Raw).Trim())). Verify the recorded item is gone (containers: docker ps -a; LM Studio: lms ps and Get-Process lms; others: Get-Process by the recorded pid), then delete $poison" }
    # Durable guard for the whole GPU job, written before any bounded or GPU-relevant command can launch. If this
    # process dies at any point (a crash, a forced kill, or an unhandled throw that abandons the mutex), the marker
    # survives and blocks later runs. It is cleared only when every command (and container) was confirmed stopped.
    # If it cannot be written, nothing is launched.
    New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
    Set-Content -LiteralPath $GpuMarker -Value "train-smoke (pid $PID) GPU job in progress since $((Get-Date).ToUniversalTime().ToString('o'))"
    $markerWritten = $true

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
    # The container's actual state, from the daemon (bounded). Only 'stopped and removed' counts as gone; a docker
    # that does not answer, or a container still listed, keeps the GPU lock.
    function Get-SmokeState {
        try {
            $ps = @(Invoke-Bounded 'docker' @('ps', '-a', '--filter', "name=^$name$", '--format', '{{.Names}}') 20)
            if ($ps[-1] -ne '__EXIT__0') { 'state UNKNOWN (docker did not answer); check: docker ps -a' }
            elseif ($ps | Where-Object { $_ -eq $name }) { 'could NOT be removed; run: docker rm -f ' + $name }
            else { 'stopped and removed' }
        } catch { "state UNKNOWN (check failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    function Stop-Smoke {
      try {
        if (@(Invoke-Bounded 'docker' @('stop', '--time', '20', $name) 40)[-1] -ne '__EXIT__0') { Invoke-Bounded 'docker' @('kill', $name) 20 | Out-Null }
        if (Wait-Job $job -Timeout 60) { Receive-Job $job | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host }
        Remove-Job $job -Force
        Get-SmokeState
      } catch { "state UNKNOWN (cleanup failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    # The job guard already exists; name the container in it before the launch attempt (best effort: the guard stays
    # either way). It is removed only once the daemon confirms the container is gone.
    try { Set-Content -LiteralPath $GpuMarker -Value "container $name launch in flight since $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
    # Unsafe from before the launch attempt: a cancel can land after Start-Job created its process but before it
    # returns, and a Start-Job that throws may still have left a worker that launches Docker later. A single daemon
    # check cannot rule that out, so any Start-Job failure keeps the guard (fail closed) for the operator to check.
    $containerGone = $false
    $job = Start-Job -ScriptBlock { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $dockerArgs)
    $done = $false
    try {
        if (-not (Wait-Job $job -Timeout ([int]($TimeoutMinutes * 60)))) {
            $done = $true   # set first: if cleanup itself throws, finally must not run it a second time
            $state = Stop-Smoke
            $containerGone = $state -eq 'stopped and removed'
            Write-Error "training exceeded ${TimeoutMinutes} min; container $name $state"
        }
        $done = $true
        # The client job ending does not prove the daemon-side container is gone (a transport error can end the
        # client while the container runs on), so confirm absence before the lock can be released.
        $state = Get-SmokeState
        $containerGone = $state -eq 'stopped and removed'
        if (-not $containerGone) { Write-Warning "docker run returned but container $name is $state" }
    } finally {
        # Reached without $done only when the wait was interrupted (Ctrl+C or a stopped pipeline): disconnecting
        # the docker client does not stop the daemon-side container, so stop it here too.
        if (-not $done) { $state = Stop-Smoke; $containerGone = $state -eq 'stopped and removed'; Write-Warning "interrupted; container $name $state" }
    }
    $out = @(Receive-Job $job); Remove-Job $job
    $out | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    $exitLine = $out | Where-Object { $_ -match '^__EXIT__' } | Select-Object -Last 1
    exit $(if ($exitLine) { [int]($exitLine -replace '^__EXIT__', '') } else { 1 })
} finally {
    if ($markerWritten) {
        if ($containerGone -eq $false) {
            # A container may still hold the GPU: the in-flight marker stays, re-labelled; both GPU scripts refuse on it,
            # instead of relying on the (re-entrant) mutex.
            try { Set-Content -LiteralPath $GpuMarker -Value "container $name not confirmed stopped at $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
            if (Test-Path -LiteralPath $GpuMarker) { Write-Warning "GPU poisoned: container $name was not confirmed stopped; see $GpuMarker" }
            else {
                # The marker is somehow gone: the mutex is the only guard left, so keep it (and its handle) until exit.
                $keepLock = $true
                Write-Warning "GPU NOT released: container $name unconfirmed and $GpuMarker is missing; the lock is held until this PowerShell process exits"
            }
        } elseif (-not $BoundedUnconfirmed) {
            # No container was left unconfirmed (the daemon confirmed it gone, or none was launched) and no bounded
            # command was left unconfirmed (Invoke-Bounded appends to the same marker): clear the job guard.
            try { Remove-Item -LiteralPath $GpuMarker -Force } catch { Write-Warning "container $name is gone but $GpuMarker could not be removed; later GPU runs refuse until it is deleted" }
        }
    }
    if ($BoundedGuardMissing) { $keepLock = $true; Write-Warning "GPU NOT released: a bounded command could not be confirmed stopped and $GpuMarker could not be written; the lock is held until this PowerShell process exits" }
    if (-not $keepLock) { $gpuLock.ReleaseMutex(); $gpuLock.Dispose() }
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | END]]
