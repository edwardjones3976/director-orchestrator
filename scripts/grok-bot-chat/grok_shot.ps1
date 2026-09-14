# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | BEGIN]]
# Grok Bot chat dispatch, step 3: screenshot only (no input). Poll until the
# reply's final VERDICT line is visible, then read the PNG.
param(
  [string]$ShotPath = (Join-Path $PSScriptRoot "last_shot.png"),
  [int]$ShotX = 0, [int]$ShotY = -864, [int]$ShotW = 1536, [int]$ShotH = 864
)
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $ShotW, $ShotH
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($ShotX, $ShotY, 0, 0, $bmp.Size); $g.Dispose()
$bmp.Save($ShotPath); $bmp.Dispose(); "shot -> $ShotPath"
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | END]]
