# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:34 UTC | BEGIN]]
# Behavioural checks for preflight.ps1 against scratch hot roots (never C:\llm, never -Init).
#   pwsh -File projects\homemade-llm\scripts\test-preflight.ps1
# Exit 0 = all cases pass. Assertions read rows from the JSON report so unrelated host
# state (Docker down, Tailscale missing) cannot make a case pass or fail.
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'preflight.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ("preflight-test-" + [guid]::NewGuid().ToString('N'))
$failures = [System.Collections.Generic.List[string]]::new()

function New-Root([switch]$WithReports) {
    $root = Join-Path $work ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $root | Out-Null
    if ($WithReports) { New-Item -ItemType Directory -Force (Join-Path $root 'reports') | Out-Null }
    Set-Content (Join-Path $root 'payload.bin') ('a' * 2048) -NoNewline
    $root
}
function Invoke-Preflight([string]$Root, [string[]]$Extra = @()) {
    $out = & pwsh -NoProfile -File $script -HotRoot $Root @Extra 2>&1 | Out-String
    [pscustomobject]@{ exit = $LASTEXITCODE; out = $out }
}
function Get-Row([string]$Root, [string]$Check) {
    $json = Get-ChildItem (Join-Path $Root 'reports') -Filter *.json | Select-Object -Last 1
    ((Get-Content $json.FullName -Raw | ConvertFrom-Json).checks | Where-Object check -eq $Check).status
}
function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}

try {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
    Assert-Equal $errors.Count 0 'parser: no errors'

    # Load the real floor comparison from the script's syntax tree, so the rounding
    # boundary is pinned without depending on live C: free space.
    $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-BelowFloor' }, $true) | Select-Object -First 1
    . ([scriptblock]::Create($fn.Extent.Text))
    Assert-Equal (Test-BelowFloor (149.96 * 1GB) 150) $true 'floor: 149.96 GB is below a 150 GB floor (no rounding)'
    Assert-Equal (Test-BelowFloor (150 * 1GB) 150) $false 'floor: exactly 150 GB meets the floor'

    $r = New-Root -WithReports
    Invoke-Preflight $r | Out-Null
    Assert-Equal @(Get-ChildItem (Join-Path $r 'reports')).Count 0 'default run writes nothing'

    $r = New-Root -WithReports
    Invoke-Preflight $r @('-Report') | Out-Null
    Assert-Equal @(Get-ChildItem (Join-Path $r 'reports')).Count 1 '-Report writes one report'
    Assert-Equal (Get-Row $r 'C:\llm size') 'OK' 'cap: 2 KB under 1 GB is OK'

    $r = New-Root -WithReports
    $res = Invoke-Preflight $r @('-Report', '-HotCapGB', '0.000001')
    Assert-Equal (Get-Row $r 'C:\llm size') 'FAIL' 'cap: 2 KB over 1 KB is FAIL although it displays 0 GB'
    Assert-Equal $res.exit 1 'cap: FAIL exits 1'

    # Extreme floors keep these cases stable while C: free space churns; the raw-byte
    # (no rounding) comparison is covered by the cap case above.
    $r = New-Root -WithReports
    $res = Invoke-Preflight $r @('-Report', '-FloorGB', '1000000')
    Assert-Equal (Get-Row $r 'drive C (hot)') 'FAIL' 'floor: above any free space is FAIL'
    Assert-Equal $res.exit 1 'floor: FAIL exits 1'

    $r = New-Root -WithReports
    Invoke-Preflight $r @('-Report', '-FloorGB', '1') | Out-Null
    Assert-Equal (Get-Row $r 'drive C (hot)') 'OK' 'floor: 1 GB is OK'

    $r = New-Root -WithReports
    Invoke-Preflight $r @('-Report') | Out-Null
    Invoke-Preflight $r @('-Report') | Out-Null
    Assert-Equal @(Get-ChildItem (Join-Path $r 'reports')).Count 2 'two reports never overwrite each other'

    $r = New-Root
    $res = Invoke-Preflight $r @('-Report')
    Assert-Equal ([bool]($res.out -match 'report\s+FAIL')) $true '-Report without reports dir shows a FAIL row'
    Assert-Equal $res.exit 1 '-Report without reports dir exits 1'

    $r = New-Root -WithReports
    Set-Content (Join-Path $r 'models') 'not a directory'
    $res = Invoke-Preflight $r @('-Report')
    Assert-Equal (Get-Row $r 'layout C:\llm') 'FAIL' 'layout: a file where a directory belongs is FAIL'

    $fileRoot = Join-Path $work 'root-is-a-file'
    New-Item -ItemType Directory -Force $work | Out-Null
    Set-Content $fileRoot 'not a directory'
    $res = Invoke-Preflight $fileRoot
    Assert-Equal ([bool]($res.out -match 'llm root\s+FAIL')) $true 'hot root: a file in place of the root is FAIL'
    Assert-Equal $res.exit 1 'hot root: file root exits 1'

    # Incomplete scan: deny listing on one subtree of a scratch root, then restore it.
    $r = New-Root -WithReports
    $locked = Join-Path $r 'locked'
    New-Item -ItemType Directory $locked | Out-Null
    Set-Content (Join-Path $locked 'hidden.bin') 'x'
    icacls $locked /deny "$($env:USERNAME):(OI)(CI)(RD)" | Out-Null
    try { $res = Invoke-Preflight $r @('-Report') } finally { icacls $locked /remove:d $env:USERNAME | Out-Null }
    Assert-Equal (Get-Row $r 'C:\llm size') 'FAIL' 'incomplete scan fails closed'
    Assert-Equal $res.exit 1 'incomplete scan exits 1'
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-preflight: all cases passed'
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:34 UTC | END]]
