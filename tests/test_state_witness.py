# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
"""No receipt, no state: the Witness Ledger.

The one test the standard names: a packet claiming ``merged = true`` for an
open pull request is refused, and the reply carries the real merge state. The
rest pin the four rules the module enforces and the reviewer's notes at b44fda2.
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
    WitnessLedgerFull,
    WitnessRefused,
    WitnessStatus,
    doc_read,
    gh_checks_green,
    no_oracle,
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


def _jobs_api(steps: int, names=("ci", "security")):
    """A fake ``gh api`` whose jobs at any SHA succeeded with ``steps`` steps."""

    def api(args):
        path = args[1]
        if "/actions/runs?" in path:
            return {"workflow_runs": [{"id": 7}]}
        if path.endswith("/runs/7/jobs?per_page=50"):
            return {"jobs": [
                {"name": name, "conclusion": "success", "steps": [{"name": "s"}] * steps}
                for name in names
            ]}
        raise KeyError(path)

    return api


class WitnessLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = StateWitnessLedger(str(Path(self.temporary.name) / "witness.sqlite3"))

    def _synthetic(self, kind, subject, status, *, agent="a", oracle="test", now=None):
        return self.ledger._record(
            kind, subject, agent=agent, claimed={}, observed={}, status=status, oracle=oracle, now=now
        )

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
        row = self._synthetic(ClaimKind.DOC_READ, "docs/x.md", WitnessStatus.MATCH)
        connection = sqlite3.connect(self.ledger.path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE state_witness SET status = 'MATCH' WHERE witness_id = ?", (row.witness_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM state_witness WHERE witness_id = ?", (row.witness_id,))
        connection.close()

    def test_any_exception_inside_a_probe_is_unobservable_never_a_match(self):
        def broken(args):
            raise RuntimeError("gh: timeout")  # not on any curated list

        row = gh_pr_merged(self.ledger, REPO, 131, SHA, agent="a", gh_json=broken)
        self.assertIs(row.status, WitnessStatus.UNOBSERVABLE)
        self.assertEqual(row.observed, {"error": "RuntimeError"})
        with self.assertRaises(WitnessRefused):
            require(self.ledger, ClaimKind.GH_PR_MERGED, f"{REPO}#131@{SHA}")

    # --- unobservable means deny ---------------------------------------------

    def test_no_row_no_oracle_and_stale_rows_all_deny(self):
        self.assertIs(self.ledger.witnessed(ClaimKind.PROVIDER_REACHABLE, "grok"), WitnessStatus.UNOBSERVABLE)
        refusals = verify_packet(
            self.ledger, [Claim(ClaimKind.PROVIDER_REACHABLE, "grok", False)], agent="a", oracles={}
        )
        self.assertIs(refusals[0].status, WitnessStatus.UNOBSERVABLE)
        self.assertEqual(refusals[0].observed, {"error": "LookupError"})
        row = self.ledger.latest(ClaimKind.PROVIDER_REACHABLE, "grok")
        self.assertEqual(row.oracle, "none#gate")  # written by the no_oracle wrapper
        self.assertIs(no_oracle(self.ledger, Claim(ClaimKind.PROVIDER_REACHABLE, "x", True), agent="a").status, WitnessStatus.UNOBSERVABLE)

        then = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self._synthetic(ClaimKind.DOC_READ, "docs/x.md", WitnessStatus.MATCH, now=then)
        self.assertIs(self.ledger.witnessed(ClaimKind.DOC_READ, "docs/x.md", now=then), WitnessStatus.MATCH)
        self.assertIs(
            self.ledger.witnessed(ClaimKind.DOC_READ, "docs/x.md", now=then + timedelta(hours=1)),
            WitnessStatus.UNOBSERVABLE,
        )

    def test_an_unknown_persisted_class_or_status_is_not_evidence(self):
        row = self._synthetic(ClaimKind.DOC_READ, "docs/x.md", WitnessStatus.MATCH)
        connection = sqlite3.connect(self.ledger.path)
        for kind, subject, status in (("future_kind", "docs/x.md", "MATCH"), ("doc_read", "docs/y.md", "PROBABLY")):
            connection.execute(
                "INSERT INTO state_witness (kind, subject, agent, claimed_json, observed_json,"
                " status, oracle, observed_at) VALUES (?, ?, 'a', '{}', '{}', ?, 'test', ?)",
                (kind, subject, status, row.observed_at.isoformat()),
            )
        connection.commit()
        connection.close()
        self.assertIs(self.ledger.witnessed(ClaimKind.DOC_READ, "docs/y.md"), WitnessStatus.UNOBSERVABLE)
        self.assertIs(self.ledger.witnessed(ClaimKind.DOC_READ, "docs/x.md"), WitnessStatus.MATCH)

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
        missing = gh_checks_green(
            self.ledger, REPO, SHA, agent="a", required=("ci", "security"), gh_json=_jobs_api(2, names=("ci",))
        )
        self.assertIs(missing.status, WitnessStatus.MISMATCH)

    def test_owner_authorization_matches_only_the_owners_own_message(self):
        digest = "1" * 64
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

    def test_malformed_gh_inputs_are_refused_before_any_endpoint_is_built(self):
        calls = []

        def api(args):
            calls.append(args)
            return {}

        for repo, pull in (("owner/name/extra", 1), ("owner name", 1), ("../x", 1), ("o/n", 0), ("o/n", True)):
            with self.assertRaises((ValueError, TypeError)):
                gh_pr_merged(self.ledger, repo, pull, SHA, agent="a", gh_json=api)
        with self.assertRaises(ValueError):
            gh_checks_green(self.ledger, "o n", SHA, agent="a", gh_json=api)
        self.assertEqual(calls, [])

    # --- strikes clear only through effect-gating matches ----------------------

    def test_strikes_clear_only_on_a_gate_match_not_cheap_trivia(self):
        kind, subject = ClaimKind.GH_PR_MERGED, "s"
        for _ in range(2):
            self._synthetic(kind, subject, WitnessStatus.MISMATCH, oracle="gh")
        self._synthetic(kind, subject, WitnessStatus.MATCH, oracle="gh")
        self.assertEqual(self.ledger.strikes("a", kind), 2)
        self._synthetic(kind, subject, WitnessStatus.MATCH, oracle="oracle_merge#gate")
        self.assertEqual(self.ledger.strikes("a", kind), 0)

    # --- bounded growth --------------------------------------------------------

    def test_the_ledger_is_bounded_and_rotates_without_deleting(self):
        small = StateWitnessLedger(str(Path(self.temporary.name) / "small.sqlite3"), max_rows=2)
        for _ in range(2):
            small._record(ClaimKind.DOC_READ, "d", agent="a", claimed={}, observed={}, status=WitnessStatus.MATCH, oracle="t")
        with self.assertRaises(WitnessLedgerFull):
            small._record(ClaimKind.DOC_READ, "d", agent="a", claimed={}, observed={}, status=WitnessStatus.MATCH, oracle="t")
        archived = small.rotate(now=datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertTrue(Path(archived).exists())
        kept = sqlite3.connect(archived)
        self.assertEqual(kept.execute("SELECT COUNT(*) FROM state_witness").fetchone()[0], 2)
        kept.close()
        self.assertIs(small.witnessed(ClaimKind.DOC_READ, "d"), WitnessStatus.UNOBSERVABLE)
        small._record(ClaimKind.DOC_READ, "d", agent="a", claimed={}, observed={}, status=WitnessStatus.MATCH, oracle="t")
        # A second rotation in the same second keeps both archives, and a name
        # another process reserved first is never taken.
        stamp = "20260912T000000Z"
        foreign = Path(f"{small.path}.{stamp}.1")
        foreign.write_bytes(b"someone else's archive")
        again = small.rotate(now=datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertEqual(again, f"{small.path}.{stamp}.2")
        self.assertEqual(foreign.read_bytes(), b"someone else's archive")
        self.assertTrue(Path(archived).exists() and Path(again).exists())
        kept = sqlite3.connect(archived)
        self.assertEqual(kept.execute("SELECT COUNT(*) FROM state_witness").fetchone()[0], 2)
        kept.close()

    # --- the outbound stamp ----------------------------------------------------

    def test_outbound_state_claims_without_a_witness_are_stamped(self):
        text = "PR #131 is merged. CI is green (witness:42). We shipped the docs."
        stamped = stamp_unwitnessed(text)
        self.assertIn("UNWITNESSED: PR #131 is merged.", stamped)
        self.assertIn(" CI is green (witness:42).", stamped)
        self.assertNotIn("UNWITNESSED: CI", stamped)
        self.assertTrue(stamped.endswith(" We shipped the docs."))

    def test_the_stamp_leaves_slack_markup_intact(self):
        text = "*Status*: PR <https://github.com/x/y/pull/1|#1> is merged.\n`ci` is green (witness:3)."
        self.assertEqual(
            stamp_unwitnessed(text),
            "UNWITNESSED: *Status*: PR <https://github.com/x/y/pull/1|#1> is merged.\n`ci` is green (witness:3).",
        )


class WitnessGateWiringTests(unittest.TestCase):
    """The two gates the first version wires: the exact-SHA merge and the
    durable authorization write. Each consumes rows and refuses on anything
    but MATCH, and each is a no-op without a ledger."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = StateWitnessLedger(str(self.root / "witness.sqlite3"))

    def _merge_fixture(self):
        from tests.test_oracle_merge import FakeGitHub, OracleMergeGateTests

        fixtures = OracleMergeGateTests("setUp")
        fixtures.setUp()
        self.addCleanup(fixtures.temporary.cleanup)
        return fixtures, FakeGitHub

    def _service(self, fixtures, github, **kw):
        # [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
        # Issue #306 removed the legacy no-boundary merge, so the wiring goes
        # through the phase 3 harness: a pinned, Ed25519-signed standing
        # boundary whose merge transport is this fixture's gateway.
        return fixtures.service(github, **kw)
        # [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]

    def _checks_oracle(self, api):
        return lambda repo, sha: gh_checks_green(
            self.ledger, repo, sha, agent="claude", required=("ci", "security"), gh_json=api
        )

    def test_merge_gate_consumes_real_step_evidence_and_merges_on_match(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus

        fixtures, FakeGitHub = self._merge_fixture()
        result = self._service(
            fixtures, FakeGitHub(fixtures.snapshot()),
            state_witness=self.ledger, checks_green_oracle=self._checks_oracle(_jobs_api(3)),
        ).process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.MERGED)
        head = fixtures.artifact().head_sha
        self.assertIs(self.ledger.witnessed(ClaimKind.GH_PR_MERGED, f"edwardjones3976/edge_engine#101@{head}"), WitnessStatus.MATCH)
        self.assertIs(self.ledger.witnessed(ClaimKind.GH_CHECKS_GREEN, f"edwardjones3976/edge_engine@{head}"), WitnessStatus.MATCH)

    def test_merge_gate_refuses_zero_step_jobs_even_when_the_snapshot_says_success(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus

        fixtures, FakeGitHub = self._merge_fixture()
        github = FakeGitHub(fixtures.snapshot())
        result = self._service(
            fixtures, github, state_witness=self.ledger, checks_green_oracle=self._checks_oracle(_jobs_api(0)),
        ).process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertEqual(github.merge_calls, 0)
        self.assertIn("witness refused gh_checks_green", result.reasons[0])
        self.assertIn('"steps": 0', result.reasons[0])
        self.assertIn("witness:", result.reasons[0])

    def test_merge_gate_refuses_a_forged_or_misrouted_checks_row(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus
        from director_orchestrator.state_witness import WitnessRow

        fixtures, FakeGitHub = self._merge_fixture()
        head = fixtures.artifact().head_sha
        forged = lambda repo, sha: WitnessRow(
            999, ClaimKind.GH_CHECKS_GREEN, f"{repo}@{sha}", "x", True, {}, WitnessStatus.MATCH,
            "forged", datetime.now(timezone.utc),
        )  # never persisted
        wrong_kind = lambda repo, sha: self.ledger._record(
            ClaimKind.DOC_READ, f"{repo}@{sha}", agent="x", claimed=True, observed={},
            status=WitnessStatus.MATCH, oracle="t",
        )
        other_sha = lambda repo, sha: gh_checks_green(
            self.ledger, repo, "b" * 40, agent="x", required=("ci", "security"), gh_json=_jobs_api(3)
        )
        def reused_id(repo, sha):
            # Persist a real MISMATCH (zero steps), then hand back a row that
            # reuses its id but claims MATCH with invented observations.
            real = gh_checks_green(
                self.ledger, repo, sha, agent="x", required=("ci", "security"), gh_json=_jobs_api(0)
            )
            return WitnessRow(
                real.witness_id, real.kind, real.subject, real.agent, real.claimed,
                {"jobs": {"ci": {"steps": 9}}}, WitnessStatus.MATCH, real.oracle, real.observed_at,
            )

        for oracle in (forged, wrong_kind, other_sha, reused_id):
            # A fresh merge store per case: the store de-duplicates artifact
            # deliveries, so a reused one would replay the first verdict.
            fixtures, FakeGitHub = self._merge_fixture()
            github = FakeGitHub(fixtures.snapshot())
            result = self._service(
                fixtures, github, state_witness=self.ledger, checks_green_oracle=oracle,
            ).process(fixtures.artifact())
            self.assertEqual(result.status, OracleMergeStatus.REJECTED)
            self.assertEqual(github.merge_calls, 0)
            self.assertIn("not the persisted observation", result.reasons[0])
        self.assertIs(
            self.ledger.witnessed(ClaimKind.GH_CHECKS_GREEN, f"edwardjones3976/edge_engine@{head}"),
            WitnessStatus.MISMATCH,  # the only persisted row is the real zero-step one
        )

    def test_merge_gate_denies_when_no_checks_oracle_can_observe_steps(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus

        fixtures, FakeGitHub = self._merge_fixture()
        github = FakeGitHub(fixtures.snapshot())
        result = self._service(fixtures, github, state_witness=self.ledger).process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertEqual(github.merge_calls, 0)
        self.assertIn("UNOBSERVABLE", result.reasons[0])

    def test_merge_gate_denies_when_the_checks_probe_raises(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus

        fixtures, FakeGitHub = self._merge_fixture()
        github = FakeGitHub(fixtures.snapshot())

        def boom(args):
            raise RuntimeError("gh exploded")

        result = self._service(
            fixtures, github, state_witness=self.ledger, checks_green_oracle=self._checks_oracle(boom),
        ).process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertEqual(github.merge_calls, 0)
        self.assertIn("UNOBSERVABLE", result.reasons[0])

    def test_merge_gate_is_a_no_op_without_a_ledger(self):
        from director_orchestrator.oracle_merge import OracleMergeStatus

        fixtures, FakeGitHub = self._merge_fixture()
        result = self._service(fixtures, FakeGitHub(fixtures.snapshot())).process(fixtures.artifact())
        self.assertEqual(result.status, OracleMergeStatus.MERGED)

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

        # A row for a different digest is not this authorization.
        owner_authorization(
            self.ledger, "C1", "1.0", agent="director", owner_user_id="U_OWNER",
            authorization_digest="f" * 64,
            fetch_message=lambda c, t: {"user": "U_OWNER", "text": "authorize"},
        )
        with self.assertRaises(WitnessRefused):
            store.record_external_openai_production_authorization(authorization)
        # Neither is a stale one.
        self.ledger._record(
            ClaimKind.OWNER_AUTHORIZATION, authorization.owner_authorization_digest, agent="director",
            claimed={}, observed={}, status=WitnessStatus.MATCH, oracle="slack",
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        with self.assertRaises(WitnessRefused):
            store.record_external_openai_production_authorization(authorization)

        owner_authorization(
            self.ledger, "C1", "1.0", agent="director", owner_user_id="U_OWNER",
            authorization_digest=authorization.owner_authorization_digest,
            fetch_message=lambda c, t: {"user": "U_OWNER", "text": "authorize"},
        )
        self.assertTrue(store.record_external_openai_production_authorization(authorization))

    def test_authorization_write_is_a_no_op_without_a_ledger(self):
        from tests.test_productive_work import ExternalOpenAIProductiveWorkTests

        fixtures = ExternalOpenAIProductiveWorkTests("setUp")
        fixtures.setUp()
        self.addCleanup(fixtures.tearDown)
        self.assertTrue(fixtures.store.record_external_openai_production_authorization(fixtures.authorization()))


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
