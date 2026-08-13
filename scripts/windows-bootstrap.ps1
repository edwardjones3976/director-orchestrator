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

function Read-ValidatedSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$RequiredPrefix
    )

    $secureValue = Read-Host $Prompt -AsSecureString
    $valuePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($valuePointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($valuePointer)
    }

    if (-not $plainValue.StartsWith($RequiredPrefix, [StringComparison]::Ordinal)) {
        throw "The supplied value for $Prompt does not begin with $RequiredPrefix."
    }

    return $plainValue
}

function Set-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $escapedName = [Regex]::Escape($Name)
    return [Regex]::Replace($Text, "(?m)^${escapedName}=.*$", "${Name}=${Value}")
}

$envText = Get-Content ".env" -Raw
$envText = $envText -replace 'ORCHESTRATOR_SHARED_SECRET=REPLACE_WITH_RANDOM_SECRET', ("ORCHESTRATOR_SHARED_SECRET=" + (New-RandomSecret))
$envText = $envText -replace 'N8N_ENCRYPTION_KEY=REPLACE_WITH_RANDOM_SECRET', ("N8N_ENCRYPTION_KEY=" + (New-RandomSecret))
$envText = $envText -replace 'N8N_POSTGRES_PASSWORD=REPLACE_WITH_RANDOM_SECRET', ("N8N_POSTGRES_PASSWORD=" + (New-RandomSecret))

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

Set-Content ".env" $envText -Encoding UTF8
$envText = $null

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe ".env" "/inheritance:r" "/grant:r" "${currentIdentity}:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to restrict the .env file permissions."
}

docker compose config | Out-Null
docker compose up -d --build

Write-Host "Director Orchestrator started in dry-run mode."
Write-Host "Open http://localhost:5678 to initialize n8n, then import n8n/workflows/director-intake.json."
Write-Host "Do not enable paid runs until provider credentials and spend limits are configured."
