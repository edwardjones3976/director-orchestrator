# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | BEGIN]]
# Unit checks for serve-smoke.ps1's decision functions, loaded from its syntax tree (no LM Studio, no Docker).
#   pwsh -File projects\homemade-llm\scripts\test-serve-smoke.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'serve-smoke.ps1'
$failures = [System.Collections.Generic.List[string]]::new()
function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}

$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
Assert-Equal $errors.Count 0 'parser: no errors'
foreach ($name in 'Test-Reply', 'Test-SameContent') {
    $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true) | Select-Object -First 1
    . ([scriptblock]::Create($fn.Extent.Text))
}

function Body([string]$Content) { @{ choices = @(@{ message = @{ content = $Content } }) } | ConvertTo-Json -Depth 5 -Compress }
$good = '{"task_id":"a","objective":"b"}'
Assert-Equal (Test-Reply 'x' @((Body $good), '200') 0).schema_valid $true 'reply: good body passes'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":null,"objective":"b"}'), '200') 0).schema_valid $false 'reply: null value fails'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":5,"objective":"b"}'), '200') 0).schema_valid $false 'reply: number fails'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":"","objective":"b"}'), '200') 0).schema_valid $false 'reply: empty string fails'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":"a"}'), '200') 0).schema_valid $false 'reply: missing key fails'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":"a","objective":"b","x":1}'), '200') 0).schema_valid $false 'reply: extra key fails'
Assert-Equal (Test-Reply 'x' @((Body $good), '500') 0).schema_valid $false 'reply: HTTP 500 fails'
Assert-Equal (Test-Reply 'x' @((Body $good), '200') 7).schema_valid $false 'reply: curl exit 7 fails'
Assert-Equal (Test-Reply 'x' @('not json', '200') 0).schema_valid $false 'reply: non-JSON body fails'

$work = Join-Path ([IO.Path]::GetTempPath()) ("serve-smoke-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
    $a = Join-Path $work 'a.gguf'; $b = Join-Path $work 'b.gguf'; $c = Join-Path $work 'c.gguf'
    Set-Content $a 'same bytes' -NoNewline; Set-Content $b 'same bytes' -NoNewline; Set-Content $c 'other byte' -NoNewline
    Assert-Equal (Test-SameContent $a $b) $true 'import: identical content matches'
    Assert-Equal (Test-SameContent $a $c) $false 'import: same size, different bytes rejected'
    Assert-Equal (Test-SameContent $a (Join-Path $work 'missing.gguf')) $false 'import: missing import rejected'
} finally { [IO.Directory]::Delete($work, $true) }

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-serve-smoke: all cases passed'
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | END]]
