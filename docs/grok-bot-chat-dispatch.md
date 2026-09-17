<!-- [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:59 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 19:15 UTC | BEGIN]] -->
# Grok Bot chat dispatch

Grok Bot (`C:\Program Files\Grok Bot\Grok Bot.exe`) has no CLI. This route
drives its chat composer from a session that has no computer-use tool. The
owner ranks it among the top three ways to reach Grok Bot, above Computer Use.
It is a coordination contact only: Grok Bot's reply is review evidence for the
exact-SHA record, never merge, callback, or runtime authority (see
`docs/provider-profiles/grok.yaml`, transport `grok-bot`).

One script, three actions, every failure a terminating error that stops before
the keystroke that would send. No P/Invoke; COM `WScript.Shell` (AppActivate
by **process id**, SendKeys), the clipboard, and a GDI screenshot only.

## Where things live

All artifacts default to `%LOCALAPPDATA%\director-orchestrator\grok-bot\` —
the packet copy, the preview and after screenshots, and the send receipt —
never inside the repository. Screenshots capture a whole display, so they must
not land in a worktree. Override with `-ArtifactRoot` if needed.

## Steps (run from the repository root, in Git Bash)

1. Write the packet as an ASCII text file outside the repo, for example
   `%LOCALAPPDATA%\director-orchestrator\grok-bot\packet.txt`: a read-only
   review request, the exact 40-character SHA, the checks, and the required final
   line `VERDICT: ACCEPT | CHANGES_REQUIRED | SERIOUS_SAFETY_STOP bound to <sha>`.
   The script refuses packets that are empty, non-ASCII, or missing that line.

2. Paste:

   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/grok-bot-chat/grok_bot_chat.ps1 -Action paste -Packet "$LOCALAPPDATA/director-orchestrator/grok-bot/packet.txt"
   ```

   It finds exactly one Grok Bot main window whose executable path matches
   (two windows or a different path is a stop), writes the clipboard and reads
   it back (mismatch is a stop), activates by process id, pastes, reads the
   clipboard back again (a change mid-paste is a stop), and writes a preview
   screenshot named by the packet's sha256. It prints `digest=<sha256>`.

3. **Read the preview PNG.** The composer must show the packet tail and the SHA.
   If it does not (focus trap, empty composer, stale text), do not send; run
   `-Action clear` to abandon the paste, fix focus, and paste again. The same
   digest cannot be sent twice. Before any of this, the desktop must be idle;
   see "Idle desktop, focus guard, clipboard restore" below.
   <!-- [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:40 UTC]] -->

4. Send:

   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/grok-bot-chat/grok_bot_chat.ps1 -Action send -Digest <sha256>
   ```

   Refuses unless a preview exists for that digest, no receipt exists yet, and
   the clipboard still holds that packet. The receipt is written **before** the
   Enter keystroke, so an ambiguous submission is never retried: if in doubt,
   look at the screen, do not press again.

5. Poll (screenshot only, no input) every minute or two until the reply ends
   with the VERDICT line, typically 3-6 minutes:

   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/grok-bot-chat/grok_bot_chat.ps1 -Action shot
   ```

6. Transcribe findings and the verdict into the PR's exact-SHA review record.
   Grok's line pins can run +/-2; it sometimes posts a self-correction message.

## Validation record (2026-09-13, Windows 11, real app)

Run from the repository root with the app open on the secondary display:

| Path | Result |
| --- | --- |
| `-Action paste` with a missing packet | `Get-Content` terminates (`PathNotFound`); no clipboard write, no keystroke |
| empty packet | `packet is empty` |
| non-ASCII packet | `packet must be ASCII; non-ASCII pastes as mojibake` |
| packet without the VERDICT line | `packet must end with the exact-SHA VERDICT line` |
| `-Action send` with an unknown digest | `no preview for <digest>` |
| `-Action paste` with a validation-only packet | preview PNG shows the text and VERDICT line in the Grok Bot composer; `digest=8bf521a8…` printed; no receipt written |
| `-Action clear` | composer empty on the after-screenshot; no send receipt exists |

### Guarded `paste` on a busy desktop (2026-09-17 01:00 UTC, real app)

