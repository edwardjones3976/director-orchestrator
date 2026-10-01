<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:43 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 09:30 UTC | BEGIN]] -->
# Findings — Gate 8, the two holds, and schedule feasibility

**Issued by:** Claude, acting Director-Orchestrator · 2026-09-09
**Owner action required.** Finding 1 changes what this delivery can contain.

Three determinations, produced by executing WO-15 (R1 cheap-gate sweep) and
WO-14 (R4/R5 feasibility) early rather than on Wednesday and at the Saturday
go/no-go, and by closing the two lanes held open in the delegation matrix.

---

## Finding 1 — Gate 8 is **false**, not unknown

**Determined:** the Native Long Table canary evidence bundle does not exist.
The canary is **registered, not run.**

Evidence, read directly from the canary worktree
`<worktree>`:

`director/native-program/langgraph-canary.yaml` (schema
`long-table-langgraph-canary/v1`) declares the program with execution closed:

| Field | Value |
| --- | --- |
| `director_runtime.mode` | `disabled` |
| `director_runtime.enabled` | `false` |
| `director_runtime.new_intake_owner` | `legacy` |
| `director_runtime.legacy_new_intake_disabled` | `false` |
| `director_runtime.writer_lease_requested` | `false` |
| `provider_surfaces.dispatch_enabled` | `false` |
| `provider_surfaces.paid_execution_enabled` | `false` |
| every `effects.*` | `false` |
| `terminal_condition.registration_only` | **`true`** |
| `terminal_condition.may_claim_primary_runtime_active` | `false` |

The file's own terminal condition is registration only. `director/evidence/`
contains four documents dated 2026-08-25 — browser candidate acceptance, browser
reference validation, rules safety hardening, WebXR room shell — all of which
predate the canary program (2026-08-31) and none of which is a canary run. A
search of both trees for canary evidence returns the program file and its
README and nothing else.

Against what `docs/langgraph-primary-operations.md` requires of the bundle — a
full all-provider graph run with deterministic IDs through Postgres
checkpointing in the production namespace, a frozen exact head at completion,
targeted and full tests, trusted CI, security review, different-provider
exact-head acceptance, restart/Docker/network recovery drills, zero duplicate
provider/Slack/GitHub effects, and rollback evidence with preserved ledgers —
**none is present.**

**Gate 8 = `F`.** Under R2, one false gate makes the conjunction false:

```
Done      = AND over gates 1..9              F   (gate 8 false)
Permitted = Done AND NOT (OR over no-go)     F   (not a pass, and not an unknown)
```

### Why this cannot be resolved before Sunday

The canary's own `provider_surfaces.required` list names **all nine lanes** —
openai, claude, cursor, grok, antigravity, perplexity, firecrawl,
github-copilot, microsoft-365-copilot — with both
`application_live_evidence_required` and
`runtime_adapter_live_evidence_required` set to `true`. Completing the canary
therefore requires `LIVE_OK` on every one of those surfaces, including
Microsoft 365 Copilot, whose transport is hard-coded unauthenticated pending a
tenant administrator (Finding 3). The dependency chain is:

```
Gate 8  →  canary run  →  nine lanes LIVE_OK  →  M365 Copilot Work IQ  →  tenant admin consent
```

The last link is an external dependency with no path that closes this week. The
canary is also bound to a different product repository
(`edwardjones3976/long-table-bid-whist`) at baseline `4d26680f` / tree
`1e0db56f`, and its recorded Director implementation commit is `811700cf` —
PR #82's head, far behind current `main`.

### What this means for Saturday

`Activate` on the candidate-live profile is **not permitted** under the
activation gates as written. Three courses exist, and the choice is the owner's.

| Option | What ships by Sunday | Cost |
| --- | --- | --- |
| **A — floor deliverable (recommended)** | Accepted candidate on the frozen SHA; Stage A simulated evidence on profile S; n8n intake hardening and status workflow; close-out report and user guide. Gate 8 recorded `F` with this evidence and the dependency chain named | The live `Activate` and the paid canary do not happen. Attended time drops from 12.1 h to about 6.1 h and both window overruns in Finding 2 disappear |
| **B — activate with Gate 8 recorded unmet** | Everything in A plus the live phase | Requires an explicit owner decision, recorded verbatim in the report, to activate against a false activation gate. It also still needs the Finding 2 capacity fix. The Director will execute it if directed and will not represent it as a passed gate |
| **C — complete the canary** | Gate 8 satisfied properly | Not achievable this week: it needs nine lanes at `LIVE_OK` behind a tenant-administration dependency |

**Director's recommendation: A.** The delivery still contains a real accepted
candidate, a real simulated activation with the authority row genuinely moving,
working n8n integration, and an honest report. Option B is available and is a
legitimate owner call; it is not one the Director will make.

**This is what R1 was adopted for.** Under the previous plan this determination
would have arrived at the Saturday noon go/no-go with eighteen hours left. It
arrived instead with about a hundred, because determining the gate is cheap even
though satisfying it is not.

## Finding 2 — the schedule is lag-consistent but capacity-infeasible

WO-14, run early. **R4 passes, R5 fails.**

