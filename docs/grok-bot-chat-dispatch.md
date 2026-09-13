<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | BEGIN]] -->
# Grok Bot chat dispatch

Grok Bot (`C:\Program Files\Grok Bot\Grok Bot.exe`) has no CLI. This route
drives its chat composer from a Director session without a computer-use tool.
The owner ranks it among the top three ways to reach Grok Bot, above
"Computer Use". Verified 2026-09-12: exact-SHA reviews of #131 and #151
returned line-pinned findings and a bound VERDICT.

## Steps

1. Write the packet to `packet.txt` (read-only review request, exact 40-char
   SHA, the checks, and the required final `VERDICT: ... bound to <sha>` line).
2. `powershell.exe -NoProfile -ExecutionPolicy Bypass -File grok_paste.ps1`
   — pastes into the composer and screenshots. **Read the PNG**: the composer
   must show the packet tail and the SHA. `SendKeys` can paste stale clipboard.
3. `powershell.exe -NoProfile -ExecutionPolicy Bypass -File grok_enter.ps1`
   — sends. Grok Bot reads the PR and code itself through its GitHub connection.
4. Poll `grok_shot.ps1` (screenshot only) every minute or two until the reply
   ends with the VERDICT line; typical reply 3–6 minutes.
5. Transcribe findings and verdict into the PR's exact-SHA review record.
   Grok's line pins can run ±2; it sometimes posts a self-correction message.

## Rules

- Run these from Git Bash via `powershell.exe -File`. Do not inline the script
  body through the PowerShell tool: Bitdefender flagged that command line
  (clipboard + SendKeys) as injection on 2026-09-12. The owner added these
  script files to the Bitdefender exclusion list; keep the logic in the files.
- Never use `Add-Type` with user32 P/Invoke (`keybd_event`, `SetForegroundWindow`)
  — that was blocked as `CMD:Heur.BZC.ZFV.Boxter` the same day.
- Screen geometry: the Grok Bot window sits on the secondary display
  (VG27WQ1B, virtual origin 0,-864). Adjust `-ShotY` if the window moves.
- Read-only packets only. Grok Bot holds a live GitHub connection and could act.
- One packet at a time; wait for the VERDICT line before sending the next.

Owner decision 2026-09-12: this route ranks among the top three ways to use Grok Bot, above Computer Use. It is a coordination contact only — Grok Bot replies are review evidence for the exact-SHA record, never merge, callback, or runtime authority (see `docs/provider-profiles/grok.yaml`, transport `grok-bot`).
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:56 UTC | END]] -->
