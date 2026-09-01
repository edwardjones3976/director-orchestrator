# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:02 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "Migrate", "Backup", "RestoreDrill", "Health", "Activate", "Rollback")]
    [string]$Action,
    [Parameter(Mandatory = $true)][string]$EnvironmentFile,
    [Parameter(Mandatory = $true)][string]$ComposeProject,
    [Parameter(Mandatory = $true)][ValidateRange(1024, 65535)][int]$HostPort,
    [string]$BackupPath,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') { $values[$matches[1]] = $matches[2] }
    }
    return $values
}

function Set-DotEnvValue {
    param([string]$Text, [string]$Name, [string]$Value)
    $pattern = "(?m)^$([Regex]::Escape($Name))=.*$"
    if ([Regex]::IsMatch($Text, $pattern)) {
        return [Regex]::Replace($Text, $pattern, "$Name=$($Value.Replace('$', '$$'))")
    }
    return $Text.TrimEnd() + [Environment]::NewLine + "$Name=$Value" + [Environment]::NewLine
}

function Write-Switches {
    param([hashtable]$Switches)
    $text = Get-Content -LiteralPath $script:environmentPath -Raw
    foreach ($entry in $Switches.GetEnumerator()) {
        $text = Set-DotEnvValue -Text $text -Name $entry.Key -Value ([string]$entry.Value)
    }
    [IO.File]::WriteAllText(
        $script:environmentPath,
        $text,
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose --project-name $script:ComposeProject --env-file $script:environmentPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed." }
}

function Wait-Health {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($script:TimeoutSeconds)
    do {
        try { return Invoke-RestMethod "http://127.0.0.1:$script:HostPort/health" -TimeoutSec 5 }
        catch { Start-Sleep -Seconds 3 }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Director health did not become available before the deadline."
}

$environmentPath = (Resolve-Path -LiteralPath $EnvironmentFile).Path
if ($ComposeProject -notmatch '^[a-z0-9][a-z0-9_-]+$') { throw "Invalid Compose project identity." }
$values = Read-DotEnv -Path $environmentPath
$required = @(
    "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD", "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA",
    "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE", "LANGGRAPH_PRIMARY_OWNER_TOKEN",
    "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT", "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
    "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON",
    "LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS",
    "LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA", "LANGGRAPH_PRIMARY_PINNED_TREE_SHA",
    "REPOSITORY_SNAPSHOT_HOST_PATH", "REPOSITORY_SNAPSHOT_ROOT",
    "REPOSITORY_ALLOWLIST", "ALLOWED_STARTING_REFS"
)
$missing = @($required | Where-Object {
    -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
    ([string]$values[$_]).StartsWith("REPLACE_WITH_")
})
if ($missing.Count -gt 0) {
    throw "Primary configuration is incomplete: $($missing -join ', '). No values were printed."
}
$requiredProviders = @(
    "openai", "claude", "cursor", "grok", "antigravity", "perplexity",
    "firecrawl", "github-copilot", "windows-copilot"
)
$configuredProviders = @(([string]$values["LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS"]).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (@(Compare-Object ($requiredProviders | Sort-Object) ($configuredProviders | Sort-Object)).Count -ne 0) {
    throw "Primary required-provider configuration must contain the exact nine provider lanes."
}
foreach ($secret in @(
    "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD", "LANGGRAPH_PRIMARY_OWNER_TOKEN",
    "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"
)) {
    if ([string]$values[$secret].Length -lt 32) { throw "$secret is too short." }
}
$secretValues = @(
    $values["LANGGRAPH_PRIMARY_POSTGRES_PASSWORD"],
    $values["LANGGRAPH_PRIMARY_OWNER_TOKEN"],
    $values["LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET"],
    $values["LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET"],
    $values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]
)
if (@($secretValues | Sort-Object -Unique).Count -ne $secretValues.Count) {
    throw "Primary internal secrets must be pairwise distinct."
}
if ([string]$values["LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA"] -notmatch '^[0-9a-f]{40}$' -or
    [string]$values["LANGGRAPH_PRIMARY_PINNED_TREE_SHA"] -notmatch '^[0-9a-f]{40}$') {
    throw "Primary repository authority must have exact commit and tree pins."
}

$snapshot = [string]$values["REPOSITORY_SNAPSHOT_HOST_PATH"]
& "$PSScriptRoot\windows-primary-snapshot.ps1" -Action Verify -Source $snapshot -Target $snapshot `
    -ExpectedRepository ([string]$values["REPOSITORY_ALLOWLIST"]) `
    -StartingRef ([string]$values["ALLOWED_STARTING_REFS"]) `
    -ExpectedCommit ([string]$values["LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA"]) `
    -ExpectedTree ([string]$values["LANGGRAPH_PRIMARY_PINNED_TREE_SHA"])

$env:DIRECTOR_ENV_FILE = $environmentPath
$env:DIRECTOR_COMPOSE_PROJECT_NAME = $ComposeProject
Invoke-Compose config --quiet

if ($Action -eq "Preflight") {
    Write-Host "Primary preflight passed; no service or provider was started."
    return
}
if ($Action -eq "Health") {
    $health = Wait-Health
    $health.langgraph_primary | ConvertTo-Json -Depth 8
    return
}

if ($Action -eq "Migrate") {
    Invoke-Compose up -d langgraph-primary-db
    Invoke-Compose build orchestrator
    Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres); print('schema_current=true'); h.close()"
    Write-Host "Primary Postgres schema migration/verification completed."
    return
}

if ($Action -eq "Backup") {
    if ([string]::IsNullOrWhiteSpace($BackupPath)) { throw "-BackupPath is required." }
    $destination = [IO.Path]::GetFullPath($BackupPath)
    if (Test-Path -LiteralPath $destination) { throw "Backup destination already exists." }
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $destination) -PathType Container)) {
        throw "Backup destination parent does not exist."
    }
    $container = [string](@(Invoke-Compose ps -q langgraph-primary-db) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($container)) { throw "Primary Postgres container is unavailable." }
    $temporary = "/tmp/director-primary-$([Guid]::NewGuid().ToString('N')).dump"
    try {
        & docker exec $container pg_dump -U director_primary -d director_primary --format=custom --file=$temporary
        if ($LASTEXITCODE -ne 0) { throw "Postgres backup failed." }
        & docker cp "${container}:${temporary}" $destination
        if ($LASTEXITCODE -ne 0) { throw "Postgres backup export failed." }
    }
    finally { & docker exec $container rm -f -- $temporary | Out-Null }
    Write-Host "Primary Postgres backup completed without displaying credentials."
    return
}

