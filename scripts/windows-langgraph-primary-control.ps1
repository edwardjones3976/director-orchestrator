# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:02 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "Health", "Activate", "Rollback")]
    [string]$Action,
    [Parameter(Mandatory = $true)][string]$EnvironmentFile,
    [Parameter(Mandatory = $true)][string]$ComposeProject,
    [Parameter(Mandatory = $true)][ValidateRange(1024, 65535)][int]$HostPort,
    [string]$BackupPath,
    [string]$GitHubRepository,
    [ValidateRange(1, [int]::MaxValue)][int]$PullRequestNumber,
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

function Get-Sha256Text {
    param([string]$Text)
    return [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Text))
    ).ToLowerInvariant()
}

function Get-TrustedActorAllowlist {
    param([string]$Name)
    if (-not $script:values.ContainsKey($Name)) {
        throw "Trusted activation reviewer allowlist is missing: $Name."
    }
    $raw = ([string]$script:values[$Name]).Trim()
    if (-not $raw.StartsWith("[") -or -not $raw.EndsWith("]")) {
        throw "Trusted activation reviewer allowlist must be a JSON array: $Name."
    }
    try { $actors = @(($raw | ConvertFrom-Json)) }
    catch { throw "Trusted activation reviewer allowlist is invalid: $Name." }
    if ($actors.Count -lt 1 -or @($actors | Where-Object {
        $_ -isnot [string] -or $_ -cnotmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$'
    }).Count -gt 0 -or @($actors | Sort-Object -Unique).Count -ne $actors.Count) {
        throw "Trusted activation reviewer allowlist is invalid: $Name."
    }
    return @($actors)
}

function Get-ExactReviewArtifact {
    param([object]$Review, [string]$Kind, [string[]]$TrustedActors)
    if ($null -eq $Review -or [string]$Review.state -cne "APPROVED" -or
        [string]$Review.authorAssociation -notin @("OWNER", "MEMBER", "COLLABORATOR")) {
        return $null
    }
    $actor = [string]$Review.author.login
    if ($TrustedActors -cnotcontains $actor) { return $null }
    $expected = [ordered]@{
        actor = $actor
        head_sha = $script:candidateCommit
        kind = $Kind
        schema_version = "director-activation-review/v1"
        verdict = "ACCEPT"
    }
    $canonical = $expected | ConvertTo-Json -Compress
    if ([string]$Review.body -cne $canonical) { return $null }
    try { $artifact = [string]$Review.body | ConvertFrom-Json }
    catch { return $null }
    $expectedNames = @("actor", "head_sha", "kind", "schema_version", "verdict")
    $observedNames = @($artifact.PSObject.Properties.Name | Sort-Object)
    if (($observedNames -join "|") -cne (($expectedNames | Sort-Object) -join "|")) {
        return $null
    }
    return [pscustomobject]@{ actor = $actor; body = $canonical }
}

