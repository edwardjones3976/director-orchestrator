<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:12 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]] -->
# Rights provisioning on verified install

**Status:** Mechanism only. Default off. No runtime authorization gate consumes
these rights. A subject appears in the `Settings` snapshot only when
`RIGHTS_LEDGER_PATH` names a readable ledger, a verified install has been
recorded, and `RIGHTS_ACTIVE_BUILD_DIGESTS_JSON` independently identifies the
same build as active. A deployment that has not opted in behaves exactly as it
did before this document existed.

> **`RIGHTS_LEDGER_PATH` must name a dedicated file used for nothing but this
> ledger.** Never set it to `SQLITE_PATH`, a completion-gate store, a LangGraph
> checkpoint database, or any other product database. `Settings` opens the path
> read-only and refuses one that does not already carry the ledger schema, so a
> mistyped path fails closed instead of migrating live data — but the writable
> `RightsLedger(...)` used for provisioning creates that schema wherever it is
> aimed, so the provisioning side has no such safety net.

**Schema authority:** `src/director_orchestrator/rights_provisioning.py`.

**Runtime effect today: none.** Nothing reads these rights at a decision point.
The readiness change that would give them one is written down but deliberately
unapplied; see `rights-provisioning-pending-hunk.md`.

## Why this exists

The owner's requirement: a provider lane or a project should receive its rights
immediately once it has properly installed and built, including projects that
arrive later, without a separate manual grant for each one.

Taken literally, "anything that builds gets rights" would hand authority to a
subject nobody has reviewed. That is the inverse of how every other gate in this
service behaves, and it is the failure mode this design exists to avoid while
still delivering what was actually asked for: no hand-configuration step.

The resolution is that a grant is **automatic but never silent, and always
reversible**.

## The four properties

1. **Closed vocabulary.** `RIGHTS_VOCABULARY` names every right that may ever be
   granted, per subject kind. A declaration naming anything else is refused
   whole rather than filtered down, because a partially honoured declaration
   grants something other than what was asked for. An unreviewed arrival
   therefore cannot invent authority for itself; widening the vocabulary is a
   reviewed code change.

2. **Evidence, or refusal.** A grant requires an `InstallVerification` carrying
   an exact sha256 build digest. There is no default-open path. The digest binds
   the grant to what was actually built, so a right cannot silently outlive the
   artifact it was granted for. A caller must separately supply the active build
   digest when reading usable rights; the ledger never treats its own grant row
   as proof of what is currently running.

3. **The notice is not optional.** The owner notice is written in the same
   transaction as the grants it describes. "Granted, but nobody was told" is not
   a state this module can express. Rights that carry authority over another
   contributor's work — `review_verdict`, `repository_write`, `paid_dispatch` —
   are listed separately on the notice so an escalation is never something the
   owner has to go looking for.

4. **Revocation outranks reinstallation.** Once the owner revokes a right, no
   later build restores it. `provision()` reports it under `withheld_revoked`
   rather than silently omitting it, so a reinstall that expected the right
   cannot mistake refusal for success. Only an explicit `restore_right` call
   brings it back. This is the property that keeps "automatic" from meaning
   "unrevocable in practice".

Re-provisioning an unchanged build grants nothing and raises no second notice.
Without that, a restart loop would train the owner to ignore the notice that
matters.

## What a held right does not do

This module records which subject holds which right, on what evidence, and
whether the owner has been told. It dispatches nothing, writes to no repository,
and decides no verdict. The deterministic policy gates remain the authority on
what a held right permits, and a held right that no gate consults has no effect.

In particular, holding `review_verdict` grants a lane nothing today: no
readiness expression, policy gate, or router consults it. Were the pending
readiness change applied, the right would make a lane *eligible* for verdict
authority and no more — it would still not widen which assignment kinds that
lane admits, because `CAPABILITY_BY_KIND` in `antigravity.py` would remain the
separate, stricter table governing admitted work.

## Configuration

| Variable | Meaning |
| --- | --- |
| `RIGHTS_LEDGER_PATH` | Absolute path to a **dedicated** SQLite rights ledger — never a product database. Unset, missing, or carrying a foreign schema means no subject holds any provisioned right. |
| `RIGHTS_ACTIVE_BUILD_DIGESTS_JSON` | Operator-supplied active builds keyed as `"<kind>:<subject>"`. Missing, malformed, or mismatched data means no provisioned right is exposed. |

`Settings` reads the ledger once at construction into
`provisioned_provider_rights` and `provisioned_project_rights`. Reading there
rather than inside a readiness method keeps `/health` free of database work and
keeps each readiness map a pure function of the settings object.