The maximum-lag constraint holds comfortably:
`t(Activate) − t(finish ValidateProviders) = 2.00 h ≤ 24 h`, and the constraint
graph contains no negative cycle. `ValidateProviders` must finish inside
`[Fri 13:00, Sat 13:00]`; the planned Saturday 09:00–11:00 sits well within it.

The capacity constraint does not hold. Aggregate looks fine — 22.50 h of owner
windows against 12.08 h of attended work, slack +10.42 h — but capacity is one
and the slack is in the wrong windows:

| Window | Available | Assigned | Result |
| --- | ---: | ---: | --- |
| Wed 09:00–17:00 | 8.00 h | 0.58 h | 7.42 h unused |
| **Thu 19:00–21:30** | 2.50 h | 4.00 h | **over by 1.50 h** |
| **Fri 18:00–20:00** | 2.00 h | 2.50 h | **over by 0.50 h** |
| Sat 09:00–17:00 | 8.00 h | 4.50 h | 3.50 h unused |
| Sun 05:00–07:00 | 2.00 h | 0.50 h | 1.50 h unused |

Thursday cannot hold both profiles' acceptance lifecycles (2.50 h for L plus
1.50 h for S), and Friday cannot hold WO-10 prep plus the Stage A pass.

**Fix, and it is free:** move profile S's acceptance lifecycle to Wednesday,
which has 7.42 h unused. That clears Thursday to 2.50 h against 2.50 h and
frees Friday to 2.00 h against 1.50 h. Under option A the overruns vanish
anyway, but the Wednesday move is worth making regardless because it front-loads
the work into the largest window.

## Finding 3 — the two holds, closed

### Antigravity: the team mode is already built, and I was wrong about it

I said reaching Antigravity's authorized team mode "is a code and charter change
to `antigravity.py`'s contract." That was wrong — the third correction I owe on
this matrix, and the most consequential in the provider's favour.

The team mode is implemented and sits behind one configuration switch,
`LANGGRAPH_PRIMARY_ANTIGRAVITY_TEAM_ENABLED` (`config.py:750`, default false).
Behind it: `antigravity_team.py`, `_artifacts.py`, `_routing.py`, `_transport.py`
and `_worker.py`; `team_ddl(schema)` composed into `_control_plane_ddl`, so the
schema is created by the ordinary `Migrate`; a `director_primary_team_guard`
trigger enforced at the database; a `TeamGrant` carrying explicit
`writable_paths`; a `PinnedSshTeamWorker`; the format pair
`("antigravity-team-engineering-v1", "qualified-native-worker-v1")` with its
schema already in `docs/schemas/`; and a dedicated qualification canary in
`provider_validation_team.py` described as "a dedicated synthetic canary through
the actual pinned SSH engineering worker."

So Antigravity's path to real engineering work with writable paths is
**configuration, migration and worker authentication**, not a contract rewrite:

1. Set `LANGGRAPH_PRIMARY_ANTIGRAVITY_TEAM_ENABLED=true` on the candidate profile.
2. `Migrate` creates the team schema and guard trigger as part of the control plane.
3. Authenticate the pinned SSH engineering worker — `antigravity_team_transport`
   states that absent authenticated worker evidence the lane fails closed.
4. Run the qualification canary in `provider_validation_team.py`.
5. Issue a `TeamGrant` with explicit writable paths bounded to a partition.

**Open question for the owner:** does a reachable SSH worker host exist for step
3? That is the one thing the repository cannot tell me, and it decides whether
this is a Thursday task or a post-Sunday one. Everything else is on the
candidate already.

### Microsoft 365 Copilot: reachable only through a tenant administrator

The lane was retired by #161 and is not in LANES. The
blocker is transport, and it is specific: Work IQ requires a **delegated work or
school Entra identity** with admin-consented `WorkIQAgent.Ask`; personal and
application permissions are unsupported, and the tenant administrator must
enable Work IQ. Only a successful authenticated response bound to its Microsoft
request ID, body digest, conversation ID and signed token subject proves tenant
enablement and licence entitlement. Environment booleans, configuration and a
consumer-app installation are not evidence.

**The determination the owner must make is binary:** is there an M365 business or
enterprise tenant with an administrator who can grant that consent? If not, the
lane is not reachable at all, and the canary requirement in Finding 1 that
depends on it is unreachable with it. Either way the lane must stay out of the
activation binding map until a verified response exists, because the combined
live signal is AND-ed across every configured binding.

## Owner decisions

**D-12 — delivery scope.** Option A, B or C from Finding 1. Recommendation: A.
This is the decision that shapes everything from Thursday onward.

**D-13 — Wednesday capacity move.** Move profile S's acceptance lifecycle into
Wednesday's unused 7.42 h. Free, and worth doing under either A or B.

**D-14 — Antigravity SSH worker.** Does a reachable, authenticable pinned SSH
worker host exist? If yes, Antigravity team mode is a Thursday task and the lane
gets real writable-path engineering work.

**D-15 — Microsoft 365 tenant.** Is there a work or school tenant with an
administrator who can consent to `WorkIQAgent.Ask`? A yes makes the lane a
post-Sunday enablement; a no closes it permanently and, with it, the canary path
in Finding 1.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 09:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:43 UTC | END]] -->
