# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:21 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | BEGIN]]
# M1-C1: convert pinned HF base snapshots to GGUF (CPU, no GPU), archive verified weights to E:, place finals under C:\llm\models\m1.
# Dry-run by default; -Execute performs conversion, archive, and placement. Never run on production weights from tests.
#   pwsh -File projects\homemade-llm\eval\convert_bases.ps1
#   pwsh -File projects\homemade-llm\eval\convert_bases.ps1 -Execute -Model Qwen/Qwen3-8B
[CmdletBinding()]
param(
    [switch]$Execute,
    [switch]$Preflight,
    [string]$Model,
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$CHotRoot = 'C:\llm',
    [string]$EArchiveRoot = 'E:\llm-archive\hf-bases-2026-09',
    [string]$GConvertRoot = 'G:\llm-convert-tmp',
    [string]$OfflineExpected,
    [int]$DockerTimeoutSec = 14400,
    [double]$MinCFreeGiB = 150,
    [double]$MaxLlmGiB = 120,
    [Nullable[long]]$ProbeCFreeBytes,
    [Nullable[long]]$ProbeLlmBytes,
    [string]$GpuPoisonPath,
    [string]$DockerPath
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\invoke-bounded.ps1')

$ConvertPy = '/opt/unsloth/llama.cpp/convert_hf_to_gguf.py'
$QuantizeBin = '/opt/unsloth/llama.cpp/llama-quantize'
$ArchiveTag = 'hf-bases-2026-09'

$Catalog = [ordered]@{
    'Qwen/Qwen3-8B' = @{
        Revision = 'b968826d9c46dd6066d109eabc6255188de91218'
        Key = 'qwen3-8b'
        Label = 'Qwen3-8B'
        Quants = @('Q4_K_M', 'Q8_0')
    }
    'Qwen/Qwen3-14B' = @{
        Revision = '40c069824f4251a91eefaf281ebe4c544efd3e18'
        Key = 'qwen3-14b'
        Label = 'Qwen3-14B'
        Quants = @('Q4_K_M')
    }
    'microsoft/phi-4' = @{
        Revision = '2db69c1c3e91a05d2c64a3185acfbaf36f744e25'
        Key = 'phi-4'
        Label = 'phi-4'
        Quants = @('Q4_K_M')
    }
    'mistralai/Mistral-Nemo-Base-2407' = @{
        Revision = 'a4477a2f977929a969745b69bbd62e03043551a5'
        Key = 'mistral-nemo-base-2407'
        Label = 'Mistral-Nemo-Base-2407'
        Quants = @('Q4_K_M')
    }
}

function Get-HubFolderName([string]$Repo) { 'models--' + ($Repo -replace '/', '--') }

function Get-SnapshotPath([string]$Repo, [string]$Revision) {
    $hub = Join-Path $CHotRoot 'hf\hub'
    Join-Path (Join-Path (Join-Path $hub (Get-HubFolderName $Repo)) 'snapshots') $Revision
}

function Get-StorageSnapshot {
    $free = if ($null -ne $ProbeCFreeBytes) { [double]$ProbeCFreeBytes } else {
        $v = Get-Volume -DriveLetter C -ErrorAction Stop
        [double]$v.SizeRemaining
    }
    $llm = if ($null -ne $ProbeLlmBytes) { [double]$ProbeLlmBytes } elseif (Test-Path -LiteralPath $CHotRoot) {
        [double](Get-ChildItem -LiteralPath $CHotRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    } else { 0.0 }
    [pscustomobject]@{
        cFreeGiB = [math]::Round($free / 1GB, 4)
        llmGiB = [math]::Round($llm / 1GB, 4)
        cFreeBytes = $free
        llmBytes = $llm
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

function Get-PoisonPath {
    if ($GpuPoisonPath) { return $GpuPoisonPath }
    $GpuMarker
}

function Assert-NotGpuPoisoned {
    $poison = Get-PoisonPath
    if (Test-Path -LiteralPath $poison) {
        Write-Error "GPU is poisoned: conversion is CPU-only and refuses while $poison exists ($((Get-Content -LiteralPath $poison -Raw).Trim()))"
    }
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

function Get-NormalPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { $full } else { $full.TrimEnd('\') }
}

function Get-SharedBlobPath([string]$Sha256) {
    $sha = $Sha256.ToLower()
    Join-Path $CHotRoot "hf\hub\blobs\$($sha.Substring(0, 2))\$sha"
}

function Get-FileSha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

function Get-OfflineManifest {
    if (-not $OfflineExpected) { return $null }
    if (-not (Test-Path -LiteralPath $OfflineExpected)) { Write-Error "OfflineExpected not found: $OfflineExpected" }
    Get-Content -LiteralPath $OfflineExpected -Raw | ConvertFrom-Json
}

function Get-ExpectedBlobMap([string]$Repo, [string]$Revision) {
    $offline = Get-OfflineManifest
    $key = "$Repo@$Revision"
    if ($offline -and $offline.PSObject.Properties.Name -contains $key) {
        $entry = $offline.$key
        $map = @{}
        foreach ($f in @($entry.files)) {
            $map[$f.path] = @{
                sha256 = $f.sha256.ToLower()
                size = if ($null -ne $f.size) { [long]$f.size } else { 0L }
            }
        }
        return $map
    }
    if ($offline) { Write-Error "OfflineExpected has no entry for $key" }
    $url = "https://huggingface.co/api/models/$Repo/revision/$Revision`?blobs=true"
    $resp = Invoke-RestMethod -Uri $url -Method Get
    $map = @{}
    foreach ($b in $resp.siblings) {
        if ($b.lfs -and $b.lfs.sha256) {
            $map[$b.path] = @{
                sha256 = $b.lfs.sha256.ToLower()
                size = [long]$b.lfs.size
            }
        }
    }
    $map
}

function Copy-FileVerified([string]$Source, [string]$Dest, [switch]$OntoCHot) {
    if ($OntoCHot) {
        $size = (Get-Item -LiteralPath $Source -Force).Length
        Assert-StorageGuard "before copy onto C: $(Split-Path $Dest -Leaf)" $size
    }
    $parent = Split-Path $Dest -Parent
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $Source -Destination $Dest -Force
}

function Convert-ContainerPathToHost([string]$ContainerPath) {
    $norm = $ContainerPath -replace '\\', '/'
    if ($norm -notmatch '^/hf/(.*)$') { Write-Error "container path not under /hf: $ContainerPath" }
    Get-NormalPath (Join-Path (Join-Path $CHotRoot 'hf') ($Matches[1] -replace '/', '\'))
}

function ConvertTo-BashCommand([string]$Script) {
    # Keep script quotes/newlines out of native argv; merge container stderr before Invoke-Bounded drains it.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script.Replace("`r`n", "`n")))
    "exec 2>&1; set -o pipefail; echo $encoded | base64 -d | bash"
}

function Invoke-DockerReadOnly([string[]]$DockerArgs, [int]$TimeoutSec, [string]$Label) {
    Write-Host "docker $Label (timeout ${TimeoutSec}s)"
    if ($DockerPath -and $DockerPath.EndsWith('.ps1', [StringComparison]::OrdinalIgnoreCase)) {
        $out = @(Invoke-Bounded 'pwsh' (@('-NoProfile', '-File', $DockerPath) + $DockerArgs) $TimeoutSec)
    } else {
        $dockerExe = if ($DockerPath) { $DockerPath } else { 'docker' }
        $out = @(Invoke-Bounded $dockerExe $DockerArgs $TimeoutSec)
    }
    $lines = @($out | Where-Object { $_ -notmatch '^__EXIT__' })
    $code = $out[-1]
    if ($code -ne '__EXIT__0') { Write-Error "docker $Label failed ($code): $($lines -join "`n")" }
    $lines
}

function Get-WeightBlobMapViaDocker([string]$Repo, [hashtable]$Entry, [string]$Snapshot) {
    $hfHost = (Resolve-Path (Join-Path $CHotRoot 'hf')).Path
    $hubName = Get-HubFolderName $Repo
    $inSnap = "/hf/hub/$hubName/snapshots/$($Entry.Revision)"
    $pyBody = @'
import json, os, sys
snap = sys.argv[1]
idx = os.path.join(snap, "model.safetensors.index.json")
single = os.path.join(snap, "model.safetensors")
if os.path.isfile(idx):
    with open(idx) as f:
        rels = sorted(set(json.load(f)["weight_map"].values()))
elif os.path.isfile(single):
    rels = ["model.safetensors"]
else:
    sys.stderr.write("no safetensors index or single shard under %s\n" % snap)
    sys.exit(1)
for rel in rels:
    print(rel)
'@
    $pyForBash = ($pyBody.Trim() -replace "'", "'\''")
    $bash = @"
set -euo pipefail
inSnap='$inSnap'
while IFS= read -r rel; do
  printf '%s|' "`$rel"
  readlink -f "`$inSnap/`$rel"
done < <(python3 -c '$pyForBash' "`$inSnap")
echo __MAPSEP__
"@
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-v', "${hfHost}:/hf:ro",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    $raw = Invoke-DockerReadOnly $dockerArgs 120 "blob-map $($Entry.Key)"
    $map = @{}
    foreach ($line in $raw) {
        if ($line -eq '__MAPSEP__') { continue }
        $idx = $line.IndexOf('|')
        if ($idx -lt 1) { Write-Error "unexpected blob-map line: $line" }
        $rel = $line.Substring(0, $idx)
        $containerPath = $line.Substring($idx + 1)
        $map[$rel] = Convert-ContainerPathToHost $containerPath
    }
    if (-not $map.Count) { Write-Error "blob map returned no weights for $($Entry.Key)" }
    $map
}

function Assert-MappedBlob([string]$BlobPath, [hashtable]$Expected) {
    if (-not (Test-Path -LiteralPath $BlobPath)) { Write-Error "mapped blob missing: $BlobPath" }
    $item = Get-Item -LiteralPath $BlobPath -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Error "mapped blob is still a reparse point on Windows: $BlobPath"
    }
    $sha = Get-FileSha256 $BlobPath
    if ($sha -ne $Expected.sha256) {
        Write-Error "hash mismatch on mapped blob $BlobPath (got $sha, expected $($Expected.sha256))"
    }
    if ($Expected.size -gt 0 -and $item.Length -ne $Expected.size) {
        Write-Error "size mismatch on mapped blob $BlobPath (got $($item.Length), expected $($Expected.size))"
    }
    $sha
}

function Invoke-ConvertDocker([hashtable]$Entry, [string]$Repo, [string]$Snapshot, [string]$WorkHost) {
    $hfHost = (Resolve-Path (Join-Path $CHotRoot 'hf')).Path
    $hubName = Get-HubFolderName $Repo
    $inSnap = "/hf/hub/$hubName/snapshots/$($Entry.Revision)"
    $intermediate = "/work/$($Entry.Label)-bf16.gguf"
    $quantLines = @()
    foreach ($q in $Entry.Quants) {
        $out = "/work/$($Entry.Label)-$q.gguf"
        $quantLines += "$QuantizeBin $intermediate $out $q"
    }
    $bash = @(
        "set -euo pipefail",
        "python $ConvertPy $inSnap --outfile $intermediate --outtype bf16",
        ($quantLines -join ' && ')
    ) -join ' && '
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-e', "HL_CONVERT_MODEL=$($Entry.Key)",
        '-v', "${hfHost}:/hf:ro",
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    Write-Host "docker convert $($Entry.Key) (timeout ${DockerTimeoutSec}s)"
    $env:HL_CONVERT_MODEL = $Entry.Key
    if ($DockerPath -and $DockerPath.EndsWith('.ps1', [StringComparison]::OrdinalIgnoreCase)) {
        $out = @(Invoke-Bounded 'pwsh' (@('-NoProfile', '-File', $DockerPath) + $dockerArgs) $DockerTimeoutSec)
    } else {
        $dockerExe = if ($DockerPath) { $DockerPath } else { 'docker' }
        $out = @(Invoke-Bounded $dockerExe $dockerArgs $DockerTimeoutSec)
    }
    Remove-Item Env:HL_CONVERT_MODEL -ErrorAction SilentlyContinue
    $lines = @($out | Where-Object { $_ -notmatch '^__EXIT__' })
    $lines | ForEach-Object { Write-Host $_ }
    $code = $out[-1]
    if ($code -ne '__EXIT__0') { Write-Error "docker convert failed ($code): $($lines -join "`n")" }
}

function Get-GgufArtifacts([string]$WorkHost, [hashtable]$Entry) {
    $list = [System.Collections.Generic.List[object]]::new()
    foreach ($q in $Entry.Quants) {
        $path = Join-Path $WorkHost "$($Entry.Label)-$q.gguf"
        if (-not (Test-Path -LiteralPath $path)) { Write-Error "expected GGUF missing: $path" }
        $list.Add([pscustomobject]@{
                quant = $q
                path = $path
                bytes = (Get-Item -LiteralPath $path).Length
                sha256 = Get-FileSha256 $path
            })
    }
    $inter = Join-Path $WorkHost "$($Entry.Label)-bf16.gguf"
    if (Test-Path -LiteralPath $inter) {
        $list.Add([pscustomobject]@{
                quant = 'bf16'
                path = $inter
                bytes = (Get-Item -LiteralPath $inter).Length
                sha256 = Get-FileSha256 $inter
            })
    }
    $list
}

function Archive-VerifiedWeights([string]$Repo, [hashtable]$Entry, [string]$Snapshot) {
    $expected = Get-ExpectedBlobMap $Repo $Entry.Revision
    $blobMap = Get-WeightBlobMapViaDocker $Repo $Entry $Snapshot
    $relWeights = @($blobMap.Keys | Sort-Object)
    $archiveBase = Join-Path $EArchiveRoot (Get-HubFolderName $Repo)
    $copied = [System.Collections.Generic.List[object]]::new()
    $blobPaths = [System.Collections.Generic.HashSet[string]]::new()

    foreach ($rel in $relWeights) {
        if (-not $expected.ContainsKey($rel)) { Write-Error "no expected sha256 for weight $rel in manifest" }
        $srcBlob = $blobMap[$rel]
        $sha = Assert-MappedBlob $srcBlob $expected[$rel]
        $dest = Join-Path (Join-Path $archiveBase (Join-Path 'snapshots' $Entry.Revision)) $rel
        Copy-FileVerified $srcBlob $dest
        $copied.Add([pscustomobject]@{ path = $dest; sha256 = $sha; role = 'weight' })
        $blobPaths.Add($srcBlob) | Out-Null
    }

    foreach ($c in $copied) {
        $onDisk = Get-FileSha256 $c.path
        if ($onDisk -ne $c.sha256) { Write-Error "archive verification failed for $($c.path)" }
    }

    $removed = [System.Collections.Generic.List[object]]::new()
    foreach ($blobPath in $blobPaths) {
        if (Test-Path -LiteralPath $blobPath) {
            $sha = Get-FileSha256 $blobPath
            Write-Host "remove C: blob $blobPath (sha256 $sha)"
            Remove-Item -LiteralPath $blobPath -Force
            $removed.Add([pscustomobject]@{ path = $blobPath; sha256 = $sha })
        }
    }

    $dangling = @($relWeights | ForEach-Object { Join-Path $Snapshot $_ })
    return , @($copied), @($removed), @($dangling)
}

function Place-FinalGgufs([hashtable]$Entry, [object[]]$Ggufs, [string]$WorkHost) {
    $destRoot = Join-Path $CHotRoot "models\m1\$($Entry.Key)"
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
    $placed = [System.Collections.Generic.List[object]]::new()
    foreach ($g in $Ggufs | Where-Object { $_.quant -ne 'bf16' }) {
        $dest = Join-Path $destRoot "$(Split-Path $g.path -Leaf)"
        Copy-FileVerified $g.path $dest -OntoCHot
        $sha = Get-FileSha256 $dest
        if ($sha -ne $g.sha256) { Write-Error "placed GGUF hash mismatch at $dest" }
        $placed.Add([pscustomobject]@{ path = $dest; quant = $g.quant; bytes = $g.bytes; sha256 = $sha })
    }
    foreach ($g in $Ggufs) {
        if (Test-Path -LiteralPath $g.path) {
            Write-Host "remove G: temp $($g.path)"
            Remove-Item -LiteralPath $g.path -Force
        }
    }
    if (Test-Path -LiteralPath $WorkHost) {
        $left = @(Get-ChildItem -LiteralPath $WorkHost -Force -ErrorAction SilentlyContinue)
        if (-not $left.Count) { Remove-Item -LiteralPath $WorkHost -Force -ErrorAction SilentlyContinue }
    }
    return , @($placed)
}

function Write-ModelReceipt(
    [hashtable]$Entry,
    [string]$Repo,
    [object[]]$Ggufs,
    [object[]]$Archived,
    [object[]]$Removed,
    [object[]]$DanglingLinks,
    [object]$StorageBefore,
    [object]$StorageAfter
) {
    $reports = Join-Path $CHotRoot 'reports'
    if ($Execute) { New-Item -ItemType Directory -Force -Path $reports | Out-Null }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $receiptPath = Join-Path $reports "m1-convert-$($Entry.Key)-$stamp.json"
    $body = [ordered]@{
        model = $Entry.Key
        repo = $Repo
        revision = $Entry.Revision
        utc = (Get-Date).ToUniversalTime().ToString('o')
        gguf = @($Ggufs | Where-Object { $_.quant -ne 'bf16' } | ForEach-Object {
                [ordered]@{ path = $_.path; quant = $_.quant; bytes = $_.bytes; sha256 = $_.sha256 }
            })
        archived = @($Archived | ForEach-Object { [ordered]@{ path = $_.path; sha256 = $_.sha256; role = $_.role } })
        removed = @($Removed | ForEach-Object { [ordered]@{ path = $_.path; sha256 = $_.sha256 } })
        dangling_links_left = @($DanglingLinks)
        storage = [ordered]@{
            before = [ordered]@{ cFreeGiB = $StorageBefore.cFreeGiB; llmGiB = $StorageBefore.llmGiB }
            after = [ordered]@{ cFreeGiB = $StorageAfter.cFreeGiB; llmGiB = $StorageAfter.llmGiB }
        }
        tools = [ordered]@{
            convertScript = $ConvertPy
            quantize = $QuantizeBin
            image = $Image
        }
    }
    if ($Execute) {
        $body | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding utf8
        Write-Host "receipt $receiptPath"
    } else {
        Write-Host "plan receipt -> $receiptPath"
    }
    $receiptPath
}

function Show-PlanLine([string]$Line) { Write-Output "PLAN $Line" }

$CHotRoot = Get-NormalPath $CHotRoot
$EArchiveRoot = Get-NormalPath $EArchiveRoot
$GConvertRoot = Get-NormalPath $GConvertRoot

Assert-NotGpuPoisoned

$sequence = @($Catalog.Keys)
if ($Model) {
    if (-not $Catalog.Contains($Model)) { Write-Error "unknown -Model $Model (expected one of: $($Catalog.Keys -join ', '))" }
    $sequence = @($Model)
}

foreach ($repo in $sequence) {
    $entry = $Catalog[$repo]
    $storageBefore = Assert-StorageGuard "model $($entry.Key) start"
    $snapshot = Get-SnapshotPath $repo $entry.Revision
    $workHost = Join-Path $GConvertRoot $entry.Key
    $m1Dest = Join-Path $CHotRoot "models\m1\$($entry.Key)"

    Show-PlanLine "model=$($entry.Key) repo=$repo revision=$($entry.Revision)"
    Show-PlanLine "snapshot=$snapshot"
    Show-PlanLine "convert_work=$workHost"
    Show-PlanLine "quants=$($entry.Quants -join ',')"
    Show-PlanLine "archive=$EArchiveRoot"
    Show-PlanLine "place=$m1Dest"

    if (-not $Execute) {
        Show-PlanLine "mode=dry-run (no docker convert, no copies, no deletes, no receipt file)"
        Show-PlanLine "storage cFreeGiB=$($storageBefore.cFreeGiB) llmGiB=$($storageBefore.llmGiB)"
        if ($Preflight -and (Test-Path -LiteralPath $snapshot)) {
            $blobMap = Get-WeightBlobMapViaDocker $repo $entry $snapshot
            foreach ($rel in @($blobMap.Keys | Sort-Object)) {
                Show-PlanLine "blob_map $rel=$($blobMap[$rel])"
            }
        }
        continue
    }

    if (-not (Test-Path -LiteralPath $snapshot)) { Write-Error "snapshot missing: $snapshot" }
    New-Item -ItemType Directory -Force -Path $workHost | Out-Null

    Invoke-ConvertDocker $entry $repo $snapshot $workHost
    $ggufs = @(Get-GgufArtifacts $workHost $entry)

    $archived = @()
    $removed = @()
    $dangling = @()
    try {
        $archived, $removed, $dangling = Archive-VerifiedWeights $repo $entry $snapshot
    } catch {
        Write-Error "archive aborted; nothing removed from C: ($($_.Exception.Message))"
    }

    $placed = @(Place-FinalGgufs $entry $ggufs $workHost)
    $storageAfter = Get-StorageSnapshot
    $receipt = Write-ModelReceipt $entry $repo @($placed) $archived $removed $dangling $storageBefore $storageAfter

    $required = @('model', 'repo', 'revision', 'gguf', 'archived', 'removed', 'storage', 'tools')
    $parsed = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    foreach ($k in $required) {
        if ($parsed.PSObject.Properties.Name -notcontains $k) { Write-Error "receipt missing field $k" }
    }
}

exit 0
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:27 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 21:25 UTC | END]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:37 UTC | END]]
