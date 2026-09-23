# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:48 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("Preflight", "Run", "CheckEnvironment", "Install")][string]$Action,
    [Parameter(Mandatory = $true)][string]$EnvironmentFile,
    [Parameter(Mandatory = $true)][string]$Workspace,
    [string]$Python = ".venv-win\Scripts\python.exe",
    [string]$InstallRoot,
    [ValidatePattern('^[A-Za-z0-9 ._-]+$')][string]$TaskName = "DirectorHostProviderBridge"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-DotEnvNames {
    param([string]$Path)
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') { $values[$matches[1]] = $matches[2] }
    }
    return $values
}

function Get-BridgeRuntimeManifest {
    # ponytail: fixed runtime set; add a directory if the bridge starts reading another repo path.
    param([string]$Root)
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $files = @(foreach ($directory in @("src/director_orchestrator", "docs/schemas", "docs/provider-profiles", "prompts")) {
        Get-ChildItem -LiteralPath (Join-Path $base $directory) -Recurse -File
    }) + @(Get-Item -LiteralPath (Join-Path $base "scripts/windows-host-provider-bridge.ps1"))
    $lines = [string[]]@(foreach ($file in $files) {
        $relative = $file.FullName.Substring($base.Length + 1).Replace('\', '/')
        if ($relative -notmatch '(^|/)__pycache__/') {
            "$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $relative"
        }
    })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return ,$lines
}

function Install-BridgeRuntime {
    # Content-addressed copy: the directory name is the SHA256 of the manifest text.
    param([string]$SourceRoot, [string]$InstallRoot)
    $source = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\', '/')
    $target = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\', '/')
    if ($target -eq $source -or $target.StartsWith($source + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The bridge install root must be outside the source checkout."
    }
    $manifest = Get-BridgeRuntimeManifest -Root $source
    $text = ($manifest -join "`n") + "`n"
    $digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($text))).ToLowerInvariant()
    $destination = Join-Path $target $digest
    if (-not (Test-Path -LiteralPath $destination)) {
        $staging = Join-Path $target ".staging-$([Guid]::NewGuid().ToString('N'))"
        foreach ($line in $manifest) {
            $relative = $line.Substring(66)
            $copy = Join-Path $staging $relative
            $null = [IO.Directory]::CreateDirectory((Split-Path -Parent $copy))
            Copy-Item -LiteralPath (Join-Path $source $relative) -Destination $copy
        }
        # Directory.Move refuses an existing destination; Move-Item would nest into it.
        [IO.Directory]::Move($staging, $destination)
    }
    if (((Get-BridgeRuntimeManifest -Root $destination) -join "`n") + "`n" -cne $text) {
        throw "Installed bridge runtime does not match its content address."
    }
    return $destination
}

function Register-BridgeTask {
    # Registration only: no password is stored and the task is never started here.
    param([string]$Destination, [string]$TaskName, [string]$EnvironmentPath, [string]$WorkspacePath,
        [string]$PythonPath, [string]$PowerShell)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $launcher = Join-Path $Destination "scripts\windows-host-provider-bridge.ps1"
    $action = New-ScheduledTaskAction -Execute $PowerShell -WorkingDirectory $Destination -Argument (
        "-NoLogo -NoProfile -NonInteractive -File `"$launcher`" -Action Run -EnvironmentFile `"$EnvironmentPath`" " +
        "-Workspace `"$WorkspacePath`" -Python `"$PythonPath`"")
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
}

$environmentPath = (Resolve-Path -LiteralPath $EnvironmentFile).Path
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$values = Read-DotEnvNames -Path $environmentPath
$required = @(
    "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
    "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON",
    "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE",
    "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN",
    "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA"
)
if ([string]$values["DIRECTOR_HOST_PROVIDER_BIND"] -eq "0.0.0.0") {
    $required += @(
        "DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE",
        "DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE"
    )
}
$missing = @($required | Where-Object {
    -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
    ([string]$values[$_]).StartsWith("REPLACE_WITH_")
})
if ($missing.Count -gt 0) {
    throw "Host bridge configuration is incomplete: $($missing -join ', '). No values were printed."
}
# Python validates the pinned environment's content; only its presence is checked here.
$pinnedEnvironmentFile = [string]$values["DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"]
if ($pinnedEnvironmentFile -and -not (Test-Path -LiteralPath $pinnedEnvironmentFile -PathType Leaf)) {
    throw "The pinned process environment file is unavailable."
}
foreach ($otherSecret in @(
    "ORCHESTRATOR_SHARED_SECRET", "SLACK_GATEWAY_SECRET",
    "LANGGRAPH_PRIMARY_OWNER_TOKEN", "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
    "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET"
)) {
    if ($values.ContainsKey($otherSecret) -and
        [string]$values[$otherSecret] -ceq [string]$values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]) {
        throw "The host bridge secret must be distinct from $otherSecret."
    }
}