function Get-GitHubAcceptanceFacts {
    if ([string]::IsNullOrWhiteSpace($script:GitHubRepository) -or $script:PullRequestNumber -lt 1) {
        throw "Accept requires -GitHubRepository and -PullRequestNumber."
    }
    if ($script:GitHubRepository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw "GitHub repository identity is invalid."
    }
    if ($null -eq (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI is required to verify exact-head acceptance evidence."
    }
    $json = & gh pr view $script:PullRequestNumber --repo $script:GitHubRepository `
        --json headRefOid,author,reviews,statusCheckRollup
    if ($LASTEXITCODE -ne 0) { throw "GitHub acceptance evidence could not be read." }
    $pull = $json | ConvertFrom-Json -Depth 20
    if ([string]$pull.headRefOid -cne $script:candidateCommit) {
        throw "Pull request head does not match the exact activation candidate."
    }
    $requiredChecks = @(
        "Contribution attribution", "Python 3.12 (ubuntu-latest)",
        "Python 3.12 (windows-latest)", "Compose and container build",
        "Secret hygiene", "Cross-process recovery probe"
    )
    $checks = @($pull.statusCheckRollup)
    foreach ($name in $requiredChecks) {
        $matching = @($checks | Where-Object { [string]$_.name -ceq $name })
        if ($matching.Count -lt 1 -or @($matching | Where-Object {
            [string]$_.status -cne "COMPLETED" -or [string]$_.conclusion -cne "SUCCESS"
        }).Count -gt 0) {
            throw "Exact-head required CI is incomplete or unsuccessful at $name."
        }
    }
    if (@($checks | Where-Object {
        ([string]$_.status -and [string]$_.status -cne "COMPLETED") -or
        ([string]$_.conclusion -and [string]$_.conclusion -cne "SUCCESS")
    }).Count -gt 0) {
        throw "Exact-head check rollup contains a pending, skipped, or unsuccessful result."
    }
    $runIds = @($checks | ForEach-Object {
        if ([string]$_.detailsUrl -match '/actions/runs/(\d+)') { [long]$matches[1] }
    } | Sort-Object -Unique)
    if ($runIds.Count -lt 1) { throw "Exact-head CI run identity is unavailable." }

    $securityActors = Get-TrustedActorAllowlist `
        "LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON"
    $independentActors = Get-TrustedActorAllowlist `
        "LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON"
    if (@($securityActors | Where-Object { $independentActors -ccontains $_ }).Count -gt 0) {
        throw "Security and independent activation reviewer allowlists must be disjoint."
    }
    $security = @($pull.reviews | ForEach-Object {
        Get-ExactReviewArtifact $_ "security" $securityActors
    } | Where-Object { $null -ne $_ })
    $independent = @($pull.reviews | ForEach-Object {
        Get-ExactReviewArtifact $_ "independent_review" $independentActors
    } | Where-Object { $null -ne $_ })
    if ($security.Count -ne 1 -or [string]::IsNullOrWhiteSpace($security[0].actor)) {
        throw "An attributable exact-head security ACCEPT artifact is required."
    }
    if ($independent.Count -ne 1 -or [string]::IsNullOrWhiteSpace($independent[0].actor)) {
        throw "An attributable exact-head independent-review ACCEPT artifact is required."
    }
    if ([string]$security[0].actor -ieq [string]$independent[0].actor) {
        throw "Security and independent acceptance require distinct reviewers."
    }
    if ([string]$security[0].actor -ieq [string]$pull.author.login -or
        [string]$independent[0].actor -ieq [string]$pull.author.login) {
        throw "The pull request author cannot self-accept the activation candidate."
    }
    $ciDigest = Get-Sha256Text (($checks | ConvertTo-Json -Depth 10 -Compress))
    $securityDigest = Get-Sha256Text $security[0].body
    $independentDigest = Get-Sha256Text $independent[0].body
    return @{
        candidate_commit = $script:candidateCommit
        candidate_tree = $script:candidateTree
        remote_head_commit = [string]$pull.headRefOid
        ci_conclusion = "SUCCESS"
        ci_required_check_count = $requiredChecks.Count
        ci_run_ids = $runIds
        ci_evidence_digest = $ciDigest
        security_verdict = "ACCEPT"
        security_reviewer = [string]$security[0].actor
        security_reviewed_commit = $script:candidateCommit
        security_evidence_digest = $securityDigest
        independent_review_verdict = "ACCEPT"
        independent_reviewer = [string]$independent[0].actor
        independent_reviewed_commit = $script:candidateCommit
        independent_review_evidence_digest = $independentDigest
    }
}

function Get-DatabaseBinding {
    $output = @(Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres); print(h.storage_identity+'|'+h.database_instance_id); h.close()")
    $binding = [string](@($output | Where-Object { $_ -match '^postgres:sha256:[0-9a-f]{64}\|[0-9a-f-]{36}$' }) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($binding)) { throw "Primary database instance binding could not be established." }
    return $binding.Split('|')
}

function Get-LegacyInventory {
    $output = @(Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator -c `
        "import hashlib,json,sqlite3; from director_orchestrator.config import Settings; s=Settings(); c=sqlite3.connect(s.sqlite_path); rows=c.execute('SELECT task_id,status FROM tasks ORDER BY task_id').fetchall(); c.close(); payload=json.dumps(rows,separators=(',',':')); print(str(len(rows))+'|'+hashlib.sha256(payload.encode()).hexdigest())")
    $inventory = [string](@($output | Where-Object { $_ -match '^\d+\|[0-9a-f]{64}$' }) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($inventory)) { throw "Legacy task ownership inventory could not be frozen." }
    return $inventory.Split('|')
}

