# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | BEGIN]]
# Behavioural checks for scan-dataset.ps1 on scratch folders (never the live C:\llm\datasets).
#   pwsh -File projects\homemade-llm\scripts\test-scan-dataset.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'scan-dataset.ps1'
$gitleaks = 'C:\llm\tools\gitleaks\gitleaks.exe'
$work = Join-Path ([IO.Path]::GetTempPath()) ("scan-dataset-test-" + [guid]::NewGuid().ToString('N'))
$failures = [System.Collections.Generic.List[string]]::new()

function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}
function Assert-True($Actual, [string]$Case) {
    if (-not $Actual) { $failures.Add("$Case`: expected true") } else { Write-Output "ok  $Case" }
}
function Invoke-Scan([string]$HotRoot, [string]$ScanPath) {
    $out = & pwsh -NoProfile -File $script -Path $ScanPath -HotRoot $HotRoot -Gitleaks $gitleaks 2>&1 | Out-String
    [pscustomobject]@{ exit = $LASTEXITCODE; out = $out }
}
function Get-LatestReport([string]$HotRoot) {
    Get-ChildItem (Join-Path $HotRoot 'reports') -Filter 'scan-dataset-*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
}

try {
    $hot = $work
    $scan = Join-Path $hot 'datasets'
    New-Item -ItemType Directory -Force (Join-Path $hot 'reports'), $scan | Out-Null

    @(
        '{"text":"hello world","source":"fixture"}'
        '{"text":"another row","meta":{"epoch":1}}'
        '{"messages":[{"role":"user","content":"ping"}]}'
    ) | Set-Content (Join-Path $scan 'train.jsonl') -Encoding utf8

    $clean = Invoke-Scan $hot $scan
    Assert-Equal $clean.exit 0 'clean fixture exits 0'
    Assert-True ($clean.out -match 'findings=0') 'clean fixture reports zero findings'

    $prefix = 'gh' + 'p_'
    $body = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 36 | ForEach-Object { [char]$_ })
    $planted = $prefix + $body
    Set-Content (Join-Path $scan 'leak.jsonl') ('{"text":"token ' + $planted + '"}') -Encoding utf8 -NoNewline

    $dirty = Invoke-Scan $hot $scan
    Assert-Equal $dirty.exit 1 'planted secret exits 1'

    $reportFile = Get-LatestReport $hot
    Assert-True ($null -ne $reportFile) 'report file was written'
    $findings = Get-Content $reportFile.FullName -Raw | ConvertFrom-Json
    $findingCount = if ($findings -is [System.Array]) { $findings.Count } else { if ($findings) { 1 } else { 0 } }
    Assert-True ($findingCount -ge 1) 'report JSON has at least one finding'
    $reportRaw = Get-Content $reportFile.FullName -Raw
    Assert-True ($dirty.out -notmatch $planted) 'planted value absent from stdout (redact)'
    Assert-True ($reportRaw -notmatch $planted) 'planted value absent from report (redact)'

    # Config discovery by any other name: a directory named .gitleaks.toml beside a TOML-bodied .gitleaks.json
    # (the reviewer's traced bypass) must not suppress the planted finding.
    New-Item -ItemType Directory (Join-Path $scan '.gitleaks.toml') | Out-Null
    Set-Content (Join-Path $scan '.gitleaks.json') "[extend]`nuseDefault = true`n[allowlist]`npaths = ['''.*''']" -Encoding utf8
    Assert-Equal (Invoke-Scan $hot $scan).exit 1 'alternate-name dataset config cannot suppress findings'
    Remove-Item (Join-Path $scan '.gitleaks.toml'), (Join-Path $scan '.gitleaks.json') -Recurse -Force

    # A link inside the dataset would be skipped by gitleaks, so it is refused (a junction needs no admin rights).
    $outside = Join-Path $work 'outside'; New-Item -ItemType Directory $outside | Out-Null
    $j = New-Item -ItemType Junction (Join-Path $scan 'linked') -Target $outside
    Assert-Equal (Invoke-Scan $hot $scan).exit 2 'link inside the dataset is refused (exit 2)'
    $j.Delete()

    # A .gitleaksignore in the caller's working directory, holding the real finding's fingerprint, must not hide it.
    $cwd = Join-Path $work 'caller-cwd'; New-Item -ItemType Directory $cwd | Out-Null
    @($findings) | ForEach-Object { $_.Fingerprint } | Set-Content (Join-Path $cwd '.gitleaksignore') -Encoding utf8
    Push-Location $cwd
    try { $fromCwd = Invoke-Scan $hot $scan } finally { Pop-Location }
    Assert-Equal $fromCwd.exit 1 'caller working-directory .gitleaksignore cannot suppress findings'

    # The dataset cannot switch its own scan off: an allow-all config, an ignore file or an inline allow marker is refused.
    Set-Content (Join-Path $scan '.gitleaks.toml') "[allowlist]`npaths = ['''.*''']" -Encoding utf8
    Assert-Equal (Invoke-Scan $hot $scan).exit 2 'dataset .gitleaks.toml is refused (exit 2)'
    Remove-Item (Join-Path $scan '.gitleaks.toml')
    Set-Content (Join-Path $scan '.gitleaksignore') 'anything' -Encoding utf8
    Assert-Equal (Invoke-Scan $hot $scan).exit 2 'dataset .gitleaksignore is refused (exit 2)'
    Remove-Item (Join-Path $scan '.gitleaksignore')
    Set-Content (Join-Path $scan 'marked.jsonl') ('{"text":"x"} # gitleaks' + ':allow') -Encoding utf8
    Assert-Equal (Invoke-Scan $hot $scan).exit 2 'inline gitleaks:allow marker is refused (exit 2)'
    Remove-Item (Join-Path $scan 'marked.jsonl')

    # A gitleaks error (its own exit 1) must map to 2, never to 'findings' (1).
    $fake = Join-Path $work 'fake-gitleaks.cmd'; Set-Content $fake '@exit /b 1' -Encoding ascii
    $err = & pwsh -NoProfile -File $script -Path $scan -HotRoot $hot -Gitleaks $fake 2>&1 | Out-String
    Assert-Equal $LASTEXITCODE 2 'gitleaks error exit maps to 2, not findings'

    $missing = Join-Path $work 'no-such-datasets'
    $miss = & pwsh -NoProfile -File $script -Path $missing -HotRoot $hot -Gitleaks $gitleaks 2>&1 | Out-Null
    Assert-Equal $LASTEXITCODE 2 'missing scan path exits 2'
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-scan-dataset: all cases passed'
exit 0
# [[Cursor | cursor-agent | 2026-09-25 | 23:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:58 UTC | END]]
