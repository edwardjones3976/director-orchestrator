# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | BEGIN]]
# Grok Bot chat dispatch, step 2: press Enter in the composer, then screenshot.
# Run only after grok_paste.ps1's screenshot shows the intended packet and SHA.
param(
  [string]$ShotPath = (Join-Path $PSScriptRoot "last_shot.png"),
  [int]$ShotX = 0, [int]$ShotY = -864, [int]$ShotW = 1536, [int]$ShotH = 864
)
$ws = New-Object -ComObject WScript.Shell
if (-not $ws.AppActivate("Grok Bot")) { throw "Grok Bot window not found." }
Start-Sleep -Seconds 1
$ws.SendKeys("{ENTER}"); Start-Sleep -Seconds 3
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $ShotW, $ShotH
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($ShotX, $ShotY, 0, 0, $bmp.Size); $g.Dispose()
$bmp.Save($ShotPath); $bmp.Dispose(); "sent -> poll with grok_shot.ps1"
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | END]]