function Stop-And-Prove-AllIntake {
    Invoke-Compose stop slackbot n8n orchestrator
    foreach ($service in @("slackbot", "n8n", "orchestrator")) {
        $running = [string](@(Invoke-Compose ps -q $service) | Select-Object -Last 1)
        if (-not [string]::IsNullOrWhiteSpace($running)) {
            throw "Authenticated intake surface remains active: $service"
        }
    }
}

function Invoke-Evidence {
    param(
        [ValidateSet("record", "require-ready", "verify-backup", "bind-legacy-inventory")][string]$Command,
        [string]$Stage,
        [string]$EvidenceDigest,
        [string]$FactsJson = "{}",
        [string]$BackupDigest,
        [string]$LegacyInventoryDigest,
        [int]$LegacyTaskCount
    )
    $arguments = @(
        "-m", "director_orchestrator.primary_activation_evidence", $Command,
        "--artifact", "/data/langgraph-primary-activation-evidence.json",
        "--candidate-commit", $script:candidateCommit,
        "--candidate-tree", $script:candidateTree,
        "--profile-id", ([string]$script:values["APP_ENV"]),
        "--compose-project", $script:ComposeProject,
        "--database-identity", $script:databaseIdentity,
        "--database-instance-id", $script:databaseInstanceId,
        "--postgres-schema", ([string]$script:values["LANGGRAPH_PRIMARY_POSTGRES_SCHEMA"]),
        "--checkpoint-namespace", ([string]$script:values["LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE"])
    )
    if ($Command -eq "record") {
        $arguments += @("--stage", $Stage, "--evidence-digest", $EvidenceDigest, "--facts-json", $FactsJson)
    }
    elseif ($Command -eq "verify-backup") {
        $arguments += @("--backup-digest", $BackupDigest)
    }
    elseif ($Command -eq "bind-legacy-inventory") {
        $arguments += @("--legacy-inventory-digest", $LegacyInventoryDigest, "--legacy-task-count", [string]$LegacyTaskCount)
    }
    Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator @arguments
}

