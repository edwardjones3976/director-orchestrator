# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:21 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | BEGIN]]
# Behavioural checks for convert_bases.ps1 on scratch roots (never real C:\llm, E:\, or G:\).
#   pwsh -File projects\homemade-llm\eval\test-convert-bases.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'convert_bases.ps1'
$shellExe = Join-Path $PSHOME $(if ($PSVersionTable.PSVersion.Major -ge 7) { 'pwsh.exe' } else { 'powershell.exe' })
$fail = [System.Collections.Generic.List[string]]::new()
function Assert([bool]$Cond, [string]$Case) {
    if (-not $Cond) { $fail.Add($Case) } else { Write-Output "ok  $Case" }
}
function Invoke-Convert([string[]]$Arguments) {
    # Native stderr is an ErrorRecord in Windows PowerShell; retain it for failure assertions.
    $ErrorActionPreference = 'Continue'
    $lines = @(& $shellExe -NoProfile -File $script @Arguments 2>&1 | ForEach-Object { "$_" })
    return , $lines, $LASTEXITCODE
}
function Out-Text($Lines) { ($Lines | ForEach-Object { "$_" }) -join "`n" }

function New-ModelFixture([string]$Root, [string]$Repo, [string]$Revision, [string]$WeightBytes) {
    $hub = Join-Path $Root 'hf\hub'
    $snap = Join-Path (Join-Path $hub ("models--" + ($Repo -replace '/', '--'))) "snapshots\$Revision"
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
    $snap = Join-Path (Join-Path $hub ("models--" + ($Repo -replace '/', '--'))) "snapshots\$Revision"
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

    # Inject faults only into a scratch copy; production has no fault switches.
    $testHooks = @'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    # Synthetic transport only: the unchanged shared helper needs .NET's ArgumentList/Kill(bool).
    function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
        if ($A[2] -ne $DockerPath) { throw 'test transport permits only the Docker fixture' }
        $shimArgs = @($A | Select-Object -Skip 3)
        & $DockerPath @shimArgs
        "__EXIT__$LASTEXITCODE"
    }
}
function Remove-Item {
    [CmdletBinding()]param([string]$LiteralPath, [switch]$Force)
    if ($LiteralPath -eq $env:HL_REMOVE_FAIL) { throw "synthetic removal failure: $LiteralPath" }
    Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}