$commands = $values["DIRECTOR_HOST_PROVIDER_COMMANDS_JSON"] | ConvertFrom-Json -AsHashtable
foreach ($provider in $commands.Keys) {
    if ($provider -notin @("openai", "claude", "cursor", "grok", "antigravity", "github-copilot")) {
        throw "The host bridge command map contains an unallowlisted provider."
    }
    $argv = @($commands[$provider])
    if ($argv.Count -lt 1) { throw "The host bridge command map contains an empty command." }
    $executable = Get-Command ([string]$argv[0]) -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $executable) { throw "A configured host bridge executable is unavailable: $provider." }
}

$validationEnabled = [string]$values["DIRECTOR_PROVIDER_VALIDATION_ENABLED"] -eq "true"
if ($values.ContainsKey("DIRECTOR_PROVIDER_VALIDATION_ENABLED") -and
    [string]$values["DIRECTOR_PROVIDER_VALIDATION_ENABLED"] -cnotin @("true", "false")) {
    throw "Provider validation switch must be true or false."
}
$validationNames = @(
    "DIRECTOR_PROVIDER_VALIDATION_ENABLED",
    "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN",
    "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
    "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN",
    "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON",
    "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE",
    "DIRECTOR_PROVIDER_VALIDATION_TREE",
    "DIRECTOR_PROVIDER_VALIDATION_GENERATION",
    "DIRECTOR_PROVIDER_VALIDATION_TEAM_CONFIG",
    "DIRECTOR_PROVIDER_VALIDATION_OPENAI_RESPONSES_CONFIG",
    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"
)
if ($validationEnabled) {
    $validationRequired = @(
        "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN",
        "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
        "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN",
        "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON",
        "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE",
        "DIRECTOR_PROVIDER_VALIDATION_TREE"
    )
    $validationMissing = @($validationRequired | Where-Object {
        -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$values[$_]) -or
        ([string]$values[$_]).StartsWith("REPLACE_WITH_")
    })
    if ($validationMissing.Count -gt 0) {
        throw "Provider validation is enabled but incomplete: $($validationMissing -join ', '). No values were printed."
    }
    foreach ($validationSecret in @(
        "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY",
        "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"
    )) {
        if (([string]$values[$validationSecret]).Length -lt 32 -or
            [string]$values[$validationSecret] -ceq [string]$values["LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET"]) {
            throw "Provider validation keys must be at least 32 characters and distinct from the host bridge secret."
        }
    }
    if ([string]$values["DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY"] -ceq
        [string]$values["DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"]) {
        throw "Provider validation owner and capture keys must be distinct."
    }
    foreach ($pin in @("DIRECTOR_PROVIDER_VALIDATION_CANDIDATE", "DIRECTOR_PROVIDER_VALIDATION_TREE")) {
        if ([string]$values[$pin] -cnotmatch '^[0-9a-f]{40}$') {
            throw "Provider validation source pins are malformed."
        }
    }
    $manifestGeneration = [string]$values["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"]
    $explicitGeneration = [string]$values["DIRECTOR_PROVIDER_VALIDATION_GENERATION"]
    $selectedGeneration = if ($manifestGeneration) { $manifestGeneration } else { $explicitGeneration }
    if ($selectedGeneration -cnotmatch '^[0-9a-f]{64}$' -or
        ($explicitGeneration -and $explicitGeneration -cne $selectedGeneration)) {
        throw "Provider validation generation is missing, malformed or conflicting."
    }
    try { $accounts = $values["DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON"] | ConvertFrom-Json -AsHashtable }
    catch { throw "Provider validation account-file bindings are malformed." }
    if ($accounts -isnot [System.Collections.IDictionary]) {
        throw "Provider validation account-file bindings must be an object."
    }
    foreach ($paths in $accounts.Values) {
        if ($paths -isnot [array] -or @($paths | Where-Object { $_ -isnot [string] -or [string]::IsNullOrWhiteSpace($_) }).Count) {
            throw "Provider validation account-file bindings must contain path arrays."
        }
    }
}