if ($Action -eq "RestoreDrill") {
    if ([string]::IsNullOrWhiteSpace($BackupPath)) { throw "-BackupPath is required." }
    $source = (Resolve-Path -LiteralPath $BackupPath).Path
    $container = [string](@(Invoke-Compose ps -q langgraph-primary-db) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($container)) { throw "Primary Postgres container is unavailable." }
    $database = "director_restore_$([Guid]::NewGuid().ToString('N').Substring(0,12))"
    $temporary = "/tmp/${database}.dump"
    try {
        & docker cp $source "${container}:${temporary}"
        if ($LASTEXITCODE -ne 0) { throw "Restore-drill import failed." }
        & docker exec $container createdb -U director_primary $database
        if ($LASTEXITCODE -ne 0) { throw "Restore-drill database creation failed." }
        & docker exec $container pg_restore -U director_primary -d $database --exit-on-error $temporary
        if ($LASTEXITCODE -ne 0) { throw "Restore drill failed." }
        & docker exec $container psql -U director_primary -d $database -v ON_ERROR_STOP=1 -Atc `
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = '$($values['LANGGRAPH_PRIMARY_POSTGRES_SCHEMA'])';"
        if ($LASTEXITCODE -ne 0) { throw "Restored schema verification failed." }
    }
    finally {
        & docker exec $container dropdb -U director_primary --if-exists $database | Out-Null
        & docker exec $container rm -f -- $temporary | Out-Null
    }
    Write-Host "Primary Postgres restore drill completed in a disposable database."
    return
}

if ($Action -eq "Activate") {
    Invoke-Compose stop slackbot
    Write-Switches @{
        ALLOW_PAID_RUNS = "true"
        LANGGRAPH_PRIMARY_ENABLED = "true"
        LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "true"
        LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED = "false"
        LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "true"
        LANGGRAPH_PARALLEL_KILL_SWITCH = "false"
    }
    Invoke-Compose up -d --build --force-recreate orchestrator
    $primary = (Wait-Health).langgraph_primary
    foreach ($field in @(
        "primary_graph_constructed", "primary_router_wired", "checkpoint_backend_postgres",
        "checkpoint_schema_current", "claim_store_ready", "effect_ledger_ready",
        "writer_lease_ready", "reconciliation_worker_ready", "primary_runtime_active",
        "legacy_new_intake_disabled"
    )) {
        if (-not [bool]$primary.$field) { throw "Primary activation health failed at $field; Slack remains stopped." }
    }
    if ([string]$primary.new_intake_owner -cne "langgraph" -or
        -not [bool]$primary.provider_activation.combined_live_ready -or
        [int]$primary.ambiguous_effect_count -ne 0) {
        throw "Primary activation ownership/provider/reconciliation proof failed; Slack remains stopped."
    }
    Invoke-Compose up -d slackbot
    Write-Host "Primary activation health passed and Slack intake was reopened."
    return
}

if ($Action -eq "Rollback") {
    Invoke-Compose stop slackbot orchestrator
    Write-Switches @{
        ALLOW_PAID_RUNS = "false"
        LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "false"
        LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "false"
        LANGGRAPH_PARALLEL_KILL_SWITCH = "true"
    }
    Start-Sleep -Seconds 65
    Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres); c=h.claim_store.current_intake_authority(); m=h.claim_store.primary_control_plane_metrics(); assert m.ambiguous_effect_count == 0, 'ambiguous effects block rollback'; n=h.claim_store.acquire_intake_authority(intake_owner='legacy', owner_token=s.langgraph_primary_owner_token, expected_generation=c.fencing_generation); print('legacy_new_intake_disabled='+str(n.legacy_new_intake_disabled).lower()); h.close()"
    Write-Switches @{ LANGGRAPH_PRIMARY_ENABLED = "false" }
    Invoke-Compose up -d --build --force-recreate orchestrator
    $null = Wait-Health
    Invoke-Compose up -d slackbot
    Write-Host "Fenced rollback returned new intake to legacy; primary evidence was preserved."
}
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:02 UTC | END]]
