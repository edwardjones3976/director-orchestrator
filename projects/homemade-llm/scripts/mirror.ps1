# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | BEGIN]]
# Homemade LLM nightly mirror (M0.7): robocopy C:\llm -> G:\llm-mirror, never copying secrets.
#   .\mirror.ps1            mirror now (/MIR also deletes mirror files that no longer exist in the source)
#   .\mirror.ps1 -List      show what would change; copies and deletes nothing
# Registering the nightly Scheduled Task is a separate owner step (see workstream-plan.md M0.7).
# Exit 0 = mirrored (robocopy codes 0-7); 1 = refused or robocopy failed (8+).
[CmdletBinding()]
param(
    [string]$Source = 'C:\llm',
    [string]$Dest = 'G:\llm-mirror',
    [switch]$List
)
$ErrorActionPreference = 'Stop'

# Ask Windows for the final path of an open handle: this resolves subst drives, 8.3 short names,
# junctions, symlinks and mapped drives, which string comparison cannot see.
Add-Type -TypeDefinition @'
using System; using System.Text; using System.Runtime.InteropServices; using Microsoft.Win32.SafeHandles;
public static class MirrorFinalPath {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sec, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder buffer, uint size, uint flags);
    public static string Of(string path) {
        // access 0 = attributes only; share read|write|delete; OPEN_EXISTING; BACKUP_SEMANTICS opens directories.
        using (var h = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (h.IsInvalid) throw new System.ComponentModel.Win32Exception();
            var sb = new StringBuilder(32768);
            uint n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
            if (n == 0 || n >= sb.Capacity) throw new System.ComponentModel.Win32Exception();
            var s = sb.ToString();
            if (s.StartsWith(@"\\?\UNC\")) return @"\\" + s.Substring(8);
            return s.StartsWith(@"\\?\") ? s.Substring(4) : s;
        }
    }
}
'@ -ErrorAction SilentlyContinue
# A destination may not exist yet: resolve its nearest existing ancestor and re-append the rest.
function Resolve-FinalPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $existing = $full; $tail = @()
    while ($existing -and -not (Test-Path -LiteralPath $existing)) {
        $tail = @(Split-Path $existing -Leaf) + $tail
        $existing = Split-Path $existing -Parent
    }
    if (-not $existing) { return $full }
    $final = [MirrorFinalPath]::Of($existing).TrimEnd('\')
    if ($tail.Count) { $final = Join-Path $final ($tail -join '\') }
    $final.TrimEnd('\')
}

$src = Resolve-FinalPath $Source
$dst = Resolve-FinalPath $Dest
# /MIR deletes at the destination, so refuse anything that could reach data outside the mirror.
if (-not (Test-Path $src -PathType Container)) { Write-Error "source $src is not a directory" }
if ($dst -match '^[A-Za-z]:$') { Write-Error "refusing a drive root as the mirror destination: $dst" }
# The mirror is local: a UNC destination (a share root, or a mapped drive resolved to one) is refused outright.
if ($dst.StartsWith('\\') -or $src.StartsWith('\\')) { Write-Error "refusing a UNC path: $src -> $dst" }
# Refuse any junction or symlink on either path or its parents, checked on the paths as given:
# the resolved final paths above have already looked through the links and would hide them.
foreach ($p in [IO.Path]::GetFullPath($Source).TrimEnd('\'), [IO.Path]::GetFullPath($Dest).TrimEnd('\')) {
    $cur = $p
    while ($cur) {
        if ((Test-Path -LiteralPath $cur) -and ((Get-Item -LiteralPath $cur -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "refusing a path through a junction or symlink: $cur"
        }
        $cur = Split-Path $cur -Parent
    }
}
# Both sides are final paths here, so aliases of the same folder compare equal.
if ($dst -eq $src -or $dst.StartsWith("$src\", 'OrdinalIgnoreCase') -or $src.StartsWith("$dst\", 'OrdinalIgnoreCase')) {
    Write-Error "source and destination overlap: $src -> $dst"
}

$logDir = Join-Path $src 'reports'
$rcArgs = @($src, $dst, '/MIR', '/XD', (Join-Path $src 'secrets'), '/R:2', '/W:5', '/NP', '/NDL', '/FFT')
if ($List) { $rcArgs += '/L' }
if (Test-Path $logDir -PathType Container) {
    $rcArgs += "/UNILOG:$(Join-Path $logDir ('mirror-{0}.log' -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')))"
    $rcArgs += '/TEE'
}
& robocopy.exe @rcArgs | Out-Host
$code = $LASTEXITCODE
# robocopy: 0-7 are success variants (bit flags for copied/extra/mismatched); 8+ means a failure.
Write-Output "mirror: robocopy exit $code$(if ($List) { ' (list only)' })"
exit ([int]($code -ge 8))
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | END]]
