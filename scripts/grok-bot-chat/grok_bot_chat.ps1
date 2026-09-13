# [[Claude Code | Claude Opus 5 | 2026-09-12 | 17:20 UTC | BEGIN]]
# Grok Bot chat dispatch: paste a read-only review packet into the Grok Bot
# composer, verify it on screen, send it once, and poll for the reply.
#
# Every failure stops before the keystroke that would send. Nothing here uses
# P/Invoke; the only automation is COM WScript.Shell (AppActivate by process id,
# SendKeys) plus the clipboard and a GDI screenshot.
#
#   grok_bot_chat.ps1 -Action paste -Packet <file>   -> clipboard verified, pasted,
#                                                       preview PNG bound to the packet digest
#   grok_bot_chat.ps1 -Action send  -Digest <sha256> -> refuses unless a preview for that
#                                                       digest exists and it was never sent
#   grok_bot_chat.ps1 -Action shot                   -> screenshot only, no input
#   grok_bot_chat.ps1 -Action clear                  -> select-all + delete in the composer
#                                                       (abandon an unsent paste); never sends
#
# Artifacts (packet copy, previews, receipts) live under
# %LOCALAPPDATA%\director-orchestrator\grok-bot\ — never in tracked source.
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet("paste", "send", "shot", "clear")][string]$Action,
  [string]$Packet,
  [string]$Digest,
  [string]$ArtifactRoot = (Join-Path $env:LOCALAPPDATA "director-orchestrator\grok-bot"),
  [string]$Executable = "C:\Program Files\Grok Bot\Grok Bot.exe",
  [int]$ShotX = 0, [int]$ShotY = -864, [int]$ShotW = 1536, [int]$ShotH = 864
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

function Get-GrokBot {
  # Target the process, not the caption: the main-window process of the exact
  # executable. Two windows or a different path is a stop, not a guess.
  $all = @(Get-Process -Name "Grok Bot" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.Path -eq $Executable })
  if ($all.Count -ne 1) { throw "expected exactly one Grok Bot main window from $Executable, found $($all.Count)" }
  return $all[0]
}

function Invoke-Screenshot([string]$Path) {
  Add-Type -AssemblyName System.Drawing
  $bmp = New-Object System.Drawing.Bitmap $ShotW, $ShotH
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  try { $g.CopyFromScreen($ShotX, $ShotY, 0, 0, $bmp.Size) } finally { $g.Dispose() }
  $bmp.Save($Path); $bmp.Dispose()
  return $Path
}

function Activate([System.Diagnostics.Process]$Process) {
  $ws = New-Object -ComObject WScript.Shell
  if (-not $ws.AppActivate($Process.Id)) { throw "AppActivate($($Process.Id)) refused; not sending" }
  Start-Sleep -Milliseconds 800
  return $ws
}

switch ($Action) {
  "clear" {
    $process = Get-GrokBot
    $ws = Activate $process
    $ws.SendKeys("^a"); Start-Sleep -Milliseconds 300; $ws.SendKeys("{DEL}"); Start-Sleep -Seconds 1
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    Write-Output ("cleared=" + (Invoke-Screenshot (Join-Path $ArtifactRoot "clear-$stamp.png")))
  }
  "shot" {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    Write-Output (Invoke-Screenshot (Join-Path $ArtifactRoot "shot-$stamp.png"))
  }
  "paste" {
    if (-not $Packet) { throw "-Packet is required" }
    $text = Get-Content -Raw -Encoding UTF8 $Packet
    if ([string]::IsNullOrWhiteSpace($text)) { throw "packet is empty" }
    if ($text -match "[^\x09\x0A\x0D\x20-\x7E]") { throw "packet must be ASCII; non-ASCII pastes as mojibake" }
    if ($text -notmatch "VERDICT: ACCEPT \| CHANGES_REQUIRED \| SERIOUS_SAFETY_STOP bound to [0-9a-f]{40}") {
      throw "packet must end with the exact-SHA VERDICT line"
    }
    $sha = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash(
      [Text.Encoding]::UTF8.GetBytes($text))).Replace("-", "").ToLower()
    Copy-Item $Packet (Join-Path $ArtifactRoot "$sha.packet.txt") -Force
    if (Test-Path (Join-Path $ArtifactRoot "$sha.sent.json")) { throw "packet $sha was already sent; no retry" }

    $process = Get-GrokBot
    Set-Clipboard -Value $text
    $back = Get-Clipboard -Raw
    if ($back -ne $text) { throw "clipboard read-back differs from the packet; not pasting" }

    $ws = Activate $process
    $ws.SendKeys("^v"); Start-Sleep -Seconds 2
    $back = Get-Clipboard -Raw
    if ($back -ne $text) { throw "clipboard changed during paste; do not send" }
    $preview = Invoke-Screenshot (Join-Path $ArtifactRoot "$sha.preview.png")
    Write-Output "digest=$sha"
    Write-Output "preview=$preview"
    Write-Output "next: READ the preview; the composer must show the packet tail and the SHA. Then -Action send -Digest $sha"
  }
  "send" {
    if ($Digest -notmatch "^[0-9a-f]{64}$") { throw "-Digest must be the sha256 printed by paste" }
    $preview = Join-Path $ArtifactRoot "$Digest.preview.png"
    $receipt = Join-Path $ArtifactRoot "$Digest.sent.json"
    if (-not (Test-Path $preview)) { throw "no preview for $Digest; paste and read it first" }
    if (Test-Path $receipt) { throw "packet $Digest was already sent; a second Enter is an ambiguous retry" }
    $text = Get-Content -Raw -Encoding UTF8 (Join-Path $ArtifactRoot "$Digest.packet.txt")
    if ((Get-Clipboard -Raw) -ne $text) { throw "clipboard no longer holds packet $Digest; re-paste" }

    $process = Get-GrokBot
    # Write the receipt BEFORE the keystroke: if the process dies between the
    # two, the record says "sent" and the operator verifies on screen rather
    # than pressing Enter again.
    @{ digest = $Digest; pid = $process.Id; sent_at = (Get-Date).ToUniversalTime().ToString("o") } |
      ConvertTo-Json | Set-Content -Encoding UTF8 $receipt
    $ws = Activate $process
    $ws.SendKeys("{ENTER}"); Start-Sleep -Seconds 3
    Write-Output "sent=$Digest"
    Write-Output ("after=" + (Invoke-Screenshot (Join-Path $ArtifactRoot "$Digest.after.png")))
  }
}
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 17:20 UTC | END]]
