# [[Claude Code | Claude Opus 5 | 2026-09-07 | 19:34 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 19:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 18:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 17:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:38 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 10:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:54 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 09:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 19:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("project-uppercut", "edge-oracle", "long-table")]
    [string]$ProfileName,
    [switch]$ApiOnly,
    [switch]$EnablePaidRuns,
    [switch]$SkipProviderChecks,
    [switch]$SeedProviderCredentialsFromPrimaryEnv,
    [string]$ProviderCredentialSourceEnvPath = "",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$originalLocation = Get-Location
$profilePath = $null
$composeProject = $null
$composeEnvironmentNames = @(
    "DIRECTOR_ENV_FILE", "DIRECTOR_COMPOSE_PROJECT_NAME", "DIRECTOR_HOST_PORT",
    "N8N_HOST_PORT", "APP_ENV", "LOG_LEVEL", "TZ", "ORCHESTRATOR_BASE_URL",
    "ORCHESTRATOR_SHARED_SECRET", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN",
    "SLACK_DIRECTOR_CHANNEL_ID", "SLACK_OWNER_USER_ID", "SLACK_GATEWAY_SECRET",
    "CHATGPT_BRIDGE_ENABLED", "CHATGPT_BRIDGE_SLACK_USER_ID",
    "CHATGPT_BRIDGE_CHANNEL_IDS", "CHATGPT_BRIDGE_MAX_PROMPT_CHARS",
    "N8N_DIRECTOR_WEBHOOK_URL",
    "N8N_POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY", "N8N_IMAGE",
    # Every gate the identity block pins must also be bound here. Compose reads
    # the process environment ahead of --env-file, so a name that is written to
    # the profile but not bound is silently overridden by whatever the operator
    # happens to have exported. That is how an inherited ALLOW_PAID_RUNS=true
    # resolves a paid-closed profile to a paid-open container.
    "SQLITE_PATH", "PROJECT_ALLOWLIST", "REPOSITORY_ALLOWLIST",
    "ALLOWED_STARTING_REFS", "MAX_AGENT_HOPS", "MAX_TASK_BUDGET_USD",
    "PRIMARY_TEAM", "PRIMARY_WORKHORSE", "USAGE_CHECK_PROVIDERS",
    "USAGE_CHECK_INTERVAL_MINUTES", "ALLOW_PAID_RUNS", "USAGE_CHECK_ENABLED",
    "ORACLE_AUTOMERGE_ENABLED", "ORACLE_AUTOMERGE_KILL_SWITCH",
    "LANGGRAPH_RUNTIME_ENABLED", "LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED",
    "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED",
    "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED",
    "LANGGRAPH_PARALLEL_KILL_SWITCH"
)
$originalProcessEnvironment = @{}
foreach ($name in $composeEnvironmentNames) {
    $originalProcessEnvironment[$name] = [pscustomobject]@{
        Present = Test-Path -LiteralPath "Env:${name}"
        Value = [Environment]::GetEnvironmentVariable($name, "Process")
    }
}

try {
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

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
    "long-table" = [ordered]@{
        DisplayName = "The Long Table"
        ProfilePath = ".env.long-table"
        ProfileTemplate = ".env.long-table.example"
        APP_ENV = "long-table"
        DIRECTOR_COMPOSE_PROJECT_NAME = "long-table-director"
        DIRECTOR_HOST_PORT = "38080"
        N8N_HOST_PORT = "35678"
        SQLITE_PATH = "/data/long-table.sqlite3"
        PROJECT_ALLOWLIST = "long-table-bid-whist"
        REPOSITORY_ALLOWLIST = "edwardjones3976/long-table-bid-whist"
        ALLOWED_STARTING_REFS = "main"
        MAX_AGENT_HOPS = "6"
        MAX_TASK_BUDGET_USD = "1.00"
    }
}

$selected = $profiles[$ProfileName]
$displayName = [string]$selected.DisplayName
$profilePath = [string]$selected.ProfilePath
$profileTemplate = [string]$selected.ProfileTemplate
# The Compose project is a fixed identity of the selected profile, never a
# value read back from the profile file. Deriving it here, before any fallible
# profile staging or validation, lets the early stop below and every rollback
# path address the correct stack. Compose resolves the orchestrator env_file
# from DIRECTOR_ENV_FILE, so bind it to the isolated profile at the same point:
# without it Compose falls back to the primary .env and cannot load the model
# to stop anything. The finally block restores the original process value.
$composeProject = [string]$selected.DIRECTOR_COMPOSE_PROJECT_NAME
[Environment]::SetEnvironmentVariable("DIRECTOR_ENV_FILE", $profilePath, "Process")

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

