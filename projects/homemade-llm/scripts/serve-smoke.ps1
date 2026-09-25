# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:55 UTC | BEGIN]]
# M0.4: serve the M0.3 smoke GGUF through LM Studio and prove /v1 works from Windows and from a container.
#   .\serve-smoke.ps1                      newest <HotRoot>\models\smoke\*.gguf
#   .\serve-smoke.ps1 -Gguf <path> [-HotRoot <root>]
# The model is imported as a hard link (no second copy) and loaded with a TTL so it frees the GPU itself.
# Exit 0 = both calls returned HTTP 200 with schema-valid JSON and LM Studio is loopback-only; 1 otherwise.
[CmdletBinding()]
param(
    [string]$Gguf,
    [string]$HotRoot = 'C:\llm',   # same default and meaning as train-smoke.ps1 -HotRoot
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [ValidateRange(1, 86400)][int]$TtlSeconds = 900,   # must be positive: 0 would mean no GPU-release deadline
    [string]$LmsModelsDir = (Join-Path $env:USERPROFILE '.lmstudio\models')
)
$ErrorActionPreference = 'Stop'
$reports = Join-Path $HotRoot 'reports'

# True only when every listener on the port is loopback, and there is at least one.
function Test-LoopbackOnly($Listeners) {
    $l = @($Listeners)
    ($l.Count -gt 0) -and (@($l | Where-Object { $_.LocalAddress -notin '127.0.0.1', '::1' }).Count -eq 0)
}

# True only when every process listening on the port is LM Studio, and there is at least one: a different
# OpenAI-compatible server on 1234 would otherwise answer the probes and produce a false PASS.
function Test-LmStudioOwner($Processes) {
    $p = @($Processes)
    ($p.Count -gt 0) -and (@($p | Where-Object { $_.ProcessName -ne 'LM Studio' }).Count -eq 0)
}

# True only when `lms ps` shows exactly one 'smoke' model, loaded from the expected catalog path with the
# requested TTL; this is what proves the reload happened and the GPU-release bound is in force.
function Test-LoadedModel($Loaded, [string]$Path, [int]$TtlSeconds) {
    $smoke = @($Loaded | Where-Object { $_.identifier -eq 'smoke' })
    ($smoke.Count -eq 1) -and ($smoke[0].path -eq $Path) -and ($null -ne $smoke[0].ttlMs) -and
        ([long]$smoke[0].ttlMs -eq [long]$TtlSeconds * 1000)
}

# True only when both files have identical content (the import is a hard link, so this is normally instant
# to confirm; a same-named stale import with different bytes fails it).
function Test-SameContent([string]$A, [string]$B) {
    (Test-Path -LiteralPath $A -PathType Leaf) -and (Test-Path -LiteralPath $B -PathType Leaf) -and
    ((Get-Item -LiteralPath $A).Length -eq (Get-Item -LiteralPath $B).Length) -and
    ((Get-FileHash -LiteralPath $A).Hash -eq (Get-FileHash -LiteralPath $B).Hash)
}
if (-not $Gguf) { $Gguf = (Get-ChildItem (Join-Path $HotRoot 'models\smoke\*.gguf') | Sort-Object LastWriteTime | Select-Object -Last 1).FullName }
if (-not $Gguf -or -not (Test-Path -LiteralPath $Gguf -PathType Leaf)) { Write-Error 'no smoke GGUF found; run train-smoke.ps1 first' }
if (-not (Test-Path -LiteralPath $reports -PathType Container)) { Write-Error "$reports does not exist (run preflight.ps1 -Init)" }

