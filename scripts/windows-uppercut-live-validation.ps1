[CmdletBinding()]
param(
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [switch]$SeedProviderCredentialsFromPrimaryEnv,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

$profilePath = ".env.uppercut"
$profileTemplate = ".env.uppercut.example"

function New-RandomSecret {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) { continue }
        $values[$line.Substring(0, $separator).Trim()] = $line.Substring($separator + 1).Trim()
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

function Assert-Configured {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    $missing = @($Names | Where-Object {
        -not $Values.ContainsKey($_) -or
        [string]::IsNullOrWhiteSpace([string]$Values[$_]) -or
        ([string]$Values[$_]).StartsWith("REPLACE_WITH_")
    })
    if ($missing.Count -gt 0) {
        throw "Missing required $profilePath entries: $($missing -join ', '). No values were printed."
    }
}

function Wait-DirectorHealth {
    param([int]$Port, [int]$Timeout)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Timeout)
    do {
        try {
            return Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/health" -Method Get -TimeoutSec 5
        }
        catch { Start-Sleep -Seconds 3 }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Project Uppercut Director did not become ready on port ${Port} within ${Timeout}s."
}

function Assert-Ready {
    param([Parameter(Mandatory = $true)]$Health)

    $providerFailures = @()
    foreach ($name in @("openai_director", "claude", "cursor")) {
        if (-not [bool]$Health.providers.$name) { $providerFailures += $name }
    }
    $profileFailures = @()
    foreach ($name in @("projects_configured", "repositories_configured", "starting_refs_configured", "claude_callback_configured")) {
        if (-not [bool]$Health.execution_profile.$name) { $profileFailures += $name }
    }
    if (@($providerFailures).Count -gt 0 -or @($profileFailures).Count -gt 0) {
        throw "Readiness failed. Providers: $($providerFailures -join ', '); profile: $($profileFailures -join ', ')."
    }
}

if (-not (Test-Path $profilePath -PathType Leaf)) {
    if (-not (Test-Path $profileTemplate -PathType Leaf)) {
        throw "Missing $profileTemplate. Pull the current main branch before running this script."
    }
    Copy-Item $profileTemplate $profilePath
}

$profileText = Get-Content $profilePath -Raw
foreach ($name in @("ORCHESTRATOR_SHARED_SECRET", "CLAUDE_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY", "N8N_POSTGRES_PASSWORD")) {
    if ($profileText -match "(?m)^$([Regex]::Escape($name))=REPLACE_WITH_RANDOM_SECRET\s*$") {
        $profileText = Set-DotEnvValue -Text $profileText -Name $name -Value (New-RandomSecret)
    }
}

if ($SeedProviderCredentialsFromPrimaryEnv) {
    if (-not (Test-Path ".env" -PathType Leaf)) {
        throw "Cannot seed providers because the primary .env does not exist."
    }
    $primaryValues = Read-DotEnv -Path ".env"
    foreach ($name in @("OPENAI_API_KEY", "CURSOR_API_KEY", "CLAUDE_ROUTINE_URL", "CLAUDE_ROUTINE_TOKEN")) {
        if ($primaryValues.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace([string]$primaryValues[$name])) {
            $profileText = Set-DotEnvValue -Text $profileText -Name $name -Value ([string]$primaryValues[$name])
        }
    }
    Write-Host "Provider credentials were copied locally from .env to $profilePath; values were not displayed."
}

$profile = [ordered]@{
    APP_ENV = "project-uppercut"
    DIRECTOR_COMPOSE_PROJECT_NAME = "project-uppercut-director"
    DIRECTOR_HOST_PORT = "18080"
    N8N_HOST_PORT = "15678"
    SQLITE_PATH = "/data/project-uppercut.sqlite3"
    PROJECT_ALLOWLIST = "project-uppercut"
    REPOSITORY_ALLOWLIST = "edwardjones3976/Project-Uppercut"
    ALLOWED_STARTING_REFS = "main"
    SLACK_DIRECTOR_CHANNEL_ID = "C0BP62MBFNK"
    PRIMARY_TEAM = "openai,claude,cursor"
    PRIMARY_WORKHORSE = "claude"
    USAGE_CHECK_PROVIDERS = "openai,claude,cursor"
    USAGE_CHECK_INTERVAL_MINUTES = "360"
    ALLOW_PAID_RUNS = $(if ($EnablePaidRuns) { "true" } else { "false" })
    USAGE_CHECK_ENABLED = $(if ($EnablePaidRuns) { "true" } else { "false" })
}
foreach ($entry in $profile.GetEnumerator()) {
    $profileText = Set-DotEnvValue -Text $profileText -Name $entry.Key -Value $entry.Value
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) $profilePath), $profileText, $utf8NoBom)
$profileText = $null

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $profilePath "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to restrict $profilePath permissions." }

