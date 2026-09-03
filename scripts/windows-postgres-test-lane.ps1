# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Status", "Dsn", "Test")]
    [string]$Action,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')][string]$ContainerName = "director-primary-test-pg",
    [ValidateRange(1024, 65535)][int]$HostPort = 55433,
    [ValidateRange(5, 900)][int]$TimeoutSeconds = 60,
    [string]$Python = ".venv\Scripts\python.exe",
    [string[]]$TestNames = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot

# These literals mirror the "Start disposable PostgreSQL (Linux)" step in
# .github/workflows/ci.yml and the primary database digest in docker-compose.yml;
# tests/test_windows_primary_activation_tools.py asserts they stay identical.
# Trust authentication is acceptable only because the port is bound to loopback
# and the database holds throwaway test data.
$image = "postgres:17-alpine@sha256:d4bb0a8c1b7bb2e29f976d099e7bfb9a5d8858cffe9e46b35cd302cd1f1f8168"
$databaseUser = "director_ci"
$databaseName = "director_test"
$laneLabel = "director-orchestrator.postgres-test-lane"
$dsnVariable = "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"
$containerVariable = "LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER"
$dsn = "postgresql://${databaseUser}@127.0.0.1:${HostPort}/${databaseName}?sslmode=disable"

function Assert-DockerReachable {
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not reachable; the disposable PostgreSQL lane needs the Docker daemon." }
}

function Get-LaneContainer {
    # $null when absent, else @{ State; Owned }. docker ps writes nothing to
    # stderr for an absent name, so Windows PowerShell 5.1 cannot turn it into a
    # terminating error; the name filter is a substring match, so compare exactly.
    $rows = @(& docker ps -a --filter "name=$ContainerName" --format '{{.Names}}|{{.State}}|{{.Labels}}')
    if ($LASTEXITCODE -ne 0) { throw "Docker container listing failed." }
    foreach ($row in $rows) {
        $parts = [string]$row -split '\|', 3
        if ($parts.Count -lt 3) { continue }
        if ($parts[0] -cne $ContainerName) { continue }
        $owned = (",$($parts[2])," -like "*,$laneLabel=true,*")
        return @{ State = $parts[1]; Owned = $owned }
    }
    return $null
}

function Assert-LaneOwned {
    param([hashtable]$Container)
    if (-not $Container.Owned) {
        throw "Container '$ContainerName' exists but was not created by this script; refusing to touch it. Remove or rename it yourself, or pass a different -ContainerName."
    }
}

function Test-LaneReady {
    # Probe only: pg_isready is expected to fail while PostgreSQL starts, so the
    # redirected stderr must not become a terminating error under 5.1. The TCP
    # probe inside the container skips the initdb-phase socket-only server, and
    # the host probe proves the Docker Desktop port forward is up.
    $ErrorActionPreference = "Continue"
    & docker exec $ContainerName pg_isready -h 127.0.0.1 -U $databaseUser -d $databaseName 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { return $false }
    $client = New-Object Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $HostPort); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}

function Wait-LaneReady {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-LaneReady) { return }
        Start-Sleep -Seconds 1
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Disposable PostgreSQL did not become ready before the deadline."
}

function Export-LaneEnvironment {
    [Environment]::SetEnvironmentVariable($dsnVariable, $dsn, "Process")
    [Environment]::SetEnvironmentVariable($containerVariable, $ContainerName, "Process")
    Write-Host "`$env:$dsnVariable = `"$dsn`""
    Write-Host "`$env:$containerVariable = `"$ContainerName`""
}

function Clear-LaneEnvironment {
    [Environment]::SetEnvironmentVariable($dsnVariable, $null, "Process")
    [Environment]::SetEnvironmentVariable($containerVariable, $null, "Process")
}

function Remove-LaneContainer {
    & docker rm --force --volumes $ContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The disposable PostgreSQL container could not be removed." }
}

