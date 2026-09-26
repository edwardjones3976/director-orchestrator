# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | BEGIN]]
# Behavioural checks for mirror.ps1 on scratch folders (never C:\llm or G:\llm-mirror).
#   pwsh -File projects\homemade-llm\scripts\test-mirror.ps1
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'mirror.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ("mirror-test-" + [guid]::NewGuid().ToString('N'))
$failures = [System.Collections.Generic.List[string]]::new()
function Assert-Equal($Actual, $Expected, [string]$Case) {
    if ($Actual -ne $Expected) { $failures.Add("$Case`: expected '$Expected', got '$Actual'") } else { Write-Output "ok  $Case" }
}
function Invoke-Mirror([string[]]$Arguments) {
    & pwsh -NoProfile -File $script @Arguments 2>&1 | Out-Null
    $LASTEXITCODE
}

try {
    $src = Join-Path $work 'src'; $dst = Join-Path $work 'dst'
    New-Item -ItemType Directory -Force (Join-Path $src 'models'), (Join-Path $src 'secrets'), (Join-Path $src 'reports') | Out-Null
    Set-Content (Join-Path $src 'models\adapter.bin') ('m' * 4096) -NoNewline
    Set-Content (Join-Path $src 'secrets\hf-token') 'never mirrored' -NoNewline

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst, '-List')) 0 '-List exits 0'
    Assert-Equal (Test-Path (Join-Path $dst 'models\adapter.bin')) $false '-List copies nothing'

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst)) 0 'mirror exits 0'
    Assert-Equal (Test-Path (Join-Path $dst 'secrets')) $false 'secrets are never mirrored'
    $a = (Get-FileHash (Join-Path $src 'models\adapter.bin')).Hash
    Assert-Equal (Get-FileHash (Join-Path $dst 'models\adapter.bin')).Hash $a 'mirrored file hash matches'

    # Restore drill: lose the source file, copy it back from the mirror, compare hashes.
    Remove-Item (Join-Path $src 'models\adapter.bin')
    Copy-Item (Join-Path $dst 'models\adapter.bin') (Join-Path $src 'models\adapter.bin')
    Assert-Equal (Get-FileHash (Join-Path $src 'models\adapter.bin')).Hash $a 'restored file hash matches'

    Set-Content (Join-Path $dst 'stale.txt') 'gone from source'
    Invoke-Mirror @('-Source', $src, '-Dest', $dst) | Out-Null
    Assert-Equal (Test-Path (Join-Path $dst 'stale.txt')) $false '/MIR removes files no longer in the source'
    Assert-Equal (@(Get-ChildItem (Join-Path $src 'reports') -Filter 'mirror-*.log').Count -gt 0) $true 'a log lands in reports'

    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', 'Q:\')) 1 'refuses an absent drive root destination'
    # Existing roots use -List so a regressed guard could only list, never delete.
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "$($env:SystemDrive)\", '-List')) 1 'refuses an existing drive root (C:\)'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $env:SystemDrive, '-List')) 1 'refuses a bare drive (C:), which means the current directory'
    Assert-Equal (Invoke-Mirror @('-Source', "$($env:SystemDrive)\", '-Dest', $dst, '-List')) 1 'refuses a drive root source'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "\\?\$dst", '-List')) 1 'refuses an extended \\?\ destination'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "\\.\$dst", '-List')) 1 'refuses a device \\.\ destination'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "$dst.", '-List')) 1 'refuses a trailing-dot component'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "$dst ", '-List')) 1 'refuses a trailing-space component'
    # The resolved-path check is defence in depth: subst cannot target a dotted folder and links are
    # refused earlier, so exercise Assert-Unambiguous directly, loaded from the script's syntax tree.
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
    $fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-Unambiguous' }, $true) | Select-Object -First 1
    . ([scriptblock]::Create($fn.Extent.Text))
    $threw = { param($p) try { Assert-Unambiguous $p 'test'; $false } catch { $true } }
    Assert-Equal (& $threw 'C:\a\dotted.\m') $true 'resolved path with a dotted component is refused'
    Assert-Equal (& $threw 'C:\a\spaced \m') $true 'resolved path with a trailing-space component is refused'
    Assert-Equal (& $threw 'C:\a\b.txt\m') $false 'an interior dot is allowed'
    Assert-Equal (& $threw 'C:\a\..\b') $false 'dot-dot segments are not treated as dotted names'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', (Join-Path $src 'inner'))) 1 'refuses a destination inside the source'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $src)) 1 'refuses source equal to destination'
    Assert-Equal (Invoke-Mirror @('-Source', (Join-Path $work 'missing'), '-Dest', $dst)) 1 'refuses a missing source'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', '\\localhost\c$')) 1 'refuses a UNC share root'
    $elsewhere = Join-Path $work 'elsewhere'; New-Item -ItemType Directory $elsewhere | Out-Null
    Set-Content (Join-Path $elsewhere 'keep.txt') 'must survive'
    $link = Join-Path $work 'link'; New-Item -ItemType Junction -Path $link -Target $elsewhere | Out-Null
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $link)) 1 'refuses a junction destination'
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', (Join-Path $link 'sub'))) 1 'refuses a destination beneath a junction'
    Assert-Equal (Test-Path (Join-Path $elsewhere 'keep.txt')) $true 'junction target left untouched'

    # A junction *inside* the destination: a real (non -List) run on scratch folders must refuse
    # and leave the junction's target intact.
    $innerLink = Join-Path $dst 'inner-link'
    New-Item -ItemType Junction -Path $innerLink -Target $elsewhere | Out-Null
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst)) 1 'refuses a destination containing a junction'
    Assert-Equal (Test-Path (Join-Path $elsewhere 'keep.txt')) $true 'target of an inner junction left untouched'
    [IO.Directory]::Delete($innerLink)   # removes the link only, never its target
    Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $dst)) 0 'mirrors again once the inner junction is gone'

    # 8.3 alias of the source used as the destination must resolve to the same folder and be refused.
    $short = (cmd /c "for %I in (`"$src`") do @echo %~sI").Trim()
    if ($short -and $short -ne $src) {
        Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', $short)) 1 'refuses an 8.3 alias of the source'
    } else { Write-Output 'skip 8.3 alias (short names disabled on this volume)' }

    # subst alias: map a free letter onto the work folder, then name the source's parent through it.
    $letter = [char[]](90..68) | Where-Object { -not (Test-Path "$($_):\") } | Select-Object -First 1
    subst "$($letter):" $work | Out-Null
    try {
        Assert-Equal (Invoke-Mirror @('-Source', $src, '-Dest', "$($letter):\src")) 1 'refuses a subst alias equal to the source'
        Assert-Equal (Invoke-Mirror @('-Source', (Join-Path $src 'models'), '-Dest', "$($letter):\src")) 1 'refuses a subst alias that is an ancestor of the source'
    } finally { subst "$($letter):" /D | Out-Null }
    Assert-Equal (Test-Path (Join-Path $src 'models\adapter.bin')) $true 'source intact after alias refusals'
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count) { $failures | ForEach-Object { Write-Output "FAIL $_" }; exit 1 }
Write-Output 'test-mirror: all cases passed'
exit 0
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | END]]
