# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:05 UTC | BEGIN]]
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

# Restore, run in a helper container with the volume at /d and the archive dir at /b. A one-word journal,
# /d/.restore-state, says what the entries beside /d/.pre-restore are, so recovery never deletes originals:
#   moving  - originals may be split between /d and .pre-restore: everything beside it is original data;
#             recovery only moves .pre-restore's contents back and deletes nothing.
#   copying - every original is inside .pre-restore: what is beside it is the new, possibly partial, copy;
#             recovery deletes that copy, then switches to 'moving' before moving the originals back.
#   done    - the copy completed: what is beside .pre-restore is the full new copy and .pre-restore is only
#             the rollback copy being dropped; recovery finishes dropping it and keeps the new copy.
# Guarantee: recovery never loses the originals and never deletes a copy journaled 'done'. A crash between
# cp finishing and 'journal done' is recovered as 'copying' (rolled back to the originals); the archive is
# untouched, so re-running the restore completes it.
# No journal but a .pre-restore (left by an older version of this script): which tree is which cannot be
# told, and same-named entries on both sides would collide, so nothing is touched and the restore exits 4
# for manual resolution. (This version always journals 'moving' before creating .pre-restore.)
# Backups are refused while any of this bookkeeping is present: the volume root may hold a partial tree.
# Steps: recover any interrupted run; extract into container scratch (/x), so a missing or corrupt archive
# stops before the volume changes; move the current contents aside; copy in; drop the rollback copy.
# ./cache, .pre-restore and .restore-state are never in archives; Open WebUI re-downloads ./cache on start.
$RestoreSh = @'
set -e
journal() { printf '%s' "$1" > /d/.restore-state.tmp && mv -f /d/.restore-state.tmp /d/.restore-state; }
undo_move() {
    find /d/.pre-restore -mindepth 1 -maxdepth 1 -exec mv -t /d {} +
    rmdir /d/.pre-restore
    rm -f /d/.restore-state
}
undo_copy() {
    find /d -mindepth 1 -maxdepth 1 ! -name .pre-restore ! -name '.restore-state*' -exec rm -rf {} +
    journal moving
    undo_move
}
if [ -d /d/.pre-restore ]; then
    echo "recovering an interrupted restore: putting the previous contents back"
    case "$(cat /d/.restore-state 2>/dev/null)" in
        copying) undo_copy ;;
        done) echo "(the previous restore had completed; finishing its cleanup)"; rm -rf /d/.pre-restore ;;
        moving) undo_move ;;
        *) echo "unjournaled /d/.pre-restore from an older version: resolve it by hand (nothing changed)" >&2; exit 4 ;;
    esac
fi
rm -f /d/.restore-state /d/.restore-state.tmp
mkdir /x
tar xzf "/b/$ARCHIVE" -C /x
# An archive carrying its own bookkeeping could overwrite the live journal mid-copy: refuse it untouched.
if find /x -mindepth 1 -maxdepth 1 \( -name .pre-restore -o -name '.restore-state*' \) | grep -q .; then
    echo "archive contains restore bookkeeping (.pre-restore or .restore-state*); refused, volume unchanged" >&2; exit 5
fi
journal moving
mkdir /d/.pre-restore
find /d -mindepth 1 -maxdepth 1 ! -name .pre-restore ! -name '.restore-state*' -exec mv -t /d/.pre-restore {} +
journal copying
if cp -a /x/. /d/; then
    journal done
    rm -rf /d/.pre-restore
    rm -f /d/.restore-state
else
    undo_copy
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
    # An interrupted restore leaves the volume root possibly partial (the originals sit in .pre-restore, which
    # archives exclude): refuse rather than archive it and let retention prune good recovery points.
    # Fail closed: only an explicit 'clean' from the probe counts.
    $probe = docker run --rm --entrypoint sh -v "${Volume}:/d:ro" $image -c 'if [ -e /d/.pre-restore ] || [ -e /d/.restore-state ]; then echo pending; else echo clean; fi' 2>$null
    if ($LASTEXITCODE -ne 0 -or $probe -ne 'clean') {
        Write-Error "an interrupted restore is pending in $Volume (or its state could not be read); no backup taken. Run -Restore <archive> with $Container stopped to recover it first."
    }
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
        docker run --rm --entrypoint tar -v "${Volume}:/d:ro" -v "${OutDir}:/b" $image czf "/b/$name.part" --exclude=./cache --exclude=./.pre-restore --exclude='./.restore-state*' -C /d .
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
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:05 UTC | END]]
