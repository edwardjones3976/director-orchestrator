#Requires -Version 7
[CmdletBinding()]
param(
    [switch]$Execute,
    [Parameter(Mandatory)][string[]]$Checkpoint,
    [Parameter(Mandatory)][string]$Name,
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$BaseRepo = 'Qwen/Qwen3-14B',
    [string]$BaseRevision = '40c069824f4251a91eefaf281ebe4c544efd3e18',
    [string]$CHotRoot = 'C:\llm',
    [string]$StagingRoot = 'E:\llm-staging\m2-export',
    [string]$ExpectedShardsJson = 'C:\llm\reports\m1-expected-qwen3-14b.json',
    [string]$BaseTemplateSha256 = 'a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8',
    [double]$MinCFreeGiB = 150,
    [double]$MaxLlmGiB = 120,
    [double]$MinEFreeGiB = 100,
    [int]$DockerTimeoutSec = 14400,
    [string]$DockerPath,
    [Nullable[long]]$ProbeCFreeBytes,
    [Nullable[long]]$ProbeEFreeBytes,
    [Nullable[long]]$ProbeLlmBytes
)

$Checkpoint = [string[]](@($Checkpoint) | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')

$MergePy = @'
import os, sys
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
snap = "/hf/hub/models--Qwen--Qwen3-14B/snapshots/" + os.environ["HL_BASE_REVISION"]
base = AutoModelForCausalLM.from_pretrained(snap, torch_dtype=torch.bfloat16, device_map={"": "cpu"}, local_files_only=True)
model = PeftModel.from_pretrained(base, "/adapter", local_files_only=True).merge_and_unload()
model.save_pretrained("/work/merged", safe_serialization=True)
AutoTokenizer.from_pretrained(snap, local_files_only=True).save_pretrained("/work/merged")
print("merged ok")
'@

$ChatTemplatePy = @'
import hashlib, sys
from gguf import GGUFReader
r = GGUFReader(sys.argv[1])
f = r.fields["tokenizer.chat_template"]
t = bytes(f.parts[f.data[0]]).decode("utf-8")
print(hashlib.sha256(t.encode("utf-8")).hexdigest())
'@

function Get-UtcStamp {
    [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
}

function Get-UtcNowString {
    [datetime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
}

function Get-NormalPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { $full } else { $full.TrimEnd('\') }
}

function Get-HubFolderName([string]$Repo) { 'models--' + ($Repo -replace '/', '--') }

function Get-SnapshotPath([string]$Repo, [string]$Revision) {
    $hub = Join-Path $CHotRoot 'hf\hub'
    Join-Path (Join-Path (Join-Path $hub (Get-HubFolderName $Repo)) 'snapshots') $Revision
}

function Get-ImageDigest {
    if ($Image -match '@sha256:([0-9a-f]{64})') { return $Matches[1].ToLower() }
    $Image
}

function Test-PathIsUnder([string]$Child, [string]$Parent) {
    $c = Get-NormalPath $Child
    $p = Get-NormalPath $Parent
    if ($c.Equals($p, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    $prefix = $p + [IO.Path]::DirectorySeparatorChar
    $c.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-FileSha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

function Get-StorageSnapshot {
    $free = if ($null -ne $ProbeCFreeBytes) { [double]$ProbeCFreeBytes } else {
        $v = Get-Volume -DriveLetter C -ErrorAction Stop
        [double]$v.SizeRemaining
    }
    $llm = if ($null -ne $ProbeLlmBytes) { [double]$ProbeLlmBytes } elseif (Test-Path -LiteralPath $CHotRoot) {
        try {
            [double](Get-ChildItem -LiteralPath $CHotRoot -Recurse -File -Force -ErrorAction Stop | ForEach-Object {
                $file = $_
                try {
                    $length = (Get-Item -LiteralPath $file.FullName -Force -ErrorAction Stop).Length
                    if ($null -eq $length) { throw 'file length unavailable' }
                    $length
                } catch { throw "cannot measure $($file.FullName): $($_.Exception.Message)" }
            } | Measure-Object -Sum).Sum
        } catch { throw "cannot measure $CHotRoot [$($_.TargetObject)]: $($_.Exception.Message)" }
    } else { 0.0 }
    [pscustomobject]@{
        cFreeGiB = [math]::Round($free / 1GB, 4)
        llmGiB = [math]::Round($llm / 1GB, 4)
        cFreeBytes = $free
        llmBytes = $llm
    }
}

function Get-EStorageSnapshot {
    $free = if ($null -ne $ProbeEFreeBytes) { [double]$ProbeEFreeBytes } else {
        $v = Get-Volume -DriveLetter E -ErrorAction Stop
        [double]$v.SizeRemaining
    }
    [pscustomobject]@{
        eFreeGiB = [math]::Round($free / 1GB, 4)
        eFreeBytes = $free
    }
}

function Assert-StorageGuard([string]$Context, [long]$AdditionalBytesOnC = 0) {
    $s = Get-StorageSnapshot
    if ($s.cFreeBytes -lt $MinCFreeGiB * 1GB) {
        Write-Error "storage guard ($Context): C: free $($s.cFreeGiB) GiB is below floor $MinCFreeGiB GiB; stopping with no further changes"
    }
    if ($s.llmBytes -gt $MaxLlmGiB * 1GB) {
        Write-Error "storage guard ($Context): $CHotRoot is $($s.llmGiB) GiB, above cap $MaxLlmGiB GiB; stopping with no further changes"
    }
    if ($AdditionalBytesOnC -gt 0) {
        $projFree = $s.cFreeBytes - $AdditionalBytesOnC
        $projLlm = $s.llmBytes + $AdditionalBytesOnC
        if ($projFree -lt $MinCFreeGiB * 1GB) {
            Write-Error "storage guard ($Context): C: free would be $([math]::Round($projFree / 1GB, 4)) GiB after a $([math]::Round($AdditionalBytesOnC / 1GB, 4)) GiB copy, below floor $MinCFreeGiB GiB; stopping with no further changes"
        }
        if ($projLlm -gt $MaxLlmGiB * 1GB) {
            Write-Error "storage guard ($Context): $CHotRoot would be $([math]::Round($projLlm / 1GB, 4)) GiB after a $([math]::Round($AdditionalBytesOnC / 1GB, 4)) GiB copy, above cap $MaxLlmGiB GiB; stopping with no further changes"
        }
    }
    $s
}

function Assert-EFree([string]$Context) {
    $e = Get-EStorageSnapshot
    if ($e.eFreeBytes -lt $MinEFreeGiB * 1GB) {
        Write-Error "storage guard ($Context): E: free $($e.eFreeGiB) GiB is below floor $MinEFreeGiB GiB; stopping with no further changes"
    }
    $e
}

function Resolve-SnapshotFile([string]$Snapshot, [string]$Relative) {
    $full = Join-Path $Snapshot $Relative
    if (-not (Test-Path -LiteralPath $full)) { Write-Error "missing weight file $full" }
    $item = Get-Item -LiteralPath $full -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $target = $item.Target
        if ($target.Count) { $target = $target[0] }
        if ($target -match '^[\\/]') { return $target }
        return (Get-NormalPath (Join-Path $Snapshot $target))
    }
    $full
}

function ConvertTo-BashCommand([string]$Script) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script.Replace("`r`n", "`n")))
    "exec 2>&1; set -o pipefail; echo $encoded | base64 -d | bash"
}

function Invoke-DockerStep([string[]]$DockerArgs, [int]$TimeoutSec, [string]$Label) {
    Write-Host "docker $Label (timeout ${TimeoutSec}s)"
    if ($DockerPath -and $DockerPath.EndsWith('.ps1', [StringComparison]::OrdinalIgnoreCase)) {
        $out = @(Invoke-Bounded 'pwsh' (@('-NoProfile', '-File', $DockerPath) + $DockerArgs) $TimeoutSec)
    } else {
        $dockerExe = if ($DockerPath) { $DockerPath } else { 'docker' }
        $out = @(Invoke-Bounded $dockerExe $DockerArgs $TimeoutSec)
    }
    if ($out.Count -lt 1) { Write-Error "docker $Label failed: empty Invoke-Bounded result" }
    $lines = @($out | Where-Object { $_ -notmatch '^__EXIT__' })
    $lines | ForEach-Object { Write-Host $_ }
    $code = $out[-1]
    if ($code -ne '__EXIT__0') { Write-Error "docker $Label failed ($code): $($lines -join "`n")" }
    $lines
}

function Get-RunStagingRoot([string]$Target) {
    $staging = Get-NormalPath $StagingRoot
    $resolved = Get-NormalPath $Target
    $prefix = $staging + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "Remove-StagedTree: $resolved is not strictly inside $staging"
    }
    $rel = $resolved.Substring($staging.Length).TrimStart('\')
    $first = ($rel -split '[\\/]', 2)[0]
    if ([string]::IsNullOrWhiteSpace($first)) {
        Write-Error "Remove-StagedTree: $resolved is not strictly inside $staging"
    }
    Get-NormalPath (Join-Path $staging $first)
}

function Assert-NoReparseOnPath([string]$Root, [string]$Target) {
    $rootN = Get-NormalPath $Root
    $targetN = Get-NormalPath $Target
    $current = $rootN
    if (Test-Path -LiteralPath $current) {
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "Remove-StagedTree: reparse point at $current"
        }
    }
    $rel = $targetN.Substring($rootN.Length).TrimStart('\')
    if ([string]::IsNullOrWhiteSpace($rel)) { return }
    foreach ($seg in ($rel -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($seg)) { continue }
        $current = Join-Path $current $seg
        if (-not (Test-Path -LiteralPath $current)) {
            Write-Error "Remove-StagedTree: missing path $current"
        }
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "Remove-StagedTree: reparse point at $current"
        }
    }
}

function Remove-StagedTree([string]$Target) {
    if (-not (Test-Path -LiteralPath $Target)) {
        Write-Error "Remove-StagedTree: target does not exist: $Target"
    }
    $staging = Get-NormalPath $StagingRoot
    $resolved = Get-NormalPath ((Resolve-Path -LiteralPath $Target).Path)
    $prefix = $staging + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "Remove-StagedTree: $resolved is not strictly inside $staging"
    }
    $runStaging = Get-RunStagingRoot $resolved
    $marker = Join-Path $runStaging '.hl-export-staging'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        Write-Error "Remove-StagedTree: missing .hl-export-staging marker in $runStaging"
    }
    Assert-NoReparseOnPath $staging $resolved
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Get-RelativePath([string]$Root, [string]$Full) {
    $rootN = Get-NormalPath $Root
    $fullN = Get-NormalPath $Full
    $prefix = $rootN + [IO.Path]::DirectorySeparatorChar
    if ($fullN.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $fullN.Substring($prefix.Length) -replace '\\', '/'
    }
    $fullN
}

function Assert-AdapterHashes([string]$AdapterDir) {
    $hashFile = Join-Path $AdapterDir 'adapter-hashes.json'
    if (-not (Test-Path -LiteralPath $hashFile -PathType Leaf)) {
        Write-Error "adapter hash gate: missing adapter-hashes.json in $AdapterDir"
    }
    $doc = Get-Content -LiteralPath $hashFile -Raw -Encoding utf8 | ConvertFrom-Json
    if ($null -eq $doc.files) {
        Write-Error "adapter hash gate: adapter-hashes.json has no files object"
    }
    $names = @($doc.files.PSObject.Properties.Name)
    if ($names -notcontains 'adapter_config.json' -or $names -notcontains 'adapter_model.safetensors') {
        Write-Error "adapter hash gate: adapter-hashes.json must list adapter_config.json and adapter_model.safetensors"
    }
    $result = [ordered]@{}
    $listedFull = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($prop in $doc.files.PSObject.Properties) {
        $rel = [string]$prop.Name
        $expected = ([string]$prop.Value).ToLower()
        $full = Join-Path $AdapterDir ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            Write-Error "adapter hash gate: $rel expected=$expected actual=missing"
        }
        [void]$listedFull.Add((Get-NormalPath $full))
        $actual = Get-FileSha256 $full
        if ($actual -ne $expected) {
            Write-Error "adapter hash gate: $rel expected=$expected actual=$actual"
        }
        $result[$rel] = $actual
    }
    Get-ChildItem -LiteralPath $AdapterDir -Recurse -File -Force | ForEach-Object {
        $norm = Get-NormalPath $_.FullName
        if (-not $listedFull.Contains($norm)) {
            $rel = Get-RelativePath $AdapterDir $_.FullName
            Write-Host "unlisted adapter file ignored: $rel"
        }
    }
    $result
}

function Assert-BaseShards([string]$Snapshot) {
    if (-not (Test-Path -LiteralPath $ExpectedShardsJson -PathType Leaf)) {
        Write-Error "base shard gate: ExpectedShardsJson not found: $ExpectedShardsJson"
    }
    $doc = Get-Content -LiteralPath $ExpectedShardsJson -Raw -Encoding utf8 | ConvertFrom-Json
    $key = "$BaseRepo@$BaseRevision"
    $prop = $doc.PSObject.Properties[$key]
    if ($null -eq $prop) {
        Write-Error "base shard gate: missing key $key in $ExpectedShardsJson"
    }
    $files = @($prop.Value.files)
    if ($files.Count -lt 1) {
        Write-Error "base shard gate: no files listed for $key"
    }
    $result = [System.Collections.Generic.List[object]]::new()
    foreach ($shard in $files) {
        $rel = [string]$shard.path
        $expected = ([string]$shard.sha256).ToLower()
        $relWin = $rel -replace '/', '\'
        $resolved = Resolve-SnapshotFile $Snapshot $relWin
        $read1 = Get-FileSha256 $resolved
        $read2 = Get-FileSha256 $resolved
        if ($read1 -ne $expected -or $read2 -ne $expected) {
            Write-Error "base shard gate: $rel expected=$expected read1=$read1 read2=$read2"
        }
        $result.Add([pscustomobject]@{
                path         = $rel
                sha256_read1 = $read1
                sha256_read2 = $read2
            })
    }
    $result
}

function New-StepClock {
    [ordered]@{}
}

function Start-Step([hashtable]$Steps, [string]$Name) {
    $Steps[$Name] = [ordered]@{ start = (Get-UtcNowString); end = $null }
}

function End-Step([hashtable]$Steps, [string]$Name) {
    if ($Steps.Contains($Name)) {
        $Steps[$Name].end = Get-UtcNowString
    }
}

function Write-ExportReport {
    param(
        [string]$ReportPath,
        [string]$CheckpointPath,
        [int]$Index,
        $AdapterHashes,
        $BaseShards,
        [string]$ChatTemplateSha256,
        [string]$OutputPath,
        [long]$OutputSize,
        [string]$OutputSha1,
        [string]$OutputSha2,
        $FreeBeforeC,
        $FreeBeforeE,
        $FreeAfterC,
        $FreeAfterE,
        $Steps
    )
    $dir = Split-Path -Parent $ReportPath
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $report = [ordered]@{
        name                  = $Name
        checkpoint            = $CheckpointPath
        checkpoint_index      = $Index
        base_repo             = $BaseRepo
        base_revision         = $BaseRevision
        adapter_hashes        = $AdapterHashes
        base_shards           = @($BaseShards)
        chat_template_sha256  = $ChatTemplateSha256
        output                = [ordered]@{
            path         = $OutputPath
            size         = $OutputSize
            sha256_read1 = $OutputSha1
            sha256_read2 = $OutputSha2
        }
        free_space            = [ordered]@{
            before = [ordered]@{
                cFreeGiB   = $FreeBeforeC.cFreeGiB
                cFreeBytes = $FreeBeforeC.cFreeBytes
                eFreeGiB   = $FreeBeforeE.eFreeGiB
                eFreeBytes = $FreeBeforeE.eFreeBytes
            }
            after  = [ordered]@{
                cFreeGiB   = $FreeAfterC.cFreeGiB
                cFreeBytes = $FreeAfterC.cFreeBytes
                eFreeGiB   = $FreeAfterE.eFreeGiB
                eFreeBytes = $FreeAfterE.eFreeBytes
            }
        }
        steps                 = $Steps
        image_digest          = (Get-ImageDigest)
    }
    $json = $report | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath $ReportPath -Value $json -Encoding utf8NoBOM
}

function Publish-Gguf([string]$Source, [string]$Dest) {
    $destDir = Split-Path -Parent $Dest
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Dest) {
        Write-Error "publish: output already exists: $Dest"
    }
    $part = "$Dest.part"
    if (Test-Path -LiteralPath $part) {
        Remove-Item -LiteralPath $part -Force
    }
    $bytes = (Get-Item -LiteralPath $Source -Force).Length
    Assert-StorageGuard 'publish' $bytes | Out-Null
    try {
        Copy-Item -LiteralPath $Source -Destination $part -Force
        $srcHash = Get-FileSha256 $Source
        $copy1 = Get-FileSha256 $part
        $copy2 = Get-FileSha256 $part
        if ($srcHash -ne $copy1 -or $copy1 -ne $copy2) {
            Write-Error "publish: hash mismatch source=$srcHash copy1=$copy1 copy2=$copy2"
        }
        Rename-Item -LiteralPath $part -NewName (Split-Path -Leaf $Dest)
    } catch {
        if (Test-Path -LiteralPath $part) {
            Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        }
        if ((Test-Path -LiteralPath $Dest) -and -not (Test-Path -LiteralPath $Source)) {
            # do not delete anything outside staging; Dest is on C: only if rename happened
        }
        if (Test-Path -LiteralPath $Dest) {
            $final1 = Get-FileSha256 $Dest
            $final2 = Get-FileSha256 $Dest
            if ($final1 -ne $srcHash -or $final2 -ne $srcHash) {
                Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
            }
        }
        throw
    }
    $out1 = Get-FileSha256 $Dest
    $out2 = Get-FileSha256 $Dest
    if ($out1 -ne $out2) {
        Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
        Write-Error "publish: two reads of $Dest differ read1=$out1 read2=$out2"
    }
    [pscustomobject]@{
        path   = $Dest
        size   = (Get-Item -LiteralPath $Dest -Force).Length
        sha1   = $out1
        sha2   = $out2
    }
}

function Invoke-MergeDocker([string]$HfHost, [string]$AdapterHost, [string]$WorkHost) {
    $bash = @(
        'set -euo pipefail',
        "cat > /work/merge.py << 'PY'",
        $MergePy.Replace("`r`n", "`n").TrimEnd(),
        'PY',
        'python /work/merge.py'
    ) -join "`n"
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-e', 'HF_HUB_OFFLINE=1',
        '-e', "HL_BASE_REVISION=$BaseRevision",
        '-v', "${HfHost}:/hf:ro",
        '-v', "${AdapterHost}:/adapter:ro",
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    Invoke-DockerStep $dockerArgs $DockerTimeoutSec 'merge'
}

function Invoke-ConvertDocker([string]$HfHost, [string]$WorkHost, [string]$Bf16Name) {
    $bash = @(
        'set -euo pipefail',
        "python /opt/unsloth/llama.cpp/convert_hf_to_gguf.py /work/merged --outfile /work/$Bf16Name --outtype bf16"
    ) -join "`n"
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-e', 'HF_HUB_OFFLINE=1',
        '-v', "${HfHost}:/hf:ro",
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    Invoke-DockerStep $dockerArgs $DockerTimeoutSec 'convert'
}

function Invoke-QuantizeDocker([string]$WorkHost, [string]$Bf16Name, [string]$QuantName) {
    $bash = @(
        'set -euo pipefail',
        "/opt/unsloth/llama.cpp/llama-quantize /work/$Bf16Name /work/$QuantName Q4_K_M"
    ) -join "`n"
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    Invoke-DockerStep $dockerArgs $DockerTimeoutSec 'quantise'
}

function Invoke-ChatTemplateDocker([string]$WorkHost, [string]$QuantName) {
    $py = $ChatTemplatePy.Replace("`r`n", "`n").TrimEnd()
    $bash = @(
        'set -euo pipefail',
        "python -c '$py' /work/$QuantName"
    ) -join "`n"
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    $lines = Invoke-DockerStep $dockerArgs $DockerTimeoutSec 'chat-template'
    $last = @($lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
    if ($last.Count -lt 1) {
        Write-Error 'chat-template check: no digest printed'
    }
    ([string]$last[0]).Trim().ToLower()
}

if ($Checkpoint.Count -gt 2) {
    Write-Error "parameter Checkpoint: at most 2 adapter directories allowed, got $($Checkpoint.Count)"
}
if ($Checkpoint.Count -lt 1) {
    Write-Error 'parameter Checkpoint: at least 1 adapter directory is required'
}
if ($Name -cnotmatch '^[a-z0-9][a-z0-9-]{0,40}$') {
    Write-Error "parameter Name: must match ^[a-z0-9][a-z0-9-]{0,40}$, got '$Name'"
}

$utcStamp = Get-UtcStamp
$snapshot = Get-SnapshotPath $BaseRepo $BaseRevision
$cSnap = Get-StorageSnapshot
$eSnap = Get-EStorageSnapshot
$plans = [System.Collections.Generic.List[object]]::new()

for ($n = 1; $n -le $Checkpoint.Count; $n++) {
    $ckpt = $Checkpoint[$n - 1]
    $runFolder = Join-Path (Get-NormalPath $StagingRoot) "$Name-ckpt$n-$utcStamp"
    $output = Join-Path (Join-Path $CHotRoot 'models\m2') "$Name-ckpt$n-Q4_K_M.gguf"
    $report = Join-Path (Join-Path $CHotRoot 'reports') "m2-export-$Name-ckpt$n-$utcStamp.json"
    Write-Output "PLAN checkpoint=$n path=$ckpt"
    Write-Output "PLAN baseSnapshot=$snapshot"
    Write-Output "PLAN staging=$runFolder"
    Write-Output "PLAN output=$output"
    Write-Output "PLAN report=$report"
    Write-Output "PLAN cFreeGiB=$($cSnap.cFreeGiB)"
    Write-Output "PLAN eFreeGiB=$($eSnap.eFreeGiB)"
    $plans.Add([pscustomobject]@{
            Index      = $n
            Checkpoint = $ckpt
            Staging    = $runFolder
            Output     = $output
            Report     = $report
        })
}

if (-not $Execute) {
    Write-Output 'PLAN mode=dry-run (no docker, no copies, no deletes, no report file)'
    return
}

if (Test-PathIsUnder $StagingRoot $CHotRoot) {
    Write-Error "export pre-check: StagingRoot $StagingRoot is under CHotRoot $CHotRoot"
}

foreach ($plan in $plans) {
    $n = $plan.Index
    $ckpt = $plan.Checkpoint
    $runStaging = $plan.Staging
    $output = $plan.Output
    $reportPath = $plan.Report
    $keepStaging = $null
    $steps = New-StepClock
    $bf16Name = "$Name-bf16.gguf"
    $quantName = "$Name-Q4_K_M.gguf"
    try {
        Start-Step $steps 'pre-check'
        if (Test-Path -LiteralPath $output) {
            Write-Error "export pre-check: output already exists: $output"
        }
        Assert-EFree 'export pre-check' | Out-Null
        Assert-StorageGuard 'export pre-check' 0 | Out-Null
        End-Step $steps 'pre-check'

        $freeBeforeC = Get-StorageSnapshot
        $freeBeforeE = Get-EStorageSnapshot

        Start-Step $steps 'adapter_hash'
        if (-not (Test-Path -LiteralPath $ckpt -PathType Container)) {
            Write-Error "adapter hash gate: checkpoint is not a directory: $ckpt"
        }
        $adapterHost = Get-NormalPath ((Resolve-Path -LiteralPath $ckpt).Path)
        $adapterHashes = Assert-AdapterHashes $adapterHost
        End-Step $steps 'adapter_hash'

        Start-Step $steps 'base_shards'
        $baseShards = Assert-BaseShards $snapshot
        End-Step $steps 'base_shards'

        Start-Step $steps 'staging'
        New-Item -ItemType Directory -Path $runStaging -Force | Out-Null
        $keepStaging = $runStaging
        $marker = [ordered]@{
            runId      = "$Name-ckpt$n-$utcStamp"
            createdUtc = Get-UtcNowString
        }
        Set-Content -LiteralPath (Join-Path $runStaging '.hl-export-staging') -Value ($marker | ConvertTo-Json) -Encoding utf8NoBOM
        End-Step $steps 'staging'

        $hfHost = Get-NormalPath (Join-Path $CHotRoot 'hf')
        if (-not (Test-Path -LiteralPath $hfHost)) {
            Write-Error "merge: missing hf dir $hfHost"
        }
        $workHost = Get-NormalPath ((Resolve-Path -LiteralPath $runStaging).Path)

        Start-Step $steps 'merge'
        Invoke-MergeDocker $hfHost $adapterHost $workHost | Out-Null
        End-Step $steps 'merge'

        Start-Step $steps 'convert'
        Invoke-ConvertDocker $hfHost $workHost $bf16Name | Out-Null
        $bf16Host = Join-Path $workHost $bf16Name
        if (-not (Test-Path -LiteralPath $bf16Host -PathType Leaf)) {
            Write-Error "convert: expected bf16 GGUF missing: $bf16Host"
        }
        End-Step $steps 'convert'

        Start-Step $steps 'delete_merged'
        $mergedHost = Join-Path $workHost 'merged'
        Remove-StagedTree $mergedHost
        End-Step $steps 'delete_merged'

        Start-Step $steps 'quantise'
        Invoke-QuantizeDocker $workHost $bf16Name $quantName | Out-Null
        $quantHost = Join-Path $workHost $quantName
        if (-not (Test-Path -LiteralPath $quantHost -PathType Leaf)) {
            Write-Error "quantise: expected Q4_K_M GGUF missing: $quantHost"
        }
        End-Step $steps 'quantise'

        Start-Step $steps 'chat_template'
        $templateSha = Invoke-ChatTemplateDocker $workHost $quantName
        Write-Output "chat_template_sha256=$templateSha"
        $expectedTemplate = $BaseTemplateSha256.ToLower()
        if ($templateSha -ne $expectedTemplate) {
            Write-Error "chat-template check: expected=$expectedTemplate actual=$templateSha"
        }
        End-Step $steps 'chat_template'

        Start-Step $steps 'publish'
        $published = Publish-Gguf $quantHost $output
        End-Step $steps 'publish'

        $freeAfterC = Get-StorageSnapshot
        $freeAfterE = Get-EStorageSnapshot

        Start-Step $steps 'report'
        Write-ExportReport -ReportPath $reportPath -CheckpointPath $adapterHost -Index $n `
            -AdapterHashes $adapterHashes -BaseShards $baseShards `
            -ChatTemplateSha256 $templateSha -OutputPath $published.path `
            -OutputSize $published.size -OutputSha1 $published.sha1 -OutputSha2 $published.sha2 `
            -FreeBeforeC $freeBeforeC -FreeBeforeE $freeBeforeE -FreeAfterC $freeAfterC -FreeAfterE $freeAfterE `
            -Steps $steps
        End-Step $steps 'report'

        Remove-StagedTree $runStaging
        $keepStaging = $null
    } catch {
        if ($keepStaging) {
            Write-Output "staging kept for inspection: $keepStaging"
        }
        throw
    }
}
