$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

function New-RandomSecret {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

function Assert-SecretShape {
    param(
        [AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [AllowEmptyString()][string]$RequiredPrefix,
        [AllowEmptyString()][string]$ForbiddenPrefix,
        [AllowEmptyString()][string]$ForbiddenMessage,
        [switch]$Optional
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        if ($Optional) {
            return $null
        }
        throw "A value for $Prompt is required."
    }

    # Checked before RequiredPrefix so the specific message wins: an Anthropic
    # console key also starts with "sk-", so it would otherwise pass as OpenAI.
    if ($ForbiddenPrefix -and $Value.StartsWith($ForbiddenPrefix, [StringComparison]::Ordinal)) {
        throw $ForbiddenMessage
    }

    if ($RequiredPrefix -and -not $Value.StartsWith($RequiredPrefix, [StringComparison]::Ordinal)) {
        throw "The supplied value for $Prompt does not begin with $RequiredPrefix."
    }

    return $Value
}

function Read-ValidatedSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$RequiredPrefix,
        [string]$ForbiddenPrefix,
        [string]$ForbiddenMessage,
        [switch]$Optional
    )

    $secureValue = Read-Host $Prompt -AsSecureString
    $valuePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($valuePointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($valuePointer)
    }

    return Assert-SecretShape -Value $plainValue -Prompt $Prompt -RequiredPrefix $RequiredPrefix `
        -ForbiddenPrefix $ForbiddenPrefix -ForbiddenMessage $ForbiddenMessage -Optional:$Optional
}

function Set-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $escapedName = [Regex]::Escape($Name)
    # "$" is a substitution marker in a Regex replacement string, so a credential
    # containing one would be silently corrupted. "$$" emits a literal "$".
    $escapedValue = $Value.Replace('$', '$$')
    return [Regex]::Replace($Text, "(?m)^${escapedName}=.*$", "${Name}=${escapedValue}")
}

$envText = Get-Content ".env" -Raw
$envText = $envText -replace 'ORCHESTRATOR_SHARED_SECRET=REPLACE_WITH_RANDOM_SECRET', ("ORCHESTRATOR_SHARED_SECRET=" + (New-RandomSecret))
if ($envText -notmatch '(?m)^SLACK_GATEWAY_SECRET=') {
    $envText = $envText.TrimEnd() + "`r`nSLACK_GATEWAY_SECRET=" + (New-RandomSecret) + "`r`n"
} else {
    $envText = $envText -replace 'SLACK_GATEWAY_SECRET=REPLACE_WITH_DISTINCT_RANDOM_SECRET', ("SLACK_GATEWAY_SECRET=" + (New-RandomSecret))
}
$envText = $envText -replace 'N8N_ENCRYPTION_KEY=REPLACE_WITH_RANDOM_SECRET', ("N8N_ENCRYPTION_KEY=" + (New-RandomSecret))
$envText = $envText -replace 'N8N_POSTGRES_PASSWORD=REPLACE_WITH_RANDOM_SECRET', ("N8N_POSTGRES_PASSWORD=" + (New-RandomSecret))
$envText = Set-DotEnvValue -Text $envText -Name "ALLOW_PAID_RUNS" -Value "false"
$envText = Set-DotEnvValue -Text $envText -Name "USAGE_CHECK_ENABLED" -Value "false"

if ($envText -match '(?m)^SLACK_APP_TOKEN=\s*$') {
    $slackAppToken = Read-ValidatedSecret -Prompt "Paste the Slack app-level token (input hidden)" -RequiredPrefix "xapp-"
    $envText = Set-DotEnvValue -Text $envText -Name "SLACK_APP_TOKEN" -Value $slackAppToken
    $slackAppToken = $null
}

if ($envText -match '(?m)^SLACK_BOT_TOKEN=\s*$') {
    $slackBotToken = Read-ValidatedSecret -Prompt "Paste the Slack bot token (input hidden)" -RequiredPrefix "xoxb-"
    $envText = Set-DotEnvValue -Text $envText -Name "SLACK_BOT_TOKEN" -Value $slackBotToken
    $slackBotToken = $null
}

# Provider credentials. Each is optional here so the dry-run bootstrap still
# completes without them; press Enter to skip and the slot stays empty, which
# readiness() reports as not configured.

if ($envText -match '(?m)^OPENAI_API_KEY=\s*$') {
    $openAiKey = Read-ValidatedSecret `
        -Prompt "Paste the OpenAI API key for the Director, from platform.openai.com (input hidden, Enter to skip)" `
        -RequiredPrefix "sk-" `
        -ForbiddenPrefix "sk-ant-" `
        -ForbiddenMessage "That is an Anthropic key. This slot is the OpenAI Director credential and is sent to api.openai.com; get it from platform.openai.com instead." `
        -Optional
    if ($openAiKey) {
        $envText = Set-DotEnvValue -Text $envText -Name "OPENAI_API_KEY" -Value $openAiKey
        $openAiKey = $null
    }
}

# Not a secret: the fire endpoint URL is shown alongside the token in the same
# modal, and carries only the routine id.
if ($envText -match '(?m)^CLAUDE_ROUTINE_URL=\s*$') {
    $claudeRoutineUrl = (Read-Host "Paste the Claude routine fire URL from claude.ai/code/routines (Enter to skip)").Trim()
    if ($claudeRoutineUrl) {
        if ($claudeRoutineUrl -notmatch '/routines/[^/]+/fire$') {
            throw "The Claude routine URL should end with /routines/<trig_...>/fire. Copy it from the API trigger modal."
        }
        $envText = Set-DotEnvValue -Text $envText -Name "CLAUDE_ROUTINE_URL" -Value $claudeRoutineUrl
    }
}

if ($envText -match '(?m)^CLAUDE_ROUTINE_TOKEN=\s*$') {
    $claudeRoutineToken = Read-ValidatedSecret `
        -Prompt "Paste the Claude routine token from the same API trigger modal (input hidden, Enter to skip)" `
        -RequiredPrefix "sk-ant-oat01-" `
        -ForbiddenMessage "" `
        -Optional
    if ($claudeRoutineToken) {
        $envText = Set-DotEnvValue -Text $envText -Name "CLAUDE_ROUTINE_TOKEN" -Value $claudeRoutineToken
        $claudeRoutineToken = $null
    }
}

# No documented prefix to validate against, so this one is accepted as typed.
if ($envText -match '(?m)^CURSOR_API_KEY=\s*$') {
    $cursorKey = Read-ValidatedSecret `
        -Prompt "Paste the Cursor API key (input hidden, Enter to skip)" `
        -Optional
    if ($cursorKey) {
        $envText = Set-DotEnvValue -Text $envText -Name "CURSOR_API_KEY" -Value $cursorKey
        $cursorKey = $null
    }
}

Set-Content ".env" $envText -Encoding UTF8
$envText = $null

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe ".env" "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to restrict the .env file permissions."
}

docker compose config | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration validation failed."
}

docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to start the Director services."
}

Write-Host "Director Orchestrator started in dry-run mode."
Write-Host "Open http://localhost:5678 to initialize n8n, then import n8n/workflows/director-intake.json."
Write-Host "Do not enable paid runs until provider credentials and spend limits are configured."
