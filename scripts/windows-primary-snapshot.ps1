# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("Verify", "Create")][string]$Action,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$ExpectedRepository,
    [Parameter(Mandatory = $true)][string]$StartingRef,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedTree
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$targetPath = [IO.Path]::GetFullPath($Target)
$sourceOrigin = [string](@(& git -C $sourcePath remote get-url origin) | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceOrigin)) {
    throw "Snapshot source has no verifiable origin."
}
$normalizedSourceOrigin = $sourceOrigin -replace '^git@github.com:', '' -replace '^https://github.com/', '' -replace '\.git$', ''
if ($normalizedSourceOrigin.Trim('/') -cne $ExpectedRepository.Trim('/')) {
    throw "Snapshot source origin does not match the repository allowlist."
}

if ($Action -eq "Create") {
    if (Test-Path -LiteralPath $targetPath) {
        throw "Snapshot target already exists; creation is non-destructive and refuses replacement."
    }
    $parent = Split-Path -Parent $targetPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Snapshot parent directory must already exist."
    }
    & git clone --no-hardlinks --no-tags --branch $StartingRef --single-branch -- $sourcePath $targetPath
    if ($LASTEXITCODE -ne 0) { throw "Snapshot clone failed." }
    & git -C $targetPath remote set-url origin $sourceOrigin
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
$normalized = $origin -replace '^git@github.com:', '' -replace '^https://github.com/', '' -replace '\.git$', ''
if ($normalized.Trim('/') -cne $ExpectedRepository.Trim('/')) {
    throw "Snapshot origin does not match the repository allowlist."
}
Write-Host "Snapshot verification passed at the exact requested commit and tree."
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | END]]
