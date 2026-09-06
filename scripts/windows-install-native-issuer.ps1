# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
<# Owner-run installation only. Never invoked by application startup.
   Creates no users, stores no passwords, dispatches no model and generates no key.
   Install once per fixed role after four dedicated non-admin users exist. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('security','independent_review')][string]$Role,
    [Parameter(Mandatory)][string]$BrokerAccount,
    [Parameter(Mandatory)][string]$WorkerAccount,
    [Parameter(Mandatory)][string]$CoordinatorSid,
    [Parameter(Mandatory)][string]$AcceptedSource,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$AcceptedCommit,
    [Parameter(Mandatory)][string]$RepositoryMirror,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository,
    [Parameter(Mandatory)][string]$PythonExecutable,
    [Parameter(Mandatory)][string]$GitExecutable,
    [Parameter(Mandatory)][string]$NativeExecutable,
    [string]$NativeEntrypoint,
    [Parameter(Mandatory)][string]$Model,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$')][string]$Deployment
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Installation requires an owner-controlled elevated administrator session.'
}
function Sid([string]$Account) {
    return ([Security.Principal.NTAccount]::new($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
}
$broker = Sid $BrokerAccount
$worker = Sid $WorkerAccount
if (@($broker,$worker,$CoordinatorSid | Sort-Object -Unique).Count -ne 3) { throw 'Three distinct identities required.' }
$admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object {$_.SID.Value})
if ($broker -in $admins -or $worker -in $admins) { throw 'Issuer identities must not be administrators.' }
foreach ($sid in @($broker,$worker)) {
    if ($sid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'Dedicated local users required.' }
    $null = Get-LocalUser -SID $sid
}
function Assert-Protected([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point forbidden: $($item.FullName)" }
        $acl = Get-Acl -LiteralPath $item.FullName
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -notin @('S-1-5-18','S-1-5-32-544')) { throw "Administrator-owned installation path required: $($item.FullName)" }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            $writes = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writes) -and $rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) {
                throw "Unprotected runtime path: $($item.FullName)"
            }
        }
        $item = if ($item.PSIsContainer) { $item.Parent } else { $item.Directory }
    }
}
# Owner must provision a protected Python/dependency installation and entire
# native installation first; individual executable hashes do not protect imports.
foreach ($p in @($PythonExecutable,$GitExecutable,$NativeExecutable)) { Assert-Protected $p }
if ($NativeEntrypoint) { Assert-Protected $NativeEntrypoint }
$root = Join-Path $env:ProgramData "DirectorAcceptance\$Role"
$root = [IO.Path]::GetFullPath($root)
if (Test-Path -LiteralPath $root) {
    $manifestPath = Join-Path $root 'installation.json'
    Assert-Protected $manifestPath
    $installed = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($installed.commit -cne $AcceptedCommit -or $installed.broker_sid -cne $broker -or $installed.worker_sid -cne $worker) { throw 'Existing installation differs; preserve state and use a separately reviewed upgrade.' }
    foreach ($entry in $installed.files) {
        $path = Join-Path $root $entry.path
        Assert-Protected $path
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.digest) { throw 'Installed code/config changed.' }
    }
    Write-Output 'Matching protected installation already exists; no changes or dispatch.'
    return
}
$source = (Resolve-Path -LiteralPath $AcceptedSource).Path
$head = & $GitExecutable -C $source rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $head -cne $AcceptedCommit) { throw 'Accepted installation source revision mismatch.' }
if (& $GitExecutable -C $source status --porcelain) { throw 'Installation source must be clean.' }
# This read-only local source is a review input, not a remote acquisition command.
$mirrorSource = (Resolve-Path -LiteralPath $RepositoryMirror).Path
$parent = Split-Path -Parent $root
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
    & icacls.exe $parent /inheritance:r /setowner '*S-1-5-32-544' /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Protected installation parent could not be established.' }
}
Assert-Protected $parent
New-Item -ItemType Directory -Path $root | Out-Null
function Set-ExactAcl([string]$Path, [hashtable]$Access) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in $Access.Keys) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),$Access[$sid],'ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
$baseAcl = @{'S-1-5-18'='FullControl';'S-1-5-32-544'='FullControl'}
$rootAcl = $baseAcl.Clone(); $rootAcl[$broker]='ReadAndExecute'; $rootAcl[$worker]='ReadAndExecute'; $rootAcl[$CoordinatorSid]='ReadAndExecute'
Set-ExactAcl $root $rootAcl
foreach ($name in @('code','authorizations','mirror','broker','worker','requests','responses','public','cursor-config')) {
    $path = Join-Path $root $name
    New-Item -ItemType Directory -Path $path | Out-Null
    $access = $baseAcl.Clone()
    switch ($name) {
        'broker' {$access[$broker]='Modify'}
        'worker' {$access[$worker]='Modify'}
        'requests' {$access[$broker]='Modify';$access[$worker]='ReadAndExecute'}
        'responses' {$access[$worker]='Modify';$access[$broker]='ReadAndExecute'}
        'public' {$access[$broker]='Modify';$access[$CoordinatorSid]='ReadAndExecute'}
        'authorizations' {$access[$broker]='ReadAndExecute'}
        default {$access[$broker]='ReadAndExecute';$access[$worker]='ReadAndExecute'}
    }
    Set-ExactAcl $path $access
}
Copy-Item -LiteralPath (Join-Path $source 'src\director_orchestrator') -Destination (Join-Path $root 'code') -Recurse
& $GitExecutable -c "core.hooksPath=NUL" clone --bare --no-hardlinks --local $mirrorSource (Join-Path $root 'mirror\repository.git')
if ($LASTEXITCODE -ne 0) { throw 'Local independent object mirror failed; no key or provider was used.' }
$provider = if ($Role -eq 'security') {'claude'} else {'cursor'}
$config = [ordered]@{
    root=$root;role=$Role;provider=$provider;broker_sid=$broker;worker_sid=$worker;deployment=$Deployment
    mirror=(Join-Path $root 'mirror\repository.git');repository=$Repository;git=$GitExecutable
    executable=$NativeExecutable;executable_digest=(Get-FileHash -LiteralPath $NativeExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
    entrypoint=$(if ($NativeEntrypoint) {$NativeEntrypoint} else {$null})
    entrypoint_digest=$(if ($NativeEntrypoint) {(Get-FileHash -LiteralPath $NativeEntrypoint -Algorithm SHA256).Hash.ToLowerInvariant()} else {$null})
    model=$Model
}
$utf8 = [Text.UTF8Encoding]::new($false)
$configPath = Join-Path $root 'issuer.json'
[IO.File]::WriteAllText($configPath,($config | ConvertTo-Json -Compress),$utf8)
$deny = @('Shell(*)','Read(**)','Read(/*)','Read(/**)','Write(**)','Write(/*)','Write(/**)','WebFetch(*)','Mcp(*:*)')
[IO.File]::WriteAllText((Join-Path $root 'cursor-config\cli-config.json'),(@{permissions=@{allow=@();deny=$deny}} | ConvertTo-Json -Depth 5 -Compress),$utf8)
$configDigest = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
# Fixed launcher has no caller-selectable code, executable, config, or role.
$launcher = @"
param([ValidateSet('prepare','initialize','issue','work','collect')][string]`$Action,[string]`$Job,[string]`$Base,[string]`$Head)
`$env:PYTHONPATH = '$root\code'
`$arguments = @('-s','-m','director_orchestrator.native_acceptance_issuer',`$Action,'--config','$configPath','--config-sha256','$configDigest')
if (`$Job) { `$arguments += @('--job',`$Job) }
if (`$Base) { `$arguments += @('--base',`$Base) }
if (`$Head) { `$arguments += @('--head',`$Head) }
& '$PythonExecutable' @arguments
exit `$LASTEXITCODE
"@
[IO.File]::WriteAllText((Join-Path $root 'invoke.ps1'),$launcher,$utf8)
# Ownership is authority: the coordinator must not retain WRITE_DAC by ownership.
$files = @()
foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in installation tree.' }
    $acl = Get-Acl -LiteralPath $item.FullName
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    Set-Acl -LiteralPath $item.FullName -AclObject $acl
    if (-not $item.PSIsContainer -and $item.FullName -notlike "$root\mirror\*") {
        $files += @{path=$item.FullName.Substring($root.Length+1);digest=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
}
$manifestPath = Join-Path $root 'installation.json'
[IO.File]::WriteAllText($manifestPath,(@{commit=$AcceptedCommit;broker_sid=$broker;worker_sid=$worker;files=$files} | ConvertTo-Json -Depth 5 -Compress),$utf8)
$acl = Get-Acl -LiteralPath $manifestPath
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
Set-Acl -LiteralPath $manifestPath -AclObject $acl
Write-Output "Installed dormant $Role tooling at $root. Config SHA256 $configDigest."
Write-Output 'No key generated and no native login or dispatch occurred. Follow docs/native-acceptance-issuers.md for separate identity enrollment and deny-access checks.'
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]
