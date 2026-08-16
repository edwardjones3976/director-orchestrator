[CmdletBinding()]
param(
    [switch]$ApiOnly,
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [switch]$SeedProviderCredentialsFromPrimaryEnv,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$arguments = @{
    ProfileName = "edge-oracle"
    TimeoutSeconds = $TimeoutSeconds
}
if ($ApiOnly) { $arguments.ApiOnly = $true }
if ($EnablePaidRuns) { $arguments.EnablePaidRuns = $true }
if ($SkipProviderChecks) { $arguments.SkipProviderChecks = $true }
if ($SeedProviderCredentialsFromPrimaryEnv) {
    $arguments.SeedProviderCredentialsFromPrimaryEnv = $true
}

& (Join-Path $PSScriptRoot "windows-isolated-live-validation.ps1") @arguments
