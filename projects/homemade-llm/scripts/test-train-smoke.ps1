# [[OpenAI Codex | GPT-6 | 2026-09-27 | 03:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | BEGIN]]
# Checks that the shared Invoke-Bounded (invoke-bounded.ps1) cannot hang on a stalled CLI and leaves no child
# process behind (no Docker needed: a fake docker.cmd that never returns is put first on PATH), then the
# train-smoke.ps1 refusals.
#   pwsh -File projects\homemade-llm\scripts\test-train-smoke.ps1
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'train-smoke.ps1'), [ref]$tokens, [ref]$errors)
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')
function Get-ShimPing { @(Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object CommandLine -match '-n 600 127\.0\.0\.1') }

$shim = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $shim | Out-Null
$oldPath = $env:PATH
try {
    Set-Content (Join-Path $shim 'docker.cmd') '@ping -n 600 127.0.0.1 >nul'
    $env:PATH = "$shim;$oldPath"
    # Every test that can leave a marker uses a private path and catches the throw, so a failing check reports a FAIL
    # instead of crashing the run or poisoning the real machine-wide marker.
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'hung-marker.txt'
    $t = [Diagnostics.Stopwatch]::StartNew()
    $r = @('__EXIT__threw'); try { $r = @(Invoke-Bounded 'docker' @('stop', 'x') 3) } catch { }
    $t.Stop()
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    Start-Sleep 1
    $orphans = (Get-ShimPing).Count
    $fail = @()
    if ($errors.Count) { $fail += 'train-smoke.ps1 has parse errors' }
    if ($r[-1] -ne '__EXIT__timeout') { $fail += "hung docker: expected __EXIT__timeout, got '$($r[-1])'" }
    if ($t.Elapsed.TotalSeconds -gt 15) { $fail += "hung docker: returned after $([int]$t.Elapsed.TotalSeconds) s (bound 3 s)" }
    if ($orphans) { $fail += "hung docker: $orphans child process(es) still running after the timeout" }
    # Cancellation (Ctrl+C or a stopped pipeline) while waiting must also kill the command's tree.
    $rs = [powershell]::Create()
    $null = $rs.AddScript(". '$(Join-Path $PSScriptRoot 'invoke-bounded.ps1')'; `$GpuMarker = '$(Join-Path $shim 'rs-marker.txt')'; `$env:PATH = '$shim;' + `$env:PATH; Invoke-Bounded 'docker' @('stop', 'y') 600")
    $h = $rs.BeginInvoke()
    for ($i = 0; $i -lt 50 -and -not (Get-ShimPing).Count; $i++) { Start-Sleep -Milliseconds 200 }
    $rs.Stop(); $rs.Dispose()
    Start-Sleep 1
    if ((Get-ShimPing).Count) { $fail += "cancelled wait: child process still running after PowerShell.Stop()" }
    # A tree the job still counts as running after the kill must fail closed (throw), never look clean.
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'survivor-marker.txt'
    function Get-BoundedJobActive([IntPtr]$Job) { 1 }   # a survivor the kill did not reach
    $threw = $false
    try { $null = Invoke-Bounded 'docker' @('stop', 'z') 3 } catch { $threw = $true }
    Start-Sleep 1
    if (-not $threw) { $fail += 'unconfirmed kill: Invoke-Bounded returned instead of failing closed' }
    if ((Get-ShimPing).Count) { $fail += 'unconfirmed kill: the command was left running (kill skipped)' }
    # ...and it persists the machine-wide guard itself, so a caller unwinding through finally cannot release the lock
    # unguarded. (A private marker path: the test never touches the real one.)
    if (-not ((Test-Path -LiteralPath $GpuMarker) -and ((Get-Content -LiteralPath $GpuMarker -Raw) -match 'could not be confirmed stopped'))) { $fail += 'unconfirmed kill: no machine-wide marker was written before the throw' }
    elseif (-not $BoundedUnconfirmed) { $fail += 'unconfirmed kill: BoundedUnconfirmed was not set' }
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    # Marker-write failure: with no guard at all (an uncreatable marker path) the callers are told to keep the lock;
    # with a guard already present (written by the scripts before any bounded command) a failed append is harmless.
    $realMarker = $GpuMarker
    $GpuMarker = Join-Path $shim 'no|such*dir\gpu-poisoned.txt'   # invalid path: the write must fail
    try { $null = Invoke-Bounded 'docker' @('stop', 'w') 3 } catch { }
    if (-not $BoundedGuardMissing) { $fail += 'marker-write failure with no guard: BoundedGuardMissing not set' }
    $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    $GpuMarker = Join-Path $shim 'present-guard.txt'; Set-Content $GpuMarker 'job guard'; (Get-Item $GpuMarker).IsReadOnly = $true
    try { $null = Invoke-Bounded 'docker' @('stop', 'v') 3 } catch { }
    if ($BoundedGuardMissing) { $fail += 'failed append with the guard present: wrongly reported the guard missing' }
    (Get-Item $GpuMarker).IsReadOnly = $false; Remove-Item $GpuMarker
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    . (Join-Path $PSScriptRoot 'invoke-bounded.ps1')   # restore the real functions
    # An orphaned grandchild (its parent already exited, so no process snapshot can find it from the root) is still
    # killed on timeout: the job object reaches every process the command started.
    Set-Content (Join-Path $shim 'orphan.cmd') "@cmd /c start `"`" /b ping -n 600 127.0.0.2 >nul`r`n@ping -n 600 127.0.0.1 >nul"
    # A private marker, and a caught throw: if the kill ever misses the orphan, the test must fail, not crash or
    # poison the real GPU marker.
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'orphan-marker.txt'
    $r = @('__EXIT__threw'); try { $r = @(Invoke-Bounded 'orphan' @() 4) } catch { }
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    Start-Sleep 1
    $orphanLeft = @(Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object CommandLine -match '-n 600 127\.0\.0\.2')
    if ($r[-1] -ne '__EXIT__timeout') { $fail += "orphaned grandchild: expected __EXIT__timeout, got '$($r[-1])'" }
    if ($orphanLeft.Count) { $fail += 'orphaned grandchild: still running after the timeout'; $orphanLeft | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
    # Root exits normally; its child retains stdout/stderr and remains subject to the deadline.
    $holderPidFile = Join-Path $shim 'pipe-holder.pid'
    $rootDone = Join-Path $shim 'pipe-root-done.txt'
    $holderScript = Join-Path $shim 'pipe-holder.ps1'
    Set-Content $holderScript ('$PID | Set-Content -LiteralPath ''' + $holderPidFile + '''; Start-Sleep 600')
    Set-Content (Join-Path $shim 'pipe-root.cmd') ("@start `"`" /b pwsh -NoProfile -File `"$holderScript`"`r`n@echo exited>`"$rootDone`"`r`n@exit /b 0")
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'pipe-holder-marker.txt'
    try {
        $r = @('__EXIT__threw'); try { $r = @(Invoke-Bounded 'pipe-root' @() 8) } catch { }
        if (-not (Test-Path $rootDone) -or -not (Test-Path $holderPidFile)) { $fail += 'pipe holder: root/child fixture did not start' }
        if ($r[-1] -ne '__EXIT__timeout') { $fail += "pipe holder: expected timeout after root exit, got '$($r[-1])'" }
        if (Test-Path $holderPidFile) {
            $holderId = [int](Get-Content $holderPidFile)
            $holderProcess = Get-Process -Id $holderId -ErrorAction SilentlyContinue
            if ($holderProcess -and -not $holderProcess.WaitForExit(2000)) {
                $holderIdentity = Get-CimInstance Win32_Process -Filter "ProcessId=$holderId" -ErrorAction SilentlyContinue
                $fail += "pipe holder: PID $holderId remained active after job termination; command line: $($holderIdentity.CommandLine)"
                if ($holderIdentity.CommandLine -and $holderIdentity.CommandLine.IndexOf($holderScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    $holderProcess | Stop-Process -Force
                }
            }
        }
        if ((Test-Path $GpuMarker) -or $BoundedUnconfirmed -or $BoundedGuardMissing) { $fail += 'pipe holder: confirmed kill must not poison the GPU guard' }
    } finally {
        $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    }
    # A failed join leaves the suspended root outside the job: it must still be killed and confirmed gone, and the
    # call must throw (no command ran, so no marker is needed).
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'join-marker.txt'
    function Add-BoundedJobMember([IntPtr]$Job, [IntPtr]$Process) { throw 'simulated join failure' }
    $threw = $false
    try { $null = Invoke-Bounded 'docker' @('stop', 'k') 3 } catch { $threw = $true }
    Start-Sleep 1
    if (-not $threw) { $fail += 'join failure: Invoke-Bounded returned instead of throwing' }
    if ((Get-ShimPing).Count) { $fail += 'join failure: the command ran outside its job' }
    if (Test-Path -LiteralPath $GpuMarker) { $fail += 'join failure: a marker was written although the root was confirmed gone' }
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    . (Join-Path $PSScriptRoot 'invoke-bounded.ps1')   # restore the real functions
    # No job, no launch: a job object that cannot be created throws before anything runs, so there is nothing to
    # kill and nothing to mark.
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'job-marker.txt'
    function New-BoundedJob { throw 'simulated job object failure' }
    $threw = $false
    try { $null = Invoke-Bounded 'docker' @('stop', 'j') 3 } catch { $threw = $true }
    Start-Sleep 1
    if (-not $threw) { $fail += 'job object failure: Invoke-Bounded ran the command without a job' }
    if ((Get-ShimPing).Count) { $fail += 'job object failure: the command was launched anyway' }
    if (Test-Path -LiteralPath $GpuMarker) { $fail += 'job object failure: a marker was written although nothing ran' }
    $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    . (Join-Path $PSScriptRoot 'invoke-bounded.ps1')   # restore the real functions
    # Output shape: one line, several lines, and none must all end in a separate exit marker.
    $one = @(Invoke-Bounded 'cmd' @('/c', 'echo one-line') 10)
    if ($one.Count -ne 2 -or $one[0] -ne 'one-line' -or $one[1] -ne '__EXIT__0') { $fail += "one-line output: got $($one | ConvertTo-Json -Compress)" }
    $two = @(Invoke-Bounded 'cmd' @('/c', 'echo a& echo b') 10)
    if (($two -join '|') -ne 'a|b|__EXIT__0') { $fail += "two-line output: got $($two -join '|')" }
    $none = @(Invoke-Bounded 'cmd' @('/c', 'exit 3') 10)
    Set-Content (Join-Path $shim 'argv.ps1') '$args | ForEach-Object { "<$_>" }'
    $argv = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', (Join-Path $shim 'argv.ps1'), 'a b', 'q"t', 'tail\', '') 60)
    if (($argv -join '|') -ne '<a b>|<q"t>|<tail\>|<>|__EXIT__0') { $fail += "argument quoting: got $($argv -join '|')" }
    if (($none -join '|') -ne '__EXIT__3') { $fail += "no output, exit 3: got $($none -join '|')" }
    # U+2713 is outside CP437: replacement with '?' must never pass as a round-trip.
    $realMarker = $GpuMarker; $GpuMarker = Join-Path $shim 'unicode-marker.txt'
    $oldEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $unicode = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-Command', '[Console]::OutputEncoding.WebName; [Console]::WriteLine([char]0x2713)') 60)
        if (($unicode -join '|') -cne "utf-8|$([char]0x2713)|__EXIT__0") { $fail += "UTF-8 output: got $($unicode -join '|')" }
    } catch { $fail += "UTF-8 output: $_" }
    finally {
        [Console]::OutputEncoding = $oldEncoding
        $GpuMarker = $realMarker; $BoundedUnconfirmed = $false; $BoundedGuardMissing = $false
    }
    # Start a real windowless console, then detach in a separate run to exercise no console at all.
    $consoleTest = Join-Path $shim 'console-test.ps1'
    Set-Content $consoleTest @'
param([string]$BoundedScript, [string]$Marker, [switch]$Detach)
$ErrorActionPreference = 'Stop'
. $BoundedScript
$GpuMarker = $Marker
if ([HomemadeLlm.JobTree]::GetConsoleWindow() -ne [IntPtr]::Zero) { throw 'fixture: expected no console window' }
if ([HomemadeLlm.JobTree]::GetConsoleCP() -eq 0 -and [HomemadeLlm.JobTree]::GetConsoleOutputCP() -eq 0) { throw 'fixture: expected a windowless console' }
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ($Detach) {
    Add-Type -Name ConsoleFixture -Namespace Tests -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern bool FreeConsole();'
    if (-not [Tests.ConsoleFixture]::FreeConsole()) { throw 'fixture: FreeConsole failed' }
    if ([HomemadeLlm.JobTree]::GetConsoleCP() -ne 0 -or [HomemadeLlm.JobTree]::GetConsoleOutputCP() -ne 0) { throw 'fixture: console is still attached' }
    $ran = "$Marker.child-ran"
    $command = "Set-Content -LiteralPath '$($ran.Replace("'", "''"))' -Value ran; [Console]::WriteLine([char]0x2713)"
    $refused = $false
    try { $null = Invoke-Bounded 'pwsh' @('-NoProfile', '-Command', $command) 60 }
    catch {
        if ($_.Exception.Message -notlike '*Invoke-Bounded requires an attached console*') { throw }
        $refused = $true
    }
    if (-not $refused -or (Test-Path -LiteralPath $ran)) { throw 'console-less launch was not refused before child execution' }
} else {
    $unicode = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-Command', '[Console]::OutputEncoding.WebName; [Console]::WriteLine([char]0x2713)') 60)
    if (($unicode -join '|') -cne "utf-8|$([char]0x2713)|__EXIT__0") { throw "windowless UTF-8 output: got $($unicode -join '|')" }
}
if ((Test-Path -LiteralPath $GpuMarker) -or $BoundedUnconfirmed -or $BoundedGuardMissing) { throw 'console test poisoned the GPU guard' }
Write-Output 'console-test-ok'
'@
    foreach ($detach in $false, $true) {
        $psi = [Diagnostics.ProcessStartInfo]::new([Environment]::ProcessPath)
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        foreach ($arg in @('-NoProfile', '-File', $consoleTest, '-BoundedScript', (Join-Path $PSScriptRoot 'invoke-bounded.ps1'), '-Marker', (Join-Path $shim "console-$detach-marker.txt"))) { $psi.ArgumentList.Add($arg) }
        if ($detach) { $psi.ArgumentList.Add('-Detach') }
        $proc = [Diagnostics.Process]::Start($psi)
        try {
            $stdout = $proc.StandardOutput.ReadToEndAsync(); $stderr = $proc.StandardError.ReadToEndAsync()
            if (-not $proc.WaitForExit(90000)) { throw 'nested pwsh exceeded 90 seconds' }
            if (-not [Threading.Tasks.Task]::WaitAll(@($stdout, $stderr), 5000)) { throw 'nested pwsh output did not close' }
            if ($proc.ExitCode -ne 0 -or $stdout.Result.Trim() -ne 'console-test-ok') { throw "exit $($proc.ExitCode): $($stdout.Result) $($stderr.Result)" }
        } catch { $fail += "no-window console test (detach=$detach): $_" }
        finally {
            if (-not $proc.HasExited) { $proc.Kill($true); $null = $proc.WaitForExit(5000) }
            $proc.Dispose()
        }
    }
} finally {
    $env:PATH = $oldPath
    Get-ShimPing | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }   # only if a check failed
    [IO.Directory]::Delete($shim, $true)
}
# P7 trust: the smoke-only root reader, the pin equality, the mount vector and the trust-outside-writable rule, loaded
# from train-smoke.ps1's syntax tree and run against a synthetic pin/root/digest list (never the production files).
. (Join-Path $PSScriptRoot 'train-adapter-stage.ps1')
foreach ($name in 'Read-SmokeTrust', 'Assert-SmokePins', 'Assert-TrustOutsideWritable', 'New-SmokeMounts', 'New-SmokeDockerArgs', 'Assert-NoLinkInMountSources') {
    $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true) | Select-Object -First 1
    if (-not $fn) { $fail += "train-smoke.ps1: function $name missing" } else { . ([scriptblock]::Create($fn.Extent.Text)) }
}
$keysAst = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$SmokeSectionKeys' }, $true) | Select-Object -First 1
. ([scriptblock]::Create($keysAst.Extent.Text))
$trustDir = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-trust-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory (Join-Path $trustDir 'custody'), (Join-Path $trustDir 'list'), (Join-Path $trustDir 'pin') | Out-Null
try {
    $tsrc = 'org/base-4bit@' + ('a' * 40); $msrc = 'org/base@' + ('b' * 40); $img = 'unsloth/unsloth@sha256:' + ('c' * 64)
    $listPath = Join-Path $trustDir 'list\digests.json'
    function Set-World([scriptblock]$EditSection = $null, [scriptblock]$EditList = $null) {
        $list = [ordered]@{ $tsrc = @{ files = @(); smoke_only = $true; source = 't' }; $msrc = @{ files = @(); smoke_only = $true; source = 't' } }
        if ($EditList) { & $EditList $list }
        [IO.File]::WriteAllText($listPath, ($list | ConvertTo-Json -Depth 5 -Compress))
        $listSha = (Get-FileHash -LiteralPath $listPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $s = [ordered]@{ chat_template_sha256 = ('d' * 64); expected_digest_list = [ordered]@{ path = $listPath; sha256 = $listSha }; image = $img
            list_builder = @{ path = 'x'; sha256 = ('e' * 64) }; merge_source = $msrc; ruling = 'r'; scope = 'smoke'; training_source = $tsrc
            two_read_inventory = @{ path = 'y'; sha256 = ('f' * 64) }; work_order = @{ path = 'z'; sha256 = ('1' * 64) } }
        if ($EditSection) { & $EditSection $s }
        $rootText = ([ordered]@{ format = 'route-r-trust-root/vtest'; smoke_snapshots = $s } | ConvertTo-Json -Depth 6 -Compress)
        if ($null -eq $s) { $rootText = '{"format":"route-r-trust-root/vtest"}' }
        [IO.File]::WriteAllText((Join-Path $trustDir 'custody\route-r-trust-root-vtest.json'), $rootText)
        $rootSha = (Get-FileHash -LiteralPath (Join-Path $trustDir 'custody\route-r-trust-root-vtest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText((Join-Path $trustDir 'pin\route_r_root_pin.py'), "ROOT_FILE = `"route-r-trust-root-vtest.json`"`nROOT_FORMAT = `"route-r-trust-root/vtest`"`nAPPROVED_TRUST_ROOT_SHA256 = `"$rootSha`"`n")
    }
    $loc = @{ PinFile = (Join-Path $trustDir 'pin\route_r_root_pin.py'); CustodyRoot = (Join-Path $trustDir 'custody') }
    function Expect-Refusal([string]$Case, [string]$Pattern, [scriptblock]$Call) {
        $msg = $null; try { & $Call | Out-Null } catch { $msg = $_.Exception.Message }
        if (-not $msg) { $script:fail += "trust: $Case was not refused" } elseif ($msg -notmatch $Pattern) { $script:fail += "trust: $Case refused for the wrong reason: $msg" }
    }
    Set-World
    $tr = Read-SmokeTrust $loc
    if ($tr.Training -cne $tsrc -or $tr.Merge -cne $msrc -or $tr.Image -cne $img -or $tr.ListPath -ne [IO.Path]::GetFullPath($listPath)) { $fail += 'trust: a valid synthetic root was not read back exactly' }
    $base, $rev = $tsrc -split '@'; $ms, $mr = $msrc -split '@'
    Assert-SmokePins -Trust $tr -Base $base -Revision $rev -MergeSource $ms -MergeRevision $mr -Image $img
    Expect-Refusal 'training override' 'does not equal the root-pinned' { Assert-SmokePins -Trust $tr -Base 'org/other-4bit' -Revision $rev -MergeSource $ms -MergeRevision $mr -Image $img }
    Expect-Refusal 'merge-revision override' 'does not equal the root-pinned' { Assert-SmokePins -Trust $tr -Base $base -Revision $rev -MergeSource $ms -MergeRevision ('9' * 40) -Image $img }
    Expect-Refusal 'wrong image' 'image .* does not equal the root-pinned' { Assert-SmokePins -Trust $tr -Base $base -Revision $rev -MergeSource $ms -MergeRevision $mr -Image 'unsloth/unsloth:latest' }
    Set-World -EditSection { param($s) $null = $s }   # control: unchanged section still reads
    Set-World -EditSection { param($s) $s.Remove('ruling') }
    Expect-Refusal 'missing smoke_snapshots key' 'smoke_snapshots section missing or not the exact installed shape' { Read-SmokeTrust $loc }
    Set-World -EditSection { param($s) $s['extra'] = 1 }
    Expect-Refusal 'extra smoke_snapshots key' 'smoke_snapshots section missing or not the exact installed shape' { Read-SmokeTrust $loc }
    Set-World
    [IO.File]::WriteAllText((Join-Path $trustDir 'custody\route-r-trust-root-vtest.json'), '{"format":"route-r-trust-root/vtest"}')
    $rs = (Get-FileHash -LiteralPath (Join-Path $trustDir 'custody\route-r-trust-root-vtest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($loc.PinFile, "ROOT_FILE = `"route-r-trust-root-vtest.json`"`nROOT_FORMAT = `"route-r-trust-root/vtest`"`nAPPROVED_TRUST_ROOT_SHA256 = `"$rs`"`n")
    Expect-Refusal 'root without smoke_snapshots' 'smoke_snapshots section missing' { Read-SmokeTrust $loc }
    Set-World
    [IO.File]::AppendAllText($listPath, ' ')   # one changed byte in the digest list
    Expect-Refusal 'digest list bytes changed' 'does not match its root-pinned SHA-256' { Read-SmokeTrust $loc }
    Set-World -EditList { param($l) $l['x/y@' + ('2' * 40)] = @{ files = @() } }
    Expect-Refusal 'digest list names a third snapshot' 'exactly the two root-pinned snapshots' { Read-SmokeTrust $loc }
    Set-World
    [IO.File]::AppendAllText((Join-Path $trustDir 'custody\route-r-trust-root-vtest.json'), ' ')
    Expect-Refusal 'root bytes differ from the pin' 'does not match the pinned SHA-256' { Read-SmokeTrust $loc }
    Set-World -EditSection { param($s) $s['image'] = 'unsloth/unsloth:latest' }
    Expect-Refusal 'unpinned image in the root' 'digest-pinned image' { Read-SmokeTrust $loc }
    # Mount vector: read-only cache and digest list; only run-specific writable directories; trust inputs outside them.
    $m = New-SmokeMounts -HotRoot 'C:\llm' -RunId 'smoke-20261006-050000-abcdef' -ListPath 'C:\llm\runs\m2\p7-smoke\smoke-expected-digests.draft.json'
    $v = @(for ($i = 1; $i -lt $m.Mounts.Count; $i += 2) { $m.Mounts[$i] })
    if (-not ($v -contains 'C:\llm\hf:/workspace/hf:ro')) { $fail += 'mounts: the HF cache must be read-only' }
    if (-not ($v -contains 'C:\llm\runs\m2\p7-smoke\smoke-expected-digests.draft.json:/workspace/smoke-digests.json:ro')) { $fail += 'mounts: the digest list must be read-only' }
    if (@($v | Where-Object { $_ -match '^C:\\llm\\(runs|reports):' }).Count) { $fail += 'mounts: the runs or reports parent is mounted (writable)' }
    if ((@($m.Writable) -join '|') -ne 'C:\llm\runs\smoke\smoke-20261006-050000-abcdef|C:\llm\models\smoke|C:\llm\reports\smoke') { $fail += "mounts: unexpected writable set $(@($m.Writable) -join '|')" }
    Assert-TrustOutsideWritable -WritableDirs $m.Writable -TrustPaths @('C:\llm\runs\m2\rr\route_r_root_pin.py', 'C:\llm\reports\route-r\route-r-trust-root-v2r6.json', 'C:\llm\runs\m2\p7-smoke\smoke-expected-digests.draft.json')
    Expect-Refusal 'trust input under a writable mount' 'sits inside the writable container mount' { Assert-TrustOutsideWritable -WritableDirs @('C:\llm\reports') -TrustPaths @('C:\llm\reports\route-r\route-r-trust-root-v2r6.json') }
    Expect-Refusal 'malformed run id' 'unexpected run id' { New-SmokeMounts -HotRoot 'C:\llm' -RunId '..\x' -ListPath $listPath }
    # The actual container argv: no network, exactly these mounts, code read-only, offline flags, the trust digests, the pinned image.
    $trustArg = @{ ListSha256 = ('5' * 64); TemplateSha256 = ('6' * 64) }
    $d = @(New-SmokeDockerArgs -Name 'hl-smoke-test0001' -Mounts $m.Mounts -Code 'C:\code\train' -RunId 'smoke-20261006-050000-abcdef' `
        -Base 'org/base-4bit' -Revision ('a' * 40) -MergeSource 'org/base' -MergeRevision ('b' * 40) -Trust $trustArg -Image $img)
    $iNet = [array]::IndexOf($d, '--network')
    if ($iNet -lt 0 -or $d[$iNet + 1] -cne 'none') { $fail += 'docker argv: --network none missing' }
    if (@($d | Where-Object { $_ -ceq '--network' }).Count -ne 1) { $fail += 'docker argv: more than one --network' }
    $vols = @(for ($i = 0; $i -lt $d.Count - 1; $i++) { if ($d[$i] -ceq '-v') { $d[$i + 1] } })
    if ((($vols | Select-Object -SkipLast 1) -join '|') -ne ((@($v)) -join '|') -or $vols[-1] -cne 'C:\code\train:/code:ro') { $fail += "docker argv: unexpected volumes $($vols -join '|')" }
    $envs = @(for ($i = 0; $i -lt $d.Count - 1; $i++) { if ($d[$i] -ceq '-e') { $d[$i + 1] } })
    foreach ($e in 'HF_HUB_OFFLINE=1', 'TRANSFORMERS_OFFLINE=1', ('SMOKE_DIGESTS_SHA256=' + ('5' * 64)), ('SMOKE_TEMPLATE_SHA256=' + ('6' * 64)), 'SMOKE_RUN=smoke-20261006-050000-abcdef') {
        if ($envs -notcontains $e) { $fail += "docker argv: missing -e $e" }
    }
    if ($d[-2] -cne $img -or $d[-1] -cne '/code/smoke_train.py' -or $d[-4] -cne '--entrypoint') { $fail += 'docker argv: the pinned image and entrypoint must close the vector' }
} finally { [IO.Directory]::Delete($trustDir, $true) }

