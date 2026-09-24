<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | BEGIN]] -->
# Chat UI runbook (M0.5)

This runbook covers the Open WebUI + SearXNG stack in [`open-webui.yml`](open-webui.yml). It runs in front of LM Studio on blackbox.

Run every command from this folder, in PowerShell. The env file is `C:\llm\secrets\open-webui.env`: it is outside the repo, holds the image digests and secrets, and must never be committed.

```powershell
$compose = 'docker compose -p homemade-llm -f open-webui.yml --env-file C:\llm\secrets\open-webui.env'
```

## 1. First boot and closing signup

1. Start the stack.

   ```powershell
   Invoke-Expression "$compose up -d"
   ```

2. Open **http://127.0.0.1:3000** on blackbox and create the account. The first account becomes the admin.
   - This local account is separate from any account on openwebui.com.
   - Check that it exists: `(Invoke-RestMethod http://127.0.0.1:3000/api/config).onboarding` must print `False`.
3. Close signup. In the env file, set `ENABLE_SIGNUP=false`, then recreate the container.

   ```powershell
   Invoke-Expression "$compose up -d --force-recreate open-webui"
   ```

   `ENABLE_PERSISTENT_CONFIG=false` in the compose file makes the env file authoritative, so this takes effect.
4. Verify: `(Invoke-RestMethod http://127.0.0.1:3000/api/config).features.enable_signup` must print `False`.

## 2. Phone access with Tailscale Serve (never Funnel)

Prerequisites:
- blackbox is signed in to your tailnet (`tailscale up`).
- MagicDNS and HTTPS certificates are enabled in the Tailscale admin console.

1. Find blackbox's HTTPS name.

   ```powershell
   (tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
   ```

   This prints something like `blackbox.<tailnet>.ts.net`.
2. Publish Open WebUI to the tailnet only. It still listens on 127.0.0.1; `tailscale serve` proxies to it.

   ```powershell
   tailscale serve --bg 3000
   tailscale serve status
   ```

   The status must show `https://<name>` pointing at `http://127.0.0.1:3000` and **no** `Funnel on` line.
3. Set `WEBUI_URL=https://<name>` in the env file, then recreate the container as in 1.3.
4. **Acceptance:** on the phone, joined to the tailnet, open `https://<name>`, sign in, and ask a question with web search enabled. The reply must cite web results.
5. **Rollback:** stop publishing, then confirm with `tailscale serve status`, which should show nothing served.

   ```powershell
   tailscale serve --https=443 off
   ```

   Setting `WEBUI_URL` back to `http://127.0.0.1:3000` is optional.

## 3. Backup and restore

The chats, users and memory live in the Docker volume `homemade-llm_open-webui-data`. That volume sits inside Docker Desktop's VM, not under `C:\llm`, so the nightly mirror can't see it. [`../scripts/backup-openwebui.ps1`](../scripts/backup-openwebui.ps1) fixes that:
- It archives the volume into `C:\llm\state\open-webui\`.
- The mirror copies that folder to `G:\llm-mirror`.
- It stops Open WebUI for a few seconds to take a consistent SQLite copy.
- It skips the re-downloadable model cache (about 1 GB).

```powershell
..\scripts\backup-openwebui.ps1
```

To restore, stop Open WebUI, restore an archive, then start it again:

```powershell
Invoke-Expression "$compose stop open-webui"
..\scripts\backup-openwebui.ps1 -Restore C:\llm\state\open-webui\open-webui-<stamp>.tgz
Invoke-Expression "$compose start open-webui"
```

Running the backup before the 03:30 mirror task is an owner step. It changes a Scheduled Task, so it needs a Go.

## 4. Diagnostics

| Check | Command | Expect |
|---|---|---|
| Open WebUI health | `curl.exe -s http://127.0.0.1:3000/health` | `{"status":true}` |
| SearXNG returns JSON | `curl.exe -s "http://127.0.0.1:8088/search?q=test&format=json"` | JSON with `results` |
| Open WebUI reaches LM Studio | `docker exec homemade-llm-open-webui-1 curl -s http://host.docker.internal:1234/v1/models` | model list |
| Nothing leaks to the LAN | `Get-NetTCPConnection -State Listen -LocalPort 1234,3000,8088` | every `LocalAddress` is `127.0.0.1` |
| Stack status | `Invoke-Expression "$compose ps"` | both services `running` |

To stop the stack while keeping its data: `Invoke-Expression "$compose down"`. **Never add `-v`**, which deletes the data volume.
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:12 UTC | END]] -->
