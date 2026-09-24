# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | BEGIN]]
# Behavioural checks for mirror.ps1 on scratch folders (never C:\llm or G:\llm-mirror).
#   pwsh -File projects\homemade-llm\scripts\test-mirror.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'mirror.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ("mirror-test-" + [guid]::NewGuid().ToString('N'))
$failures = [System.Collections.Generic.List[string]]::new()
function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}
function Invoke-Mirror([string[]]$Arguments) {
    & pwsh -NoProfile -File $script @Arguments 2>&1 | Out-Null
    $LASTEXITCODE
}

try {
    $src = Join-Path $work 'src'; $dst = Join-Path $work 'dst'
    New-Item -ItemType Directory -Force (Join-Path $src 'models'), (Join-Path $src 'secrets'), (Join-Path $src 'reports') | Out-Null
    Set-Content (Join-Path $src 'models\adapter.bin') ('m' * 4096) -NoNewline
    Set-Content (Join-Path $src 'secrets\hf-token') 'never mirrored' -NoNewline

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst, '-List')) 0 '-List exits 0'
    Assert-Equal (Test-Path (Join-Path $dst 'models\adapter.bin')) $false '-List copies nothing'

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst)) 0 'mirror exits 0'
    Assert-Equal (Test-Path (Join-Path $dst 'secrets')) $false 'secrets are never mirrored'
    $a = (Get-FileHash (Join-Path $src 'models\adapter.bin')).Hash
    Assert-Equal (Get-FileHash (Join-Path $dst 'models\adapter.bin')).Hash $a 'mirrored file hash matches'

    # Restore drill: lose the source file, copy it back from the mirror, compare hashes.
    Remove-Item (Join-Path $src 'models\adapter.bin')
    Copy-Item (Join-Path $dst 'models\adapter.bin') (Join-Path $src 'models\adapter.bin')
    Assert-Equal (Get-FileHash (Join-Path $src 'models\adapter.bin')).Hash $a 'restored file hash matches'

    Set-Content (Join-Path $dst 'stale.txt') 'gone from source'
    Invoke-Mirror @('-Source', $src, '-Dest', $dst) | Out-Null
    Assert-Equal (Test-Path (Join-Path $dst 'stale.txt')) $false '/MIR removes files no longer in the source'
    Assert-Equal (@(Get-ChildItem (Join-Path $src 'reports') -Filter 'mirror-*.log').Count -gt 0) $true 'a log lands in reports'

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', 'Q:\')) 1 'refuses a drive root destination'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', (Join-Path $src 'inner'))) 1 'refuses a destination inside the source'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $src)) 1 'refuses source equal to destination'
    Assert-Equal (Invoke-Mirror @('-Source', (Join-Path $work 'missing'), '-Dest', $dst)) 1 'refuses a missing source'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', '\\localhost\c$')) 1 'refuses a UNC share root'
    $elsewhere = Join-Path $work 'elsewhere'; New-Item -ItemType Directory $elsewhere | Out-Null
    Set-Content (Join-Path $elsewhere 'keep.txt') 'must survive'
    $link = Join-Path $work 'link'; New-Item -ItemType Junction -Path $link -Target $elsewhere | Out-Null
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $link)) 1 'refuses a junction destination'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', (Join-Path $link 'sub'))) 1 'refuses a destination beneath a junction'
    Assert-Equal (Test-Path (Join-Path $elsewhere 'keep.txt')) $true 'junction target left untouched'
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-mirror: all cases passed'
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | END]]
