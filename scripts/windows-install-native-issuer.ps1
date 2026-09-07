# [[Claude Code | Claude Opus 5 | 2026-09-07 | 04:55 UTC | BEGIN]]
# [[OpenAI Codex Desktop | GPT-6 | 2026-09-07 | 02:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
<# Owner-run installation only. Never invoked by application startup.
   Creates no users, stores no passwords, dispatches no model and generates no key.
   Install once per fixed role after four dedicated non-admin users exist. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('security','independent_review')][string]$Role,
    [ValidateSet('claude','cursor','openai','gemini','antigravity')][string]$Provider,
    [string]$NativeHome,
    [string]$NativeProject,
    [string]$NativeDenyHook,
    [string]$NativeAuthAccountId = '',
    [string]$NativeAuthRegistrySha256 = '',
    [Parameter(Mandatory)][string]$BrokerAccount,
    [Parameter(Mandatory)][string]$WorkerAccount,
    [Parameter(Mandatory)][string]$CoordinatorSid,
    [Parameter(Mandatory)][string]$AcceptedSource,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$AcceptedCommit,
    [Parameter(Mandatory)][string]$RepositoryMirror,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository,
    [Parameter(Mandatory)][string]$PythonExecutable,
    [Parameter(Mandatory)][string[]]$ProtectedRuntimeRoots,
    [Parameter(Mandatory)][string]$GitExecutable,
    [Parameter(Mandatory)][string]$NativeExecutable,
    [string]$NativeEntrypoint,
    [Parameter(Mandatory)][string]$Model,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$')][string]$Deployment
)
$ErrorActionPreference = 'Stop'
if ($Provider -eq 'antigravity') { $Provider = 'gemini' }
if (-not $Provider) { $Provider = if ($Role -eq 'security') {'claude'} else {'cursor'} }
if (($Role -eq 'security' -and $Provider -notin @('claude','openai','gemini')) -or ($Role -eq 'independent_review' -and $Provider -notin @('cursor','gemini'))) { throw 'Provider is not eligible for this issuer role.' }
if ($Provider -eq 'gemini' -and (-not $NativeHome -or -not $NativeProject -or -not $NativeDenyHook)) { throw 'AGY requires separately qualified protected native home, project and deny-all hook.' }
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
function Assert-Protected([string]$Path, [bool]$Runtime = $false) {
    $item = Get-Item -LiteralPath $Path
    $depth = 0
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point forbidden: $($item.FullName)" }
        $acl = Get-Acl -LiteralPath $item.FullName
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        $trusted = @('S-1-5-18','S-1-5-32-544')
        $roleParent = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'DirectorAcceptance')).TrimEnd('\')
        $insideRole = $item.FullName.Equals($roleParent,[StringComparison]::OrdinalIgnoreCase) -or $item.FullName.StartsWith($roleParent+'\',[StringComparison]::OrdinalIgnoreCase)
        if (($Runtime -or $depth -gt 0) -and -not $insideRole) {
            $trusted += 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464' # Windows TrustedInstaller
        }
        if ($owner -notin $trusted) { throw "Administrator-owned installation path required: $($item.FullName)" }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            # InheritOnly does not grant access to the object being inspected.
            if ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) { continue }
            $writes = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($depth -gt 0) {
                # Ancestors must not permit replacing protected descendants;
                # permission to create an unrelated sibling is not replacement.
                $writes = [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            }
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writes) -and $rule.IdentityReference.Value -notin $trusted) {
                throw "Unprotected runtime path: $($item.FullName)"
            }
        }
        # Parent/Directory returns raw .NET objects without PSIsContainer.
        $item = if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
        $depth++
    }
}
# Owner must provision a protected Python/dependency installation and entire
# native installation first; individual executable hashes do not protect imports.
foreach ($p in @($PythonExecutable,$GitExecutable,$NativeExecutable)) { Assert-Protected $p $true }
if ($NativeEntrypoint) { Assert-Protected $NativeEntrypoint $true }
# Audit every loadable sibling, not merely the executable. Roots are owner inputs.
foreach ($runtimeRoot in $ProtectedRuntimeRoots) {
    Assert-Protected $runtimeRoot $true
    $children = @(Get-ChildItem -LiteralPath $runtimeRoot -Recurse -Force)
    if ($children.Count -gt 30000) { throw 'Runtime tree exceeds inspection bound.' }
    foreach ($child in $children) { Assert-Protected $child.FullName $true }
}
foreach ($executable in @($PythonExecutable,$GitExecutable,$NativeExecutable,$NativeEntrypoint) | Where-Object {$_}) {
    if (-not @($ProtectedRuntimeRoots | Where-Object {[IO.Path]::GetFullPath($executable).StartsWith([IO.Path]::GetFullPath($_).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)}).Count) { throw 'Executable outside protected runtime roots.' }
}
# Isolated metadata probe after the complete runtime trees have passed ACL review.
# Base interpreter/stdlib and global site dependencies must all be covered, even
# when the executable is inside a virtual environment.
$pythonPaths = & $PythonExecutable -I -c 'import json,sys; print(json.dumps([sys.base_prefix,sys.prefix,*sys.path]))'
if ($LASTEXITCODE -ne 0) { throw 'Protected Python runtime inspection failed.' }
foreach ($loadPath in ($pythonPaths | ConvertFrom-Json)) {
    if (-not @($ProtectedRuntimeRoots | Where-Object { $a=[IO.Path]::GetFullPath($loadPath); $b=[IO.Path]::GetFullPath($_).TrimEnd('\'); $a.Equals($b,[StringComparison]::OrdinalIgnoreCase) -or $a.StartsWith($b+'\',[StringComparison]::OrdinalIgnoreCase)}).Count) { throw 'Python base or dependency path outside protected runtime roots.' }
}
$root = Join-Path $env:ProgramData "DirectorAcceptance\$Role"
if ($Provider -ne $(if ($Role -eq 'security') {'claude'} else {'cursor'})) { $root += "-$Provider" }
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
$acceptedTree = & $GitExecutable -C $source rev-parse "$AcceptedCommit^{tree}"
if ($LASTEXITCODE -ne 0 -or $acceptedTree -notmatch '^[0-9a-f]{40}$') { throw 'Accepted source tree could not be resolved.' }
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
foreach ($name in @('code','authorizations','mirror','broker','worker','requests','responses','public','cursor-config','native-auth')) {
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
function Install-TrackedPackage([string]$Git, [string]$Source, [string]$Commit, [string]$Destination) {
    # Accepted raw object bytes only. Archive and checkout apply .gitattributes
    # eol/export rules and core.autocrlf, so their bytes are a transformation of
    # the accepted blob rather than the accepted blob itself.
    function Copy-AcceptedBlob([string]$Executable, [string]$Repository, [string]$Blob, [string]$Target) {
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $Executable
        # Blob is a validated hex object name; the repository is selected by
        # working directory so no path is ever parsed as an argument.
        $info.Arguments = "cat-file blob $Blob"
        $info.WorkingDirectory = $Repository
        $info.UseShellExecute = $false
        $info.RedirectStandardOutput = $true
        $info.CreateNoWindow = $true
        $reader = [Diagnostics.Process]::Start($info)
        try {
            $output = [IO.File]::Open($Target,[IO.FileMode]::CreateNew)
            # Byte stream only; a text reader would re-encode the accepted bytes.
            try { $reader.StandardOutput.BaseStream.CopyTo($output) } finally { $output.Dispose() }
            $reader.WaitForExit()
            if ($reader.ExitCode -ne 0) { throw "Accepted tracked blob could not be read: $Blob" }
        } finally { $reader.Dispose() }
    }
    $entries = & $Git -C $Source ls-tree -r $Commit -- src/director_orchestrator
    if ($LASTEXITCODE -ne 0 -or -not $entries) { throw 'Tracked package tree unavailable.' }
    $blobs = [ordered]@{}
    foreach ($entry in $entries) {
        if ($entry -notmatch '^(100644|100755) blob [0-9a-f]{40}\t') { throw 'Only tracked regular files may be installed.' }
        $parts = $entry.Split([char]9,2)
        $name = $parts[1]
        # Quoted or unusual tracked paths fail closed here rather than being decoded.
        if (-not $name.StartsWith('src/director_orchestrator/') -or $name.Contains('\') -or $name.Contains('"') -or @($name.Split('/') | Where-Object {$_ -in @('','.','..')}).Count) { throw 'Unsafe tracked package path.' }
        if ($blobs.Contains($name)) { throw 'Duplicate tracked package path.' }
        $blobs[$name] = $parts[0].Split(' ')[2]
    }
    foreach ($name in @($blobs.Keys)) {
        $target = Join-Path $Destination $name.Substring(4)
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
        Copy-AcceptedBlob $Git $Source $blobs[$name] $target
        $actual = & $Git hash-object --no-filters -- $target
        if ($LASTEXITCODE -ne 0) { throw "Installed tracked file could not be hashed: $name" }
        if ($actual -cne $blobs[$name]) {
            # Bounded diagnostic: accepted tracked path and both object names only.
            throw "Installed bytes differ from accepted tracked blob: $name expected $($blobs[$name]) actual $actual"
        }
    }
    # Every accepted blob was written and verified; nothing else may be present.
    $installed = @(Get-ChildItem -LiteralPath $Destination -Recurse -Force -File)
    if ($installed.Count -ne $blobs.Count) { throw "Installed tracked file set differs from accepted tree: expected $($blobs.Count) actual $($installed.Count)" }
}
Install-TrackedPackage $GitExecutable $source $AcceptedCommit (Join-Path $root 'code')
& $GitExecutable -c "core.hooksPath=NUL" clone --bare --no-hardlinks --local $mirrorSource (Join-Path $root 'mirror\repository.git')
if ($LASTEXITCODE -ne 0) { throw 'Local independent object mirror failed; no key or provider was used.' }
$provider = $Provider
$config = [ordered]@{
    root=$root;role=$Role;provider=$provider;broker_sid=$broker;worker_sid=$worker;deployment=$Deployment
    mirror=(Join-Path $root 'mirror\repository.git');repository=$Repository;git=$GitExecutable
    executable=$NativeExecutable;executable_digest=(Get-FileHash -LiteralPath $NativeExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
    entrypoint=$(if ($NativeEntrypoint) {$NativeEntrypoint} else {$null})
    entrypoint_digest=$(if ($NativeEntrypoint) {(Get-FileHash -LiteralPath $NativeEntrypoint -Algorithm SHA256).Hash.ToLowerInvariant()} else {$null})
    model=$Model;runtime_roots=@($ProtectedRuntimeRoots)
    native_home=$NativeHome;native_project=$NativeProject;native_deny_hook=$NativeDenyHook
    native_auth_account_id=$NativeAuthAccountId;native_auth_registry_sha256=$NativeAuthRegistrySha256
    native_deny_hook_digest=$(if ($NativeDenyHook) {(Get-FileHash -LiteralPath $NativeDenyHook -Algorithm SHA256).Hash.ToLowerInvariant()} else {''})
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
Get-ChildItem Env: | Where-Object {`$_.Name -like 'PYTHON*'} | Remove-Item
`$env:PYTHONPATH = '$root\code'
`$arguments = @('-P','-s','-m','director_orchestrator.native_acceptance_issuer',`$Action,'--config','$configPath','--config-sha256','$configDigest')
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
[IO.File]::WriteAllText($manifestPath,(@{commit=$AcceptedCommit;tree=$acceptedTree;broker_sid=$broker;worker_sid=$worker;files=$files} | ConvertTo-Json -Depth 5 -Compress),$utf8)
$acl = Get-Acl -LiteralPath $manifestPath
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
Set-Acl -LiteralPath $manifestPath -AclObject $acl
Write-Output "Installed dormant $Role tooling at $root. Config SHA256 $configDigest."
Write-Output 'No key generated and no native login or dispatch occurred. Follow docs/native-acceptance-issuers.md for separate identity enrollment and deny-access checks.'
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:40 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:45 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | END]]

# [[OpenAI Codex Desktop | GPT-6 | 2026-09-07 | 02:34 UTC | END]]

# [[Claude Code | Claude Opus 5 | 2026-09-07 | 04:55 UTC | END]]
