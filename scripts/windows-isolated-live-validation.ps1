[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("project-uppercut", "edge-oracle")]
    [string]$ProfileName,
    [switch]$ApiOnly,
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [switch]$SeedProviderCredentialsFromPrimaryEnv,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$originalLocation = Get-Location
$composeEnvironmentNames = @(
    "DIRECTOR_ENV_FILE", "DIRECTOR_COMPOSE_PROJECT_NAME", "DIRECTOR_HOST_PORT",
    "N8N_HOST_PORT", "APP_ENV", "LOG_LEVEL", "TZ", "ORCHESTRATOR_BASE_URL",
    "ORCHESTRATOR_SHARED_SECRET", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN",
    "SLACK_DIRECTOR_CHANNEL_ID", "SLACK_OWNER_USER_ID", "N8N_DIRECTOR_WEBHOOK_URL",
    "N8N_POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY", "N8N_IMAGE"
)
$originalProcessEnvironment = @{}
foreach ($name in $composeEnvironmentNames) {
    $originalProcessEnvironment[$name] = [pscustomobject]@{
        Present = Test-Path -LiteralPath "Env:${name}"
        Value = [Environment]::GetEnvironmentVariable($name, "Process")
    }
}

try {
Set-Location (Split-Path -Parent $PSScriptRoot)

$profiles = @{
    "project-uppercut" = [ordered]@{
        DisplayName = "Project Uppercut"
        ProfilePath = ".env.uppercut"
        ProfileTemplate = ".env.uppercut.example"
        APP_ENV = "project-uppercut"
        DIRECTOR_COMPOSE_PROJECT_NAME = "project-uppercut-director"
        DIRECTOR_HOST_PORT = "18080"
        N8N_HOST_PORT = "15678"
        SQLITE_PATH = "/data/project-uppercut.sqlite3"
        PROJECT_ALLOWLIST = "project-uppercut"
        REPOSITORY_ALLOWLIST = "edwardjones3976/Project-Uppercut"
        ALLOWED_STARTING_REFS = "main"
        MAX_AGENT_HOPS = "6"
        MAX_TASK_BUDGET_USD = "2.00"
    }
    "edge-oracle" = [ordered]@{
        DisplayName = "Edge Oracle"
        ProfilePath = ".env.edge-oracle"
        ProfileTemplate = ".env.edge-oracle.example"
        APP_ENV = "edge-oracle"
        DIRECTOR_COMPOSE_PROJECT_NAME = "edge-oracle-director"
        DIRECTOR_HOST_PORT = "28080"
        N8N_HOST_PORT = "25678"
        SQLITE_PATH = "/data/edge-oracle.sqlite3"
        PROJECT_ALLOWLIST = "edge-oracle"
        REPOSITORY_ALLOWLIST = "edwardjones3976/edge_engine"
        ALLOWED_STARTING_REFS = "main"
        MAX_AGENT_HOPS = "8"
        MAX_TASK_BUDGET_USD = "3.00"
    }
}

$selected = $profiles[$ProfileName]
$displayName = [string]$selected.DisplayName
$profilePath = [string]$selected.ProfilePath
$profileTemplate = [string]$selected.ProfileTemplate

function New-RandomSecret {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
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

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) $Path), $Text, $utf8NoBom)
}

