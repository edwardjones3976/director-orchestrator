# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | BEGIN]]
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Provider,

    [Parameter(ValueFromRemainingArguments = $true)]
    [AllowEmptyString()]
    [string[]] $ProviderArguments
)

$ErrorActionPreference = 'Stop'

function Resolve-NormalizedPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Stop-WithError {
    param(
        [Parameter(Mandatory = $true)][string] $Message,
        [int] $Code = 2
    )

    [Console]::Error.WriteLine("agent: $Message")
    exit $Code
}

$providerKey = $Provider.Trim().ToLowerInvariant()
$providerExecutable = switch ($providerKey) {
    'cursor' { 'cursor-agent'; break }
    'grok' { 'grok'; break }
    default { Stop-WithError "unknown provider '$Provider'; expected cursor or grok" }
}

$workingDirectory = Resolve-NormalizedPath -Path (Get-Location).ProviderPath
$windowsDirectory = Resolve-NormalizedPath -Path $env:WINDIR
$systemDirectory = Resolve-NormalizedPath -Path ([Environment]::SystemDirectory)
$userDirectory = Resolve-NormalizedPath -Path ([Environment]::GetFolderPath('UserProfile'))

if (
    $workingDirectory.Equals($windowsDirectory, [StringComparison]::OrdinalIgnoreCase) -or
    $workingDirectory.Equals($systemDirectory, [StringComparison]::OrdinalIgnoreCase) -or
    $workingDirectory.Equals($userDirectory, [StringComparison]::OrdinalIgnoreCase)
) {
    Stop-WithError "refusing generic working directory '$workingDirectory'"
}

$gitCommand = Get-Command -Name 'git' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $gitCommand) {
    Stop-WithError 'git executable not found; a verified Git worktree is required'
}

$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$insideWorktree = & $gitCommand.Source rev-parse --is-inside-work-tree 2>$null
$gitStatus = $LASTEXITCODE
$ErrorActionPreference = $savedErrorActionPreference
if ($gitStatus -ne 0 -or ($insideWorktree | Select-Object -First 1) -ne 'true') {
    Stop-WithError "working directory '$workingDirectory' is not a Git worktree"
}

$ErrorActionPreference = 'Continue'
$worktreeRoot = & $gitCommand.Source rev-parse --show-toplevel 2>$null
$gitStatus = $LASTEXITCODE
$ErrorActionPreference = $savedErrorActionPreference
if ($gitStatus -ne 0 -or [string]::IsNullOrWhiteSpace(($worktreeRoot | Select-Object -First 1))) {
    Stop-WithError 'Git worktree root could not be verified'
}

$providerCommand = Get-Command -Name $providerExecutable -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $providerCommand) {
    Stop-WithError "provider executable '$providerExecutable' not found" 127
}

# The call operator with an argument array preserves boundaries and never evaluates child output.
& $providerCommand.Source @ProviderArguments
$childExitCode = $LASTEXITCODE
if ($null -eq $childExitCode) {
    $childExitCode = if ($?) { 0 } else { 1 }
}
exit $childExitCode
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | END]]
