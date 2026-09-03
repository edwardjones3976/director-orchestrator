# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
param(
    [string] $Destination = (Join-Path $HOME '.local\bin'),
    [switch] $SkipPathUpdate
)

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'pplx.ps1') -Destination $destinationPath -Force
Copy-Item -LiteralPath (Join-Path $source 'pplx.cmd') -Destination $destinationPath -Force

if (-not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -notcontains $destinationPath) {
        $updated = (@($entries) + $destinationPath) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
    }
}
Write-Output $destinationPath
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | END]]
