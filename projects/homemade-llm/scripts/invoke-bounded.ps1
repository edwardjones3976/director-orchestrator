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
    $stdout = $p.StandardOutput.ReadToEndAsync()
    $null = $p.StandardError.ReadToEndAsync()   # drained so a chatty stderr cannot block the child
    if (-not $p.WaitForExit($Seconds * 1000)) {
        $p.Kill($true)   # the entire tree, not just the direct child
        $null = $p.WaitForExit(5000)
        return @('__EXIT__timeout')
    }
    $p.WaitForExit()   # also waits for the redirected streams to finish
    $text = $stdout.Result.TrimEnd("`r", "`n")
    # @() matters: a single output line would otherwise be a string, and string + string concatenates.
    @(if ($text) { $text -split "`r?`n" }) + "__EXIT__$($p.ExitCode)"
}
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:00 UTC | END]]
