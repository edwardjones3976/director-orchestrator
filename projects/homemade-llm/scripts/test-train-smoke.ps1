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
try {
    Set-Content (Join-Path $tmpRoot 'hf\token') 'hf_dummy'
    # Error text arrives colored and wrapped to the console width: strip ANSI codes and collapse whitespace.
    # Fail-safe if a guard regresses: an invalid image reference (docker rejects it locally, no pull) and a
    # 3-second timeout keep the run from ever starting real training.
    function Run([string]$Root) { ((& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'train-smoke.ps1') -HotRoot $Root -Image 'INVALID_REFERENCE' -TimeoutMinutes 0.05 2>&1 | Out-String) -replace '\x1b\[[0-9;]*m', '' -replace '[\s|]+', ' ') }
    $o = Run $tmpRoot
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'stored Hugging Face credential') { $fail += "stored token: not refused (exit $LASTEXITCODE)" }
    $o = Run 'D:\llm'
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'HotRoot must be on C:') { $fail += "non-C HotRoot: not refused (exit $LASTEXITCODE)" }
} finally {
    Get-ChildItem -LiteralPath $tmpRoot -Recurse -Force -File | ForEach-Object { $_.Attributes = 'Normal' }   # HF marks some files read-only
    [IO.Directory]::Delete($tmpRoot, $true)
}

if ($fail) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'ok  stored Hugging Face token in the mounted cache refused'
Write-Output 'ok  HotRoot off drive C refused'
Write-Output "ok  hung docker CLI: cleanup call returned after $([math]::Round($t.Elapsed.TotalSeconds, 1)) s with a timeout marker"
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | END]]