# One GPU job at a time, shared with train-smoke.ps1: overlapping serve runs could probe each other's GGUF
# through the one 'smoke' identifier, and a training run needs the whole GPU. Released when this process exits.
$lock = [System.Threading.Mutex]::new($false, 'Global\homemade-llm-gpu')
try { $held = $lock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { Write-Error 'another GPU job (train-smoke or serve-smoke) is running; not starting' }
# Everything below holds the GPU lock; finally releases it on every exit (error, Ctrl+C, exit), which matters
# when this script runs inside a longer-lived PowerShell session rather than its own process.
try {

    . (Join-Path $PSScriptRoot 'invoke-bounded.ps1')   # Invoke-Bounded: deadline + process-tree kill
    # lms through Invoke-Bounded: returns its stdout lines and sets $lmsExit (124 when it timed out).
    function Invoke-Lms([string[]]$A, [int]$Seconds = 60) {
        $r = @(Invoke-Bounded 'lms' $A $Seconds)
        $script:lmsExit = if ($r[-1] -match '^__EXIT__(\d+)$') { [int]$Matches[1] } else { 124 }
        @($r | Select-Object -SkipLast 1)
    }

    Invoke-Lms @('server', 'start') | Out-Null
    # Any failure to confirm the server stops it before aborting: it may already be listening, even beyond loopback.
    if ($lmsExit -ne 0) { Invoke-Lms @('server', 'stop') | Out-Null; Write-Error "lms server start failed (exit $lmsExit; server stopped); is another service using port 1234?" }
    $status = try { (Invoke-Lms @('server', 'status', '--json')) -join "`n" | ConvertFrom-Json } catch { $null }
    if (-not ($status.running -eq $true -and $status.port -eq 1234)) {
        Invoke-Lms @('server', 'stop') | Out-Null
        Write-Error "lms server status did not confirm running on 1234 (server stopped): $($status | ConvertTo-Json -Compress)"
    }
    # Refuse before anything is loaded if the API is reachable beyond loopback ("serve on local network").
    $preLoad = Get-NetTCPConnection -State Listen -LocalPort 1234 -ErrorAction SilentlyContinue
    if (-not (Test-LoopbackOnly $preLoad)) {
        Invoke-Lms @('server', 'stop') | Out-Null
        Write-Error "LM Studio is listening beyond loopback on 1234 ($(($preLoad.LocalAddress | Sort-Object -Unique) -join ', ')); server stopped, nothing loaded. Turn off 'serve on local network'."
    }
    $owners = @($preLoad | ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue })
    if (-not (Test-LmStudioOwner $owners)) {
        Write-Error "port 1234 is not served by LM Studio (listener process: $(($owners.ProcessName | Sort-Object -Unique) -join ', ')); nothing loaded"
    }
    $known = ((Invoke-Lms @('ls', '--json')) -join "`n" | ConvertFrom-Json) | Where-Object { $_.path -like "homemade-llm/smoke/$(Split-Path $Gguf -Leaf)" }
    if (-not $known) {
        Invoke-Lms @('import', $Gguf, '-L', '-y', '--user-repo', 'homemade-llm/smoke') 300 | Out-Host
        if ($lmsExit -ne 0) { Write-Error "lms import failed (exit $lmsExit)" }
        # LM Studio indexes a new import a few seconds later; poll instead of failing on the first look.
        for ($i = 0; $i -lt 20 -and -not $known; $i++) {
            Start-Sleep -Seconds 1
            $known = ((Invoke-Lms @('ls', '--json')) -join "`n" | ConvertFrom-Json) | Where-Object { $_.path -like "homemade-llm/smoke/$(Split-Path $Gguf -Leaf)" }
        }
    }
    if (-not $known) { Write-Error "imported model not listed by lms: $Gguf" }
    # The catalog entry must be this exact GGUF, not an older import that merely shares the file name.
    $imported = Join-Path $LmsModelsDir ($known.path -replace '/', '\')
    if (-not (Test-SameContent $Gguf $imported)) { Write-Error "LM Studio's $($known.path) is not the requested GGUF (content differs)" }
    # Always (re)load, so the requested TTL applies even when this model is already resident.
    if (((Invoke-Lms @('ps', '--json')) -join "`n" | ConvertFrom-Json) | Where-Object { $_.identifier -eq 'smoke' }) { Invoke-Lms @('unload', 'smoke') | Out-Null }
    Invoke-Lms @('load', $known.modelKey, '--identifier', 'smoke', '--ttl', $TtlSeconds, '-y') 300 | Out-Null
    # If the load or its TTL cannot be verified, the GPU-release deadline cannot be trusted: unload before failing.
    if ($lmsExit -ne 0) { Invoke-Lms @('unload', 'smoke') | Out-Null; Write-Error "lms load failed (exit $lmsExit; smoke unloaded)" }
    if (-not (Test-LoadedModel ((Invoke-Lms @('ps', '--json')) -join "`n" | ConvertFrom-Json) $known.path $TtlSeconds)) {
        Invoke-Lms @('unload', 'smoke') | Out-Null
        Write-Error "after loading, lms ps does not show 'smoke' = $($known.path) with ttl ${TtlSeconds}s (smoke unloaded)"
    }

    # The request pins the output to a JSON schema (LM Studio honours response_format on chat/completions).
    $request = @{
        model = 'smoke'; temperature = 0; max_tokens = 96
        messages = @(
            @{ role = 'system'; content = 'Reply with one JSON object with keys task_id and objective. No prose.' },
            @{ role = 'user'; content = 'Fix the preflight floor (item 5) /no_think' }
        )
        response_format = @{ type = 'json_schema'; json_schema = @{ name = 'smoke_packet'; strict = $true; schema = @{
            type = 'object'; additionalProperties = $false; required = @('task_id', 'objective')
            properties = @{ task_id = @{ type = 'string' }; objective = @{ type = 'string' } } } } }
    } | ConvertTo-Json -Depth 8 -Compress
    $reqFile = Join-Path $reports ("serve-smoke-request-{0}.json" -f [guid]::NewGuid().ToString('N').Substring(0, 8))

    # PASS needs curl exit 0, HTTP 200, exactly the two keys, and both values non-empty strings (the schema's types).
    function Test-Reply([string]$Label, [string[]]$Lines, [int]$CurlExit) {
        $ok = $false; $content = ''; $status = if ($Lines.Count) { $Lines[-1] } else { '' }
        try {
            $body = $Lines[0..($Lines.Count - 2)] -join "`n"
            $content = ($body | ConvertFrom-Json).choices[0].message.content
            # -NoEnumerate keeps the top-level shape: a one-element array must not unwrap into a passing object.
            $obj = ConvertFrom-Json -InputObject $content -NoEnumerate
            if ($obj -isnot [System.Management.Automation.PSCustomObject]) { throw 'reply is not a JSON object' }
            # JSON property names are case-sensitive; PowerShell's -eq and member access are not, so compare
            # names with -ceq and read values by exact name.
            $names = @($obj.PSObject.Properties.Name | Sort-Object -CaseSensitive)
            $keys = ($names -join ',') -ceq 'objective,task_id'
            $val = { param($n) ($obj.PSObject.Properties | Where-Object { $_.Name -ceq $n } | Select-Object -First 1).Value }
            $tid = & $val 'task_id'; $obv = & $val 'objective'
            $types = ($tid -is [string]) -and ($obv -is [string]) -and $tid -and $obv
            $ok = ($CurlExit -eq 0) -and ($status -eq '200') -and $keys -and $types
        } catch { $ok = $false }
        # Model output is untrusted: escape control characters (ANSI/OSC sequences) before it reaches a terminal.
        $shown = [regex]::Replace([string]$content, '\p{Cc}', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
        [pscustomobject]@{ caller = $Label; curl = $CurlExit; http = $status; schema_valid = $ok; content = $shown }
    }

    try {
        [IO.File]::WriteAllText($reqFile, $request)   # inside try so a partial write is still cleaned up
        $hostLines = @(curl.exe -s -w "`n%{http_code}" --max-time 120 http://127.0.0.1:1234/v1/chat/completions -H 'Content-Type: application/json' --data "@$reqFile")
        $results = @(Test-Reply 'windows' $hostLines $LASTEXITCODE)

        # From a container: Docker Desktop forwards host.docker.internal to the host loopback, so LM Studio
        # can stay bound to 127.0.0.1 (no "serve on local network").
        $leaf = Split-Path $reqFile -Leaf
        $ctrName = 'hl-serve-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
        $r = @(Invoke-Bounded 'docker' @('run', '--rm', '--name', $ctrName, '-v', "${reports}:/r:ro", '--entrypoint', 'curl', $Image,
            '-s', '-w', "`n%{http_code}", '--max-time', '120', 'http://host.docker.internal:1234/v1/chat/completions',
            '-H', 'Content-Type: application/json', '--data', "@/r/$leaf") 180)
        if ($r[-1] -eq '__EXIT__timeout') { Invoke-Bounded 'docker' @('kill', $ctrName) 20 | Out-Null }
        $ctrExit = if ($r[-1] -match '^__EXIT__(\d+)$') { [int]$Matches[1] } else { 124 }
        $results += Test-Reply 'container' @($r | Select-Object -SkipLast 1) $ctrExit
    } finally {
        if (Test-Path -LiteralPath $reqFile) { [IO.File]::Delete($reqFile) }
    }

    # The API must not be reachable from the network: every listener on 1234 has to be loopback.
    # Checked again at the end in case the binding changed during the run.
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 1234 -ErrorAction SilentlyContinue)
    $loopbackOnly = Test-LoopbackOnly $listeners
    if (-not $loopbackOnly) {
        # Exposure appeared during the run: take the model and the server down now, not at TTL expiry.
        Invoke-Lms @('unload', 'smoke') | Out-Null
        Invoke-Lms @('server', 'stop') | Out-Null
    }
    $results | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
    Write-Output "port 1234 listeners: $(($listeners.LocalAddress | Sort-Object -Unique) -join ', ') (loopback only: $loopbackOnly)"
    $pass = (@($results | Where-Object schema_valid).Count -eq 2) -and $loopbackOnly
    Write-Output "serve-smoke: $(if ($pass) { 'PASS' } else { 'FAIL' }) (model $($known.modelKey), ttl ${TtlSeconds}s)"
    exit ([int](-not $pass))
} finally {
    $lock.ReleaseMutex(); $lock.Dispose()
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:55 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