function Write-ProfileSwitches {
    param(
        [Parameter(Mandatory = $true)][bool]$PaidRuns,
        [Parameter(Mandatory = $true)][bool]$UsageChecks
    )

    $text = Get-Content -LiteralPath $profilePath -Raw
    $text = Set-DotEnvValue -Text $text -Name "ALLOW_PAID_RUNS" -Value $(if ($PaidRuns) { "true" } else { "false" })
    $text = Set-DotEnvValue -Text $text -Name "USAGE_CHECK_ENABLED" -Value $(if ($UsageChecks) { "true" } else { "false" })
    Write-Utf8NoBom -Path $profilePath -Text $text
    $text = $null
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

function Assert-ProfileIdentity {
    param([Parameter(Mandatory = $true)][hashtable]$Values)

    foreach ($name in @(
        "APP_ENV", "DIRECTOR_COMPOSE_PROJECT_NAME", "DIRECTOR_HOST_PORT", "N8N_HOST_PORT",
        "SQLITE_PATH", "PROJECT_ALLOWLIST", "REPOSITORY_ALLOWLIST", "ALLOWED_STARTING_REFS"
    )) {
        if ([string]$Values[$name] -cne [string]$selected[$name]) {
            throw "$profilePath has an unexpected $name identity. No profile was started."
        }
    }

    $hostPort = 0
    $n8nPort = 0
    if (-not [int]::TryParse([string]$Values["DIRECTOR_HOST_PORT"], [ref]$hostPort) -or $hostPort -lt 1024 -or $hostPort -gt 65535) {
        throw "$profilePath has an invalid Director host port."
    }
    if (-not [int]::TryParse([string]$Values["N8N_HOST_PORT"], [ref]$n8nPort) -or $n8nPort -lt 1024 -or $n8nPort -gt 65535) {
        throw "$profilePath has an invalid n8n host port."
    }
    if ($hostPort -eq $n8nPort) { throw "$profilePath assigns the same port twice." }
    if ([string]$Values["DIRECTOR_COMPOSE_PROJECT_NAME"] -notmatch '^[a-z0-9][a-z0-9_-]+$') {
        throw "$profilePath has an invalid Compose project identity."
    }
    if ([string]$Values["SQLITE_PATH"] -notmatch '^/data/[a-z0-9._-]+\.sqlite3$') {
        throw "$profilePath has an invalid isolated SQLite path."
    }
    if ([string]$Values["ORACLE_AUTOMERGE_ENABLED"] -cne "false" -or
        [string]$Values["ORACLE_AUTOMERGE_KILL_SWITCH"] -cne "true") {
        throw "$profilePath did not stage the Oracle merge gate closed."
    }
    if ($ProfileName -eq "edge-oracle") {
        $oracleIdentity = [ordered]@{
            ORACLE_AUTOMERGE_PROJECT = "edge-oracle"
            ORACLE_AUTOMERGE_REPOSITORY = "edwardjones3976/edge_engine"
            ORACLE_AUTOMERGE_BASE_REF = "main"
            ORACLE_AUTOMERGE_BRANCH_PREFIX = "oracle/"
            ORACLE_AUTOMERGE_ALLOWED_PATHS = "oracle/**,docs/oracle/**,tests/oracle/**"
            ORACLE_AUTOMERGE_REQUIRED_CHECKS = "test"
            ORACLE_AUTOMERGE_REQUIRED_CHECK_APP = "github-actions"
        }
        foreach ($entry in $oracleIdentity.GetEnumerator()) {
            if ([string]$Values[$entry.Key] -cne [string]$entry.Value) {
                throw "$profilePath has an unexpected $($entry.Key) Oracle merge boundary."
            }
        }
    }
}

function Assert-NoProfileCollisions {
    param([Parameter(Mandatory = $true)][hashtable]$TargetValues)

    $identityNames = @(
        "DIRECTOR_COMPOSE_PROJECT_NAME", "DIRECTOR_HOST_PORT", "N8N_HOST_PORT", "SQLITE_PATH",
        "ORCHESTRATOR_SHARED_SECRET", "CLAUDE_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY",
        "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_REVIEW_SECRET",
        "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64",
        "SLACK_DIRECTOR_CHANNEL_ID", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"
    )
    foreach ($otherPath in @(".env", ".env.uppercut", ".env.edge-oracle")) {
        if ($otherPath -eq $profilePath -or -not (Test-Path -LiteralPath $otherPath -PathType Leaf)) { continue }
        $other = Read-DotEnv -Path $otherPath
        foreach ($name in $identityNames) {
            if ($other.ContainsKey($name) -and
                -not [string]::IsNullOrWhiteSpace([string]$other[$name]) -and
                [string]$other[$name] -ceq [string]$TargetValues[$name]) {
                throw "$profilePath collides with $otherPath on $name. No service was started."
            }
        }
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
    throw "$displayName Director did not become ready on port ${Port} within ${Timeout}s."
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

function Get-UsageProviderNames {
    param($Values)

    if ($null -eq $Values) { return @() }
    # Force array enumeration: a bare string (single-element JSON unwrap) would
    # otherwise pipe character-by-character through ForEach-Object.
    return @(@($Values) | ForEach-Object { [string]$_ })
}

function Assert-UsageProviderPartition {
    param(
        [Parameter(Mandatory = $true)]$ScheduledProviders,
        [Parameter(Mandatory = $true)]$CoolingDownProviders,
        [string[]]$ExpectedPrimary = @("claude", "cursor", "openai")
    )

    $scheduled = @(Get-UsageProviderNames -Values $ScheduledProviders)
    $cooling = @(Get-UsageProviderNames -Values $CoolingDownProviders)
    if ($scheduled.Count -ne @($scheduled | Sort-Object -Unique).Count) {
        throw "scheduled_providers contains duplicates."
    }
    if ($cooling.Count -ne @($cooling | Sort-Object -Unique).Count) {
        throw "cooling_down_providers contains duplicates."
    }
    $scheduledSorted = @($scheduled | Sort-Object)
    $coolingSorted = @($cooling | Sort-Object)
    $expectedSorted = @($ExpectedPrimary | Sort-Object)
    $unknown = @(Compare-Object -ReferenceObject $expectedSorted -DifferenceObject @($scheduledSorted + $coolingSorted | Sort-Object -Unique) |
        Where-Object { $_.SideIndicator -eq "=>" } |
        ForEach-Object { [string]$_.InputObject })
    if ($unknown.Count -gt 0) {
        throw "Unknown providers in schedule response: $($unknown -join ', ')."
    }
    $overlap = @($scheduledSorted | Where-Object { $coolingSorted -contains $_ })
    if ($overlap.Count -gt 0) {
        throw "scheduled_providers and cooling_down_providers overlap: $($overlap -join ', ')."
    }
    $covered = @($scheduledSorted + $coolingSorted | Sort-Object -Unique)
    if (@(Compare-Object -ReferenceObject $expectedSorted -DifferenceObject $covered).Count -ne 0) {
        throw "scheduled_providers and cooling_down_providers must partition expected primary providers $($expectedSorted -join ', '); got $($covered -join ', ')."
    }
    return [pscustomobject]@{
        ScheduledProviders = $scheduledSorted
        CoolingDownProviders = $coolingSorted
    }
}

function Assert-CoolingProviderEvidence {
    param(
        [Parameter(Mandatory = $true)][string[]]$Providers,
        [Parameter(Mandatory = $true)]$LatestRecords,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Now,
        [int]$CooldownWindowMinutes = 15
    )

    $accepted = @()
    foreach ($provider in $Providers) {
        $matches = @($LatestRecords | Where-Object { [string]$_.provider -ceq $provider })
        if ($matches.Count -eq 0) {
            throw "Missing latest usage record for cooling provider $provider."
        }
        if ($matches.Count -ne 1) {
            throw "Ambiguous usage evidence for provider ${provider}: $($matches.Count) records."
        }
        $record = $matches[0]
        $status = [string]$record.status
        if ($status -notin @("ok", "prompted")) {
            throw "Cooling provider $provider latest status is '$status'; only ok/prompted evidence is acceptable."
        }
        $checkedAt = [DateTimeOffset]::Parse([string]$record.checked_at)
        $age = $Now - $checkedAt
        if ($age -lt [TimeSpan]::Zero -or $age -ge [TimeSpan]::FromMinutes($CooldownWindowMinutes)) {
            throw "Cooling provider $provider latest check is outside the ${CooldownWindowMinutes}-minute cooldown window."
        }
        $accepted += $record
    }
    return $accepted
}

function Assert-ScheduledCycleEvidence {
    param(
        [Parameter(Mandatory = $true)][string[]]$Providers,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)]$Records,
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt
    )

    $matched = @($Records | Where-Object {
        [string]$_.cycle_id -ceq $CycleId -and
        [string]$_.provider -in $Providers -and
        [DateTimeOffset]::Parse([string]$_.checked_at) -ge $StartedAt
    })
    $accepted = @()
    foreach ($provider in $Providers) {
        $matches = @($matched | Where-Object { [string]$_.provider -ceq $provider })
        if ($matches.Count -eq 0) {
            throw "Missing exact-cycle usage record for scheduled provider $provider."
        }
        if ($matches.Count -ne 1) {
            throw "Ambiguous usage evidence for provider ${provider}: $($matches.Count) records."
        }
        $record = $matches[0]
        $status = [string]$record.status
        if ($status -notin @("ok", "prompted")) {
            throw "Scheduled provider $provider status is '$status'; only ok/prompted evidence is acceptable."
        }
        $accepted += $record
    }
    return $accepted
}

if ($EnablePaidRuns -and $SkipProviderChecks) {
    throw "-SkipProviderChecks cannot be combined with -EnablePaidRuns. Provider readiness is a required paid-run gate."
}
if ($ApiOnly -and $EnablePaidRuns) {
    throw "-ApiOnly cannot be combined with -EnablePaidRuns. API-only activation keeps paid execution closed."
}
if ($ApiOnly -and $ProfileName -ne "edge-oracle") {
    throw "-ApiOnly is currently limited to the Edge Oracle recovery profile."
}

if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $profileTemplate -PathType Leaf)) {
        throw "Missing $profileTemplate. Pull the current main branch before running this script."
    }
    Copy-Item -LiteralPath $profileTemplate -Destination $profilePath
}

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $profilePath "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to restrict $profilePath permissions before secret handling." }