function Copy-ProviderCredentialValues {
    param(
        [Parameter(Mandatory = $true)][hashtable]$SourceValues,
        [Parameter(Mandatory = $true)][string]$ProfileText
    )

    # Only these named credential values are ever copied. Enable flags such as
    # GROK_ENABLED and PERPLEXITY_ENABLED are neither copied from the source
    # nor synthesized because a key exists: a seeded key stays inert until the
    # target profile explicitly opts in and lists the provider in
    # USAGE_CHECK_PROVIDERS.
    $text = $ProfileText
    foreach ($name in @(
        "OPENAI_API_KEY", "CURSOR_API_KEY", "CLAUDE_ROUTINE_URL", "CLAUDE_ROUTINE_TOKEN",
        "GEMINI_API_KEY", "PERPLEXITY_API_KEY", "XAI_API_KEY"
    )) {
        if ($SourceValues.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace([string]$SourceValues[$name])) {
            $text = Set-DotEnvValue -Text $text -Name $name -Value ([string]$SourceValues[$name])
        }
    }
    return $text
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) $Path), $Text, $utf8NoBom)
}

function Clear-ProcessEnvironmentVariable {
    param([Parameter(Mandatory = $true)][string]$Name)

    # PowerShell 7 keeps the name with an empty value when SetEnvironmentVariable
    # is handed $null, and an exported empty value still beats --env-file. Only
    # actual removal restores absence, which Windows PowerShell 5.1 already did.
    Remove-Item -LiteralPath "Env:${Name}" -ErrorAction SilentlyContinue
}

function Sync-ComposeEnvironment {
    param([Parameter(Mandatory = $true)][hashtable]$Values)

    # Compose reads the process environment ahead of --env-file, so every
    # managed name is rebound whenever the profile changes. Binding once at
    # staging would pin the closed gates for the rest of the run and make
    # authorized activation impossible: the file would say true while the
    # exported false kept winning.
    foreach ($name in @($composeEnvironmentNames | Where-Object { $_ -ne "DIRECTOR_ENV_FILE" })) {
        if ($Values.ContainsKey($name)) {
            [Environment]::SetEnvironmentVariable($name, [string]$Values[$name], "Process")
        }
        else {
            Clear-ProcessEnvironmentVariable -Name $name
        }
    }
}

