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
    [string]$TrustedDigestManifest = (Join-Path $CHotRoot 'reports\m2-trusted-digest-manifest-v2.json'),
    [string]$ExpectedShardsJson = (Join-Path $CHotRoot 'reports\m2-expected-qwen3-14b-v2.json'),
    [string]$BaseTemplateSha256 = 'a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8',
    [double]$MinCFreeGiB = 150,
    [double]$MaxLlmGiB = 120,
    [double]$MinEFreeGiB = 100,
    [int]$DockerTimeoutSec = 14400,
    [string]$DockerPath,
    [Nullable[long]]$ProbeCFreeBytes,
    [Nullable[long]]$ProbeEFreeBytes,
    [Nullable[long]]$ProbeLlmBytes,
    [string]$RunId = ''
)

$Checkpoint = [string[]](@($Checkpoint) | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')

# Pinned authority: the root trust manifest's bytes must hash to exactly this value.
# No parameter may override this constant; only its path (-TrustedDigestManifest) may move.
$script:PinnedTrustedDigestManifestSha256 = 'd1a4a2d921c3f5197c2867dcdb50dbfb46d2189bbb88141ed56d3f3eb19d36af'

$MergePy = @'
import os, sys, json, hashlib
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
snap = "/base-snapshot"
def verify_base_snapshot():
    raw = open("/base-integrity.json", "rb").read()
    if hashlib.sha256(raw).hexdigest() != os.environ["HL_BASE_GATE_SHA256"]:
        raise RuntimeError("base load gate: integrity envelope hash mismatch")
    approved = json.loads(raw)
    expected = {entry["path"]: entry for entry in approved["files"]}
    present = set()
    for directory, directories, files in os.walk(snap, followlinks=False):
        if any(os.path.islink(os.path.join(directory, name)) for name in directories + files):
            raise RuntimeError("base load gate: snapshot link refused")
        present.update(os.path.relpath(os.path.join(directory, name), snap).replace(os.sep, "/") for name in files)
    if present != set(expected) or len(expected) != approved["covered_files"]:
        raise RuntimeError("base load gate: snapshot coverage mismatch")
    for relative, entry in expected.items():
        path = os.path.join(snap, relative)
        if os.path.getsize(path) != entry["size"]:
            raise RuntimeError("base load gate: size mismatch: " + relative)
        def digest():
            result = hashlib.sha256()
            with open(path, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    result.update(block)
            return result.hexdigest()
        read1, read2 = digest(), digest()
        if read1 != entry["sha256"] or read2 != entry["sha256"]:
            raise RuntimeError("base load gate: two-read approved hash mismatch: " + relative)
verify_base_snapshot()
base = AutoModelForCausalLM.from_pretrained(snap, torch_dtype=torch.bfloat16, device_map={"": "cpu"}, local_files_only=True)
model = PeftModel.from_pretrained(base, "/adapter", local_files_only=True).merge_and_unload()
model.save_pretrained("/work/merged", safe_serialization=True)
verify_base_snapshot()
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

function Get-Sha256Bytes([byte[]]$Bytes) {
    $h = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($h.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $h.Dispose() }
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

function Assert-StorageGuard([string]$Context, [long]$AdditionalBytesOnC = 0, [long]$AdditionalBytesOutsideHotOnC = 0) {
    $s = Get-StorageSnapshot
    if ($s.cFreeBytes -lt $MinCFreeGiB * 1GB) {
        Write-Error "storage guard ($Context): C: free $($s.cFreeGiB) GiB is below floor $MinCFreeGiB GiB; stopping with no further changes"
    }
    if ($s.llmBytes -gt $MaxLlmGiB * 1GB) {
        Write-Error "storage guard ($Context): $CHotRoot is $($s.llmGiB) GiB, above cap $MaxLlmGiB GiB; stopping with no further changes"
    }
    if ($AdditionalBytesOnC -gt 0 -or $AdditionalBytesOutsideHotOnC -gt 0) {
        $projFree = $s.cFreeBytes - $AdditionalBytesOnC - $AdditionalBytesOutsideHotOnC
        $projLlm = $s.llmBytes + $AdditionalBytesOnC
        if ($projFree -lt $MinCFreeGiB * 1GB) {
            Write-Error "storage guard ($Context): C: free would be $([math]::Round($projFree / 1GB, 4)) GiB after a $([math]::Round(([decimal]$AdditionalBytesOnC + $AdditionalBytesOutsideHotOnC) / 1GB, 4)) GiB copy, below floor $MinCFreeGiB GiB; stopping with no further changes"
        }
        if ($projLlm -gt $MaxLlmGiB * 1GB) {
            Write-Error "storage guard ($Context): $CHotRoot would be $([math]::Round($projLlm / 1GB, 4)) GiB after a $([math]::Round($AdditionalBytesOnC / 1GB, 4)) GiB copy, above cap $MaxLlmGiB GiB; stopping with no further changes"
        }
    }
    $s
}

function Assert-EFree([string]$Context, [long]$AdditionalBytesOnE = 0) {
    $e = Get-EStorageSnapshot
    if ($e.eFreeBytes -lt $MinEFreeGiB * 1GB) {
        Write-Error "storage guard ($Context): E: free $($e.eFreeGiB) GiB is below floor $MinEFreeGiB GiB; stopping with no further changes"
    }
    if ($AdditionalBytesOnE -gt 0 -and ($e.eFreeBytes - $AdditionalBytesOnE) -lt $MinEFreeGiB * 1GB) {
        Write-Error "storage guard ($Context): E: free would be below floor $MinEFreeGiB GiB after a $AdditionalBytesOnE byte snapshot copy; stopping with no further changes"
    }
    $e
}

# Small, test-replaceable wrapper: tests redefine this function to replay canned
# `fsutil reparsepoint query` output instead of invoking the real tool.
function Invoke-FsutilReparseQuery([string]$Path) {
    & fsutil reparsepoint query $Path 2>&1
}

# Decodes the LX symlink (reparse tag 0xa000001d) hex dump that `fsutil reparsepoint
# query` prints for the WSL-style reparse points found in the Hugging Face cache on
# this host (where Get-Item's .Target is empty and Get-FileHash cannot read the link).
function Resolve-LxReparseTarget([string]$Path) {
    $lines = @(Invoke-FsutilReparseQuery $Path)
    if ($lines.Count -lt 1) {
        Write-Error "LX symlink gate: empty fsutil reparsepoint query output for $Path"
    }
    $tagLine = @($lines | Where-Object { $_ -match 'Reparse\s*Tag\s*Value\s*:\s*(0x[0-9A-Fa-f]+)' }) | Select-Object -First 1
    if (-not $tagLine) {
        Write-Error "LX symlink gate: no reparse tag found in fsutil output for $Path"
    }
    [void]($tagLine -match 'Reparse\s*Tag\s*Value\s*:\s*(0x[0-9A-Fa-f]+)')
    $tag = $Matches[1].ToLower()
    if ($tag -ne '0xa000001d') {
        Write-Error "LX symlink gate: unexpected reparse tag $tag at $Path (expected 0xa000001d)"
    }
    $hexBytes = [System.Collections.Generic.List[byte]]::new()
    $sawDump = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*[0-9A-Fa-f]{4}:\s+((?:[0-9A-Fa-f]{2}\s+)+)') {
            $sawDump = $true
            foreach ($b in (($Matches[1].Trim()) -split '\s+')) {
                [void]$hexBytes.Add([Convert]::ToByte($b, 16))
            }
        }
    }
    if (-not $sawDump -or $hexBytes.Count -le 4) {
        Write-Error "LX symlink gate: unparsable or empty reparse data dump for $Path"
    }
    $payload = $hexBytes.GetRange(4, $hexBytes.Count - 4).ToArray()
    $target = [Text.Encoding]::UTF8.GetString($payload)
    if ([string]::IsNullOrWhiteSpace($target)) {
        Write-Error "LX symlink gate: empty decoded target for $Path"
    }
    $target
}

function Assert-PathInsideHub([string]$Path, [string]$HubRoot, [string]$Context) {
    $pathN = Get-NormalPath $Path
    $hubN = Get-NormalPath $HubRoot
    $hubPrefix = $hubN + [IO.Path]::DirectorySeparatorChar
    if (-not $pathN.Equals($hubN, [StringComparison]::OrdinalIgnoreCase) -and -not $pathN.StartsWith($hubPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "${Context}: resolved path escapes hub folder ${hubN}: $pathN"
    }
    # Prefix confinement is lexical. Refuse directory redirects before querying
    # the leaf; the leaf itself may be a supported file link resolved below.
    Assert-NoReparseExistingAncestors $hubN $Context
    Assert-NoReparseExistingAncestors (Split-Path -Parent $pathN) $Context
}

# Resolves a snapshot-relative weight/metadata file to its final, readable, regular-file
# path, following at most 4 reparse hops. Supports ordinary NTFS file symlinks
# (non-empty .Target) and the WSL LX symlinks used by the Hugging Face cache on this
# host (empty .Target, decoded via Resolve-LxReparseTarget). The snapshot's own entry
# name must stay inside the repo's hub folder (HubRoot); every hop thereafter must stay
# inside the shared hub cache root (HubCacheRoot, the parent of every repo folder),
# because real weight blobs on this host resolve through a repo-folder link to a
# second LX link that lands in the hub-level shared blob store one level above the
# repo folder. A target reached through at least one hop must land inside either the
# repo's own `blobs` folder or the hub cache root's shared `blobs` folder (including
# its two-hex subfolders) -- the two legitimate Hugging Face cache layouts.
function Resolve-SnapshotFile([string]$Snapshot, [string]$Relative, [string]$HubRoot, [string]$HubCacheRoot) {
    Assert-NoReparseExistingAncestors $Snapshot 'LX symlink gate'
    Assert-NoReparseExistingAncestors $HubCacheRoot 'LX symlink gate'
    $full = Get-NormalPath (Join-Path $Snapshot $Relative)
    Assert-PathInsideHub $full $HubRoot 'LX symlink gate'
    if (-not (Test-Path -LiteralPath $full)) { Write-Error "missing weight file $full" }
    $current = $full
    $hops = 0
    while ($true) {
        $item = Get-Item -LiteralPath $current -Force
        if ($item.PSIsContainer) {
            Write-Error "LX symlink gate: resolved target is a directory: $current"
        }
        if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            if ($hops -gt 0) {
                $repoBlobsPrefix = (Get-NormalPath (Join-Path $HubRoot 'blobs')) + [IO.Path]::DirectorySeparatorChar
                $hubBlobsPrefix = (Get-NormalPath (Join-Path $HubCacheRoot 'blobs')) + [IO.Path]::DirectorySeparatorChar
                $inRepoBlobs = $current.StartsWith($repoBlobsPrefix, [StringComparison]::OrdinalIgnoreCase)
                $inHubBlobs = $current.StartsWith($hubBlobsPrefix, [StringComparison]::OrdinalIgnoreCase)
                if (-not $inRepoBlobs -and -not $inHubBlobs) {
                    Write-Error "LX symlink gate: resolved link target is not inside the repo blobs folder or the hub blobs store: $current"
                }
            }
            return $current
        }
        if ($hops -ge 4) {
            Write-Error "LX symlink gate: more than 4 hops resolving $full"
        }
        $hops++
        $target = @($item.Target)
        $rel = $null
        if ($target -and $target.Count -gt 0 -and -not [string]::IsNullOrEmpty([string]$target[0])) {
            $rel = [string]$target[0]
        } else {
            $rel = Resolve-LxReparseTarget $current
        }
        $relNorm = $rel -replace '/', '\'
        if ($relNorm -match '^[A-Za-z]:' -or $relNorm.StartsWith('\')) {
            $current = Get-NormalPath $relNorm
        } else {
            $current = Get-NormalPath (Join-Path (Split-Path -Parent $current) $relNorm)
        }
        Assert-PathInsideHub $current $HubCacheRoot 'LX symlink gate'
        if (-not (Test-Path -LiteralPath $current)) {
            Write-Error "LX symlink gate: missing link target $current"
        }
    }
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

function Assert-NoReparseExistingAncestors([string]$Path, [string]$Context) {
    $pathN = Get-NormalPath $Path
    $driveRoot = [IO.Path]::GetPathRoot($pathN)
    if ([string]::IsNullOrEmpty($driveRoot)) { return }
    if (Test-Path -LiteralPath $driveRoot) {
        $item = Get-Item -LiteralPath $driveRoot -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "${Context}: reparse point at $driveRoot"
        }
    }
    $current = $driveRoot.TrimEnd('\')
    $rel = $pathN.Substring($driveRoot.Length).TrimStart('\')
    foreach ($seg in ($rel -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($seg)) { continue }
        $current = Join-Path $current $seg
        if (-not (Test-Path -LiteralPath $current)) { continue }
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "${Context}: reparse point at $current"
        }
    }
}

function Assert-NoReparseOnPath([string]$Root, [string]$Target, [string]$Context = 'Remove-StagedTree') {
    Assert-NoReparseExistingAncestors $Root $Context
    if (-not [string]::IsNullOrWhiteSpace($Target)) {
        $targetN = Get-NormalPath $Target
        $rootN = Get-NormalPath $Root
        if (-not $targetN.Equals($rootN, [StringComparison]::OrdinalIgnoreCase)) {
            Assert-NoReparseExistingAncestors $Target $Context
        }
    }
    $rootN = Get-NormalPath $Root
    $targetN = Get-NormalPath $Target
    $current = $rootN
    if (Test-Path -LiteralPath $current) {
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "${Context}: reparse point at $current"
        }
    }
    $rel = $targetN.Substring($rootN.Length).TrimStart('\')
    if ([string]::IsNullOrWhiteSpace($rel)) { return }
    foreach ($seg in ($rel -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($seg)) { continue }
        $current = Join-Path $current $seg
        if (-not (Test-Path -LiteralPath $current)) {
            Write-Error "${Context}: missing path $current"
        }
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "${Context}: reparse point at $current"
        }
    }
}

function Assert-PhysicalStagingContainment([string]$RunPath, [string]$Context) {
    Assert-NoReparseExistingAncestors (Get-NormalPath $CHotRoot) $Context
    $staging = Get-NormalPath $StagingRoot
    $stagingResolved = $staging
    if (Test-Path -LiteralPath $staging -PathType Container) {
        $stagingResolved = Get-NormalPath ((Resolve-Path -LiteralPath $staging).Path)
    }
    if (Test-Path -LiteralPath $CHotRoot -PathType Container) {
        $chotResolved = Get-NormalPath ((Resolve-Path -LiteralPath $CHotRoot).Path)
        if (Test-PathIsUnder $stagingResolved $chotResolved) {
            Write-Error "${Context}: StagingRoot $stagingResolved is under CHotRoot $chotResolved"
        }
    }
    if ([string]::IsNullOrWhiteSpace($RunPath)) { return }
    if (-not (Test-Path -LiteralPath $staging -PathType Container)) {
        Write-Error "${Context}: staging root does not exist: $staging"
    }
    $stagingResolved = Get-NormalPath ((Resolve-Path -LiteralPath $staging).Path)
    if (-not (Test-Path -LiteralPath $RunPath)) {
        Write-Error "${Context}: run path does not exist: $RunPath"
    }
    $runResolved = Get-NormalPath ((Resolve-Path -LiteralPath $RunPath).Path)
    if (-not (Test-PathIsUnder $runResolved $stagingResolved)) {
        Write-Error "${Context}: $runResolved is not strictly inside $stagingResolved"
    }
}

function Assert-StagingMarkerOwned([string]$RunStaging, [string]$OwnershipToken, [string]$Context = 'Remove-StagedTree') {
    $marker = Join-Path $runStaging '.hl-export-staging'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        Write-Error "${Context}: missing .hl-export-staging marker in $RunStaging"
    }
    try {
        $doc = Get-Content -LiteralPath $marker -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        Write-Error "${Context}: unreadable .hl-export-staging marker in $RunStaging"
    }
    $token = [string]$doc.ownershipToken
    if ([string]::IsNullOrWhiteSpace($token)) {
        Write-Error "${Context}: .hl-export-staging marker has no ownership token in $RunStaging"
    }
    if (-not $token.Equals($OwnershipToken, [StringComparison]::Ordinal)) {
        Write-Error "${Context}: .hl-export-staging ownership token does not match this execution"
    }
}

function Remove-StagedTree([string]$Target, [string]$OwnershipToken) {
    if ([string]::IsNullOrWhiteSpace($OwnershipToken)) {
        Write-Error 'Remove-StagedTree: ownership token is required'
    }
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
    Assert-NoReparseOnPath $staging $resolved
    Assert-PhysicalStagingContainment $runStaging 'Remove-StagedTree'
    Assert-StagingMarkerOwned $runStaging $OwnershipToken
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

function Assert-NormalizedRelativePath([string]$Rel, [string]$Context) {
    $ok = $true
    if ([string]::IsNullOrEmpty($Rel)) {
        $ok = $false
    } elseif ($Rel.Contains('\') -or $Rel.Contains(':') -or $Rel.StartsWith('/')) {
        $ok = $false
    } else {
        foreach ($seg in ($Rel.Split('/'))) {
            if ([string]::IsNullOrEmpty($seg) -or $seg -eq '.' -or $seg -eq '..') {
                $ok = $false
                break
            }
            if ($seg.EndsWith(' ') -or $seg.EndsWith('.')) {
                $ok = $false
                break
            }
        }
    }
    if (-not $ok) {
        Write-Error "${Context}: invalid relative path $Rel"
    }
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
    $unique = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($prop in $doc.files.PSObject.Properties) {
        $rel = [string]$prop.Name
        Assert-NormalizedRelativePath $rel 'adapter hash gate'
        $expectedRaw = [string]$prop.Value
        if ($expectedRaw -notmatch '^[0-9a-fA-F]{64}$') {
            Write-Error "adapter hash gate: invalid hash value $rel"
        }
        if (-not $unique.Add($rel)) {
            Write-Error "adapter hash gate: duplicate path $rel"
        }
    }
    $adapterN = Get-NormalPath $AdapterDir
    $insidePrefix = $adapterN + [IO.Path]::DirectorySeparatorChar
    foreach ($prop in $doc.files.PSObject.Properties) {
        $rel = [string]$prop.Name
        $expected = ([string]$prop.Value).ToLower()
        $full = [IO.Path]::GetFullPath((Join-Path $AdapterDir ($rel -replace '/', '\')))
        $fullN = Get-NormalPath $full
        if (-not $fullN.StartsWith($insidePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Write-Error "adapter hash gate: path not inside adapter directory: $rel"
        }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            Write-Error "adapter hash gate: $rel expected=$expected actual=missing"
        }
        Assert-NoReparseOnPath $AdapterDir $full 'adapter hash gate'
    }
    $stack = [System.Collections.Generic.Stack[string]]::new()
    $stack.Push($adapterN)
    $diskFiles = [System.Collections.Generic.List[string]]::new()
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        $dirItem = Get-Item -LiteralPath $dir -Force
        if ($dirItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "adapter hash gate: reparse point at $dir"
        }
        foreach ($child in @(Get-ChildItem -LiteralPath $dir -Force)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Write-Error "adapter hash gate: reparse point at $($child.FullName)"
            }
            if ($child.PSIsContainer) {
                $stack.Push($child.FullName)
            } else {
                [void]$diskFiles.Add($child.FullName)
            }
        }
    }
    $hashFileN = Get-NormalPath $hashFile
    foreach ($fullFile in $diskFiles) {
        if ((Get-NormalPath $fullFile).Equals($hashFileN, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $rel = Get-RelativePath $AdapterDir $fullFile
        if (-not $unique.Contains($rel)) {
            Write-Error "adapter hash gate: unlisted file $rel"
        }
    }
    $result = [ordered]@{}
    foreach ($prop in $doc.files.PSObject.Properties) {
        $rel = [string]$prop.Name
        $expected = ([string]$prop.Value).ToLower()
        $full = [IO.Path]::GetFullPath((Join-Path $AdapterDir ($rel -replace '/', '\')))
        $actual = Get-FileSha256 $full
        if ($actual -ne $expected) {
            Write-Error "adapter hash gate: $rel expected=$expected actual=$actual"
        }
        $result[$rel] = $actual
    }
    $result
}

function Get-TrustedListDigest([string]$RootPath, [string]$Repo, [string]$Revision) {
    if (-not (Test-Path -LiteralPath $RootPath -PathType Leaf)) {
        Write-Error "trust root gate: root manifest not found: $RootPath"
    }
    $rootBytes = [IO.File]::ReadAllBytes($RootPath)
    $rootHash = Get-Sha256Bytes $rootBytes
    if ($rootHash -ne $script:PinnedTrustedDigestManifestSha256) {
        Write-Error "trust root gate: root manifest hash mismatch expected=$($script:PinnedTrustedDigestManifestSha256) actual=$rootHash path=$RootPath"
    }
    $rootDoc = [Text.Encoding]::UTF8.GetString($rootBytes) | ConvertFrom-Json
    if ($null -eq $rootDoc.models) {
        Write-Error "trust root gate: root manifest has no models array"
    }
    $matched = @($rootDoc.models | Where-Object { [string]$_.hf_repo -ceq $Repo -and [string]$_.hf_revision -ceq $Revision })
    if ($matched.Count -ne 1) {
        Write-Error "trust root gate: expected exactly one model entry for $Repo@$Revision, found $($matched.Count)"
    }
    $digest = [string]$matched[0].upstream_weight_digests_sha256
    if ($digest -cnotmatch '^[0-9a-f]{64}$') {
        Write-Error "trust root gate: upstream_weight_digests_sha256 is not 64 lowercase hex for $Repo@$Revision"
    }
    [pscustomobject]@{
        RootSha256 = $rootHash
        ListDigest = $digest
    }
}

function Assert-BaseShards([string]$Snapshot, [string]$HubRoot, [string]$HubCacheRoot) {
    # Inspect the existing roots as directories before walking the snapshot or
    # reading covered files. A directory link cannot be treated as a file hop.
    Assert-NoReparseExistingAncestors $HubCacheRoot 'base shard gate'
    Assert-NoReparseExistingAncestors $HubRoot 'base shard gate'
    Assert-NoReparseExistingAncestors $Snapshot 'base shard gate'
    $trust = Get-TrustedListDigest $TrustedDigestManifest $BaseRepo $BaseRevision

    if (-not (Test-Path -LiteralPath $ExpectedShardsJson -PathType Leaf)) {
        Write-Error "base shard gate: ExpectedShardsJson not found: $ExpectedShardsJson"
    }
    $listBytes = [IO.File]::ReadAllBytes($ExpectedShardsJson)
    $listHash = Get-Sha256Bytes $listBytes
    if ($listHash -ne $trust.ListDigest) {
        Write-Error "base shard gate: list manifest hash mismatch expected=$($trust.ListDigest) actual=$listHash path=$ExpectedShardsJson"
    }
    $doc = [Text.Encoding]::UTF8.GetString($listBytes) | ConvertFrom-Json
    $key = "$BaseRepo@$BaseRevision"
    $matches = @($doc.PSObject.Properties | Where-Object { $_.Name -ceq $key })
    if ($matches.Count -ne 1) {
        Write-Error "base shard gate: missing key $key in $ExpectedShardsJson"
    }
    $prop = $matches[0]
    $files = @($prop.Value.files)
    if ($files.Count -lt 1) {
        Write-Error "base shard gate: no files listed for $key"
    }

    # Step 3: validate every authenticated list entry (normalized path, 64-hex sha256,
    # non-negative integer size, no case-insensitive duplicates).
    $expectedAll = [ordered]@{}
    $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $expectedSafe = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($f in $files) {
        $rel = [string]$f.path
        Assert-NormalizedRelativePath $rel 'base shard gate'
        if (-not $seen.Add($rel)) {
            Write-Error "base shard gate: duplicate path $rel"
        }
        $sha = [string]$f.sha256
        if ($sha -notmatch '^[0-9a-fA-F]{64}$') {
            Write-Error "base shard gate: invalid sha256 for $rel"
        }
        $sizeRaw = $f.size
        if ($null -eq $sizeRaw) {
            Write-Error "base shard gate: missing size for $rel"
        }
        $sizeDouble = [double]$sizeRaw
        if ($sizeDouble -lt 0 -or $sizeDouble -ne [math]::Floor($sizeDouble)) {
            Write-Error "base shard gate: invalid size for $rel"
        }
        $expectedAll[$rel] = [pscustomobject]@{
            path   = $rel
            sha256 = $sha.ToLower()
            size   = [long]$sizeDouble
        }
        if ($rel.EndsWith('.safetensors', [StringComparison]::OrdinalIgnoreCase)) {
            [void]$expectedSafe.Add($rel)
        }
    }

    # Step 4: full coverage. Walk the snapshot tree without following directory
    # reparse points; the resulting path set must equal the approved list exactly.
    $snapN = Get-NormalPath $Snapshot
    $stack = [System.Collections.Generic.Stack[string]]::new()
    $stack.Push($snapN)
    $presentRel = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        Assert-NoReparseExistingAncestors $dir 'base shard gate'
        foreach ($child in @(Get-ChildItem -LiteralPath $dir -Force)) {
            if ($child.PSIsContainer) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    Write-Error "base shard gate: directory reparse point not permitted at $($child.FullName)"
                }
                $stack.Push($child.FullName)
            } else {
                $rel = Get-RelativePath $Snapshot $child.FullName
                [void]$presentRel.Add($rel)
            }
        }
    }
    $expectedSet = [System.Collections.Generic.HashSet[string]]::new([string[]]$expectedAll.Keys, [StringComparer]::OrdinalIgnoreCase)
    if (-not $expectedSet.SetEquals($presentRel)) {
        $missing = @($expectedSet | Where-Object { -not $presentRel.Contains($_) })
        $extra = @($presentRel | Where-Object { -not $expectedSet.Contains($_) })
        Write-Error ("base shard gate: coverage mismatch missing=" + ($missing -join ',') + " extra=" + ($extra -join ','))
    }

    # Step 5: verify size and two SHA-256 reads of every covered file BEFORE parsing
    # model.safetensors.index.json or running any loader/Docker step.
    $result = [System.Collections.Generic.List[object]]::new()
    $resolvedByRel = [System.Collections.Generic.Dictionary[string, string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($rel in $expectedAll.Keys) {
        $entry = $expectedAll[$rel]
        $relWin = $rel -replace '/', '\'
        $resolved = Resolve-SnapshotFile $Snapshot $relWin $HubRoot $HubCacheRoot
        $item = Get-Item -LiteralPath $resolved -Force
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "base shard gate: $rel resolved to a non-regular file $resolved"
        }
        $actualSize = $item.Length
        if ($actualSize -ne $entry.size) {
            Write-Error "base shard gate: $rel size mismatch expected=$($entry.size) actual=$actualSize"
        }
        $read1 = Get-FileSha256 $resolved
        $read2 = Get-FileSha256 $resolved
        if ($read1 -ne $entry.sha256 -or $read2 -ne $entry.sha256) {
            Write-Error "base shard gate: $rel expected=$($entry.sha256) read1=$read1 read2=$read2"
        }
        $resolvedByRel[$rel] = $resolved
        $result.Add([pscustomobject]@{
                path         = $rel
                size         = $actualSize
                sha256_read1 = $read1
                sha256_read2 = $read2
            })
    }

    # Only now parse model.safetensors.index.json. Its bytes were already verified
    # above as a covered list entry, but ONLY through its resolved (LX-hop-followed)
    # path: the snapshot path itself may be an LX reparse point PowerShell cannot
    # read directly. Read the index from that same resolved path -- never from the
    # snapshot path, and never with Test-Path/Get-Content on the snapshot path --
    # and re-hash the exact bytes parsed so the verified bytes and the parsed bytes
    # are provably identical (check/use gap closed).
    $indexResolved = $null
    if (-not $resolvedByRel.TryGetValue('model.safetensors.index.json', [ref]$indexResolved)) {
        Write-Error "base shard gate: missing model.safetensors.index.json"
    }
    $indexEntry = $expectedAll['model.safetensors.index.json']
    $indexBytes = [IO.File]::ReadAllBytes($indexResolved)
    $indexBytesHash = Get-Sha256Bytes $indexBytes
    if ($indexBytesHash -ne $indexEntry.sha256) {
        Write-Error "base shard gate: model.safetensors.index.json parsed-bytes hash mismatch expected=$($indexEntry.sha256) actual=$indexBytesHash"
    }
    $indexDoc = [Text.Encoding]::UTF8.GetString($indexBytes) | ConvertFrom-Json
    if ($null -eq $indexDoc.weight_map) {
        Write-Error "base shard gate: index has no weight_map"
    }
    $indexed = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($wm in $indexDoc.weight_map.PSObject.Properties) {
        $rel = [string]$wm.Value
        Assert-NormalizedRelativePath $rel 'base shard gate'
        if (-not $expectedSet.Contains($rel)) {
            Write-Error "base shard gate: index references unapproved file $rel"
        }
        [void]$indexed.Add($rel)
    }
    if (-not $indexed.SetEquals($expectedSafe)) {
        $absent = @($expectedSafe | Where-Object { -not $indexed.Contains($_) })
        $unindexed = @($indexed | Where-Object { -not $expectedSafe.Contains($_) })
        Write-Error ("base shard gate: absent=" + ($absent -join ',') + " unindexed=" + ($unindexed -join ','))
    }

    [pscustomobject]@{
        root_sha256   = $trust.RootSha256
        list_sha256   = $listHash
        covered_files = $result.Count
        files         = $result
    }
}

function Copy-BaseSnapshotFile([string]$Source, [string]$Destination) {
    # Create new, independent bytes. Never use a hardlink or a cache symlink.
    $sourceStream = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $destinationStream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $sourceStream.CopyTo($destinationStream) } finally { $destinationStream.Dispose() }
    } finally { $sourceStream.Dispose() }
}

function Get-BaseIntegrityBytes($Approval) {
    $integrity = [ordered]@{
        root_sha256 = $Approval.root_sha256; list_sha256 = $Approval.list_sha256
        covered_files = $Approval.covered_files
        files = @($Approval.files | ForEach-Object { [ordered]@{ path = $_.path; size = $_.size; sha256 = $_.sha256_read1 } })
    }
    [Text.Encoding]::UTF8.GetBytes(($integrity | ConvertTo-Json -Depth 8) + "`n")
}

function New-VerifiedBaseSnapshot([string]$Snapshot, [string]$HubRoot, [string]$HubCacheRoot, $BaseShards, [string]$RunStaging, [string]$OwnershipToken) {
    Assert-NoReparseOnPath (Get-NormalPath $StagingRoot) $RunStaging 'base snapshot copy gate'
    Assert-PhysicalStagingContainment $RunStaging 'base snapshot copy gate'
    Assert-StagingMarkerOwned $RunStaging $OwnershipToken 'base snapshot copy gate'
    # Re-authenticate exact approved list bytes before copying/checking weights.
    # The expected digests come from that list, never from an arbitrary new copy.
    $trust = Get-TrustedListDigest $TrustedDigestManifest $BaseRepo $BaseRevision
    $listBytes = [IO.File]::ReadAllBytes($ExpectedShardsJson)
    $listSha = Get-Sha256Bytes $listBytes
    if ($listSha -ne $trust.ListDigest -or $listSha -ne $BaseShards.list_sha256 -or $trust.RootSha256 -ne $BaseShards.root_sha256) { Write-Error 'base snapshot copy gate: authenticated root/list binding changed' }
    $approvedDoc = [Text.Encoding]::UTF8.GetString($listBytes) | ConvertFrom-Json
    $approvedMatches = @($approvedDoc.PSObject.Properties | Where-Object { $_.Name -ceq "$BaseRepo@$BaseRevision" })
    if ($approvedMatches.Count -ne 1) { Write-Error 'base snapshot copy gate: exact approved model key missing' }
    $approvedEntries = @($approvedMatches[0].Value.files | ForEach-Object {
        [pscustomobject]@{ path = [string]$_.path; size = [long]$_.size; sha256_read1 = ([string]$_.sha256).ToLowerInvariant() }
    })
    if ($approvedEntries.Count -ne $BaseShards.covered_files) { Write-Error 'base snapshot copy gate: approved count changed' }
    $destination = Get-NormalPath (Join-Path $RunStaging 'base-snapshot')
    if (Test-Path -LiteralPath $destination) { Write-Error "base snapshot copy gate: destination already exists: $destination" }
    New-Item -ItemType Directory -Path $destination | Out-Null
    $leases = [System.Collections.Generic.List[object]]::new()
    $copied = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($entry in $approvedEntries) {
            $relative = [string]$entry.path
            Assert-NormalizedRelativePath $relative 'base snapshot copy gate'
            $source = Resolve-SnapshotFile $Snapshot ($relative -replace '/', '\') $HubRoot $HubCacheRoot
            $target = Get-NormalPath (Join-Path $destination ($relative -replace '/', '\'))
            if (-not (Test-PathIsUnder $target $destination) -or $target -eq $destination) { Write-Error 'base snapshot copy gate: destination escapes snapshot' }
            $parent = Split-Path -Parent $target
            Assert-NoReparseExistingAncestors $parent 'base snapshot copy gate'
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            Assert-NoReparseOnPath $destination $parent 'base snapshot copy gate'
            Copy-BaseSnapshotFile $source $target
            Assert-NoReparseOnPath $destination $target 'base snapshot copy gate'
            # Keep read leases until Docker and publication finish. These prevent
            # normal Windows file writes/deletes while allowing Docker reads;
            # a privileged host owner is still outside this integrity boundary.
            $lease = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            $leases.Add($lease)
            $copy1 = Get-FileSha256 $target
            $copy2 = Get-FileSha256 $target
            if ($lease.Length -ne $entry.size -or $copy1 -ne $entry.sha256_read1 -or $copy2 -ne $entry.sha256_read1) {
                Write-Error "base snapshot copy gate: $relative copied bytes mismatch expected=$($entry.sha256_read1) copy1=$copy1 copy2=$copy2"
            }
            $copied.Add([pscustomobject]@{ path = $relative; size = $lease.Length; sha256_read1 = $copy1; sha256_read2 = $copy2 })
        }
        # Full coverage includes config, tokenizer and the authenticated index.
        $actual = [System.Collections.Generic.List[string]]::new()
        $directories = [System.Collections.Generic.Stack[string]]::new()
        $directories.Push($destination)
        while ($directories.Count -gt 0) {
            $directory = $directories.Pop()
            Assert-NoReparseOnPath $destination $directory 'base snapshot copy gate'
            foreach ($child in @(Get-ChildItem -LiteralPath $directory -Force)) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { Write-Error "base snapshot copy gate: reparse point at $($child.FullName)" }
                if ($child.PSIsContainer) { $directories.Push($child.FullName) } else { $actual.Add((Get-RelativePath $destination $child.FullName)) }
            }
        }
        $expected = @($copied | ForEach-Object { $_.path })
        if (@(Compare-Object $expected $actual -CaseSensitive).Count -ne 0) { Write-Error 'base snapshot copy gate: copied coverage mismatch' }
        $integrityPath = Join-Path $RunStaging 'base-integrity.json'
        Assert-NoReparseExistingAncestors $RunStaging 'base snapshot copy gate'
        $integrityBytes = Get-BaseIntegrityBytes ([pscustomobject]@{ root_sha256 = $trust.RootSha256; list_sha256 = $listSha; covered_files = $approvedEntries.Count; files = $approvedEntries })
        $integrityStream = [IO.File]::Open($integrityPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $integrityStream.Write($integrityBytes, 0, $integrityBytes.Length) } finally { $integrityStream.Dispose() }
        Assert-NoReparseOnPath $RunStaging $integrityPath 'base snapshot copy gate'
        $leases.Add([IO.File]::Open($integrityPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read))
        $integritySha = Get-Sha256Bytes $integrityBytes
        if ((Get-FileSha256 $integrityPath) -ne $integritySha -or (Get-FileSha256 $integrityPath) -ne $integritySha) { Write-Error 'base snapshot copy gate: integrity envelope changed' }
        [pscustomobject]@{ path = $destination; container_path = '/base-snapshot'; covered_files = $copied.Count; files = $copied; integrity_path = $integrityPath; integrity_sha256 = $integritySha; leases = $leases }
    } catch {
        foreach ($lease in $leases) { $lease.Dispose() }
        throw
    }
}

function New-StepClock {
    $script:StepStartUtc = @{}
    [ordered]@{}
}

function Start-Step([System.Collections.IDictionary]$Steps, [string]$Name) {
    $t = [datetime]::UtcNow
    $script:StepStartUtc[$Name] = $t
    $Steps[$Name] = [ordered]@{ start = $t.ToString('yyyy-MM-ddTHH:mm:ssZ'); end = $null; durationSeconds = $null }
}

function End-Step([System.Collections.IDictionary]$Steps, [string]$Name) {
    if (-not $Steps.Contains($Name)) {
        Write-Error "step clock: unknown step $Name"
    }
    $tEnd = [datetime]::UtcNow
    $tStart = $script:StepStartUtc[$Name]
    $Steps[$Name].end = $tEnd.ToString('yyyy-MM-ddTHH:mm:ssZ')
    $Steps[$Name].durationSeconds = [double]($tEnd - $tStart).TotalSeconds
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
    if (Test-Path -LiteralPath $ReportPath) {
        Write-Error "report: output already exists: $ReportPath"
    }
    $report = [ordered]@{
        name                  = $Name
        checkpoint            = $CheckpointPath
        checkpoint_index      = $Index
        base_repo             = $BaseRepo
        base_revision         = $BaseRevision
        adapter_hashes        = $AdapterHashes
        base_trust            = [ordered]@{
            root_sha256   = $BaseShards.root_sha256
            list_sha256   = $BaseShards.list_sha256
            covered_files = $BaseShards.covered_files
        }
        base_shards           = @($BaseShards.files)
        base_consumed         = $BaseShards.consumed
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
    $claimed = $false
    $fs = $null
    try {
        try {
            $fs = [System.IO.File]::Open($ReportPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        } catch {
            Write-Error "report: output already exists: $ReportPath"
        }
        $claimed = $true
        $payload = [Text.UTF8Encoding]::new($false).GetBytes($json)
        $fs.Write($payload, 0, $payload.Length)
        $fs.Dispose()
        $fs = $null
    } catch {
        if ($claimed) {
            if ($null -ne $fs) {
                try { $fs.Dispose() } catch { }
            }
            if (Test-Path -LiteralPath $ReportPath) {
                Remove-Item -LiteralPath $ReportPath -Force
            }
        }
        throw
    }
}

function Publish-Gguf([string]$Source, [string]$Dest) {
    $destDir = Split-Path -Parent $Dest
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Dest) {
        Write-Error "publish: output already exists: $Dest"
    }
    $bytes = (Get-Item -LiteralPath $Source -Force).Length
    Assert-StorageGuard 'publish' $bytes | Out-Null
    $claimed = $false
    $fs = $null
    try {
        try {
            $fs = [System.IO.File]::Open($Dest, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        } catch {
            Write-Error "publish: output already exists: $Dest"
        }
        $claimed = $true
        $srcFs = [System.IO.File]::Open($Source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {
            $srcFs.CopyTo($fs)
        } finally {
            $srcFs.Dispose()
        }
        $fs.Dispose()
        $fs = $null
        $srcHash = Get-FileSha256 $Source
        $copy1 = Get-FileSha256 $Dest
        $copy2 = Get-FileSha256 $Dest
        if ($srcHash -ne $copy1 -or $copy1 -ne $copy2) {
            Write-Error "publish: hash mismatch source=$srcHash copy1=$copy1 copy2=$copy2"
        }
        $out1 = Get-FileSha256 $Dest
        $out2 = Get-FileSha256 $Dest
        if ($out1 -ne $out2) {
            Write-Error "publish: two reads of $Dest differ read1=$out1 read2=$out2"
        }
        [pscustomobject]@{
            path   = $Dest
            size   = (Get-Item -LiteralPath $Dest -Force).Length
            sha1   = $out1
            sha2   = $out2
        }
    } catch {
        if ($claimed) {
            if ($null -ne $fs) {
                try { $fs.Dispose() } catch { }
            }
            if (Test-Path -LiteralPath $Dest) {
                Remove-Item -LiteralPath $Dest -Force
            }
        }
        throw
    }
}

function Invoke-MergeDocker([string]$BaseSnapshotHost, [string]$IntegrityHost, [string]$IntegritySha256, [string]$AdapterHost, [string]$WorkHost) {
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
        '-e', "HL_BASE_GATE_SHA256=$IntegritySha256",
        '-v', "${BaseSnapshotHost}:/base-snapshot:ro",
        '-v', "${IntegrityHost}:/base-integrity.json:ro",
        '-v', "${AdapterHost}:/adapter:ro",
        '-v', "${WorkHost}:/work",
        '--entrypoint', 'bash',
        $Image,
        '-lc', (ConvertTo-BashCommand $bash)
    )
    Invoke-DockerStep $dockerArgs $DockerTimeoutSec 'merge'
}

function Invoke-ConvertDocker([string]$WorkHost, [string]$Bf16Name) {
    $bash = @(
        'set -euo pipefail',
        "python /opt/unsloth/llama.cpp/convert_hf_to_gguf.py /work/merged --outfile /work/$Bf16Name --outtype bf16"
    ) -join "`n"
    $dockerArgs = @(
        'run', '--rm', '--network', 'none',
        '-e', 'HF_HUB_OFFLINE=1',
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
if ($RunId -and $RunId -cnotmatch '^[a-z0-9][a-z0-9-]{0,80}$') {
    Write-Error "parameter RunId: must match ^[a-z0-9][a-z0-9-]{0,80}$, got '$RunId'"
}
if ($RunId -and $Checkpoint.Count -gt 1) {
    Write-Error "parameter RunId: cannot be used with more than one checkpoint"
}
if (-not (($BaseRepo -ceq 'Qwen/Qwen3-14B' -and $BaseRevision -ceq '40c069824f4251a91eefaf281ebe4c544efd3e18') -or
          ($BaseRepo -ceq 'Qwen/Qwen3-8B' -and $BaseRevision -ceq 'b968826d9c46dd6066d109eabc6255188de91218'))) {
    Write-Error "parameter BaseRepo/BaseRevision: only the pinned 14B or 8B student export is supported, got '$BaseRepo@$BaseRevision'"
}

$utcStamp = Get-UtcStamp
$snapshot = Get-SnapshotPath $BaseRepo $BaseRevision
$hubCacheRoot = Join-Path $CHotRoot 'hf\hub'
$hubRoot = Join-Path $hubCacheRoot (Get-HubFolderName $BaseRepo)
$cSnap = Get-StorageSnapshot
$eSnap = Get-EStorageSnapshot
$plans = [System.Collections.Generic.List[object]]::new()

for ($n = 1; $n -le $Checkpoint.Count; $n++) {
    $ckpt = $Checkpoint[$n - 1]
    $runLeaf = if ($RunId) { $RunId } else { "$Name-ckpt$n-$utcStamp" }
    $runFolder = Join-Path (Get-NormalPath $StagingRoot) $runLeaf
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
    $ownershipToken = $null
    $frozenBase = $null
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
        $baseShards = Assert-BaseShards $snapshot $hubRoot $hubCacheRoot
        End-Step $steps 'base_shards'
        $baseCopyBytes = [long]0
        foreach ($entry in $baseShards.files) {
            if ([long]$entry.size -gt ([long]::MaxValue - $baseCopyBytes)) { Write-Error 'base snapshot copy gate: total byte count exceeds Int64' }
            $baseCopyBytes += [long]$entry.size
        }
        $envelopeBytes = Get-BaseIntegrityBytes $baseShards
        if ($envelopeBytes.Length -gt ([long]::MaxValue - $baseCopyBytes)) { Write-Error 'base snapshot copy gate: total byte count exceeds Int64' }
        $baseCopyBytes += $envelopeBytes.Length
        Assert-EFree 'base snapshot copy' $baseCopyBytes | Out-Null
        if (([IO.Path]::GetPathRoot((Get-NormalPath $StagingRoot))).StartsWith('C:', [StringComparison]::OrdinalIgnoreCase)) {
            Assert-StorageGuard 'base snapshot copy' 0 $baseCopyBytes | Out-Null
        }

        Start-Step $steps 'staging'
        Assert-NoReparseExistingAncestors (Get-NormalPath $CHotRoot) 'export staging'
        Assert-NoReparseExistingAncestors $runStaging 'export staging'
        Assert-NoReparseExistingAncestors (Get-NormalPath $StagingRoot) 'export staging'
        Assert-PhysicalStagingContainment '' 'export staging'
        if (Test-Path -LiteralPath $runStaging) {
            Write-Error "export staging: run folder already exists: $runStaging"
        }
        New-Item -ItemType Directory -Path $runStaging | Out-Null
        $keepStaging = $runStaging
        $ownershipToken = [guid]::NewGuid().ToString('d')
        $runLeaf = if ($RunId) { $RunId } else { "$Name-ckpt$n-$utcStamp" }
        $marker = [ordered]@{
            runId           = $runLeaf
            createdUtc      = Get-UtcNowString
            ownershipToken  = $ownershipToken
        }
        $markerPath = Join-Path $runStaging '.hl-export-staging'
        $markerJson = ($marker | ConvertTo-Json) + "`n"
        $markerBytes = [Text.UTF8Encoding]::new($false).GetBytes($markerJson)
        try {
            $mfs = [System.IO.File]::Open($markerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try {
                $mfs.Write($markerBytes, 0, $markerBytes.Length)
            } finally {
                $mfs.Dispose()
            }
        } catch {
            Write-Error "export staging: could not create .hl-export-staging marker in $runStaging"
        }
        Assert-PhysicalStagingContainment $runStaging 'export staging'
        Assert-NoReparseOnPath (Get-NormalPath $StagingRoot) $runStaging 'export staging'
        End-Step $steps 'staging'

        Start-Step $steps 'base_copy'
        $frozenBase = New-VerifiedBaseSnapshot $snapshot $hubRoot $hubCacheRoot $baseShards $runStaging $ownershipToken
        $baseShards | Add-Member -NotePropertyName consumed -NotePropertyValue ([ordered]@{
            host_path = $frozenBase.path; container_path = $frozenBase.container_path
            covered_files = $frozenBase.covered_files; files = @($frozenBase.files)
            root_sha256 = $baseShards.root_sha256; list_sha256 = $baseShards.list_sha256
            integrity_sha256 = $frozenBase.integrity_sha256
        })
        $workHost = Get-NormalPath (Join-Path $runStaging 'work')
        New-Item -ItemType Directory -Path $workHost | Out-Null
        Assert-NoReparseOnPath $runStaging $workHost 'export staging'
        End-Step $steps 'base_copy'

        Start-Step $steps 'merge'
        Invoke-MergeDocker $frozenBase.path $frozenBase.integrity_path $frozenBase.integrity_sha256 $adapterHost $workHost | Out-Null
        End-Step $steps 'merge'

        Start-Step $steps 'convert'
        Invoke-ConvertDocker $workHost $bf16Name | Out-Null
        $bf16Host = Join-Path $workHost $bf16Name
        if (-not (Test-Path -LiteralPath $bf16Host -PathType Leaf)) {
            Write-Error "convert: expected bf16 GGUF missing: $bf16Host"
        }
        End-Step $steps 'convert'

        Start-Step $steps 'delete_merged'
        $mergedHost = Join-Path $workHost 'merged'
        Remove-StagedTree $mergedHost $ownershipToken
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

        foreach ($lease in $frozenBase.leases) { $lease.Dispose() }
        $frozenBase = $null
        Remove-StagedTree $runStaging $ownershipToken
        $keepStaging = $null
    } catch {
        if ($keepStaging) {
            Write-Output "staging kept for inspection: $keepStaging"
        }
        throw
    } finally {
        if ($null -ne $frozenBase) { foreach ($lease in $frozenBase.leases) { $lease.Dispose() } }
    }
}