Write-Host "Host bridge preflight passed for $($commands.Count) supported provider command(s); no provider was invoked."
if ($Action -eq "Preflight") { return }

if ($Action -eq "Install") {
    if ([string]::IsNullOrWhiteSpace($InstallRoot)) { throw "Install requires -InstallRoot." }
    # A scheduled task does not inherit this shell's PATH/TEMP, so the pinned environment is mandatory.
    if (-not $pinnedEnvironmentFile) {
        throw "Install requires DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE in the bridge profile."
    }
    $destination = Install-BridgeRuntime -SourceRoot (Split-Path -Parent $PSScriptRoot) -InstallRoot $InstallRoot
    # The app-execution alias, not [Environment]::ProcessPath: the packaged path is ACL-restricted.
    $powerShell = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
    & $powerShell -NoLogo -NoProfile -NonInteractive -File (Join-Path $destination "scripts\windows-host-provider-bridge.ps1") `
        -Action CheckEnvironment -EnvironmentFile $environmentPath -Workspace $workspacePath -Python $pythonPath
    if ($LASTEXITCODE -ne 0) {
        throw "The installed bridge runtime failed its environment check; no task was registered."
    }
    Register-BridgeTask -Destination $destination -TaskName $TaskName -EnvironmentPath $environmentPath `
        -WorkspacePath $workspacePath -PythonPath $pythonPath -PowerShell $powerShell
    # The adapter path is part of production_configuration_digest, so generations
    # must pin the digests the installed copy printed above.
    Write-Host "Bridge runtime $(Split-Path -Leaf $destination) was installed and task '$TaskName' was registered; it was NOT started."
    return
}

$optional = @(
    "DIRECTOR_HOST_PROVIDER_WORKSPACE", "DIRECTOR_HOST_PROVIDER_BIND",
    "DIRECTOR_HOST_PROVIDER_PORT", "DIRECTOR_HOST_PROVIDER_TIMEOUT_SECONDS",
    "DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE", "DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE",
    "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"
) + $validationNames
$original = @{}
foreach ($name in ($required + $optional + @("PYTHONPATH") | Select-Object -Unique)) {
    $original[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
# A stale parent value must never select validation or a pinned environment.
foreach ($name in $validationNames + @("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE")) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
foreach ($name in $required + $optional) {
    if ($values.ContainsKey($name)) {
        [Environment]::SetEnvironmentVariable($name, [string]$values[$name], "Process")
    }
}
[Environment]::SetEnvironmentVariable("DIRECTOR_HOST_PROVIDER_WORKSPACE", $workspacePath, "Process")
# The launcher always runs the code beside it, including an installed copy.
[Environment]::SetEnvironmentVariable("PYTHONPATH", (Join-Path (Split-Path -Parent $PSScriptRoot) "src"), "Process")
$serverArguments = @()
if ($Action -eq "CheckEnvironment") { $serverArguments = @("--check-environment") }
try {
    & $pythonPath -m director_orchestrator.host_provider_bridge_server @serverArguments
    if ($LASTEXITCODE -ne 0) { throw "The Windows host provider bridge exited unsuccessfully." }
}
finally {
    foreach ($entry in $original.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 07:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:05 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:48 UTC | END]]