# Link guard scope (#299 6011426186): the production guard over the production mount vector of a synthetic C: root (real
# junctions; no admin rights needed). An unrelated parent-tree junction (a retained review fixture under reports\, another
# run's entry under runs\smoke\, or anything under runs\m2) is allowed; a native link inside each actual mounted source,
# and an ancestor redirect of a mounted source, are refused.
$gRoot = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-guard-" + [guid]::NewGuid().ToString('N'))
$gLinks = @()
try {
    $gRun = 'smoke-20261006-070000-abcdef'
    $outside = Join-Path $gRoot 'outside'
    foreach ($dd in 'outside\smoke', 'hf\hub', 'models\smoke', 'reports\smoke', "runs\smoke\$gRun", 'reports\old-review\fixture', 'runs\m2') {
        New-Item -ItemType Directory -Force (Join-Path $gRoot $dd) | Out-Null
    }
    function Get-GuardRefusal([string]$Root) {
        $gm = New-SmokeMounts -HotRoot $Root -RunId $gRun -ListPath (Join-Path $gRoot 'outside\list.json')
        try { Assert-NoLinkInMountSources -Dirs (@($gm.ReadOnlyDirs) + @($gm.Writable)); '' } catch { $_.Exception.Message }
    }
    $gm = New-SmokeMounts -HotRoot $gRoot -RunId $gRun -ListPath (Join-Path $gRoot 'outside\list.json')
    $want = @('hf', "runs\smoke\$gRun", 'models\smoke', 'reports\smoke' | ForEach-Object { Join-Path $gRoot $_ })
    if (((@($gm.ReadOnlyDirs) + @($gm.Writable)) -join '|') -ne ($want -join '|')) { $fail += "link guard: walked sources are not exactly the mount vector's directories: $((@($gm.ReadOnlyDirs) + @($gm.Writable)) -join '|')" }
    if ($gm.Mounts[1] -cne "$($gm.ReadOnlyDirs[0]):/workspace/hf:ro") { $fail += 'link guard: the read-only HF source is not the mounted HF directory' }
    $r = Get-GuardRefusal $gRoot
    if ($r) { $fail += "link guard: a clean root was refused: $r" }
    $gLinks += New-Item -ItemType Junction (Join-Path $gRoot 'reports\old-review\fixture\parent-junction') -Target $outside
    $gLinks += New-Item -ItemType Junction (Join-Path $gRoot 'runs\smoke\other-run-junction') -Target $outside
    $gLinks += New-Item -ItemType Junction (Join-Path $gRoot 'runs\m2\alias') -Target $outside
    $r = Get-GuardRefusal $gRoot
    if ($r) { $fail += "link guard: an unrelated parent-tree junction was refused: $r" }
    foreach ($src in 'hf\hub', 'models\smoke', 'reports\smoke', "runs\smoke\$gRun") {
        $l = New-Item -ItemType Junction (Join-Path $gRoot "$src\inner-link") -Target $outside
        $r = Get-GuardRefusal $gRoot
        $l.Delete()
        if ($r -notmatch 'is a Junction inside a mounted folder') { $fail += "link guard: a junction inside the mounted $src was not refused ($r)" }
    }
    # Ancestor redirect: 'models' itself is a junction, so the mounted models\smoke resolves somewhere else.
    $aRoot = Join-Path $gRoot 'aroot'
    New-Item -ItemType Directory (Join-Path $aRoot 'hf'), (Join-Path $aRoot 'reports\smoke') | Out-Null
    $gLinks += New-Item -ItemType Junction (Join-Path $aRoot 'models') -Target $outside
    $r = Get-GuardRefusal $aRoot
    if ($r -notmatch 'models is a junction or symbolic link; HotRoot and its mounted folders must be real folders') { $fail += "link guard: an ancestor redirect of models\smoke was not refused ($r)" }
} finally {
    foreach ($l in $gLinks) { if ($l) { $l.Delete() } }   # remove the links themselves before the recursive delete
    [IO.Directory]::Delete($gRoot, $true)
}

