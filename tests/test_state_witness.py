# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
"""No receipt, no state: the Witness Ledger.

The one test the standard names: a packet claiming ``merged = true`` for an
open pull request is refused, and the reply carries the real merge state. The
rest pin the four rules the module enforces.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from director_orchestrator.state_witness import (
    Claim,
    ClaimKind,
    StateWitnessLedger,
    WitnessRefused,
    WitnessStatus,
    doc_read,
    gh_checks_green,
    gh_pr_merged,
    owner_authorization,
    require,
    stamp_unwitnessed,
    verify_packet,
)

SHA = "a" * 40
REPO = "edwardjones3976/director-orchestrator"


def _open_pr_api(args):
    """A fake ``gh api`` for an OPEN, unmerged pull request at SHA."""

    path = args[1]
    if path.endswith("/pulls/131"):
        return {"merged": False, "state": "open", "head": {"sha": SHA}, "merge_commit_sha": None}
    raise KeyError(path)


class WitnessLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = StateWitnessLedger(str(Path(self.temporary.name) / "witness.sqlite3"))

    # --- the named test ------------------------------------------------------

    def test_a_packet_claiming_merged_for_an_open_pr_is_refused_with_the_real_state(self):
        claims = [Claim(ClaimKind.GH_PR_MERGED, f"{REPO}#131@{SHA}", True)]
        oracles = {
            ClaimKind.GH_PR_MERGED: lambda claim: gh_pr_merged(
                self.ledger, REPO, 131, SHA, agent="claude", claimed=True, gh_json=_open_pr_api
            )
        }

        refusals = verify_packet(self.ledger, claims, agent="claude", oracles=oracles)

        self.assertEqual(len(refusals), 1)
        refusal = refusals[0]
        self.assertIs(refusal.status, WitnessStatus.MISMATCH)
        self.assertIs(refusal.claimed, True)
        self.assertEqual(refusal.observed["merged"], False)
        self.assertEqual(refusal.observed["state"], "open")
        # The reply the agent gets carries the pair, not a score.
        feedback = refusal.feedback()["witness_refusal"]
        self.assertEqual(feedback["claimed"], True)
        self.assertEqual(feedback["observed"]["merged"], False)
        # And the refusal is itself a row, so the strike is on the record.
        self.assertEqual(self.ledger.strikes("claude", ClaimKind.GH_PR_MERGED), 1)

    # --- only oracles write rows; rows are append-only -----------------------

    def test_rows_cannot_be_updated_or_deleted_even_from_outside(self):
        row = self.ledger.record(
            ClaimKind.DOC_READ, "docs/x.md", agent="a", claimed={}, observed={},
            status=WitnessStatus.MATCH, oracle="test",
        )
        connection = sqlite3.connect(self.ledger.path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE state_witness SET status = 'MATCH' WHERE witness_id = ?", (row.witness_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM state_witness WHERE witness_id = ?", (row.witness_id,))
        connection.close()

    def test_a_probe_that_raises_is_unobservable_never_a_match(self):
        def broken(args):
            raise OSError("gh: timeout")

        row = gh_pr_merged(self.ledger, REPO, 131, SHA, agent="a", gh_json=broken)
        self.assertIs(row.status, WitnessStatus.UNOBSERVABLE)
        self.assertEqual(row.observed, {"error": "OSError"})
        with self.assertRaises(WitnessRefused):
            require(self.ledger, ClaimKind.GH_PR_MERGED, f"{REPO}#131@{SHA}")

    # --- unobservable means deny ---------------------------------------------

    def test_no_row_no_oracle_and_stale_rows_all_deny(self):
        self.assertIs(self.ledger.witnessed(ClaimKind.PROVIDER_REACHABLE, "grok"), WitnessStatus.UNOBSERVABLE)
        refusals = verify_packet(
            self.ledger, [Claim(ClaimKind.PROVIDER_REACHABLE, "grok", False)], agent="a", oracles={}
        )
        self.assertIs(refusals[0].status, WitnessStatus.UNOBSERVABLE)

        then = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self.ledger.record(
            ClaimKind.DOC_READ, "docs/x.md", agent="a", claimed={}, observed={},
            status=WitnessStatus.MATCH, oracle="test", now=then,
        )
        self.assertIs(self.ledger.witnessed(ClaimKind.DOC_READ, "docs/x.md", now=then), WitnessStatus.MATCH)
        self.assertIs(
            self.ledger.witnessed(ClaimKind.DOC_READ, "docs/x.md", now=then + timedelta(hours=1)),
            WitnessStatus.UNOBSERVABLE,
        )

    # --- the four wrappers -----------------------------------------------------

    def test_checks_green_requires_success_and_at_least_one_step(self):
        def api(args):
            path = args[1]
            if "/actions/runs?" in path:
                return {"workflow_runs": [{"id": 7}]}
            if path.endswith("/runs/7/jobs?per_page=50"):
                return {"jobs": [
                    {"name": "ci", "conclusion": "success", "steps": [{"name": "run"}]},
                    {"name": "security", "conclusion": "success", "steps": []},  # billing stub
                ]}
            raise KeyError(path)

        row = gh_checks_green(self.ledger, REPO, SHA, agent="a", required=("ci", "security"), gh_json=api)
        self.assertIs(row.status, WitnessStatus.MISMATCH)
        self.assertEqual(row.observed["jobs"]["security"]["steps"], 0)

    def test_owner_authorization_matches_only_the_owners_own_message(self):
        digest = "sha256:" + "1" * 64
        row = owner_authorization(
            self.ledger, "C1", "1.0", agent="a", owner_user_id="U_OWNER",
            authorization_digest=digest,
            fetch_message=lambda c, t: {"user": "U_IMPOSTOR", "text": "approve it"},
        )
        self.assertIs(row.status, WitnessStatus.MISMATCH)
        with self.assertRaises(WitnessRefused):
            require(self.ledger, ClaimKind.OWNER_AUTHORIZATION, digest)
        owner_authorization(
            self.ledger, "C1", "1.0", agent="a", owner_user_id="U_OWNER",
            authorization_digest=digest,
            fetch_message=lambda c, t: {"user": "U_OWNER", "text": "approve it"},
        )
        require(self.ledger, ClaimKind.OWNER_AUTHORIZATION, digest)  # no raise

    def test_doc_read_binds_the_bytes_actually_read(self):
        path = Path(self.temporary.name) / "doc.md"
        path.write_bytes(b"the document says eight lanes")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertIs(doc_read(self.ledger, str(path), digest, agent="a").status, WitnessStatus.MATCH)
        self.assertIs(doc_read(self.ledger, str(path), "0" * 64, agent="a").status, WitnessStatus.MISMATCH)
        self.assertIs(doc_read(self.ledger, str(path) + ".missing", digest, agent="a").status, WitnessStatus.UNOBSERVABLE)

    # --- strikes clear only through effect-gating matches ----------------------

    def test_strikes_clear_only_on_a_gate_match_not_cheap_trivia(self):
        kind, subject = ClaimKind.GH_PR_MERGED, "s"
        for _ in range(2):
            self.ledger.record(kind, subject, agent="a", claimed=True, observed={}, status=WitnessStatus.MISMATCH, oracle="gh")
        self.ledger.record(kind, subject, agent="a", claimed=True, observed={}, status=WitnessStatus.MATCH, oracle="gh")
        self.assertEqual(self.ledger.strikes("a", kind), 2)
        self.ledger.record(kind, subject, agent="a", claimed=True, observed={}, status=WitnessStatus.MATCH, oracle="oracle_merge#gate")
        self.assertEqual(self.ledger.strikes("a", kind), 0)

    # --- the outbound stamp ----------------------------------------------------

    def test_outbound_state_claims_without_a_witness_are_stamped(self):
        text = "PR #131 is merged. CI is green (witness:42). We shipped the docs."
        stamped = stamp_unwitnessed(text)
        self.assertIn("UNWITNESSED: PR #131 is merged.", stamped)
        self.assertIn(" CI is green (witness:42).", stamped)
        self.assertNotIn("UNWITNESSED: CI", stamped)
        self.assertTrue(stamped.endswith(" We shipped the docs."))


class WitnessGateWiringTests(unittest.TestCase):
    """The two gates the first version wires: the exact-SHA merge and the
    durable authorization write. Each consumes rows and refuses on anything
    but MATCH."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = StateWitnessLedger(str(self.root / "witness.sqlite3"))

    def test_merge_gate_writes_rows_from_the_final_snapshot_and_merges_on_match(self):
        from tests.test_oracle_merge import FakeGitHub, OracleMergeGateTests
        from director_orchestrator.oracle_merge import (
            OracleMergePolicy, OracleMergeService, OracleMergeStatus, OracleMergeStore,
        )

        fixtures = OracleMergeGateTests("setUp")
        fixtures.setUp()
        self.addCleanup(fixtures.temporary.cleanup)
        service = OracleMergeService(
            OracleMergePolicy(fixtures.settings), OracleMergeStore(fixtures.database),
            FakeGitHub(fixtures.snapshot()), state_witness=self.ledger,
        )
        result = service.process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        head = fixtures.artifact().head_sha
        self.assertIs(
            self.ledger.witnessed(ClaimKind.GH_PR_MERGED, f"edwardjones3976/edge_engine#101@{head}"),
            WitnessStatus.MATCH,
        )
        self.assertIs(
            self.ledger.witnessed(ClaimKind.GH_CHECKS_GREEN, f"edwardjones3976/edge_engine@{head}"),
            WitnessStatus.MATCH,
        )

    def test_merge_gate_never_reaches_an_already_merged_head(self):
        from tests.test_oracle_merge import FakeGitHub, OracleMergeGateTests
        from director_orchestrator.oracle_merge import (
            OracleMergePolicy, OracleMergeService, OracleMergeStatus, OracleMergeStore,
        )

        fixtures = OracleMergeGateTests("setUp")
        fixtures.setUp()
        self.addCleanup(fixtures.temporary.cleanup)
        github = FakeGitHub(fixtures.snapshot(merged=True))
        service = OracleMergeService(
            OracleMergePolicy(fixtures.settings), OracleMergeStore(fixtures.database),
            github, state_witness=self.ledger,
        )
        result = service.process(fixtures.artifact())
        # Policy refuses first ("not open and unmerged"); the witness gate is
        # the layer behind it and never sees a snapshot policy already rejected.
        self.assertNotEqual(result.status, OracleMergeStatus.MERGED)
        self.assertEqual(github.merge_calls, 0)
        self.assertIs(
            self.ledger.witnessed(ClaimKind.GH_PR_MERGED, "edwardjones3976/edge_engine#101@" + "a" * 40),
            WitnessStatus.UNOBSERVABLE,
        )

    def test_authorization_write_consumes_a_fresh_owner_witness_or_refuses(self):
        from tests.test_productive_work import ExternalOpenAIProductiveWorkTests
        from director_orchestrator.store import TaskStore

        fixtures = ExternalOpenAIProductiveWorkTests("setUp")
        fixtures.setUp()
        self.addCleanup(fixtures.tearDown)
        store = TaskStore(str(self.root / "tasks.sqlite3"), state_witness=self.ledger)
        store.create(fixtures.task)
        authorization = fixtures.authorization()

        with self.assertRaises(WitnessRefused) as refused:
            store.record_external_openai_production_authorization(authorization)
        self.assertIs(refused.exception.refusal.status, WitnessStatus.UNOBSERVABLE)

        owner_authorization(
            self.ledger, "C1", "1.0", agent="director", owner_user_id="U_OWNER",
            authorization_digest=authorization.owner_authorization_digest,
            fetch_message=lambda c, t: {"user": "U_OWNER", "text": "authorize"},
        )
        self.assertTrue(store.record_external_openai_production_authorization(authorization))


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
