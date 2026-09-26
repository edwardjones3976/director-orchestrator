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

# All descendants of a process (one CIM snapshot, walked breadth-first), with their creation times.
function Get-DescendantProcess([int]$Root) {
    $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CreationDate -ErrorAction Stop)
    $found = [System.Collections.Generic.List[object]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new(); $queue.Enqueue($Root)
    while ($queue.Count) {
        $parent = $queue.Dequeue()
        foreach ($c in $all) { if ($c.ParentProcessId -eq $parent -and $c.ProcessId -ne $parent -and $c.ProcessId -ne $Root) { $found.Add($c); $queue.Enqueue([int]$c.ProcessId) } }
    }
    $found
}
# The snapshot members still running: same PID and exactly the same CIM creation time, so a PID that was reused
# by an unrelated process is never mistaken for a survivor (and never force-stopped).
function Get-SurvivingProcess($Snapshot) {
    foreach ($s in @($Snapshot)) {
        # -ErrorAction Stop: a failed query must not look like "no survivors" (an absent PID returns nothing, not an error).
        $q = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$s.ProcessId)" -Property ProcessId, CreationDate -ErrorAction Stop
        if ($q -and $q.CreationDate -eq $s.CreationDate) { $s }
    }
}
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $path = (Get-Command $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $psi = [System.Diagnostics.ProcessStartInfo]::new($path)
    foreach ($x in $A) { $psi.ArgumentList.Add([string]$x) }   # quoted per argument, unlike a joined string
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $completed = $false
    try {
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $null = $p.StandardError.ReadToEndAsync()   # drained so a chatty stderr cannot block the child
        # Wait in short slices: PowerShell.Stop() and Ctrl+C cannot interrupt one long blocking WaitForExit, but
        # can stop the loop between slices, and finally then kills the tree.
        $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
        while (-not $p.WaitForExit(200)) {
            if ([DateTime]::UtcNow -ge $deadline) { return @('__EXIT__timeout') }   # finally kills the tree
        }
        $p.WaitForExit()   # also waits for the redirected streams to finish
        $completed = $true
        $text = $stdout.Result.TrimEnd("`r", "`n")
        # @() matters: a single output line would otherwise be a string, and string + string concatenates.
        @(if ($text) { $text -split "`r?`n" }) + "__EXIT__$($p.ExitCode)"
    } finally {
        # Timeout, Ctrl+C or a stopped pipeline: never leave the command running after we stop waiting for it.
        # If it cannot be confirmed dead, fail loudly: a silent 'timeout' would let callers release the GPU lock
        # while the command (possibly a GPU container operation) is still running.
        # HasExited covers only the root, so snapshot the whole tree before killing and verify every member is gone:
        # a root that dies first, or a partial Kill, must not leave a descendant running unnoticed.
        # On any path but normal completion (timeout, Ctrl+C, a stopped pipeline), snapshot the tree even if the root
        # already exited: Windows keeps a dead parent's PID as its children's ParentProcessId, so they are still found.
        # A CIM failure must never skip the kill: discovery and the survivor check can fail, and then the result is
        # "not confirmed", which throws (fail closed) after the kill has still been attempted.
        $tree = @(); $unverified = $false
        if (-not $completed) {
            try { $tree = @(Get-DescendantProcess $p.Id) } catch { $unverified = $true }
            if (-not $p.HasExited) { try { $p.Kill($true) } catch { } }
        }
        $gone = $p.HasExited -or $p.WaitForExit(5000)
        $id = $p.Id
        $p.Dispose()
        $survivors = @()
        try {
            $survivors = @(Get-SurvivingProcess $tree)
            foreach ($s in $survivors) { Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue }
            if ($survivors) { Start-Sleep -Seconds 2; $survivors = @(Get-SurvivingProcess $survivors) }
        } catch { $unverified = $true }
        if (-not $gone -or $survivors -or $unverified) {
            $msg = "Invoke-Bounded: $Exe (pid $id) or a child process could not be confirmed stopped and may still be running"
            # Persist the machine-wide GPU guard before any caller can unwind and release the mutex: every caller is
            # covered, not just the container path. If even that fails, flag it so callers keep the lock instead.
            $script:BoundedUnconfirmed = $true
            try {
                New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
                Add-Content -LiteralPath $GpuMarker -Value "$msg ($((Get-Date).ToUniversalTime().ToString('o')))"
            } catch { $script:BoundedGuardMissing = $true }
            throw $msg
        }
    }
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:55 UTC | END]]