function Write-ProfileSwitches {
    param(
        [Parameter(Mandatory = $true)][bool]$PaidRuns,
        [Parameter(Mandatory = $true)][bool]$UsageChecks,
        [bool]$RuntimeGraduation = $false
    )

    $text = Get-Content -LiteralPath $profilePath -Raw
    $text = Set-DotEnvValue -Text $text -Name "ALLOW_PAID_RUNS" -Value $(if ($PaidRuns) { "true" } else { "false" })
    $text = Set-DotEnvValue -Text $text -Name "USAGE_CHECK_ENABLED" -Value $(if ($UsageChecks) { "true" } else { "false" })
    if ($ProfileName -eq "long-table") {
        if ($RuntimeGraduation -and -not $PaidRuns) {
            throw "Long Table runtime graduation cannot open while paid execution is closed."
        }
        # The Long Table is the only graduated API-only canary. One operation
        # opens or closes the paid gate, the single-writer runtime, and bounded
        # parallel dispatch together so rollback cannot leave a partial writer.
        $enabled = $(if ($RuntimeGraduation) { "true" } else { "false" })
        $text = Set-DotEnvValue -Text $text -Name "LANGGRAPH_RUNTIME_ENABLED" -Value $enabled
        $text = Set-DotEnvValue -Text $text -Name "LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED" -Value $enabled
        $text = Set-DotEnvValue -Text $text -Name "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED" -Value $enabled
        $text = Set-DotEnvValue -Text $text -Name "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED" -Value $enabled
        $text = Set-DotEnvValue -Text $text -Name "LANGGRAPH_PARALLEL_KILL_SWITCH" -Value $(if ($RuntimeGraduation) { "false" } else { "true" })
        # Completion workers have their own evidence and sandbox prerequisites.
        # Paid activation must never silently turn either worker tier on.
    }
    Write-Utf8NoBom -Path $profilePath -Text $text
    $text = $null
    Sync-ComposeEnvironment -Values (Read-DotEnv -Path $profilePath)
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
    if ($ProfileName -eq "long-table") {
        if ([string]$Values["REPOSITORY_SNAPSHOT_HOST_PATH"] -cne "C:/Users/edwar/.director-snapshots/long-table-main" -or
            [string]$Values["REPOSITORY_SNAPSHOT_ROOT"] -cne "/repository") {
            throw "$profilePath has an unexpected Long Table repository snapshot boundary."
        }
        if ([string]$Values["MAX_TASK_BUDGET_USD"] -cne "1.00" -or
            [string]$Values["MAX_DAILY_PROVIDER_BUDGET_USD"] -cne "5.00") {
            throw "$profilePath has an unexpected Long Table paid-use budget."
        }
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
        "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_REVIEW_SECRET", "SLACK_GATEWAY_SECRET",
        "LANGGRAPH_RUNTIME_WRITER_SECRET",
        "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64",
        "SLACK_DIRECTOR_CHANNEL_ID", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"
    )
    foreach ($otherPath in @(".env", ".env.uppercut", ".env.edge-oracle", ".env.long-table")) {
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
    return @($Values | ForEach-Object { [string]$_ })
}

function Get-ValidatedUsageCheckProviders {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][string]$ProfileLabel
    )

    # Mirrors SUPPORTED_PROVIDERS in src/director_orchestrator/usage.py. OpenAI,
    # Claude, and Cursor are mandatory check providers. An optional provider is
    # scheduled only when it is listed here, explicitly enabled, and carries a
    # configured credential; anything not listed is never dispatched. The
    # returned set is the exact partition the usage-check endpoint must report.
    $mandatory = @("openai", "claude", "cursor")
    $known = @("openai", "claude", "cursor", "grok", "perplexity", "gemini")
    if (-not $Values.ContainsKey("USAGE_CHECK_PROVIDERS") -or
        [string]::IsNullOrWhiteSpace([string]$Values["USAGE_CHECK_PROVIDERS"])) {
        throw "$ProfileLabel must declare USAGE_CHECK_PROVIDERS explicitly before paid provider checks."
    }
    $configured = @(([string]$Values["USAGE_CHECK_PROVIDERS"]).Split(",") | ForEach-Object { $_.Trim() })
    if (@($configured | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
        throw "$ProfileLabel USAGE_CHECK_PROVIDERS contains an empty provider entry."
    }
    $unknown = @($configured | Where-Object { $_ -cnotin $known })
    if ($unknown.Count -gt 0) {
        throw "$ProfileLabel USAGE_CHECK_PROVIDERS names unknown providers: $($unknown -join ', ')."
    }
    $duplicates = @($configured | Group-Object -CaseSensitive | Where-Object { $_.Count -gt 1 } | ForEach-Object { [string]$_.Name })
    if ($duplicates.Count -gt 0) {
        throw "$ProfileLabel USAGE_CHECK_PROVIDERS contains duplicate providers: $($duplicates -join ', ')."
    }
    $missing = @($mandatory | Where-Object { $_ -cnotin $configured })
    if ($missing.Count -gt 0) {
        throw "$ProfileLabel USAGE_CHECK_PROVIDERS omits mandatory primary providers: $($missing -join ', ')."
    }
    foreach ($provider in $configured) {
        $enabledName = $null
        $credentialNames = @()
        if ($provider -ceq "grok") {
            $enabledName = "GROK_ENABLED"
            $credentialNames = @("XAI_API_KEY")
        }
        elseif ($provider -ceq "perplexity") {
            $enabledName = "PERPLEXITY_ENABLED"
            $credentialNames = @("PERPLEXITY_API_KEY")
        }
        elseif ($provider -ceq "gemini") {
            $credentialNames = @("GEMINI_API_KEY")
        }
        else {
            continue
        }
        if ($null -ne $enabledName -and [string]$Values[$enabledName] -cne "true") {
            throw "$ProfileLabel lists $provider in USAGE_CHECK_PROVIDERS but $enabledName is not exactly true."
        }
        foreach ($name in $credentialNames) {
            if (-not $Values.ContainsKey($name) -or
                [string]::IsNullOrWhiteSpace([string]$Values[$name]) -or
                ([string]$Values[$name]).StartsWith("REPLACE_WITH_")) {
                throw "$ProfileLabel lists $provider in USAGE_CHECK_PROVIDERS but $name is not configured. No values were printed."
            }
        }
    }
    return @($configured | Sort-Object)
}

