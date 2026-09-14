<!-- [[OpenAI Codex | GPT-6 | 2026-09-13 | 21:39 UTC | BEGIN]] -->
# ChatGPT bridge activation

`CHATGPT_BRIDGE_ENABLED=true` enables the conversation bridge. Paid answers additionally require either `CHATGPT_BRIDGE_ALLOW_PAID_RUNS=true` or the existing global `ALLOW_PAID_RUNS=true`.

Use the bridge-specific opt-in when the main orchestrator must remain in offline simulation. It affects only ChatGPT bridge validation/readiness; it does not activate primary provider dispatch, parallel dispatch, or external effects. Keep the global switch and other runtime gates at their current settings.

All existing owner/channel verification, signed per-request approval, prompt/output limits, rate limits, reservation accounting, encryption and delivery reconciliation remain mandatory. Enabling the bridge does not approve a pending prompt or replay a previous failed request.

The configured daily budget is enforced against estimated per-request reservations, not invoice reconciliation. Do not represent that estimate as measured actual spend.

To disable the bridge regardless of the global paid switch, set `CHATGPT_BRIDGE_ENABLED=false`. To remove only the independent paid opt-in, set `CHATGPT_BRIDGE_ALLOW_PAID_RUNS=false`; global paid authorization, if enabled, retains its previous behavior.

Apply a reviewed release and configuration to the existing single runtime owner. Verify the bridge readiness flag and unchanged global provider gates, then use one fresh Slack request and its exact signed owner approval. An OWNER_DECISION_REQUIRED reply proves intake only; an actual answer must be delivered and verified before declaring the provider functional.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-13 | 21:39 UTC | END]] -->
