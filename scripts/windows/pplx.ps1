# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PplxArguments
)

$ErrorActionPreference = 'Stop'
& wsl.exe --distribution Ubuntu --exec /home/edward/.local/bin/pplx @PplxArguments
exit $LASTEXITCODE
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