| Path | Result |
| --- | --- |
| `-Action focus` before the run | `focused=ChatGPT pid=41744 grok_bot_window_pid=41880 keys_would_reach_grok_bot=False` |
| first `-Action paste` | refused at the clipboard read-back (`clipboard read-back differs from the packet; not pasting`); no keystroke; owner clipboard sentinel intact. A transient race with another clipboard writer; the refusal is the designed outcome |
| second and third `-Action paste` | `AppActivate` took focus, `Assert-GrokBotFocused` passed, text landed in the composer, `clipboard=restored` and the owner's sentinel was back on the clipboard; one `.pasted.json` marker written |
| `-Action clear` | composer empty afterwards; no `.sent.json` exists; nothing was sent |

Not yet observed: the case the guard exists for, `AppActivate` returning true while focus stays with the interactive user. On this desktop AppActivate took focus each time; the guard's refusal path is proven only by `-Action focus` reporting `False` when Grok Bot is not in front.
| `-Action shot` | one PNG under `%LOCALAPPDATA%\director-orchestrator\grok-bot\` |

Nothing was sent to Grok Bot during validation.

## Constraints

- Run the script as a file from Git Bash. Do not inline PowerShell bodies that
  touch the clipboard and SendKeys through a tool: Bitdefender flagged that
  command line as injection on 2026-09-12. Never `Add-Type` user32 P/Invoke
  (`keybd_event`, `SetForegroundWindow`), blocked as `CMD:Heur.BZC.ZFV.Boxter`.
- Screen geometry defaults to the secondary display at virtual origin
  `(0,-864)`; pass `-ShotX/-ShotY/-ShotW/-ShotH` if the window moves.
- Read-only packets only. Grok Bot holds a live GitHub connection and could act.
- One packet at a time; wait for the VERDICT line before pasting the next.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 19:15 UTC | END]] -->

<!-- [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:40 UTC | BEGIN]] -->
## Idle desktop, focus guard, clipboard restore (added 2026-09-17)

**Incident.** On 2026-09-16 at 23:23 UTC a keystroke meant for Grok Bot reached
the owner's browser instead. `AppActivate` had returned `$true`, but Windows keeps
focus with an interactively active user, so the key went to the foreground window
the owner was using. An hour later a screenshot taken before a paste showed the
owner in a credential dialog with a "Copy to clipboard" button on screen; the
paste step, which overwrites the clipboard, would have replaced the value the
owner was about to copy. Neither risk was covered by the 2026-09-13 script.

**Rule.** Run `paste`, `send` and `clear` only on an idle desktop: the owner is
not typing, and no dialog is open. `-Action shot` and `-Action focus` are always
safe (no input, no clipboard). When in doubt run `focus` first; it prints
`keys_would_reach_grok_bot=True|False`.

**Guards in the script.**

- Every `SendKeys` is preceded by `Assert-GrokBotFocused`, which reads the
  focused element through managed UI Automation (`UIAutomationClient`, no user32
  P/Invoke) and stops with `keyboard focus is with '<name>' (pid N), not Grok
  Bot` unless the focus owner is the Grok Bot main-window process or a same-named
  child of the exact executable. `AppActivate` returning `$true` is not trusted.
- `paste` saves the owner's clipboard text before writing the packet and
  restores it (or clears a non-text clipboard) in a `finally` once the paste is
  verified; the output line `clipboard=restored|cleared (...)` records which.
  Non-text clipboard content (an image, a file list) cannot be restored and is
  cleared; the line says so.
- `send` no longer checks the clipboard. It requires `<digest>.pasted.json`,
  written by `paste`, and refuses if the Grok Bot pid has changed since the paste.
  Focus is asserted **before** the receipt is written, so a refused send leaves
  no false "sent" record and the same digest can be retried.

**Validation, 2026-09-17 00:37 UTC, Windows 11, real app.** `-Action focus` with
the Claude desktop app in front: `focused=claude pid=35152
grok_bot_window_pid=41880 keys_would_reach_grok_bot=False`. The script parses
(`Parser::ParseFile`, zero errors); `UIAutomationClient` loads without a
Bitdefender block. `paste` with the guard was exercised on 2026-09-17 (table above); `send` with the guard was not, and the AppActivate-succeeds-but-focus-stays case was not observed at validation
time because the desktop was in use — which is the point.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 19:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-17 | 00:59 UTC | END]] -->