function Assert-UsageProviderPartition {
    param(
        [Parameter(Mandatory = $true)]$ScheduledProviders,
        [Parameter(Mandatory = $true)]$CoolingDownProviders,
        [Parameter(Mandatory = $true)][string[]]$ExpectedPrimary
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

function Get-CompletionVerifiedProviders {
    param([Parameter(Mandatory = $true)][string]$ProfileName)

    if ($ProfileName -ieq "long-table") {
        return @("claude")
    }
    return @()
}

function Assert-CoolingProviderEvidence {
    param(
        [Parameter(Mandatory = $true)][string[]]$Providers,
        [Parameter(Mandatory = $true)]$LatestRecords,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Now,
        [int]$CooldownWindowMinutes = 15,
        [string[]]$CompletionVerifiedProviders = @()
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
        if ($provider -in $CompletionVerifiedProviders -and $status -cne "ok") {
            throw "Cooling provider $provider requires authenticated terminal callback evidence; status '$status' is not completion-verified."
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
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt,
        [string[]]$CompletionVerifiedProviders = @()
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
        if ($provider -in $CompletionVerifiedProviders -and $status -cne "ok") {
            throw "Scheduled provider $provider requires authenticated terminal callback evidence; status '$status' is not completion-verified."
        }
        $accepted += $record
    }
    return $accepted
}

if ($EnablePaidRuns -and $SkipProviderChecks) {
    throw "-SkipProviderChecks cannot be combined with -EnablePaidRuns. Provider readiness is a required paid-run gate."
}
if ($ApiOnly -and $EnablePaidRuns -and $ProfileName -ne "long-table") {
    throw "-ApiOnly can combine with -EnablePaidRuns only for the dedicated Long Table canary."
}

function ConvertTo-StrictBoolean {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($Value -is [bool]) { return [bool]$Value }
    $text = [string]$Value
    if ($text -ceq "true") { return $true }
    if ($text -ceq "false") { return $false }
    throw "$Name must be the exact boolean true or false."
}

function Assert-LongTableGraduationReady {
    param(
        [Parameter(Mandatory = $true)]$Health,
        [Parameter(Mandatory = $true)][hashtable]$Values
    )

    if ($ProfileName -ne "long-table") { return }
    $writerActive = ConvertTo-StrictBoolean -Value $Health.langgraph_runtime.task_writer_active -Name "langgraph_runtime.task_writer_active"
    $parallelActive = ConvertTo-StrictBoolean -Value $Health.parallel_dispatch.active -Name "parallel_dispatch.active"
    if (-not $writerActive -or -not $parallelActive) {
        throw "Long Table paid activation did not establish the LangGraph task writer and bounded parallel dispatch."
    }
    $workerActive = ConvertTo-StrictBoolean -Value $Health.completion_gates.worker_execution_active -Name "completion_gates.worker_execution_active"
    $isolatedProofEnabled = ConvertTo-StrictBoolean -Value $Values["COMPLETION_GATE_ISOLATED_PROOF_ENABLED"] -Name "COMPLETION_GATE_ISOLATED_PROOF_ENABLED"
    if ($workerActive -and -not $isolatedProofEnabled) {
        throw "Long Table paid activation unexpectedly enabled completion workers."
    }
}

function Assert-LongTableSnapshot {
    param([Parameter(Mandatory = $true)][hashtable]$Values)

    if ($ProfileName -ne "long-table") { return }
    $snapshotPath = [string]$Values["REPOSITORY_SNAPSHOT_HOST_PATH"]
    if (-not (Test-Path -LiteralPath $snapshotPath -PathType Container)) {
        throw "The configured Long Table repository snapshot is unavailable. No service was started."
    }
    $inside = @(& git -C $snapshotPath rev-parse --is-inside-work-tree 2>$null)
    if ($LASTEXITCODE -ne 0 -or $inside.Count -ne 1 -or [string]$inside[0] -cne "true") {
        throw "The configured Long Table snapshot is not a Git worktree. No service was started."
    }
    $origin = [string](@(& git -C $snapshotPath remote get-url origin 2>$null) | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or
        $origin -notmatch '^(?:https://github\.com/|git@github\.com:)edwardjones3976/long-table-bid-whist(?:\.git)?$') {
        throw "The configured Long Table snapshot has an unexpected origin. No service was started."
    }
    $dirty = @(& git -C $snapshotPath status --porcelain --untracked-files=all 2>$null)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
        throw "The configured Long Table snapshot has tracked changes or could not be inspected. No service was started."
    }
    $head = [string](@(& git -C $snapshotPath rev-parse HEAD 2>$null) | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-f]{40}$') {
        throw "The configured Long Table snapshot has no exact commit. No service was started."
    }
    $remoteHead = [string](@(& git -C $snapshotPath rev-parse origin/main 2>$null) | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $remoteHead -notmatch '^[0-9a-f]{40}$' -or $head -cne $remoteHead) {
        throw "The configured Long Table snapshot is not at the locally verified origin/main commit. No service was started."
    }
}

function Assert-N8nWorkflowPublishCapability {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$HelpOutput)

    # The selected n8n image must expose both exact command names before any
    # workflow mutation. The pinned CLI renders command headings with a trailing
    # separator (for example, "publish:workflow:"). Anchor each name at the
    # beginning of its own help line so unpublish cannot satisfy publish and a
    # deprecation note cannot stand in for a missing command.
    $ansi = "{0}\[[0-9;?]*[ -/]*[@-~]" -f [char]27
    $lines = @(
        $HelpOutput |
            ForEach-Object { ([string]$_) -replace $ansi, "" } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $missing = @(@("publish:workflow", "unpublish:workflow") | Where-Object {
        $heading = "^\s*$([Regex]::Escape($_)):\s*(?:$|\S.*$)"
        @($lines | Where-Object { [string]$_ -cmatch $heading }).Count -eq 0
    })
    if ($missing.Count -gt 0) {
        throw "The selected n8n image does not expose the required workflow commands: $($missing -join ', '). No workflow was modified."
    }
}

function Stop-PreviouslyRunningServices {
    param(
        [Parameter(Mandatory = $true)][string]$ComposeProject,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string[]]$Services
    )

    # Containers keep the environment they were created with, so closing the
    # profile switches cannot reach a paid orchestrator or Slack intake that an
    # earlier activation left running. Stop the named services, then prove that
    # none of them is still live. A failed stop is never masked by the check and
    # a live container fails closed before any preflight step can run.
    $label = $Services -join " and "
    docker compose --project-name $ComposeProject --env-file $ProfilePath stop $Services
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to stop the previously running $label services of Compose project $ComposeProject before preflight."
    }
    $live = @(
        docker compose --project-name $ComposeProject --env-file $ProfilePath ps --quiet --status running --status paused --status restarting $Services |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to verify that the $label services of Compose project $ComposeProject are stopped before preflight."
    }
    if ($live.Count -gt 0) {
        throw "Compose project $ComposeProject still has $($live.Count) live $label container(s) after the stop. No preflight step was started."
    }
}

