# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:35 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 12:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | BEGIN]]
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

    if ([regex]::IsMatch($RemoteUrl, '[^\x00-\x7F]')) { return $null }
    $value = $RemoteUrl.Trim()
    $match = [regex]::Match(
        $value,
        '^(?:https://github\.com/|git@github\.com:)(?<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/(?<repository>[A-Za-z0-9._-]+?)(?:\.git)?/?$',
        ([Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
            [Text.RegularExpressions.RegexOptions]::CultureInvariant)
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
    if ($LASTEXITCODE -ne 0) {
        throw "Snapshot source starting ref could not be verified on the selected remote."
    }
    # ls-remote tail-matches its pattern client-side, so a branch such as
    # decoy/refs/heads/main also answers and sorts first. Select the exact ref
    # instead of trusting the row count.
    $matchingRows = @($remoteRows | Where-Object {
        $fields = @([string]$_ -split '\s+')
        $fields.Count -ge 2 -and $fields[1] -ceq $remoteRef
    })
    if ($matchingRows.Count -ne 1) {
        throw "Snapshot source starting ref could not be verified on the selected remote."
    }
    $remoteFields = @($matchingRows[0] -split '\s+')
    if ($remoteFields.Count -lt 2 -or
        $remoteFields[0] -cne $ExpectedCommit -or
        $remoteFields[1] -cne $remoteRef) {
        throw "Snapshot source starting ref does not match the frozen authority."
    }
    # -c sets these in the new repository before anything is checked out, so the
    # working tree is written with the same line endings as the blobs and stays
    # that way on any later checkout.
    #
    # Without it, a host with core.autocrlf=true writes a CRLF working tree over
    # LF blobs. That is invisible here, because the host's own git applies the
    # same conversion when it compares -- so the cleanliness check below passes
    # and the snapshot certifies as good. The consumer is the Linux orchestrator
    # container, which reads the same files through a read-only bind with no such
    # conversion, sees every line as modified, and refuses the authority with
    # "Default primary repository authority must be clean". Certifying a snapshot
    # the consumer rejects is worse than failing to build one.
    & git clone -c core.autocrlf=false -c core.eol=lf --no-hardlinks --no-tags --branch $StartingRef --single-branch -- $sourceRemoteUrl $targetPath
    if ($LASTEXITCODE -ne 0) { throw "Snapshot clone failed." }
    & git -C $targetPath remote set-url origin $sourceRemoteUrl
    if ($LASTEXITCODE -ne 0) { throw "Snapshot origin binding failed." }
}

if (-not (Test-Path -LiteralPath (Join-Path $targetPath ".git"))) {
    throw "Snapshot target is not a Git checkout."
}
# Verify must not execute target-local clean/process filters or fsmonitor hooks.
# The source checkout's Git policy does not cover this separate repository.
$configNames = @(& git -C $targetPath config --name-only --list)
if ($LASTEXITCODE -ne 0) { throw "Snapshot Git configuration could not be inspected." }
if (@($configNames | Where-Object { $_ -match '^filter\..*\.(clean|smudge|process)$' }).Count -ne 0) {
    throw "Snapshot executable Git filters are unsupported."
}
if (Test-Path -LiteralPath (Join-Path $targetPath ".gitmodules")) {
    throw "Snapshot submodules are unsupported."
}
$dirty = @(& git -C $targetPath -c core.fsmonitor=false -c core.hooksPath= -c core.untrackedCache=false status --porcelain=v2 --untracked-files=all)
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
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:27 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 12:24 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:35 UTC | END]]
