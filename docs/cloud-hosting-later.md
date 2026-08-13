# Cloud Hosting Later

Keep the first release on the always-on Windows desktop until the dry-run, approval, restart-recovery, and at least one live specialist flow are proven. Move to cloud hosting only to improve availability, remote callbacks, observability, or isolation—not merely to make the diagram look more sophisticated.

## Migration triggers

Consider cloud hosting when any two of these are true:

- The desktop causes repeated missed tasks or more than one material outage per month.
- A provider requires a public signed callback to close the loop.
- More than one owner or Slack workspace needs the service.
- Task volume requires concurrent workers or queue isolation.
- Centralized secret rotation, audit logs, or formal backups become mandatory.
- Project Uppercut needs a separately governed environment.

## Target changes

| Desktop component | Cloud replacement |
| --- | --- |
| SQLite | Managed PostgreSQL with migrations and point-in-time recovery |
| Docker Desktop | Managed containers or a small private VM with health replacement |
| Local `.env` | Managed secret store with workload identity where supported |
| Local logs | Centralized structured logs, alerts, and retention policy |
| Socket-only callbacks | HTTPS ingress with signature verification, rate limits, and allowlists |
| Manual volume backup | Encrypted automated backups plus restore drills |

Retain Slack Socket Mode unless inbound HTTP materially improves the design. Keep the task schema, approval policy, task IDs, and provider adapters stable so the move is operational rather than a rewrite.

## Cloud go/no-go gate

Do not migrate until there is a reviewed infrastructure plan, a monthly cost ceiling, a rollback path, secret-rotation procedure, backup/restore test, and explicit owner approval. Do not expose the n8n editor or orchestrator API directly to the public internet.