function Start-Lane {
    Assert-DockerReachable
    $existing = Get-LaneContainer
    if ($null -ne $existing) {
        Assert-LaneOwned -Container $existing
        # Always begin from a fresh data directory so the role baked into the
        # container equals the DSN user; a leftover is never restarted.
        Remove-LaneContainer
    }
    $publishers = @(& docker ps --filter "publish=$HostPort" --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { throw "Docker container listing failed." }
    if ($publishers.Count -gt 0) {
        throw "Host port $HostPort is already published by container(s): $($publishers -join ', '). Choose another -HostPort or stop them yourself; this script never removes containers it did not create."
    }
    & docker run --detach --rm --name $ContainerName --label "$laneLabel=true" --publish "127.0.0.1:${HostPort}:5432" --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_USER=$databaseUser" --env "POSTGRES_DB=$databaseName" $image | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The disposable PostgreSQL container could not be started." }
    try { Wait-LaneReady }
    catch { Remove-LaneContainer; throw }
    Export-LaneEnvironment
    Write-Host "Disposable PostgreSQL lane '$ContainerName' is ready on 127.0.0.1:$HostPort (trust auth, loopback only, no volume, no password)."
}

function Stop-Lane {
    $existing = Get-LaneContainer
    if ($null -eq $existing) {
        Clear-LaneEnvironment
        Write-Host "No container named '$ContainerName' exists; nothing to remove."
        return
    }
    Assert-LaneOwned -Container $existing
    Remove-LaneContainer
    Clear-LaneEnvironment
    Write-Host "Disposable PostgreSQL lane '$ContainerName' was removed."
}

if ($Action -eq "Start") { Start-Lane; return }
if ($Action -eq "Stop") { Assert-DockerReachable; Stop-Lane; return }
if ($Action -eq "Status") {
    Assert-DockerReachable
    $existing = Get-LaneContainer
    if ($null -eq $existing) { Write-Host "Container: $ContainerName (absent)"; return }
    $ready = $false
    if ($existing.Owned -and $existing.State -eq "running") { $ready = Test-LaneReady }
    Write-Host "Container: $ContainerName"
    Write-Host "State: $($existing.State)"
    Write-Host "Owned by this script: $($existing.Owned)"
    Write-Host "Ready: $ready"
    Write-Host "Published: 127.0.0.1:$HostPort"
    Write-Host "DSN: $dsn"
    return
}
if ($Action -eq "Dsn") {
    Assert-DockerReachable
    $existing = Get-LaneContainer
    if ($null -eq $existing) { throw "No container named '$ContainerName' exists; run -Action Start first." }
    Assert-LaneOwned -Container $existing
    if (-not (Test-LaneReady)) { throw "The disposable PostgreSQL lane is not accepting connections." }
    Export-LaneEnvironment
    return
}

# Test: start the lane, run unittest from the repository root with this
# process's environment, always remove the lane and restore the environment.
$originalProcessEnvironment = @{}
foreach ($name in @("PYTHONPATH", "SQLITE_PATH", $dsnVariable, $containerVariable)) {
    $originalProcessEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
Push-Location -LiteralPath $repoRoot
Start-Lane
$testExit = 1
try {
    $pythonPath = (Resolve-Path -LiteralPath $Python).Path
    [Environment]::SetEnvironmentVariable("PYTHONPATH", "src", "Process")
    $sqlitePath = Join-Path ([IO.Path]::GetTempPath()) "director-postgres-test-lane-$([Guid]::NewGuid().ToString('N')).sqlite3"
    [Environment]::SetEnvironmentVariable("SQLITE_PATH", $sqlitePath, "Process")
    if (@($TestNames).Count -gt 0) { & $pythonPath -m unittest -v @TestNames }
    else { & $pythonPath -m unittest discover -s tests -v }
    $testExit = $LASTEXITCODE
}
finally {
    Stop-Lane
    foreach ($entry in $originalProcessEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
    Pop-Location
}
if ($testExit -ne 0) { throw "Unit tests failed with exit code $testExit; the disposable PostgreSQL lane was removed." }
Write-Host "Unit tests passed against the disposable PostgreSQL lane; the container was removed."
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | END]]
