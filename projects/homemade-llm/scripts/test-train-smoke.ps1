# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | BEGIN]]
# Checks that the shared Invoke-Bounded (invoke-bounded.ps1) cannot hang on a stalled CLI and leaves no child
# process behind (no Docker needed: a fake docker.cmd that never returns is put first on PATH), then the
# train-smoke.ps1 refusals.
#   pwsh -File projects\homemade-llm\scripts\test-train-smoke.ps1
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'train-smoke.ps1'), [ref]$tokens, [ref]$errors)
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')
function Get-ShimPing { @(Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object CommandLine -match '-n 600 127\.0\.0\.1') }

$shim = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $shim | Out-Null
$oldPath = $env:PATH
try {
    Set-Content (Join-Path $shim 'docker.cmd') '@ping -n 600 127.0.0.1 >nul'
    $env:PATH = "$shim;$oldPath"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $r = @(Invoke-Bounded 'docker' @('stop', 'x') 3)
    $t.Stop()
    Start-Sleep 1
    $orphans = (Get-ShimPing).Count
    $fail = @()
    if ($errors.Count) { $fail += 'train-smoke.ps1 has parse errors' }
    if ($r[-1] -ne '__EXIT__timeout') { $fail += "hung docker: expected __EXIT__timeout, got '$($r[-1])'" }
    if ($t.Elapsed.TotalSeconds -gt 15) { $fail += "hung docker: returned after $([int]$t.Elapsed.TotalSeconds) s (bound 3 s)" }
    if ($orphans) { $fail += "hung docker: $orphans child process(es) still running after the timeout" }
    # Cancellation (Ctrl+C or a stopped pipeline) while waiting must also kill the command's tree.
    $rs = [powershell]::Create()
    $null = $rs.AddScript(". '$(Join-Path $PSScriptRoot 'invoke-bounded.ps1')'; `$env:PATH = '$shim;' + `$env:PATH; Invoke-Bounded 'docker' @('stop', 'y') 600")
    $h = $rs.BeginInvoke()
    for ($i = 0; $i -lt 50 -and -not (Get-ShimPing).Count; $i++) { Start-Sleep -Milliseconds 200 }
    $rs.Stop(); $rs.Dispose()
    Start-Sleep 1
    if ((Get-ShimPing).Count) { $fail += "cancelled wait: child process still running after PowerShell.Stop()" }
    # Output shape: one line, several lines, and none must all end in a separate exit marker.
    $one = @(Invoke-Bounded 'cmd' @('/c', 'echo one-line') 10)
    if ($one.Count -ne 2 -or $one[0] -ne 'one-line' -or $one[1] -ne '__EXIT__0') { $fail += "one-line output: got $($one | ConvertTo-Json -Compress)" }
    $two = @(Invoke-Bounded 'cmd' @('/c', 'echo a& echo b') 10)
    if (($two -join '|') -ne 'a|b|__EXIT__0') { $fail += "two-line output: got $($two -join '|')" }
    $none = @(Invoke-Bounded 'cmd' @('/c', 'exit 3') 10)
    if (($none -join '|') -ne '__EXIT__3') { $fail += "no output, exit 3: got $($none -join '|')" }
} finally {
    $env:PATH = $oldPath
    Get-ShimPing | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }   # only if a check failed
    [IO.Directory]::Delete($shim, $true)
}
# Pre-preflight refusals (run the real script; both stop before preflight or Docker).
$tmpRoot = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-root-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory (Join-Path $tmpRoot 'hf') | Out-Null
$links = @()
try {
    Set-Content (Join-Path $tmpRoot 'hf\token') 'hf_dummy'
    # Error text arrives colored and wrapped to the console width: strip ANSI codes and collapse whitespace.
    # Fail-safe if a guard regresses: an invalid image reference (docker rejects it locally, no pull) and a
    # 3-second timeout keep the run from ever starting real training.
    function Run([string]$Root, [string[]]$Extra = @()) { ((& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'train-smoke.ps1') -HotRoot $Root -Image 'INVALID_REFERENCE' -TimeoutMinutes 0.05 @Extra 2>&1 | Out-String) -replace '\x1b\[[0-9;]*m', '' -replace '[\s|]+', ' ') }
    $o = Run $tmpRoot
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'stored Hugging Face credential') { $fail += "stored token: not refused (exit $LASTEXITCODE)" }
    $o = Run 'D:\llm'
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'HotRoot must be on C:') { $fail += "non-C HotRoot: not refused (exit $LASTEXITCODE)" }
    # Links (junctions need no admin rights): on the root itself, and on a mounted subfolder of a real root.
    $real = Join-Path $tmpRoot 'real'; $clean = Join-Path $tmpRoot 'clean'
    New-Item -ItemType Directory $real, $clean | Out-Null
    $links = @((New-Item -ItemType Junction (Join-Path $tmpRoot 'jroot') -Target $real), (New-Item -ItemType Junction (Join-Path $clean 'reports') -Target $real))
    $o = Run (Join-Path $tmpRoot 'jroot')
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'junction or symbolic link') { $fail += "junction HotRoot: not refused (exit $LASTEXITCODE)" }
    $o = Run $clean
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'junction or symbolic link') { $fail += "junction reports folder: not refused (exit $LASTEXITCODE)" }
    $nested = Join-Path $tmpRoot 'nested'; New-Item -ItemType Directory (Join-Path $nested 'hf') | Out-Null
    $links += New-Item -ItemType Junction (Join-Path $nested 'hf\hub') -Target $real
    $o = Run $nested
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'Junction inside a mounted folder') { $fail += "junction nested in hf: not refused (exit $LASTEXITCODE)" }
    # A base override needs its own merge-source pin, and every revision must be a full commit.
    $plain = Join-Path $tmpRoot 'plain'; New-Item -ItemType Directory $plain | Out-Null
    $o = Run $plain @('-Base', 'org/other-4bit', '-Revision', ('a' * 40))
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'needs a matching -MergeSource') { $fail += "base override without merge pin: not refused (exit $LASTEXITCODE)" }
    $o = Run $plain @('-Revision', ('b' * 40))
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'needs a matching -MergeSource') { $fail += "revision override without merge pin: not refused (exit $LASTEXITCODE)" }
    $o = Run $plain @('-MergeRevision', 'main')
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'not a full 40-hex commit') { $fail += "moving merge revision: not refused (exit $LASTEXITCODE)" }
    # The shared GPU lock: while another process holds it, training must not start.
    $holder = Start-Process pwsh -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command',
        '$m = [Threading.Mutex]::new($false, ''Global\homemade-llm-gpu''); $null = $m.WaitOne(0); Start-Sleep 60'
    try {
        Start-Sleep 3
        $o = Run $plain
        if ($LASTEXITCODE -eq 0 -or $o -notmatch 'another GPU job') { $fail += "GPU lock held elsewhere: not refused (exit $LASTEXITCODE)" }
    } finally { Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue }
} finally {
    foreach ($l in $links) { if ($l) { $l.Delete() } }   # remove the links themselves before the recursive delete
    Get-ChildItem -LiteralPath $tmpRoot -Recurse -Force -File | ForEach-Object { $_.Attributes = 'Normal' }   # HF marks some files read-only
    [IO.Directory]::Delete($tmpRoot, $true)
}