function Initialize-N8nIntakeWorkflow {
    param(
        [Parameter(Mandatory = $true)][string]$ComposeProject,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    $workflowId = "directorIntake"
    $workflowPath = Join-Path $RepositoryRoot "n8n\workflows\director-intake.json"
    if (-not (Test-Path -LiteralPath $workflowPath -PathType Leaf)) {
        throw "The n8n intake workflow is missing. Full-stack readiness cannot be established."
    }
    try {
        $workflowDefinition = Get-Content -LiteralPath $workflowPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The n8n intake workflow is not valid JSON. Full-stack readiness cannot be established."
    }
    if ([string]$workflowDefinition.id -cne $workflowId) {
        throw "The committed n8n intake workflow does not carry the required directorIntake identity."
    }
    $workflowMount = "${workflowPath}:/workflows/director-intake.json:ro"

    # Capability preflight: prove the selected n8n image's CLI exposes the exact
    # publish/unpublish commands in an ephemeral container before n8n is stopped,
    # n8n-db is started, or any workflow is unpublished, imported, or published.
    $helpOutput = @(
        docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T n8n --help |
            ForEach-Object { [string]$_ }
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to read the selected n8n image's command list. No workflow was modified." }
    Assert-N8nWorkflowPublishCapability -HelpOutput $helpOutput

    docker compose --project-name $ComposeProject --env-file $ProfilePath stop n8n
    if ($LASTEXITCODE -ne 0) { throw "Unable to stop n8n before deterministic workflow import." }
    docker compose --project-name $ComposeProject --env-file $ProfilePath up -d --wait --wait-timeout 120 n8n-db
    if ($LASTEXITCODE -ne 0) { throw "Unable to start the isolated n8n database." }

    $existingIds = @(
        docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T n8n list:workflow --onlyId |
            Where-Object { [string]$_ -match '^[A-Za-z0-9_-]+$' }
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect isolated n8n workflows." }
    if ($workflowId -in $existingIds) {
        docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T n8n unpublish:workflow --id=$workflowId
        if ($LASTEXITCODE -ne 0) { throw "Unable to unpublish the prior n8n intake workflow before replacement." }
    }

    docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T -v $workflowMount n8n import:workflow --input=/workflows/director-intake.json
    if ($LASTEXITCODE -ne 0) { throw "Unable to import the n8n intake workflow." }
    docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T n8n publish:workflow --id=$workflowId
    if ($LASTEXITCODE -ne 0) { throw "Unable to publish the n8n intake workflow." }

    $activeIds = @(
        docker compose --project-name $ComposeProject --env-file $ProfilePath run --rm --no-deps -T n8n list:workflow --active=true --onlyId |
            Where-Object { [string]$_ -match '^[A-Za-z0-9_-]+$' }
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to verify active isolated n8n workflows." }
    if (@($activeIds | Where-Object { [string]$_ -ceq $workflowId }).Count -ne 1) {
        throw "The isolated n8n intake workflow is not uniquely active."
    }
    Write-Host "The isolated n8n intake workflow was imported, published, and verified active."
}
if ($ApiOnly -and $ProfileName -notin @("edge-oracle", "long-table")) {
    throw "-ApiOnly is limited to the Edge Oracle recovery and Long Table canary profiles."
}

if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $profileTemplate -PathType Leaf)) {
        throw "Missing $profileTemplate. Pull the current main branch before running this script."
    }
    Copy-Item -LiteralPath $profileTemplate -Destination $profilePath
}

# Every invocation stages the paid gate closed in the profile, but containers
# started by an earlier activation keep their launch-time environment. Stop the
# previously running orchestrator and Slack intake, and prove they are stopped,
# before any profile validation, snapshot check, n8n mutation, image build,
# store inspection, or provider prevalidation can fail and leave a paid service
# live. They stay stopped until this script intentionally recreates the
# validated service further down.
Stop-PreviouslyRunningServices -ComposeProject $composeProject -ProfilePath $profilePath -Services @("orchestrator", "slackbot")
Write-Host "Previously running $displayName orchestrator and Slack intake are stopped; they stay stopped until the validated service is recreated."

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $profilePath "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to restrict $profilePath permissions before secret handling." }

