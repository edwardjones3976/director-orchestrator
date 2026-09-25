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
