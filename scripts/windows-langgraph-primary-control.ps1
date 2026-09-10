# [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]]
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
    [ValidateSet("PrepareAcceptance", "BootstrapDatabaseIdentity", "SelectGeneration", "StageRuntime", "PrepareOfflineHandoff", "ValidateProviders", "Challenge", "Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "Health", "Activate", "Rollback")]
    [string]$Action,
    [string]$EnvironmentFile,
    [string]$ComposeProject,
    [int]$HostPort,
    [string]$CandidateCommit,
    [string]$SecurityReviewerAllowlistJson,
    [string]$IndependentReviewerAllowlistJson,
    [string]$BackupPath,
    [string]$AcceptanceArtifactPath,
    [string]$GenerationManifestPath,
    [string]$GenerationManifestSha256,
    [string]$GitHubRepository,
    [ValidateRange(1, [int]::MaxValue)][int]$PullRequestNumber,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)
if ($Action -cne "SelectGeneration" -and ($GenerationManifestPath -or $GenerationManifestSha256)) {
    throw "Generation selection inputs are valid only for SelectGeneration."
}

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
    # Same-directory atomic replacement keeps generation pointers together.
    $temporary = $script:environmentPath + "." + [Guid]::NewGuid().ToString("N") + ".tmp"
    try {
        [IO.File]::WriteAllText($temporary, $text, [Text.UTF8Encoding]::new($false))
        [IO.File]::Replace($temporary, $script:environmentPath, [NullString]::Value)
        foreach ($entry in $Switches.GetEnumerator()) {
            $script:values[$entry.Key] = [string]$entry.Value
            # Compose interpolation otherwise prefers an inherited process value
            # over the newly selected --env-file (including the ASGI entry point).
            [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, "Process")
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose --project-name $script:ComposeProject --env-file $script:environmentPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed." }
}

function Get-ResolvedDeploymentDigest {
    $configuration = @(Invoke-Compose config --format json) -join [Environment]::NewLine
    # Compute inside the exact candidate image with no network or host mounts.
    # Resolved secrets stay on stdin; only the digest is returned.
    $result = @($configuration | & docker run --rm -i --network none --entrypoint python $script:runtimeImageId `
        -m director_orchestrator.primary_generation compose-digest --image-id $script:runtimeImageId)
    $observed = [string]($result | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $observed -cnotmatch '^[0-9a-f]{64}$') { throw "Resolved deployment configuration is not verified." }
    return $observed
}

function Assert-RunningCandidate {
    param([string]$AsgiApp)
    $containers = @(Invoke-Compose ps --status running -q orchestrator)
    if ($containers.Count -ne 1) { throw "Expected one running candidate container." }
    $raw = @(& docker inspect ([string]$containers[0])) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) { throw "Candidate readback failed." }
    $observed = @(ConvertFrom-Json -InputObject $raw)[0]
    $expectedCommand = @("uvicorn", $AsgiApp, "--host", "0.0.0.0", "--port", "8080")
    if ([string]$observed.Image -cne $script:runtimeImageId -or
        (@($observed.Config.Cmd) -join "|") -cne ($expectedCommand -join "|")) {
        throw "Running container image or command differs from the verified candidate."
    }
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
    $json = & gh pr view $script:PullRequestNumber --repo "https://github.com/$script:GitHubRepository" `
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
    return [ordered]@{
        candidate_commit = $script:candidateCommit
        candidate_tree = $script:candidateTree
        ci_conclusion = "SUCCESS"
        ci_evidence_digest = $ciDigest
        ci_required_check_count = $requiredChecks.Count
        ci_run_ids = $runIds
        independent_review_evidence_digest = $independentDigest
        independent_review_verdict = "ACCEPT"
        independent_reviewed_commit = $script:candidateCommit
        independent_reviewer = [string]$independent[0].actor
        pull_request_author = [string]$pull.author.login
        remote_head_commit = [string]$pull.headRefOid
        security_evidence_digest = $securityDigest
        security_reviewed_commit = $script:candidateCommit
        security_reviewer = [string]$security[0].actor
        security_verdict = "ACCEPT"
    }
}

function Assert-CanonicalAcceptanceArtifact {
    param(
        [Collections.Specialized.OrderedDictionary]$Facts,
        [string]$Path
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Accept requires -AcceptanceArtifactPath from the independent evidence issuers."
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    try { $artifact = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 20 }
    catch { throw "The independently supplied acceptance artifact is invalid JSON." }
    $expectedNames = @(
        "attestations", "facts", "facts_digest", "pull_request_number",
        "repository", "version"
    )
    $observedNames = @($artifact.PSObject.Properties.Name | Sort-Object)
    if (($observedNames -join "|") -cne (($expectedNames | Sort-Object) -join "|")) {
        throw "The independently supplied acceptance artifact contract is invalid."
    }
    $factsJson = $Facts | ConvertTo-Json -Depth 8 -Compress
    $artifactFactsJson = $artifact.facts | ConvertTo-Json -Depth 8 -Compress
    if ($artifactFactsJson -cne $factsJson -or
        [string]$artifact.facts_digest -cne (Get-Sha256Text $factsJson) -or
        [long]$artifact.pull_request_number -ne $script:PullRequestNumber -or
        [string]$artifact.repository -cne $script:GitHubRepository -or
        [string]$artifact.version -cne "director-primary-acceptance-artifact/v1") {
        throw "The independently supplied artifact does not match canonical GitHub facts."
    }
    $attestationNames = @($artifact.attestations.PSObject.Properties.Name | Sort-Object)
    $expectedAttestationNames = @("ci", "independent_review", "security")
    if (($attestationNames -join "|") -cne (($expectedAttestationNames | Sort-Object) -join "|")) {
        throw "The independently supplied acceptance attestations are invalid."
    }
    $expectedIssuers = @{
        ci = "github-actions"
        independent_review = [string]$artifact.facts.independent_reviewer
        security = [string]$artifact.facts.security_reviewer
    }
    foreach ($kind in $expectedAttestationNames) {
        $attestation = $artifact.attestations.$kind
        $names = @($attestation.PSObject.Properties.Name | Sort-Object)
        if (($names -join "|") -cne "issuer|signature" -or
            [string]$attestation.issuer -cne [string]$expectedIssuers[$kind] -or
            [string]$attestation.signature -cnotmatch '^[0-9a-f]{64}$') {
            throw "The independently supplied $kind acceptance attestation is invalid."
        }
    }
    return $resolved
}

function Get-DatabaseBinding {
    param([switch]$Bootstrap)
    $bootstrapValue = if ($Bootstrap) { "True" } else { "False" }
    $output = @(Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import database_binding; s=Settings(); b=database_binding(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres, bootstrap=$bootstrapValue); print('|'.join(b))")
    $binding = [string](@($output | Where-Object { $_ -match '^postgres:sha256:[0-9a-f]{64}\|[0-9a-f-]{36}$' }) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($binding)) { throw "Primary database identity is unavailable; use explicit BootstrapDatabaseIdentity first." }
    return $binding.Split('|')
}

function Get-LegacyInventory {
    $output = @(Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -c `
        "import hashlib,json,sqlite3; from pathlib import Path; from director_orchestrator.config import Settings; s=Settings(); c=sqlite3.connect(Path(s.sqlite_path).resolve().as_uri()+'?mode=ro',uri=True); rows=c.execute('SELECT task_id,status FROM tasks ORDER BY task_id').fetchall(); c.close(); payload=json.dumps(rows,separators=(',',':')); print(str(len(rows))+'|'+hashlib.sha256(payload.encode()).hexdigest())")
    $inventory = [string](@($output | Where-Object { $_ -match '^\d+\|[0-9a-f]{64}$' }) | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($inventory)) { throw "Legacy task ownership inventory could not be frozen." }
    return $inventory.Split('|')
}

function Stop-And-Prove-AllIntake {
    Invoke-Compose stop slackbot n8n orchestrator
    Assert-AllIntakeStopped
}

function Assert-OfflineHandoffProfile {
    if ($script:acceptanceMode -cne "provider-v2") {
        throw "PrepareOfflineHandoff requires a selected provider-v2 generation."
    }
    foreach ($name in @("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "DIRECTOR_PROVIDER_VALIDATION_ENABLED")) {
        if (-not $script:values.ContainsKey($name) -or [string]$script:values[$name] -cne "false") {
            throw "PrepareOfflineHandoff requires explicit false in the initial generation-bound profile: $name."
        }
    }
}

function Assert-AllIntakeStopped {
    foreach ($service in @("slackbot", "n8n", "orchestrator")) {
        $running = [string](@(Invoke-Compose ps --status running -q $service) | Select-Object -Last 1)
        if (-not [string]::IsNullOrWhiteSpace($running)) {
            throw "Authenticated intake surface remains active: $service"
        }
    }
}

function Invoke-Evidence {
    param(
        [ValidateSet("record", "require-ready", "require-staged", "require-accepted", "verify-backup", "bind-legacy-inventory", "challenge")][string]$Command,
        [string]$Stage,
        [string]$FactsJson = "{}",
        [string]$AcceptanceArtifactPath,
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
        $arguments += @("--stage", $Stage)
        if ($Stage -eq "ACCEPTED") {
            if ([string]::IsNullOrWhiteSpace($AcceptanceArtifactPath)) {
                throw "ACCEPTED requires the canonical verified artifact path."
            }
            $arguments += @("--acceptance-artifact", "/run/director-acceptance.json")
        }
        else {
            $arguments += @("--evidence-digest", (Get-Sha256Text $FactsJson), "--facts-json", $FactsJson)
        }
    }
    elseif ($Command -eq "verify-backup") {
        $arguments += @("--backup-digest", $BackupDigest)
    }
    elseif ($Command -eq "bind-legacy-inventory") {
        $arguments += @("--legacy-inventory-digest", $LegacyInventoryDigest, "--legacy-task-count", [string]$LegacyTaskCount)
    }
    if ($Command -eq "record" -and $Stage -eq "ACCEPTED") {
        Invoke-Compose run --rm --no-deps --pull never -T `
            -v "${AcceptanceArtifactPath}:/run/director-acceptance.json:ro" `
            --entrypoint python orchestrator @arguments
    }
    else {
        Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator @arguments
    }
}

# Preparation is deliberately before any environment read, Docker call, or write.
# This unsigned output is not acceptance evidence and has no attestation fields.
if ($Action -ceq "PrepareAcceptance") {
    if ($EnvironmentFile -or $ComposeProject -or $HostPort -or $AcceptanceArtifactPath -or $BackupPath) {
        throw "PrepareAcceptance accepts no runtime environment or artifact inputs."
    }
    if ($CandidateCommit -cnotmatch '^[0-9a-f]{40}$' -or
        $GitHubRepository -cnotmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $PullRequestNumber -lt 1) {
        throw "Preparation requires repository, pull request, and exact candidate commit."
    }
    $script:values = @{
        LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON = $SecurityReviewerAllowlistJson
        LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON = $IndependentReviewerAllowlistJson
    }
    if ($null -eq (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "Authenticated GitHub CLI is required to prepare acceptance."
    }
    # Tree authority comes from GitHub, not a caller-provided facts file or tree.
    $commitJson = & gh api --hostname github.com "repos/$GitHubRepository/git/commits/$CandidateCommit"
    if ($LASTEXITCODE -ne 0) { throw "GitHub candidate could not be verified." }
    $commit = $commitJson | ConvertFrom-Json -Depth 10
    if ([string]$commit.sha -cne $CandidateCommit -or [string]$commit.tree.sha -cnotmatch '^[0-9a-f]{40}$') {
        throw "GitHub candidate identity is invalid."
    }
    $script:candidateTree = [string]$commit.tree.sha
    $facts = Get-GitHubAcceptanceFacts
    $unsigned = [ordered]@{
        facts = $facts
        facts_digest = Get-Sha256Text ($facts | ConvertTo-Json -Depth 8 -Compress)
        pull_request_number = $PullRequestNumber
        repository = $GitHubRepository
        version = "director-primary-acceptance-artifact/v1"
    }
    $unsigned | ConvertTo-Json -Depth 12 -Compress
    return
}
if (-not $EnvironmentFile -or -not $ComposeProject -or $HostPort -lt 1024 -or $HostPort -gt 65535) {
    throw "Runtime actions require EnvironmentFile, ComposeProject, and HostPort (1024-65535)."
}
if ($CandidateCommit -or $SecurityReviewerAllowlistJson -or $IndependentReviewerAllowlistJson) {
    throw "Runtime actions do not accept preparation-only authority inputs."
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
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT", "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
    "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON",
    "LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS",
    "LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA", "LANGGRAPH_PRIMARY_PINNED_TREE_SHA",
    "REPOSITORY_SNAPSHOT_HOST_PATH", "REPOSITORY_SNAPSHOT_ROOT",
    "REPOSITORY_ALLOWLIST", "ALLOWED_STARTING_REFS"
)
$acceptanceMode = "github-v1"
if ($values.ContainsKey("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE")) {
    $acceptanceMode = [string]$values["LANGGRAPH_PRIMARY_ACCEPTANCE_MODE"]
}
if ($Action -eq "PrepareOfflineHandoff") { Assert-OfflineHandoffProfile }
switch -CaseSensitive ($acceptanceMode) {
    "github-v1" {
        $required += @("LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON",
                       "LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON")
    }
    "provider-v2" {
        $required += @("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH",
                       "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256",
                       "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH",
                       "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_DIRECTORY")
    }
    default { throw "Unknown primary acceptance mode." }
}
$missing = @($required | Where-Object {
    -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
    ([string]$values[$_]).StartsWith("REPLACE_WITH_")
})
if ($missing.Count -gt 0) {
    throw "Primary configuration is incomplete: $($missing -join ', '). No values were printed."
}
$requiredProviders = @(
    "openai", "claude", "cursor", "grok", "antigravity", "perplexity",
    "firecrawl", "github-copilot"
)
$configuredProviders = @(([string]$values["LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS"]).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$coreProviders = @("openai", "claude", "cursor", "grok")
if ($configuredProviders.Count -ne @($configuredProviders | Sort-Object -Unique).Count -or
    @($configuredProviders | Where-Object { $_ -notin $requiredProviders }).Count -gt 0 -or
    "openai" -notin $configuredProviders -or
    @($configuredProviders | Where-Object { $_ -in $coreProviders }).Count -lt 3) {
    throw "Primary configured pool must cover orchestration and independent implementation/review with at least three primary providers."
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
foreach ($name in @("DIRECTOR_ASGI_APP", "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH",
    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH", "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256",
    "DIRECTOR_RUNTIME_IMAGE", "DIRECTOR_RUNTIME_IMAGE_ID", "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256",
    "ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_ENABLED", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED", "LANGGRAPH_PARALLEL_KILL_SWITCH")) {
    $selected = if ($values.ContainsKey($name)) { [string]$values[$name] } else { $null }
    [Environment]::SetEnvironmentVariable($name, $selected, "Process")
}
Invoke-Compose config --quiet
$candidateCommit = [string](@(& git rev-parse --verify 'HEAD^{commit}') | Select-Object -Last 1)
$candidateTree = [string](@(& git rev-parse --verify 'HEAD^{tree}') | Select-Object -Last 1)
if ($candidateCommit -notmatch '^[0-9a-f]{40}$' -or $candidateTree -notmatch '^[0-9a-f]{40}$') {
    throw "The Director candidate commit/tree cannot be frozen."
}
# Health observes the existing endpoint and never starts even a helper container.
if ($Action -eq "Health") {
    $health = Wait-Health
    $health.langgraph_primary | ConvertTo-Json -Depth 8
    return
}
# Image construction is an explicit preparatory operation, never a side effect
# of observing state, bootstrapping identity, selecting a generation or staging.
if (@(& git status --porcelain).Count -ne 0 -or $LASTEXITCODE -ne 0) {
    throw "Runtime control requires a clean exact-candidate checkout."
}
$imageProof = @(& docker image inspect "$ComposeProject-orchestrator" --format '{{.Id}}|{{index .Config.Labels "org.director.candidate.commit"}}|{{index .Config.Labels "org.director.candidate.tree"}}|{{json .Config.Entrypoint}}')
$imageParts = ([string]($imageProof | Select-Object -Last 1)).Split('|')
if ($LASTEXITCODE -ne 0 -or $imageParts.Count -ne 4 -or $imageParts[0] -cnotmatch '^sha256:[0-9a-f]{64}$' -or
    $imageParts[1] -cne $candidateCommit -or $imageParts[2] -cne $candidateTree -or $imageParts[3] -cnotin @("null", "[]")) {
    throw "An existing exact-candidate image is required; build explicitly with DIRECTOR_CANDIDATE_COMMIT/TREE build arguments first."
}
$runtimeImageId = [string]$imageParts[0]
$env:DIRECTOR_RUNTIME_IMAGE = $runtimeImageId
$env:DIRECTOR_RUNTIME_IMAGE_ID = $runtimeImageId
if ($candidateCommit -cne [string]$values["LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA"] -or
    $candidateTree -cne [string]$values["LANGGRAPH_PRIMARY_PINNED_TREE_SHA"]) {
    throw "Control checkout differs from the configured accepted candidate."
}
# Only this explicit action may establish pre-acceptance database metadata.
if ($Action -eq "BootstrapDatabaseIdentity") {
    $null = Get-DatabaseBinding -Bootstrap
    Write-Host "Durable identity established; no application or checkpoint schema was initialized."
    return
}
$databaseBinding = Get-DatabaseBinding
$databaseIdentity = [string]$databaseBinding[0]
$databaseInstanceId = [string]$databaseBinding[1]

if ($Action -eq "SelectGeneration") {
    if ($acceptanceMode -cne "provider-v2") { throw "SelectGeneration requires provider-v2 acceptance." }
    if (-not $GenerationManifestPath -or $GenerationManifestSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "SelectGeneration requires the owner-provided manifest and exact SHA256."
    }
    $manifestSource = (Resolve-Path -LiteralPath $GenerationManifestPath).Path
    if ((Get-FileHash -LiteralPath $manifestSource -Algorithm SHA256).Hash.ToLowerInvariant() -cne $GenerationManifestSha256) {
        throw "Generation manifest digest does not match."
    }
    $manifest = Get-Content -LiteralPath $manifestSource -Raw | ConvertFrom-Json
    $journalPath = [string]$manifest.journal_path
    if ($journalPath -cnotmatch '^/data/[a-zA-Z0-9_-]+[.]sqlite3$') { throw "Invalid generation journal path." }
    Stop-And-Prove-AllIntake
    $env:LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH = $journalPath
    $env:DIRECTOR_RESOLVED_DEPLOYMENT_SHA256 = Get-ResolvedDeploymentDigest
    # Exclusive reservation preserves earlier journals/manifests, including any
    # orphan left by interrupted selection. A retry needs a fresh generation.
    Invoke-Compose run --rm --no-deps --pull never -T `
        -v "${manifestSource}:/run/director-proposed-generation.json:ro" `
        -e "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH=/run/director-proposed-generation.json" `
        -e "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256=$GenerationManifestSha256" `
        -e "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH=$journalPath" `
        --entrypoint python orchestrator -m director_orchestrator.primary_generation reserve
    Assert-AllIntakeStopped
    Write-Switches @{
        LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH = "/data/generation-$GenerationManifestSha256.json"
        LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256 = $GenerationManifestSha256
        LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH = $journalPath
        DIRECTOR_RUNTIME_IMAGE = $runtimeImageId
        DIRECTOR_RUNTIME_IMAGE_ID = $runtimeImageId
        DIRECTOR_RESOLVED_DEPLOYMENT_SHA256 = $env:DIRECTOR_RESOLVED_DEPLOYMENT_SHA256
    }
    Write-Host "Fresh generation selected atomically; all mutating services remain stopped."
    return
}
if ($acceptanceMode -ceq "provider-v2") {
    $env:DIRECTOR_RESOLVED_DEPLOYMENT_SHA256 = Get-ResolvedDeploymentDigest
    Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -m director_orchestrator.primary_generation verify
}
if ($Action -eq "Preflight") {
    Invoke-Evidence -Command record -Stage PREFLIGHT -FactsJson '{"compose_config_valid":true,"snapshot_exact":true}'
    Write-Host "Primary preflight passed against existing database identity; no service was started or schema initialized."
    return
}
if ($Action -eq "Challenge") {
    if (-not $values.ContainsKey("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE") -or $values["LANGGRAPH_PRIMARY_ACCEPTANCE_MODE"] -cne "provider-v2") { throw "Challenge requires provider-v2 mode." }
    Invoke-Evidence -Command challenge
    return
}
if ($Action -eq "Accept") {
    if ($values.ContainsKey("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE") -and $values["LANGGRAPH_PRIMARY_ACCEPTANCE_MODE"] -ceq "provider-v2") {
        $verifiedArtifactPath = (Resolve-Path -LiteralPath $AcceptanceArtifactPath).Path
        Invoke-Evidence -Command record -Stage ACCEPTED -AcceptanceArtifactPath $verifiedArtifactPath
        Write-Host "Provider-v2 signed acceptance atomically recorded."
        return
    }
    $facts = Get-GitHubAcceptanceFacts
    $verifiedArtifactPath = Assert-CanonicalAcceptanceArtifact `
        -Facts $facts -Path $AcceptanceArtifactPath
    Invoke-Evidence -Command record -Stage ACCEPTED `
        -AcceptanceArtifactPath $verifiedArtifactPath
    Write-Host "Exact-head CI, security, and independent-review acceptance evidence was bound."
    return
}
if ($Action -eq "Migrate") {
    Invoke-Evidence -Command require-accepted
    Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres, accepted_storage_identity='$databaseIdentity', accepted_instance_id='$databaseInstanceId', initial_accepted_migration=True); print('schema_current=true'); h.close()"
    Invoke-Evidence -Command record -Stage MIGRATED -FactsJson '{"schema_current":true}'
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
    Invoke-Evidence -Command record -Stage BACKED_UP -FactsJson ("{`"backup_digest`":`"sha256:$backupDigest`"}")
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
            "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = '$schema' AND table_name IN ('director_primary_control_plane','director_primary_intake_authority','director_primary_task_projection','director_primary_task_writer','director_primary_claim','director_primary_effect_reservation','director_primary_effect_approval_binding','director_primary_typed_effect_intent','director_primary_effect_execution','director_primary_reconciliation_event','director_primary_terminal_evidence','director_provider_activation_evidence','director_provider_activation_authorization','director_primary_parallel_batch','director_primary_parallel_assignment','director_primary_provider_attempt','director_primary_workload_assignment')) || '|' || (SELECT count(*) FROM information_schema.tables WHERE table_schema = '$schema' AND table_name IN ('checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations')) || '|' || (SELECT count(*) FROM $schema.director_primary_control_plane WHERE database_instance_id = '$databaseInstanceId');" ) | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or $proof -notmatch '^(\d+)\|(\d+)\|(\d+)$') { throw "Restored schema verification failed." }
        $requiredTableCount = [int]$matches[1]
        $checkpointTableCount = [int]$matches[2]
        $schemaEvidenceRows = [int]$matches[3]
        if ($requiredTableCount -ne 17 -or $checkpointTableCount -ne 4 -or $schemaEvidenceRows -lt 1) {
            throw "Restore drill lacks required control-plane/checkpoint tables or schema evidence."
        }
    }
    finally {
        & docker exec $container dropdb -U director_primary --if-exists $database | Out-Null
        & docker exec $container rm -f -- $temporary | Out-Null
    }
    # Key order is canonical so the script and Python store hash identical JSON.
    $facts = "{`"backup_digest`":`"sha256:$restoreDigest`",`"checkpoint_tables`":$checkpointTableCount,`"required_control_plane_tables`":$requiredTableCount,`"schema_evidence_rows`":$schemaEvidenceRows}"
    Invoke-Evidence -Command record -Stage RESTORE_PROVED -FactsJson $facts
    Write-Host "Primary Postgres restore drill completed in a disposable database."
    return
}

if ($Action -eq "StageRuntime") {
    # Authenticated acceptance and an exact restore proof precede service startup.
    # Legacy inventory is intentionally deferred until the final stopped cutover.
    Invoke-Evidence -Command require-staged
    Stop-And-Prove-AllIntake
    Write-Switches @{
        DIRECTOR_ASGI_APP = "director_orchestrator.primary_staging:app"
        ALLOW_PAID_RUNS = "false"
        LANGGRAPH_PRIMARY_ENABLED = "false"
        LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "false"
        LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "false"
        LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED = "false"
        LANGGRAPH_PARALLEL_KILL_SWITCH = "true"
    }
    try {
        Invoke-Compose up -d --no-deps --no-build --force-recreate orchestrator
        Assert-RunningCandidate -AsgiApp "director_orchestrator.primary_staging:app"
        $primary = (Wait-Health).langgraph_primary
        if (-not $primary.staged_ready -or -not $primary.evidence_only -or $primary.primary_runtime_active) {
            throw "Staged evidence health was not established."
        }
    }
    catch {
        Invoke-Compose stop orchestrator
        throw "StageRuntime failed; mutating services remain stopped."
    }
    Write-Host "Evidence-only service staged; intake, existing-task recovery, dispatch, effects and writer loops remain disabled."
    return
}

if ($Action -eq "ValidateProviders") {
    Assert-RunningCandidate -AsgiApp "director_orchestrator.primary_staging:app"
    $primary = (Wait-Health).langgraph_primary
    if (-not $primary.staged_ready -or -not $primary.evidence_only -or $primary.primary_runtime_active) {
        throw "Provider validation requires the exact evidence-only staged service."
    }
    Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -m director_orchestrator.primary_generation validate-providers
    return
}

if ($Action -eq "PrepareOfflineHandoff") {
    Invoke-Evidence -Command require-staged
    Stop-And-Prove-AllIntake
    $legacyInventory = Get-LegacyInventory
    Invoke-Evidence -Command bind-legacy-inventory -LegacyTaskCount ([int]$legacyInventory[0]) -LegacyInventoryDigest ([string]$legacyInventory[1])
    Invoke-Evidence -Command require-ready
    Assert-AllIntakeStopped
    Write-Host "Offline handoff inventory prepared; selected-project intake remains stopped. No runtime was started or activated."
    return
}
if ($Action -eq "Activate") {
    Stop-And-Prove-AllIntake
    $legacyInventory = Get-LegacyInventory
    Invoke-Evidence -Command bind-legacy-inventory -LegacyTaskCount ([int]$legacyInventory[0]) -LegacyInventoryDigest ([string]$legacyInventory[1])
    Invoke-Evidence -Command require-ready
    try {
        Write-Switches @{ DIRECTOR_ASGI_APP = "director_orchestrator.main:app"; ALLOW_PAID_RUNS = "true"; LANGGRAPH_PRIMARY_ENABLED = "true"; LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED = "true"; LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED = "false"; LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED = "true"; LANGGRAPH_PARALLEL_KILL_SWITCH = "false" }
        Invoke-Compose up -d --no-build --force-recreate orchestrator
        Assert-RunningCandidate -AsgiApp "director_orchestrator.main:app"
        $primary = (Wait-Health).langgraph_primary
        foreach ($field in @("primary_graph_constructed", "primary_router_wired", "checkpoint_backend_postgres", "checkpoint_schema_current", "claim_store_ready", "effect_ledger_ready", "writer_lease_ready", "reconciliation_worker_ready", "primary_runtime_active", "legacy_new_intake_disabled")) {
            if (-not [bool]$primary.$field) { throw "Primary activation health failed at $field." }
        }
        if ([string]$primary.new_intake_owner -cne "langgraph" -or -not [bool]$primary.provider_activation.combined_live_ready -or [int]$primary.ambiguous_effect_count -ne 0) { throw "Primary activation ownership/provider/reconciliation proof failed." }
        Invoke-Evidence -Command record -Stage ACTIVATED -FactsJson ("{`"intake_fencing_generation`":$([int]$primary.intake_fencing_generation)}")
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
    Invoke-Compose run --rm --no-deps --pull never -T --entrypoint python orchestrator -c `
        "from director_orchestrator.config import Settings; from director_orchestrator.langgraph_postgres import open_postgres_control_plane; s=Settings(); h=open_postgres_control_plane(s.langgraph_primary_postgres_dsn, schema=s.langgraph_primary_postgres_schema, allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres, accepted_storage_identity='$databaseIdentity', accepted_instance_id='$databaseInstanceId'); c=h.claim_store.current_intake_authority(); m=h.claim_store.primary_control_plane_metrics(); q=h.claim_store.primary_quiescence_metrics(); (_ for _ in ()).throw(RuntimeError('ambiguous effects block rollback')) if m.ambiguous_effect_count != 0 else None; (_ for _ in ()).throw(RuntimeError('active provider dispatches, assignments, writer claims, reconciliation, or claims block rollback')) if not q.rollback_safe else None; n=h.claim_store.acquire_intake_authority(intake_owner='legacy', owner_token=s.langgraph_primary_owner_token, expected_generation=c.fencing_generation); (_ for _ in ()).throw(RuntimeError('legacy intake CAS did not complete')) if n.legacy_new_intake_disabled else None; h.close()"
    Write-Switches @{ DIRECTOR_ASGI_APP = "director_orchestrator.main:app"; LANGGRAPH_PRIMARY_ENABLED = "false"; LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED = "false" }
    Invoke-Compose up -d --no-build --force-recreate orchestrator
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
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | END]]