That read is read-only in the strict sense: `Settings` opens the ledger through
a `mode=ro` connection with `query_only` pinned, and creates no file, no
directory, and no table. Constructing `Settings` cannot modify anything on disk.

Those maps are construction-time snapshots, not live authorization answers. An
already-constructed `Settings` never changes, and `main.py` keeps exactly one of
them for the life of the process, so nothing that decides anything may read
them. Two lookups exist for that instead:

| Lookup | Freshness | Use it when |
| --- | --- | --- |
| `RightsLedger.held_rights_for_build(kind, subject, active_digest)` | Exact, read at the call | A privileged operation is about to act. The answer is bound to the digest of the build actually running. |
| `config.current_provisioned_rights(kind)` | At most `RIGHTS_SNAPSHOT_TTL_SECONDS` (5s) stale | Something needs a current-enough view and is not itself about to act on it. |
| `Settings.provisioned_*_rights` | Frozen at construction | Display and readiness only. Never authorization. |

`current_provisioned_rights` is what keeps "automatic" and "revocable" true in a
long-lived process. A grant recorded after boot becomes visible within the TTL
without a restart, and — the direction that matters — a revoked right stops
being reported within the same bound. Changing either environment variable takes
effect at the next call rather than after the TTL, because the cached answer
records the configuration it was derived from. `invalidate_provisioned_rights_cache()`
makes an in-process change visible at once.

The refresh is bounded in both directions. Staleness is bounded by the TTL;
work is bounded by performing the read while holding the cache lock, so callers
that arrive together share one read instead of opening the database once each. A
deployment that has not opted in — no `RIGHTS_LEDGER_PATH`, or no active build
configured — performs no read at all and stores no cache entry, so none of this
machinery runs until an operator opts in. `/health` is unaffected: readiness maps
read only the frozen snapshot and perform no database work.

Expected ledger availability and operator-data failures return no rights. A
programming defect is allowed to surface rather than being hidden behind an
empty, plausible-looking result.

## Operating the ledger

```python
from director_orchestrator.rights_provisioning import (
    InstallVerification, RightsLedger, SubjectKind, build_digest_for,
)

ledger = RightsLedger("/data/rights.sqlite3")
outcome = ledger.provision(
    InstallVerification(
        subject_kind=SubjectKind.PROVIDER,
        subject_id="antigravity",
        declared_rights=frozenset({"review_verdict"}),
        build_digest=build_digest_for(artifact_chunks),
        verified_at=int(time.time()),
    )
)
if outcome.notice is not None:
    print(outcome.notice.summary())
```

Revoking, and confirming that a rebuild does not undo it:

```python
ledger.revoke_right(
    SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
)
ledger.provision(same_verification).withheld_revoked   # ("review_verdict",)
```

`restore_right` puts the row back as it was stored, build binding included: the
right returns bound to the digest it was originally granted against. If the
subject has rebuilt since, that binding is stale, and `held_rights_for_build`
keeps reporting nothing until the subject is reprovisioned against the current
build or the active-build configuration is realigned with the stored digest.
Restoring a right deliberately says nothing about which build is live.

`holds()` and `held_rights()` are introspection only — they report live grant
rows for any build, which is the right answer for showing an owner what the
ledger contains and the wrong answer for deciding whether anything may proceed.
`held_rights_for_build()` is the only build-bound read.

Outstanding notices: `ledger.pending_notices()`, cleared with
`acknowledge_notice(notice_id)`.

## Relationship to the agent operating rules

`AGENTS.md` rule 4 states that model output may add approval requirements but
may not remove them. This mechanism is built to add one, and has not yet added
it.

Verdict authority is governed today exactly as it was before this branch: by
`gemini_allow_review_verdict` alone, which cannot produce a ready lane at all —
setting it makes the lane unready, and no configuration reaches verdict
authority. No readiness expression consults a provisioned grant, because the
hunk that would introduce that term is deliberately unapplied. It is written out
in full, with its rationale, in `rights-provisioning-pending-hunk.md`.

If the reviewer with merge authority applies that hunk, the flag *would become*
necessary but not sufficient: a provisioned grant recorded against a verified
build, matching the independently configured active build, *would* also be
required, and the owner *would be* notified when one appeared. Until that
happens, nothing about verdict authority has changed.

Rule 10 reserves expanded access to an owner decision. The ledger's owner notice
does not itself satisfy that gate or activate a runtime capability. Applying a
readiness change, widening admitted assignment kinds, or consuming a right at a
privileged boundary remains a separate reviewed owner decision.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:12 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | END]] -->
