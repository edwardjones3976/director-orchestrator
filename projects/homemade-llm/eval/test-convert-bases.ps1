# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:21 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | BEGIN]]
# Behavioural checks for convert_bases.ps1 on scratch roots (never real C:\llm, E:\, or G:\).
#   pwsh -File projects\homemade-llm\eval\test-convert-bases.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'convert_bases.ps1'
$fail = [System.Collections.Generic.List[string]]::new()
function Assert([bool]$Cond, [string]$Case) {
    if (-not $Cond) { $fail.Add($Case) } else { Write-Output "ok  $Case" }
}
function Invoke-Convert([string[]]$Arguments) {
    $lines = @(& pwsh -NoProfile -File $script @Arguments 2>&1 | ForEach-Object { "$_" })
    return , $lines, $LASTEXITCODE
}
function Out-Text($Lines) { ($Lines | ForEach-Object { "$_" }) -join "`n" }

function New-ModelFixture([string]$Root, [string]$Repo, [string]$Revision, [string]$WeightBytes) {
    $hub = Join-Path $Root 'hf\hub'
    $snap = Join-Path $hub ("models--" + ($Repo -replace '/', '--')) "snapshots\$Revision"
    New-Item -ItemType Directory -Force -Path $snap | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $snap 'model.safetensors.index.json') | Out-Null
    $weight = Join-Path $snap 'model.safetensors'
    Set-Content -LiteralPath $weight -Value $WeightBytes -NoNewline
    $sha = (Get-FileHash -LiteralPath $weight -Algorithm SHA256).Hash.ToLower()
    $blob = Join-Path $hub "blobs\$($sha.Substring(0, 2))\$sha"
    New-Item -ItemType Directory -Force (Split-Path $blob) | Out-Null
    Copy-Item -LiteralPath $weight -Destination $blob -Force
    [ordered]@{
        path = $weight
        sha256 = $sha
        blob = $blob
        size = (Get-Item -LiteralPath $blob).Length
    }
}

function New-XetBlobFixture([string]$Root, [string]$Repo, [string]$Revision, [string]$WeightBytes, [string]$XetHash) {
    $hub = Join-Path $Root 'hf\hub'
    $snap = Join-Path $hub ("models--" + ($Repo -replace '/', '--')) "snapshots\$Revision"
    New-Item -ItemType Directory -Force -Path $snap | Out-Null
    $blob = Join-Path $hub "blobs\$($XetHash.Substring(0, 2))\$XetHash"
    New-Item -ItemType Directory -Force (Split-Path $blob) | Out-Null
    Set-Content -LiteralPath $blob -Value $WeightBytes -NoNewline
    $lfsSha = (Get-FileHash -LiteralPath $blob -Algorithm SHA256).Hash.ToLower()
    New-Item -ItemType Directory -Force -Path (Join-Path $snap 'model.safetensors.index.json') | Out-Null
    $weight = Join-Path $snap 'model.safetensors'
    Set-Content -LiteralPath $weight -Value 'unresolvable-snapshot-stub' -NoNewline
    [ordered]@{
        path = $weight
        sha256 = $lfsSha
        blob = $blob
        size = (Get-Item -LiteralPath $blob).Length
        mapJson = (@{ 'model.safetensors' = "/hf/hub/blobs/$($XetHash.Substring(0, 2))/$XetHash" } | ConvertTo-Json -Compress)
    }
}

