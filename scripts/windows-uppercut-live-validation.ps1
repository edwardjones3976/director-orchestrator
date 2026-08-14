[CmdletBinding()]
param(
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env" -PathType Leaf)) {
    throw "Missing .env in the director-orchestrator repository root. Run scripts/windows-bootstrap.ps1 first."
}

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        $values[$name] = $value
    }
    return $values
}

function Set-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $escapedName = [Regex]::Escape($Name)
    $escapedValue = $Value.Replace('$', '$$')
    $pattern = "(?m)^${escapedName}=.*$"
    if ([Regex]::IsMatch($Text, $pattern)) {
        return [Regex]::Replace($Text, $pattern, "${Name}=${escapedValue}")
    }
    return $Text.TrimEnd() + [Environment]::NewLine + "${Name}=${Value}" + [Environment]::NewLine
}

function Assert-Present {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    $missing = @($Names | Where-Object {
        -not $Values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$Values[$_])
    })
    if ($missing.Count -gt 0) {
        throw "Missing required .env entries: $($missing -join ', '). No values were printed."
    }
}

function Wait-DirectorHealth {
    param([int]$Timeout)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Timeout)
    do {
        try {
            return Invoke-RestMethod -Uri "http://127.0.0.1:8080/health" -Method Get -TimeoutSec 5
        }
        catch {
            Start-Sleep -Seconds 3
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Director health endpoint did not become ready within ${Timeout}s."
}

function Assert-Ready {
    param([Parameter(Mandatory = $true)]$Health)

    $providerFailures = @()
    foreach ($name in @("openai_director", "claude", "cursor")) {
        if (-not [bool]$Health.providers.$name) {
            $providerFailures += $name
        }
    }
    $profileFailures = @()
    foreach ($name in @("projects_configured", "repositories_configured", "starting_refs_configured", "claude_callback_configured")) {
        if (-not [bool]$Health.execution_profile.$name) {
            $profileFailures += $name
        }
    }
    if ($providerFailures.Count -gt 0 -or $profileFailures.Count -gt 0) {
        throw "Readiness failed. Providers: $($providerFailures -join ', '); profile: $($profileFailures -join ', ')."
    }
}

$envText = Get-Content ".env" -Raw
$profile = [ordered]@{
    APP_ENV = "project-uppercut"
    PROJECT_ALLOWLIST = "project-uppercut"
    REPOSITORY_ALLOWLIST = "edwardjones3976/Project-Uppercut"
    ALLOWED_STARTING_REFS = "main"
    SLACK_DIRECTOR_CHANNEL_ID = "C0BP62MBFNK"
    PRIMARY_TEAM = "openai,claude,cursor"
    PRIMARY_WORKHORSE = "claude"
    USAGE_CHECK_PROVIDERS = "openai,claude,cursor"
    USAGE_CHECK_INTERVAL_MINUTES = "360"
}
foreach ($entry in $profile.GetEnumerator()) {
    $envText = Set-DotEnvValue -Text $envText -Name $entry.Key -Value $entry.Value
}

if ($EnablePaidRuns) {
    $envText = Set-DotEnvValue -Text $envText -Name "ALLOW_PAID_RUNS" -Value "true"
    $envText = Set-DotEnvValue -Text $envText -Name "USAGE_CHECK_ENABLED" -Value "true"
}
else {
    $envText = Set-DotEnvValue -Text $envText -Name "ALLOW_PAID_RUNS" -Value "false"
    $envText = Set-DotEnvValue -Text $envText -Name "USAGE_CHECK_ENABLED" -Value "false"
}

Set-Content ".env" $envText -Encoding UTF8
$envText = $null

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe ".env" "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to restrict .env permissions."
}

$values = Read-DotEnv -Path ".env"
Assert-Present -Values $values -Names @(
    "ORCHESTRATOR_SHARED_SECRET",
    "SLACK_APP_TOKEN",
    "SLACK_BOT_TOKEN",
    "OPENAI_API_KEY",
    "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN",
    "CLAUDE_CALLBACK_SECRET",
    "CURSOR_API_KEY"
)

Write-Host "Uppercut profile applied; required credentials are present (values not displayed)."
docker compose --env-file .env config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed." }

docker compose --env-file .env up -d --build --remove-orphans
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the Director services." }

$health = Wait-DirectorHealth -Timeout ([Math]::Min($TimeoutSeconds, 180))
Assert-Ready -Health $health
Write-Host "Director health: ready; OpenAI, Claude, Cursor, and the Uppercut execution profile are configured."

if (-not $EnablePaidRuns) {
    Write-Host "Paid execution remains disabled. Re-run with -EnablePaidRuns after provider-side spending limits are set."
    exit 0
}

if ($SkipProviderChecks) {
    Write-Host "Paid execution and scheduled checks are enabled; the immediate provider check was skipped."
    exit 0
}

$startedAt = [DateTimeOffset]::UtcNow
$headers = @{ Authorization = "Bearer $($values['ORCHESTRATOR_SHARED_SECRET'])" }
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/usage-checks/run" -Method Post -Headers $headers -TimeoutSec 30 | Out-Null
Write-Host "Bounded provider check accepted. Waiting for OpenAI, Claude, and Cursor results..."

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$records = @()
do {
    Start-Sleep -Seconds 5
    $latest = @(Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/usage-checks/latest" -Method Get -Headers $headers -TimeoutSec 30)
    $records = @($latest | Where-Object {
        $_.provider -in @("openai", "claude", "cursor") -and
        [DateTimeOffset]::Parse($_.checked_at) -ge $startedAt
    })
} while ($records.Count -lt 3 -and [DateTimeOffset]::UtcNow -lt $deadline)

if ($records.Count -lt 3) {
    throw "Timed out waiting for all three provider checks. Inspect: docker compose logs --tail 200 orchestrator"
}

$summary = $records | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at
$summary | Format-Table -AutoSize
$failures = @($records | Where-Object { $_.status -eq "error" -or $_.status -eq "skipped" })
if ($failures.Count -gt 0) {
    throw "One or more providers did not pass the bounded availability check. Secret values were not displayed."
}

Write-Host "LIVE VALIDATION PASSED: OpenAI, Claude, and Cursor responded or accepted their bounded check."
Write-Host "Periodic checks remain enabled every six hours. Project work still requires an owner-authored DIRECTOR message in #uppercut-ops."
