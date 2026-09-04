# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]]
"""Contract tests for automatic rights provisioning.

The owner asked for rights to be assigned immediately once a subject installs
and builds, with the owner notified rather than asked first. These tests pin the
properties that make that safe to have built at all: a right nobody declared
cannot appear, a grant cannot reach the ledger without a notice behind it, and a
revocation is not undone by rebuilding.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.rights_provisioning import (
    AUTHORITY_BEARING_RIGHTS,
    InstallVerification,
    RightsLedger,
    RightsProvisioningError,
    RIGHTS_VOCABULARY,
    SubjectKind,
    build_digest_for,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def verification(
    *,
    kind: SubjectKind = SubjectKind.PROVIDER,
    subject_id: str = "antigravity",
    rights: frozenset[str] = frozenset({"review_verdict"}),
    digest: str = DIGEST_A,
    verified_at: int = 1_760_000_000,
) -> InstallVerification:
    return InstallVerification(
        subject_kind=kind,
        subject_id=subject_id,
        declared_rights=rights,
        build_digest=digest,
        verified_at=verified_at,
    )


class InstallVerificationTests(unittest.TestCase):
    def test_a_right_outside_the_vocabulary_is_refused_whole(self) -> None:
        # Refused entirely rather than filtered down to the known subset: a
        # partially honoured declaration would grant something the caller did
        # not ask for in the shape it asked for it.
        with self.assertRaisesRegex(RightsProvisioningError, "closed vocabulary"):
            verification(rights=frozenset({"review_verdict", "become_owner"}))

    def test_project_and_provider_vocabularies_do_not_bleed(self) -> None:
        with self.assertRaisesRegex(RightsProvisioningError, "closed vocabulary"):
            verification(kind=SubjectKind.PROJECT, rights=frozenset({"tools"}))
        with self.assertRaisesRegex(RightsProvisioningError, "closed vocabulary"):
            verification(rights=frozenset({"intake"}))

    def test_evidence_must_be_exact(self) -> None:
        with self.assertRaisesRegex(RightsProvisioningError, "exact sha256"):
            verification(digest="not-a-digest")
        with self.assertRaisesRegex(RightsProvisioningError, "exact sha256"):
            verification(digest="A" * 64)
        with self.assertRaisesRegex(RightsProvisioningError, "safe identifier"):
            verification(subject_id="../escape")
        with self.assertRaisesRegex(RightsProvisioningError, "timestamp"):
            verification(verified_at=0)

    def test_every_authority_bearing_right_is_in_some_vocabulary(self) -> None:
        every = set().union(*RIGHTS_VOCABULARY.values())
        self.assertTrue(AUTHORITY_BEARING_RIGHTS <= every)


class RightsLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = RightsLedger(Path(self.temp.name) / "rights.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unknown_subject_holds_nothing(self) -> None:
        self.assertFalse(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )
        self.assertEqual(
            self.ledger.held_rights(SubjectKind.PROVIDER, "antigravity"), frozenset()
        )

    def test_verified_install_grants_immediately(self) -> None:
        outcome = self.ledger.provision(verification())
        self.assertEqual(outcome.granted, ("review_verdict",))
        self.assertTrue(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )

    def test_a_grant_always_carries_an_owner_notice(self) -> None:
        outcome = self.ledger.provision(verification())
        self.assertIsNotNone(outcome.notice)
        assert outcome.notice is not None
        self.assertEqual(outcome.notice.rights, ("review_verdict",))
        # review_verdict is authority over another contributor's work, so the
        # notice must say so rather than burying it among ordinary rights.
        self.assertEqual(outcome.notice.authority_bearing, ("review_verdict",))
        pending = self.ledger.pending_notices()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].notice_id, outcome.notice.notice_id)
        self.assertIn("antigravity", pending[0].summary())
        self.assertIn("Revoke", pending[0].summary())

    def test_notice_is_acknowledged_once(self) -> None:
        outcome = self.ledger.provision(verification())
        assert outcome.notice is not None
        self.assertTrue(self.ledger.acknowledge_notice(outcome.notice.notice_id))
        self.assertFalse(self.ledger.acknowledge_notice(outcome.notice.notice_id))
        self.assertEqual(self.ledger.pending_notices(), ())

    def test_reprovisioning_the_same_build_does_not_renotify(self) -> None:
        self.ledger.provision(verification())
        again = self.ledger.provision(verification())
        self.assertEqual(again.granted, ())
        self.assertEqual(again.already_held, ("review_verdict",))
        self.assertIsNone(again.notice)
        # One build, one notice: otherwise a restart loop would train the owner
        # to ignore the notice that matters.
        self.assertEqual(len(self.ledger.pending_notices()), 1)

    def test_a_new_build_rebinds_the_grant_and_renotifies(self) -> None:
        self.ledger.provision(verification(digest=DIGEST_A))
        rebuilt = self.ledger.provision(verification(digest=DIGEST_B))
        self.assertEqual(rebuilt.granted, ("review_verdict",))
        assert rebuilt.notice is not None
        self.assertEqual(rebuilt.notice.build_digest, DIGEST_B)

    def test_revocation_outranks_reinstallation(self) -> None:
        self.ledger.provision(verification())
        self.assertTrue(
            self.ledger.revoke_right(
                SubjectKind.PROVIDER,
                "antigravity",
                "review_verdict",
                revoked_by="owner",
            )
        )
        self.assertFalse(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )
        # The whole point of the design: rebuilding must not launder a revoked
        # right back into existence.
        after = self.ledger.provision(verification(digest=DIGEST_B))
        self.assertEqual(after.granted, ())
        self.assertEqual(after.withheld_revoked, ("review_verdict",))
        self.assertIsNone(after.notice)
        self.assertFalse(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )

    def test_only_an_explicit_restore_undoes_a_revocation(self) -> None:
        self.ledger.provision(verification())
        self.ledger.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        self.assertTrue(
            self.ledger.restore_right(
                SubjectKind.PROVIDER,
                "antigravity",
                "review_verdict",
                restored_by="owner",
            )
        )
        self.assertTrue(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )
        # Restoring something that was never revoked is not a silent success.
        self.assertFalse(
            self.ledger.restore_right(
                SubjectKind.PROVIDER,
                "antigravity",
                "review_verdict",
                restored_by="owner",
            )
        )

    def test_revocation_and_restoration_must_name_an_owner(self) -> None:
        self.ledger.provision(verification())
        with self.assertRaisesRegex(RightsProvisioningError, "revoking owner"):
            self.ledger.revoke_right(
                SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="  "
            )
        with self.assertRaisesRegex(RightsProvisioningError, "restoring owner"):
            self.ledger.restore_right(
                SubjectKind.PROVIDER, "antigravity", "review_verdict", restored_by=""
            )

    def test_revoking_one_right_leaves_the_others(self) -> None:
        self.ledger.provision(
            verification(rights=frozenset({"review_verdict", "debate_turn"}))
        )
        self.ledger.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        self.assertEqual(
            self.ledger.held_rights(SubjectKind.PROVIDER, "antigravity"),
            frozenset({"debate_turn"}),
        )

    def test_subjects_and_kinds_are_isolated(self) -> None:
        self.ledger.provision(verification(subject_id="antigravity"))
        self.ledger.provision(verification(subject_id="grok"))
        self.ledger.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        self.assertTrue(
            self.ledger.holds(SubjectKind.PROVIDER, "grok", "review_verdict")
        )
        # A project named like a provider is a different subject entirely.
        self.ledger.provision(
            verification(
                kind=SubjectKind.PROJECT,
                subject_id="grok",
                rights=frozenset({"intake"}),
            )
        )
        self.assertFalse(
            self.ledger.holds(SubjectKind.PROJECT, "grok", "review_verdict")
        )

    def test_a_declaration_with_no_rights_grants_nothing_and_notifies_nobody(
        self,
    ) -> None:
        outcome = self.ledger.provision(verification(rights=frozenset()))
        self.assertEqual(outcome.granted, ())
        self.assertIsNone(outcome.notice)
        self.assertEqual(self.ledger.pending_notices(), ())

    def test_state_survives_reopening(self) -> None:
        path = Path(self.temp.name) / "persist.sqlite3"
        first = RightsLedger(path)
        first.provision(verification())
        first.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        # A revocation that vanished on restart would be the worst possible
        # failure mode for this module.
        reopened = RightsLedger(path)
        self.assertFalse(
            reopened.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )
        self.assertEqual(
            reopened.provision(verification()).withheld_revoked, ("review_verdict",)
        )


class SettingsIntegrationTests(unittest.TestCase):
    """The ledger reaches Settings without changing a deployment that opted out."""

    def test_absent_ledger_path_grants_nothing(self) -> None:
        with patch.dict(os.environ, {"RIGHTS_LEDGER_PATH": ""}, clear=False):
            settings = Settings()
        self.assertEqual(settings.provisioned_provider_rights, {})
        self.assertEqual(settings.provisioned_project_rights, {})

    def test_unreadable_ledger_is_refusal_not_a_crash(self) -> None:
        # A corrupt or missing ledger must be indistinguishable from "granted
        # nothing", never from "granted something", and must not take startup
        # down: Settings is constructed once at import time in main.py.
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.sqlite3"
            corrupt.write_bytes(b"this is not a database")
            with patch.dict(
                os.environ, {"RIGHTS_LEDGER_PATH": str(corrupt)}, clear=False
            ):
                settings = Settings()
            self.assertEqual(settings.provisioned_provider_rights, {})

    def test_a_provisioned_grant_reaches_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            ledger = RightsLedger(path)
            ledger.provision(verification())
            with patch.dict(
                os.environ, {"RIGHTS_LEDGER_PATH": str(path)}, clear=False
            ):
                settings = Settings()
            self.assertEqual(
                settings.provisioned_provider_rights.get("antigravity"),
                frozenset({"review_verdict"}),
            )
            # A revoked right must not reappear through the settings view.
            ledger.revoke_right(
                SubjectKind.PROVIDER,
                "antigravity",
                "review_verdict",
                revoked_by="owner",
            )
            with patch.dict(
                os.environ, {"RIGHTS_LEDGER_PATH": str(path)}, clear=False
            ):
                after = Settings()
            self.assertEqual(after.provisioned_provider_rights, {})

    def test_antigravity_still_fails_closed_on_the_flag_alone(self) -> None:
        # The invariant that must survive this feature: requesting verdict
        # authority without a provisioned grant is still refused.
        base = replace(Settings(), gemini_api_key="test-gemini")
        self.assertTrue(base.readiness()["gemini"])
        self.assertFalse(
            replace(base, gemini_allow_review_verdict=True).readiness()["gemini"]
        )
        self.assertFalse(
            replace(base, gemini_allow_primary_authority=True).readiness()["gemini"]
        )


class BuildDigestTests(unittest.TestCase):
    def test_digest_is_content_bound_and_order_sensitive(self) -> None:
        self.assertEqual(build_digest_for([b"ab"]), build_digest_for([b"a", b"b"]))
        self.assertNotEqual(build_digest_for([b"ab"]), build_digest_for([b"ba"]))
        self.assertRegex(build_digest_for([b""]), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]]
