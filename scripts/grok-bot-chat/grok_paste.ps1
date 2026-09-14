# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | BEGIN]]
# Grok Bot chat dispatch, step 1: put the packet in the composer (does NOT send).
# Reads the packet from packet.txt next to this script, activates the Grok Bot
# window by title via COM, pastes with ctrl+v, and screenshots the display the
# window lives on so the caller can verify the composer BEFORE pressing Enter.
# No P/Invoke, no user32: WScript.Shell + clipboard + System.Drawing only.
param(
  [string]$PacketPath = (Join-Path $PSScriptRoot "packet.txt"),
  [string]$ShotPath = (Join-Path $PSScriptRoot "last_shot.png"),
  [int]$ShotX = 0, [int]$ShotY = -864, [int]$ShotW = 1536, [int]$ShotH = 864
)
$packet = Get-Content -Raw $PacketPath
Set-Clipboard -Value $packet
$ws = New-Object -ComObject WScript.Shell
if (-not $ws.AppActivate("Grok Bot")) { throw "Grok Bot window not found; launch 'C:\Program Files\Grok Bot\Grok Bot.exe' first." }
Start-Sleep -Seconds 1
$ws.SendKeys("^v"); Start-Sleep -Seconds 2
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $ShotW, $ShotH
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($ShotX, $ShotY, 0, 0, $bmp.Size); $g.Dispose()
$bmp.Save($ShotPath); $bmp.Dispose(); "pasted -> verify $ShotPath, then run grok_enter.ps1"
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | END]]