# Pre-preflight refusals (run the real script; all stop before preflight or Docker).
$tmpRoot = Join-Path ([IO.Path]::GetTempPath()) ("train-smoke-root-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory (Join-Path $tmpRoot 'hf') | Out-Null
$links = @()
$failShim = Join-Path $tmpRoot 'shim'
New-Item -ItemType Directory $failShim | Out-Null
Set-Content (Join-Path $failShim 'docker.cmd') '@exit /b 97'
Set-Content (Join-Path $failShim 'lms.cmd') '@exit /b 97'
try {
    Set-Content (Join-Path $tmpRoot 'hf\token') 'hf_dummy'
    # Error text arrives colored and wrapped to the console width: strip ANSI codes and collapse whitespace.
    # The script now refuses any image other than the root-pinned one before the GPU mutex, so these runs pass the
    # pinned image and the fail-safe is a PATH whose docker and lms always fail (no container, no LM Studio unload),
    # plus a 3-second timeout. The trust checks read this host's installed root (a run on a host without it refuses
    # earlier, at the trust step).
    $pinnedImage = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739'
    function Run([string]$Root, [string[]]$Extra = @()) {
        $old = $env:PATH; $env:PATH = "$failShim;$old"
        try { ((& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'train-smoke.ps1') -HotRoot $Root -Image $pinnedImage -TimeoutMinutes 0.05 @Extra 2>&1 | Out-String) -replace '\x1b\[[0-9;]*m', '' -replace '[\s|]+', ' ') }
        finally { $env:PATH = $old }
    }
    $o = Run $tmpRoot
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'stored Hugging Face credential') { $fail += "stored token: not refused (exit $LASTEXITCODE)" }
    $o = Run 'D:\llm'
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'HotRoot must be on C:') { $fail += "non-C HotRoot: not refused (exit $LASTEXITCODE)" }
    # Links (junctions need no admin rights): on the root itself, and on a mounted subfolder of a real root.
    $real = Join-Path $tmpRoot 'real'; $clean = Join-Path $tmpRoot 'clean'
    New-Item -ItemType Directory $real, $clean | Out-Null
    $links = @((New-Item -ItemType Junction (Join-Path $tmpRoot 'jroot') -Target $real), (New-Item -ItemType Junction (Join-Path $clean 'reports') -Target $real))
    $o = Run (Join-Path $tmpRoot 'jroot')
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'junction or symbolic link') { $fail += "junction HotRoot: not refused (exit $LASTEXITCODE)" }
    $o = Run $clean
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'junction or symbolic link') { $fail += "junction reports folder: not refused (exit $LASTEXITCODE)" }
    $nested = Join-Path $tmpRoot 'nested'; New-Item -ItemType Directory (Join-Path $nested 'hf') | Out-Null
    $links += New-Item -ItemType Junction (Join-Path $nested 'hf\hub') -Target $real
    $o = Run $nested
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'Junction inside a mounted folder') { $fail += "junction nested in hf: not refused (exit $LASTEXITCODE)" }
    # A base override needs its own merge-source pin, and every revision must be a full commit.
    $plain = Join-Path $tmpRoot 'plain'; New-Item -ItemType Directory $plain | Out-Null
    $o = Run $plain @('-Base', 'org/other-4bit', '-Revision', ('a' * 40))
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'needs a matching -MergeSource') { $fail += "base override without merge pin: not refused (exit $LASTEXITCODE)" }
    $o = Run $plain @('-Revision', ('b' * 40))
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'needs a matching -MergeSource') { $fail += "revision override without merge pin: not refused (exit $LASTEXITCODE)" }
    $o = Run $plain @('-MergeRevision', 'main')
    if ($LASTEXITCODE -eq 0 -or $o -notmatch 'not a full 40-hex commit') { $fail += "moving merge revision: not refused (exit $LASTEXITCODE)" }
    # The shared GPU lock: while another process holds it, training must not start.
    $holder = Start-Process pwsh -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command',
        '$m = [Threading.Mutex]::new($false, ''Global\homemade-llm-gpu''); $null = $m.WaitOne(0); Start-Sleep 60'
    try {
        Start-Sleep 3
        $o = Run $plain
        if ($LASTEXITCODE -eq 0 -or $o -notmatch 'another GPU job') { $fail += "GPU lock held elsewhere: not refused (exit $LASTEXITCODE)" }
    } finally { Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue }
    # The machine-wide marker (not a per-root file, not a re-entrant mutex) blocks training from any -HotRoot.
    # It uses the real path, so it is planted while this test holds the GPU mutex (no live run can be past its marker
    # check, and any later run refuses on it), and removed only if it still holds the test's own text.
    $testMark = 'container hl-smoke-test not confirmed stopped (test)'
    $tm = [Threading.Mutex]::new($false, 'Global\homemade-llm-gpu')
    $own = try { $tm.WaitOne(0) } catch [Threading.AbandonedMutexException] { $true }
    if (-not $own) { $fail += 'poison marker: a GPU job is running; refusal not tested' }
    elseif (Test-Path -LiteralPath $GpuMarker) { $tm.ReleaseMutex(); $fail += "poison marker: a real marker exists at $GpuMarker; check it before deleting it" }
    else {
        New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
        Set-Content -LiteralPath $GpuMarker $testMark
        $tm.ReleaseMutex()   # the child run must be able to take the lock and reach the marker check
        try {
            $o = Run $plain
            if ($LASTEXITCODE -eq 0 -or $o -notmatch 'GPU is poisoned') { $fail += "poison marker: training not refused (exit $LASTEXITCODE)" }
        } finally { if ((Get-Content -LiteralPath $GpuMarker -Raw -ErrorAction SilentlyContinue).Trim() -eq $testMark) { Remove-Item -LiteralPath $GpuMarker -Force } }
    }
    $tm.Dispose()
} finally {
    foreach ($l in $links) { if ($l) { $l.Delete() } }   # remove the links themselves before the recursive delete
    Get-ChildItem -LiteralPath $tmpRoot -Recurse -Force -File | ForEach-Object { $_.Attributes = 'Normal' }   # HF marks some files read-only
    [IO.Directory]::Delete($tmpRoot, $true)
}

