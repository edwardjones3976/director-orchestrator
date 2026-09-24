# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | BEGIN]]
# Archive the Open WebUI data volume into C:\llm\state\open-webui so the nightly mirror (G:\llm-mirror) keeps it.
#   .\backup-openwebui.ps1                 back up (stops open-webui briefly for a consistent SQLite copy)
#   .\backup-openwebui.ps1 -Restore <tgz>  restore an archive into the volume (open-webui must be stopped)
# Uses the already-pulled Open WebUI image for tar, so no extra image is downloaded.
# Tests: test-backup-openwebui.ps1 (scratch volume and container; never touches the live stack).
[CmdletBinding()]
param(
    [string]$Restore,
    [string]$Volume = 'homemade-llm_open-webui-data',
    [string]$Container = 'homemade-llm-open-webui-1',
    [string]$OutDir = 'C:\llm\state\open-webui'
)
$ErrorActionPreference = 'Stop'
$Keep = 14   # newest archives kept; older ones are pruned only after a successful backup
$ArchiveName = '^open-webui-\d{8}-\d{6}\.tgz$'

# Restore, run in a helper container with the volume at /d and the archive dir at /b:
#  0. recover an interrupted earlier restore: if /d/.pre-restore exists, drop whatever is beside it and move
#     the preserved contents back, so the volume is exactly as it was before that run;
#  1. extract into container scratch (/x): a missing or corrupt archive stops here, volume untouched;
#  2. move the current contents aside into /d/.pre-restore (a rename on the same volume, needs no space);
#  3. copy the extracted tree in; on success drop the rollback copy, on failure remove the partial copy
#     and move the previous contents back (exit 3).
# ./cache is not in archives; Open WebUI re-downloads it on start.
$RestoreSh = @'
set -e
rollback() {
    find /d -mindepth 1 -maxdepth 1 ! -name .pre-restore -exec rm -rf {} +
    find /d/.pre-restore -mindepth 1 -maxdepth 1 -exec mv -t /d {} +
    rmdir /d/.pre-restore
}
if [ -d /d/.pre-restore ]; then
    echo "recovering an interrupted restore: putting the previous contents back"
    rollback
fi
mkdir /x
tar xzf "/b/$ARCHIVE" -C /x
mkdir /d/.pre-restore
find /d -mindepth 1 -maxdepth 1 ! -name .pre-restore -exec mv -t /d/.pre-restore {} +
if cp -a /x/. /d/; then
    rm -rf /d/.pre-restore
else
    rollback
    exit 3
fi
'@

# Fail closed: only an explicit, successful 'true' or 'false' counts as knowing the container's state.
function Get-ContainerRunning([string]$Name) {
    $state = docker inspect --format '{{.State.Running}}' $Name 2>$null
    if ($LASTEXITCODE -ne 0 -or $state -notin 'true', 'false') { throw "cannot determine whether $Name is running" }
    $state -eq 'true'
}

# One backup or restore per volume at a time (a scheduled run overlapping a manual one would otherwise
# race on stop/start and on the archive name). Held until the script exits.
$lock = [System.Threading.Mutex]::new($false, "Global\homemade-llm-openwebui-$Volume")
try { $held = $lock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { Write-Error "another backup or restore of $Volume is running; not starting" }

try {
    $image = docker inspect --format '{{.Config.Image}}' $Container 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $image) { Write-Error "container $Container not found; is the stack deployed?" }
    docker volume inspect $Volume *> $null
    if ($LASTEXITCODE -ne 0) { Write-Error "volume $Volume not found" }

    if ($Restore) {
        $full = (Resolve-Path -LiteralPath $Restore).Path
        if (Get-ContainerRunning $Container) { Write-Error "stop $Container before restoring" }
        $dir = Split-Path $full -Parent; $leaf = Split-Path $full -Leaf
        # The name reaches the shell only as a quoted env var, and only in the form this script writes.
        if ($leaf -notmatch $ArchiveName) { Write-Error "unexpected archive name: $leaf" }
        docker run --rm --entrypoint sh -e "ARCHIVE=$leaf" -v "${Volume}:/d" -v "${dir}:/b:ro" $image -c $RestoreSh
        if ($LASTEXITCODE -ne 0) { Write-Error "restore failed (exit $LASTEXITCODE); the previous contents were kept or rolled back" }
        Write-Output "restored $leaf into $Volume; start the stack again"
        exit 0
    }

    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $name = 'open-webui-{0}.tgz' -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $final = Join-Path $OutDir $name
    $part = "$final.part"
    $wasRunning = Get-ContainerRunning $Container
    $restartFailed = $false
    try {
        # A consistent SQLite copy needs the container stopped; if it will not stop, do not back up a live DB.
        if ($wasRunning) {
            docker stop $Container | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Error "could not stop $Container; no backup taken" }
        }
        # ./cache holds re-downloadable embedding/speech models (about 1 GB); chats, users and memory are the rest.
        # Write under .part and rename only on success, so a failed tar never leaves a file that looks like a
        # valid backup (which retention would count among the newest and use to prune real recovery points).
        docker run --rm --entrypoint tar -v "${Volume}:/d:ro" -v "${OutDir}:/b" $image czf "/b/$name.part" --exclude=./cache -C /d .
        $tarExit = $LASTEXITCODE
        if ($tarExit -ne 0) {
            if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }
            Write-Error "backup failed (tar exit $tarExit); partial archive removed"
        }
        Move-Item -LiteralPath $part -Destination $final
    } finally {
        if ($wasRunning) {
            docker start $Container | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $restartFailed = $true
                # Emitted here too, so it is seen even when an earlier error is already propagating.
                Write-Warning "$Container failed to restart; start it manually"
            }
        }
    }
    if ($restartFailed) { Write-Error "backup written but $Container failed to restart; start it manually" }
    Write-Output ("backup: {0} ({1:N1} MB, sha256 {2})" -f $final, ((Get-Item $final).Length / 1MB), (Get-FileHash $final).Hash)

    # Retention: only after a successful backup, and only files this script names, keep the newest $Keep.
    $old = @(Get-ChildItem -LiteralPath $OutDir -File | Where-Object Name -match $ArchiveName |
        Sort-Object Name -Descending | Select-Object -Skip $Keep)
    foreach ($f in $old) { Remove-Item -LiteralPath $f.FullName -Force }
    if ($old.Count) { Write-Output "pruned $($old.Count) archive(s) older than the newest $Keep" }
} finally {
    $lock.ReleaseMutex(); $lock.Dispose()
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | END]]
