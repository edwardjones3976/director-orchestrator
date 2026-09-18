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
#   grok_bot_chat.ps1 -Action focus                  -> report which process owns keyboard focus;
#                                                       no input, no clipboard
#
# [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | BEGIN]] focus guard + clipboard restore
# Two guards added after a keystroke meant for Grok Bot reached the owner's browser
# on 2026-09-16 while the desktop was in interactive use:
#   * every SendKeys is preceded by Assert-GrokBotFocused, which reads the focused
#     element through managed UI Automation (no user32 P/Invoke) and stops unless
#     the focus owner is the Grok Bot process; AppActivate returning $true is not
#     trusted, because Windows may refuse to move focus away from an active user;
#   * paste saves the owner's clipboard text before writing the packet and restores
#     it once the paste has been verified, so a value the owner was about to paste
#     (an API key, say) is not replaced; send no longer depends on the clipboard
#     and instead requires the paste marker written for the same digest and pid.
# [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | END]]
#
# Artifacts (packet copy, previews, receipts) live under
# %LOCALAPPDATA%\director-orchestrator\grok-bot\ — never in tracked source.
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet("paste", "send", "shot", "clear", "focus")][string]$Action,
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

# [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | BEGIN]]
function Get-FocusedProcess {
  # Managed UI Automation only. The focused element's owning process is the one a
  # keystroke would reach right now. Never user32 P/Invoke (Bitdefender Boxter rule).
  Add-Type -AssemblyName UIAutomationClient
  $element = [System.Windows.Automation.AutomationElement]::FocusedElement
  if ($null -eq $element) { throw "no element owns keyboard focus; not sending keys" }
  return Get-Process -Id $element.Current.ProcessId -ErrorAction Stop
}

function Assert-GrokBotFocused([System.Diagnostics.Process]$Process) {
  # The focus owner must be the Grok Bot main-window process, or a same-named
  # child of the exact executable (Chromium renderers share the image name).
  $focused = Get-FocusedProcess
  $samePath = $false
  try { $samePath = ($focused.Name -eq $Process.Name) -and ($focused.Path -eq $Executable) } catch { $samePath = $false }
  if ($focused.Id -ne $Process.Id -and -not $samePath) {
    throw "keyboard focus is with '$($focused.Name)' (pid $($focused.Id)), not Grok Bot (pid $($Process.Id)); not sending keys"
  }
}

function Read-ClipboardText {
  # Text only. Non-text clipboard content (image, files) reads as $null and is not restored.
  try { return (Get-Clipboard -Raw -ErrorAction Stop) } catch { return $null }
}

function Restore-ClipboardText($Saved) {
  # Untyped on purpose: a [string] parameter turns $null into "" and
  # Set-Clipboard rejects an empty string, which broke the first paste that
  # started from an empty clipboard (2026-09-18). Empty and null both mean clear.
  # [[Claude Code | Claude Opus 5 | 2026-09-18 | 17:35 UTC]]
  if (-not [string]::IsNullOrEmpty($Saved)) { Set-Clipboard -Value $Saved; return "restored" }
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.Clipboard]::Clear()
  return "cleared (previous content was empty or not text)"
}
# [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | END]]

switch ($Action) {
  "focus" {
    # Read-only diagnostic: no AppActivate, no keys, no clipboard.
    $focused = Get-FocusedProcess
    $bot = @(Get-Process -Name "Grok Bot" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and $_.Path -eq $Executable })
    $isBot = ($bot.Count -eq 1) -and (($focused.Id -eq $bot[0].Id) -or ($focused.Name -eq $bot[0].Name))
    Write-Output ("focused=" + $focused.Name + " pid=" + $focused.Id + " grok_bot_window_pid=" + $(if ($bot.Count -eq 1) { $bot[0].Id } else { "none" }) + " keys_would_reach_grok_bot=" + $isBot)
  }
  "clear" {
    $process = Get-GrokBot
    $ws = Activate $process
    Assert-GrokBotFocused $process  # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC]]
    $ws.SendKeys("^a"); Start-Sleep -Milliseconds 300
    Assert-GrokBotFocused $process
    $ws.SendKeys("{DEL}"); Start-Sleep -Seconds 1
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
    # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | BEGIN]] save the owner's clipboard, guard focus, restore
    $saved = Read-ClipboardText
    Set-Clipboard -Value $text
    $back = Get-Clipboard -Raw
    if ($back -ne $text) { Restore-ClipboardText $saved | Out-Null; throw "clipboard read-back differs from the packet; not pasting" }

    try {
      $ws = Activate $process
      Assert-GrokBotFocused $process
      $ws.SendKeys("^v"); Start-Sleep -Seconds 2
      $back = Get-Clipboard -Raw
      if ($back -ne $text) { throw "clipboard changed during paste; do not send" }
    } finally {
      $clip = Restore-ClipboardText $saved
    }
    $preview = Invoke-Screenshot (Join-Path $ArtifactRoot "$sha.preview.png")
    @{ digest = $sha; pid = $process.Id; pasted_at = (Get-Date).ToUniversalTime().ToString("o") } |
      ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $ArtifactRoot "$sha.pasted.json")
    Write-Output "digest=$sha"
    Write-Output "preview=$preview"
    Write-Output "clipboard=$clip"
    # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | END]]
    Write-Output "next: READ the preview; the composer must show the packet tail and the SHA. Then -Action send -Digest $sha"
  }
  "send" {
    if ($Digest -notmatch "^[0-9a-f]{64}$") { throw "-Digest must be the sha256 printed by paste" }
    $preview = Join-Path $ArtifactRoot "$Digest.preview.png"
    $receipt = Join-Path $ArtifactRoot "$Digest.sent.json"
    if (-not (Test-Path $preview)) { throw "no preview for $Digest; paste and read it first" }
    if (Test-Path $receipt) { throw "packet $Digest was already sent; a second Enter is an ambiguous retry" }
    # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | BEGIN]] the paste marker replaces the
    # clipboard check (the clipboard is restored to the owner's content after paste).
    $marker = Join-Path $ArtifactRoot "$Digest.pasted.json"
    if (-not (Test-Path $marker)) { throw "no paste marker for $Digest; paste and read the preview first" }
    $pasted = Get-Content -Raw -Encoding UTF8 $marker | ConvertFrom-Json

    $process = Get-GrokBot
    if ([int]$pasted.pid -ne $process.Id) { throw "Grok Bot pid changed since the paste ($($pasted.pid) -> $($process.Id)); the composer content is unverified, re-paste" }
    # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC | END]]
    $ws = Activate $process
    Assert-GrokBotFocused $process  # [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:35 UTC]] prove focus BEFORE the receipt, so a refusal leaves no false "sent"
    # Write the receipt BEFORE the keystroke: if the process dies between the
    # two, the record says "sent" and the operator verifies on screen rather
    # than pressing Enter again.
    @{ digest = $Digest; pid = $process.Id; sent_at = (Get-Date).ToUniversalTime().ToString("o") } |
      ConvertTo-Json | Set-Content -Encoding UTF8 $receipt
    $ws.SendKeys("{ENTER}"); Start-Sleep -Seconds 3
    Write-Output "sent=$Digest"
    Write-Output ("after=" + (Invoke-Screenshot (Join-Path $ArtifactRoot "$Digest.after.png")))
  }
}
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 17:20 UTC | END]]