$environmentPath = (Resolve-Path -LiteralPath $EnvironmentFile).Path
if ($ComposeProject -notmatch '^[a-z0-9][a-z0-9_-]+$') { throw "Invalid Compose project identity." }
$values = Read-DotEnv -Path $environmentPath
$required = @(
    "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD", "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA",
    "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE", "LANGGRAPH_PRIMARY_OWNER_TOKEN",
    "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON",
    "LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON",
    "LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON",
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
    "firecrawl", "github-copilot", "microsoft-365-copilot"
)
$configuredProviders = @(([string]$values["LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS"]).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (@(Compare-Object ($requiredProviders | Sort-Object) ($configuredProviders | Sort-Object)).Count -ne 0) {
    throw "Primary required-provider configuration must contain the exact nine provider lanes."
}
foreach ($secret in @(
    "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD", "LANGGRAPH_PRIMARY_OWNER_TOKEN",
    "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"
)) {
    if ([string]$values[$secret].Length -lt 32) { throw "$secret is too short." }
}
$secretValues = @(
    $values["LANGGRAPH_PRIMARY_POSTGRES_PASSWORD"],
    $values["LANGGRAPH_PRIMARY_OWNER_TOKEN"],
    $values["LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET"],
    $values["LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET"],
    $values["LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET"],
    $values["LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET"],
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
$candidateCommit = [string](@(& git rev-parse --verify 'HEAD^{commit}') | Select-Object -Last 1)
$candidateTree = [string](@(& git rev-parse --verify 'HEAD^{tree}') | Select-Object -Last 1)
if ($candidateCommit -notmatch '^[0-9a-f]{40}$' -or $candidateTree -notmatch '^[0-9a-f]{40}$') {
    throw "The Director candidate commit/tree cannot be frozen."
}
Invoke-Compose build orchestrator
# Establish the durable database-instance UUID before any activation evidence is
# recorded. This is the only service Preflight may start.
Invoke-Compose up -d langgraph-primary-db
$databaseBinding = Get-DatabaseBinding
$databaseIdentity = [string]$databaseBinding[0]
$databaseInstanceId = [string]$databaseBinding[1]

if ($Action -eq "Preflight") {
    $digest = Get-Sha256Text "$candidateCommit|$candidateTree|$ComposeProject|$databaseIdentity|preflight"
    Invoke-Evidence -Command record -Stage PREFLIGHT -EvidenceDigest $digest -FactsJson '{"compose_config_valid":true,"snapshot_exact":true}'
    Write-Host "Primary preflight passed; no service or provider was started."
    return
}
if ($Action -eq "Accept") {
    $facts = Get-GitHubAcceptanceFacts
    $factsJson = $facts | ConvertTo-Json -Depth 8 -Compress
    $digest = Get-Sha256Text $factsJson
    Invoke-Evidence -Command record -Stage ACCEPTED -EvidenceDigest $digest -FactsJson $factsJson
    Write-Host "Exact-head CI, security, and independent-review acceptance evidence was bound."
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
    $digest = Get-Sha256Text "$candidateCommit|$ComposeProject|$databaseIdentity|migrated"
    Invoke-Evidence -Command record -Stage MIGRATED -EvidenceDigest $digest -FactsJson '{"schema_current":true}'
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
    $backupDigest = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    $digest = Get-Sha256Text "$candidateCommit|$ComposeProject|$databaseIdentity|$backupDigest"
    Invoke-Evidence -Command record -Stage BACKED_UP -EvidenceDigest $digest -FactsJson ("{`"backup_digest`":`"sha256:$backupDigest`"}")
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
        $hashProof = [string](@(& docker exec $container sha256sum -- $temporary) | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or $hashProof -notmatch '^([0-9a-f]{64})\s+') {
            throw "Restore-drill artifact could not be hashed."
        }
        $restoreDigest = [string]$matches[1]
        Invoke-Evidence -Command verify-backup -BackupDigest "sha256:$restoreDigest"
        & docker exec $container createdb -U director_primary $database
        if ($LASTEXITCODE -ne 0) { throw "Restore-drill database creation failed." }
        & docker exec $container pg_restore -U director_primary -d $database --exit-on-error $temporary
        if ($LASTEXITCODE -ne 0) { throw "Restore drill failed." }
        $schema = [string]$values['LANGGRAPH_PRIMARY_POSTGRES_SCHEMA']
        $proof = [string](@(& docker exec $container psql -U director_primary -d $database -v ON_ERROR_STOP=1 -Atc `
            "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = '$schema' AND table_name IN ('director_primary_control_plane','director_primary_intake_authority','director_primary_task_projection','director_primary_task_writer','director_primary_claim','director_primary_effect_reservation','director_primary_effect_approval_binding','director_primary_typed_effect_intent','director_primary_effect_execution','director_primary_reconciliation_event','director_primary_terminal_evidence','director_provider_activation_evidence','director_provider_activation_authorization','director_primary_parallel_batch','director_primary_parallel_assignment','director_primary_provider_attempt')) || '|' || (SELECT count(*) FROM information_schema.tables WHERE table_schema = '$schema' AND table_name IN ('checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations')) || '|' || (SELECT count(*) FROM $schema.director_primary_control_plane WHERE database_instance_id = '$databaseInstanceId');" ) | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or $proof -notmatch '^(\d+)\|(\d+)\|(\d+)$') { throw "Restored schema verification failed." }
        $requiredTableCount = [int]$matches[1]
        $checkpointTableCount = [int]$matches[2]
        $schemaEvidenceRows = [int]$matches[3]
        if ($requiredTableCount -ne 15 -or $checkpointTableCount -ne 4 -or $schemaEvidenceRows -lt 1) {
            throw "Restore drill lacks required control-plane/checkpoint tables or schema evidence."
        }
    }
    finally {
        & docker exec $container dropdb -U director_primary --if-exists $database | Out-Null
        & docker exec $container rm -f -- $temporary | Out-Null
    }
    $facts = "{`"backup_digest`":`"sha256:$restoreDigest`",`"required_control_plane_tables`":$requiredTableCount,`"checkpoint_tables`":$checkpointTableCount,`"schema_evidence_rows`":$schemaEvidenceRows}"
    $digest = Get-Sha256Text "$candidateCommit|$ComposeProject|$databaseIdentity|$facts|restore"
    Invoke-Evidence -Command record -Stage RESTORE_PROVED -EvidenceDigest $digest -FactsJson $facts
    Write-Host "Primary Postgres restore drill completed in a disposable database."
    return
}

if ($Action -eq "Activate") {
    Stop-And-Prove-AllIntake
    $legacyInventory = Get-LegacyInventory
    Invoke-Evidence -Command bind-legacy-inventory -LegacyTaskCount ([int]$legacyInventory[0]) -LegacyInventoryDigest ([string]$legacyInventory[1])
    Invoke-Evidence -Command require-ready
    try {
        Write-Switches @{ ALLOW_PAID_RUNS = "true"; LANGGRAPH_PRIMARY_ENABLED = "true"; LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "true"; LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED = "false"; LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "true"; LANGGRAPH_PARALLEL_KILL_SWITCH = "false" }
        Invoke-Compose up -d --build --force-recreate orchestrator
        $primary = (Wait-Health).langgraph_primary
        foreach ($field in @("primary_graph_constructed", "primary_router_wired", "checkpoint_backend_postgres", "checkpoint_schema_current", "claim_store_ready", "effect_ledger_ready", "writer_lease_ready", "reconciliation_worker_ready", "primary_runtime_active", "legacy_new_intake_disabled")) {
            if (-not [bool]$primary.$field) { throw "Primary activation health failed at $field." }
        }
        if ([string]$primary.new_intake_owner -cne "langgraph" -or -not [bool]$primary.provider_activation.combined_live_ready -or [int]$primary.ambiguous_effect_count -ne 0) { throw "Primary activation ownership/provider/reconciliation proof failed." }
        $digest = Get-Sha256Text "$candidateCommit|$ComposeProject|$databaseIdentity|$databaseInstanceId|$($primary.intake_fencing_generation)|activated"
        Invoke-Evidence -Command record -Stage ACTIVATED -EvidenceDigest $digest -FactsJson ("{`"intake_fencing_generation`":$([int]$primary.intake_fencing_generation)}")
        Invoke-Compose up -d n8n slackbot
        Write-Host "Primary activation health passed and authenticated intake was reopened."
        return
    }
    catch {
        try { Invoke-Compose stop orchestrator }
        finally { Write-Switches @{ ALLOW_PAID_RUNS = "false"; LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "false"; LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "false"; LANGGRAPH_PARALLEL_KILL_SWITCH = "true" } }
        throw "Primary activation failed and was unwound; Slack, API, and n8n intake remain stopped. $($_.Exception.Message)"
    }
}

if ($Action -eq "Rollback") {
    Stop-And-Prove-AllIntake
    Write-Switches @{
        ALLOW_PAID_RUNS = "false"
        LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "false"
        LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "false"
        LANGGRAPH_PARALLEL_KILL_SWITCH = "true"
    }
    Start-Sleep -Seconds 65
    Invoke-Compose run --rm --no-deps -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres); c=h.claim_store.current_intake_authority(); m=h.claim_store.primary_control_plane_metrics(); q=h.claim_store.primary_quiescence_metrics(); (_ for _ in ()).throw(RuntimeError('ambiguous effects block rollback')) if m.ambiguous_effect_count != 0 else None; (_ for _ in ()).throw(RuntimeError('active provider dispatches, assignments, writer claims, reconciliation, or claims block rollback')) if not q.rollback_safe else None; n=h.claim_store.acquire_intake_authority(intake_owner='legacy', owner_token=s.langgraph_primary_owner_token, expected_generation=c.fencing_generation); (_ for _ in ()).throw(RuntimeError('legacy intake CAS did not complete')) if n.legacy_new_intake_disabled else None; h.close()"
    Write-Switches @{ LANGGRAPH_PRIMARY_ENABLED = "false" }
    Invoke-Compose up -d --build --force-recreate orchestrator
    $null = Wait-Health
    Invoke-Compose up -d n8n slackbot
    Write-Host "Fenced rollback returned new intake to legacy; primary evidence was preserved."
}
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:02 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:34 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:36 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
