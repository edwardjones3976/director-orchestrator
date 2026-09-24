# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | BEGIN]]
# Archive the Open WebUI data volume into C:\llm\state\open-webui so the nightly mirror (G:\llm-mirror) keeps it.
#   .\backup-openwebui.ps1                 back up (stops open-webui briefly for a consistent SQLite copy)
#   .\backup-openwebui.ps1 -Restore <tgz>  restore an archive into the volume (open-webui must be stopped)
# Uses the already-pulled Open WebUI image for tar, so no extra image is downloaded.
[CmdletBinding()]
param(
    [string]$Restore,
    [string]$Volume = 'homemade-llm_open-webui-data',
    [string]$Container = 'homemade-llm-open-webui-1',
    [string]$OutDir = 'C:\llm\state\open-webui'
)
$ErrorActionPreference = 'Stop'
$image = docker inspect --format '{{.Config.Image}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or -not $image) { Write-Error "container $Container not found; is the stack deployed?" }
docker volume inspect $Volume *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "volume $Volume not found" }

if ($Restore) {
    $full = (Resolve-Path -LiteralPath $Restore).Path
    if ((docker inspect --format '{{.State.Running}}' $Container) -eq 'true') { Write-Error "stop $Container before restoring" }
    $dir = Split-Path $full -Parent; $leaf = Split-Path $full -Leaf
    # Wipe the volume's contents, then unpack; Open WebUI re-downloads ./cache on next start.
    docker run --rm --entrypoint sh -v "${Volume}:/d" -v "${dir}:/b:ro" $image -c "find /d -mindepth 1 -delete && tar xzf /b/$leaf -C /d"
    if ($LASTEXITCODE -ne 0) { Write-Error 'restore failed' }
    Write-Output "restored $leaf into $Volume; start the stack again"
    exit 0
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$name = 'open-webui-{0}.tgz' -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
$wasRunning = (docker inspect --format '{{.State.Running}}' $Container) -eq 'true'
if ($wasRunning) { docker stop $Container | Out-Null }
try {
    # ./cache holds re-downloadable embedding/speech models (about 1 GB); chats, users and memory are the rest.
    docker run --rm --entrypoint tar -v "${Volume}:/d:ro" -v "${OutDir}:/b" $image czf "/b/$name" --exclude=./cache -C /d .
    if ($LASTEXITCODE -ne 0) { Write-Error 'backup failed' }
} finally {
    if ($wasRunning) { docker start $Container | Out-Null }
}
$file = Join-Path $OutDir $name
Write-Output ("backup: {0} ({1:N1} MB, sha256 {2})" -f $file, ((Get-Item $file).Length / 1MB), (Get-FileHash $file).Hash)
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | END]]
