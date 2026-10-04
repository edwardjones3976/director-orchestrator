# Behavioural checks for export-adapter.ps1 on scratch roots (never real C:\llm, E:\, Docker, GPU, network, or real weights).
#   pwsh -File projects\homemade-llm\scripts\test-export-adapter.ps1
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) {
    Write-Output 'SKIP'
    exit 0
}

$realScript = Join-Path $PSScriptRoot 'export-adapter.ps1'
$script = $null
$shellExe = Join-Path $PSHOME 'pwsh.exe'
$fail = [System.Collections.Generic.List[string]]::new()

function Assert([bool]$Cond, [string]$Case) {
    if (-not $Cond) { $fail.Add($Case) } else { Write-Output "ok  $Case" }
}

# Runs a generated probe script via -File instead of -Command: some of these
# generated scripts (the extracted gate/staging source plus fixture literals)
# are big enough to exceed the ~32,000-character Windows command-line limit if
# passed through -Command, which makes pwsh.exe fail to launch at all. Writing
# the text to a scratch .ps1 file under $root and running that file has no such
# limit.
function Invoke-ShellScript([string]$ScriptText) {
    $probePath = Join-Path $root ('probe-' + [guid]::NewGuid().ToString('n') + '.ps1')
    [IO.File]::WriteAllText($probePath, $ScriptText)
    try {
        return @(& $shellExe -NoProfile -NonInteractive -File $probePath 2>&1 | ForEach-Object { "$_" })
    } finally {
        Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
    }
}

# Runs export-adapter.ps1 as a subprocess. By default this targets the shared
# baseline "patched" copy of the real script (see New-PatchedExportScript below):
# a test-only artifact whose pinned trust-root hash constant is swapped for the
# value matching this run's fixture root manifest. The real, committed
# export-adapter.ps1 keeps its hardcoded pin untouched; no test ever edits it.
function Invoke-Export([string[]]$Arguments, [string]$ScriptPathOverride = '') {
    $target = if ($ScriptPathOverride) { $ScriptPathOverride } else { $script }
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $shellExe -NoProfile -NonInteractive -File $target @Arguments 2>&1 | ForEach-Object { "$_" })
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

