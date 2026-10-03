# Behavioural checks for export-adapter.ps1 on scratch roots (never real C:\llm, E:\, Docker, GPU, network, or real weights).
#   pwsh -File projects\homemade-llm\scripts\test-export-adapter.ps1
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) {
    Write-Output 'SKIP'
    exit 0
}

$script = Join-Path $PSScriptRoot 'export-adapter.ps1'
$shellExe = Join-Path $PSHOME 'pwsh.exe'
$fail = [System.Collections.Generic.List[string]]::new()

function Assert([bool]$Cond, [string]$Case) {
    if (-not $Cond) { $fail.Add($Case) } else { Write-Output "ok  $Case" }
}

function Invoke-Export([string[]]$Arguments) {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $shellExe -NoProfile -NonInteractive -File $script @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = -1 }
        return [pscustomobject]@{ Lines = @($lines); Code = [int]$code }
    } finally {
        $ErrorActionPreference = $prevEa
    }
}

function Out-Text($Lines) { (@($Lines) | ForEach-Object { "$_" }) -join "`n" }

function Get-Sha256File([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

function Get-Sha256Bytes([byte[]]$Bytes) {
    $h = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($h.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $h.Dispose() }
}

function Invoke-FlipFirstByte([string]$Path) {
    $b = [IO.File]::ReadAllBytes($Path)
    $b[0] = [byte]($b[0] -bxor 0xFF)
    [IO.File]::WriteAllBytes($Path, $b)
}

$root = $null
$jTarget = $null
$eRoot = $null
$savedShimLog = $env:HL_SHIM_LOG
$savedShimFail = $env:HL_SHIM_FAIL
$savedFakeTemplate = $env:HL_FAKE_TEMPLATE_SHA
$savedJunction = $env:HL_SHIM_JUNCTION
$savedJunctionTarget = $env:HL_SHIM_JUNCTION_TARGET
$savedCreateDest = $env:HL_SHIM_CREATE_DEST

try {
    $uniq = [guid]::NewGuid().ToString('n')
    $root = Join-Path $env:TEMP "hl-test-export-adapter-$uniq"
    $jTarget = Join-Path $env:TEMP "hl-test-export-jtarget-$uniq"
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    New-Item -ItemType Directory -Path $jTarget -Force | Out-Null
    $jSentinel = Join-Path $jTarget 'junction-sentinel.bin'
    [IO.File]::WriteAllBytes($jSentinel, [Text.Encoding]::UTF8.GetBytes('junction-sentinel'))
    $jSentinelSha = Get-Sha256File $jSentinel

    $cRoot = Join-Path $root 'C'
    $eRoot = Join-Path $root 'E\export'
    $adapter1 = Join-Path $root 'adapter1'
    $adapter2 = Join-Path $root 'adapter2'
    $shimPath = Join-Path $root 'docker-shim.ps1'
    $shimLogPath = Join-Path $root 'shim.log'

    $rev = '0123456789abcdef0123456789abcdef01234567'
    $templateSha = 'cafebabecafebabecafebabecafebabecafebabecafebabecafebabecafebabe'
    $cFreeOk = [long](200GB)
    $eFreeOk = [long](150GB)
    $llmOk = [long](10GB)
    $cFreeLow = [long](1GB)
    $eFreeLow = [long](1GB)
    $quantText = 'Q4_K_M-FIXTURE-BYTES'
    $quantSha = Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes($quantText))

    $snapDir = Join-Path $cRoot "hf\hub\models--Qwen--Qwen3-14B\snapshots\$rev"
    New-Item -ItemType Directory -Path $snapDir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $cRoot 'reports') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $cRoot 'models\m1\qwen3-14b') -Force | Out-Null
    New-Item -ItemType Directory -Path $eRoot -Force | Out-Null

    $shard1Path = Join-Path $snapDir 'model-00001-of-00002.safetensors'
    $shard2Path = Join-Path $snapDir 'model-00002-of-00002.safetensors'
    $shard1Bytes = [Text.Encoding]::UTF8.GetBytes('SHARD-ONE')
    $shard2Bytes = [Text.Encoding]::UTF8.GetBytes('SHARD-TWO')
    [IO.File]::WriteAllBytes($shard1Path, $shard1Bytes)
    [IO.File]::WriteAllBytes($shard2Path, $shard2Bytes)
    $shard1Sha = Get-Sha256File $shard1Path
    $shard2Sha = Get-Sha256File $shard2Path
    $indexPath = Join-Path $snapDir 'model.safetensors.index.json'

    $expectedPath = Join-Path $cRoot 'reports\m1-expected-qwen3-14b.json'

    function Write-FixtureExpected {
        $expectedDoc = [ordered]@{}
        $expectedDoc["Qwen/Qwen3-14B@$rev"] = [ordered]@{
            files = @(
                [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
                [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
            )
        }
        [IO.File]::WriteAllText($expectedPath, ($expectedDoc | ConvertTo-Json -Depth 8))
    }

    function Write-FixtureIndex {
        $indexDoc = [ordered]@{
            weight_map = [ordered]@{
                'model.embed_tokens.weight' = 'model-00001-of-00002.safetensors'
                'model.layers.0.weight'     = 'model-00001-of-00002.safetensors'
                'lm_head.weight'            = 'model-00002-of-00002.safetensors'
            }
        }
        [IO.File]::WriteAllText($indexPath, ($indexDoc | ConvertTo-Json -Depth 8))
    }

    Write-FixtureExpected
    Write-FixtureIndex

    $sentinelPath = Join-Path $cRoot 'models\m1\qwen3-14b\sentinel.gguf'
    [IO.File]::WriteAllBytes($sentinelPath, [Text.Encoding]::UTF8.GetBytes('sentinel-gguf'))
    $sentinelSha = Get-Sha256File $sentinelPath

    function New-AdapterDir([string]$Dir, [string]$ConfigText, [string]$ModelText) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
        $cfg = Join-Path $Dir 'adapter_config.json'
        $mod = Join-Path $Dir 'adapter_model.safetensors'
        [IO.File]::WriteAllText($cfg, $ConfigText)
        [IO.File]::WriteAllText($mod, $ModelText)
        $h1 = Get-Sha256File $cfg
        $h2 = Get-Sha256File $mod
        $doc = @{ files = [ordered]@{ 'adapter_config.json' = $h1; 'adapter_model.safetensors' = $h2 } }
        [IO.File]::WriteAllText((Join-Path $Dir 'adapter-hashes.json'), ($doc | ConvertTo-Json -Compress))
    }

    New-AdapterDir $adapter1 '{"name":"a1"}' 'ADAPTER-MODEL-ONE'
    New-AdapterDir $adapter2 '{"name":"a2"}' 'ADAPTER-MODEL-TWO'

    $guardPaths = @(
        $sentinelPath,
        $shard1Path,
        $shard2Path,
        (Join-Path $adapter1 'adapter_config.json'),
        (Join-Path $adapter1 'adapter_model.safetensors'),
        (Join-Path $adapter1 'adapter-hashes.json'),
        (Join-Path $adapter2 'adapter_config.json'),
        (Join-Path $adapter2 'adapter_model.safetensors'),
        (Join-Path $adapter2 'adapter-hashes.json')
    )

    function Get-GuardFingerprint {
        $parts = foreach ($p in $guardPaths) {
            '{0}={1}' -f $p, (Get-Sha256File $p)
        }
        $parts -join '|'
    }

    $fp0 = Get-GuardFingerprint

    $shim = @'
$ErrorActionPreference = 'Stop'
$DockerArgs = @($args)
$workHost = $null
$hasNetworkNone = $false
$gpusOrToken = $false
for ($i = 0; $i -lt $DockerArgs.Count; $i++) {
    $a = [string]$DockerArgs[$i]
    if ($a -eq '--network' -and ($i + 1) -lt $DockerArgs.Count -and [string]$DockerArgs[$i + 1] -eq 'none') {
        $hasNetworkNone = $true
    }
    if ($a -eq '--gpus' -or $a.StartsWith('--gpus') -or $a.Contains('TOKEN')) {
        $gpusOrToken = $true
    }
    if ($a -eq '-v' -and ($i + 1) -lt $DockerArgs.Count) {
        $vol = [string]$DockerArgs[$i + 1]
        if ($vol -match '^(.*):/work$') { $workHost = $Matches[1] }
    }
}
$lc = $null
for ($j = 0; $j -lt $DockerArgs.Count; $j++) {
    if ([string]$DockerArgs[$j] -eq '-lc' and ($j + 1) -lt $DockerArgs.Count) {
        $lc = [string]$DockerArgs[$j + 1]
        break
    }
}
if ($null -eq $lc -or $lc -cnotmatch '\Aexec 2>&1; set -o pipefail; echo ([A-Za-z0-9+/]+={0,2}) \| base64 -d \| bash\z') {
    throw 'expected encoded bash transport with stderr capture'
}
$decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Matches[1]))
if ($decoded.Contains("`r")) { throw 'bash script must use LF newlines' }
$step = 'unknown'
if ($decoded.Contains('merge.py')) { $step = 'merge' }
elseif ($decoded.Contains('convert_hf_to_gguf')) { $step = 'convert' }
elseif ($decoded.Contains('llama-quantize')) { $step = 'quantise' }
elseif ($decoded.Contains('GGUFReader')) { $step = 'template' }
$mergedExists = 'n/a'
if ($step -eq 'quantise') {
    if ($workHost) {
        $mergedExists = [bool](Test-Path -LiteralPath (Join-Path $workHost 'merged'))
    } else {
        $mergedExists = 'no-work'
    }
}
$line = "step=$step network_none=$hasNetworkNone gpus_or_token=$gpusOrToken merged_exists=$mergedExists"
if ($env:HL_SHIM_LOG) {
    [IO.File]::AppendAllText($env:HL_SHIM_LOG, $line + "`n")
}
if ($env:HL_SHIM_FAIL -eq $step) {
    Write-Output "shim fail $step"
    exit 2
}
if ($step -eq 'merge') {
    $merged = Join-Path $workHost 'merged'
    New-Item -ItemType Directory -Path $merged -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $merged 'model.safetensors'), 'merged-weights')
    [IO.File]::WriteAllText((Join-Path $merged 'config.json'), '{}')
    Write-Output 'merged ok'
    exit 0
}
if ($step -eq 'convert') {
    [IO.File]::WriteAllText((Join-Path $workHost 'tstexp-bf16.gguf'), 'BF16-FIXTURE')
    if ($env:HL_SHIM_JUNCTION -eq '1') {
        $merged = Join-Path $workHost 'merged'
        if (Test-Path -LiteralPath $merged) {
            Remove-Item -LiteralPath $merged -Recurse -Force
        }
        New-Item -ItemType Junction -Path $merged -Target $env:HL_SHIM_JUNCTION_TARGET | Out-Null
    }
    Write-Output 'convert ok'
    exit 0
}
if ($step -eq 'quantise') {
    [IO.File]::WriteAllText((Join-Path $workHost 'tstexp-Q4_K_M.gguf'), 'Q4_K_M-FIXTURE-BYTES')
    Write-Output 'quantise ok'
    exit 0
}
if ($step -eq 'template') {
    if ($env:HL_SHIM_CREATE_DEST) {
        $destCreate = [string]$env:HL_SHIM_CREATE_DEST
        $destCreateDir = Split-Path -Parent $destCreate
        if ($destCreateDir -and -not (Test-Path -LiteralPath $destCreateDir)) {
            New-Item -ItemType Directory -Path $destCreateDir -Force | Out-Null
        }
        [IO.File]::WriteAllBytes($destCreate, [Text.Encoding]::UTF8.GetBytes('CONCURRENT-SENTINEL'))
    }
    Write-Output ([string]$env:HL_FAKE_TEMPLATE_SHA)
    exit 0
}
throw "unknown docker step"
'@
    $shim = $shim.Replace("if ([string]`$DockerArgs[`$j] -eq '-lc' and (`$j + 1) -lt `$DockerArgs.Count) {", "if ([string]`$DockerArgs[`$j] -eq '-lc' -and (`$j + 1) -lt `$DockerArgs.Count) {")
    Set-Content -LiteralPath $shimPath -Value $shim -Encoding utf8NoBOM

    $env:HL_SHIM_LOG = $shimLogPath
    $env:HL_FAKE_TEMPLATE_SHA = $templateSha
    $env:HL_SHIM_JUNCTION_TARGET = $jTarget

    function Get-ExportStagingHelperSource {
        $exportScript = Join-Path $PSScriptRoot 'export-adapter.ps1'
        $content = Get-Content -LiteralPath $exportScript -Raw -Encoding utf8
        $start = $content.IndexOf('function Get-NormalPath')
        $end = $content.IndexOf('function Get-RelativePath')
        if ($start -lt 0 -or $end -le $start) { throw 'export helper extract failed' }
        return $content.Substring($start, $end - $start)
    }

    function Invoke-RemoveStagedTreeProbe {
        param(
            [Parameter(Mandatory)][string]$StagingRootArg,
            [Parameter(Mandatory)][string]$CHotRootArg,
            [Parameter(Mandatory)][string]$Target,
            [Parameter(Mandatory)][string]$OwnershipToken
        )
        $helpers = Get-ExportStagingHelperSource
        $stagingEsc = $StagingRootArg.Replace("'", "''")
        $chotEsc = $CHotRootArg.Replace("'", "''")
        $targetEsc = $Target.Replace("'", "''")
        $tokenEsc = $OwnershipToken.Replace("'", "''")
        $cmd = @"
`$ErrorActionPreference = 'Stop'
`$StagingRoot = '$stagingEsc'
`$CHotRoot = '$chotEsc'
$helpers
try {
    Remove-StagedTree -Target '$targetEsc' -OwnershipToken '$tokenEsc'
    'PROBE_OK'
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
        $prevEa = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $lines = @(& $shellExe -NoProfile -NonInteractive -Command $cmd 2>&1 | ForEach-Object { "$_" })
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = -1 }
            return [pscustomobject]@{ Lines = @($lines); Code = [int]$code; Text = (Out-Text $lines) }
        } finally {
            $ErrorActionPreference = $prevEa
        }
    }

    function New-ExportArgs {
        param(
            [Parameter(Mandatory)][string[]]$Checkpoint,
            [switch]$Execute,
            [string]$Name = 'tstexp',
            [string]$StagingRoot = '',
            [string]$RunId = '',
            [long]$ProbeCFreeBytes = -1,
            [long]$ProbeEFreeBytes = -1,
            [long]$ProbeLlmBytes = -1
        )
        if ($StagingRoot -eq '') { $StagingRoot = $eRoot }
        if ($ProbeCFreeBytes -lt 0) { $ProbeCFreeBytes = $cFreeOk }
        if ($ProbeEFreeBytes -lt 0) { $ProbeEFreeBytes = $eFreeOk }
        if ($ProbeLlmBytes -lt 0) { $ProbeLlmBytes = $llmOk }
        $a = [System.Collections.Generic.List[string]]::new()
        if ($Execute) { [void]$a.Add('-Execute') }
        [void]$a.Add('-Checkpoint')
        [void]$a.Add(($Checkpoint -join ','))
        [void]$a.Add('-Name'); [void]$a.Add($Name)
        if ($RunId) { [void]$a.Add('-RunId'); [void]$a.Add($RunId) }
        [void]$a.Add('-BaseRevision'); [void]$a.Add($rev)
        [void]$a.Add('-CHotRoot'); [void]$a.Add($cRoot)
        [void]$a.Add('-StagingRoot'); [void]$a.Add($StagingRoot)
        [void]$a.Add('-ExpectedShardsJson'); [void]$a.Add($expectedPath)
        [void]$a.Add('-BaseTemplateSha256'); [void]$a.Add($templateSha)
        [void]$a.Add('-DockerPath'); [void]$a.Add($shimPath)
        [void]$a.Add('-ProbeCFreeBytes'); [void]$a.Add("$ProbeCFreeBytes")
        [void]$a.Add('-ProbeEFreeBytes'); [void]$a.Add("$ProbeEFreeBytes")
        [void]$a.Add('-ProbeLlmBytes'); [void]$a.Add("$ProbeLlmBytes")
        [void]$a.Add('-DockerTimeoutSec'); [void]$a.Add('120')
        return , $a.ToArray()
    }

    function Get-ShimLogLines {
        if (-not (Test-Path -LiteralPath $shimLogPath)) { return @() }
        return @((Get-Content -LiteralPath $shimLogPath -ErrorAction SilentlyContinue | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }))
    }

    function Remove-JunctionsUnder([string]$Root) {
        if (-not $Root -or -not (Test-Path -LiteralPath $Root)) { return }
        $stack = [System.Collections.Generic.Stack[string]]::new()
        $stack.Push($Root)
        $reparse = [System.Collections.Generic.List[string]]::new()
        while ($stack.Count -gt 0) {
            $dir = $stack.Pop()
            if (-not (Test-Path -LiteralPath $dir)) { continue }
            $dirItem = Get-Item -LiteralPath $dir -Force
            if ($dirItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [void]$reparse.Add($dir)
                continue
            }
            foreach ($child in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue)) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    [void]$reparse.Add($child.FullName)
                } elseif ($child.PSIsContainer) {
                    $stack.Push($child.FullName)
                }
            }
        }
        foreach ($p in $reparse) {
            if (-not (Test-Path -LiteralPath $p)) { continue }
            $it = Get-Item -LiteralPath $p -Force
            if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                if ($it.PSIsContainer) {
                    [System.IO.Directory]::Delete($p, $false)
                } else {
                    [System.IO.File]::Delete($p)
                }
            }
        }
    }

    function Remove-TreeSafe([string]$Path) {
        if ([string]::IsNullOrWhiteSpace($Path)) { return }
        if (-not (Test-Path -LiteralPath $Path)) { return }
        Remove-JunctionsUnder $Path
        if (Test-Path -LiteralPath $Path) {
            $it = Get-Item -LiteralPath $Path -Force
            if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                if ($it.PSIsContainer) {
                    [System.IO.Directory]::Delete($Path, $false)
                } else {
                    [System.IO.File]::Delete($Path)
                }
            } else {
                Remove-Item -LiteralPath $Path -Recurse -Force
            }
        }
    }

    function Clear-StagingJunctions {
        if (-not $eRoot -or -not (Test-Path -LiteralPath $eRoot)) { return }
        Remove-TreeSafe $eRoot
    }

    function Clear-Artifacts {
        Clear-StagingJunctions
        if ($eRoot -and -not (Test-Path -LiteralPath $eRoot)) {
            New-Item -ItemType Directory -Path $eRoot -Force | Out-Null
        }
        $m2 = Join-Path $cRoot 'models\m2'
        if (Test-Path -LiteralPath $m2) {
            Remove-TreeSafe $m2
        }
        $rep = Join-Path $cRoot 'reports'
        if (Test-Path -LiteralPath $rep) {
            Get-ChildItem -LiteralPath $rep -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'm2-export-*' } |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
        }
        if (Test-Path -LiteralPath $eRoot) {
            Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue | ForEach-Object {
                Remove-TreeSafe $_.FullName
            }
        }
        if (Test-Path -LiteralPath $shimLogPath) { Remove-Item -LiteralPath $shimLogPath -Force }
        $env:HL_SHIM_FAIL = ''
        $env:HL_SHIM_JUNCTION = ''
        $env:HL_SHIM_CREATE_DEST = ''
        $env:HL_FAKE_TEMPLATE_SHA = $templateSha
    }

    function Get-KeptStaging([object[]]$Lines) {
        foreach ($ln in $Lines) {
            if ("$ln" -match 'staging kept for inspection:\s*(.+)$') {
                return $Matches[1].Trim()
            }
        }
        return $null
    }

    function Get-ExportReports {
        $rep = Join-Path $cRoot 'reports'
        if (-not (Test-Path -LiteralPath $rep)) { return @() }
        return @(Get-ChildItem -LiteralPath $rep -Filter 'm2-export-tstexp-*.json' -File -ErrorAction SilentlyContinue)
    }

    $outCkpt1 = Join-Path (Join-Path $cRoot 'models\m2') 'tstexp-ckpt1-Q4_K_M.gguf'
    $outCkpt2 = Join-Path (Join-Path $cRoot 'models\m2') 'tstexp-ckpt2-Q4_K_M.gguf'

    # --- 1. Dry run ---
    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1))
    $text = Out-Text $r.Lines
    Assert ($r.Code -eq 0) 'dry-run: exit 0'
    Assert ($text.Contains('PLAN mode=dry-run')) 'dry-run: PLAN mode=dry-run'
    $stagingKids = @()
    if (Test-Path -LiteralPath $eRoot) {
        $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force)
    }
    Assert ($stagingKids.Count -eq 0) 'dry-run: no staging folder'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 'dry-run: no output file'
    Assert ((Get-ExportReports).Count -eq 0) 'dry-run: no report'
    $log = Get-ShimLogLines
    Assert ($log.Count -eq 0) 'dry-run: empty or missing shim log'

    # --- 2. Parameter refusals ---
    $r = Invoke-Export (New-ExportArgs -Checkpoint @('ck1', 'ck2', 'ck3'))
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'at most 2') 'param: three checkpoints refused'

    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Name 'BadName')
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'parameter Name') 'param: bad Name refused'

    $r = Invoke-Export @('-Name', 'tstexp', '-CHotRoot', $cRoot, '-StagingRoot', $eRoot, '-ProbeCFreeBytes', "$cFreeOk", '-ProbeEFreeBytes', "$eFreeOk", '-ProbeLlmBytes', "$llmOk")
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'Checkpoint') 'param: no checkpoint refused'

    # --- 3. Happy path, one checkpoint ---
    Clear-Artifacts
    $fpBeforeHappy = Get-GuardFingerprint
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -eq 0) 'happy: exit 0'
    Assert (Test-Path -LiteralPath $outCkpt1 -PathType Leaf) 'happy: output file exists'
    Assert ((Get-Sha256File $outCkpt1) -eq $quantSha) 'happy: output SHA-256 equals staged quant file'
    $reports = Get-ExportReports
    Assert ($reports.Count -eq 1) 'happy: report JSON exists'
    $j = $null
    if ($reports.Count -ge 1) {
        $j = Get-Content -LiteralPath $reports[0].FullName -Raw -Encoding utf8 | ConvertFrom-Json
    }
    $reportOk = $false
    if ($null -ne $j) {
        $shards = @($j.base_shards)
        $ah = $j.adapter_hashes
        $hasAh = ($null -ne $ah) -and ($null -ne $ah.PSObject.Properties['adapter_config.json']) -and ($null -ne $ah.PSObject.Properties['adapter_model.safetensors'])
        $shardDigests = $false
        if ($shards.Count -eq 2) {
            $shardDigests = ($null -ne $shards[0].sha256_read1) -and ($null -ne $shards[0].sha256_read2) -and ($null -ne $shards[1].sha256_read1) -and ($null -ne $shards[1].sha256_read2)
        }
        $fs = $j.free_space
        $hasFree = ($null -ne $fs) -and ($null -ne $fs.before) -and ($null -ne $fs.after) -and ($null -ne $fs.before.cFreeBytes) -and ($null -ne $fs.before.eFreeBytes) -and ($null -ne $fs.after.cFreeBytes) -and ($null -ne $fs.after.eFreeBytes)
        $reportOk = ($j.name -eq 'tstexp') -and ($j.base_revision -eq $rev) -and $hasAh -and $shardDigests -and ($j.chat_template_sha256 -eq $templateSha) -and ($null -ne $j.output.size) -and ($j.output.sha256_read1 -eq $quantSha) -and ($j.output.sha256_read2 -eq $quantSha) -and $hasFree
    }
    Assert $reportOk 'happy: report fields (name, base revision, adapter hashes, shard read digests, chat-template digest, output size/digests, free-space)'
    $stepNames = @('pre-check', 'adapter_hash', 'base_shards', 'staging', 'merge', 'convert', 'delete_merged', 'quantise', 'chat_template', 'publish')
    $timingOk = $false
    if ($null -ne $j -and $null -ne $j.steps) {
        $timingOk = $true
        foreach ($sn in $stepNames) {
            $st = $j.steps.$sn
            if ($null -eq $st) { $timingOk = $false; break }
            $ds = $st.durationSeconds
            $hasDur = ($null -ne $ds) -and ([double]$ds -ge 0)
            if ([string]::IsNullOrWhiteSpace([string]$st.start) -or [string]::IsNullOrWhiteSpace([string]$st.end) -or -not $hasDur) {
                $timingOk = $false
                break
            }
        }
    }
    Assert $timingOk 'happy: step timings start, end, durationSeconds'
    $stagingKids = @()
    if (Test-Path -LiteralPath $eRoot) { $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force) }
    Assert ($stagingKids.Count -eq 0) 'happy: run staging folder is gone'
    $log = Get-ShimLogLines
    Assert ($log.Count -eq 4) 'happy: exactly four docker runs'
    $steps = @($log | ForEach-Object { if ($_ -match 'step=(\S+)') { $Matches[1] } })
    Assert (($steps -join ',') -eq 'merge,convert,quantise,template') 'happy: docker step order merge, convert, quantise, template'
    $netOk = $true
    $tokOk = $true
    foreach ($ln in $log) {
        if ($ln -notmatch 'network_none=True') { $netOk = $false }
        if ($ln -notmatch 'gpus_or_token=False') { $tokOk = $false }
    }
    Assert $netOk 'happy: each docker run has --network none'
    Assert $tokOk 'happy: no --gpus and no TOKEN on docker args'
    $qline = @($log | Where-Object { $_ -match 'step=quantise' })
    Assert ($qline.Count -eq 1 -and $qline[0] -match 'merged_exists=False') 'happy: quantise records merged already absent'
    Assert ((Get-GuardFingerprint) -eq $fpBeforeHappy) 'happy: sentinel, adapter files, and base shards unchanged'

    # --- 4. Two checkpoints ---
    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1, $adapter2) -Execute)
    Assert ($r.Code -eq 0) 'two-ckpt: exit 0'
    Assert ((Test-Path -LiteralPath $outCkpt1 -PathType Leaf) -and (Get-Sha256File $outCkpt1) -eq $quantSha) 'two-ckpt: ckpt1 output'
    Assert ((Test-Path -LiteralPath $outCkpt2 -PathType Leaf) -and (Get-Sha256File $outCkpt2) -eq $quantSha) 'two-ckpt: ckpt2 output'
    Assert ((Get-ExportReports).Count -eq 2) 'two-ckpt: two reports'

    # --- 5. Adapter hash mismatch ---
    Clear-Artifacts
    $badHashDir = Join-Path $root 'adapter-bad-hash'
    if (Test-Path -LiteralPath $badHashDir) { Remove-Item -LiteralPath $badHashDir -Recurse -Force }
    Copy-Item -LiteralPath $adapter1 -Destination $badHashDir -Recurse
    $listed = Join-Path $badHashDir 'adapter_model.safetensors'
    $hashDoc = Get-Content -LiteralPath (Join-Path $badHashDir 'adapter-hashes.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $expectedListed = ([string]$hashDoc.files.PSObject.Properties['adapter_model.safetensors'].Value).ToLower()
    Invoke-FlipFirstByte $listed
    $actualListed = Get-Sha256File $listed
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($badHashDir) -Execute)
    $text = Out-Text $r.Lines
    $log = Get-ShimLogLines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0) 'adapter-mismatch: refused'
    Assert ($text.Contains($expectedListed) -and $text.Contains($actualListed)) 'adapter-mismatch: refusal names both digests'
    Assert ($log.Count -eq 0) 'adapter-mismatch: no docker call'
    Assert ($stagingKids.Count -eq 0) 'adapter-mismatch: no staging folder'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 'adapter-mismatch: no output file'

    # --- 6. Adapter manifest problems ---
    Clear-Artifacts
    $missJson = Join-Path $root 'adapter-miss-json'
    if (Test-Path -LiteralPath $missJson) { Remove-Item -LiteralPath $missJson -Recurse -Force }
    Copy-Item -LiteralPath $adapter1 -Destination $missJson -Recurse
    Remove-Item -LiteralPath (Join-Path $missJson 'adapter-hashes.json') -Force
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($missJson) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'missing adapter-hashes\.json' -and (Get-ShimLogLines).Count -eq 0) 'manifest: missing adapter-hashes.json refused, no docker'

    Clear-Artifacts
    $missListed = Join-Path $root 'adapter-miss-listed'
    if (Test-Path -LiteralPath $missListed) { Remove-Item -LiteralPath $missListed -Recurse -Force }
    Copy-Item -LiteralPath $adapter1 -Destination $missListed -Recurse
    $cfgOnly = Get-Sha256File (Join-Path $missListed 'adapter_config.json')
    $partial = @{ files = [ordered]@{ 'adapter_config.json' = $cfgOnly } } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $missListed 'adapter-hashes.json'), $partial)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($missListed) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'must list adapter_config\.json and adapter_model\.safetensors' -and (Get-ShimLogLines).Count -eq 0) 'manifest: does not list adapter_model.safetensors refused, no docker'

    Clear-Artifacts
    $missFile = Join-Path $root 'adapter-miss-file'
    if (Test-Path -LiteralPath $missFile) { Remove-Item -LiteralPath $missFile -Recurse -Force }
    Copy-Item -LiteralPath $adapter1 -Destination $missFile -Recurse
    Remove-Item -LiteralPath (Join-Path $missFile 'adapter_model.safetensors') -Force
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($missFile) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'actual=missing' -and (Get-ShimLogLines).Count -eq 0) 'manifest: listed file missing refused, no docker'

    # --- T2 adapter-manifest regressions ---
    Clear-Artifacts
    $outsidePath = Join-Path $root 'outside.bin'
    $outsideBytes = [Text.Encoding]::UTF8.GetBytes('OUTSIDE-BYTES')
    [IO.File]::WriteAllBytes($outsidePath, $outsideBytes)
    $outsideSha = Get-Sha256File $outsidePath
    $escapeDir = Join-Path $root 'adapter-escape'
    if (Test-Path -LiteralPath $escapeDir) { Remove-TreeSafe $escapeDir }
    Copy-Item -LiteralPath $adapter1 -Destination $escapeDir -Recurse
    $h1e = Get-Sha256File (Join-Path $escapeDir 'adapter_config.json')
    $h2e = Get-Sha256File (Join-Path $escapeDir 'adapter_model.safetensors')
    $outH = Get-Sha256File $outsidePath
    $escJson = '{"files":{"adapter_config.json":"' + $h1e + '","adapter_model.safetensors":"' + $h2e + '","../outside.bin":"' + $outH + '"}}'
    [IO.File]::WriteAllText((Join-Path $escapeDir 'adapter-hashes.json'), $escJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($escapeDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'invalid relative path' -and (Get-ShimLogLines).Count -eq 0) 't2a: ../outside.bin refused, no docker'
    Assert ((Get-Sha256File $outsidePath) -eq $outsideSha) 't2a: outside.bin unchanged'

    Clear-Artifacts
    $unlistedTop = Join-Path $root 'adapter-unlisted-top'
    if (Test-Path -LiteralPath $unlistedTop) { Remove-TreeSafe $unlistedTop }
    Copy-Item -LiteralPath $adapter1 -Destination $unlistedTop -Recurse
    [IO.File]::WriteAllText((Join-Path $unlistedTop 'extra.txt'), 'unlisted-top')
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($unlistedTop) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'unlisted file' -and (Get-ShimLogLines).Count -eq 0) 't2b: top-level unlisted file refused, no docker'

    Clear-Artifacts
    $unlistedSub = Join-Path $root 'adapter-unlisted-sub'
    if (Test-Path -LiteralPath $unlistedSub) { Remove-TreeSafe $unlistedSub }
    Copy-Item -LiteralPath $adapter1 -Destination $unlistedSub -Recurse
    $subDir = Join-Path $unlistedSub 'nested'
    New-Item -ItemType Directory -Path $subDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $subDir 'extra.bin'), 'unlisted-nested')
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($unlistedSub) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'unlisted file' -and (Get-ShimLogLines).Count -eq 0) 't2b: nested unlisted file refused, no docker'

    Clear-Artifacts
    $driveDir = Join-Path $root 'adapter-drive-key'
    if (Test-Path -LiteralPath $driveDir) { Remove-TreeSafe $driveDir }
    Copy-Item -LiteralPath $adapter1 -Destination $driveDir -Recurse
    $h1d = Get-Sha256File (Join-Path $driveDir 'adapter_config.json')
    $h2d = Get-Sha256File (Join-Path $driveDir 'adapter_model.safetensors')
    $driveJson = '{"files":{"adapter_config.json":"' + $h1d + '","adapter_model.safetensors":"' + $h2d + '","C:/adapter_config.json":"' + $h1d + '"}}'
    [IO.File]::WriteAllText((Join-Path $driveDir 'adapter-hashes.json'), $driveJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($driveDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'invalid relative path' -and (Get-ShimLogLines).Count -eq 0) 't2c: drive-qualified key refused, no docker'

    Clear-Artifacts
    $rootedDir = Join-Path $root 'adapter-rooted-key'
    if (Test-Path -LiteralPath $rootedDir) { Remove-TreeSafe $rootedDir }
    Copy-Item -LiteralPath $adapter1 -Destination $rootedDir -Recurse
    $h1r = Get-Sha256File (Join-Path $rootedDir 'adapter_config.json')
    $h2r = Get-Sha256File (Join-Path $rootedDir 'adapter_model.safetensors')
    $rootedJson = '{"files":{"adapter_config.json":"' + $h1r + '","adapter_model.safetensors":"' + $h2r + '","/adapter_config.json":"' + $h1r + '"}}'
    [IO.File]::WriteAllText((Join-Path $rootedDir 'adapter-hashes.json'), $rootedJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($rootedDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'invalid relative path' -and (Get-ShimLogLines).Count -eq 0) 't2c: rooted key refused, no docker'

    Clear-Artifacts
    $slashDir = Join-Path $root 'adapter-backslash-key'
    if (Test-Path -LiteralPath $slashDir) { Remove-TreeSafe $slashDir }
    Copy-Item -LiteralPath $adapter1 -Destination $slashDir -Recurse
    $h1s = Get-Sha256File (Join-Path $slashDir 'adapter_config.json')
    $h2s = Get-Sha256File (Join-Path $slashDir 'adapter_model.safetensors')
    $slashJson = '{"files":{"adapter_config.json":"' + $h1s + '","adapter_model.safetensors":"' + $h2s + '","sub\\adapter_config.json":"' + $h1s + '"}}'
    [IO.File]::WriteAllText((Join-Path $slashDir 'adapter-hashes.json'), $slashJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($slashDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'invalid relative path' -and (Get-ShimLogLines).Count -eq 0) 't2c: backslash key refused, no docker'

    Clear-Artifacts
    $juncAdapter = Join-Path $root 'adapter-junc'
    if (Test-Path -LiteralPath $juncAdapter) { Remove-TreeSafe $juncAdapter }
    Copy-Item -LiteralPath $adapter1 -Destination $juncAdapter -Recurse
    $via = Join-Path $juncAdapter 'via'
    try {
        New-Item -ItemType Junction -Path $via -Target $jTarget | Out-Null
        $h1j = Get-Sha256File (Join-Path $juncAdapter 'adapter_config.json')
        $h2j = Get-Sha256File (Join-Path $juncAdapter 'adapter_model.safetensors')
        $juncJson = '{"files":{"adapter_config.json":"' + $h1j + '","adapter_model.safetensors":"' + $h2j + '","via/junction-sentinel.bin":"' + $jSentinelSha + '"}}'
        [IO.File]::WriteAllText((Join-Path $juncAdapter 'adapter-hashes.json'), $juncJson)
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($juncAdapter) -Execute)
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'reparse point' -and (Get-ShimLogLines).Count -eq 0) 't2d: junction path refused, no docker'
        Assert ((Test-Path -LiteralPath $jSentinel -PathType Leaf) -and (Get-Sha256File $jSentinel) -eq $jSentinelSha) 't2d: junction target sentinel unchanged'
    } finally {
        if (Test-Path -LiteralPath $via) {
            $itVia = Get-Item -LiteralPath $via -Force
            if ($itVia.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [System.IO.Directory]::Delete($via, $false)
            }
        }
    }

    Clear-Artifacts
    $caseDir = Join-Path $root 'adapter-case-dup'
    if (Test-Path -LiteralPath $caseDir) { Remove-TreeSafe $caseDir }
    Copy-Item -LiteralPath $adapter1 -Destination $caseDir -Recurse
    $h1c = Get-Sha256File (Join-Path $caseDir 'adapter_config.json')
    $h2c = Get-Sha256File (Join-Path $caseDir 'adapter_model.safetensors')
    $caseJson = '{"files":{"adapter_config.json":"' + $h1c + '","Adapter_config.json":"' + $h1c + '","adapter_model.safetensors":"' + $h2c + '"}}'
    [IO.File]::WriteAllText((Join-Path $caseDir 'adapter-hashes.json'), $caseJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($caseDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'duplicate|different casing' -and (Get-ShimLogLines).Count -eq 0) 't2e: case-duplicate keys refused, no docker'

    Clear-Artifacts
    $badValDir = Join-Path $root 'adapter-bad-hash-value'
    if (Test-Path -LiteralPath $badValDir) { Remove-TreeSafe $badValDir }
    Copy-Item -LiteralPath $adapter1 -Destination $badValDir -Recurse
    $h1b = Get-Sha256File (Join-Path $badValDir 'adapter_config.json')
    $h2b = Get-Sha256File (Join-Path $badValDir 'adapter_model.safetensors')
    $badValJson = '{"files":{"adapter_config.json":"not-a-hash","adapter_model.safetensors":"' + $h2b + '"}}'
    [IO.File]::WriteAllText((Join-Path $badValDir 'adapter-hashes.json'), $badValJson)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($badValDir) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'invalid hash value' -and (Get-ShimLogLines).Count -eq 0) 't2f: bad hash value refused, no docker'
    $null = $h1b

    # --- 7. Base shard mismatch ---
    Clear-Artifacts
    Invoke-FlipFirstByte $shard1Path
    $readAfter = Get-Sha256File $shard1Path
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $log = Get-ShimLogLines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0) 'base-shard: refused'
    Assert ($text.Contains($shard1Sha) -and $text.Contains($readAfter) -and $text -match 'read1=' -and $text -match 'read2=') 'base-shard: expected digest and both read digests'
    Assert ($log.Count -eq 0) 'base-shard: no docker call'
    Assert ($stagingKids.Count -eq 0) 'base-shard: no staging folder'
    [IO.File]::WriteAllBytes($shard1Path, $shard1Bytes)
    Assert ((Get-Sha256File $shard1Path) -eq $shard1Sha) 'base-shard: fixture restored'

    # --- T3 base-shard regressions ---
    Clear-Artifacts
    $idxExtra = [ordered]@{
        weight_map = [ordered]@{
            'model.embed_tokens.weight' = 'model-00001-of-00002.safetensors'
            'lm_head.weight'            = 'model-00002-of-00002.safetensors'
            'extra.weight'              = 'model-00003-of-00003.safetensors'
        }
    }
    [IO.File]::WriteAllText($indexPath, ($idxExtra | ConvertTo-Json -Depth 8))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3a: extra indexed shard refused, no docker, no staging'
    Write-FixtureIndex

    Clear-Artifacts
    $expMissing = [ordered]@{}
    $expMissing["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(
            [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
            [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
            [ordered]@{ path = 'model-00003-of-00003.safetensors'; sha256 = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; size = 1 }
        )
    }
    [IO.File]::WriteAllText($expectedPath, ($expMissing | ConvertTo-Json -Depth 8))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3b: expected shard absent refused, no docker, no staging'
    Write-FixtureExpected

    Clear-Artifacts
    $extraShard = Join-Path $snapDir 'extra.safetensors'
    [IO.File]::WriteAllBytes($extraShard, [Text.Encoding]::UTF8.GetBytes('EXTRA-SHARD'))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3c: unexpected present shard refused, no docker, no staging'
    if (Test-Path -LiteralPath $extraShard) { Remove-Item -LiteralPath $extraShard -Force }

    Clear-Artifacts
    Remove-Item -LiteralPath $indexPath -Force
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'model\.safetensors\.index\.json' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3d: missing index refused, no docker, no staging'
    Write-FixtureIndex

    Clear-Artifacts
    $expDots = [ordered]@{}
    $expDots["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(
            [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
            [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
            [ordered]@{ path = '../model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
        )
    }
    [IO.File]::WriteAllText($expectedPath, ($expDots | ConvertTo-Json -Depth 8))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match '\.\.' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3e: expected path with .. refused, no docker, no staging'
    Write-FixtureExpected

    Clear-Artifacts
    $expDup = [ordered]@{}
    $expDup["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(
            [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
            [ordered]@{ path = 'Model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
            [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
        )
    }
    [IO.File]::WriteAllText($expectedPath, ($expDup | ConvertTo-Json -Depth 8))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'duplicate' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3f: duplicate expected path refused, no docker, no staging'
    Write-FixtureExpected

    # --- 8. Staging under the C root ---
    Clear-Artifacts
    $badStaging = Join-Path $cRoot 'staging-under-c'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -StagingRoot $badStaging)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'under CHotRoot' -and (Get-ShimLogLines).Count -eq 0) 'staging-under-c: refused'

    # --- 9. Free space ---
    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -ProbeEFreeBytes $eFreeLow)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'E: free' -and (Get-ShimLogLines).Count -eq 0) 'free-space: E below floor refused before docker'

    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -ProbeCFreeBytes $cFreeLow)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'C: free' -and (Get-ShimLogLines).Count -eq 0) 'free-space: C below floor refused before docker'

    # --- 10. Output already exists ---
    Clear-Artifacts
    $m2dir = Join-Path $cRoot 'models\m2'
    New-Item -ItemType Directory -Path $m2dir -Force | Out-Null
    $existingBytes = [Text.Encoding]::UTF8.GetBytes('ALREADY-EXISTS')
    [IO.File]::WriteAllBytes($outCkpt1, $existingBytes)
    $existSha = Get-Sha256File $outCkpt1
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'output already exists') 'output-exists: refused'
    Assert ((Get-Sha256File $outCkpt1) -eq $existSha) 'output-exists: existing file bytes unchanged'

    # --- T4(b) pre-existing .part sentinel ---
    Clear-Artifacts
    $m2dir = Join-Path $cRoot 'models\m2'
    New-Item -ItemType Directory -Path $m2dir -Force | Out-Null
    $partPath = $outCkpt1 + '.part'
    $partBytes = [Text.Encoding]::UTF8.GetBytes('PART-SENTINEL')
    [IO.File]::WriteAllBytes($partPath, $partBytes)
    $partSha = Get-Sha256File $partPath
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    Assert ($r.Code -eq 0) 'part-sentinel happy: exit 0'
    Assert ((Get-Sha256File $partPath) -eq $partSha) 'part-sentinel happy: .part unchanged'
    Assert (Test-Path -LiteralPath $outCkpt1 -PathType Leaf) 'part-sentinel happy: dest written'

    Clear-Artifacts
    $m2dir = Join-Path $cRoot 'models\m2'
    New-Item -ItemType Directory -Path $m2dir -Force | Out-Null
    [IO.File]::WriteAllBytes($partPath, $partBytes)
    $partSha = Get-Sha256File $partPath
    $env:HL_SHIM_FAIL = 'merge'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    Assert ($r.Code -ne 0) 'part-sentinel fail: run fails'
    Assert ((Get-Sha256File $partPath) -eq $partSha) 'part-sentinel fail: .part unchanged'
    $env:HL_SHIM_FAIL = ''

    # --- T4(c) concurrent dest creation ---
    Clear-Artifacts
    $concurrentBytes = [Text.Encoding]::UTF8.GetBytes('CONCURRENT-SENTINEL')
    $env:HL_SHIM_CREATE_DEST = $outCkpt1
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $kept = Get-KeptStaging $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'output already exists') 'concurrent: refused already exists'
    Assert ((Test-Path -LiteralPath $outCkpt1 -PathType Leaf) -and (Get-Sha256File $outCkpt1) -eq (Get-Sha256Bytes $concurrentBytes)) 'concurrent: sentinel bytes unchanged'
    Assert (-not (Test-Path -LiteralPath ($outCkpt1 + '.part'))) 'concurrent: no .part'
    Assert ($null -ne $kept -and (Test-Path -LiteralPath (Join-Path $kept '.hl-export-staging') -PathType Leaf)) 'concurrent: staging kept with marker'
    Assert ((Get-ExportReports).Count -eq 0) 'concurrent: no report file'
    $env:HL_SHIM_CREATE_DEST = ''

    # --- 11. Chat-template mismatch ---
    Clear-Artifacts
    $env:HL_FAKE_TEMPLATE_SHA = 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $kept = Get-KeptStaging $r.Lines
    Assert ($r.Code -ne 0) 'template-mismatch: run fails'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 'template-mismatch: no output file'
    Assert (-not (Test-Path -LiteralPath ($outCkpt1 + '.part'))) 'template-mismatch: no .part file'
    Assert ($text.Contains('staging kept for inspection:')) 'template-mismatch: output says staging kept'
    Assert ($null -ne $kept -and (Test-Path -LiteralPath (Join-Path $kept '.hl-export-staging') -PathType Leaf)) 'template-mismatch: run staging kept with marker'
    $env:HL_FAKE_TEMPLATE_SHA = $templateSha

    # --- 12. Docker step failure ---
    Clear-Artifacts
    $fpBeforeFail = Get-GuardFingerprint
    $env:HL_SHIM_FAIL = 'merge'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $kept = Get-KeptStaging $r.Lines
    Assert ($r.Code -ne 0) 'docker-fail merge: run fails'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 'docker-fail merge: no output file'
    Assert ($null -ne $kept -and (Test-Path -LiteralPath $kept)) 'docker-fail merge: staging kept'
    Assert ((Get-GuardFingerprint) -eq $fpBeforeFail) 'docker-fail merge: nothing outside staging changed'
    $env:HL_SHIM_FAIL = ''

    Clear-Artifacts
    $fpBeforeFail = Get-GuardFingerprint
    $env:HL_SHIM_FAIL = 'quantise'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $kept = Get-KeptStaging $r.Lines
    Assert ($r.Code -ne 0) 'docker-fail quantise: run fails'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 'docker-fail quantise: no output file'
    Assert ($null -ne $kept -and (Test-Path -LiteralPath $kept)) 'docker-fail quantise: staging kept'
    Assert ((Get-GuardFingerprint) -eq $fpBeforeFail) 'docker-fail quantise: nothing outside staging changed'
    $env:HL_SHIM_FAIL = ''

    # --- T-A ancestor junction on StagingRoot ---
    Clear-Artifacts
    $ajOutside = Join-Path $root 'ancestor-outside'
    $ajLink = Join-Path $root 'ancestor-junction'
    if (Test-Path -LiteralPath $ajLink) { Remove-TreeSafe $ajLink }
    if (Test-Path -LiteralPath $ajOutside) { Remove-TreeSafe $ajOutside }
    New-Item -ItemType Directory -Path $ajOutside -Force | Out-Null
    New-Item -ItemType Junction -Path $ajLink -Target $ajOutside | Out-Null
    $ajStaging = Join-Path $ajLink 'stage'
    $ancestRunId = 'ancest-run-1'
    $physStage = Join-Path $ajOutside 'stage'
    $physRun = Join-Path $physStage $ancestRunId
    $physMerged = Join-Path $physRun 'merged'
    New-Item -ItemType Directory -Path $physMerged -Force | Out-Null
    $ancestSentinel = Join-Path $physMerged 'keep-ancestor.bin'
    [IO.File]::WriteAllBytes($ancestSentinel, [Text.Encoding]::UTF8.GetBytes('KEEP-ANCESTOR'))
    $ancestSentSha = Get-Sha256File $ancestSentinel
    try {
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -StagingRoot $ajStaging -RunId $ancestRunId)
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'reparse point') 't-a: ancestor junction export refused'
        Assert ((Get-ShimLogLines).Count -eq 0) 't-a: ancestor junction no docker'
        Assert ((Get-Sha256File $ancestSentinel) -eq $ancestSentSha) 't-a: ancestor sentinel unchanged after export refuse'

        New-Item -ItemType Directory -Path $physRun -Force | Out-Null
        $foreignTok = [guid]::NewGuid().ToString('d')
        $markerDoc = @{ runId = $ancestRunId; ownershipToken = $foreignTok } | ConvertTo-Json -Compress
        [IO.File]::WriteAllText((Join-Path $physRun '.hl-export-staging'), $markerDoc)
        $logicalMerged = Join-Path (Join-Path $ajStaging $ancestRunId) 'merged'
        $probe = Invoke-RemoveStagedTreeProbe -StagingRootArg $ajStaging -CHotRootArg $cRoot -Target $logicalMerged -OwnershipToken $foreignTok
        Assert ($probe.Code -ne 0 -and $probe.Text -match 'reparse point') 't-a: Remove-StagedTree refuses ancestor junction route'
        Assert ((Get-Sha256File $ancestSentinel) -eq $ancestSentSha) 't-a: ancestor sentinel unchanged after Remove-StagedTree refuse'
    } finally {
        if (Test-Path -LiteralPath $ajLink) {
            $ajItem = Get-Item -LiteralPath $ajLink -Force
            if ($ajItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [System.IO.Directory]::Delete($ajLink, $false)
            }
        }
        if (Test-Path -LiteralPath $ajOutside) { Remove-TreeSafe $ajOutside }
    }

    # --- T-B run directory collision and foreign ownership token ---
    Clear-Artifacts
    $collisionRunId = 'collision-run-1'
    $preRun = Join-Path $eRoot $collisionRunId
    New-Item -ItemType Directory -Path $preRun -Force | Out-Null
    $priorToken = [guid]::NewGuid().ToString('d')
    $priorMarker = @{ runId = $collisionRunId; createdUtc = '2020-01-01T00:00:00Z'; ownershipToken = $priorToken } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $preRun '.hl-export-staging'), $priorMarker)
    $collisionSentinel = Join-Path $preRun 'collision-sentinel.bin'
    [IO.File]::WriteAllBytes($collisionSentinel, [Text.Encoding]::UTF8.GetBytes('COLLISION-SENTINEL'))
    $collisionSentSha = Get-Sha256File $collisionSentinel
    $priorMarkerSha = Get-Sha256File (Join-Path $preRun '.hl-export-staging')
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -RunId $collisionRunId)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'run folder already exists') 't-b: pre-existing run folder refused'
    Assert ((Get-ShimLogLines).Count -eq 0) 't-b: collision no docker'
    Assert (-not (Test-Path -LiteralPath $outCkpt1)) 't-b: collision no output file'
    Assert ((Get-Sha256File $collisionSentinel) -eq $collisionSentSha) 't-b: collision sentinel unchanged'
    Assert ((Get-Sha256File (Join-Path $preRun '.hl-export-staging')) -eq $priorMarkerSha) 't-b: prior marker unchanged'

    Clear-Artifacts
    $foreignRunId = 'foreign-token-run'
    $foreignRun = Join-Path $eRoot $foreignRunId
    New-Item -ItemType Directory -Path $foreignRun -Force | Out-Null
    $foreignTok = [guid]::NewGuid().ToString('d')
    $wrongTok = [guid]::NewGuid().ToString('d')
    $foreignMarker = @{ runId = $foreignRunId; ownershipToken = $foreignTok } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $foreignRun '.hl-export-staging'), $foreignMarker)
    $foreignSentinel = Join-Path $foreignRun 'foreign-sentinel.bin'
    [IO.File]::WriteAllBytes($foreignSentinel, [Text.Encoding]::UTF8.GetBytes('FOREIGN-SENTINEL'))
    $foreignSentSha = Get-Sha256File $foreignSentinel
    $foreignMarkerSha = Get-Sha256File (Join-Path $foreignRun '.hl-export-staging')
    $probe = Invoke-RemoveStagedTreeProbe -StagingRootArg $eRoot -CHotRootArg $cRoot -Target $foreignRun -OwnershipToken $wrongTok
    Assert ($probe.Code -ne 0 -and $probe.Text -match 'ownership token') 't-b: Remove-StagedTree refuses foreign ownership token'
    Assert ((Get-Sha256File $foreignSentinel) -eq $foreignSentSha) 't-b: foreign sentinel unchanged'
    Assert ((Get-Sha256File (Join-Path $foreignRun '.hl-export-staging')) -eq $foreignMarkerSha) 't-b: foreign marker unchanged'

    # --- T-C normal run removes owned staging (covered by happy path; re-check marker token cleanup) ---
    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -RunId 'owned-run-1')
    Assert ($r.Code -eq 0) 't-c: owned run exit 0'
    $stagingKids = @()
    if (Test-Path -LiteralPath $eRoot) { $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force) }
    Assert ($stagingKids.Count -eq 0) 't-c: owned staging folder removed after success'

    # --- 13. Delete guard ---
    Clear-Artifacts
    $env:HL_SHIM_JUNCTION = '1'
    $env:HL_SHIM_JUNCTION_TARGET = $jTarget
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'reparse point') 'delete-guard: fails at delete with reparse-point message'
    Assert ((Test-Path -LiteralPath $jSentinel -PathType Leaf) -and (Get-Sha256File $jSentinel) -eq $jSentinelSha) 'delete-guard: sentinel behind junction still exists'
    $env:HL_SHIM_JUNCTION = ''
    Clear-StagingJunctions
    if ($eRoot -and -not (Test-Path -LiteralPath $eRoot)) {
        New-Item -ItemType Directory -Path $eRoot -Force | Out-Null
    }

    # --- 14. Static checks ---
    $st = Get-Content -LiteralPath $script -Raw -Encoding utf8
    Assert ($st -notmatch '--gpus') 'static: no --gpus'
    Assert ($st -notmatch 'HF_TOKEN') 'static: no HF_TOKEN'
    Assert (-not $st.Contains('Global\homemade-llm-gpu')) 'static: no Global\homemade-llm-gpu'
    $blocks = [regex]::Matches($st, "'run', '--rm'")
    $allHaveNet = $blocks.Count -eq 4
    foreach ($b in $blocks) {
        $close = $st.IndexOf(')', $b.Index)
        $list = if ($close -ge 0) { $st.Substring($b.Index, $close - $b.Index) } else { '' }
        if ($list -notmatch "'--network',\s*'none'") { $allHaveNet = $false }
    }
    Assert $allHaveNet 'static: every docker run argument list includes --network followed by none'

    Assert ((Get-Sha256File $sentinelPath) -eq $sentinelSha) 'guard: C sentinel unchanged at end'
    Assert ((Test-Path -LiteralPath $jSentinel -PathType Leaf) -and (Get-Sha256File $jSentinel) -eq $jSentinelSha) 'guard: junction target sentinel unchanged at end'
} finally {
    function Assert-DeletableScratch([string]$Path) {
        $full = [IO.Path]::GetFullPath($Path)
        $temp = [IO.Path]::GetFullPath($env:TEMP)
        $sep = [IO.Path]::DirectorySeparatorChar
        $prefix = $temp.TrimEnd($sep) + $sep
        if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "cleanup refused: $full is not strictly inside $temp"
        }
        $leaf = [IO.Path]::GetFileName($full.TrimEnd($sep))
        if (-not ($leaf.StartsWith('hl-test-export-adapter-') -or $leaf.StartsWith('hl-test-export-jtarget-'))) {
            throw "cleanup refused: $full leaf not a test scratch name"
        }
    }
    if ($root) { Assert-DeletableScratch $root }
    if ($jTarget) { Assert-DeletableScratch $jTarget }
    if ($root -and (Test-Path -LiteralPath $root)) {
        Remove-TreeSafe $root
    }
    if ($jSentinel -and $jSentinelSha) {
        if (-not (Test-Path -LiteralPath $jSentinel -PathType Leaf)) {
            throw 'junction sentinel missing after scratch cleanup'
        }
        if ((Get-Sha256File $jSentinel) -ne $jSentinelSha) {
            throw 'junction sentinel hash changed after scratch cleanup'
        }
    }
    if ($jTarget -and (Test-Path -LiteralPath $jTarget)) {
        Remove-TreeSafe $jTarget
    }
    if ($null -eq $savedShimLog) { Remove-Item Env:HL_SHIM_LOG -ErrorAction SilentlyContinue } else { $env:HL_SHIM_LOG = $savedShimLog }
    if ($null -eq $savedShimFail) { Remove-Item Env:HL_SHIM_FAIL -ErrorAction SilentlyContinue } else { $env:HL_SHIM_FAIL = $savedShimFail }
    if ($null -eq $savedFakeTemplate) { Remove-Item Env:HL_FAKE_TEMPLATE_SHA -ErrorAction SilentlyContinue } else { $env:HL_FAKE_TEMPLATE_SHA = $savedFakeTemplate }
    if ($null -eq $savedJunction) { Remove-Item Env:HL_SHIM_JUNCTION -ErrorAction SilentlyContinue } else { $env:HL_SHIM_JUNCTION = $savedJunction }
    if ($null -eq $savedJunctionTarget) { Remove-Item Env:HL_SHIM_JUNCTION_TARGET -ErrorAction SilentlyContinue } else { $env:HL_SHIM_JUNCTION_TARGET = $savedJunctionTarget }
    if ($null -eq $savedCreateDest) { Remove-Item Env:HL_SHIM_CREATE_DEST -ErrorAction SilentlyContinue } else { $env:HL_SHIM_CREATE_DEST = $savedCreateDest }
}

if ($fail.Count -gt 0) {
    foreach ($c in $fail) { Write-Output "FAIL  $c" }
    exit 1
}
Write-Output 'test-export-adapter: all cases passed'
exit 0
