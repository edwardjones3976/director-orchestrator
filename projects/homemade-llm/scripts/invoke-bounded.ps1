# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:05 UTC | BEGIN]]
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

# Each command runs in its own Windows job object. It is created suspended, put in the job, and only then resumed,
# so it cannot start a single child outside the job; every later descendant inherits the job, even one whose parent
# has exited. TerminateJobObject therefore reaches the whole tree, and the job's own active-process count verifies
# the kill with no process snapshots.
if (-not ('HomemadeLlm.JobTree' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
namespace HomemadeLlm {
public static class JobTree {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicAccounting { public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime; public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo { public int cb; public string lpReserved, lpDesktop, lpTitle; public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags; public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInformation { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
    const uint CREATE_SUSPENDED = 0x00000004, CREATE_NO_WINDOW = 0x08000000;
    const int STARTF_USESTDHANDLES = 0x00000100;
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryInformationJobObject(IntPtr job, int infoClass, out BasicAccounting info, int length, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref StartupInfo si, out ProcessInformation pi);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr process, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)] static extern int GetProcessId(IntPtr process);
    public static IntPtr Create() { IntPtr h = CreateJobObjectW(IntPtr.Zero, null); if (h == IntPtr.Zero) throw new Win32Exception(); return h; }
    public static void Terminate(IntPtr job) { if (!TerminateJobObject(job, 1)) throw new Win32Exception(); }
    public static uint Active(IntPtr job) { BasicAccounting i; if (!QueryInformationJobObject(job, 1, out i, Marshal.SizeOf(typeof(BasicAccounting)), IntPtr.Zero)) throw new Win32Exception(); return i.ActiveProcesses; }
    public static void Close(IntPtr handle) { if (handle != IntPtr.Zero) CloseHandle(handle); }
    public static bool Wait(IntPtr process, uint ms) { return WaitForSingleObject(process, ms) == 0; }
    public static int ExitCode(IntPtr process) { uint c; if (!GetExitCodeProcess(process, out c)) throw new Win32Exception(); return unchecked((int)c); }
    // One argument per argv slot, quoted by the rules CommandLineToArgvW and the C runtime use to split them back.
    public static string Quote(string arg) {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0) return arg;
        var sb = new StringBuilder("\"");
        for (int i = 0; ; i++) {
            int slashes = 0;
            while (i < arg.Length && arg[i] == '\\') { slashes++; i++; }
            if (i == arg.Length) { sb.Append('\\', slashes * 2); break; }
            if (arg[i] == '"') { sb.Append('\\', slashes * 2 + 1).Append('"'); } else { sb.Append('\\', slashes).Append(arg[i]); }
        }
        return sb.Append('"').ToString();
    }
    // Create suspended and hand both handles back at once: the caller owns the process from its first instant, so
    // a failure to join or resume below is cleaned up (and verified) by the caller, never hidden here.
    public static IntPtr CreateSuspended(string commandLine, IntPtr stdIn, IntPtr stdOut, IntPtr stdErr, out IntPtr thread) {
        var si = new StartupInfo(); si.cb = Marshal.SizeOf(typeof(StartupInfo)); si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = stdIn; si.hStdOutput = stdOut; si.hStdError = stdErr;
        ProcessInformation pi;
        if (!CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true, CREATE_SUSPENDED | CREATE_NO_WINDOW, IntPtr.Zero, null, ref si, out pi)) throw new Win32Exception();
        thread = pi.hThread;
        return pi.hProcess;
    }
    public static void Join(IntPtr job, IntPtr process) { if (!AssignProcessToJobObject(job, process)) throw new Win32Exception(); }
    public static void Resume(IntPtr thread) { if (ResumeThread(thread) == 0xFFFFFFFF) throw new Win32Exception(); }
    public static void Kill(IntPtr process) { TerminateProcess(process, 1); }   // verified by the caller's wait
    public static int ProcessId(IntPtr process) { return GetProcessId(process); }
}}
'@
}
# Separate functions so the tests can simulate a job failure or a survivor the job still counts.
function New-BoundedJob { [HomemadeLlm.JobTree]::Create() }
function Get-BoundedJobActive([IntPtr]$Job) { [HomemadeLlm.JobTree]::Active($Job) }
function Add-BoundedJobMember([IntPtr]$Job, [IntPtr]$Process) { [HomemadeLlm.JobTree]::Join($Job, $Process) }
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $path = (Get-Command $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $commandLine = (@($path) + @($A) | ForEach-Object { [HomemadeLlm.JobTree]::Quote([string]$_) }) -join ' '
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    # No job, no launch: a job that cannot be created throws here, before anything runs.
    $job = New-BoundedJob
    $h = [IntPtr]::Zero; $rootPid = 0; $completed = $false; $pipes = @()
    try {
        # Inheritable anonymous pipes: stdin is closed at once (EOF), stdout is returned, stderr is drained so a
        # chatty child cannot block on it.
        $in = [IO.Pipes.AnonymousPipeServerStream]::new([IO.Pipes.PipeDirection]::Out, [IO.HandleInheritability]::Inheritable)
        $out = [IO.Pipes.AnonymousPipeServerStream]::new([IO.Pipes.PipeDirection]::In, [IO.HandleInheritability]::Inheritable)
        $err = [IO.Pipes.AnonymousPipeServerStream]::new([IO.Pipes.PipeDirection]::In, [IO.HandleInheritability]::Inheritable)
        $pipes = @($in, $out, $err)
        # Created suspended, joined to the job, and only then resumed: no instruction runs outside the job. $h is
        # held from creation on, so if the join or resume fails, finally still kills and verifies the process.
        $t = [IntPtr]::Zero
        $h = [HomemadeLlm.JobTree]::CreateSuspended($commandLine, $in.ClientSafePipeHandle.DangerousGetHandle(), $out.ClientSafePipeHandle.DangerousGetHandle(), $err.ClientSafePipeHandle.DangerousGetHandle(), [ref]$t)
        $rootPid = [HomemadeLlm.JobTree]::ProcessId($h)
        try { Add-BoundedJobMember $job $h; [HomemadeLlm.JobTree]::Resume($t) } finally { [HomemadeLlm.JobTree]::Close($t) }
        foreach ($pipe in $pipes) { $pipe.DisposeLocalCopyOfClientHandle() }
        $in.Dispose()
        $stdout = [IO.StreamReader]::new($out, [Console]::OutputEncoding).ReadToEndAsync()
        $stderr = [IO.StreamReader]::new($err, [Console]::OutputEncoding).ReadToEndAsync()
        # Wait in short slices: PowerShell.Stop() and Ctrl+C cannot interrupt one long blocking wait, but can stop
        # the loop between slices, and finally then kills the tree.
        while (-not [HomemadeLlm.JobTree]::Wait($h, 200)) {
            if ([DateTime]::UtcNow -ge $deadline) { return @('__EXIT__timeout') }   # finally kills the tree
        }
        # The output ends when every holder of the pipe exits; that too must finish within the deadline.
        $left = [int][math]::Max(0, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (-not [Threading.Tasks.Task]::WaitAll(@($stdout, $stderr), $left)) { return @('__EXIT__timeout') }
        $completed = $true
        $text = $stdout.Result.TrimEnd("`r", "`n")
        # @() matters: a single output line would otherwise be a string, and string + string concatenates.
        @(if ($text) { $text -split "`r?`n" }) + "__EXIT__$([HomemadeLlm.JobTree]::ExitCode($h))"
    } finally {
        # Timeout, Ctrl+C or a stopped pipeline: never leave the command running after we stop waiting for it, and
        # if its tree cannot be confirmed dead, fail loudly: a silent 'timeout' would let callers release the GPU
        # lock while the command (possibly a GPU container operation) is still running.
        $unverified = $false
        if (-not $completed -and $h -ne [IntPtr]::Zero) {
            try {
                [HomemadeLlm.JobTree]::Terminate($job)
                $until = [DateTime]::UtcNow.AddSeconds(10)   # one deadline for the whole tree
                # Thread.Sleep, not Start-Sleep: in a stopping pipeline (Ctrl+C, PowerShell.Stop()) Start-Sleep throws,
                # which would misreport a confirmed kill as unconfirmed.
                while ((Get-BoundedJobActive $job) -gt 0 -and [DateTime]::UtcNow -lt $until) { [Threading.Thread]::Sleep(100) }
                if ((Get-BoundedJobActive $job) -gt 0) { $unverified = $true }
            } catch { $unverified = $true }
            # The root itself may be outside the job (a failed join leaves it suspended there): kill it directly too.
            # The wait below confirms it is gone.
            [HomemadeLlm.JobTree]::Kill($h)
        }
        $gone = ($h -eq [IntPtr]::Zero) -or [HomemadeLlm.JobTree]::Wait($h, 5000)
        # No kill-on-close: after a normal exit, a process the command deliberately left running keeps running.
        [HomemadeLlm.JobTree]::Close($h); [HomemadeLlm.JobTree]::Close($job)
        foreach ($pipe in $pipes) { $pipe.Dispose() }
        if (-not $gone -or $unverified) {
            $msg = "Invoke-Bounded: $Exe (pid $rootPid) or a child process could not be confirmed stopped and may still be running"
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
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:05 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:01 UTC | END]]
