# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PplxArguments
)

$ErrorActionPreference = 'Stop'
# The Linux entry point is resolved through the WSL distribution's PATH. A
# deployment may override the distribution or the executable with a bounded
# configuration value; no personal home directory is checked in.
$distribution = if ($env:DIRECTOR_PPLX_WSL_DISTRIBUTION) { $env:DIRECTOR_PPLX_WSL_DISTRIBUTION } else { 'Ubuntu' }
$executable = if ($env:DIRECTOR_PPLX_WSL_EXEC) { $env:DIRECTOR_PPLX_WSL_EXEC } else { 'pplx' }
& wsl.exe --distribution $distribution --exec $executable @PplxArguments
exit $LASTEXITCODE
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
