<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]] -->
# Rights provisioning on verified install

**Status:** Mechanism only. Default off. No subject holds a provisioned right
until `RIGHTS_LEDGER_PATH` names a readable ledger and a verified install has
been recorded into it. A deployment that has not opted in behaves exactly as it
did before this document existed.

**Schema authority:** `src/director_orchestrator/rights_provisioning.py`.

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
   artifact it was granted for — a different build produces a different digest
   and a fresh grant.

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

In particular, holding `review_verdict` makes a lane *eligible* for verdict
authority. It does not by itself widen which assignment kinds that lane admits;
`CAPABILITY_BY_KIND` in `antigravity.py` remains the separate, stricter table
governing admitted work.

## Configuration

| Variable | Meaning |
| --- | --- |
| `RIGHTS_LEDGER_PATH` | Absolute path to the SQLite ledger. Unset means no subject holds any provisioned right. |

`Settings` reads the ledger once at construction into
`provisioned_provider_rights` and `provisioned_project_rights`. Reading there
rather than inside a readiness method keeps `/health` free of database work and
keeps each readiness map a pure function of the settings object.

Every failure path returns no rights. A ledger that is missing, corrupt or
unreadable must not be indistinguishable from one that granted something.

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

Outstanding notices: `ledger.pending_notices()`, cleared with
`acknowledge_notice(notice_id)`.

## Relationship to the agent operating rules

`AGENTS.md` rule 4 states that model output may add approval requirements but
may not remove them. This mechanism adds one. Before it, a lane's verdict
authority was governed by a single settings flag. After it, the flag is
necessary but no longer sufficient: a provisioned grant recorded against a
verified build is also required, and the owner is notified when one appears.

Rule 10 reserves expanded access to an owner decision. The owner notice is that
decision point, raised after the grant rather than before it, with revocation as
the acting mechanism. That ordering was the owner's explicit choice.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]] -->
