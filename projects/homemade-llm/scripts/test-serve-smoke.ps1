# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
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
foreach ($name in 'Test-Reply', 'Test-SameContent', 'Test-LoadedModel', 'Test-LoopbackOnly', 'Test-LmStudioOwner', 'Resolve-SmokeTrainReport') {
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
Assert-Equal (Test-Reply 'x' @((Body '{"Task_ID":"a","Objective":"b"}'), '200') 0).schema_valid $false 'reply: differently-cased keys fail'
Assert-Equal (Test-Reply 'x' @((Body '{"task_id":"a","Objective":"b"}'), '200') 0).schema_valid $false 'reply: one differently-cased key fails'
Assert-Equal (Test-Reply 'x' @((Body '[{"task_id":"a","objective":"b"}]'), '200') 0).schema_valid $false 'reply: one-element array fails'
$esc = [string][char]27 + ']0;owned' + [char]7 + [char]27 + '[31mred'
$shown = (Test-Reply 'x' @((Body $esc), '200') 0).content
Assert-Equal ([regex]::IsMatch($shown, '\p{Cc}')) $false 'reply: control characters in model output are escaped for display'
Assert-Equal ($shown -like '\u001b]0;owned\u0007*') $true 'reply: escaped form stays readable'

$p = 'homemade-llm/smoke/new.gguf'
$m = { param($path, $ttl, $id = 'smoke') [pscustomobject]@{ identifier = $id; path = $path; ttlMs = $ttl } }
Assert-Equal (Test-LoadedModel @(& $m $p 900000) $p 900) $true 'load: right model with requested TTL passes'
Assert-Equal (Test-LoadedModel @(& $m $p $null) $p 900) $false 'load: model without TTL (reload skipped) fails'
Assert-Equal (Test-LoadedModel @(& $m $p $null) $p 0) $false 'load: missing TTL fails even when 0 is requested'
Assert-Equal (Test-LoadedModel @(& $m $p 600000) $p 900) $false 'load: stale TTL from an earlier load fails'
Assert-Equal (Test-LoadedModel @(& $m 'homemade-llm/smoke/old.gguf' 900000) $p 900) $false 'load: older build still loaded fails'
Assert-Equal (Test-LoadedModel @() $p 900) $false 'load: nothing loaded fails'
Assert-Equal (Test-LoadedModel @((& $m $p 900000), (& $m $p 900000)) $p 900) $false 'load: duplicate smoke identifiers fail'
Assert-Equal (Test-LoadedModel @((& $m $p 900000), (& $m 'x' 1 'other')) $p 900) $true 'load: other loaded models are ignored'

$l = { param($addr) [pscustomobject]@{ LocalAddress = $addr; LocalPort = 1234 } }
Assert-Equal (Test-LoopbackOnly @(& $l '127.0.0.1')) $true 'bind: IPv4 loopback only passes'
Assert-Equal (Test-LoopbackOnly @((& $l '127.0.0.1'), (& $l '::1'))) $true 'bind: IPv4 and IPv6 loopback passes'
Assert-Equal (Test-LoopbackOnly @((& $l '127.0.0.1'), (& $l '0.0.0.0'))) $false 'bind: any extra non-loopback listener fails'
Assert-Equal (Test-LoopbackOnly @(& $l '192.168.1.20')) $false 'bind: LAN address fails'
Assert-Equal (Test-LoopbackOnly @()) $false 'bind: no listener fails'
$proc = { param($n) [pscustomobject]@{ ProcessName = $n } }
Assert-Equal (Test-LmStudioOwner @(& $proc 'LM Studio')) $true 'owner: LM Studio listener passes'
Assert-Equal (Test-LmStudioOwner @((& $proc 'LM Studio'), (& $proc 'ollama'))) $false 'owner: another process also on 1234 fails'
Assert-Equal (Test-LmStudioOwner @(& $proc 'python')) $false 'owner: a different server fails'
Assert-Equal (Test-LmStudioOwner @()) $false 'owner: no owning process fails'

# Structural checks on serve-smoke.ps1 itself: the function tests above would still pass if a call site
# were deleted or moved, so assert the script's top-level call order from its syntax tree.
$fnSpans = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
    ForEach-Object { , @($_.Extent.StartOffset, $_.Extent.EndOffset) })