function Copy-Item {
    [CmdletBinding()]param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($env:HL_PLACE_FAIL -and $Destination.StartsWith((Join-Path $CHotRoot 'models\m1'))) {
        throw "synthetic placement failure: $Destination"
    }
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
    if ($LiteralPath -eq $env:HL_CHANGE_SOURCE) { Set-Content -LiteralPath $LiteralPath -Value 'changed-after-copy' -NoNewline }
}
function Get-Item {
    [CmdletBinding()]param([string]$LiteralPath, [switch]$Force)
    if ($LiteralPath -eq $env:HL_MEASURE_FAIL) { throw "synthetic unreadable file: $LiteralPath" }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force
}
$script:journalWriter = ${function:Write-JournalLine}
$script:journalAppends = 0
function Write-JournalLine([string]$Path, [object]$Record, [switch]$Create) {
    if ($Create -and $env:HL_JOURNAL_FAIL -eq 'create') {
        throw 'synthetic journal creation failure'
    }
    if (-not $Create) {
        $script:journalAppends++
        if ($env:HL_JOURNAL_FAIL -eq 'append' -and $script:journalAppends -eq 2) {
            throw 'synthetic journal append failure before write'
        }
    }
    & $script:journalWriter $Path $Record -Create:$Create
}
function Set-Content {
    [CmdletBinding()]param(
        [string]$LiteralPath, [Parameter(ValueFromPipeline)]$Value,
        [string]$Encoding = 'utf8', [switch]$NoNewline
    )
    process {
        if ($env:HL_RECEIPT_FAIL -and $LiteralPath -like '*m1-convert-*.json') {
            throw 'synthetic receipt write failure'
        }
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $LiteralPath -Value $Value -Encoding $Encoding -NoNewline:$NoNewline
    }
}
'@
    $sourceText = Get-Content -LiteralPath $script -Raw
    $helper = (Resolve-Path (Join-Path $PSScriptRoot '..\scripts\invoke-bounded.ps1')).Path.Replace("'", "''")
    $script = Join-Path $work 'convert-under-test.ps1'
    $sourceText.Replace(". (Join-Path `$PSScriptRoot '..\scripts\invoke-bounded.ps1')", ". '$helper'").Replace('$CHotRoot = Get-NormalPath $CHotRoot', "$testHooks`r`n" + '$CHotRoot = Get-NormalPath $CHotRoot') | Set-Content -LiteralPath $script -Encoding utf8

    # Exercise quotes, Unicode and CRLF through the same bounded native argv route.
    . (Join-Path $PSScriptRoot '..\scripts\invoke-bounded.ps1')
    $ast = [Management.Automation.Language.Parser]::ParseFile($script, [ref]$null, [ref]$null)
    $encoder = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'ConvertTo-BashCommand' }, $true)
    . ([scriptblock]::Create($encoder.Extent.Text))
    $sample = "printf '%s|%s\\n' `"a'b`" 'caf$([char]0xe9)'`r`nprintf '%s\\n' '`$HOME'"
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $env:HL_SCRIPT_ECHO = '1'
        $roundtrip = @(Invoke-Bounded $shellExe @('-NoProfile', '-File', $dockerShim, 'run', '-lc', (ConvertTo-BashCommand $sample)) 30)
        Remove-Item Env:HL_SCRIPT_ECHO
        Assert ($roundtrip[-1] -eq '__EXIT__0') 'encoded script crosses bounded native argv'
        Assert (($roundtrip[0..($roundtrip.Count - 2)] -join "`n") -ceq $sample.Replace("`r`n", "`n")) 'decoded script preserves quotes and Unicode with LF newlines'
    } else {
        Write-Output 'LIMITATION: PowerShell 5.1 uses a synthetic transport; unchanged Invoke-Bounded requires PowerShell 7 (.NET ArgumentList and Kill(bool)). Native transport check not run.'
    }

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
    $before = @(Get-ChildItem -LiteralPath $work -Recurse -File -Force).Count
    $out, $code = Invoke-Convert @(
        '-Preflight', '-Model', 'Qwen/Qwen3-8B', '-CHotRoot', $cRoot,
        '-EArchiveRoot', $eRoot, '-GConvertRoot', $gRoot, '-GpuPoisonPath', $poison,
        '-DockerPath', $dockerShim, '-ProbeCFreeBytes', ([long](200GB)), '-ProbeLlmBytes', 0
    )
    Assert ($code -eq 0) 'preflight exits 0'
    Assert ($before -eq @(Get-ChildItem -LiteralPath $work -Recurse -File -Force).Count) 'preflight creates no files or journal'
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
    foreach ($field in @('model', 'repo', 'revision', 'gguf', 'archived', 'removed', 'storage', 'tools', 'journal')) {
        Assert ($rc.PSObject.Properties.Name -contains $field) "receipt has $field"
    }
    Assert ($rc.gguf.Count -eq 2) 'receipt lists exactly two final GGUF artifacts'
    Assert (Test-Path -LiteralPath $rc.journal) 'success receipt references retained journal'
    foreach ($g in $rc.gguf) {
        Assert (-not [string]::IsNullOrWhiteSpace($g.path) -and $g.sha256 -match '^[a-f0-9]{64}$') 'GGUF entry has path and sha256'
    }
    Assert ($rc.PSObject.Properties.Name -contains 'dangling_links_left') 'receipt notes dangling snapshot links'
    Assert (-not (Test-Path -LiteralPath $fx.blob)) 'verified archive removed shared blob from C:'
    Assert (Test-Path -LiteralPath $fx.path) 'snapshot link left on C: after blob removal'
    Assert (Test-Path -LiteralPath (Join-Path $cRoot 'models\m1\qwen3-8b\Qwen3-8B-Q4_K_M.gguf')) 'final GGUF on C: m1 path'

    # --- fourth-round failures: exact progress survives exceptions ---
    foreach ($case in @('partial-removal', 'placement', 'changed-source', 'unreadable', 'receipt-write', 'partial-receipt-write', 'journal-create', 'journal-append')) {
        $cCase = Join-Path $work "c-$case"
        $eCase = Join-Path $work "e-$case"
        $gCase = Join-Path $work "g-$case"
        $fxCase = New-XetBlobFixture $cCase 'Qwen/Qwen3-8B' 'b968826d9c46dd6066d109eabc6255188de91218' 'first-weight' ('a' * 64)
        $second = Join-Path $cCase ("hf\hub\blobs\bb\" + ('b' * 64))
        New-Item -ItemType Directory -Force (Split-Path $second) | Out-Null
        Set-Content -LiteralPath $second -Value 'second-weight' -NoNewline
        $secondSha = (Get-FileHash -LiteralPath $second -Algorithm SHA256).Hash.ToLower()
        $map = @{
            'first.safetensors' = ('/hf/hub/blobs/aa/' + ('a' * 64))
            'second.safetensors' = ('/hf/hub/blobs/bb/' + ('b' * 64))
        }
        $files = @(
            @{ path = 'first.safetensors'; sha256 = $fxCase.sha256; size = $fxCase.size }
            @{ path = 'second.safetensors'; sha256 = $secondSha; size = (Get-Item -LiteralPath $second).Length }
        )
        if ($case -eq 'journal-append') {
            $third = Join-Path $cCase ("hf\hub\blobs\cc\" + ('c' * 64))
            New-Item -ItemType Directory -Force (Split-Path $third) | Out-Null
            Set-Content -LiteralPath $third -Value 'third-weight' -NoNewline
            $thirdSha = (Get-FileHash -LiteralPath $third -Algorithm SHA256).Hash.ToLower()
            $files += @{ path = 'third.safetensors'; sha256 = $thirdSha; size = (Get-Item -LiteralPath $third).Length }
            $map['third.safetensors'] = '/hf/hub/blobs/cc/' + ('c' * 64)
        }
        $env:HL_BLOB_MAP_JSON = $map | ConvertTo-Json -Compress
        $caseManifest = Join-Path $work "offline-$case.json"
        @{
            'Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218' = @{
                files = $files
            }
        } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $caseManifest -Encoding utf8
        switch ($case) {
            'partial-removal' { $env:HL_REMOVE_FAIL = $second }
            'placement' { $env:HL_PLACE_FAIL = '1' }
            'changed-source' { $env:HL_CHANGE_SOURCE = $fxCase.blob }
            'unreadable' { $env:HL_MEASURE_FAIL = $second }
            'receipt-write' { $env:HL_RECEIPT_FAIL = '1' }
            'partial-receipt-write' { $env:HL_RECEIPT_FAIL = '1'; $env:HL_PLACE_FAIL = '1' }
            'journal-create' { $env:HL_JOURNAL_FAIL = 'create' }
            'journal-append' { $env:HL_JOURNAL_FAIL = 'append' }
        }
        $out, $code = Invoke-Convert @(
            '-Execute', '-Model', 'Qwen/Qwen3-8B',
            '-CHotRoot', $cCase, '-EArchiveRoot', $eCase, '-GConvertRoot', $gCase,
            '-GpuPoisonPath', $poison, '-OfflineExpected', $caseManifest,
            '-ProbeCFreeBytes', ([long](200GB)), '-DockerPath', $dockerShim
        )
        foreach ($name in @('HL_REMOVE_FAIL', 'HL_PLACE_FAIL', 'HL_CHANGE_SOURCE', 'HL_MEASURE_FAIL', 'HL_BLOB_MAP_JSON', 'HL_RECEIPT_FAIL', 'HL_JOURNAL_FAIL')) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
        $message = Out-Text $out
        # Windows PowerShell wraps native error text at the console width, even inside paths.
        $errorText = $message -replace '\s', ''
        Assert ($code -ne 0) "$case exits nonzero"
        if ($case -eq 'unreadable') {
            Assert ($errorText.Contains('cannotmeasure') -and $errorText.Contains(($second -replace '\s', ''))) 'unreadable error names file'
            Assert ((Test-Path -LiteralPath $fxCase.blob) -and (Test-Path -LiteralPath $second)) 'unreadable measurement preserves both blobs'
            Assert (-not (Test-Path -LiteralPath $gCase)) 'unreadable measurement stops before conversion'
            continue
        }
        $caseReceipts = @(Get-ChildItem -LiteralPath (Join-Path $cCase 'reports') -Filter '*.json')
        $journals = @(Get-ChildItem -LiteralPath (Join-Path $cCase 'reports') -Filter '*.journal.jsonl')
        if ($case -in @('receipt-write', 'partial-receipt-write', 'journal-append')) {
            Assert ($journals.Count -eq 1) "$case retains one journal"
            $rawLines = @(Get-Content -LiteralPath $journals[0].FullName)
            $journal = @($rawLines | ForEach-Object { $_ | ConvertFrom-Json })
            $intent = $journal[0]
            Assert ($intent.type -eq 'intent' -and $intent.model -eq 'qwen3-8b' -and $intent.repo -eq 'Qwen/Qwen3-8B' -and $intent.revision -eq 'b968826d9c46dd6066d109eabc6255188de91218') "$case intent pins model and revision"
            Assert (@($intent.removals).Count -eq $files.Count) "$case intent lists every planned removal"
            Assert ($intent.storage.before.cFreeBytes -eq 200GB -and $intent.storage.beforeRemoval.cFreeBytes -eq 200GB) "$case intent contains storage figures"
            foreach ($planned in $intent.removals) {
                Assert ((Get-FileHash -LiteralPath $planned.archivePath -Algorithm SHA256).Hash.ToLower() -eq $planned.archiveSha256 -and $planned.sha256 -eq $planned.archiveSha256) "$case intent names verified archive and hash"
            }
            for ($i = 1; $i -lt $journal.Count; $i++) {
                $record = $journal[$i]
                $planned = @($intent.removals | Where-Object { $_.path -eq $record.path })
                Assert ($record.type -eq 'removed' -and $planned.Count -eq 1 -and $record.sha256 -eq $planned[0].sha256 -and $rawLines[$i] -match '"utc"\s*:\s*"[^"]*Z"' -and -not (Test-Path -LiteralPath $record.path)) "$case completion line matches a removed blob and UTC time"
            }
            Assert ($errorText.Contains(($journals[0].FullName -replace '\s', ''))) "$case failure names retained journal"
        }
        if ($case -in @('receipt-write', 'partial-receipt-write')) {
            Assert ($caseReceipts.Count -eq 0) "$case writes no final receipt"
            Assert ($journal.Count -eq 3) "$case journal has intent plus both removals"
            Assert ($journal[1].path -eq $fxCase.blob -and $journal[2].path -eq $second) "$case journal records exact removal order"
            Assert (-not (Test-Path -LiteralPath $fxCase.blob) -and -not (Test-Path -LiteralPath $second)) "$case fails after all removals"
            Assert ($message -match 'synthetic receipt write failure') "$case surfaces receipt failure"
            continue
        }
        Assert ($caseReceipts.Count -eq 1) "$case writes one partial receipt"
        $partial = Get-Content -LiteralPath $caseReceipts[0].FullName -Raw | ConvertFrom-Json
        Assert ($partial.status -eq 'partial') "$case receipt status partial"
        Assert (@($partial.gguf).Count -eq 0) "$case reports no placed GGUFs"
        Assert ($errorText.Contains('removedC:blobs') -and $errorText.Contains('remainingC:blobs')) "$case error reports removal state"
        foreach ($arch in $partial.archived) {
            Assert ((Get-FileHash -LiteralPath $arch.path -Algorithm SHA256).Hash.ToLower() -eq $arch.sha256) "$case archive remains verified"
        }
        Assert (@($partial.archived).Count -eq $files.Count) "$case all weights verified on archive"
        if ($journals.Count) { Assert ($partial.journal -eq $journals[0].FullName) "$case partial receipt references journal" }
        switch ($case) {
            'partial-removal' {
                Assert (@($partial.removed).Count -eq 1 -and $partial.removed[0].path -eq $fxCase.blob -and $partial.removed[0].sha256 -eq $fxCase.sha256) 'partial receipt records exactly first removal and hash'
                Assert (@($partial.remaining).Count -eq 1 -and $partial.remaining[0] -eq $second) 'partial receipt records exactly second remaining blob'
                Assert (-not (Test-Path -LiteralPath $fxCase.blob) -and (Test-Path -LiteralPath $second)) 'second removal failure preserves second blob only'
                Assert ($message.Contains($fxCase.blob) -and $message.Contains($second)) 'partial removal error names both blobs'
            }
            'placement' {
                Assert (@($partial.removed).Count -eq 2 -and @($partial.remaining).Count -eq 0) 'placement failure records both removed blobs and none remaining'
                Assert ($partial.removed[0].path -eq $fxCase.blob -and $partial.removed[1].path -eq $second) 'placement receipt names exact removed blobs'
                Assert (-not (Test-Path -LiteralPath $fxCase.blob) -and -not (Test-Path -LiteralPath $second)) 'placement failure follows actual removals'
                Assert ($partial.failure -match 'placement failed' -and $message -match 'placement failed' -and $message.Contains($fxCase.blob) -and $message.Contains($second)) 'placement receipt and error explain failure and removals'
                Assert (Test-Path -LiteralPath (Join-Path $gCase 'qwen3-8b\Qwen3-8B-Q4_K_M.gguf')) 'placement failure preserves temporary GGUF'
            }
            'changed-source' {
                Assert (@($partial.removed).Count -eq 0 -and @($partial.remaining).Count -eq 2) 'changed source removes nothing'
                Assert ((Test-Path -LiteralPath $fxCase.blob) -and (Test-Path -LiteralPath $second)) 'changed source preserves both blobs'
                Assert ((Get-FileHash -LiteralPath $fxCase.blob -Algorithm SHA256).Hash.ToLower() -ne $fxCase.sha256) 'source changed after verified copy'
                Assert ($partial.failure -match 'hash mismatch on mapped blob' -and $message -match 'hash mismatch on mapped blob') 'immediate pre-removal rehash refuses mismatch'
            }
            'journal-create' {
                Assert ($journals.Count -eq 0) 'failed journal creation leaves no journal'
                Assert (@($partial.removed).Count -eq 0 -and @($partial.remaining).Count -eq 2) 'journal creation failure reports zero removals'
                Assert ((Test-Path -LiteralPath $fxCase.blob) -and (Test-Path -LiteralPath $second)) 'journal creation failure preserves every blob'
                Assert ($partial.failure -match 'synthetic journal creation failure') 'partial receipt explains journal creation failure'
            }
            'journal-append' {
                Assert (-not (Test-Path -LiteralPath $fxCase.blob) -and -not (Test-Path -LiteralPath $second) -and (Test-Path -LiteralPath $third)) 'append failure stops before third removal'
                Assert (@($partial.removed).Count -eq 2 -and $partial.removed[0].path -eq $fxCase.blob -and $partial.removed[1].path -eq $second -and @($partial.remaining).Count -eq 1 -and $partial.remaining[0] -eq $third) 'partial receipt records exact state after failed append'
                Assert ($journal.Count -eq 2 -and $journal[1].path -eq $fxCase.blob) 'journal retains intent and first completion; failed second append adds no line'
                Assert (@($intent.removals | Where-Object { $_.path -eq $second -and $_.sha256 -eq $secondSha }).Count -eq 1) 'unconfirmed second removal retains write-ahead archive evidence'
                Assert ($partial.failure -match 'synthetic journal append failure before write') 'partial receipt explains append failure'
            }
        }
    }

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
    foreach ($name in @('HL_REMOVE_FAIL', 'HL_PLACE_FAIL', 'HL_CHANGE_SOURCE', 'HL_MEASURE_FAIL', 'HL_RECEIPT_FAIL', 'HL_JOURNAL_FAIL')) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    Remove-Item Env:HL_SCRIPT_ECHO -ErrorAction SilentlyContinue
    Remove-Item Env:HL_DOCKER_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:CONVERT_ORDER_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:HL_BLOB_MAP_JSON -ErrorAction SilentlyContinue
    $resolvedWork = [IO.Path]::GetFullPath($work)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedWork.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolvedWork -Leaf) -notlike 'convert-bases-test-*') { throw 'unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction SilentlyContinue
}

if ($fail.Count) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-convert-bases: all cases passed'
exit 0
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:25 UTC | END]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:40 UTC | END]]
