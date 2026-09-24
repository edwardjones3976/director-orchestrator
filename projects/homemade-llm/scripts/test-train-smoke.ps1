# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | BEGIN]]
# Checks that train-smoke.ps1's timeout cleanup cannot hang on a stalled docker CLI (no Docker needed:
# a fake docker.cmd that never returns is put first on PATH).
#   pwsh -File projects\homemade-llm\scripts\test-train-smoke.ps1
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'train-smoke.ps1'), [ref]$tokens, [ref]$errors)
$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-Bounded' }, $true) | Select-Object -First 1
. ([scriptblock]::Create($fn.Extent.Text))

$shim = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $shim | Out-Null
$oldPath = $env:PATH
try {
    Set-Content (Join-Path $shim 'docker.cmd') '@ping -n 600 127.0.0.1 >nul'
    $env:PATH = "$shim;$oldPath"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $r = @(Invoke-Bounded @('stop', 'x') 3)
    $t.Stop()
    $fail = @()
    if ($errors.Count) { $fail += 'train-smoke.ps1 has parse errors' }
    if ($r[-1] -ne '__EXIT__timeout') { $fail += "hung docker: expected __EXIT__timeout, got '$($r[-1])'" }
    if ($t.Elapsed.TotalSeconds -gt 15) { $fail += "hung docker: returned after $([int]$t.Elapsed.TotalSeconds) s (bound 3 s)" }
} finally {
    $env:PATH = $oldPath
    Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object CommandLine -match '-n 600 127\.0\.0\.1' |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
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

if ($fail) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'ok  stored Hugging Face token in the mounted cache refused'
Write-Output 'ok  HotRoot off drive C refused'
Write-Output 'ok  junction as HotRoot, as a mounted subfolder, and nested inside hf, refused'
Write-Output 'ok  base override without a merge pin, and a non-commit revision, refused'
Write-Output 'ok  GPU lock held by another process refused'
Write-Output "ok  hung docker CLI: cleanup call returned after $([math]::Round($t.Elapsed.TotalSeconds, 1)) s with a timeout marker"
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | END]]
