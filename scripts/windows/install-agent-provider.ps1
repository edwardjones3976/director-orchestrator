# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | BEGIN]]
[CmdletBinding()]
param(
    [string] $Destination = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.director-orchestrator\bin'),
    [switch] $SkipPathUpdate
)

$ErrorActionPreference = 'Stop'
$sourceDirectory = $PSScriptRoot
$dispatcherSource = Join-Path $sourceDirectory 'agent-provider.ps1'
$shimSource = Join-Path $sourceDirectory 'agent.cmd'
$managedMarker = '[[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | BEGIN]]'

if (-not (Test-Path -LiteralPath $dispatcherSource -PathType Leaf) -or
    -not (Test-Path -LiteralPath $shimSource -PathType Leaf)) {
    throw 'Dispatcher installation sources are incomplete.'
}

$null = New-Item -ItemType Directory -Path $Destination -Force
$resolvedDestination = [System.IO.Path]::GetFullPath($Destination)

function Install-ManagedFile {
    param(
        [Parameter(Mandatory = $true)][string] $Source,
        [Parameter(Mandatory = $true)][string] $Target
    )

    $sourceText = [System.IO.File]::ReadAllText($Source)
    if (Test-Path -LiteralPath $Target) {
        $targetText = [System.IO.File]::ReadAllText($Target)
        if ($targetText -eq $sourceText) {
            return
        }
        if (-not $targetText.Contains($managedMarker)) {
            throw "Refusing to overwrite existing unmanaged executable '$Target'."
        }
    }
    [System.IO.File]::WriteAllText($Target, $sourceText, [System.Text.UTF8Encoding]::new($false))
}

Install-ManagedFile -Source $dispatcherSource -Target (Join-Path $resolvedDestination 'agent.ps1')
Install-ManagedFile -Source $shimSource -Target (Join-Path $resolvedDestination 'agent.cmd')

if (-not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $resolvedDestination.TrimEnd('\') })) {
        $newPath = (@($resolvedDestination) + $entries) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    }
}

Write-Output $resolvedDestination
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | END]]
