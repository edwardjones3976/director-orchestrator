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
foreach ($name in 'Test-Reply', 'Test-SameContent', 'Test-LoadedModel', 'Test-LoopbackOnly', 'Test-LmStudioOwner', 'Resolve-SmokeTrainReport',
    'Test-FinalOwnership', 'Read-RawBody', 'Assert-GgufMatchesReport', 'Write-ServeReceipt', 'Get-PostRequestModelState') {
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
# Final ownership, from the final listeners (IPv4 + IPv6 sockets of one LM Studio process resolve to two owner entries).
$two = @((& $l '127.0.0.1'), (& $l '::1'))
Assert-Equal (Test-FinalOwnership $two @((& $proc 'LM Studio'), (& $proc 'LM Studio'))) $true 'final owner: every final listener owned by LM Studio passes'
Assert-Equal (Test-FinalOwnership $two @(& $proc 'LM Studio')) $false 'final owner: a listener whose owner cannot be resolved fails'
Assert-Equal (Test-FinalOwnership $two @((& $proc 'LM Studio'), (& $proc 'python'))) $false 'final owner: a replaced owner fails'
Assert-Equal (Test-FinalOwnership @() @()) $false 'final owner: no final listener fails'

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

# #468 revision 2: reservation, pre-load revalidation, raw response bytes, final ownership, receipt completion.
$reserveAt = First-Offset { $_.Extent.Text -ceq 'Write-ServeReceipt $bound.ReceiptPath $reservation -Reserve' }
$markerAt = $text.IndexOf('$markerWritten = $true'); $lockedAt = $text.IndexOf('$held = $lock.WaitOne(0)')
Assert-Equal ([bool]($reserveAt -and $lockedAt -lt $reserveAt -and $markerAt -lt $reserveAt -and $reserveAt -lt $start -and $reserveAt -lt $curl)) $true 'reserve: the receipt is reserved after the GPU lock and guard, before the server starts and before the request'
$completeAt = First-Offset { $_.Extent.Text -ceq 'Write-ServeReceipt $bound.ReceiptPath $receipt' }
Assert-Equal ([bool]($completeAt -and $completeAt -gt $curl)) $true 'reserve: the reservation is completed in place after the request'
Assert-Equal ([bool]($text -notmatch '(Remove-Item|Delete)[^\r\n]*ReceiptPath')) $true 'reserve: no code path deletes the receipt or reservation'
$sameAt = First-Offset { $_.Extent.Text -match '^Test-SameContent \$Gguf \$imported' }
$revalAt = First-Offset { $_.Extent.Text -ceq 'Assert-GgufMatchesReport @($Gguf, $imported) $bound.GgufBytes $bound.GgufSha256' }
Assert-Equal ([bool]($revalAt -and $sameAt -lt $revalAt -and $revalAt -lt $load)) $true 'revalidate: source and imported GGUF are checked against the report right before the load'
Assert-Equal ($text.Contains("curl.exe -s --noproxy '*' -o `$bodyFile -w '%{http_code}'")) $true 'raw body: curl writes the body to its own file and reports the status separately'
Assert-Equal ($text.Contains('$responseSha = $raw.Sha256')) $true 'raw body: the receipt hashes the raw body bytes, not reconstructed text'
Assert-Equal ($text.Contains('if (Test-Path -LiteralPath $bodyFile) { [IO.File]::Delete($bodyFile) }')) $true 'raw body: the temporary body file is deleted'
$finalOwnerAt = First-Offset { $_.Extent.Text -ceq 'Get-ListenerOwners $listeners' }
Assert-Equal ([bool]($finalOwnerAt -and $finalOwnerAt -gt $finalBind)) $true 'final owner: owners are resolved afresh from the final listeners'
Assert-Equal ($text.Contains('-and $loopbackOnly -and $ownedByLmStudio')) $true 'final owner: PASS requires LM Studio ownership of the final listeners'
Assert-Equal ($text.Contains('if (-not ($loopbackOnly -and $ownedByLmStudio -and $modelAfter)) {')) $true 'final owner: a missing or replaced owner unloads and stops the server'

# #468 revision 3.
# Proxy bypass at the argument level: every local smoke HTTP call carries --noproxy '*' (host curl and the container leg).
$hostCurl = @($cmds | Where-Object { $_.GetCommandName() -eq 'curl.exe' })
Assert-Equal ($hostCurl.Count -eq 1 -and @($hostCurl[0].CommandElements | ForEach-Object { $_.Extent.Text }) -join ' ' -match "^curl\.exe -s --noproxy '\*' ") $true 'proxy: the host curl bypasses any proxy (--noproxy *)'
$ctrCall = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.Extent.Text -match "^Invoke-Bounded 'docker' @\('run'" }, $true) | Select-Object -First 1
Assert-Equal ([bool]($ctrCall -and $ctrCall.Extent.Text -match '''--entrypoint'', ''curl'', \$Image,\s*''-s'', ''--noproxy'', ''\*''')) $true 'proxy: the container curl leg bypasses any proxy (--noproxy *)'
Assert-Equal ([bool]($text -notmatch 'FileMode\]::Truncate')) $true 'reserve: completion never truncates the reservation in place'
Assert-Equal ($text.Contains('[IO.File]::Replace($sibling, $Path, [NullString]::Value)')) $true 'reserve: completion atomically replaces the reservation with a flushed sibling'
# Fresh loaded-model state after the request: required for PASS, wired into the late cleanup, recorded in the receipt.
$postModelAt = ($cmds | Where-Object { $_.Extent.Text -ceq 'Get-PostRequestModelState $known.path $TtlSeconds' } | Select-Object -First 1).Extent.StartOffset
Assert-Equal ([bool]($postModelAt -and $postModelAt -gt $curl -and $postModelAt -lt $finalBind)) $true 'model after: loaded-model state is read again after the request'
Assert-Equal ($text.Contains('-and $loopbackOnly -and $ownedByLmStudio -and $modelAfter')) $true 'model after: PASS requires the same model still loaded after the request'
Assert-Equal ($text.Contains('model_after_request = [ordered]@{ expected_path = $known.path; still_loaded = $modelAfter; lms_ps_exit = $post.Exit;')) $true 'model after: the receipt records the post-request model state and the captured ps exit'
# The post-request ps exit is captured at once: a failed ps (124) followed by the cleanup it triggers (which resets the shared
# $lmsExit to 0) is still recorded as 124. Synthetic Invoke-Lms only; no LM Studio.
$script:lmsExit = $null
$script:psReply = @{ Exit = 124; Lines = @() }
function Invoke-Lms([string[]]$A, [int]$Seconds = 60) {
    if ($A[0] -eq 'ps') { $script:lmsExit = $script:psReply.Exit; $script:psReply.Lines } else { $script:lmsExit = 0; @() }
}
$st = Get-PostRequestModelState 'homemade-llm/smoke/new.gguf' 900
Invoke-Lms @('unload', 'smoke') | Out-Null; Invoke-Lms @('server', 'stop') | Out-Null   # the cleanup a failed check triggers
Assert-Equal $st.Exit 124 'model after: a failed lms ps exit survives the following cleanup calls'
Assert-Equal $st.StillLoaded $false 'model after: a failed lms ps never counts as still loaded'
Assert-Equal $script:lmsExit 0 'model after: (the cleanup really did overwrite the shared lms exit)'
$script:psReply = @{ Exit = 0; Lines = @('[{"identifier":"smoke","path":"homemade-llm/smoke/new.gguf","ttlMs":900000}]') }
$st = Get-PostRequestModelState 'homemade-llm/smoke/new.gguf' 900
Assert-Equal ($st.Exit -eq 0 -and $st.StillLoaded -and $st.Entries.Count -eq 1 -and $st.Entries[0].path -eq 'homemade-llm/smoke/new.gguf') $true 'model after: the same model still loaded passes and is recorded'
$script:psReply = @{ Exit = 0; Lines = @('[{"identifier":"smoke","path":"homemade-llm/smoke/other.gguf","ttlMs":900000}]') }
Assert-Equal (Get-PostRequestModelState 'homemade-llm/smoke/new.gguf' 900).StillLoaded $false 'model after: a replaced model fails'
Remove-Item Function:\Invoke-Lms
Assert-Equal ($text.Contains('owners = @($finalOwners | ForEach-Object')) $true 'final owner: the receipt binds the final owners'

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

    # The run names the receipt: only the generated id grammar, and the receipt stays in the report's directory.
    Set-Report
    $b = Resolve-SmokeTrainReport $rp $hr ''
    Assert-Equal ($b.ReceiptPath -eq [IO.Path]::GetFullPath((Join-Path $hr 'reports\smoke\serve-smoke-20261006-050000-abcdef.json'))) $true 'run: the receipt path is serve-<run>.json beside the report'
    Assert-Equal ($b.GgufBytes -eq 200) $true 'run: the report GGUF size is carried for the pre-load check'
    foreach ($bad in @('x\..\..\outside', '..\..\outside', 'C:\outside', '/outside', 'smoke-20261006-050000-abcdef\x', 'smoke-20261006-050000-abcdef/x',
            'smoke-20261006-050000-ABCDEF', 'smoke-2026106-050000-abcdef', 'smoke-20261006-050000-abcdef ', '')) {
        Set-Report @{ run = $bad }
        Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'not a generated smoke run id') $true "run: '$bad' refuses"
    }
    foreach ($bad in @(5, $null, @('smoke-20261006-050000-abcdef'))) {
        Set-Report @{ run = $bad }
        Assert-Equal (Refuses { Resolve-SmokeTrainReport $rp $hr '' } 'not a generated smoke run id') $true "run: a non-string run ($(ConvertTo-Json $bad -Compress)) refuses"
    }
    Assert-Equal (@(Get-ChildItem -LiteralPath (Join-Path $hr 'reports\smoke')).Name -join ',') 'smoke-20261006-050000-abcdef.json' 'run: refusals create no file'

    # Pre-load revalidation against the REPORT's size and digest (two reads), for the source and the imported copy.
    $imp = Join-Path $work 'imported.gguf'; [IO.File]::Copy($gg, $imp)
    Assert-Equal (Refuses { Assert-GgufMatchesReport @($gg, $imp) 200 $ggSha } 'no longer') $false 'revalidate: unchanged source and import pass'
    [IO.File]::WriteAllBytes($imp, [byte[]](2..201))
    Assert-Equal (Refuses { Assert-GgufMatchesReport @($gg, $imp) 200 $ggSha } 'imported.gguf SHA-256 read 1 no longer equals') $true 'revalidate: an import replaced after validation refuses'
    [IO.File]::Copy($gg, $imp, $true); [IO.File]::WriteAllBytes($gg, [byte[]](3..202))
    Assert-Equal (Refuses { Assert-GgufMatchesReport @($gg, $imp) 200 $ggSha } 'model.Q4_K_M.gguf SHA-256 read 1 no longer equals') $true 'revalidate: a source replaced after validation refuses (even when the import still matches)'
    [IO.File]::WriteAllBytes($gg, [byte[]](1..199))
    Assert-Equal (Refuses { Assert-GgufMatchesReport @($gg, $imp) 200 $ggSha } "no longer has the train report's size") $true 'revalidate: a resized source refuses'
    [IO.File]::WriteAllBytes($gg, [byte[]](1..200))
    $script:readsLeft = 1
    function Get-FileHash { param([string]$LiteralPath, [string]$Algorithm) if ($script:readsLeft-- -gt 0) { Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm } else { [pscustomobject]@{ Hash = '0' * 64 } } }
    Assert-Equal (Refuses { Assert-GgufMatchesReport @($gg) 200 $ggSha } 'SHA-256 read 2 no longer equals') $true 'revalidate: a second read that disagrees refuses (two reads)'
    Remove-Item Function:\Get-FileHash

    # Raw response bytes: CRLF line ends and non-ASCII text are hashed exactly as received.
    $body = Join-Path $work 'body.bin'
    $rawBytes = [Text.Encoding]::UTF8.GetBytes("{`"a`":`"caf$([char]0xE9) $([char]0x2713)`"}`r`n{`"b`":1}`r`n")
    [IO.File]::WriteAllBytes($body, $rawBytes)
    $rb = Read-RawBody $body
    Assert-Equal $rb.Sha256 (Get-FileHash -LiteralPath $body -Algorithm SHA256).Hash.ToLowerInvariant() 'raw body: the digest is of the exact bytes curl wrote'
    Assert-Equal $rb.Bytes $rawBytes.Length 'raw body: the byte count is exact'
    $lineJoined = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes((Get-Content -LiteralPath $body) -join "`n"))).ToLowerInvariant()
    Assert-Equal ($rb.Sha256 -ne $lineJoined) $true 'raw body: differs from the old line-joined reconstruction for a CRLF body'
    Assert-Equal ($rb.Text.Contains("caf$([char]0xE9) $([char]0x2713)") -and $rb.Text.Contains("`r`n")) $true 'raw body: the decoded text keeps non-ASCII characters and CRLF'
    Assert-Equal ((Read-RawBody (Join-Path $work 'no-body.bin')).Bytes) 0 'raw body: a missing body file is an empty body'

    # Reservation: exclusive, never overwritten by anyone but its own process, and refused once it exists.
    $rcpt = Join-Path $work 'serve-smoke-20261006-050000-abcdef.json'
    Write-ServeReceipt $rcpt ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'reserved'; pid = $PID }) -Reserve
    Assert-Equal (Refuses { Write-ServeReceipt $rcpt ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'reserved'; pid = $PID }) -Reserve } 'exists') $true 'reserve: an existing reservation refuses a second reservation'
    Write-ServeReceipt $rcpt ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'complete'; pass = $true })
    Assert-Equal ((Get-Content -LiteralPath $rcpt -Raw | ConvertFrom-Json).status) 'complete' 'reserve: this process completes its own reservation in place'
    Assert-Equal (Refuses { Write-ServeReceipt $rcpt ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'complete' }) } 'not this run''s own reservation') $true 'reserve: a completed receipt is never overwritten'
    Assert-Equal (Refuses { Write-ServeReceipt $rcpt ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'reserved'; pid = $PID }) -Reserve } 'exists') $true 'reserve: a completed receipt refuses a new reservation (no second request)'
    $other = Join-Path $work 'serve-other.json'
    [IO.File]::WriteAllText($other, (@{ run = 'smoke-20261006-050000-abcdef'; status = 'reserved'; pid = $PID + 1 } | ConvertTo-Json))
    Assert-Equal (Refuses { Write-ServeReceipt $other ([ordered]@{ run = 'smoke-20261006-050000-abcdef'; status = 'complete' }) } 'not this run''s own reservation') $true 'reserve: another process''s reservation is never completed or overwritten'
    Assert-Equal ((Get-Content -LiteralPath $other -Raw | ConvertFrom-Json).status) 'reserved' 'reserve: a refused completion leaves the reservation intact'
    Assert-Equal (@(Get-ChildItem -LiteralPath $work -Filter 'serve-*.tmp').Count) 0 'reserve: a completed receipt leaves no sibling temp file'
    # A completion whose atomic replace fails keeps the reservation byte-for-byte and removes only its own sibling.
    $locked = Join-Path $work 'serve-smoke-20261006-060000-abcdef.json'
    Write-ServeReceipt $locked ([ordered]@{ run = 'smoke-20261006-060000-abcdef'; status = 'reserved'; pid = $PID }) -Reserve
    $before = [IO.File]::ReadAllBytes($locked)
    Set-ItemProperty -LiteralPath $locked -Name IsReadOnly -Value $true   # makes File.Replace fail on the destination
    $msg = ''; try { Write-ServeReceipt $locked ([ordered]@{ run = 'smoke-20261006-060000-abcdef'; status = 'complete' }) } catch { $msg = $_.Exception.Message }
    Set-ItemProperty -LiteralPath $locked -Name IsReadOnly -Value $false
    Assert-Equal ([bool]$msg) $true 'reserve: a failed atomic replace raises'
    Assert-Equal ([Convert]::ToHexString([IO.File]::ReadAllBytes($locked)) -eq [Convert]::ToHexString($before)) $true 'reserve: a failed completion leaves the reservation byte-for-byte intact'
    Assert-Equal (@(Get-ChildItem -LiteralPath $work -Filter 'serve-smoke-20261006-060000-abcdef.json.*.tmp').Count) 0 'reserve: a failed completion removes only its own sibling'

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