$cmds = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) |
    Where-Object { $o = $_.Extent.StartOffset; -not ($fnSpans | Where-Object { $o -ge $_[0] -and $o -lt $_[1] }) } |
    Sort-Object { $_.Extent.StartOffset })
function First-Offset([scriptblock]$Match) { ($cmds | Where-Object $Match | Select-Object -First 1).Extent.StartOffset }
$start = First-Offset { $_.Extent.Text -match "^Invoke-Lms @\('server', 'start'\)" }
$preBind = First-Offset { $_.Extent.Text -match '^Test-LoopbackOnly' }
$unload = First-Offset { $_.Extent.Text -match "^Invoke-Lms @\('unload', 'smoke'\)" }
$load = First-Offset { $_.Extent.Text -match "^Invoke-Lms @\('load', .*'--ttl', \`$TtlSeconds" }
$postLoad = First-Offset { $_.Extent.Text -match '^Test-LoadedModel' }
$curl = First-Offset { $_.Extent.Text -match '^curl\.exe' }
Assert-Equal ([bool]($start -and $preBind -and $start -lt $preBind -and $preBind -lt $load)) $true 'order: loopback check runs after server start and before any load'
$ownerCheck = First-Offset { $_.Extent.Text -match '^Test-LmStudioOwner' }
Assert-Equal ([bool]($ownerCheck -and $start -lt $ownerCheck -and $ownerCheck -lt $load)) $true 'order: listener ownership is checked after server start and before any load'
Assert-Equal ([bool]($unload -and $unload -lt $load)) $true 'order: an existing smoke model is unloaded before the reload'
Assert-Equal ([bool]$load) $true 'order: the model is (re)loaded with --ttl $TtlSeconds'
Assert-Equal ([bool]($postLoad -and $load -lt $postLoad -and $postLoad -lt $curl)) $true 'order: Test-LoadedModel verifies the load before any request'
# A late exposure must take the server down: after the final loopback check there is an unload and a stop.
$finalBind = ($cmds | Where-Object { $_.Extent.Text -match '^Test-LoopbackOnly \$listeners' } | Select-Object -First 1).Extent.StartOffset
$lateStop = ($cmds | Where-Object { $_.Extent.Text -match "^Invoke-Lms @\('server', 'stop'\)" -and $_.Extent.StartOffset -gt $finalBind } | Select-Object -First 1).Extent.StartOffset
$lateUnload = ($cmds | Where-Object { $_.Extent.Text -match "^Invoke-Lms @\('unload', 'smoke'\)" -and $_.Extent.StartOffset -gt $finalBind } | Select-Object -First 1).Extent.StartOffset
Assert-Equal ([bool]($finalBind -and $lateUnload -and $lateStop)) $true 'order: a failed final loopback check unloads and stops the server'

# Both load-verification failures unload 'smoke' before failing (the TTL cannot be trusted then).
$unloadsBeforeProbe = @($cmds | Where-Object { $_.Extent.Text -match "^Invoke-Lms @\('unload', 'smoke'\)" -and $_.Extent.StartOffset -gt $load -and $_.Extent.StartOffset -lt $curl })
Assert-Equal $unloadsBeforeProbe.Count 2 'cleanup: failed load and failed TTL check both unload smoke'
# The cross-process lock is taken before the server is touched.
$lockAt = $ast.Extent.Text.IndexOf("Mutex]::new(`$false, 'Global\homemade-llm-gpu')")
Assert-Equal ([bool]($lockAt -ge 0 -and $lockAt -lt $start)) $true 'lock: shared GPU mutex taken before lms server start'

# A failed listener-ownership check stops the server before failing (#275 post-merge finding).
$ownerIf = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -match 'Test-LmStudioOwner' }, $true) | Select-Object -First 1
Assert-Equal ([bool]($ownerIf -and $ownerIf.Clauses[0].Item2.Extent.Text -match "Invoke-Lms @\('server', 'stop'\)")) $true 'owner: failed ownership check stops the server'

# Every docker and lms call goes through Invoke-Bounded, so none can hang the run while it holds the GPU lock.
$bare = @($cmds | Where-Object { $_.GetCommandName() -in 'docker', 'lms' })
Assert-Equal $bare.Count 0 'bounded: no top-level docker or lms call bypasses Invoke-Bounded'