$profileText = Get-Content -LiteralPath $profilePath -Raw
foreach ($name in @("ORCHESTRATOR_SHARED_SECRET", "CLAUDE_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY", "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_REVIEW_SECRET")) {
    if ($profileText -match "(?m)^$([Regex]::Escape($name))=REPLACE_WITH_RANDOM_SECRET\s*$") {
        $profileText = Set-DotEnvValue -Text $profileText -Name $name -Value (New-RandomSecret)
    }
}

if ($SeedProviderCredentialsFromPrimaryEnv) {
    if (-not (Test-Path -LiteralPath ".env" -PathType Leaf)) {
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

$identity = [ordered]@{
    APP_ENV = [string]$selected.APP_ENV
    DIRECTOR_COMPOSE_PROJECT_NAME = [string]$selected.DIRECTOR_COMPOSE_PROJECT_NAME
    DIRECTOR_HOST_PORT = [string]$selected.DIRECTOR_HOST_PORT
    N8N_HOST_PORT = [string]$selected.N8N_HOST_PORT
    SQLITE_PATH = [string]$selected.SQLITE_PATH
    PROJECT_ALLOWLIST = [string]$selected.PROJECT_ALLOWLIST
    REPOSITORY_ALLOWLIST = [string]$selected.REPOSITORY_ALLOWLIST
    ALLOWED_STARTING_REFS = [string]$selected.ALLOWED_STARTING_REFS
    MAX_AGENT_HOPS = [string]$selected.MAX_AGENT_HOPS
    MAX_TASK_BUDGET_USD = [string]$selected.MAX_TASK_BUDGET_USD
    PRIMARY_TEAM = "openai,claude,cursor"
    PRIMARY_WORKHORSE = "claude"
    USAGE_CHECK_PROVIDERS = "openai,claude,cursor"
    USAGE_CHECK_INTERVAL_MINUTES = "360"
    ALLOW_PAID_RUNS = "false"
    USAGE_CHECK_ENABLED = "false"
    ORACLE_AUTOMERGE_ENABLED = "false"
    ORACLE_AUTOMERGE_KILL_SWITCH = "true"
}
foreach ($entry in $identity.GetEnumerator()) {
    $profileText = Set-DotEnvValue -Text $profileText -Name $entry.Key -Value $entry.Value
}

Write-Utf8NoBom -Path $profilePath -Text $profileText
$profileText = $null

$values = Read-DotEnv -Path $profilePath
Assert-ProfileIdentity -Values $values
Assert-NoProfileCollisions -TargetValues $values
$requiredConfiguration = @(
    "ORCHESTRATOR_SHARED_SECRET", "OPENAI_API_KEY", "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN", "CLAUDE_CALLBACK_SECRET", "CURSOR_API_KEY",
    "N8N_ENCRYPTION_KEY", "N8N_POSTGRES_PASSWORD"
)
if (-not $ApiOnly) {
    $requiredConfiguration += @(
        "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID"
    )
}
Assert-Configured -Values $values -Names $requiredConfiguration
if ($ProfileName -eq "edge-oracle") {
    Assert-Configured -Values $values -Names @("ORACLE_AUTOMERGE_REVIEW_SECRET")
}

$composeProject = [string]$values["DIRECTOR_COMPOSE_PROJECT_NAME"]
$hostPort = [int]$values["DIRECTOR_HOST_PORT"]
[Environment]::SetEnvironmentVariable("DIRECTOR_ENV_FILE", $profilePath, "Process")
foreach ($name in @($composeEnvironmentNames | Where-Object { $_ -ne "DIRECTOR_ENV_FILE" })) {
    if ($values.ContainsKey($name)) {
        [Environment]::SetEnvironmentVariable($name, [string]$values[$name], "Process")
    }
    else {
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
}

Write-Host "Using isolated $displayName profile $profilePath, Compose project $composeProject, and host port $hostPort."
docker compose --project-name $composeProject --env-file $profilePath config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed." }

docker compose --project-name $composeProject --env-file $profilePath build orchestrator
if ($LASTEXITCODE -ne 0) { throw "Unable to build the isolated $displayName Director image." }

# Inspect the isolated volume before starting FastAPI. Its lifespan hook would
# otherwise schedule recoverable work before this paid-run preflight.
$recoverableOutput = @(
    docker compose --project-name $composeProject --env-file $profilePath run --rm --no-deps -T --entrypoint python orchestrator -c "from director_orchestrator.config import Settings; from director_orchestrator.store import TaskStore; print(len(TaskStore(Settings().sqlite_path).list_recoverable()))"
)
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect the isolated task store." }
$recoverableLine = @($recoverableOutput | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1)
if ($recoverableLine.Count -ne 1) { throw "The isolated task-store preflight returned an unreadable result." }
if ([int]$recoverableLine[0] -ne 0) {
    throw "The isolated task store contains recoverable work. Paid execution remains disabled; resolve or archive it explicitly."
}
Write-Host "Isolated task store contains no recoverable work."

if (-not $EnablePaidRuns) {
    if ($ApiOnly) {
        docker compose --project-name $composeProject --env-file $profilePath stop slackbot n8n n8n-db
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop optional Edge Oracle services for API-only activation." }
        docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans orchestrator
    }
    else {
        docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the $displayName Director." }
    $health = Wait-DirectorHealth -Port $hostPort -Timeout ([Math]::Min($TimeoutSeconds, 180))
    Assert-Ready -Health $health
    if ($ApiOnly) {
        Write-Host "$displayName Director API is ready with paid execution disabled; Slack and n8n remain stopped."
    }
    else {
        Write-Host "$displayName Director is ready with paid execution disabled; other deployments were not modified."
    }
    return
}

Write-ProfileSwitches -PaidRuns $true -UsageChecks $true
$values = Read-DotEnv -Path $profilePath
$headers = @{ Authorization = "Bearer $($values['ORCHESTRATOR_SHARED_SECRET'])" }
$baseUrl = "http://127.0.0.1:${hostPort}"

try {
    docker compose --project-name $composeProject --env-file $profilePath stop slackbot
    if ($LASTEXITCODE -ne 0) { throw "Unable to close Slack intake before provider checks." }

    $startedAt = [DateTimeOffset]::UtcNow
    docker compose --project-name $composeProject --env-file $profilePath up -d --build --force-recreate orchestrator
    if ($LASTEXITCODE -ne 0) { throw "Unable to start the gated provider-check service." }
    $health = Wait-DirectorHealth -Port $hostPort -Timeout ([Math]::Min($TimeoutSeconds, 180))
    Assert-Ready -Health $health

    $accepted = Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/run" -Method Post -Headers $headers -TimeoutSec 30
    # Freeze cooling age to accept time; waiting for scheduled probes must not
    # expire evidence that justified cooling_down_providers at reserve time.
    $acceptedAt = [DateTimeOffset]::UtcNow
    $runStatus = [string]$accepted.status
    $partition = Assert-UsageProviderPartition `
        -ScheduledProviders $accepted.scheduled_providers `
        -CoolingDownProviders $accepted.cooling_down_providers
    $scheduledProviders = @($partition.ScheduledProviders)
    $coolingProviders = @($partition.CoolingDownProviders)

    if ($runStatus -ceq "not_due") {
        Write-Host (
            "No usage-check providers are due under the 15-minute minimum. " +
            "Verifying recent successful evidence for: $($coolingProviders -join ', ')."
        )
        $latest = @(Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/latest" -Method Get -Headers $headers -TimeoutSec 30 | ForEach-Object { $_ })
        $evidence = Assert-CoolingProviderEvidence `
            -Providers $coolingProviders `
            -LatestRecords $latest `
            -Now ([DateTimeOffset]::UtcNow)
        $evidence | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at | Format-Table -AutoSize
        docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans
        if ($LASTEXITCODE -ne 0) { throw "Cooldown evidence accepted, but the complete isolated stack did not start." }
        Write-Host "LIVE VALIDATION PASSED. Paid execution is enabled only for the isolated $displayName deployment."
        return
    }
    if ($runStatus -cne "accepted") {
        throw "The usage-check endpoint refused the cycle with status '$runStatus'."
    }
    $cycleId = [string]$accepted.cycle_id
    if ($cycleId -notmatch '^USAGE-[A-F0-9]{10}$') {
        throw "The usage-check endpoint did not return a valid cycle identifier."
    }
    if ($scheduledProviders.Count -eq 0) {
        throw "The usage-check endpoint accepted a cycle but scheduled no primary providers."
    }
    Write-Host (
        "Bounded check cycle $cycleId accepted. Waiting for scheduled providers: " +
        ($scheduledProviders -join ", ") +
        $(if ($coolingProviders.Count -gt 0) { "; verifying cooling providers: $($coolingProviders -join ', ')" } else { "" }) +
        "..."
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $latest = @()
    $scheduledReady = $false
    do {
        Start-Sleep -Seconds 5
        $latest = @(Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/latest" -Method Get -Headers $headers -TimeoutSec 30 | ForEach-Object { $_ })
        $matched = @($latest | Where-Object {
            [string]$_.cycle_id -ceq $cycleId -and
            [string]$_.provider -in $scheduledProviders -and
            [DateTimeOffset]::Parse([string]$_.checked_at) -ge $startedAt
        })
        $failed = @($matched | Where-Object { [string]$_.status -notin @("ok", "prompted") })
        if ($failed.Count -gt 0) {
            $names = @($failed | ForEach-Object { [string]$_.provider } | Sort-Object -Unique)
            throw "Scheduled provider checks returned unsuccessful status for: $($names -join ', ')."
        }
        $ambiguous = @($scheduledProviders | Where-Object {
            $provider = $_
            @($matched | Where-Object { [string]$_.provider -ceq $provider }).Count -gt 1
        })
        if ($ambiguous.Count -gt 0) {
            throw "Ambiguous usage evidence for provider $($ambiguous[0])."
        }
        $observedProviders = @($matched | ForEach-Object { [string]$_.provider } | Sort-Object -Unique)
        $scheduledReady = ($observedProviders.Count -eq $scheduledProviders.Count)
    } while (
        -not $scheduledReady -and
        [DateTimeOffset]::UtcNow -lt $deadline
    )

    if (-not $scheduledReady) {
        throw "Timed out waiting for scheduled provider checks ($($scheduledProviders -join ', ')). Inspect with: docker compose -p $composeProject logs --tail 200 orchestrator"
    }
    $scheduledEvidence = Assert-ScheduledCycleEvidence `
        -Providers $scheduledProviders `
        -CycleId $cycleId `
        -Records $latest `
        -StartedAt $startedAt
    $coolingEvidence = Assert-CoolingProviderEvidence `
        -Providers $coolingProviders `
        -LatestRecords $latest `
        -Now $acceptedAt
    $allEvidence = @($scheduledEvidence + $coolingEvidence)
    $allEvidence | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at | Format-Table -AutoSize

    docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Provider checks passed, but the complete isolated stack did not start." }
}
catch {
    $failure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    $switchesClosed = $true
    try { Write-ProfileSwitches -PaidRuns $false -UsageChecks $false }
    catch {
        $switchesClosed = $false
        $rollbackErrors.Add("profile switches could not be closed")
    }
    docker compose --project-name $composeProject --env-file $profilePath stop slackbot | Out-Null
    if ($LASTEXITCODE -ne 0) { $rollbackErrors.Add("Slack intake could not be stopped") }
    if ($switchesClosed) {
        docker compose --project-name $composeProject --env-file $profilePath up -d --force-recreate orchestrator | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $rollbackErrors.Add("the paid-closed orchestrator could not be recreated")
            docker compose --project-name $composeProject --env-file $profilePath stop orchestrator slackbot | Out-Null
            if ($LASTEXITCODE -ne 0) { $rollbackErrors.Add("the isolated API and Slack services could not be stopped") }
        }
    }
    else {
        docker compose --project-name $composeProject --env-file $profilePath stop orchestrator slackbot | Out-Null
        if ($LASTEXITCODE -ne 0) { $rollbackErrors.Add("the isolated API and Slack services could not be stopped") }
    }
    if ($rollbackErrors.Count -gt 0) {
        throw "Live validation failed: $($failure.Exception.Message). Rollback was not fully proven: $($rollbackErrors -join '; ')."
    }
    throw $failure
}

Write-Host "LIVE VALIDATION PASSED. Paid execution is enabled only for the isolated $displayName deployment."
}
finally {
    Set-Location $originalLocation
    foreach ($name in $composeEnvironmentNames) {
        $original = $originalProcessEnvironment[$name]
        if ($original.Present) {
            [Environment]::SetEnvironmentVariable($name, [string]$original.Value, "Process")
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
}
