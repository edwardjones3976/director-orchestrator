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

$envText = Get-Content ".env" -Raw
$envText = $envText -replace 'ORCHESTRATOR_SHARED_SECRET=REPLACE_WITH_RANDOM_SECRET', ("ORCHESTRATOR_SHARED_SECRET=" + (New-RandomSecret))
$envText = $envText -replace 'N8N_ENCRYPTION_KEY=REPLACE_WITH_RANDOM_SECRET', ("N8N_ENCRYPTION_KEY=" + (New-RandomSecret))
$envText = $envText -replace 'N8N_POSTGRES_PASSWORD=REPLACE_WITH_RANDOM_SECRET', ("N8N_POSTGRES_PASSWORD=" + (New-RandomSecret))
Set-Content ".env" $envText -Encoding UTF8

docker compose config | Out-Null
docker compose up -d --build

Write-Host "Director Orchestrator started in dry-run mode."
Write-Host "Open http://localhost:5678 to initialize n8n, then import n8n/workflows/director-intake.json."
Write-Host "Do not enable paid runs until provider credentials and spend limits are configured."

