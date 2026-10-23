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
    $mixed = Run @('-Rows', $rows, '-Probe', '-Winner', '-Base', 'org/other')
    if ($LASTEXITCODE -eq 0 -or $mixed -notmatch 'do not pass -Base') { $fail += "winner probe accepted another base: $mixed" }
    $missing = Run @('-Rows', (Join-Path (Split-Path $rows -Parent) 'accepted.jsonl'))
    if ($LASTEXITCODE -eq 0 -or $missing -notmatch 'training rows not found') { $fail += "missing rows file was accepted: $missing" }
    $raw = Run @('-Rows', $rows)
    if ($LASTEXITCODE -eq 0 -or $raw -notmatch 'accepted.jsonl, not raw rows') { $fail += "raw rows file was accepted: $raw" }
    foreach ($other in '-Winner', '-Fallback') {
        $mixed = Run @('-Rows', $rows, '-Probe', '-Student4Bit', $other)
        if ($LASTEXITCODE -eq 0 -or $mixed -notmatch 'mutually exclusive') { $fail += "student switch conflict accepted: $mixed" }
    }
    $missingMode = Run @('-Rows', $rows, '-Student4Bit')
    if ($LASTEXITCODE -eq 0 -or $missingMode -notmatch 'requires -Probe or -Epochs') { $fail += "student without mode accepted: $missingMode" }
    $zeroEpochs = Run @('-Rows', $rows, '-Student4Bit', '-Epochs', '0')
    if ($LASTEXITCODE -eq 0 -or $zeroEpochs -notmatch '-Epochs must be between 1 and 6') { $fail += "student zero epochs accepted: $zeroEpochs" }
    foreach ($name in '-Base', '-Revision', '-MergeSource', '-MergeRevision') {
        $mixed = Run @('-Rows', $rows, '-Probe', '-Student4Bit', $name, 'override')
        if ($LASTEXITCODE -eq 0 -or $mixed -notmatch "do not[\s|]+pass $name") { $fail += "student pin override accepted: $mixed" }
    }
} finally {
    Remove-Item -LiteralPath $rows -Force -ErrorAction SilentlyContinue
}
# Exercise the actual selection/default/root blocks without GPU or production data.
. (Join-Path $PSScriptRoot 'train-adapter-stage.ps1')
$source = Get-Content -LiteralPath $script -Raw
$start = $source.IndexOf('$WinnerBase = ')
$selection = [scriptblock]::Create('param($Epochs = 0, $EpochContextLimit, $TimeoutMinutes = 1, $Batch)' + "`n" + $source.Substring($start, $source.IndexOf('if ($Steps -lt 1)', $start) - $start))
$start = $source.IndexOf('if ($Winner -and [string]::IsNullOrWhiteSpace($ExpectedDigests))')
$defaults = [scriptblock]::Create($source.Substring($start, $source.IndexOf("`$TrustedDigestRoot = ''", $start) - $start))
$start = $source.IndexOf("`$TrustedDigestRoot = ''")
$rootBlock = $source.Substring($start, $source.IndexOf('if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests))', $start) - $start)
foreach ($epoch in $false, $true) {
    try {
        & {
            $Winner = $false; $Fallback = $false; $Student4Bit = $true; $Probe = -not $epoch
            $Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739'
            $selectionArgs = @{}
            if ($epoch) { $selectionArgs = @{ Epochs = 1; EpochContextLimit = 4096; TimeoutMinutes = 1; Batch = 'batch-0001' } }
            . $selection @selectionArgs
            if ($Base -cne 'unsloth/Qwen3-8B-unsloth-bnb-4bit' -or $Revision -cne '62efd7f9d748e394734a7adae2adf96e13a2abc8' -or
                $MergeSource -cne 'Qwen/Qwen3-8B' -or $MergeRevision -cne 'b968826d9c46dd6066d109eabc6255188de91218') { throw 'student selection/merge pair differs' }
            $HotRoot = 'C:\synthetic'; $ExpectedDigests = ''
            . $defaults
            if ($ExpectedDigests -cne 'C:\synthetic\reports\m2-expected-qwen3-8b-4bit-v1.json') { throw 'student default list differs' }
            $ExpectedDigests = 'explicit-list.json'
            . $defaults
            if ($ExpectedDigests -cne 'explicit-list.json') { throw 'explicit list was overwritten' }
            $absent = Join-Path ([IO.Path]::GetTempPath()) (([guid]::NewGuid().ToString('N')) + '-v4.json')
            $fixture = [scriptblock]::Create($rootBlock.Replace('C:\llm\reports\m2-trusted-digest-manifest-v4.json', $absent))
            $refused = $false
            try { . $fixture } catch {
                if ($TrustedDigestRoot -cne $absent -or $TrustedDigestRootSHA256 -cne $TrustedDigestRootV4SHA256 -or
                    $_.Exception.ToString() -notmatch [regex]::Escape($absent)) { throw }
                $refused = $true
            }
            if (-not $refused) { throw 'missing final v4 root did not fail closed' }
        }
    } catch { $fail += "student selection/default/v4 gate (epoch=$epoch): $_" }
}
if ($fail.Count) { $fail | Write-Output; exit 1 }
Write-Output 'ok  input refusals, student switch exclusivity, probe/epoch selection, digest-list defaults and missing final v4 refusal before GPU access'
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