$profileText = Get-Content -LiteralPath $profilePath -Raw
if ($profileText -notmatch '(?m)^SLACK_GATEWAY_SECRET=') {
    $profileText = $profileText.TrimEnd() + "`r`nSLACK_GATEWAY_SECRET=REPLACE_WITH_DISTINCT_RANDOM_SECRET`r`n"
}
foreach ($name in @("ORCHESTRATOR_SHARED_SECRET", "CLAUDE_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY", "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_REVIEW_SECRET", "SLACK_GATEWAY_SECRET", "LANGGRAPH_RUNTIME_WRITER_SECRET")) {
    if ($profileText -match "(?m)^$([Regex]::Escape($name))=REPLACE_WITH_(?:DISTINCT_)?RANDOM_SECRET\s*$") {
        $profileText = Set-DotEnvValue -Text $profileText -Name $name -Value (New-RandomSecret)
    }
}

if ($SeedProviderCredentialsFromPrimaryEnv -and
    -not [string]::IsNullOrWhiteSpace($ProviderCredentialSourceEnvPath)) {
    throw "Choose either -SeedProviderCredentialsFromPrimaryEnv or -ProviderCredentialSourceEnvPath, not both."
}
$providerCredentialSource = if ($SeedProviderCredentialsFromPrimaryEnv) {
    ".env"
}
else {
    $ProviderCredentialSourceEnvPath
}
if (-not [string]::IsNullOrWhiteSpace($providerCredentialSource)) {
    if (-not (Test-Path -LiteralPath $providerCredentialSource -PathType Leaf)) {
        throw "Cannot seed providers because the configured credential source does not exist."
    }
    $resolvedCredentialSource = (Resolve-Path -LiteralPath $providerCredentialSource).Path
    $resolvedProfilePath = (Resolve-Path -LiteralPath $profilePath).Path
    if ($resolvedCredentialSource -ceq $resolvedProfilePath) {
        throw "The provider credential source must be different from the target profile."
    }
    $primaryValues = Read-DotEnv -Path $resolvedCredentialSource
    # Credential values only; enable flags are never copied or synthesized here.
    $profileText = Copy-ProviderCredentialValues -SourceValues $primaryValues -ProfileText $profileText
    Write-Host "Provider credentials were copied locally from the configured source to $profilePath; values were not displayed."
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
    PRIMARY_TEAM = "openai,claude,cursor,grok"
    PRIMARY_WORKHORSE = "cheapest_capable"
    # USAGE_CHECK_PROVIDERS is deliberately not forced here. The profile's
    # explicit periodic-check membership is preserved and validated by
    # Get-ValidatedUsageCheckProviders before any paid dispatch.
    USAGE_CHECK_INTERVAL_MINUTES = "360"
    ALLOW_PAID_RUNS = "false"
    USAGE_CHECK_ENABLED = "false"
    ORACLE_AUTOMERGE_ENABLED = "false"
    ORACLE_AUTOMERGE_KILL_SWITCH = "true"
}
if ($ProfileName -eq "long-table") {
    # Every invocation stages all runtime writers closed before validation.
    # Only the explicit paid path reopens them after provider checks.
    $identity["LANGGRAPH_RUNTIME_ENABLED"] = "false"
    $identity["LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED"] = "false"
    $identity["LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED"] = "false"
    $identity["LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED"] = "false"
    $identity["LANGGRAPH_PARALLEL_KILL_SWITCH"] = "true"
}
$unboundIdentityNames = @(
    $identity.Keys | Where-Object { $composeEnvironmentNames -notcontains $_ }
)
if ($unboundIdentityNames.Count -gt 0) {
    throw ("Identity names are written to the profile but not bound into the " +
        "Compose environment, so an inherited value would win: " +
        ($unboundIdentityNames -join ", "))
}
foreach ($entry in $identity.GetEnumerator()) {
    $profileText = Set-DotEnvValue -Text $profileText -Name $entry.Key -Value $entry.Value
}

