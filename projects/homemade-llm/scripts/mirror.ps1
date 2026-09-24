# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | BEGIN]]
# Homemade LLM nightly mirror (M0.7): robocopy C:\llm -> G:\llm-mirror, never copying secrets.
#   .\mirror.ps1            mirror now (/MIR also deletes mirror files that no longer exist in the source)
#   .\mirror.ps1 -List      show what would change; copies and deletes nothing
# Registering the nightly Scheduled Task is a separate owner step (see workstream-plan.md M0.7).
# Exit 0 = mirrored (robocopy codes 0-7); 1 = refused or robocopy failed (8+).
[CmdletBinding()]
param(
    [string]$Source = 'C:\llm',
    [string]$Dest = 'G:\llm-mirror',
    [switch]$List
)
$ErrorActionPreference = 'Stop'

$src = [IO.Path]::GetFullPath($Source).TrimEnd('\')
$dst = [IO.Path]::GetFullPath($Dest).TrimEnd('\')
# /MIR deletes at the destination, so refuse anything that could reach data outside the mirror.
if (-not (Test-Path $src -PathType Container)) { Write-Error "source $src is not a directory" }
if ($dst -match '^[A-Za-z]:$') { Write-Error "refusing a drive root as the mirror destination: $dst" }
# The mirror is local: a UNC destination (including a share root) is refused outright.
if ($dst.StartsWith('\\') -or $src.StartsWith('\\')) { Write-Error "refusing a UNC path: $src -> $dst" }
# String checks cannot see through junctions or symlinks, so refuse any link on either path or its parents.
foreach ($p in $src, $dst) {
    $cur = $p
    while ($cur) {
        if ((Test-Path -LiteralPath $cur) -and ((Get-Item -LiteralPath $cur -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "refusing a path through a junction or symlink: $cur"
        }
        $cur = Split-Path $cur -Parent
    }
}
if ($dst -eq $src -or $dst.StartsWith("$src\", 'OrdinalIgnoreCase') -or $src.StartsWith("$dst\", 'OrdinalIgnoreCase')) {
    Write-Error "source and destination overlap: $src -> $dst"
}

$logDir = Join-Path $src 'reports'
$rcArgs = @($src, $dst, '/MIR', '/XD', (Join-Path $src 'secrets'), '/R:2', '/W:5', '/NP', '/NDL', '/FFT')
if ($List) { $rcArgs += '/L' }
if (Test-Path $logDir -PathType Container) {
    $rcArgs += "/UNILOG:$(Join-Path $logDir ('mirror-{0}.log' -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')))"
    $rcArgs += '/TEE'
}
& robocopy.exe @rcArgs | Out-Host
$code = $LASTEXITCODE
# robocopy: 0-7 are success variants (bit flags for copied/extra/mismatched); 8+ means a failure.
Write-Output "mirror: robocopy exit $code$(if ($List) { ' (list only)' })"
exit ([int]($code -ge 8))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | END]]