# Both scripts release the GPU lock in a finally block, so a run inside a long-lived session cannot keep it.
foreach ($f in 'train-smoke.ps1', 'serve-smoke.ps1') {
    $a = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $f), [ref]$null, [ref]$null)
    $release = @($a.FindAll({ param($n) $n -is [System.Management.Automation.Language.TryStatementAst] -and $n.Finally -and $n.Finally.Extent.Text -match 'ReleaseMutex\(\)' }, $true))
    if ($release.Count -ne 1) { $fail += "$f`: expected one finally that releases the GPU mutex, found $($release.Count)" }
}

# Grok post-merge findings on #275 (structural: an unkillable process or unremovable container cannot be simulated here).
$ib = Get-Content -Raw (Join-Path $PSScriptRoot 'invoke-bounded.ps1')
if ($ib -notmatch '(?s)finally \{.*if \(-not \$completed -and \$h -ne \[IntPtr\]::Zero\) \{.*Terminate\(\$job\).*if \(\(Get-BoundedJobActive \$job\) -gt 0\) \{ \$unverified = \$true \}.*if \(-not \$gone -or \$unverified\) \{.*Add-Content -LiteralPath \$GpuMarker.*throw \$msg') { $fail += 'invoke-bounded: the whole tree must be verified gone through the job, or it throws' }
$ts = Get-Content -Raw (Join-Path $PSScriptRoot 'train-smoke.ps1')
foreach ($g in 'train-smoke.ps1', 'serve-smoke.ps1') {
    $gs = Get-Content -Raw (Join-Path $PSScriptRoot $g)
    if (-not $gs.Contains('if ($BoundedGuardMissing)')) { $fail += "$g`: must keep the lock when Invoke-Bounded could not write its guard" }
    if ($gs -match 'state.gpu-poisoned' -or -not $gs.Contains('$poison = $GpuMarker')) { $fail += "$g`: must use the one machine-wide marker, not a per-HotRoot file" }
}
$iMark = $ts.IndexOf('launch in flight since'); $iStart = $ts.IndexOf('$job = Start-Job')
if (-not ($iMark -ge 0 -and $iStart -gt $iMark)) { $fail += 'train-smoke: the in-flight marker must be written before Start-Job' }
if ($ts -notmatch '(?s)\} elseif \(-not \$BoundedUnconfirmed\) \{\s*# No container was left unconfirmed.*Remove-Item -LiteralPath \$GpuMarker') { $fail += 'train-smoke: the marker may be cleared only when the container is confirmed gone' }
if ($ib -notmatch '(?s)if \(GetConsoleCP\(\) == 0 && GetConsoleOutputCP\(\) == 0\) throw new InvalidOperationException\([^\r\n]+;.*if \(!CreateProcessW\([^\r\n]+true, CREATE_SUSPENDED \| EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, null, ref si, out pi\).*\$h = \[HomemadeLlm\.JobTree\]::CreateSuspended\(.*Add-BoundedJobMember \$job \$h; \[HomemadeLlm\.JobTree\]::Resume\(\$t\)') { $fail += 'invoke-bounded: refuse a console-less launch; otherwise inherit the console, create suspended and join the job before resuming' }
if ($ib -notmatch '(?s)Marshal\.WriteInt32\(policy, PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_DISABLE_PROCESS_TREE\);.*if \(!UpdateProcThreadAttribute\(si\.lpAttributeList, 0, new IntPtr\(PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY\), policy, new IntPtr\(sizeof\(int\)\), IntPtr.Zero, IntPtr.Zero\)\) throw new Win32Exception\(\);\s*if \(!CreateProcessW\([^\r\n]+CREATE_SUSPENDED \| EXTENDED_STARTUPINFO_PRESENT') { $fail += 'invoke-bounded: apply desktop-app breakaway-disable policy to contain packaged descendants' }
if ($ib -notmatch '(?s)if \(-not \$completed -and \$h -ne \[IntPtr\]::Zero\) \{.*\[HomemadeLlm\.JobTree\]::Kill\(\$h\).*\$gone = ') { $fail += 'invoke-bounded: the root must be killed directly too (it may be outside the job) before the wait confirms it' }
if (-not $ib.Contains('(pid $rootPid)')) { $fail += 'invoke-bounded: the marker must record the root pid' }
if ($ts -notmatch '(?s)\$keepLock = \$true.*if \(-not \$keepLock\) \{ \$gpuLock\.ReleaseMutex') { $fail += 'train-smoke: a missing marker for an unconfirmed container must keep the lock' }
foreach ($g in 'train-smoke.ps1', 'serve-smoke.ps1') { if (-not (Get-Content -Raw (Join-Path $PSScriptRoot $g)).Contains('GPU is poisoned')) { $fail += "$g`: must refuse to start while the GPU is poisoned" } }
# The normal path confirms absence with a daemon check. The only direct true is a synchronous Start-Job failure,
# and the unsafe flag is set before the launch attempt (a cancel mid-Start-Job must not release the GPU).
if ($ts.Contains('$containerGone = $true')) { $fail += 'train-smoke: containerGone may never be set true without a daemon check' }
if ($ts -match 'catch \{[^}]*containerGone') { $fail += 'train-smoke: a Start-Job failure must keep the guard (no catch may clear containerGone)' }
foreach ($g in 'train-smoke.ps1', 'serve-smoke.ps1') { if (-not (Get-Content -Raw (Join-Path $PSScriptRoot $g)).Contains('LM Studio: lms ps')) { $fail += "$g`: the recovery text must cover LM Studio and other commands, not only containers" } }
if ($ib -match 'Get-SurvivingProcess|Stop-Process') { $fail += 'invoke-bounded: survivors are killed and counted by the job object, never per PID' }
if ($ib -match 'Get-CimInstance|Diagnostics\.Process\]::Start') { $fail += 'invoke-bounded: no process snapshots and no unsuspended start; the job is the only record of the tree' }
if ($ib -match 'KILL_ON_JOB_CLOSE|0x2000') { $fail += 'invoke-bounded: the job must not kill on close (a normal exit may leave a process running on purpose)' }
foreach ($pair in @(@('train-smoke.ps1', "Invoke-Bounded 'pwsh'"), @('serve-smoke.ps1', "Invoke-Lms @('server', 'start')"))) {
    $gs = Get-Content -Raw (Join-Path $PSScriptRoot $pair[0]); $iG = $gs.IndexOf('GPU job in progress since'); $iFirst = $gs.IndexOf($pair[1])
    if (-not ($iG -ge 0 -and $iFirst -gt $iG)) { $fail += "$($pair[0])`: the durable job guard must be written before the first bounded command" }
}
$iJob = $ts.IndexOf('$job = Start-Job'); $iFlag = $ts.IndexOf('$containerGone = $false')
if (-not ($iFlag -ge 0 -and $iJob -gt $iFlag)) { $fail += 'train-smoke: containerGone=false must come before Start-Job' }
if (-not $ts.Contains('$containerGone = $state -eq ''stopped and removed''')) { $fail += 'train-smoke: containerGone must come from the Stop-Smoke result' }

