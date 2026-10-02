# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 23:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 16:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 17:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 01:54 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | BEGIN]]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Status", "Dsn", "Test")]
    [string]$Action,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')][string]$ContainerName = "director-primary-test-pg",
    [ValidateRange(1024, 65535)][int]$HostPort = 55433,
    [ValidateRange(5, 900)][int]$TimeoutSeconds = 60,
    [ValidateRange(1, 86400)][int]$SlotWaitSeconds = 2700,
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$Tls,
    [ValidateRange(1, 256)][int]$Parallel,
    [string[]]$TestNames = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($PSBoundParameters.ContainsKey('Parallel') -and ($Action -ne 'Test' -or @($TestNames).Count -gt 0)) {
    throw '-Parallel requires -Action Test and full discovery (no -TestNames).'
}

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
$caVariable = "LANGGRAPH_RUNTIME_POSTGRES_TEST_CA"
$script:tlsDirectory = $null
$script:laneInstanceId = $null
$dsn = "postgresql://${databaseUser}@127.0.0.1:${HostPort}/${databaseName}?sslmode=disable"

function New-LaneTls {
    $script:tlsDirectory = Join-Path ([IO.Path]::GetTempPath()) ("director-test-tls-" + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($script:tlsDirectory) | Out-Null
    $pythonPath = $Python
    if (-not [IO.Path]::IsPathRooted($pythonPath)) { $pythonPath = Join-Path $repoRoot $pythonPath }
    # Code on stdin, directory in argv; no keys or certificates on stdout.
    @'
import datetime, ipaddress, pathlib, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
root = pathlib.Path(sys.argv[1])
now = datetime.datetime.now(datetime.timezone.utc)
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Disposable test lane CA')])
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
def builder(subject, public_key):
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name)
            .public_key(public_key).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1)))
