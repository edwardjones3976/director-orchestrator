# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:55 UTC | BEGIN]]
# M0.4: serve the M0.3 smoke GGUF through LM Studio and prove /v1 works from Windows and from a container.
#   .\serve-smoke.ps1                      newest C:\llm\models\smoke\*.gguf
#   .\serve-smoke.ps1 -Gguf <path>
# The model is imported as a hard link (no second copy) and loaded with a TTL so it frees the GPU itself.
# Exit 0 = both calls returned HTTP 200 with schema-valid JSON; 1 otherwise.
[CmdletBinding()]
param(
    [string]$Gguf,
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [int]$TtlSeconds = 900
)
$ErrorActionPreference = 'Stop'
if (-not $Gguf) { $Gguf = (Get-ChildItem 'C:\llm\models\smoke\*.gguf' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName }
if (-not $Gguf -or -not (Test-Path -LiteralPath $Gguf -PathType Leaf)) { Write-Error 'no smoke GGUF found; run train-smoke.ps1 first' }

# lms prints status on stderr; native exit codes are checked explicitly.
lms server start 2>&1 | Out-Null
$known = (lms ls --json 2>$null | ConvertFrom-Json) | Where-Object { $_.path -like "homemade-llm/smoke/$(Split-Path $Gguf -Leaf)" }
if (-not $known) {
    lms import $Gguf -L -y --user-repo homemade-llm/smoke 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Error 'lms import failed' }
    # LM Studio indexes a new import a few seconds later; poll instead of failing on the first look.
    for ($i = 0; $i -lt 20 -and -not $known; $i++) {
        Start-Sleep -Seconds 1
        $known = (lms ls --json 2>$null | ConvertFrom-Json) | Where-Object { $_.path -like "homemade-llm/smoke/$(Split-Path $Gguf -Leaf)" }
    }
}
if (-not $known) { Write-Error "imported model not listed by lms: $Gguf" }
$loaded = (lms ps --json 2>$null | ConvertFrom-Json) | Where-Object { $_.identifier -eq 'smoke' }
# An older smoke build may still hold the identifier; replace it so this run tests the GGUF it was given.
if ($loaded -and $loaded.path -ne $known.path) { lms unload smoke 2>&1 | Out-Null; $loaded = $null }
if (-not $loaded) {
    lms load $known.modelKey --identifier smoke --ttl $TtlSeconds -y 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error 'lms load failed' }
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
$reqFile = Join-Path 'C:\llm\reports' ("serve-smoke-request-{0}.json" -f [guid]::NewGuid().ToString('N').Substring(0, 8))

# PASS needs curl exit 0, HTTP 200, exactly the two keys, and both values non-empty strings (the schema's types).
function Test-Reply([string]$Label, [string[]]$Lines, [int]$CurlExit) {
    $ok = $false; $content = ''; $status = if ($Lines.Count) { $Lines[-1] } else { '' }
    try {
        $body = $Lines[0..($Lines.Count - 2)] -join "`n"
        $content = ($body | ConvertFrom-Json).choices[0].message.content
        $obj = $content | ConvertFrom-Json
        $keys = (@($obj.PSObject.Properties.Name | Sort-Object) -join ',') -eq 'objective,task_id'
        $types = ($obj.task_id -is [string]) -and ($obj.objective -is [string]) -and $obj.task_id -and $obj.objective
        $ok = ($CurlExit -eq 0) -and ($status -eq '200') -and $keys -and $types
    } catch { $ok = $false }
    [pscustomobject]@{ caller = $Label; curl = $CurlExit; http = $status; schema_valid = $ok; content = $content }
}

try {
    [IO.File]::WriteAllText($reqFile, $request)   # inside try so a partial write is still cleaned up
    $hostLines = @(curl.exe -s -w "`n%{http_code}" --max-time 120 http://127.0.0.1:1234/v1/chat/completions -H 'Content-Type: application/json' --data "@$reqFile")
    $results = @(Test-Reply 'windows' $hostLines $LASTEXITCODE)

    # From a container: Docker Desktop forwards host.docker.internal to the host loopback, so LM Studio
    # can stay bound to 127.0.0.1 (no "serve on local network").
    $leaf = Split-Path $reqFile -Leaf
    $ctrLines = @(docker run --rm -v 'C:\llm\reports:/r:ro' --entrypoint curl $Image -s -w "`n%{http_code}" --max-time 120 `
        http://host.docker.internal:1234/v1/chat/completions -H 'Content-Type: application/json' --data "@/r/$leaf")
    $results += Test-Reply 'container' $ctrLines $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $reqFile) { [IO.File]::Delete($reqFile) }
}

# The API must not be reachable from the network: every listener on 1234 has to be loopback.
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 1234 -ErrorAction SilentlyContinue)
$exposed = @($listeners | Where-Object { $_.LocalAddress -notin '127.0.0.1', '::1' })
$loopbackOnly = $listeners.Count -gt 0 -and $exposed.Count -eq 0
$results | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
Write-Output "port 1234 listeners: $(($listeners.LocalAddress | Sort-Object -Unique) -join ', ') (loopback only: $loopbackOnly)"
$pass = (@($results | Where-Object schema_valid).Count -eq 2) -and $loopbackOnly
Write-Output "serve-smoke: $(if ($pass) { 'PASS' } else { 'FAIL' }) (model $($known.modelKey), ttl ${TtlSeconds}s)"
exit ([int](-not $pass))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:55 UTC | END]]