function New-DockerShim([string]$Dir) {
    $shim = @'
$ErrorActionPreference = 'Stop'
$DockerArgs = @($args)
if ($DockerArgs.Count -lt 2 -or $DockerArgs[0] -ne 'run') { exit 0 }
$hfHost = $null
$workHost = $null
for ($i = 0; $i -lt $DockerArgs.Count; $i++) {
    if ($DockerArgs[$i] -eq '-v' -and $i + 1 -lt $DockerArgs.Count) {
        if ($DockerArgs[$i + 1] -match '^(.+):/hf:ro$') { $hfHost = $Matches[1] }
        if ($DockerArgs[$i + 1] -match '^(.+):/work$') { $workHost = $Matches[1] }
    }
}
$bash = $null
for ($j = 0; $j -lt $DockerArgs.Count; $j++) {
    if ($DockerArgs[$j] -eq '-lc' -and $j + 1 -lt $DockerArgs.Count) { $bash = $DockerArgs[$j + 1]; break }
}
# Decode the actual native argument, just as base64 -d does in the container.
if ($bash -cnotmatch '\Aexec 2>&1; set -o pipefail; echo ([A-Za-z0-9+/]+={0,2}) \| base64 -d \| bash\z') {
    throw 'expected encoded bash transport with stderr capture'
}
$bash = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Matches[1]))
if ($bash.Contains("`r")) { throw 'bash script must use LF newlines' }
if ($env:HL_SCRIPT_ECHO) { Write-Output $bash; exit 0 }
$step = if ($bash -match 'readlink -f') { 'blob-map' } else { 'convert' }
if ($env:HL_DOCKER_FAIL -eq $step) {
    # Model container stderr after the wrapper's exec 2>&1.
    Write-Output "container stderr: $step fixture failure"
    exit 2
}
if ($bash -and $bash -match 'readlink -f') {
    if (-not $hfHost) { exit 1 }
    if ($env:HL_BLOB_MAP_JSON) {
        $m = $env:HL_BLOB_MAP_JSON | ConvertFrom-Json
        foreach ($p in $m.PSObject.Properties) { Write-Output ("{0}|{1}" -f $p.Name, $p.Value) }
        Write-Output '__MAPSEP__'
        exit 0
    }
    $snapMatch = [regex]::Match($bash, "/hf/hub/models--[^/]+/snapshots/[^']+")
    if (-not $snapMatch.Success) { exit 1 }
    $snapContainer = $snapMatch.Value
    $snapHost = Join-Path $hfHost ($snapContainer -replace '^/hf/', '' -replace '/', '\')
    $rels = if ($env:HL_WEIGHT_RELS) { $env:HL_WEIGHT_RELS.Split(',') } else { @('model.safetensors') }
    foreach ($rel in $rels) {
        $candidate = Join-Path $snapHost $rel
        $sha = $null
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            try { $sha = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLower() } catch { }
        }
        if (-not $sha) { exit 1 }
        $containerBlob = "/hf/hub/blobs/$($sha.Substring(0,2))/$sha"
        Write-Output ("{0}|{1}" -f $rel, $containerBlob)
    }
    Write-Output '__MAPSEP__'
    exit 0
}
if (-not $workHost) { exit 1 }
$key = $env:HL_CONVERT_MODEL
for ($j = 0; $j -lt $DockerArgs.Count; $j++) {
    if ($DockerArgs[$j] -eq '-e' -and $j + 1 -lt $DockerArgs.Count -and $DockerArgs[$j + 1] -match '^HL_CONVERT_MODEL=(.+)$') { $key = $Matches[1] }
}
if ($env:CONVERT_ORDER_LOG -and $key) { Add-Content -LiteralPath $env:CONVERT_ORDER_LOG -Value $key }
$labels = @{
    'qwen3-8b' = 'Qwen3-8B'; 'qwen3-14b' = 'Qwen3-14B'; 'phi-4' = 'phi-4'
    'mistral-nemo-base-2407' = 'Mistral-Nemo-Base-2407'
}
$label = $labels[$key]
if (-not $label) { exit 1 }
New-Item -ItemType Directory -Force -Path $workHost | Out-Null
$bf16 = Join-Path $workHost "$label-bf16.gguf"
Set-Content -LiteralPath $bf16 -Value ('b' * 64) -NoNewline
$quants = if ($key -eq 'qwen3-8b') { @('Q4_K_M', 'Q8_0') } else { @('Q4_K_M') }
foreach ($q in $quants) { Set-Content -LiteralPath (Join-Path $workHost "$label-$q.gguf") -Value ($q.Substring(0,1) * 64) -NoNewline }
exit 0
'@
    Set-Content -LiteralPath (Join-Path $Dir 'docker.ps1') $shim -Encoding utf8
    Set-Content -LiteralPath (Join-Path $Dir 'docker.cmd') '@pwsh -NoProfile -File "%~dp0docker.ps1" %*' -Encoding ascii
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("convert-bases-test-" + [guid]::NewGuid().ToString('N'))
$shimDir = Join-Path $work 'shim'
$orderLog = Join-Path $work 'order.log'
$offline = Join-Path $work 'offline.json'
$poison = Join-Path $work 'gpu-poisoned.txt'
$oldPath = $env:PATH
try {
    New-Item -ItemType Directory -Force $shimDir, (Join-Path $work 'c'), (Join-Path $work 'e'), (Join-Path $work 'g') | Out-Null
    New-DockerShim $shimDir
    $dockerShim = Join-Path $shimDir 'docker.ps1'

    # Exercise quotes, Unicode and CRLF through the same bounded native argv route.
    . (Join-Path $PSScriptRoot '..\scripts\invoke-bounded.ps1')
    $ast = [Management.Automation.Language.Parser]::ParseFile($script, [ref]$null, [ref]$null)
    $encoder = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'ConvertTo-BashCommand' }, $true)
    . ([scriptblock]::Create($encoder.Extent.Text))
    $sample = "printf '%s|%s\\n' `"a'b`" 'caf$([char]0xe9)'`r`nprintf '%s\\n' '`$HOME'"
    $env:HL_SCRIPT_ECHO = '1'
    $roundtrip = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', $dockerShim, 'run', '-lc', (ConvertTo-BashCommand $sample)) 30)
    Remove-Item Env:HL_SCRIPT_ECHO
    Assert ($roundtrip[-1] -eq '__EXIT__0') 'encoded script crosses bounded native argv'
    Assert (($roundtrip[0..($roundtrip.Count - 2)] -join "`n") -ceq $sample.Replace("`r`n", "`n")) 'decoded script preserves quotes and Unicode with LF newlines'

    $cRoot = Join-Path $work 'c'
    $eRoot = Join-Path $work 'e\hf-bases-2026-09'
    $gRoot = Join-Path $work 'g\llm-convert-tmp'

    # --- dry-run touches nothing ---
    $before = @(Get-ChildItem -LiteralPath $work -Recurse -File -Force).Count
    $out, $code = Invoke-Convert @('-CHotRoot', $cRoot, '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot, '-GpuPoisonPath', $poison, '-DockerPath', $dockerShim, '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0)
    $after = @(Get-ChildItem -LiteralPath $work -Recurse -File -Force).Count
    Assert ($code -eq 0) 'dry-run exits 0'
    Assert ($before -eq $after) 'dry-run creates no new files'
    Assert ((Out-Text $out) -match 'PLAN model=qwen3-8b') 'dry-run plans first model'

    # --- one-at-a-time catalog order (dry-run PLAN lines) ---
    $expectedOrder = @('qwen3-8b', 'qwen3-14b', 'phi-4', 'mistral-nemo-base-2407')
    $seen = [regex]::Matches((Out-Text $out), 'PLAN model=([^\s]+)') | ForEach-Object { $_.Groups[1].Value }
    Assert (($seen -join ',') -eq ($expectedOrder -join ',')) 'model order in dry-run plan'

    # --- storage guard stops before docker ---
    $out, $code = Invoke-Convert @(
        '-CHotRoot', $cRoot, '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot,
        '-GpuPoisonPath', $poison, '-ProbeCFreeBytes', ([long](100GB)), '-ProbeLlmBytes', 0,
        '-Execute', '-Model', 'Qwen/Qwen3-8B', '-OfflineExpected', $offline, '-DockerPath', $dockerShim
    )
    Assert ($code -ne 0) 'storage guard fails execute when C: below floor'
    Assert ((Out-Text $out) -match 'storage guard') 'storage guard message present'

    # --- poison marker refuses ---
    Set-Content -LiteralPath $poison 'test poison'
    $poisonOut, $poisonCode = Invoke-Convert @('-CHotRoot', $cRoot, '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot, '-GpuPoisonPath', $poison, '-DockerPath', $dockerShim, '-ProbeCFreeBytes', ([long](200GB)))
    Remove-Item -LiteralPath $poison -Force
    Assert ($poisonCode -ne 0) 'poison marker refuses run'
    Assert ((Out-Text $poisonOut) -match 'GPU is poisoned') 'poison message present'

    # --- execute one model: receipt fields + order log ---
    $fx = New-ModelFixture $cRoot 'Qwen/Qwen3-8B' 'b968826d9c46dd6066d109eabc6255188de91218' 'weight-bytes'
    $sha = $fx.sha256
    $offlineBody = @{
        'Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218' = @{
            files = @(@{ path = 'model.safetensors'; sha256 = $sha; size = $fx.size })
        }
    } | ConvertTo-Json -Depth 5
    Set-Content -LiteralPath $offline $offlineBody -Encoding utf8
    foreach ($step in @('blob-map', 'convert')) {
        $env:HL_DOCKER_FAIL = $step
        $mode = if ($step -eq 'blob-map') { '-Preflight' } else { '-Execute' }
        $out, $code = Invoke-Convert @(
            $mode, '-Model', 'Qwen/Qwen3-8B',
            '-CHotRoot', $cRoot, '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot,
            '-GpuPoisonPath', $poison, '-OfflineExpected', $offline,
            '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
        )
        Remove-Item Env:HL_DOCKER_FAIL
        Assert ($code -ne 0) "$step container failure exits nonzero"
        Assert ((Out-Text $out) -match "failed \(__EXIT__2\): container stderr: $step fixture failure") "$step error includes container stderr"
    }
    $env:CONVERT_ORDER_LOG = $orderLog
    $out, $code = Invoke-Convert @(
        '-Execute', '-Model', 'Qwen/Qwen3-8B',
        '-CHotRoot', $cRoot, '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $offline,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
    )
    Remove-Item Env:CONVERT_ORDER_LOG -ErrorAction SilentlyContinue
    if ($code -ne 0) { Write-Output "execute output: $(Out-Text $out)" }
    Assert ($code -eq 0) 'execute single model exits 0'
    Assert (Test-Path -LiteralPath $orderLog) 'docker shim recorded order'
    Assert ((Get-Content -LiteralPath $orderLog | Select-Object -First 1) -eq 'qwen3-8b') 'execute runs docker for the requested model'
    $receipts = Get-ChildItem (Join-Path $cRoot 'reports') -Filter 'm1-convert-qwen3-8b-*.json'
    Assert ($receipts.Count -eq 1) 'one receipt written'
    $rc = Get-Content -LiteralPath $receipts[0].FullName -Raw | ConvertFrom-Json
    foreach ($field in @('model', 'repo', 'revision', 'gguf', 'archived', 'removed', 'storage', 'tools')) {
        Assert ($rc.PSObject.Properties.Name -contains $field) "receipt has $field"
    }
    Assert ($rc.gguf.Count -ge 2) 'receipt lists final GGUF artifacts'
    Assert ($rc.PSObject.Properties.Name -contains 'dangling_links_left') 'receipt notes dangling snapshot links'
    Assert (-not (Test-Path -LiteralPath $fx.blob)) 'verified archive removed shared blob from C:'
    Assert (Test-Path -LiteralPath $fx.path) 'snapshot link left on C: after blob removal'
    Assert (Test-Path -LiteralPath (Join-Path $cRoot 'models\m1\qwen3-8b\Qwen3-8B-Q4_K_M.gguf')) 'final GGUF on C: m1 path'

    # --- projected storage guard blocks GGUF copy onto C: ---
    $cProj = Join-Path $work 'c-proj'
    $gProj = Join-Path $work 'g-proj'
    New-Item -ItemType Directory -Force $cProj, $gProj, (Join-Path $work 'e-proj') | Out-Null
    $fxP = New-ModelFixture $cProj 'Qwen/Qwen3-8B' 'b968826d9c46dd6066d109eabc6255188de91218' 'proj-bytes'
    $offlineProj = @{
        'Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218' = @{
            files = @(@{ path = 'model.safetensors'; sha256 = $fxP.sha256; size = $fxP.size })
        }
    } | ConvertTo-Json -Depth 5
    $offlineProjPath = Join-Path $work 'offline-proj.json'
    Set-Content -LiteralPath $offlineProjPath $offlineProj -Encoding utf8
    $out, $code = Invoke-Convert @(
        '-Execute', '-Model', 'Qwen/Qwen3-8B',
        '-CHotRoot', $cProj, '-EArchiveRoot', (Join-Path $work 'e-proj'), '-GConvertRoot', $gProj,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $offlineProjPath,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-MaxLlmGiB', 0.00000001,
        '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
    )
    Assert ($code -ne 0) 'projected llm cap fails execute'
    Assert ((Out-Text $out) -match 'would be') 'projected storage guard message present'
    Assert (-not (Test-Path -LiteralPath (Join-Path $cProj 'models\m1\qwen3-8b\Qwen3-8B-Q4_K_M.gguf'))) 'projected guard copied no GGUF onto C:'

    # --- docker blob map archives xet-named blobs; dangling snapshot stays ---
    $cXet = Join-Path $work 'c-xet'
    $gXet = Join-Path $work 'g-xet'
    New-Item -ItemType Directory -Force $cXet, $gXet, (Join-Path $work 'e-xet') | Out-Null
    $xetHash = ('d5' + ('0' * 62)).Substring(0, 64)
    $fxX = New-XetBlobFixture $cXet 'microsoft/phi-4' '2db69c1c3e91a05d2c64a3185acfbaf36f744e25' 'xet-weight' $xetHash
    $offlineX = @{
        'microsoft/phi-4@2db69c1c3e91a05d2c64a3185acfbaf36f744e25' = @{
            files = @(@{ path = 'model.safetensors'; sha256 = $fxX.sha256; size = $fxX.size })
        }
    } | ConvertTo-Json -Depth 5
    $offlineXPath = Join-Path $work 'offline-xet.json'
    Set-Content -LiteralPath $offlineXPath $offlineX -Encoding utf8
    $env:HL_BLOB_MAP_JSON = $fxX.mapJson
    $out, $code = Invoke-Convert @(
        '-Execute', '-Model', 'microsoft/phi-4',
        '-CHotRoot', $cXet, '-EArchiveRoot', (Join-Path $work 'e-xet'), '-GConvertRoot', $gXet,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $offlineXPath,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
    )
    Remove-Item Env:HL_BLOB_MAP_JSON -ErrorAction SilentlyContinue
    Assert ($code -eq 0) 'xet blob map execute exits 0'
    Assert (-not (Test-Path -LiteralPath $fxX.blob)) 'xet archive removed mapped blob from C:'
    Assert (Test-Path -LiteralPath $fxX.path) 'xet snapshot link left dangling on C:'
    $archPath = Join-Path (Join-Path $work 'e-xet') 'models--microsoft--phi-4\snapshots\2db69c1c3e91a05d2c64a3185acfbaf36f744e25\model.safetensors'
    Assert (Test-Path -LiteralPath $archPath) 'xet weight archived under E: snapshot path'
    Assert ((Get-FileHash -LiteralPath $archPath -Algorithm SHA256).Hash.ToLower() -eq $fxX.sha256) 'E: archive matches LFS sha256'

    # --- mapped blob hash mismatch removes nothing ---
    $cBadMap = Join-Path $work 'c-badmap'
    $gBadMap = Join-Path $work 'g-badmap'
    New-Item -ItemType Directory -Force $cBadMap, $gBadMap, (Join-Path $work 'e-badmap') | Out-Null
    $fxM = New-XetBlobFixture $cBadMap 'mistralai/Mistral-Nemo-Base-2407' 'a4477a2f977929a969745b69bbd62e03043551a5' 'badmap' ('e1' + ('0' * 62))
    $offlineBadMap = @{
        'mistralai/Mistral-Nemo-Base-2407@a4477a2f977929a969745b69bbd62e03043551a5' = @{
            files = @(@{ path = 'model.safetensors'; sha256 = ('f' * 64); size = $fxM.size })
        }
    } | ConvertTo-Json -Depth 5
    $offlineBadMapPath = Join-Path $work 'offline-badmap.json'
    Set-Content -LiteralPath $offlineBadMapPath $offlineBadMap -Encoding utf8
    $env:HL_BLOB_MAP_JSON = $fxM.mapJson
    $out, $code = Invoke-Convert @(
        '-Execute', '-Model', 'mistralai/Mistral-Nemo-Base-2407',
        '-CHotRoot', $cBadMap, '-EArchiveRoot', (Join-Path $work 'e-badmap'), '-GConvertRoot', $gBadMap,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $offlineBadMapPath,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
    )
    Remove-Item Env:HL_BLOB_MAP_JSON -ErrorAction SilentlyContinue
    Assert ($code -ne 0) 'mapped blob hash mismatch fails execute'
    Assert (Test-Path -LiteralPath $fxM.blob) 'mapped blob hash mismatch removed nothing from C:'

    # --- hash mismatch on archive: C: weight remains ---
    $c2 = Join-Path $work 'c2'
    New-Item -ItemType Directory -Force $c2 | Out-Null
    $fx2 = New-ModelFixture $c2 'microsoft/phi-4' '2db69c1c3e91a05d2c64a3185acfbaf36f744e25' 'other'
    $badOffline = @{
        'microsoft/phi-4@2db69c1c3e91a05d2c64a3185acfbaf36f744e25' = @{
            files = @(@{ path = 'model.safetensors'; sha256 = ('0' * 64) })
        }
    } | ConvertTo-Json -Depth 5
    $badFile = Join-Path $work 'offline-bad.json'
    Set-Content -LiteralPath $badFile $badOffline -Encoding utf8
    $g2 = Join-Path $work 'g2'
    $out, $code = Invoke-Convert @(
        '-Execute', '-Model', 'microsoft/phi-4',
        '-CHotRoot', $c2, '-EArchiveRoot', (Join-Path $work 'e2'), '-GConvertRoot', $g2,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $badFile,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 30, '-DockerPath', $dockerShim
    )
    Assert ($code -ne 0) 'hash mismatch fails execute'
    Assert (Test-Path -LiteralPath $fx2.path) 'hash mismatch leaves C: weight in place'

    # --- execute all four: shim order log ---
    $cAll = Join-Path $work 'c-all'
    $gAll = Join-Path $work 'g-all'
    New-Item -ItemType Directory -Force $cAll, $gAll, (Join-Path $work 'e-all') | Out-Null
    $offlineAll = @{}
    foreach ($pair in @(
            , @('Qwen/Qwen3-8B', 'b968826d9c46dd6066d109eabc6255188de91218', 'w8')
            , @('Qwen/Qwen3-14B', '40c069824f4251a91eefaf281ebe4c544efd3e18', 'w14')
            , @('microsoft/phi-4', '2db69c1c3e91a05d2c64a3185acfbaf36f744e25', 'wp')
            , @('mistralai/Mistral-Nemo-Base-2407', 'a4477a2f977929a969745b69bbd62e03043551a5', 'wm')
        )) {
        $built = New-ModelFixture $cAll $pair[0] $pair[1] $pair[2]
        $offlineAll["$($pair[0])@$($pair[1])"] = @{ files = @(@{ path = 'model.safetensors'; sha256 = $built.sha256; size = $built.size }) }
    }
    $offlineAllPath = Join-Path $work 'offline-all.json'
    $offlineAll | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $offlineAllPath -Encoding utf8
    $orderAll = Join-Path $work 'order-all.log'
    $env:CONVERT_ORDER_LOG = $orderAll
    $out, $code = Invoke-Convert @(
        '-Execute',
        '-CHotRoot', $cAll, '-EArchiveRoot', (Join-Path $work 'e-all'), '-GConvertRoot', $gAll,
        '-GpuPoisonPath', $poison, '-OfflineExpected', $offlineAllPath,
        '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0, '-DockerTimeoutSec', 60, '-DockerPath', $dockerShim
    )
    Remove-Item Env:CONVERT_ORDER_LOG -ErrorAction SilentlyContinue
    Assert ($code -eq 0) 'execute all models exits 0'
    $logged = Get-Content -LiteralPath $orderAll
    Assert (($logged -join ',') -eq ($expectedOrder -join ',')) 'execute processes models one-at-a-time in catalog order'
} finally {
    Remove-Item Env:HL_SCRIPT_ECHO -ErrorAction SilentlyContinue
    Remove-Item Env:HL_DOCKER_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:CONVERT_ORDER_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:HL_BLOB_MAP_JSON -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fail.Count) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-convert-bases: all cases passed'
exit 0
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:25 UTC | END]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | END]]