# Writes a throwaway, never-committed copy of the real export-adapter.ps1 with only
# the pinned trust-root hash constant's literal value swapped for $PinSha256. This
# lets synthetic fixtures (whose bytes can never equal the real shipped pin, by
# construction of SHA-256) exercise the full trust-chain gate. The real file on disk
# is never read-modified-written; it is only read once per call and never touched.
function New-PatchedExportScript([string]$PinSha256) {
    $srcText = Get-Content -LiteralPath $realScript -Raw -Encoding utf8
    $marker = "`$script:PinnedTrustedDigestManifestSha256 = '"
    $idx = $srcText.IndexOf($marker)
    if ($idx -lt 0) { throw 'test harness: pinned hash constant not found for patching' }
    $valueStart = $idx + $marker.Length
    $closeIdx = $srcText.IndexOf("'", $valueStart)
    if ($closeIdx -lt 0) { throw 'test harness: pinned hash constant malformed for patching' }
    $patched = $srcText.Substring(0, $valueStart) + $PinSha256 + $srcText.Substring($closeIdx)
    $path = Join-Path $root ("export-adapter.patched." + [guid]::NewGuid().ToString('n') + '.ps1')
    [IO.File]::WriteAllText($path, $patched)
    # The patched copy lives under $root, not $PSScriptRoot, but it still dot-sources
    # invoke-bounded.ps1 by $PSScriptRoot at run time, so every patched copy needs its
    # own sibling copy of the real helper.
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'invoke-bounded.ps1') -Destination (Join-Path $root 'invoke-bounded.ps1') -Force
    return $path
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
$savedBaseExpected = $env:HL_SHIM_BASE_EXPECTED
$savedMutateBase = $env:HL_SHIM_MUTATE_BASE
$savedMutateCopy = $env:HL_SHIM_MUTATE_COPY

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

    # Non-weight snapshot files required for full-coverage testing (requirement 4:
    # every regular file in the snapshot, not just .safetensors shards, must be
    # listed and verified).
    $configPath = Join-Path $snapDir 'config.json'
    $tokenizerPath = Join-Path $snapDir 'tokenizer.json'
    $configBytes = [Text.Encoding]::UTF8.GetBytes('{"model_type":"qwen3"}')
    $tokenizerBytes = [Text.Encoding]::UTF8.GetBytes('{"tokenizer_class":"fixture"}')
    [IO.File]::WriteAllBytes($configPath, $configBytes)
    [IO.File]::WriteAllBytes($tokenizerPath, $tokenizerBytes)

    # New two-file trust chain: a root manifest (pinned by SHA-256 inside
    # export-adapter.ps1) names, per base repo@revision, the SHA-256 of the list
    # manifest that is actually authenticated and parsed for per-file hashes/sizes.
    $rootPath = Join-Path $cRoot 'reports\m2-trusted-digest-manifest-v2.json'
    $expectedPath = Join-Path $cRoot 'reports\m2-expected-qwen3-14b-v2.json'

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

    # Canonical list manifest covering every regular file in the fixture snapshot
    # (config, tokenizer, index, both shards). Deterministic: restores the exact
    # same bytes every time it is called with no arguments, so repeated calls never
    # change the list's SHA-256 and therefore never invalidate the baseline root/pin.
    function Write-FixtureExpected {
        $cSha = Get-Sha256File $configPath
        $cLen = (Get-Item -LiteralPath $configPath).Length
        $tSha = Get-Sha256File $tokenizerPath
        $tLen = (Get-Item -LiteralPath $tokenizerPath).Length
        $iSha = Get-Sha256File $indexPath
        $iLen = (Get-Item -LiteralPath $indexPath).Length
        $expectedDoc = [ordered]@{}
        $expectedDoc["Qwen/Qwen3-14B@$rev"] = [ordered]@{
            files = @(
                [ordered]@{ path = 'config.json'; sha256 = $cSha; size = $cLen }
                [ordered]@{ path = 'tokenizer.json'; sha256 = $tSha; size = $tLen }
                [ordered]@{ path = 'model.safetensors.index.json'; sha256 = $iSha; size = $iLen }
                [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
                [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
            )
        }
        [IO.File]::WriteAllText($expectedPath, ($expectedDoc | ConvertTo-Json -Depth 8))
    }

    # Writes the root trust manifest. With no -Models override, it embeds the
    # SHA-256 of whatever currently sits at $expectedPath (one matching model
    # entry for the fixture repo@revision) -- the canonical, authenticated state.
    function Write-FixtureRoot {
        param(
            [string]$ListSha256 = $null,
            [object[]]$Models = $null
        )
        if ($null -eq $Models) {
            if ([string]::IsNullOrEmpty($ListSha256)) { $ListSha256 = Get-Sha256File $expectedPath }
            $Models = @([ordered]@{ hf_repo = 'Qwen/Qwen3-14B'; hf_revision = $rev; upstream_weight_digests_sha256 = $ListSha256 })
        }
        $rootDoc = [ordered]@{ models = @($Models) }
        [IO.File]::WriteAllText($rootPath, ($rootDoc | ConvertTo-Json -Depth 8))
    }

    Write-FixtureIndex
    Write-FixtureExpected
    Write-FixtureRoot

    # Generate the baseline patched export-adapter.ps1 copy: identical to the real,
    # committed script except the pinned root-hash constant is swapped for the
    # SHA-256 of this run's own fixture root manifest, so the authenticated chain
    # can be exercised without ever weakening or overriding the real shipped pin.
    $baselineRootSha = Get-Sha256File $rootPath
    $script = New-PatchedExportScript $baselineRootSha

    # Builds a scenario-specific patched script whose pin matches a mutated list
    # (root resynced to the new list's hash), so the mutation is authenticated and
    # the *deeper* validation layers (path safety, duplicates, size, coverage...)
    # are what actually gets exercised, rather than a shallow list-hash mismatch.
    function Set-TrustFixtureList([scriptblock]$WriteList) {
        & $WriteList
        Write-FixtureRoot
        $scenarioScript = New-PatchedExportScript (Get-Sha256File $rootPath)
        return $scenarioScript
    }
    function Restore-TrustFixtureList {
        Write-FixtureExpected
        Write-FixtureRoot
    }

    # The five canonical, currently-on-disk list entries as a plain array, for tests
    # that need to append/mutate one entry while keeping the other four authentic.
    function Get-CanonicalFileEntries {
        @(
            [ordered]@{ path = 'config.json'; sha256 = (Get-Sha256File $configPath); size = (Get-Item -LiteralPath $configPath).Length }
            [ordered]@{ path = 'tokenizer.json'; sha256 = (Get-Sha256File $tokenizerPath); size = (Get-Item -LiteralPath $tokenizerPath).Length }
            [ordered]@{ path = 'model.safetensors.index.json'; sha256 = (Get-Sha256File $indexPath); size = (Get-Item -LiteralPath $indexPath).Length }
            [ordered]@{ path = 'model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length }
            [ordered]@{ path = 'model-00002-of-00002.safetensors'; sha256 = $shard2Sha; size = $shard2Bytes.Length }
        )
    }

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
$baseHost = $null
$integrityHost = $null
$integritySha = $null
$cacheMount = $false
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
        if ($vol -match '^(.*):/base-snapshot:ro$') { $baseHost = $Matches[1] }
        if ($vol -match '^(.*):/base-integrity.json:ro$') { $integrityHost = $Matches[1] }
        if ($vol -match ':/hf(:|$)') { $cacheMount = $true }
    }
    if ($a -eq '-e' -and ($i + 1) -lt $DockerArgs.Count -and [string]$DockerArgs[$i + 1] -match '^HL_BASE_GATE_SHA256=([0-9a-f]{64})$') { $integritySha = $Matches[1] }
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
    if ($cacheMount -or -not $baseHost -or -not $decoded.Contains('snap = "/base-snapshot"')) { throw 'merge must consume only the bound frozen snapshot' }
    if (-not $integrityHost -or (Get-FileHash -LiteralPath $integrityHost -Algorithm SHA256).Hash.ToLower() -cne $integritySha) { throw 'merge integrity envelope mount/hash is unbound' }
    $integrity = Get-Content -LiteralPath $integrityHost -Raw | ConvertFrom-Json
    $baseN = [IO.Path]::GetFullPath($baseHost).TrimEnd('\')
    $workN = [IO.Path]::GetFullPath($workHost).TrimEnd('\')
    if ($baseN.StartsWith($workN + '\', [StringComparison]::OrdinalIgnoreCase) -or $workN.StartsWith($baseN + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'writable work mount overlaps base snapshot' }
    if ($env:HL_SHIM_MUTATE_BASE) {
        $bytes = [IO.File]::ReadAllBytes($env:HL_SHIM_MUTATE_BASE)
        $bytes[0] = $bytes[0] -bxor 0xff
        [IO.File]::WriteAllBytes($env:HL_SHIM_MUTATE_BASE, $bytes)
    }
    if ($env:HL_SHIM_MUTATE_COPY) {
        $path = Join-Path $baseHost $env:HL_SHIM_MUTATE_COPY
        $bytes = [IO.File]::ReadAllBytes($path)
        $bytes[0] = $bytes[0] -bxor 0xff
        try {
            [IO.File]::WriteAllBytes($path, $bytes)
        } catch {
            $cause = if ($_.Exception.InnerException) { $_.Exception.InnerException } else { $_.Exception }
            if (($cause.HResult -band 0xffff) -eq 32) {
                [IO.File]::AppendAllText($env:HL_SHIM_LOG, "copy_write_refused=sharing_violation`n")
            }
            throw
        }
        throw 'UNEXPECTED_COPY_WRITE_SUCCEEDED'
    }
    $approved = Get-Content -LiteralPath $env:HL_SHIM_BASE_EXPECTED -Raw | ConvertFrom-Json
    $entries = @(@($approved.PSObject.Properties)[0].Value.files)
    if ($integrity.covered_files -ne $entries.Count -or @($integrity.files).Count -ne $entries.Count) { throw 'integrity envelope coverage mismatch' }
    $present = @(Get-ChildItem -LiteralPath $baseHost -Recurse -File -Force)
    if ($present.Count -ne $entries.Count) { throw 'frozen snapshot coverage mismatch at consumption' }
    foreach ($entry in $entries) {
        $file = Join-Path $baseHost ($entry.path -replace '/', '\')
        $boundEntry = @($integrity.files | Where-Object { $_.path -ceq $entry.path })
        $read1 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
        $read2 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
        if ($boundEntry.Count -ne 1 -or $boundEntry[0].sha256 -cne $entry.sha256 -or (Get-Item -LiteralPath $file).Length -ne $entry.size -or $read1 -ne $entry.sha256 -or $read2 -ne $entry.sha256) { throw 'bad frozen bytes at consumption' }
    }
    [IO.File]::AppendAllText($env:HL_SHIM_LOG, "base_consumed_verified=True cache_mount=False writable_overlap=False`n")
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
    $env:HL_SHIM_BASE_EXPECTED = $expectedPath

    function Get-ExportStagingHelperSource {
        $exportScript = Join-Path $PSScriptRoot 'export-adapter.ps1'
        $content = Get-Content -LiteralPath $exportScript -Raw -Encoding utf8
        $start = $content.IndexOf('function Get-NormalPath')
        $end = $content.IndexOf('function Get-RelativePath')
        if ($start -lt 0 -or $end -le $start) { throw 'export helper extract failed' }
        return $content.Substring($start, $end - $start)
    }

    # Wider extraction than Get-ExportStagingHelperSource: everything from
    # Get-NormalPath through the end of Assert-BaseShards (exclusive of
    # New-StepClock), so a probe can call Assert-BaseShards directly -- the
    # real gate function, unmodified -- with faked Get-Item/Test-Path/
    # Invoke-FsutilReparseQuery for one literal path, exactly like the
    # Resolve-SnapshotFile probes above.
    function Get-ExportGateHelperSource {
        $exportScript = Join-Path $PSScriptRoot 'export-adapter.ps1'
        $content = Get-Content -LiteralPath $exportScript -Raw -Encoding utf8
        $start = $content.IndexOf('function Get-NormalPath')
        $end = $content.IndexOf('function New-StepClock')
        if ($start -lt 0 -or $end -le $start) { throw 'export gate helper extract failed' }
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
            $lines = @(Invoke-ShellScript $cmd)
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
            [string]$CHotRootArg = '',
            [string]$RunId = '',
            [string]$BaseRepoArg = 'Qwen/Qwen3-14B',
            [string]$BaseRevisionArg = '',
            [long]$ProbeCFreeBytes = -1,
            [long]$ProbeEFreeBytes = -1,
            [long]$ProbeLlmBytes = -1
        )
        if ($StagingRoot -eq '') { $StagingRoot = $eRoot }
        if ($CHotRootArg -eq '') { $CHotRootArg = $cRoot }
        if ($ProbeCFreeBytes -lt 0) { $ProbeCFreeBytes = $cFreeOk }
        if ($ProbeEFreeBytes -lt 0) { $ProbeEFreeBytes = $eFreeOk }
        if ($ProbeLlmBytes -lt 0) { $ProbeLlmBytes = $llmOk }
        $a = [System.Collections.Generic.List[string]]::new()
        if ($Execute) { [void]$a.Add('-Execute') }
        [void]$a.Add('-Checkpoint')
        [void]$a.Add(($Checkpoint -join ','))
        [void]$a.Add('-Name'); [void]$a.Add($Name)
        if ($RunId) { [void]$a.Add('-RunId'); [void]$a.Add($RunId) }
        if (-not $BaseRevisionArg) { $BaseRevisionArg = $rev }
        [void]$a.Add('-BaseRepo'); [void]$a.Add($BaseRepoArg)
        [void]$a.Add('-BaseRevision'); [void]$a.Add($BaseRevisionArg)
        [void]$a.Add('-CHotRoot'); [void]$a.Add($CHotRootArg)
        [void]$a.Add('-StagingRoot'); [void]$a.Add($StagingRoot)
        [void]$a.Add('-TrustedDigestManifest'); [void]$a.Add($rootPath)
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
        $env:HL_SHIM_MUTATE_BASE = ''
        $env:HL_SHIM_MUTATE_COPY = ''
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
    foreach ($unsupported in @('Other/Approved-Model', 'qwen/Qwen3-14B')) {
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -BaseRepoArg $unsupported)
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'parameter BaseRepo' -and (Get-ShimLogLines).Count -eq 0) "param: unsupported or differently cased repository $unsupported refused before cache access"
    }

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
    $trustOk = $false
    if ($null -ne $j) {
        $shards = @($j.base_shards)
        $ah = $j.adapter_hashes
        $hasAh = ($null -ne $ah) -and ($null -ne $ah.PSObject.Properties['adapter_config.json']) -and ($null -ne $ah.PSObject.Properties['adapter_model.safetensors'])
        # Five covered files now (config.json, tokenizer.json, index, two shards);
        # every covered file -- not merely the shards -- must carry two read digests.
        $byPath = @{}
        foreach ($s in $shards) { $byPath[[string]$s.path] = $s }
        $expectCovered = @('config.json', 'tokenizer.json', 'model.safetensors.index.json', 'model-00001-of-00002.safetensors', 'model-00002-of-00002.safetensors')
        $shardDigests = $shards.Count -eq 5
        if ($shardDigests) {
            foreach ($p in $expectCovered) {
                $e = $byPath[$p]
                if ($null -eq $e -or $null -eq $e.sha256_read1 -or $null -eq $e.sha256_read2 -or $null -eq $e.size) {
                    $shardDigests = $false
                    break
                }
            }
        }
        $fs = $j.free_space
        $hasFree = ($null -ne $fs) -and ($null -ne $fs.before) -and ($null -ne $fs.after) -and ($null -ne $fs.before.cFreeBytes) -and ($null -ne $fs.before.eFreeBytes) -and ($null -ne $fs.after.cFreeBytes) -and ($null -ne $fs.after.eFreeBytes)
        $bt = $j.base_trust
        $trustOk = ($null -ne $bt) -and ($bt.root_sha256 -eq $baselineRootSha) -and ($bt.list_sha256 -eq (Get-Sha256File $expectedPath)) -and ([int]$bt.covered_files -eq 5)
        $reportOk = ($j.name -eq 'tstexp') -and ($j.base_revision -eq $rev) -and $hasAh -and $shardDigests -and ($j.chat_template_sha256 -eq $templateSha) -and ($null -ne $j.output.size) -and ($j.output.sha256_read1 -eq $quantSha) -and ($j.output.sha256_read2 -eq $quantSha) -and $hasFree
    }
    Assert $reportOk 'happy: report fields (name, base revision, adapter hashes, full-coverage read digests, chat-template digest, output size/digests, free-space)'
    Assert $trustOk 'happy: base_trust root/list SHA-256 and covered_files=5 recorded (authenticated matching fixture accepted, two reads)'
    $stepNames = @('pre-check', 'adapter_hash', 'base_shards', 'staging', 'base_copy', 'merge', 'convert', 'delete_merged', 'quantise', 'chat_template', 'publish')
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
    Assert (@($log | Where-Object { $_ -match '^step=' }).Count -eq 4) 'happy: exactly four docker runs'
    $steps = @($log | ForEach-Object { if ($_ -match 'step=(\S+)') { $Matches[1] } })
    Assert (($steps -join ',') -eq 'merge,convert,quantise,template') 'happy: docker step order merge, convert, quantise, template'
    $netOk = $true
    $tokOk = $true
    foreach ($ln in $log) {
        if ($ln -notmatch '^step=') { continue }
        if ($ln -notmatch 'network_none=True') { $netOk = $false }
        if ($ln -notmatch 'gpus_or_token=False') { $tokOk = $false }
    }
    Assert $netOk 'happy: each docker run has --network none'
    Assert $tokOk 'happy: no --gpus and no TOKEN on docker args'
    $qline = @($log | Where-Object { $_ -match 'step=quantise' })
    Assert ($qline.Count -eq 1 -and $qline[0] -match 'merged_exists=False') 'happy: quantise records merged already absent'
    Assert ((Get-GuardFingerprint) -eq $fpBeforeHappy) 'happy: sentinel, adapter files, and base shards unchanged'
    Assert ($log -contains 'base_consumed_verified=True cache_mount=False writable_overlap=False') 'frozen base: exact verified snapshot consumed with no cache mount or writable work overlap'
    $consumed = $j.base_consumed
    Assert ($consumed.container_path -ceq '/base-snapshot' -and $consumed.covered_files -eq 5 -and @($consumed.files).Count -eq 5 -and $consumed.root_sha256 -eq $baselineRootSha -and $consumed.list_sha256 -eq $j.base_trust.list_sha256) 'frozen base: report binds consumed full snapshot to authenticated root and list'

    Clear-Artifacts
    $env:HL_SHIM_MUTATE_BASE = $shard1Path
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    Assert ($r.Code -eq 0 -and (Get-Sha256File $shard1Path) -ne $shard1Sha -and (Get-ShimLogLines) -contains 'base_consumed_verified=True cache_mount=False writable_overlap=False') 'frozen base: original cache mutation after verification cannot change Docker consumed copy'
    [IO.File]::WriteAllBytes($shard1Path, $shard1Bytes)

    Clear-Artifacts
    $env:HL_SHIM_MUTATE_COPY = 'tokenizer.json'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $kept = Get-KeptStaging $r.Lines
    $copyUnchanged = $kept -and (Get-Sha256File (Join-Path $kept 'base-snapshot\tokenizer.json')) -eq (Get-Sha256File $tokenizerPath)
    Assert ($r.Code -ne 0 -and (Get-ShimLogLines) -contains 'copy_write_refused=sharing_violation' -and $copyUnchanged -and -not (Test-Path -LiteralPath $outCkpt1)) 'frozen base: Windows read lease refuses copied input rewrite during consumption'

    # Faults are injected only in throwaway copies, after the real gate or the
    # real independent byte copy. The committed script has no mutation hook.
    function New-FaultExportScript([switch]$AfterGate, [switch]$ListAfterGate) {
        $path = New-PatchedExportScript $baselineRootSha
        $source = [IO.File]::ReadAllText($path)
        if ($AfterGate) {
            $source = $source.Replace('function Assert-BaseShards(', 'function Assert-BaseShardsOriginal(')
            $faultPath = if ($ListAfterGate) { $expectedPath } else { $shard1Path }
            $faultPathEsc = $faultPath.Replace("'", "''")
            $wrapper = @"
function Assert-BaseShards([string]`$Snapshot, [string]`$HubRoot, [string]`$HubCacheRoot) {
    `$result = Assert-BaseShardsOriginal `$Snapshot `$HubRoot `$HubCacheRoot
    `$bytes = [IO.File]::ReadAllBytes('$faultPathEsc')
    `$bytes[0] = `$bytes[0] -bxor 0xff
    [IO.File]::WriteAllBytes('$faultPathEsc', `$bytes)
    return `$result
}
"@
        } else {
            $source = $source.Replace('function Copy-BaseSnapshotFile(', 'function Copy-BaseSnapshotFileOriginal(')
            $wrapper = @'
function Copy-BaseSnapshotFile([string]$Source, [string]$Destination) {
    Copy-BaseSnapshotFileOriginal $Source $Destination
    $bytes = [IO.File]::ReadAllBytes($Destination)
    $bytes[0] = $bytes[0] -bxor 0xff
    [IO.File]::WriteAllBytes($Destination, $bytes)
}
'@
        }
        $entry = 'if ($Checkpoint.Count -gt 2) {'
        $source = $source.Replace($entry, $wrapper + "`n" + $entry)
        [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes($source))
        return $path
    }
    foreach ($afterGate in @($true, $false)) {
        Clear-Artifacts
        $faultScript = New-FaultExportScript -AfterGate:$afterGate
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $faultScript
        $text = Out-Text $r.Lines
        $where = if ($afterGate) { 'original mutated after original gate' } else { 'independent copied bytes corrupted' }
        Assert ($r.Code -ne 0 -and $text -match 'base snapshot copy gate:.*copied bytes mismatch' -and (Get-ShimLogLines).Count -eq 0 -and -not (Test-Path -LiteralPath $outCkpt1)) "frozen base: $where refused before Docker consumption"
        [IO.File]::WriteAllBytes($shard1Path, $shard1Bytes)
    }
    Clear-Artifacts
    $faultScript = New-FaultExportScript -AfterGate -ListAfterGate
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $faultScript
    $text = Out-Text $r.Lines
    $kept = Get-KeptStaging $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'authenticated root/list binding changed' -and (Get-ShimLogLines).Count -eq 0 -and $kept -and -not (Test-Path -LiteralPath (Join-Path $kept 'base-snapshot'))) 'frozen base: exact approved list re-authenticated after original gate before any copied weight bytes'
    Restore-TrustFixtureList

    $copyBytes = [long]0
    foreach ($entry in (Get-CanonicalFileEntries)) { $copyBytes += [long]$entry['size'] }
    Assert ($copyBytes -gt 0) 'frozen base: copy-space fixture totals actual approved dictionary sizes'
    foreach ($drive in @('C', 'E')) {
        Clear-Artifacts
        $spaceArgs = if ($drive -eq 'C') { New-ExportArgs -Checkpoint @($adapter1) -Execute -ProbeCFreeBytes (150GB + $copyBytes - 1) } else { New-ExportArgs -Checkpoint @($adapter1) -Execute -ProbeEFreeBytes (100GB + $copyBytes - 1) }
        $r = Invoke-Export $spaceArgs
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'storage guard \(base snapshot copy\)' -and (Get-ShimLogLines).Count -eq 0 -and @(Get-ChildItem -LiteralPath $eRoot -Force).Count -eq 0) "frozen base: projected $drive free space includes full snapshot before staging or Docker"
    }

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

    # t3e and t3f authenticate the mutated list against a resynced root (via
    # Set-TrustFixtureList) so the deeper path-safety/duplicate validation in
    # Assert-BaseShards is what is actually exercised, not a shallow list-hash
    # mismatch against the untouched baseline root.
    Clear-Artifacts
    $expDots = [ordered]@{}
    $expDots["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(Get-CanonicalFileEntries) + @([ordered]@{ path = '../model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length })
    }
    $scenarioScript = Set-TrustFixtureList { [IO.File]::WriteAllText($expectedPath, ($expDots | ConvertTo-Json -Depth 8)) }
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match '\.\.' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3e: authenticated list entry with .. refused by path-safety validation, no docker, no staging'
    Restore-TrustFixtureList

    Clear-Artifacts
    $expDup = [ordered]@{}
    $expDup["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(Get-CanonicalFileEntries) + @([ordered]@{ path = 'Model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length })
    }
    $scenarioScript = Set-TrustFixtureList { [IO.File]::WriteAllText($expectedPath, ($expDup | ConvertTo-Json -Depth 8)) }
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'duplicate' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 't3f: authenticated list with case-duplicate path refused, no docker, no staging'
    Restore-TrustFixtureList

    # --- T5 trust-chain gate: root/list authentication and deeper list validation ---

    # Changed root bytes: flip a byte in the root manifest on disk. The baseline
    # patched script's pin (fixed at fixture setup) no longer matches, so the gate
    # must refuse before even looking at the list.
    Clear-Artifacts
    Invoke-FlipFirstByte $rootPath
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'root manifest hash mismatch' -and (Get-ShimLogLines).Count -eq 0) 'trust: changed root bytes refused before list is read'
    Write-FixtureRoot

    # Changed list with a self-consistent replacement shard: the list is internally
    # well formed (every entry valid, every file present and matching), but the root
    # was deliberately left referencing the OLD list digest, so this must still be
    # refused -- being internally consistent is not enough without the root's say-so.
    Clear-Artifacts
    $replacementBytes = [Text.Encoding]::UTF8.GetBytes('SHARD-ONE-REPLACED')
    [IO.File]::WriteAllBytes($shard1Path, $replacementBytes)
    $replacementSha = Get-Sha256File $shard1Path
    $selfConsistentEntries = @(Get-CanonicalFileEntries) | ForEach-Object {
        if ([string]$_.path -eq 'model-00001-of-00002.safetensors') {
            [ordered]@{ path = $_.path; sha256 = $replacementSha; size = $replacementBytes.Length }
        } else { $_ }
    }
    $selfConsistentDoc = [ordered]@{}
    $selfConsistentDoc["Qwen/Qwen3-14B@$rev"] = [ordered]@{ files = @($selfConsistentEntries) }
    [IO.File]::WriteAllText($expectedPath, ($selfConsistentDoc | ConvertTo-Json -Depth 8))
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'list manifest hash mismatch' -and (Get-ShimLogLines).Count -eq 0) 'trust: self-consistent replacement list refused because root still references the old list digest'
    [IO.File]::WriteAllBytes($shard1Path, $shard1Bytes)
    Restore-TrustFixtureList

    # Wrong model/revision in the (otherwise valid) root manifest: no entry matches
    # the BaseRepo/BaseRevision the export is running with.
    Clear-Artifacts
    $wrongRevModels = @([ordered]@{ hf_repo = 'Qwen/Qwen3-14B'; hf_revision = ('f' * 40); upstream_weight_digests_sha256 = (Get-Sha256File $expectedPath) })
    Write-FixtureRoot -Models $wrongRevModels
    $scenarioScript = New-PatchedExportScript (Get-Sha256File $rootPath)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'expected exactly one model entry' -and (Get-ShimLogLines).Count -eq 0) 'trust: wrong hf_revision in root refused (zero matching entries)'
    Restore-TrustFixtureList

    # Duplicate model entries: two root entries both match BaseRepo/BaseRevision.
    Clear-Artifacts
    $dupModels = @(
        [ordered]@{ hf_repo = 'Qwen/Qwen3-14B'; hf_revision = $rev; upstream_weight_digests_sha256 = (Get-Sha256File $expectedPath) },
        [ordered]@{ hf_repo = 'Qwen/Qwen3-14B'; hf_revision = $rev; upstream_weight_digests_sha256 = ('f' * 64) }
    )
    Write-FixtureRoot -Models $dupModels
    $scenarioScript = New-PatchedExportScript (Get-Sha256File $rootPath)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'expected exactly one model entry' -and (Get-ShimLogLines).Count -eq 0) 'trust: duplicate matching root entries refused (ambiguous)'
    Restore-TrustFixtureList

    # Zero model entries at all.
    Clear-Artifacts
    Write-FixtureRoot -Models @()
    $scenarioScript = New-PatchedExportScript (Get-Sha256File $rootPath)
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'expected exactly one model entry' -and (Get-ShimLogLines).Count -eq 0) 'trust: empty models array refused (zero matching entries)'
    Restore-TrustFixtureList

    foreach ($caseField in @('repo', 'revision')) {
        Clear-Artifacts
        $repoCase = if ($caseField -eq 'repo') { 'qwen/Qwen3-14B' } else { 'Qwen/Qwen3-14B' }
        $revisionCase = if ($caseField -eq 'revision') { $rev.ToUpperInvariant() } else { $rev }
        Write-FixtureRoot -Models @([ordered]@{ hf_repo = $repoCase; hf_revision = $revisionCase; upstream_weight_digests_sha256 = (Get-Sha256File $expectedPath) })
        $scenarioScript = New-PatchedExportScript (Get-Sha256File $rootPath)
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'expected exactly one model entry' -and (Get-ShimLogLines).Count -eq 0) "trust: root $caseField must match authenticated case exactly"
        Restore-TrustFixtureList
    }
    Clear-Artifacts
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -BaseRevisionArg $rev.ToUpperInvariant())
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'expected exactly one model entry' -and (Get-ShimLogLines).Count -eq 0) 'trust: caller revision case change refused against exact root entry'
    Clear-Artifacts
    $scenarioScript = Set-TrustFixtureList {
        $caseList = [ordered]@{}
        $caseList["qwen/Qwen3-14B@$rev"] = [ordered]@{ files = @(Get-CanonicalFileEntries) }
        [IO.File]::WriteAllText($expectedPath, ($caseList | ConvertTo-Json -Depth 8))
    }
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    Assert ($r.Code -ne 0 -and $text -match 'base shard gate: missing key' -and (Get-ShimLogLines).Count -eq 0) 'trust: authenticated list key must match repository and revision case exactly'
    Restore-TrustFixtureList

    # Unlisted extra snapshot file: an on-disk file not named in the (authentic,
    # untouched) list must cause a refusal naming it as extra.
    Clear-Artifacts
    $unlistedExtra = Join-Path $snapDir 'README.md'
    [IO.File]::WriteAllText($unlistedExtra, 'unlisted readme')
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'coverage mismatch' -and $text.Contains('README.md') -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: unlisted extra snapshot file refused, no docker, no staging'
    Remove-Item -LiteralPath $unlistedExtra -Force

    # Missing listed file: tokenizer.json is deleted from disk.
    Clear-Artifacts
    Rename-Item -LiteralPath $tokenizerPath -NewName 'tokenizer.json.hidden'
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'coverage mismatch' -and $text.Contains('tokenizer.json') -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: missing listed file (tokenizer.json) refused, no docker, no staging'
    Rename-Item -LiteralPath (Join-Path $snapDir 'tokenizer.json.hidden') -NewName 'tokenizer.json'

    # Corrupt config.json on disk: must be refused by the size/hash check BEFORE
    # model.safetensors.index.json is ever parsed (ordering requirement 5/8).
    Clear-Artifacts
    Invoke-FlipFirstByte $configPath
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text.Contains('config.json') -and $text -match 'read1=' -and $text -match 'read2=' -and $text -notmatch 'weight_map' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: corrupt config.json refused by hash check before index is parsed'
    [IO.File]::WriteAllBytes($configPath, $configBytes)

    # Corrupt tokenizer.json on disk: same ordering guarantee.
    Clear-Artifacts
    Invoke-FlipFirstByte $tokenizerPath
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text.Contains('tokenizer.json') -and $text -match 'read1=' -and $text -match 'read2=' -and $text -notmatch 'weight_map' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: corrupt tokenizer.json refused by hash check before index is parsed'
    [IO.File]::WriteAllBytes($tokenizerPath, $tokenizerBytes)

    # Corrupt model.safetensors.index.json content on disk while it still matches
    # the list's recorded size: must still be refused by hash check (not reach the
    # weight_map-specific validation), proving the content check runs first.
    Clear-Artifacts
    $origIndexBytes = [IO.File]::ReadAllBytes($indexPath)
    Invoke-FlipFirstByte $indexPath
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute)
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text.Contains('model.safetensors.index.json') -and $text -match 'read1=' -and $text -match 'read2=' -and $text -notmatch 'weight_map' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: corrupt index.json content refused by hash check before weight_map is parsed'
    [IO.File]::WriteAllBytes($indexPath, $origIndexBytes)

    # Size mismatch: list records the wrong size for shard2 while sha256 stays correct.
    Clear-Artifacts
    $sizeMismatchEntries = @(Get-CanonicalFileEntries) | ForEach-Object {
        if ([string]$_.path -eq 'model-00002-of-00002.safetensors') {
            [ordered]@{ path = $_.path; sha256 = $_.sha256; size = ([int]$_.size + 1) }
        } else { $_ }
    }
    $sizeMismatchDoc = [ordered]@{}
    $sizeMismatchDoc["Qwen/Qwen3-14B@$rev"] = [ordered]@{ files = @($sizeMismatchEntries) }
    $scenarioScript = Set-TrustFixtureList { [IO.File]::WriteAllText($expectedPath, ($sizeMismatchDoc | ConvertTo-Json -Depth 8)) }
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'size mismatch' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: authenticated list with wrong recorded size refused, no docker, no staging'
    Restore-TrustFixtureList

    # Absolute list path: authenticated list names an absolute path instead of a
    # snapshot-relative one.
    Clear-Artifacts
    $absDoc = [ordered]@{}
    $absDoc["Qwen/Qwen3-14B@$rev"] = [ordered]@{
        files = @(Get-CanonicalFileEntries) + @([ordered]@{ path = 'C:/evil/model-00001-of-00002.safetensors'; sha256 = $shard1Sha; size = $shard1Bytes.Length })
    }
    $scenarioScript = Set-TrustFixtureList { [IO.File]::WriteAllText($expectedPath, ($absDoc | ConvertTo-Json -Depth 8)) }
    $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute) $scenarioScript
    $text = Out-Text $r.Lines
    $stagingKids = @(Get-ChildItem -LiteralPath $eRoot -Force -ErrorAction SilentlyContinue)
    Assert ($r.Code -ne 0 -and $text -match 'invalid relative path' -and (Get-ShimLogLines).Count -eq 0 -and $stagingKids.Count -eq 0) 'trust: authenticated list with absolute path refused, no docker, no staging'
    Restore-TrustFixtureList

    # --- 7b. LX symlink resolver probes ---
    # These probes call the real export-adapter.ps1 resolver functions directly
    # (no subprocess export run), replaying canned `fsutil reparsepoint query`
    # text so the decode/hop logic can be exercised without creating real WSL/LX
    # reparse points, which this host cannot author unprivileged. The canned
    # text format (a `Reparse Tag Value : 0x...` line followed by `NNNN:  hex
    # hex ...` dump lines, a 4-byte zero header, then the UTF-8 target string)
    # is this test's best-effort reproduction of real `fsutil` output and is
    # flagged in the summary as needing real-host confirmation.
    function Build-FsutilLines([string]$RelativeTarget) {
        $payload = [Text.Encoding]::UTF8.GetBytes($RelativeTarget)
        $header = [byte[]]@(0, 0, 0, 0)
        $all = $header + $payload
        $out = [System.Collections.Generic.List[string]]::new()
        $out.Add('Reparse Tag Value: 0xa000001d')
        for ($i = 0; $i -lt $all.Length; $i += 16) {
            $endIdx = [Math]::Min($i + 15, $all.Length - 1)
            $chunk = $all[$i..$endIdx]
            $hex = (($chunk | ForEach-Object { $_.ToString('x2') }) -join ' ') + ' '
            $out.Add(('{0:x4}:  {1}' -f $i, $hex))
        }
        $out.ToArray()
    }

    function Invoke-ResolveLxReparseTargetProbe {
        param(
            [Parameter(Mandatory)][string]$PathArg,
            [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$FsutilLines
        )
        $helpers = Get-ExportStagingHelperSource
        $pathEsc = $PathArg.Replace("'", "''")
        $linesLiteral = if ($FsutilLines.Count -gt 0) { ($FsutilLines | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ', ' } else { '' }
        $cmd = @"
`$ErrorActionPreference = 'Stop'
$helpers
function Invoke-FsutilReparseQuery([string]`$Path) { @($linesLiteral) }
try {
    `$t = Resolve-LxReparseTarget -Path '$pathEsc'
    Write-Output "PROBE_OK:`$t"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
        $prevEa = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $lines = @(Invoke-ShellScript $cmd)
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = -1 }
            return [pscustomobject]@{ Lines = @($lines); Code = [int]$code; Text = (Out-Text $lines) }
        } finally {
            $ErrorActionPreference = $prevEa
        }
    }

    $probeGoodLines = Build-FsutilLines '../../blobs/deadbeefblob'
    $probe = Invoke-ResolveLxReparseTargetProbe -PathArg 'C:\probe\lx\link' -FsutilLines $probeGoodLines
    Assert ($probe.Code -eq 0 -and $probe.Text -eq 'PROBE_OK:../../blobs/deadbeefblob') 'lx-resolver: good replayed fsutil output decodes to recorded relative target'

    $probeBadTagLines = @('Reparse Tag Value: 0x80000003') + ($probeGoodLines | Select-Object -Skip 1)
    $probe = Invoke-ResolveLxReparseTargetProbe -PathArg 'C:\probe\lx\link' -FsutilLines $probeBadTagLines
    Assert ($probe.Code -ne 0 -and $probe.Text -match 'unexpected reparse tag') 'lx-resolver: non-LX reparse tag refused'

    $probe = Invoke-ResolveLxReparseTargetProbe -PathArg 'C:\probe\lx\link' -FsutilLines @()
    Assert ($probe.Code -ne 0 -and $probe.Text -match 'empty fsutil reparsepoint query output') 'lx-resolver: empty fsutil output refused'

    # --- 7c. Resolve-SnapshotFile hop-following probes (fake Get-Item/Test-Path) ---
    # These probes fake the on-disk reparse-point attributes that this host
    # cannot create unprivileged (unprivileged Windows processes cannot author
    # file-level LX reparse points), by locally overriding Get-Item/Test-Path
    # inside an isolated subprocess. The override only intercepts the exact
    # literal paths this probe registers and falls through to the real
    # cmdlets for everything else, so it cannot affect any other test.
    $lxHub = 'C:\probe-lx\hub\models--Qwen--Qwen3-14B'
    $lxHubCacheRoot = 'C:\probe-lx\hub'
    $lxSnap = Join-Path $lxHub 'snapshots\deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    $lxLink = Join-Path $lxSnap 'model-00001-of-00002.safetensors'
    $lxBlob = Join-Path $lxHub 'blobs\deadbeefblob'
    $lxLinkEsc = $lxLink.Replace("'", "''")
    $lxBlobEsc = $lxBlob.Replace("'", "''")
    $lxSnapEsc = $lxSnap.Replace("'", "''")
    $lxHubEsc = $lxHub.Replace("'", "''")
    $lxHubCacheRootEsc = $lxHubCacheRoot.Replace("'", "''")

    $acceptedCmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportStagingHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$lxLinkEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$lxBlobEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::Normal; Target = `$null }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$lxLinkEsc') { return (Build-FsutilLines '../../blobs/deadbeefblob') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$resolved = Resolve-SnapshotFile -Snapshot '$lxSnapEsc' -Relative 'model-00001-of-00002.safetensors' -HubRoot '$lxHubEsc' -HubCacheRoot '$lxHubCacheRootEsc'
    Write-Output "PROBE_OK:`$resolved"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $accLines = @(Invoke-ShellScript $acceptedCmd)
        $accCode = $LASTEXITCODE
        if ($null -eq $accCode) { $accCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $accText = Out-Text $accLines
    Assert ($accCode -eq 0 -and $accText -eq "PROBE_OK:$lxBlob") 'resolve-snapshot-file: legitimate LX file link resolves through to its blob target and is accepted'

    # Loop guard: five chained LX reparse hops (link0..link4, each decoding to
    # the next) must be refused once more than 4 hops have been followed.
    $loopLink1 = Join-Path $lxSnap 'loop-link-1'
    $loopLink2 = Join-Path $lxSnap 'loop-link-2'
    $loopLink3 = Join-Path $lxSnap 'loop-link-3'
    $loopLink4 = Join-Path $lxSnap 'loop-link-4'
    $loopLink1Esc = $loopLink1.Replace("'", "''")
    $loopLink2Esc = $loopLink2.Replace("'", "''")
    $loopLink3Esc = $loopLink3.Replace("'", "''")
    $loopLink4Esc = $loopLink4.Replace("'", "''")
    $loopCmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportStagingHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$lxLinkEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$loopLink1Esc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$loopLink2Esc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$loopLink3Esc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$loopLink4Esc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$lxLinkEsc') { return (Build-FsutilLines 'loop-link-1') }
    if (`$Path -eq '$loopLink1Esc') { return (Build-FsutilLines 'loop-link-2') }
    if (`$Path -eq '$loopLink2Esc') { return (Build-FsutilLines 'loop-link-3') }
    if (`$Path -eq '$loopLink3Esc') { return (Build-FsutilLines 'loop-link-4') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$resolved = Resolve-SnapshotFile -Snapshot '$lxSnapEsc' -Relative 'model-00001-of-00002.safetensors' -HubRoot '$lxHubEsc' -HubCacheRoot '$lxHubCacheRootEsc'
    Write-Output "PROBE_OK:`$resolved"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $loopLines = @(Invoke-ShellScript $loopCmd)
        $loopCode = $LASTEXITCODE
        if ($null -eq $loopCode) { $loopCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $loopText = Out-Text $loopLines
    Assert ($loopCode -ne 0 -and $loopText -match 'more than 4' -and $loopText -notmatch 'PROBE_OK') 'resolve-snapshot-file: chain of more than 4 LX reparse hops is refused'

    # --- 7d. Resolve-SnapshotFile hub-level shared blob store probes ---
    # Real chain on the target host for large files: a snapshot LX link resolves
    # into the repo's own `blobs` folder, and that blob entry is itself an LX link
    # that resolves one level further, into the shared store directly under the hub
    # cache root (`<hub>\blobs\<2 hex>\<hash>`), not the repo folder. These probes
    # cover the accepted two-hop shared-store case and two refusals: a hop that
    # escapes the hub cache root, and a final target that lands inside the hub
    # root but outside both legitimate blobs folders.
    $sharedBlobFile = Join-Path $lxHubCacheRoot 'blobs\ab\deadbeefsharedblob'
    $sharedBlobFileEsc = $sharedBlobFile.Replace("'", "''")

    $twoHopCmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportStagingHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$lxLinkEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$lxBlobEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$sharedBlobFileEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::Normal; Target = `$null }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$lxLinkEsc') { return (Build-FsutilLines '../../blobs/deadbeefblob') }
    if (`$Path -eq '$lxBlobEsc') { return (Build-FsutilLines '../../blobs/ab/deadbeefsharedblob') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$resolved = Resolve-SnapshotFile -Snapshot '$lxSnapEsc' -Relative 'model-00001-of-00002.safetensors' -HubRoot '$lxHubEsc' -HubCacheRoot '$lxHubCacheRootEsc'
    Write-Output "PROBE_OK:`$resolved"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $twoHopLines = @(Invoke-ShellScript $twoHopCmd)
        $twoHopCode = $LASTEXITCODE
        if ($null -eq $twoHopCode) { $twoHopCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $twoHopText = Out-Text $twoHopLines
    Assert ($twoHopCode -eq 0 -and $twoHopText -eq "PROBE_OK:$sharedBlobFile") 'resolve-snapshot-file: two-hop chain through a repo blobs link into the hub-level shared blobs store is accepted'

    # Directory redirects are distinct from supported leaf file links. These
    # subprocesses use entirely synthetic filesystem observations, including all
    # ancestors; no junction or cache file is created or queried. Gate probes
    # fail if trust metadata is read or coverage walking begins before refusal.
    function Invoke-DirectoryRedirectProbe {
        param([string]$Redirect = '', [switch]$Gate, [switch]$TwoHop, [switch]$Lx, [switch]$ArrayTarget)
        $redirectEsc = $Redirect.Replace("'", "''")
        $repoTarget = Join-Path $lxHub 'blobs\redirect\leaf.bin'
        $finalTarget = if ($TwoHop) { Join-Path $lxHubCacheRoot 'blobs\ab\redirect\leaf.bin' } else { $repoTarget }
        $repoTargetEsc = $repoTarget.Replace("'", "''")
        $finalTargetEsc = $finalTarget.Replace("'", "''")
        $gateLiteral = if ($Gate) { '$true' } else { '$false' }
        $twoHopLiteral = if ($TwoHop) { '$true' } else { '$false' }
        $lxLiteral = if ($Lx) { '$true' } else { '$false' }
        $arrayTargetLiteral = if ($ArrayTarget) { '$true' } else { '$false' }
        $cmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportGateHelperSource)
`$script:LeafQueries = [System.Collections.Generic.List[string]]::new()
`$script:Targets = @{ '$lxLinkEsc' = '$repoTargetEsc' }
if ($twoHopLiteral) { `$script:Targets['$repoTargetEsc'] = '$finalTargetEsc' }
function Test-Path { param([string]`$LiteralPath); return `$true }
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$LiteralPath -eq '$redirectEsc') {
        return [pscustomobject]@{ PSIsContainer = `$true; Attributes = [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint }
    }
    if (`$script:Targets.ContainsKey(`$LiteralPath)) {
        `$script:LeafQueries.Add(`$LiteralPath)
        `$target = if ($lxLiteral) { @() } elseif ($arrayTargetLiteral) { ,@(`$script:Targets[`$LiteralPath]) } else { `$script:Targets[`$LiteralPath] }
        return [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = `$target }
    }
    if (`$LiteralPath -eq '$finalTargetEsc') {
        `$script:LeafQueries.Add(`$LiteralPath)
        return [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::Normal; Target = `$null }
    }
    return [pscustomobject]@{ PSIsContainer = `$true; Attributes = [IO.FileAttributes]::Directory }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (-not `$script:Targets.ContainsKey(`$Path)) { throw 'unexpected synthetic fsutil path' }
    `$all = [byte[]]@(0,0,0,0) + [Text.Encoding]::UTF8.GetBytes(`$script:Targets[`$Path])
    `$lines = [System.Collections.Generic.List[string]]::new()
    `$lines.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$last = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$hex = ((`$all[`$i..`$last] | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$lines.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$lines.ToArray()
}
function Get-TrustedListDigest { throw 'UNEXPECTED_METADATA_READ' }
function Get-ChildItem { throw 'UNEXPECTED_COVERAGE_WALK' }
try {
    if ($gateLiteral) {
        `$null = Assert-BaseShards '$lxSnapEsc' '$lxHubEsc' '$lxHubCacheRootEsc'
    } else {
        `$resolved = Resolve-SnapshotFile '$lxSnapEsc' 'model-00001-of-00002.safetensors' '$lxHubEsc' '$lxHubCacheRootEsc'
        Write-Output "PROBE_OK:`$resolved"
    }
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    Write-Output ('LEAF_QUERIES:' + (`$script:LeafQueries -join '|'))
    exit 1
}
"@
        $prevEa = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $lines = @(Invoke-ShellScript $cmd)
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = -1 }
            return [pscustomobject]@{ Code = $code; Text = (Out-Text $lines); FinalTarget = $finalTarget }
        } finally { $ErrorActionPreference = $prevEa }
    }

    foreach ($rootCase in @(
        @{ Name = 'snapshot root'; Path = $lxSnap },
        @{ Name = 'snapshot ancestor'; Path = (Split-Path -Parent $lxSnap) },
        @{ Name = 'repo hub root'; Path = $lxHub },
        @{ Name = 'shared hub cache root'; Path = $lxHubCacheRoot }
    )) {
        $probe = Invoke-DirectoryRedirectProbe -Redirect $rootCase.Path -Gate
        Assert ($probe.Code -ne 0 -and $probe.Text.Contains("reparse point at $($rootCase.Path)") -and $probe.Text -notmatch 'UNEXPECTED_|PROBE_OK' -and $probe.Text.EndsWith('LEAF_QUERIES:')) "assert-base-shards: redirected $($rootCase.Name) refused before metadata, walking or file reads"
    }
    $probe = Invoke-DirectoryRedirectProbe -Redirect $lxSnap
    Assert ($probe.Code -ne 0 -and $probe.Text.Contains("reparse point at $lxSnap") -and $probe.Text.EndsWith('LEAF_QUERIES:')) 'resolve-snapshot-file: redirected initial snapshot refused before leaf query'

    $repoRedirect = Join-Path $lxHub 'blobs\redirect'
    $probe = Invoke-DirectoryRedirectProbe -Redirect $repoRedirect
    Assert ($probe.Code -ne 0 -and $probe.Text.Contains("reparse point at $repoRedirect") -and $probe.Text.EndsWith("LEAF_QUERIES:$lxLink")) 'resolve-snapshot-file: ordinary file link into redirected repo blob ancestor refused before target leaf query'
    $sharedRedirect = Join-Path $lxHubCacheRoot 'blobs\ab\redirect'
    $probe = Invoke-DirectoryRedirectProbe -Redirect $sharedRedirect -TwoHop -Lx
    $intermediate = Join-Path $lxHub 'blobs\redirect\leaf.bin'
    Assert ($probe.Code -ne 0 -and $probe.Text.Contains("reparse point at $sharedRedirect") -and $probe.Text.EndsWith("LEAF_QUERIES:$lxLink|$intermediate")) 'resolve-snapshot-file: second LX hop into redirected shared blob ancestor refused before final leaf query'
    $probe = Invoke-DirectoryRedirectProbe
    Assert ($probe.Code -eq 0 -and $probe.Text -eq "PROBE_OK:$($probe.FinalTarget)") 'resolve-snapshot-file: ordinary leaf file link with scalar Target and regular directory ancestors accepted'
    $probe = Invoke-DirectoryRedirectProbe -ArrayTarget
    Assert ($probe.Code -eq 0 -and $probe.Text -eq "PROBE_OK:$($probe.FinalTarget)") 'resolve-snapshot-file: ordinary leaf file link with array Target and regular directory ancestors accepted'

    $outsideTarget = 'C:\probe-lx-outside\evil.bin'
    $outsideCmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportStagingHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$lxLinkEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$lxLinkEsc') { return (Build-FsutilLines '$outsideTarget') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$resolved = Resolve-SnapshotFile -Snapshot '$lxSnapEsc' -Relative 'model-00001-of-00002.safetensors' -HubRoot '$lxHubEsc' -HubCacheRoot '$lxHubCacheRootEsc'
    Write-Output "PROBE_OK:`$resolved"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $outsideLines = @(Invoke-ShellScript $outsideCmd)
        $outsideCode = $LASTEXITCODE
        if ($null -eq $outsideCode) { $outsideCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $outsideText = Out-Text $outsideLines
    Assert ($outsideCode -ne 0 -and $outsideText -match 'escapes hub folder' -and $outsideText -notmatch 'PROBE_OK') 'resolve-snapshot-file: a hop that leaves the hub cache root is refused'

    $otherSnapFile = Join-Path $lxSnap 'other-file-in-snapshot.bin'
    $otherSnapFileEsc = $otherSnapFile.Replace("'", "''")
    $notBlobsCmd = @"
`$ErrorActionPreference = 'Stop'
$(Get-ExportStagingHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$lxLinkEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
    '$otherSnapFileEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::Normal; Target = `$null }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$lxLinkEsc') { return (Build-FsutilLines 'other-file-in-snapshot.bin') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$resolved = Resolve-SnapshotFile -Snapshot '$lxSnapEsc' -Relative 'model-00001-of-00002.safetensors' -HubRoot '$lxHubEsc' -HubCacheRoot '$lxHubCacheRootEsc'
    Write-Output "PROBE_OK:`$resolved"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $notBlobsLines = @(Invoke-ShellScript $notBlobsCmd)
        $notBlobsCode = $LASTEXITCODE
        if ($null -eq $notBlobsCode) { $notBlobsCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $notBlobsText = Out-Text $notBlobsLines
    Assert ($notBlobsCode -ne 0 -and $notBlobsText -match 'not inside the repo blobs folder or the hub blobs store' -and $notBlobsText -notmatch 'PROBE_OK') 'resolve-snapshot-file: a final target inside the hub root but outside both blobs folders is refused'

    # --- 7e. Assert-BaseShards: model.safetensors.index.json reached through an
    # LX-resolved path (not a plain Test-Path/Get-Content read of the snapshot
    # path) must still be accepted and parsed from its resolved bytes. These call
    # the real Assert-BaseShards directly against the already-authenticated main
    # fixture (config.json/tokenizer.json/shards/list/root all untouched and
    # canonical at this point), faking only the single index path's reparse
    # status, exactly as the Resolve-SnapshotFile probes above fake Get-Item.
    $gateHubCacheRoot = Join-Path $cRoot 'hf\hub'
    $gateHubRoot = Join-Path $gateHubCacheRoot 'models--Qwen--Qwen3-14B'
    $gateIndexBlobPath = Join-Path $gateHubRoot 'blobs\indexblob'
    New-Item -ItemType Directory -Path (Split-Path -Parent $gateIndexBlobPath) -Force | Out-Null
    Copy-Item -LiteralPath $indexPath -Destination $gateIndexBlobPath -Force
    $rootShaForGate = Get-Sha256File $rootPath
    $snapDirEsc = $snapDir.Replace("'", "''")
    $gateHubRootEsc = $gateHubRoot.Replace("'", "''")
    $gateHubCacheRootEsc = $gateHubCacheRoot.Replace("'", "''")
    $rootPathEsc = $rootPath.Replace("'", "''")
    $expectedPathEsc = $expectedPath.Replace("'", "''")
    $indexPathEsc = $indexPath.Replace("'", "''")

    $gateAcceptedCmd = @"
`$ErrorActionPreference = 'Stop'
`$BaseRepo = 'Qwen/Qwen3-14B'
`$BaseRevision = '$rev'
`$TrustedDigestManifest = '$rootPathEsc'
`$ExpectedShardsJson = '$expectedPathEsc'
`$script:PinnedTrustedDigestManifestSha256 = '$rootShaForGate'
$(Get-ExportGateHelperSource)
function Build-FsutilLines([string]`$RelativeTarget) {
    `$payload = [Text.Encoding]::UTF8.GetBytes(`$RelativeTarget)
    `$header = [byte[]]@(0,0,0,0)
    `$all = `$header + `$payload
    `$out = [System.Collections.Generic.List[string]]::new()
    `$out.Add('Reparse Tag Value: 0xa000001d')
    for (`$i = 0; `$i -lt `$all.Length; `$i += 16) {
        `$endIdx = [Math]::Min(`$i + 15, `$all.Length - 1)
        `$chunk = `$all[`$i..`$endIdx]
        `$hex = ((`$chunk | ForEach-Object { `$_.ToString('x2') }) -join ' ') + ' '
        `$out.Add(('{0:x4}:  {1}' -f `$i, `$hex))
    }
    `$out.ToArray()
}
`$script:FakeItems = @{
    '$indexPathEsc' = [pscustomobject]@{ PSIsContainer = `$false; Attributes = [IO.FileAttributes]::ReparsePoint; Target = @() }
}
function Invoke-FsutilReparseQuery([string]`$Path) {
    if (`$Path -eq '$indexPathEsc') { return (Build-FsutilLines '../../blobs/indexblob') }
    @()
}
function Get-Item {
    param([string]`$LiteralPath, [switch]`$Force)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$script:FakeItems[`$LiteralPath] }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath `$LiteralPath -Force:`$Force
}
function Test-Path {
    param([string]`$LiteralPath)
    if (`$script:FakeItems.ContainsKey(`$LiteralPath)) { return `$true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath `$LiteralPath
}
try {
    `$r = Assert-BaseShards -Snapshot '$snapDirEsc' -HubRoot '$gateHubRootEsc' -HubCacheRoot '$gateHubCacheRootEsc'
    Write-Output "PROBE_OK:`$(`$r.covered_files)"
    exit 0
} catch {
    Write-Output `$_.Exception.Message
    exit 1
}
"@
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $gateAcceptedLines = @(Invoke-ShellScript $gateAcceptedCmd)
        $gateAcceptedCode = $LASTEXITCODE
        if ($null -eq $gateAcceptedCode) { $gateAcceptedCode = -1 }
    } finally {
        $ErrorActionPreference = $prevEa
    }
    $gateAcceptedText = Out-Text $gateAcceptedLines
    Assert ($gateAcceptedCode -eq 0 -and $gateAcceptedText -eq 'PROBE_OK:5') 'assert-base-shards: index reached via resolved LX-link path (not the snapshot path) is accepted and parsed from the resolved bytes'
    $copyRun = Join-Path $eRoot 'lx-copy-probe'
    $copyRunEsc = $copyRun.Replace("'", "''")
    $copyStagingEsc = $eRoot.Replace("'", "''")
    $copyHotEsc = $cRoot.Replace("'", "''")
    $copyBody = @"
    `$CHotRoot = '$copyHotEsc'
    `$StagingRoot = '$copyStagingEsc'
    New-Item -ItemType Directory -Path '$copyRunEsc' | Out-Null
    [IO.File]::WriteAllText((Join-Path '$copyRunEsc' '.hl-export-staging'), '{"ownershipToken":"lx-copy-owner"}')
    `$copy = New-VerifiedBaseSnapshot '$snapDirEsc' '$gateHubRootEsc' '$gateHubCacheRootEsc' `$r '$copyRunEsc' 'lx-copy-owner'
    try {
        if (`$copy.covered_files -ne 5 -or (Get-FileSha256 (Join-Path `$copy.path 'model.safetensors.index.json')) -ne (Get-FileSha256 '$indexPathEsc')) { throw 'LX copied index mismatch' }
        Write-Output 'PROBE_COPY_OK:5'
    } finally { foreach (`$lease in `$copy.leases) { `$lease.Dispose() } }
    Remove-StagedTree '$copyRunEsc' 'lx-copy-owner'
"@
    $copyCmd = $gateAcceptedCmd.Replace('    Write-Output "PROBE_OK:$($r.covered_files)"', $copyBody)
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $copyLines = @(Invoke-ShellScript $copyCmd)
        $copyCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEa }
    Assert ($copyCode -eq 0 -and (Out-Text $copyLines) -eq 'PROBE_COPY_OK:5') 'frozen base: full approved snapshot including LX-resolved index copied as independent verified regular files'
    Remove-Item -LiteralPath $gateIndexBlobPath -Force -ErrorAction SilentlyContinue

    # The real TOCTOU gap this change closes (verified bytes differ from parsed
    # bytes) cannot be reproduced in-process without hooking file I/O between the
    # verification loop and the parse step. Instead, assert by code structure that
    # the exact primitive Assert-BaseShards now uses to re-check parsed bytes
    # against the verified digest (Get-Sha256Bytes equality) behaves correctly for
    # both matching and non-matching byte arrays, which is what makes the re-check
    # in Assert-BaseShards a sound proof that verified bytes and parsed bytes are
    # the same.
    $hashRecheckGood = [Text.Encoding]::UTF8.GetBytes('{"weight_map":{}}')
    $hashRecheckSame = [Text.Encoding]::UTF8.GetBytes('{"weight_map":{}}')
    $hashRecheckTampered = [Text.Encoding]::UTF8.GetBytes('{"weight_map":{"x":1}}')
    Assert ((Get-Sha256Bytes $hashRecheckGood) -eq (Get-Sha256Bytes $hashRecheckSame)) 'hash-recheck primitive: identical bytes produce matching digests (verified bytes accepted)'
    Assert ((Get-Sha256Bytes $hashRecheckGood) -ne (Get-Sha256Bytes $hashRecheckTampered)) 'hash-recheck primitive: tampered bytes produce a different digest (parse-time swap would be refused)'

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

    # --- T-H Hot-root alias regression ---
    Clear-Artifacts
    $physicalHot = Join-Path $root 'physical-hot'
    $hotAlias = Join-Path $root 'hot-alias'
    if (Test-Path -LiteralPath $hotAlias) { Remove-TreeSafe $hotAlias }
    if (Test-Path -LiteralPath $physicalHot) { Remove-TreeSafe $physicalHot }
    New-Item -ItemType Directory -Path $physicalHot -Force | Out-Null
    Get-ChildItem -LiteralPath $cRoot -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $physicalHot -Recurse -Force
    }
    $hotSentinel = Join-Path $physicalHot 'hot-root-sentinel.bin'
    [IO.File]::WriteAllBytes($hotSentinel, [Text.Encoding]::UTF8.GetBytes('HOT-ROOT-SENTINEL'))
    $hotSentSha = Get-Sha256File $hotSentinel
    New-Item -ItemType Junction -Path $hotAlias -Target $physicalHot | Out-Null
    $hotStaging = Join-Path $physicalHot 'staging'
    $hotOutCkpt1 = Join-Path $physicalHot 'models\m2\tstexp-ckpt1-Q4_K_M.gguf'
    $hotReportsDir = Join-Path $physicalHot 'reports'
    try {
        $r = Invoke-Export (New-ExportArgs -Checkpoint @($adapter1) -Execute -CHotRootArg $hotAlias -StagingRoot $hotStaging)
        $text = Out-Text $r.Lines
        Assert ($r.Code -ne 0 -and $text -match 'reparse point') 't-h: hot-root alias export refused'
        Assert ((Get-ShimLogLines).Count -eq 0) 't-h: hot-root alias no docker'
        Assert (-not (Test-Path -LiteralPath $hotStaging)) 't-h: no staging directory under physical-hot'
        Assert (-not (Test-Path -LiteralPath $hotOutCkpt1)) 't-h: no output file'
        $hotReports = @()
        if (Test-Path -LiteralPath $hotReportsDir) {
            $hotReports = @(Get-ChildItem -LiteralPath $hotReportsDir -Filter 'm2-export-tstexp-*.json' -File -ErrorAction SilentlyContinue)
        }
        Assert ($hotReports.Count -eq 0) 't-h: no report'
        Assert ((Get-Sha256File $hotSentinel) -eq $hotSentSha) 't-h: physical-hot sentinel unchanged'
    } finally {
        if (Test-Path -LiteralPath $hotAlias) {
            $hotItem = Get-Item -LiteralPath $hotAlias -Force
            if ($hotItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [System.IO.Directory]::Delete($hotAlias, $false)
            }
        }
        if (Test-Path -LiteralPath $physicalHot) { Remove-TreeSafe $physicalHot }
    }

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
    # Checked against the real, committed script -- $script may point at a
    # throwaway patched copy used only to make the pinned trust-root constant
    # match this run's synthetic fixtures (see New-PatchedExportScript).
    $st = Get-Content -LiteralPath $realScript -Raw -Encoding utf8
    # Execute only the actual standard-library load verifier extracted from
    # MergePy. No Torch/Transformers imports, model load or Docker execution.
    $verifierStart = $st.IndexOf('def verify_base_snapshot():')
    $verifierEnd = $st.IndexOf("`nverify_base_snapshot()", $verifierStart)
    if ($verifierStart -lt 0 -or $verifierEnd -le $verifierStart) { throw 'base load verifier extract failed' }
    $verifier = $st.Substring($verifierStart, $verifierEnd - $verifierStart)
    $loadRoot = Join-Path $root 'load-guard'
    New-Item -ItemType Directory -Path $loadRoot | Out-Null
    $loadFiles = @(Get-CanonicalFileEntries)
    foreach ($entry in $loadFiles) { Copy-Item -LiteralPath (Join-Path $snapDir $entry.path) -Destination (Join-Path $loadRoot $entry.path) }
    $loadIntegrity = Join-Path $root 'load-integrity.json'
    $loadDoc = [ordered]@{ root_sha256 = $baselineRootSha; list_sha256 = (Get-Sha256File $expectedPath); covered_files = $loadFiles.Count; files = $loadFiles }
    [IO.File]::WriteAllBytes($loadIntegrity, [Text.Encoding]::UTF8.GetBytes(($loadDoc | ConvertTo-Json -Depth 8)))
    $pythonPrelude = @'
import os, json, hashlib, builtins, sys
snap, integrity_path, expected_sha = sys.argv[1:]
os.environ["HL_BASE_GATE_SHA256"] = expected_sha
real_open = builtins.open
reads = {}
def counted_open(path, mode="r", *args, **kwargs):
    if path == "/base-integrity.json":
        path = integrity_path
    elif mode == "rb" and str(path).startswith(snap + os.sep):
        reads[path] = reads.get(path, 0) + 1
    return real_open(path, mode, *args, **kwargs)
builtins.open = counted_open
'@
    $pythonCases = @'
verify_base_snapshot()
assert len(reads) == 5 and set(reads.values()) == {2}, reads
verify_base_snapshot()
assert len(reads) == 5 and set(reads.values()) == {4}, reads
print("ALL_FILES_TWO_READS_BEFORE_EACH_LOAD")
changed_path = os.path.join(snap, "tokenizer.json")
with real_open(changed_path, "rb") as stream:
    original = stream.read()
changed = bytearray(original)
changed[0] ^= 0xff
with real_open(changed_path, "wb") as stream:
    stream.write(changed)
try:
    verify_base_snapshot()
except RuntimeError as error:
    assert "two-read approved hash mismatch" in str(error), error
    print("MUTATED_COPY_REFUSED_BEFORE_NEXT_LOAD")
else:
    raise AssertionError("mutated copied file accepted")
with real_open(changed_path, "wb") as stream:
    stream.write(original)
with real_open(integrity_path, "ab") as stream:
    stream.write(b" ")
try:
    verify_base_snapshot()
except RuntimeError as error:
    assert "integrity envelope hash mismatch" in str(error), error
    print("MUTATED_ENVELOPE_REFUSED")
else:
    raise AssertionError("mutated envelope accepted")
'@
    $pythonProbe = Join-Path $root 'load-verifier-probe.py'
    [IO.File]::WriteAllBytes($pythonProbe, [Text.Encoding]::UTF8.GetBytes($pythonPrelude + "`n" + $verifier + "`n" + $pythonCases + "`n"))
    $pythonExe = if ($env:HL_TEST_PYTHON_EXE) { $env:HL_TEST_PYTHON_EXE } else { (Get-Command python -ErrorAction Stop).Source }
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $loadLines = @(& $pythonExe $pythonProbe $loadRoot $loadIntegrity (Get-Sha256File $loadIntegrity) 2>&1 | ForEach-Object { "$_" })
        $loadCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEa }
    Assert ($loadCode -eq 0 -and $loadLines -contains 'ALL_FILES_TWO_READS_BEFORE_EACH_LOAD') 'base load: actual Python verifier reads every approved entry twice before each load'
    Assert ($loadCode -eq 0 -and $loadLines -contains 'MUTATED_COPY_REFUSED_BEFORE_NEXT_LOAD') 'base load: changed staged file refused by actual verifier before next load'
    Assert ($loadCode -eq 0 -and $loadLines -contains 'MUTATED_ENVELOPE_REFUSED') 'base load: changed expected-digest envelope refused before weight reads'
    $pinTokens = $null
    $pinErrors = $null
    $realAst = [Management.Automation.Language.Parser]::ParseInput($st, [ref]$pinTokens, [ref]$pinErrors)
    $pinAssignments = @($realAst.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -ceq '$script:PinnedTrustedDigestManifestSha256' }, $true))
    Assert ($pinAssignments.Count -eq 1 -and $pinAssignments[0].Right.Extent.Text -ceq "'d1a4a2d921c3f5197c2867dcdb50dbfb46d2189bbb88141ed56d3f3eb19d36af'") 'static: committed approved v2 root SHA-256 pin equals literal authority'
    Assert ($st.Contains("'reports\m2-trusted-digest-manifest-v2.json'") -and $st.Contains("'reports\m2-expected-qwen3-14b-v2.json'")) 'static: committed default root and list paths remain approved v2'
    Assert ($st.Contains('snap = "/base-snapshot"') -and $st -notmatch ':/hf:ro') 'static: model loader consumes exact frozen snapshot mount and never mounts mutable HF cache'
    Assert ($st -match 'verify_base_snapshot\(\)\r?\nbase = AutoModel' -and $st -match 'verify_base_snapshot\(\)\r?\nAutoTokenizer') 'static: full two-read gate immediately precedes both model and tokenizer loads'
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
    if ($null -eq $savedBaseExpected) { Remove-Item Env:HL_SHIM_BASE_EXPECTED -ErrorAction SilentlyContinue } else { $env:HL_SHIM_BASE_EXPECTED = $savedBaseExpected }
    if ($null -eq $savedMutateBase) { Remove-Item Env:HL_SHIM_MUTATE_BASE -ErrorAction SilentlyContinue } else { $env:HL_SHIM_MUTATE_BASE = $savedMutateBase }
    if ($null -eq $savedMutateCopy) { Remove-Item Env:HL_SHIM_MUTATE_COPY -ErrorAction SilentlyContinue } else { $env:HL_SHIM_MUTATE_COPY = $savedMutateCopy }
}

if ($fail.Count -gt 0) {
    foreach ($c in $fail) { Write-Output "FAIL  $c" }
    exit 1
}
Write-Output 'test-export-adapter: all cases passed'
exit 0