ca = builder(ca_name, ca_key.public_key()).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(ca_key, hashes.SHA256())
cert = (builder(name, key.public_key())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))
(root / 'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
(root / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(root / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
(root / 'server.key').chmod(0o600)
'@ | & $pythonPath - $script:tlsDirectory
    if ($LASTEXITCODE -ne 0) { throw "Disposable TLS certificate generation failed." }
}

function Read-LaneTls {
    # Called only after the existing container passed Assert-LaneOwned.
    # Docker Desktop can translate mount sources to VM paths. Retain the host
    # spelling in a label so Stop in a fresh shell can clean the exact directory.
    $encoded = & docker inspect --format '{{index .Config.Labels "director-orchestrator.test-lane-tls-directory"}}' $ContainerName
    if ($LASTEXITCODE -ne 0 -or -not $encoded) { throw "Lane TLS directory inspection failed." }
    $script:tlsDirectory = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
}

function Remove-LaneTls {
    if (-not $script:tlsDirectory) { return }
    $target = [IO.Path]::GetFullPath($script:tlsDirectory)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ((Split-Path -Parent $target) -ne $tempRoot -or (Split-Path -Leaf $target) -notmatch '^director-test-tls-[0-9a-f]{32}$') {
        throw "Refusing cleanup outside the disposable TLS directory."
    }
    if (Test-Path -LiteralPath $target) {
        $item = Get-Item -LiteralPath $target
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked TLS directory." }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    $script:tlsDirectory = $null
}

function Assert-DockerReachable {
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not reachable; the disposable PostgreSQL lane needs the Docker daemon." }
}

function Get-LaneContainer {
    # $null when absent, else @{ State; Owned }. docker ps writes nothing to
    # stderr for an absent name, so Windows PowerShell 5.1 cannot turn it into a
    # terminating error; the name filter is a substring match, so compare exactly.
    $rows = @(& docker ps -a --filter "name=$ContainerName" --format '{{.ID}}|{{.Names}}|{{.State}}|{{.Labels}}')
    if ($LASTEXITCODE -ne 0) { throw "Docker container listing failed." }
    foreach ($row in $rows) {
        $parts = [string]$row -split '\|', 4
        if ($parts.Count -lt 4) { continue }
        if ($parts[1] -cne $ContainerName) { continue }
        $owned = (",$($parts[3])," -like "*,$laneLabel=true,*")
        return @{ Id = $parts[0]; State = $parts[2]; Owned = $owned; Tls = (",$($parts[3])," -like '*,director-orchestrator.test-lane-tls=true,*') }
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
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker exec $ContainerName pg_isready -h 127.0.0.1 -U $databaseUser -d $databaseName 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        $client = New-Object Net.Sockets.TcpClient
        try { $client.Connect("127.0.0.1", $HostPort); return $true }
        catch { return $false }
        finally { $client.Dispose() }
    }
    finally { $ErrorActionPreference = $priorErrorActionPreference }
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
    if ($script:tlsDirectory) {
        $caPath = Join-Path $script:tlsDirectory 'ca.crt'
        if (-not (Test-Path -LiteralPath $caPath -PathType Leaf)) { throw "Lane CA is unavailable." }
        $script:dsn = "postgresql://${databaseUser}@127.0.0.1:${HostPort}/${databaseName}?sslmode=verify-full&sslrootcert=$([Uri]::EscapeDataString($caPath))"
        [Environment]::SetEnvironmentVariable($caVariable, $caPath, "Process")
    } else {
        [Environment]::SetEnvironmentVariable($caVariable, $null, "Process")
    }
    [Environment]::SetEnvironmentVariable($dsnVariable, $dsn, "Process")
    [Environment]::SetEnvironmentVariable($containerVariable, $ContainerName, "Process")
    Write-Host "PostgreSQL test connection environment exported (values withheld)."
    Write-Host "`$env:$containerVariable = `"$ContainerName`""
}

function Clear-LaneEnvironment {
    [Environment]::SetEnvironmentVariable($caVariable, $null, "Process")
    [Environment]::SetEnvironmentVariable($dsnVariable, $null, "Process")
    [Environment]::SetEnvironmentVariable($containerVariable, $null, "Process")
}

function Remove-LaneContainer {
    & docker rm --force --volumes $ContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The disposable PostgreSQL container could not be removed." }
}

function Get-LaneStateDirectory {
    $root = $env:LOCALAPPDATA
    if (-not $root) { $root = $env:XDG_STATE_HOME }
    if (-not $root -and $env:HOME) { $root = Join-Path $env:HOME '.local/state' }
    if (-not $root) { $root = $env:RUNNER_TEMP }
    if (-not $root) { throw "No test lane state directory is available." }
    $directory = Join-Path $root 'director-orchestrator/test-lanes'
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    return $directory
}

function Get-SlotInventory {
    # One daemon snapshot includes reservations and their pre-created owners.
    $rows = @(& docker ps -a --no-trunc --format '{{.ID}}|{{.Names}}|{{.State}}|{{.Labels}}')
    if ($LASTEXITCODE -ne 0) { throw "Docker container listing failed." }
    foreach ($row in $rows) {
        $parts = [string]$row -split '\|', 4
        if ($parts.Count -ne 4) { throw "Invalid Docker slot inventory." }
        $labels = @{}
        foreach ($label in ($parts[3] -split ',')) {
            $pair = $label -split '=', 2
            if ($pair.Count -eq 2) { $labels[$pair[0]] = $pair[1] }
        }
        [pscustomobject]@{ Id = $parts[0]; Name = $parts[1]; State = $parts[2]; Owner = $labels['director-orchestrator.test-lane-slot']; Instance = $labels['director-orchestrator.test-lane-instance']; Lane = $labels['director-orchestrator.postgres-test-lane']; Managed = $labels['director-orchestrator.test-lane-managed'] }
    }
}

function Remove-SlotContainer {
    param([string]$Id)
    # Immutable IDs prevent removing another caller's replacement reservation.
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker rm --volumes $Id 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        $remaining = @(& docker ps -a -q --no-trunc --filter "id=$Id" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $remaining.Count -eq 0) { return }
        throw "The PostgreSQL test lane slot could not be removed."
    }
    finally { $ErrorActionPreference = $priorErrorActionPreference }
}

function Resolve-LaneInstanceId {
    $current = $null
    foreach ($item in @(Get-SlotInventory)) {
        if ($item.Name -ceq $ContainerName -and $item.Lane -eq 'true' -and $item.Managed -eq 'true') {
            $current = [string]$item.Id
            break
        }
    }
    if ($script:laneInstanceId -and ((-not $current) -or ($script:laneInstanceId -ceq $current))) {
        return [string]$script:laneInstanceId
    }
    if ($current) {
        $script:laneInstanceId = $current
        return $current
    }
    if ($script:laneInstanceId) { return [string]$script:laneInstanceId }
    return $null
}

function Remove-LaneSqlite {
    param([string]$Path)
    if (-not $Path) { return }
    foreach ($suffix in @('', '-wal', '-shm')) {
        $target = "$Path$suffix"
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    }
}

function Enter-LaneSlot {
    $limit = 2
    if ($env:GITHUB_ACTIONS -eq 'true') { $limit = 4 }
    if ($null -ne $env:DIRECTOR_TEST_LANE_MAX_CONCURRENCY) {
        if (-not [int]::TryParse($env:DIRECTOR_TEST_LANE_MAX_CONCURRENCY, [ref]$limit) -or $limit -lt 1) {
            throw "DIRECTOR_TEST_LANE_MAX_CONCURRENCY must be a positive integer."
        }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $inventory = @(Get-SlotInventory)
        foreach ($slot in $inventory | Where-Object { $_.Owner -and $_.Name -match '^director-test-lane-slot-[1-9][0-9]*$' }) {
            $owner = @($inventory | Where-Object { $_.Name -ceq $slot.Owner })
            if ($owner.Count -eq 0) { Remove-SlotContainer $slot.Id; continue }
            # A managed lane runs for the whole of its job. One that has exited
            # or died belongs to a job the daemon or runner already lost (for
            # example a Docker Desktop outage, which skips the Stop step), so it
            # can never release its slot: reclaim the slot and the dead lane.
            # A lane still in the created state may be a neighbour between its
            # create and start, and a lane this script did not create is never
            # touched.
            if ($owner[0].Lane -eq 'true' -and $owner[0].Managed -eq 'true' -and $owner[0].State -in @('exited', 'dead')) {
                Remove-SlotContainer $slot.Id
                Remove-SlotContainer $owner[0].Id
            }
        }
        for ($number = 1; $number -le $limit; $number++) {
            # Daemon name uniqueness is atomic across Windows and WSL.
            # The owner exists before reservation, closing the reclamation race.
            $priorErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $instanceId = Resolve-LaneInstanceId
                if (-not $instanceId) { throw "PostgreSQL test lane instance is unknown." }
                $id = & docker create --pull=never --name "director-test-lane-slot-$number" --label "director-orchestrator.test-lane-slot=$ContainerName" --label "director-orchestrator.test-lane-instance=$instanceId" $image 2>$null
                $created = $LASTEXITCODE -eq 0
            }
            finally { $ErrorActionPreference = $priorErrorActionPreference }
            if (-not $created) { continue }
            # High-number CI slots and legacy lanes count against local caps too.
            $inventory = @(Get-SlotInventory)
            $reservationNames = @($inventory | Where-Object { $_.Name -match '^director-test-lane-slot-[1-9][0-9]*$' -and $_.Owner } | ForEach-Object { $_.Owner })
            $orphanedManaged = @($inventory | Where-Object {
                $_.Lane -eq 'true' -and $_.Managed -eq 'true' -and $_.State -notin @('exited', 'dead') -and ($reservationNames -notcontains $_.Name)
            })
            $occupied = @($inventory | Where-Object { $_.Owner -or ($_.Lane -eq 'true' -and $_.Managed -ne 'true') }) + $orphanedManaged
            if ($occupied.Count -le $limit) { return }
            Remove-SlotContainer ([string]$id)
        }
        if ($timer.Elapsed.TotalSeconds -ge $SlotWaitSeconds) {
            throw "Timed out waiting for a PostgreSQL test lane slot after $SlotWaitSeconds seconds (cap $limit)."
        }
        Write-Host "Waiting for a PostgreSQL test lane slot (cap $limit; $([int]$timer.Elapsed.TotalSeconds)s elapsed)."
        Start-Sleep -Milliseconds ([int][Math]::Min(15000, [Math]::Max(1, ($SlotWaitSeconds - $timer.Elapsed.TotalSeconds) * 1000)))
    } while ($true)
}

function Remove-LaneSlot {
    # The stored instance id is this process's owner. A same-name replacement
    # has a different id, so this Stop cannot delete that reservation.
    $instance = [string]$script:laneInstanceId
    if (-not $instance) {
        $existing = Get-LaneContainer
        if ($null -ne $existing -and $existing.Contains('Id') -and $existing.Id) {
            $instance = [string]$existing.Id
        }
    }
    if (-not $instance) { return }
    foreach ($slot in @(Get-SlotInventory)) {
        if ($slot.Instance -ceq $instance -and $slot.Name -match '^director-test-lane-slot-[1-9][0-9]*$') {
            Remove-SlotContainer $slot.Id
        }
    }
}

function Start-Lane {
    Assert-DockerReachable
    $existing = Get-LaneContainer
    if ($null -ne $existing) {
        Assert-LaneOwned -Container $existing
        throw "Container '$ContainerName' already belongs to a lane; run -Action Stop first or choose a distinct name."
    }
    $publishers = @(& docker ps --filter "publish=$HostPort" --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { throw "Docker container listing failed." }
    if ($publishers.Count -gt 0) {
        throw "Host port $HostPort is already published; choose another -HostPort or stop your own lane."
    }
    $created = $false
    try {
        $tlsOptions = @()
        $serverCommand = @()
        if ($Tls) {
            New-LaneTls
            $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script:tlsDirectory))
            $tlsOptions = @('--label', 'director-orchestrator.test-lane-tls=true', '--label', "director-orchestrator.test-lane-tls-directory=$encoded", '--mount', "type=bind,source=$script:tlsDirectory,target=/lane-tls,readonly", '--entrypoint', 'sh')
            $serverCommand = @('-c', 'set -eu; mkdir -p /tmp/lane-tls; cp /lane-tls/server.crt /lane-tls/server.key /tmp/lane-tls/; chown -R postgres:postgres /tmp/lane-tls; chmod 0700 /tmp/lane-tls; chmod 0600 /tmp/lane-tls/server.key; exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/lane-tls/server.crt -c ssl_key_file=/tmp/lane-tls/server.key')
        }
        # Create the owner first, but do not start PostgreSQL until admission.
        $createdId = & docker create --name $ContainerName --label "$laneLabel=true" --label "director-orchestrator.test-lane-managed=true" --publish ("127.0.0.1:" + $HostPort + ":5432") --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_USER=$databaseUser" --env "POSTGRES_DB=$databaseName" @tlsOptions $image @serverCommand
        if ($LASTEXITCODE -ne 0) { throw "The disposable PostgreSQL container could not be created." }
        $script:laneInstanceId = ([string]$createdId).Trim()
        if (-not $script:laneInstanceId) { throw "The disposable PostgreSQL container did not return an instance id." }
        $created = $true
        Enter-LaneSlot
        & docker start $ContainerName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "The disposable PostgreSQL container could not be started." }
        Wait-LaneReady
        Export-LaneEnvironment
        Write-Host "Disposable PostgreSQL lane is ready."
    }
    catch {
        try {
            if ($created) { Remove-LaneContainer }
            Remove-LaneTls
            if ($created) { Remove-LaneSlot }
            Clear-LaneEnvironment
        }
        catch { Write-Warning "Could not verify lane cleanup; slot retained." }
        throw
    }
}

function Stop-Lane {
    $existing = Get-LaneContainer
    if ($null -ne $existing) {
        Assert-LaneOwned -Container $existing
        if ($existing.Tls) { Read-LaneTls }
        Remove-LaneContainer
    }
    Remove-LaneTls
    Remove-LaneSlot
    Clear-LaneEnvironment
    Write-Host "Disposable PostgreSQL lane was removed or confirmed absent."
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
    return
}
if ($Action -eq "Dsn") {
    Assert-DockerReachable
    $existing = Get-LaneContainer
    if ($null -eq $existing) { throw "No container named '$ContainerName' exists; run -Action Start first." }
    Assert-LaneOwned -Container $existing
    if ($existing.Tls) { Read-LaneTls }
    if (-not (Test-LaneReady)) { throw "The disposable PostgreSQL lane is not accepting connections." }
    Export-LaneEnvironment
    return
}

# Test: start the lane, run unittest from the repository root with this
# process's environment, always remove the lane and restore the environment.
$originalProcessEnvironment = @{}
foreach ($name in @("PYTHONPATH", "SQLITE_PATH", $dsnVariable, $containerVariable, $caVariable)) {
    $originalProcessEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
Push-Location -LiteralPath $repoRoot
$testExit = 1
$laneRemoved = $false
$laneStarted = $false
$sqlitePath = $null
try {
    # Start inside the try so a refused, unreachable, or timed-out lane still
    # restores the caller's working directory and environment on the way out.
    Start-Lane
    $laneStarted = $true
    $pythonPath = (Resolve-Path -LiteralPath $Python).Path
    [Environment]::SetEnvironmentVariable("PYTHONPATH", "src", "Process")
    $sqlitePath = Join-Path (Get-LaneStateDirectory) "director-postgres-test-lane-$([Guid]::NewGuid().ToString('N')).sqlite3"
    [Environment]::SetEnvironmentVariable("SQLITE_PATH", $sqlitePath, "Process")
    if ($PSBoundParameters.ContainsKey('Parallel')) { & $pythonPath scripts/run-tests-parallel.py --shards $Parallel }
    elseif (@($TestNames).Count -gt 0) { & $pythonPath -m unittest -v @TestNames }
    else { & $pythonPath -m unittest discover -s tests -v }
    $testExit = $LASTEXITCODE
}
finally {
    # A cleanup failure must not mask the error that ended the run, and it must
    # not be reported as a removal either: a surviving container keeps the
    # published port, and the next -Action Start refuses a lane it does not own.
    if ($laneStarted) {
        try { Stop-Lane; $laneRemoved = $true } catch { Write-Warning $_.Exception.Message }
    }
    foreach ($entry in $originalProcessEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
    if ($sqlitePath) {
        try { Remove-LaneSqlite $sqlitePath } catch { Write-Warning $_.Exception.Message }
    }
    Pop-Location
}
if ($laneRemoved) { $laneOutcome = "the disposable PostgreSQL lane was removed" }
else { $laneOutcome = "the disposable PostgreSQL lane could not be removed and may still be running; rerun -Action Stop" }
if ($testExit -ne 0) { throw "Unit tests failed with exit code $testExit; $laneOutcome." }
if (-not $laneRemoved) { throw "Unit tests passed but $laneOutcome." }
Write-Host "Unit tests passed against the disposable PostgreSQL lane; the container was removed."
# [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:35 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:28 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 17:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 16:54 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 23:43 UTC | END]]
