# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
[CmdletBinding()]
param(
    [switch]$ApiOnly,
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [switch]$SeedProviderCredentialsFromPrimaryEnv,
    [string]$ProviderCredentialSourceEnvPath = "",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$arguments = @{
    ProfileName = "long-table"
    TimeoutSeconds = $TimeoutSeconds
}
if ($ApiOnly) { $arguments.ApiOnly = $true }
if ($EnablePaidRuns) { $arguments.EnablePaidRuns = $true }
if ($SkipProviderChecks) { $arguments.SkipProviderChecks = $true }
if ($SeedProviderCredentialsFromPrimaryEnv) {
    $arguments.SeedProviderCredentialsFromPrimaryEnv = $true
}
if (-not [string]::IsNullOrWhiteSpace($ProviderCredentialSourceEnvPath)) {
    $arguments.ProviderCredentialSourceEnvPath = $ProviderCredentialSourceEnvPath
}

& (Join-Path $PSScriptRoot "windows-isolated-live-validation.ps1") @arguments
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | END]]