Write-Utf8NoBom -Path $profilePath -Text $profileText
$profileText = $null

$values = Read-DotEnv -Path $profilePath
Assert-ProfileIdentity -Values $values
Assert-LongTableSnapshot -Values $values
Assert-NoProfileCollisions -TargetValues $values
$requiredConfiguration = @(
    "ORCHESTRATOR_SHARED_SECRET", "OPENAI_API_KEY", "CLAUDE_ROUTINE_URL",
    "CLAUDE_ROUTINE_TOKEN", "CLAUDE_CALLBACK_SECRET", "CURSOR_API_KEY",
    "N8N_ENCRYPTION_KEY", "N8N_POSTGRES_PASSWORD"
)
if (-not $ApiOnly) {
    $requiredConfiguration += @(
        "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID",
        "SLACK_GATEWAY_SECRET"
    )
}
Assert-Configured -Values $values -Names $requiredConfiguration
if ($EnablePaidRuns) {
    # The operator owns the comparable-cost order. Preserve the ignored
    # profile value and fail closed instead of inventing or printing ranks.
    Assert-Configured -Values $values -Names @("TRUSTED_PROVIDER_COST_RANKS")
}
if ($ProfileName -eq "edge-oracle") {
    Assert-Configured -Values $values -Names @("ORACLE_AUTOMERGE_REVIEW_SECRET")
}
if ($ProfileName -eq "long-table") {
    Assert-Configured -Values $values -Names @("LANGGRAPH_RUNTIME_WRITER_SECRET")
    $secretNames = @(
        "ORCHESTRATOR_SHARED_SECRET", "CLAUDE_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY",
        "N8N_POSTGRES_PASSWORD", "ORACLE_AUTOMERGE_REVIEW_SECRET", "SLACK_GATEWAY_SECRET",
        "LANGGRAPH_RUNTIME_WRITER_SECRET"
    )
    $secretValues = @($secretNames | ForEach-Object { [string]$values[$_] })
    if (@($secretValues | Sort-Object -Unique).Count -ne $secretValues.Count) {
        throw "$profilePath reuses a Long Table internal secret. No service was started."
    }
}

# Assert-ProfileIdentity already proved the profile names the selected Compose
# project, so $composeProject keeps the value derived before validation.
$hostPort = [int]$values["DIRECTOR_HOST_PORT"]
[Environment]::SetEnvironmentVariable("DIRECTOR_ENV_FILE", $profilePath, "Process")
Sync-ComposeEnvironment -Values $values

Write-Host "Using isolated $displayName profile $profilePath, Compose project $composeProject, and host port $hostPort."
docker compose --project-name $composeProject --env-file $profilePath config --quiet
if ($LASTEXITCODE -ne 0) { throw "Docker Compose configuration validation failed." }

