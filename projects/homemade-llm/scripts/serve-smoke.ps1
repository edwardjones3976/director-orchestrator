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
[IO.File]::WriteAllText($reqFile, $request)

function Test-Reply([string]$Label, [string]$Body, [string]$Status) {
    $ok = $false; $content = ''
    try {
        $content = ($Body | ConvertFrom-Json).choices[0].message.content
        $obj = $content | ConvertFrom-Json
        $ok = ($Status -eq '200') -and (@($obj.PSObject.Properties.Name | Sort-Object) -join ',') -eq 'objective,task_id'
    } catch { $ok = $false }
    [pscustomobject]@{ caller = $Label; http = $Status; schema_valid = $ok; content = $content }
}

try {
    $hostOut = curl.exe -s -w "`n%{http_code}" --max-time 120 http://127.0.0.1:1234/v1/chat/completions -H 'Content-Type: application/json' --data "@$reqFile"
    $hostLines = @($hostOut); $results = @(Test-Reply 'windows' ($hostLines[0..($hostLines.Count - 2)] -join "`n") $hostLines[-1])

    # From a container: Docker Desktop forwards host.docker.internal to the host loopback, so LM Studio
    # can stay bound to 127.0.0.1 (no "serve on local network").
    $leaf = Split-Path $reqFile -Leaf
    $ctrOut = docker run --rm -v 'C:\llm\reports:/r:ro' --entrypoint curl $Image -s -w "`n%{http_code}" --max-time 120 `
        http://host.docker.internal:1234/v1/chat/completions -H 'Content-Type: application/json' --data "@/r/$leaf"
    $ctrLines = @($ctrOut); $results += Test-Reply 'container' ($ctrLines[0..($ctrLines.Count - 2)] -join "`n") $ctrLines[-1]
} finally {
    [IO.File]::Delete($reqFile)
}

$results | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
$pass = @($results | Where-Object schema_valid).Count -eq 2
Write-Output "serve-smoke: $(if ($pass) { 'PASS' } else { 'FAIL' }) (model $($known.modelKey), ttl ${TtlSeconds}s)"
exit ([int](-not $pass))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:55 UTC | END]]