$values = Read-DotEnv -Path $profilePath
Assert-Configured -Values $values -Names @(
    "ORCHESTRATOR_SHARED_SECRET",
    "SLACK_APP_TOKEN",
    "SLACK_BOT_TOKEN",
    "OPENAI_API_KEY",
    "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN",
    "CLAUDE_CALLBACK_SECRET",
    "CURSOR_API_KEY",
    "N8N_ENCRYPTION_KEY",
    "N8N_POSTGRES_PASSWORD"
)

$composeProject = [string]$values["DIRECTOR_COMPOSE_PROJECT_NAME"]
$hostPort = [int]$values["DIRECTOR_HOST_PORT"]
$env:DIRECTOR_ENV_FILE = $profilePath
$env:DIRECTOR_COMPOSE_PROJECT_NAME = $composeProject

Write-Host "Using isolated profile $profilePath, Compose project $composeProject, and host port $hostPort."
docker compose --project-name $composeProject --env-file $profilePath config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed." }

docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the Project Uppercut Director." }

$health = Wait-DirectorHealth -Port $hostPort -Timeout ([Math]::Min($TimeoutSeconds, 180))
Assert-Ready -Health $health
Write-Host "Project Uppercut Director is ready; the primary Director deployment was not modified."

if (-not $EnablePaidRuns) {
    Write-Host "Paid execution remains disabled for this isolated profile."
    exit 0
}
if ($SkipProviderChecks) {
    Write-Host "Paid execution and scheduled checks are enabled; immediate checks were skipped."
    exit 0
}

$startedAt = [DateTimeOffset]::UtcNow
$headers = @{ Authorization = "Bearer $($values['ORCHESTRATOR_SHARED_SECRET'])" }
$baseUrl = "http://127.0.0.1:${hostPort}"
Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/run" -Method Post -Headers $headers -TimeoutSec 30 | Out-Null
Write-Host "Bounded checks accepted. Waiting for OpenAI, Claude, and Cursor..."

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$records = @()
do {
    Start-Sleep -Seconds 5
    $latest = @(Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/latest" -Method Get -Headers $headers -TimeoutSec 30)
    $records = @($latest | Where-Object {
        $_.provider -in @("openai", "claude", "cursor") -and
        [DateTimeOffset]::Parse($_.checked_at) -ge $startedAt
    })
} while ($records.Count -lt 3 -and [DateTimeOffset]::UtcNow -lt $deadline)

if ($records.Count -lt 3) {
    throw "Timed out waiting for all provider checks. Inspect the isolated stack with: docker compose -p $composeProject logs --tail 200 orchestrator"
}

$records | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at | Format-Table -AutoSize
$failures = @($records | Where-Object { $_.status -in @("error", "skipped") })
if ($failures.Count -gt 0) {
    throw "One or more providers failed the bounded check. Secret values were not displayed."
}

Write-Host "LIVE VALIDATION PASSED for the isolated Project Uppercut deployment."
