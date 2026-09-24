# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:58 UTC | BEGIN]]
# Behavioural tests for backup-openwebui.ps1 on a scratch volume and scratch containers; the live stack is
# never stopped or touched. Needs Docker and the Open WebUI image (already pulled for the stack).
#   pwsh -File projects\homemade-llm\scripts\test-backup-openwebui.ps1 [-Image <open-webui image>]
[CmdletBinding()]
param([string]$Image = (docker inspect --format '{{.Config.Image}}' homemade-llm-open-webui-1 2>$null))
$ErrorActionPreference = 'Stop'
if (-not $Image) { Write-Error 'pass -Image (the Open WebUI image); the live container was not found to read it from' }
$script = Join-Path $PSScriptRoot 'backup-openwebui.ps1'
$id = [guid]::NewGuid().ToString('N').Substring(0, 8)
$vol = "hl-bktest-$id"; $ctr = "hl-bktest-$id"; $busy = "hl-bktest-busy-$id"
$work = Join-Path ([IO.Path]::GetTempPath()) "bktest-$id"; $out = Join-Path $work 'out'
$failures = [System.Collections.Generic.List[string]]::new()
function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}
function Invoke-Backup([string[]]$Arguments) { & pwsh -NoProfile -File $script @Arguments *> $null; $LASTEXITCODE }
function In-Volume([string]$Cmd) { (docker run --rm --entrypoint sh -v "${vol}:/d" $Image -c $Cmd | Out-String).Trim() }
function Seed([string]$Sentinel) { In-Volume "find /d -mindepth 1 -delete; echo $Sentinel > /d/sentinel.txt; mkdir -p /d/cache; echo big > /d/cache/model.bin" | Out-Null }