# P7 single-host mode: exactly one call (no container leg), bound to the train report, with a no-clobber receipt.
$text = $ast.Extent.Text
$containerIf = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '-not $SingleHost' }, $true) |
    Where-Object { $_.Clauses[0].Item2.Extent.Text -match "Invoke-Bounded 'docker'" } | Select-Object -First 1
Assert-Equal ([bool]$containerIf) $true 'single-host: the container leg runs only in the default two-leg mode'
Assert-Equal ($text.Contains('-eq $(if ($SingleHost) { 1 } else { 2 })')) $true 'single-host: PASS needs one schema-valid call there and two in the default mode'
Assert-Equal ($text -match '\[IO\.FileMode\]::CreateNew') $true 'single-host: the serve receipt is created exclusively (no clobber)'
Assert-Equal ($text.Contains('if ($SingleHost -xor [bool]$TrainReport)')) $true 'single-host: -SingleHost and -TrainReport are required together'
$bindAt = $text.IndexOf('$bound = Resolve-SmokeTrainReport'); $newestAt = $text.IndexOf("Get-ChildItem (Join-Path `$HotRoot 'models\smoke\*.gguf')")
Assert-Equal ([bool]($bindAt -ge 0 -and $newestAt -gt $bindAt)) $true 'single-host: the report binding is resolved before (and replaces) the newest-file default'

$work = Join-Path ([IO.Path]::GetTempPath()) ("serve-smoke-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
    # Train report binding: a synthetic HotRoot with one published GGUF and its report.
    $hr = Join-Path $work 'root'; New-Item -ItemType Directory (Join-Path $hr 'models\smoke'), (Join-Path $hr 'reports\smoke') | Out-Null
    $gg = Join-Path $hr 'models\smoke\smoke-20261006-050000-abcdef-model.Q4_K_M.gguf'
    [IO.File]::WriteAllBytes($gg, [byte[]](1..200))
    $ggSha = (Get-FileHash -LiteralPath $gg -Algorithm SHA256).Hash.ToLowerInvariant()
    $rp = Join-Path $hr 'reports\smoke\smoke-20261006-050000-abcdef.json'
    function Set-Report([hashtable]$Over = @{}) {
        $g = @{ path = '/workspace/models/smoke/smoke-20261006-050000-abcdef-model.Q4_K_M.gguf'; bytes = 200; sha256 = $ggSha }
        $r = @{ run = 'smoke-20261006-050000-abcdef'; status = 'complete'; gguf = @($g) }
        foreach ($k in $Over.Keys) { if ($k -like 'g.*') { $g[$k.Substring(2)] = $Over[$k] } else { $r[$k] = $Over[$k] } }
        [IO.File]::WriteAllText($rp, ($r | ConvertTo-Json -Depth 5))
    }
    function Refuses([scriptblock]$Call, [string]$Pattern) { try { & $Call | Out-Null; $false } catch { $_.Exception.Message -match $Pattern } }
    Set-Report
    $b = Resolve-SmokeTrainReport $rp $hr ''
    Assert-Equal ($b.Gguf -eq [IO.Path]::GetFullPath($gg) -and $b.GgufSha256 -eq $ggSha -and $b.Run -eq 'smoke-20261006-050000-abcdef') $true 'report: the named GGUF is resolved with its digest'
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr (Join-Path $hr 'models\smoke\other.gguf') } 'is not the GGUF the train report names') $true 'report: a -Gguf naming another file refuses'
    Set-Report @{ status = 'trained-not-exported' }
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } "not 'complete'") $true 'report: an incomplete train report refuses'
    Set-Report @{ 'g.sha256' = ('0' * 64) }
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'SHA-256 read 1 differs') $true 'report: a GGUF whose bytes differ from the report refuses'
    Set-Report @{ 'g.bytes' = 199 }
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'size differs') $true 'report: a size mismatch refuses'
    Set-Report @{ 'g.path' = '/workspace/elsewhere/x.gguf' }
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'not a published smoke GGUF') $true 'report: a GGUF path outside models/smoke refuses'
    Set-Report @{ gguf = @(@{ path = '/workspace/models/smoke/a.gguf'; bytes = 1; sha256 = ('1' * 64) }, @{ path = '/workspace/models/smoke/b.gguf'; bytes = 1; sha256 = ('2' * 64) }) }
    Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'exactly one GGUF') $true 'report: more than one GGUF refuses'

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
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