if ($fail) { $fail | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'ok  smoke trust: a valid synthetic root reads back exactly; missing/extra smoke_snapshots keys, a root without the section, root bytes off the pin, a changed digest list, a third snapshot and an unpinned image refuse'
Write-Output 'ok  smoke pins: a training, merge-revision or image override refuses'
Write-Output 'ok  smoke mounts: HF cache and digest list read-only; only runs\smoke\<run>, models\smoke and reports\smoke writable; a trust input under a writable mount refuses'
Write-Output 'ok  smoke container argv: --network none, exactly the run mounts plus read-only code, offline flags, trust digests, pinned image and entrypoint'
Write-Output 'ok  stored Hugging Face token in the mounted cache refused'
Write-Output 'ok  HotRoot off drive C refused'
Write-Output 'ok  junction as HotRoot, as a mounted subfolder, and nested inside hf, refused'
Write-Output 'ok  link guard walks only the mount sources: unrelated parent-tree junctions allowed; inside each mounted source and ancestor redirect refused'
Write-Output 'ok  base or revision override without a merge pin, and a non-commit revision, refused'
Write-Output 'ok  both scripts release the GPU mutex in a finally block'
Write-Output 'ok  bounded command tree verified gone through its job object or it throws; poison marker unless the container is confirmed gone'
Write-Output 'ok  GPU lock held by another process refused'
Write-Output 'ok  machine-wide poison marker refuses training (from any -HotRoot)'
Write-Output 'ok  in-flight marker before launch; cleared only when the container is confirmed gone'
Write-Output 'ok  a tree the job still counts after the kill fails closed and persists the machine-wide marker'
Write-Output 'ok  marker-write failure: guard missing keeps the lock; failed append with a guard present is harmless'
Write-Output 'ok  durable job guard written before the first bounded command; a Start-Job failure keeps it'
Write-Output 'ok  suspended start joins the job first; a failed join still kills the root; an orphaned grandchild is killed on timeout; no job means no launch; marker keeps the root pid'
Write-Output 'ok  root-exited pipe holder times out, is killed, and leaves no poison marker'
Write-Output 'ok  Invoke-Bounded kills the child when its pipeline is stopped'
Write-Output 'ok  Invoke-Bounded output shape: one line, two lines, and no output with an exit code; arguments reach argv exactly'
Write-Output 'ok  Invoke-Bounded round-trips U+2713 with inherited and windowless consoles; a truly console-less launch is refused without poisoning the GPU guard'
Write-Output "ok  hung docker CLI: returned after $([math]::Round($t.Elapsed.TotalSeconds, 1)) s with a timeout marker, no child process left"
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:14 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:01 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:52 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:16 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 03:48 UTC | END]]
