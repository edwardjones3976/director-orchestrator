# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | BEGIN]]
# Shared by train-smoke.ps1 and serve-smoke.ps1 (dot-sourced): run a native command with a host-side deadline.
# A stalled Docker or LM Studio daemon must not hang a run that holds the GPU lock, and a timed-out command
# must not keep running after we give up on it, so the whole process tree is killed on timeout.
# Returns the command's stdout lines, then '__EXIT__<code>' (or '__EXIT__timeout') as the last element.
# One machine-wide GPU marker for the one machine-wide Global\homemade-llm-gpu mutex: a fixed path under ProgramData,
# never derived from a caller's -HotRoot, so no choice of root can miss it.
$GpuMarker = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'homemade-llm\gpu-poisoned.txt'

# A Windows job object supervises each command's whole process tree. Every process the command starts joins the
# job, so TerminateJobObject reaches a descendant whose parent already exited (ancestry a process snapshot cannot
# recover), and the job's own active-process count verifies the kill without a query per process.
if (-not ('HomemadeLlm.JobTree' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace HomemadeLlm {
public static class JobTree {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicAccounting { public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime; public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses; }
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryInformationJobObject(IntPtr job, int infoClass, out BasicAccounting info, int length, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    public static IntPtr Create() { IntPtr h = CreateJobObjectW(IntPtr.Zero, null); if (h == IntPtr.Zero) throw new Win32Exception(); return h; }
    public static void Assign(IntPtr job, IntPtr process) { if (!AssignProcessToJobObject(job, process)) throw new Win32Exception(); }
    public static void Terminate(IntPtr job) { if (!TerminateJobObject(job, 1)) throw new Win32Exception(); }
    public static uint Active(IntPtr job) { BasicAccounting i; if (!QueryInformationJobObject(job, 1, out i, Marshal.SizeOf(typeof(BasicAccounting)), IntPtr.Zero)) throw new Win32Exception(); return i.ActiveProcesses; }
    public static void Close(IntPtr job) { CloseHandle(job); }
}}
'@
}
# Separate functions so the tests can simulate a job or snapshot failure.
function New-BoundedJob { [HomemadeLlm.JobTree]::Create() }
# All descendants of a process (one bounded CIM snapshot, walked breadth-first), with their creation times.
function Get-DescendantProcess([int]$Root) {
    $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CreationDate -OperationTimeoutSec 10 -ErrorAction Stop)
    $found = [System.Collections.Generic.List[object]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new(); $queue.Enqueue($Root)
    while ($queue.Count) {
        $parent = $queue.Dequeue()
        foreach ($c in $all) { if ($c.ParentProcessId -eq $parent -and $c.ProcessId -ne $parent -and $c.ProcessId -ne $Root) { $found.Add($c); $queue.Enqueue([int]$c.ProcessId) } }
    }
    $found
}
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $path = (Get-Command $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $psi = [System.Diagnostics.ProcessStartInfo]::new($path)
    foreach ($x in $A) { $psi.ArgumentList.Add([string]$x) }   # quoted per argument, unlike a joined string
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    $p = [System.Diagnostics.Process]::Start($psi)
    $completed = $false
    $job = [IntPtr]::Zero; $supervised = $false
    try {
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $null = $p.StandardError.ReadToEndAsync()   # drained so a chatty stderr cannot block the child
        # Put the command in its job. Anything it started before the assignment is swept in once, bound through a
        # handle and matched by exact start time, so a reused PID is never assigned; later children inherit the job.
        # If supervision cannot be set up, a normal exit is still fine, but any other exit fails closed below. That
        # includes a Ctrl+C during this brief setup: the call is then reported unconfirmed and leaves the marker.
        try {
            $job = New-BoundedJob
            [HomemadeLlm.JobTree]::Assign($job, $p.Handle)
            foreach ($c in @(Get-DescendantProcess $p.Id)) {
                $h = Get-Process -Id $c.ProcessId -ErrorAction SilentlyContinue
                if (-not $h) { continue }
                try {
                    $null = $h.SafeHandle
                    if ([math]::Abs(($h.StartTime - $c.CreationDate).TotalMilliseconds) -lt 1) {
                        try { [HomemadeLlm.JobTree]::Assign($job, $h.Handle) } catch { if (-not $h.HasExited) { throw } }
                    }
                } finally { $h.Dispose() }
            }
            $supervised = $true
        } catch { }
        # Wait in short slices: PowerShell.Stop() and Ctrl+C cannot interrupt one long blocking WaitForExit, but
        # can stop the loop between slices, and finally then kills the tree.
        while (-not $p.WaitForExit(200)) {
            if ([DateTime]::UtcNow -ge $deadline) { return @('__EXIT__timeout') }   # finally kills the tree
        }
        $p.WaitForExit()   # also waits for the redirected streams to finish
        $completed = $true
        $text = $stdout.Result.TrimEnd("`r", "`n")
        # @() matters: a single output line would otherwise be a string, and string + string concatenates.
        @(if ($text) { $text -split "`r?`n" }) + "__EXIT__$($p.ExitCode)"
    } finally {
        # Timeout, Ctrl+C or a stopped pipeline: never leave the command running after we stop waiting for it, and
        # if its tree cannot be confirmed dead, fail loudly: a silent 'timeout' would let callers release the GPU
        # lock while the command (possibly a GPU container operation) is still running.
        $unverified = $false
        if (-not $completed) {
            if ($supervised) {
                try {
                    [HomemadeLlm.JobTree]::Terminate($job)
                    $until = [DateTime]::UtcNow.AddSeconds(10)   # one deadline for the whole tree
                    # Thread.Sleep, not Start-Sleep: in a stopping pipeline (Ctrl+C, PowerShell.Stop()) Start-Sleep throws,
                    # which would misreport a confirmed kill as unconfirmed.
                    while ([HomemadeLlm.JobTree]::Active($job) -gt 0 -and [DateTime]::UtcNow -lt $until) { [Threading.Thread]::Sleep(100) }
                    if ([HomemadeLlm.JobTree]::Active($job) -gt 0) { $unverified = $true }
                } catch { $unverified = $true }
            } else {
                # No job: kill what .NET can still reach, but the tree cannot be verified, so this fails closed.
                try { $p.Kill($true) } catch { }
                $unverified = $true
            }
        }
        $gone = $p.HasExited -or $p.WaitForExit(5000)
        $id = $p.Id
        # No kill-on-close: after a normal exit, a process the command deliberately left running keeps running.
        if ($job -ne [IntPtr]::Zero) { [HomemadeLlm.JobTree]::Close($job) }
        $p.Dispose()
        if (-not $gone -or $unverified) {
            $msg = "Invoke-Bounded: $Exe (pid $id) or a child process could not be confirmed stopped and may still be running"
            # Persist the machine-wide GPU guard before any caller can unwind and release the mutex: every caller is
            # covered, not just the container path. If even that fails, flag it so callers keep the lock instead.
            $script:BoundedUnconfirmed = $true
            try {
                New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
                Add-Content -LiteralPath $GpuMarker -Value "$msg ($((Get-Date).ToUniversalTime().ToString('o')))"
            } catch {
                # A guard that already exists (the scripts write it before any bounded command) still guards; only a
                # missing marker leaves the callers without one.
                if (-not (Test-Path -LiteralPath $GpuMarker)) { $script:BoundedGuardMissing = $true }
            }
            throw $msg
        }
    }
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:40 UTC | END]]