New-Item -ItemType Directory -Force $out | Out-Null
docker volume create $vol | Out-Null
docker create --name $ctr -v "${vol}:/app/backend/data" $Image | Out-Null   # stopped: stands in for Open WebUI
$common = @('-Volume', $vol, '-Container', $ctr, '-OutDir', $out)
try {
    # Backup: one archive, no .part, cache excluded, stopped container left stopped.
    Seed 'v1'
    Assert-Equal (Invoke-Backup $common) 0 'backup: exits 0'
    $arch = @(Get-ChildItem $out -Filter 'open-webui-*.tgz')
    Assert-Equal $arch.Count 1 'backup: exactly one archive'
    Assert-Equal @(Get-ChildItem $out -Filter '*.part').Count 0 'backup: no .part left'
    $listing = (docker run --rm --entrypoint tar -v "${out}:/b:ro" $Image tzf "/b/$($arch[0].Name)" | Out-String)
    Assert-Equal ([bool]($listing -match 'sentinel\.txt')) $true 'backup: data included'
    Assert-Equal ([bool]($listing -match 'cache/')) $false 'backup: ./cache excluded'
    Assert-Equal (docker inspect --format '{{.State.Running}}' $ctr) 'false' 'backup: stopped container not started'
    $good = $arch[0].FullName

    # Restore: replaces contents with the archive, leaves no .pre-restore.
    Seed 'v2'
    Assert-Equal (Invoke-Backup ($common + @('-Restore', $good))) 0 'restore: exits 0'
    Assert-Equal (In-Volume 'cat /d/sentinel.txt') 'v1' 'restore: archive contents restored'
    Assert-Equal (In-Volume 'test -e /d/.pre-restore && echo yes || echo no') 'no' 'restore: no .pre-restore left'

    # Corrupt archive: refused after extraction fails; volume untouched.
    Seed 'v3'
    $bad = Join-Path $out 'open-webui-20000101-000000.tgz'; Set-Content $bad 'not gzip' -NoNewline
    Assert-Equal ((Invoke-Backup ($common + @('-Restore', $bad))) -ne 0) $true 'restore: corrupt archive fails'
    Assert-Equal (In-Volume 'cat /d/sentinel.txt') 'v3' 'restore: corrupt archive leaves data intact'
    Remove-Item $bad

    # Name not in the script's own format: refused before docker runs.
    $evil = Join-Path $out 'x;touch pwned.tgz'; Copy-Item $good $evil
    Assert-Equal ((Invoke-Backup ($common + @('-Restore', $evil))) -ne 0) $true 'restore: unexpected archive name refused'
    Assert-Equal (In-Volume 'cat /d/sentinel.txt') 'v3' 'restore: refused name leaves data intact'
    Remove-Item -LiteralPath $evil

    # Interrupted earlier restores. Each is recovered first, even when the new archive then turns out corrupt,
    # so the check is purely the recovery. 'list' prints every entry, so nothing lost goes unnoticed.
    Set-Content $bad 'not gzip' -NoNewline
    $list = 'cd /d && find . -mindepth 1 | sort | tr "\n" " "'
    # (a) Interrupted mid-copy: every original inside .pre-restore, a partial new copy beside it.
    In-Volume 'find /d -mindepth 1 -delete; mkdir /d/.pre-restore; echo original > /d/.pre-restore/sentinel.txt; echo partial > /d/half.txt; printf copying > /d/.restore-state' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './sentinel.txt' 'interrupted mid-copy: originals back, partial copy removed, journal cleared'
    # (b) Interrupted mid-move: originals split between /d and .pre-restore. Nothing may be deleted.
    In-Volume 'find /d -mindepth 1 -delete; mkdir /d/.pre-restore; echo a > /d/.pre-restore/moved.txt; echo b > /d/unmoved.txt; mkdir /d/sub; echo c > /d/sub/deep.txt; printf moving > /d/.restore-state' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './moved.txt ./sub ./sub/deep.txt ./unmoved.txt' 'interrupted mid-move: every original kept, none deleted'
    # (c) Created .pre-restore but moved nothing yet (the window the reviewer found).
    In-Volume 'find /d -mindepth 1 -delete; echo only > /d/sentinel.txt; printf moving > /d/.restore-state; mkdir /d/.pre-restore' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './sentinel.txt' 'interrupted before any move: original kept'
    # (d) Left by an older version of this script (no journal): treated as mid-move, so nothing is deleted.
    In-Volume 'find /d -mindepth 1 -delete; mkdir /d/.pre-restore; echo a > /d/.pre-restore/x.txt; echo b > /d/y.txt' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './x.txt ./y.txt' 'legacy leftover without journal: nothing deleted'
    # (e) Interrupted during rollback after the journal switched back to moving: finish moving, delete nothing.
    In-Volume 'find /d -mindepth 1 -delete; mkdir /d/.pre-restore; echo back > /d/already-back.txt; echo in > /d/.pre-restore/still-in.txt; printf moving > /d/.restore-state' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './already-back.txt ./still-in.txt' 'interrupted mid-rollback: all originals kept'
    # (f) Interrupted while dropping the rollback copy after a completed copy: keep the new copy, finish cleanup.
    In-Volume 'find /d -mindepth 1 -delete; mkdir -p /d/.pre-restore/half-gone; echo old > /d/.pre-restore/half-gone/x.txt; echo new > /d/restored.txt; printf done > /d/.restore-state' | Out-Null
    Invoke-Backup ($common + @('-Restore', $bad)) | Out-Null
    Assert-Equal (In-Volume $list) './restored.txt' 'interrupted during cleanup after a completed copy: new copy kept'
    Remove-Item $bad
    # Backups never capture restore bookkeeping.
    In-Volume 'find /d -mindepth 1 -delete; echo v > /d/sentinel.txt; mkdir /d/.pre-restore; printf moving > /d/.restore-state' | Out-Null
    $before = @(Get-ChildItem $out -Filter 'open-webui-*.tgz').Name
    Invoke-Backup $common | Out-Null
    $new = Get-ChildItem $out -Filter 'open-webui-*.tgz' | Where-Object Name -notin $before | Select-Object -First 1
    $newList = (docker run --rm --entrypoint tar -v "${out}:/b:ro" $Image tzf "/b/$($new.Name)" | Out-String)
    Assert-Equal ([bool]($newList -match 'pre-restore|restore-state')) $false 'backup: restore bookkeeping excluded'
    In-Volume 'rm -rf /d/.pre-restore /d/.restore-state' | Out-Null
    Remove-Item $new.FullName

    # Fail closed on unreadable state: load Get-ContainerRunning and shadow docker with a fake.
    $tokens0 = $null; $errs0 = $null
    $ast0 = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens0, [ref]$errs0)
    . ([scriptblock]::Create(($ast0.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ContainerRunning' }, $true) | Select-Object -First 1).Extent.Text))
    $threw = { try { Get-ContainerRunning 'x' | Out-Null; $false } catch { $true } }
    function docker { $global:LASTEXITCODE = 1; '' }
    Assert-Equal (& $threw) $true 'state: failed inspect throws (not "stopped")'
    function docker { $global:LASTEXITCODE = 0; 'maybe' }
    Assert-Equal (& $threw) $true 'state: unexpected output throws'
    function docker { $global:LASTEXITCODE = 0; 'false' }
    Assert-Equal (Get-ContainerRunning 'x') $false 'state: explicit false is stopped'
    function docker { $global:LASTEXITCODE = 0; 'true' }
    Assert-Equal (Get-ContainerRunning 'x') $true 'state: explicit true is running'
    Remove-Item function:docker

    # Copy fails mid-restore (ENOSPC on a 20 MB tmpfs, 50 MB archive): previous contents rolled back, exit 3.
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errs)
    $restoreSh = ($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$RestoreSh' }, $true) | Select-Object -First 1).Right.Expression.Value
    $big = 'open-webui-20000101-000001.tgz'
    docker run --rm --entrypoint sh -v "${out}:/b" $Image -c "mkdir -p /s && head -c 52428800 /dev/urandom > /s/big.bin && tar czf /b/$big -C /s ." | Out-Null
    $probe = docker run --rm --entrypoint sh --mount 'type=tmpfs,destination=/d,tmpfs-size=20m' -e "ARCHIVE=$big" -v "${out}:/b:ro" $Image -c ("echo keep > /d/sentinel.txt; ( " + $restoreSh + " ); echo rc=`$?; ls -A /d; cat /d/sentinel.txt") 2>$null | Out-String
    Assert-Equal ([bool]($probe -match 'rc=3')) $true 'copy failure: exits 3'
    Assert-Equal ([bool]($probe -match 'keep' -and $probe -notmatch 'big\.bin' -and $probe -notmatch 'pre-restore')) $true 'copy failure: only the original data remains'
    Remove-Item (Join-Path $out $big)

    # Restore into a running container is refused (a sleeping helper stands in for a live Open WebUI).
    docker run -d --name $busy --entrypoint sleep -v "${vol}:/app/backend/data" $Image 300 | Out-Null
    Assert-Equal ((Invoke-Backup @('-Volume', $vol, '-Container', $busy, '-OutDir', $out, '-Restore', $good)) -ne 0) $true 'restore: running container refused'
    docker rm -f $busy | Out-Null

    # State that cannot be read fails closed.
    Assert-Equal ((Invoke-Backup @('-Volume', $vol, '-Container', "missing-$id", '-OutDir', $out)) -ne 0) $true 'state: unknown container fails closed'

    # Lock: a second run while one holds the per-volume lock is refused.
    $mutex = [System.Threading.Mutex]::new($true, "Global\homemade-llm-openwebui-$vol")
    try { Assert-Equal ((Invoke-Backup $common) -ne 0) $true 'lock: concurrent run refused' }
    finally { $mutex.ReleaseMutex(); $mutex.Dispose() }

    # Retention: 16 old archives + one new backup leaves the newest 14; other files are untouched.
    Get-ChildItem $out -Filter 'open-webui-*.tgz' | Remove-Item
    1..16 | ForEach-Object { Set-Content (Join-Path $out ('open-webui-200001{0:d2}-000000.tgz' -f $_)) 'x' }
    Set-Content (Join-Path $out 'notes.txt') 'keep'
    Invoke-Backup $common | Out-Null
    $left = @(Get-ChildItem $out -Filter 'open-webui-*.tgz' | Sort-Object Name)
    Assert-Equal $left.Count 14 'retention: newest 14 kept'
    Assert-Equal ($left[-1].Name -notlike 'open-webui-2000*') $true "retention: today's backup kept"
    Assert-Equal (Test-Path (Join-Path $out 'notes.txt')) $true 'retention: unrelated files untouched'
} finally {
    docker rm -f $busy 2>$null | Out-Null
    docker rm -f $ctr 2>$null | Out-Null
    docker volume rm $vol 2>$null | Out-Null
    if (Test-Path $work) { [IO.Directory]::Delete($work, $true) }
}

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-backup-openwebui: all cases passed'
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:58 UTC | END]]
