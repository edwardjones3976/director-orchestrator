# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | BEGIN]]
# Shared by train-smoke.ps1 and serve-smoke.ps1 (dot-sourced): run a native command with a host-side deadline.
# A stalled Docker or LM Studio daemon must not hang a run that holds the GPU lock, and a timed-out command
# must not keep running after we give up on it, so the whole process tree is killed on timeout.
# Returns the command's stdout lines, then '__EXIT__<code>' (or '__EXIT__timeout') as the last element.
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $path = (Get-Command $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $psi = [System.Diagnostics.ProcessStartInfo]::new($path)
    foreach ($x in $A) { $psi.ArgumentList.Add([string]$x) }   # quoted per argument, unlike a joined string
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
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
        $text = $stdout.Result.TrimEnd("`r", "`n")
        # @() matters: a single output line would otherwise be a string, and string + string concatenates.
        @(if ($text) { $text -split "`r?`n" }) + "__EXIT__$($p.ExitCode)"
    } finally {
        # Timeout, Ctrl+C or a stopped pipeline: never leave the command running after we stop waiting for it.
        if (-not $p.HasExited) { try { $p.Kill($true); $null = $p.WaitForExit(5000) } catch { } }
        $p.Dispose()
    }
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
