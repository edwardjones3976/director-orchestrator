# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
# Refusals for train-adapter.ps1 that happen before the GPU lock. No Docker and no preflight.
#   pwsh -File projects\homemade-llm\scripts\test-train-adapter.ps1
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$script = Join-Path $PSScriptRoot 'train-adapter.ps1'
[void][System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
$fail = @()
if ($errors.Count) { $fail += 'train-adapter.ps1 has parse errors' }
function Run([string[]]$argList) {
    $out = & pwsh -NoProfile -File $script @argList 2>&1 | Out-String
    return $out
}
$rows = Join-Path ([IO.Path]::GetTempPath()) ("m2-t1-rows-" + [guid]::NewGuid().ToString('N') + '.jsonl')
Set-Content -LiteralPath $rows -Value '{"split":"train"}' -Encoding utf8
try {
    $bad = Run @('-Rows', $rows, '-Revision', 'main')
    if ($LASTEXITCODE -eq 0 -or $bad -notmatch 'not a full 40-hex commit') { $fail += "short revision was accepted: $bad" }
    $mixed = Run @('-Rows', $rows, '-Winner', '-Base', 'org/other')
    if ($LASTEXITCODE -eq 0 -or $mixed -notmatch 'do not pass -Base') { $fail += "winner probe accepted another base: $mixed" }
    $missing = Run @('-Rows', (Join-Path (Split-Path $rows -Parent) 'accepted.jsonl'))
    if ($LASTEXITCODE -eq 0 -or $missing -notmatch 'training rows not found') { $fail += "missing rows file was accepted: $missing" }
    $raw = Run @('-Rows', $rows)
    if ($LASTEXITCODE -eq 0 -or $raw -notmatch 'accepted.jsonl, not raw rows') { $fail += "raw rows file was accepted: $raw" }
} finally {
    Remove-Item -LiteralPath $rows -Force -ErrorAction SilentlyContinue
}
if ($fail.Count) { $fail | Write-Output; exit 1 }
Write-Output 'ok  short revision, winner-plus-base, a missing accepted file, and raw rows are refused before the GPU lock'
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