if ($ProfileName -eq "long-table" -and -not $ApiOnly) {
    Initialize-N8nIntakeWorkflow `
        -ComposeProject $composeProject `
        -ProfilePath $profilePath `
        -RepositoryRoot $repoRoot
}

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

# Provider partition preflight. The paid gate, usage checks, and the manual
# usage-check endpoint stay closed until the profile's explicit
# USAGE_CHECK_PROVIDERS is valid: no duplicates or unknown names, every
# mandatory primary provider present, and every listed optional provider
# explicitly enabled with a configured credential. The endpoint response must
# later partition exactly this validated set.
$expectedUsageProviders = @(Get-ValidatedUsageCheckProviders -Values $values -ProfileLabel $profilePath)
Write-Host "Validated usage-check providers before paid dispatch: $($expectedUsageProviders -join ', ')."

# Provider probes require the paid gate, but the live writer and parallel gate
# remain closed until that evidence succeeds.
Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $false
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
    $runStatus = [string]$accepted.status
    $partition = Assert-UsageProviderPartition `
        -ScheduledProviders $accepted.scheduled_providers `
        -CoolingDownProviders $accepted.cooling_down_providers `
        -ExpectedPrimary $expectedUsageProviders
    $scheduledProviders = @($partition.ScheduledProviders)
    $coolingProviders = @($partition.CoolingDownProviders)
    $completionVerifiedProviders = @(
        Get-CompletionVerifiedProviders -ProfileName $ProfileName
    )

    if ($runStatus -ceq "not_due") {
        Write-Host (
            "No usage-check providers are due under the 15-minute minimum. " +
            "Verifying recent successful evidence for: $($coolingProviders -join ', ')."
        )
        $latest = @(Invoke-RestMethod -Uri "${baseUrl}/v1/usage-checks/latest" -Method Get -Headers $headers -TimeoutSec 30 | ForEach-Object { $_ })
        $evidence = Assert-CoolingProviderEvidence `
            -Providers $coolingProviders `
            -LatestRecords $latest `
            -Now ([DateTimeOffset]::UtcNow) `
            -CompletionVerifiedProviders $completionVerifiedProviders
        $evidence | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at | Format-Table -AutoSize
        Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $true
        if ($ApiOnly) {
            docker compose --project-name $composeProject --env-file $profilePath stop slackbot n8n n8n-db
            if ($LASTEXITCODE -ne 0) { throw "Unable to stop optional services after cooldown verification." }
            docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans --force-recreate orchestrator
        }
        else {
            docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans --force-recreate
        }
        if ($LASTEXITCODE -ne 0) { throw "Cooldown evidence accepted, but the complete isolated stack did not start." }
        $health = Wait-DirectorHealth -Port $hostPort -Timeout ([Math]::Min($TimeoutSeconds, 180))
        Assert-LongTableGraduationReady -Health $health -Values $values
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
        -StartedAt $startedAt `
        -CompletionVerifiedProviders $completionVerifiedProviders
    $coolingEvidence = Assert-CoolingProviderEvidence `
        -Providers $coolingProviders `
        -LatestRecords $latest `
        -Now ([DateTimeOffset]::UtcNow) `
        -CompletionVerifiedProviders $completionVerifiedProviders
    $allEvidence = @($scheduledEvidence + $coolingEvidence)
    $allEvidence | Sort-Object provider | Select-Object provider, status, total_tokens, checked_at | Format-Table -AutoSize

    Write-ProfileSwitches -PaidRuns $true -UsageChecks $true -RuntimeGraduation $true
    if ($ApiOnly) {
        docker compose --project-name $composeProject --env-file $profilePath stop slackbot n8n n8n-db
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop optional services after provider checks." }
        docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans --force-recreate orchestrator
    }
    else {
        docker compose --project-name $composeProject --env-file $profilePath up -d --build --remove-orphans --force-recreate
    }
    if ($LASTEXITCODE -ne 0) { throw "Provider checks passed, but the complete isolated stack did not start." }
    $health = Wait-DirectorHealth -Port $hostPort -Timeout ([Math]::Min($TimeoutSeconds, 180))
    Assert-LongTableGraduationReady -Health $health -Values $values
}
catch {
    $failure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    $switchesClosed = $true
    try { Write-ProfileSwitches -PaidRuns $false -UsageChecks $false -RuntimeGraduation $false }
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
catch {
    $outerFailure = $_
    if ($ProfileName -eq "long-table") {
        $outerRollbackErrors = New-Object System.Collections.Generic.List[string]
        if ($null -ne $profilePath -and (Test-Path -LiteralPath $profilePath)) {
            try {
                Write-ProfileSwitches -PaidRuns $false -UsageChecks $false -RuntimeGraduation $false
            }
            catch {
                $outerRollbackErrors.Add("the Long Table profile switches could not be closed")
            }
        }
        if ($null -ne $composeProject -and $composeProject) {
            docker compose --project-name $composeProject --env-file $profilePath stop orchestrator slackbot n8n n8n-db | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $outerRollbackErrors.Add("the previously running Long Table services could not be stopped")
            }
        }
        if ($outerRollbackErrors.Count -gt 0) {
            throw "Activation failed: $($outerFailure.Exception.Message). Early fail-closed rollback was incomplete: $($outerRollbackErrors -join '; ')."
        }
    }
    throw $outerFailure
}
finally {
    Set-Location $originalLocation
    foreach ($name in $composeEnvironmentNames) {
        $original = $originalProcessEnvironment[$name]
        if ($original.Present) {
            [Environment]::SetEnvironmentVariable($name, [string]$original.Value, "Process")
            if (-not (Test-Path -LiteralPath "Env:${name}")) {
                # Windows PowerShell 5.1 cannot hold an empty process variable:
                # .NET, Set-Item, assignment and New-Item all delete the name
                # instead. An inherited empty gate is a real override, because an
                # empty export still beats --env-file, so losing it silently
                # would let the next Compose run read the profile value instead.
                Write-Warning ("Could not restore the inherited empty ${name}. " +
                    "This PowerShell edition cannot represent an empty process " +
                    "variable, so that override is gone; re-export it before the " +
                    "next Compose run if it was deliberate.")
            }
        }
        else {
            Clear-ProcessEnvironmentVariable -Name $name
        }
    }
}
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 19:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 09:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:54 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-31 | 10:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:38 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 17:12 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 18:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 19:17 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 19:34 UTC | END]]