# Both scripts release the GPU lock in a finally block, so a run inside a long-lived session cannot keep it.
foreach ($f in 'train-smoke.ps1', 'serve-smoke.ps1') {
    $a = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $f), [ref]$null, [ref]$null)
    $release = @($a.FindAll({ param($n) $n -is [System.Management.Automation.Language.TryStatementAst] -and $n.Finally -and $n.Finally.Extent.Text -match 'ReleaseMutex\(\)' }, $true))
    if ($release.Count -ne 1) { $fail += "$f`: expected one finally that releases the GPU mutex, found $($release.Count)" }
}

# Grok post-merge findings on #275 (structural: an unkillable process or unremovable container cannot be simulated here).
$ib = Get-Content -Raw (Join-Path $PSScriptRoot 'invoke-bounded.ps1')
if ($ib -notmatch '(?s)finally \{.*\$gone = \$p\.HasExited -or \$p\.WaitForExit\(5000\).*if \(-not \$gone\) \{ throw') { $fail += 'invoke-bounded: an unkillable command must throw, not return a timeout' }
$ts = Get-Content -Raw (Join-Path $PSScriptRoot 'train-smoke.ps1')
if ($ts -notmatch 'if \(\$containerGone -ne \$false\) \{ \$gpuLock\.ReleaseMutex\(\)') { $fail += 'train-smoke: the GPU lock must be kept unless the container is confirmed gone' }
# #279 review: the normal path confirms absence too, and the flag is set only after Start-Job launched.
if (-not $ts.Contains('$containerGone = $true')) { } else { $fail += 'train-smoke: containerGone must never be set true without a daemon check' }
$iJob = $ts.IndexOf('$job = Start-Job'); $iFlag = $ts.IndexOf('$containerGone = $false')
if (-not ($iJob -ge 0 -and $iFlag -gt $iJob)) { $fail += 'train-smoke: containerGone=false must come after Start-Job' }
if (-not $ts.Contains('$containerGone = $state -eq ''stopped and removed''')) { $fail += 'train-smoke: containerGone must come from the Stop-Smoke result' }

if ($fail) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'ok  stored Hugging Face token in the mounted cache refused'
Write-Output 'ok  HotRoot off drive C refused'
Write-Output 'ok  junction as HotRoot, as a mounted subfolder, and nested inside hf, refused'
Write-Output 'ok  base or revision override without a merge pin, and a non-commit revision, refused'
Write-Output 'ok  both scripts release the GPU mutex in a finally block'
Write-Output 'ok  unkillable bounded command throws; GPU lock kept unless the container is confirmed gone'
Write-Output 'ok  GPU lock held by another process refused'
Write-Output 'ok  Invoke-Bounded kills the child when its pipeline is stopped'
Write-Output 'ok  Invoke-Bounded output shape: one line, two lines, and no output with an exit code'
Write-Output "ok  hung docker CLI: returned after $([math]::Round($t.Elapsed.TotalSeconds, 1)) s with a timeout marker, no child process left"
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
