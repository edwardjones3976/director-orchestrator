# Project Uppercut Reiteration

Run this only after the shared Director passes its dry-run and one low-risk end-to-end live test. Project Uppercut is a separate deployment profile, not just another prompt.

## Isolation requirements

- Private repository allowlist limited to `edwardjones3976/Project-Uppercut` and explicitly approved dependencies.
- Dedicated Slack channel or an enforceable thread policy.
- Separate `.env`, task database, Docker volume names, provider budgets, and callback secrets.
- Project-specific acceptance checks and branch protection.
- No shared private documents, genealogy evidence, credentials, or provider sessions unless explicitly approved.
- Separate audit and backup retention.

## Reiteration checklist

1. Copy the deployment configuration, not the runtime secrets.
2. Change Compose project and volume names to `project-uppercut-director`.
3. Set the Slack channel ID and a repository allowlist containing only Project Uppercut.
4. Add Project Uppercut instructions and tests in its own repository.
5. Run the dry-run smoke test with `ALLOW_PAID_RUNS=false`.
6. Run one reversible live task on an isolated branch.
7. Review the draft PR, logs, spend, and approval behavior before enabling routine use.

No agent may merge, deploy, publish, delete, purchase, expand access, or handle sensitive Project Uppercut data without the matching owner decision gate.

