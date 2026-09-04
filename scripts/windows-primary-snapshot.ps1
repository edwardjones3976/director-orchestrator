# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("Verify", "Create")][string]$Action,
    [Parameter(Mandatory = $true)][string]$Source,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')][string]$SourceRemoteName = "origin",
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$ExpectedRepository,
    [Parameter(Mandatory = $true)][string]$StartingRef,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedTree
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-GitHubRepositoryFromRemoteUrl {
    param([Parameter(Mandatory = $true)][string]$RemoteUrl)

    $match = [regex]::Match(
        $RemoteUrl.Trim(),
        '^(?:https://github\.com/|git@github\.com:)(?<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/(?<repository>[A-Za-z0-9._-]+?)(?:\.git)?/?$',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $match.Success) { return $null }
    return ("{0}/{1}" -f $match.Groups["owner"].Value, $match.Groups["repository"].Value).ToLowerInvariant()
}

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$targetPath = [IO.Path]::GetFullPath($Target)
$sourceRemoteUrl = [string](@(& git -C $sourcePath remote get-url $SourceRemoteName) | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceRemoteUrl)) {
    throw "Snapshot source has no verifiable selected remote."
}
$normalizedSourceRepository = Get-GitHubRepositoryFromRemoteUrl -RemoteUrl $sourceRemoteUrl
if ($normalizedSourceRepository -cne $ExpectedRepository.Trim('/').ToLowerInvariant()) {
    throw "Snapshot source remote does not match the repository allowlist."
}

if ($Action -eq "Create") {
    if (Test-Path -LiteralPath $targetPath) {
        throw "Snapshot target already exists; creation is non-destructive and refuses replacement."
    }
    $parent = Split-Path -Parent $targetPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Snapshot parent directory must already exist."
    }
    $remoteRef = "refs/heads/$StartingRef"
    $remoteRows = @(& git ls-remote --exit-code --refs -- $sourceRemoteUrl $remoteRef)
    if ($LASTEXITCODE -ne 0 -or $remoteRows.Count -ne 1) {
        throw "Snapshot source starting ref could not be verified on the selected remote."
    }
    $remoteFields = @($remoteRows[0] -split '\s+')
    if ($remoteFields.Count -lt 2 -or
        $remoteFields[0] -cne $ExpectedCommit -or
        $remoteFields[1] -cne $remoteRef) {
        throw "Snapshot source starting ref does not match the frozen authority."
    }
    & git clone --no-hardlinks --no-tags --branch $StartingRef --single-branch -- $sourceRemoteUrl $targetPath
    if ($LASTEXITCODE -ne 0) { throw "Snapshot clone failed." }
    & git -C $targetPath remote set-url origin $sourceRemoteUrl
    if ($LASTEXITCODE -ne 0) { throw "Snapshot origin binding failed." }
}

if (-not (Test-Path -LiteralPath (Join-Path $targetPath ".git"))) {
    throw "Snapshot target is not a Git checkout."
}
$dirty = @(& git -C $targetPath status --porcelain=v2 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) { throw "Snapshot target is not clean." }
$head = [string](@(& git -C $targetPath rev-parse --verify 'HEAD^{commit}') | Select-Object -Last 1)
$tree = [string](@(& git -C $targetPath rev-parse --verify 'HEAD^{tree}') | Select-Object -Last 1)
$refCommit = [string](@(& git -C $targetPath rev-parse --verify "$StartingRef^{commit}") | Select-Object -Last 1)
if ($head -cne $ExpectedCommit -or $refCommit -cne $ExpectedCommit -or $tree -cne $ExpectedTree) {
    throw "Snapshot commit, tree, or starting ref does not match the frozen authority."
}
$origin = [string](@(& git -C $targetPath remote get-url origin) | Select-Object -Last 1)
$normalized = Get-GitHubRepositoryFromRemoteUrl -RemoteUrl $origin
if ($normalized -cne $ExpectedRepository.Trim('/').ToLowerInvariant()) {
    throw "Snapshot origin does not match the repository allowlist."
}
Write-Host "Snapshot verification passed at the exact requested commit and tree."
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
