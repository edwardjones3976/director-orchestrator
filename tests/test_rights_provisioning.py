# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | BEGIN]]
# [[OpenAI | GPT-5.6 | 2026-09-04 | 11:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 10:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:12 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]]
"""Contract tests for automatic rights provisioning.

The owner asked for rights to be assigned immediately once a subject installs
and builds, with the owner notified rather than asked first. These tests pin the
properties that make that safe to have built at all: a right nobody declared
cannot appear, a grant cannot reach the ledger without a notice behind it, and a
revocation is not undone by rebuilding.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import (
    RIGHTS_SNAPSHOT_TTL_SECONDS,
    Settings,
    current_provisioned_rights,
    invalidate_provisioned_rights_cache,
)
from director_orchestrator.rights_provisioning import (
    AUTHORITY_BEARING_RIGHTS,
    InstallVerification,
    RightsLedger,
    RightsProvisioningError,
    RIGHTS_VOCABULARY,
    SubjectKind,
    build_digest_for,
    parse_active_build_digests,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
#: One fixed instant, passed explicitly wherever a test depends on two calls
#: landing in the same second. Relying on the wall clock to stay inside one
#: second is exactly the flake that hides the collision these tests are for.
RAISED_AT = 1_760_000_000


def hermetic_env(**overrides: str):
    """Pin every rights variable Settings reads.

    Settings reads the live process environment, so an ambient
    RIGHTS_LEDGER_PATH on a developer machine would otherwise decide what these
    assertions see.
    """
    values = {"RIGHTS_LEDGER_PATH": "", "RIGHTS_ACTIVE_BUILD_DIGESTS_JSON": ""}
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


def active_builds(subject_id: str = "antigravity", digest: str = DIGEST_A) -> str:
    return json.dumps({f"provider:{subject_id}": digest})


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
    def test_declared_rights_are_copied_and_frozen_before_validation(self) -> None:
        declared = {"review_verdict"}
        verified = verification(rights=declared)  # type: ignore[arg-type]
        declared.add("become_owner")
        self.assertEqual(verified.declared_rights, frozenset({"review_verdict"}))

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

    def test_split_same_second_grants_have_distinct_notices(self) -> None:
        # Both calls are pinned to the identical instant, so "same second" is a
        # property of the test rather than a hope about how fast it runs.
        first = self.ledger.provision(
            verification(rights=frozenset({"review_verdict"})), now=RAISED_AT
        )
        second = self.ledger.provision(
            verification(rights=frozenset({"debate_turn"})), now=RAISED_AT
        )
        assert first.notice is not None
        assert second.notice is not None
        self.assertEqual(first.notice.raised_at, second.notice.raised_at)
        self.assertNotEqual(first.notice.notice_id, second.notice.notice_id)
        self.assertEqual(
            {notice.rights for notice in self.ledger.pending_notices()},
            {("review_verdict",), ("debate_turn",)},
        )

    def test_a_forced_notice_collision_rolls_back_instead_of_overwriting(
        self,
    ) -> None:
        # Deriving the id from the granted rights is what stops a collision
        # arising on its own, so the failure mode has to be forced to be tested.
        # What must not happen is the second grant landing while the first
        # notice is overwritten to describe it: the first grant's rows would
        # then point at a notice about different rights.
        with patch(
            "director_orchestrator.rights_provisioning._notice_id",
            return_value="forced-collision",
        ):
            self.ledger.provision(
                verification(rights=frozenset({"review_verdict"})), now=RAISED_AT
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.provision(
                    verification(rights=frozenset({"debate_turn"})), now=RAISED_AT
                )
        # The refused attempt left nothing behind.
        self.assertFalse(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "debate_turn")
        )
        # The first grant and its notice are untouched.
        self.assertTrue(
            self.ledger.holds(SubjectKind.PROVIDER, "antigravity", "review_verdict")
        )
        notices = self.ledger.pending_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].notice_id, "forced-collision")
        self.assertEqual(notices[0].rights, ("review_verdict",))

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

    def test_rebinding_updates_the_row_and_leaves_the_earlier_notice_intact(
        self,
    ) -> None:
        # The rebind is an upsert of three columns, not a delete-and-reinsert,
        # and it must not disturb the earlier notice: the owner's record of the
        # first grant is evidence about a build that really did hold the right.
        first = self.ledger.provision(verification(digest=DIGEST_A), now=RAISED_AT)
        second = self.ledger.provision(verification(digest=DIGEST_B), now=RAISED_AT)
        assert first.notice is not None and second.notice is not None
        ledger_path = Path(self.temp.name) / "rights.sqlite3"
        with closing(sqlite3.connect(ledger_path)) as connection:
            rows = connection.execute(
                """
                SELECT build_digest, granted_at, notice_id, revoked_at, revoked_by
                FROM provisioned_right
                WHERE subject_kind = 'provider' AND subject_id = 'antigravity'
                  AND right_name = 'review_verdict'
                """
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], DIGEST_B)
        self.assertEqual(rows[0][2], second.notice.notice_id)
        self.assertIsNone(rows[0][3])
        self.assertIsNone(rows[0][4])
        # Both notices survive, each describing the build it was raised for.
        notices = {notice.notice_id: notice for notice in self.ledger.pending_notices()}
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[first.notice.notice_id].build_digest, DIGEST_A)
        self.assertEqual(notices[second.notice.notice_id].build_digest, DIGEST_B)

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

    def test_subject_ids_lists_only_kinds_with_a_live_right(self) -> None:
        # Settings enumerates subjects through this, so a subject whose last
        # right was revoked must drop out of it entirely rather than surface
        # with an empty right set.
        self.assertEqual(self.ledger.subject_ids(SubjectKind.PROVIDER), ())
        self.ledger.provision(verification(subject_id="antigravity"))
        self.ledger.provision(verification(subject_id="grok"))
        self.ledger.provision(
            verification(
                kind=SubjectKind.PROJECT,
                subject_id="uppercut",
                rights=frozenset({"intake"}),
            )
        )
        self.assertEqual(
            self.ledger.subject_ids(SubjectKind.PROVIDER), ("antigravity", "grok")
        )
        self.assertEqual(self.ledger.subject_ids(SubjectKind.PROJECT), ("uppercut",))
        self.ledger.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        self.assertEqual(self.ledger.subject_ids(SubjectKind.PROVIDER), ("grok",))

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

    def test_held_rights_are_bound_to_the_callers_active_build(self) -> None:
        self.ledger.provision(verification(digest=DIGEST_A))
        self.assertEqual(
            self.ledger.held_rights_for_build(
                SubjectKind.PROVIDER, "antigravity", DIGEST_A
            ),
            frozenset({"review_verdict"}),
        )
        self.assertEqual(
            self.ledger.held_rights_for_build(
                SubjectKind.PROVIDER, "antigravity", DIGEST_B
            ),
            frozenset(),
        )
        with self.assertRaisesRegex(RightsProvisioningError, "exact sha256"):
            self.ledger.held_rights_for_build(
                SubjectKind.PROVIDER, "antigravity", "not-a-digest"
            )

    def test_build_bound_read_observes_revocation_immediately(self) -> None:
        self.ledger.provision(verification())
        self.ledger.revoke_right(
            SubjectKind.PROVIDER, "antigravity", "review_verdict", revoked_by="owner"
        )
        self.assertEqual(
            self.ledger.held_rights_for_build(
                SubjectKind.PROVIDER, "antigravity", DIGEST_A
            ),
            frozenset(),
        )


class SettingsIntegrationTests(unittest.TestCase):
    """The ledger reaches Settings without changing a deployment that opted out."""

    def setUp(self) -> None:
        # Each case owns its own ledger path, and the cache is keyed by that
        # path, but clearing it makes these assertions independent of order.
        invalidate_provisioned_rights_cache()

    def tearDown(self) -> None:
        invalidate_provisioned_rights_cache()

    def test_absent_ledger_path_grants_nothing(self) -> None:
        with hermetic_env():
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
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(corrupt),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
            ):
                settings = Settings()
            self.assertEqual(settings.provisioned_provider_rights, {})

    def test_settings_never_creates_a_missing_ledger(self) -> None:
        # Settings is constructed at import time from an operator-supplied path.
        # If a wrong path could bring a ledger into existence, the fail-closed
        # story would depend on nobody making a typo.
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nested" / "rights.sqlite3"
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(missing),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
            ):
                settings = Settings()
            self.assertEqual(settings.provisioned_provider_rights, {})
            self.assertEqual(settings.provisioned_project_rights, {})
            self.assertFalse(missing.exists())
            self.assertFalse(missing.parent.exists())
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), [])

    def test_settings_does_not_touch_an_unrelated_database(self) -> None:
        # The realistic misconfiguration is RIGHTS_LEDGER_PATH aimed at
        # SQLITE_PATH. That must be a refusal, not a silent schema migration of
        # live product data.
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "director.sqlite3"
            with closing(sqlite3.connect(product)) as connection:
                connection.execute("CREATE TABLE task (id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO task (id) VALUES ('t-1')")
                connection.commit()
            before = product.read_bytes()
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(product),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
            ):
                settings = Settings()
            # Sampled before anything else reopens the file, so the comparison
            # can only be attributing changes to Settings.
            after = product.read_bytes()
            self.assertEqual(settings.provisioned_provider_rights, {})
            self.assertEqual(settings.provisioned_project_rights, {})
            self.assertEqual(after, before)
            self.assertFalse((Path(directory) / "director.sqlite3-wal").exists())
            self.assertFalse((Path(directory) / "director.sqlite3-shm").exists())
            with closing(sqlite3.connect(product)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertEqual(tables, {"task"})

    def test_malformed_active_build_configuration_grants_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            RightsLedger(path).provision(verification())
            for raw in (
                "not-json",
                "[]",
                '{"provider:antigravity":"too-short"}',
                '{"antigravity":"' + DIGEST_A + '"}',
                '{"provider:antigravity":42}',
                # json.loads raises more than JSONDecodeError. An integer
                # literal past CPython's 4300-digit conversion cap raises a
                # bare ValueError from the scanner, and deep nesting raises
                # RecursionError. Neither is caught by the narrow except in
                # config.py, so before these were translated they escaped to
                # Settings() -- which main.py builds at module scope, making a
                # mistyped variable a service that will not boot rather than
                # one that grants nothing.
                "1" * 4301,
                "[" * 3000 + "]" * 3000,
            ):
                with self.subTest(raw=raw):
                    with hermetic_env(
                        RIGHTS_LEDGER_PATH=str(path),
                        RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=raw,
                    ):
                        settings = Settings()
                    # A map that cannot be trusted is a map that grants nothing,
                    # including the entries that happened to parse.
                    self.assertEqual(settings.provisioned_provider_rights, {})
                    self.assertEqual(settings.provisioned_project_rights, {})

    def test_a_provisioned_grant_reaches_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            ledger = RightsLedger(path)
            ledger.provision(verification())
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(path),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
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
            # Settings is explicitly a construction-time snapshot. The
            # privileged operation boundary must use held_rights_for_build, or
            # current_provisioned_rights for a bounded-freshness answer.
            self.assertEqual(
                settings.provisioned_provider_rights.get("antigravity"),
                frozenset({"review_verdict"}),
            )
            # A fresh process starts with an empty read cache, so clearing it is
            # what "restart" means to this module.
            invalidate_provisioned_rights_cache()
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(path),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
            ):
                after = Settings()
            self.assertEqual(after.provisioned_provider_rights, {})

    def test_settings_refuses_missing_or_mismatched_active_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            RightsLedger(path).provision(verification(digest=DIGEST_A))
            with hermetic_env(RIGHTS_LEDGER_PATH=str(path)):
                missing = Settings()
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(path),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(digest=DIGEST_B),
            ):
                mismatched = Settings()
            self.assertEqual(missing.provisioned_provider_rights, {})
            self.assertEqual(mismatched.provisioned_provider_rights, {})

    def test_expected_ledger_errors_fail_closed_but_programmer_errors_surface(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            RightsLedger(path)
            with hermetic_env(
                RIGHTS_LEDGER_PATH=str(path),
                RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
            ), patch(
                "director_orchestrator.rights_provisioning.RightsLedger.subject_ids",
                side_effect=RuntimeError("programmer defect"),
            ):
                with self.assertRaisesRegex(RuntimeError, "programmer defect"):
                    Settings()

    def test_antigravity_still_fails_closed_on_the_flag_alone(self) -> None:
        # The invariant that must survive this feature: requesting verdict
        # authority without a provisioned grant is still refused.
        with hermetic_env():
            base = replace(Settings(), gemini_api_key="test-gemini")
        self.assertTrue(base.readiness()["gemini"])
        self.assertFalse(
            replace(base, gemini_allow_review_verdict=True).readiness()["gemini"]
        )
        self.assertFalse(
            replace(base, gemini_allow_primary_authority=True).readiness()["gemini"]
        )


class ProvisionedRightsRefreshTests(unittest.TestCase):
    """A rights change must not need a restart, and must not cost a read per call.

    main.py builds one module-level Settings and keeps it for the life of the
    process. If a decision point read that snapshot, a subject provisioned after
    boot would stay invisible and — the direction that matters — a revoked right
    would keep working until every process was restarted. These pin the lookup
    that does not have that property, and pin the cost of having it.
    """

    def setUp(self) -> None:
        invalidate_provisioned_rights_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "rights.sqlite3"
        self.ledger = RightsLedger(self.path)
        self.env = hermetic_env(
            RIGHTS_LEDGER_PATH=str(self.path),
            RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(),
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(invalidate_provisioned_rights_cache)
        self.addCleanup(self.temp.cleanup)

    def test_a_grant_recorded_after_boot_becomes_visible(self) -> None:
        self.assertEqual(current_provisioned_rights("provider", now=0.0), {})
        self.ledger.provision(verification())
        # Still inside the staleness budget: the cached answer stands.
        self.assertEqual(current_provisioned_rights("provider", now=0.0), {})
        self.assertEqual(
            current_provisioned_rights(
                "provider", now=RIGHTS_SNAPSHOT_TTL_SECONDS
            ).get("antigravity"),
            frozenset({"review_verdict"}),
        )

    def test_a_revocation_stops_being_honoured_within_the_bound(self) -> None:
        self.ledger.provision(verification())
        self.assertEqual(
            current_provisioned_rights("provider", now=0.0).get("antigravity"),
            frozenset({"review_verdict"}),
        )
        self.assertTrue(
            self.ledger.revoke_right(
                SubjectKind.PROVIDER,
                "antigravity",
                "review_verdict",
                revoked_by="owner",
            )
        )
        # The staleness is bounded, not unbounded: one TTL, not one restart.
        self.assertEqual(
            current_provisioned_rights("provider", now=RIGHTS_SNAPSHOT_TTL_SECONDS),
            {},
        )

    def test_an_in_process_revocation_can_be_made_visible_at_once(self) -> None:
        self.ledger.provision(verification())
        self.assertEqual(
            current_provisioned_rights("provider", now=0.0).get("antigravity"),
            frozenset({"review_verdict"}),
        )
        self.ledger.revoke_right(
            SubjectKind.PROVIDER,
            "antigravity",
            "review_verdict",
            revoked_by="owner",
        )
        invalidate_provisioned_rights_cache()
        self.assertEqual(current_provisioned_rights("provider", now=0.0), {})

    def test_a_configuration_change_is_not_held_back_by_the_cache(self) -> None:
        self.ledger.provision(verification(digest=DIGEST_A))
        self.assertEqual(
            current_provisioned_rights("provider", now=0.0).get("antigravity"),
            frozenset({"review_verdict"}),
        )
        # Same instant, different active build: the answer was derived from
        # configuration that no longer holds, so it cannot be reused.
        with hermetic_env(
            RIGHTS_LEDGER_PATH=str(self.path),
            RIGHTS_ACTIVE_BUILD_DIGESTS_JSON=active_builds(digest=DIGEST_B),
        ):
            self.assertEqual(current_provisioned_rights("provider", now=0.0), {})

    def test_a_rewound_clock_produces_a_fresh_read_never_a_stale_one(self) -> None:
        self.assertEqual(current_provisioned_rights("provider", now=100.0), {})
        self.ledger.provision(verification())
        self.assertEqual(
            current_provisioned_rights("provider", now=0.0).get("antigravity"),
            frozenset({"review_verdict"}),
        )

    def test_concurrent_lookups_cost_one_ledger_read(self) -> None:
        self.ledger.provision(verification())
        reads = []
        lock = threading.Lock()
        real = current_provisioned_rights.__globals__["_read_provisioned_rights"]

        def counting(kind: str, path: str, active_raw: str):
            with lock:
                reads.append(kind)
            return real(kind, path, active_raw)

        start = threading.Barrier(24)
        results: list[dict[str, frozenset[str]]] = []

        def worker() -> None:
            start.wait()
            answer = current_provisioned_rights("provider", now=0.0)
            with lock:
                results.append(answer)

        with patch(
            "director_orchestrator.config._read_provisioned_rights",
            side_effect=counting,
        ):
            threads = [threading.Thread(target=worker) for _ in range(24)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        # Bounded I/O: arriving together costs one read, not one per caller.
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(results), 24)
        for answer in results:
            self.assertEqual(answer.get("antigravity"), frozenset({"review_verdict"}))

    def test_the_returned_map_cannot_be_mutated_through_the_cache(self) -> None:
        self.ledger.provision(verification())
        first = current_provisioned_rights("provider", now=0.0)
        first["antigravity"] = frozenset({"repository_write"})
        first["injected"] = frozenset({"review_verdict"})
        second = current_provisioned_rights("provider", now=0.0)
        self.assertEqual(second.get("antigravity"), frozenset({"review_verdict"}))
        self.assertNotIn("injected", second)

    def test_an_unreadable_ledger_is_still_no_rights(self) -> None:
        self.path.write_bytes(b"not a database")
        self.assertEqual(current_provisioned_rights("provider", now=0.0), {})

    def test_a_programmer_defect_still_surfaces(self) -> None:
        with patch(
            "director_orchestrator.rights_provisioning.RightsLedger.subject_ids",
            side_effect=RuntimeError("programmer defect"),
        ):
            with self.assertRaisesRegex(RuntimeError, "programmer defect"):
                current_provisioned_rights("provider", now=0.0)


class OptedOutRefreshTests(unittest.TestCase):
    """A deployment that has not opted in must do no work and keep no state."""

    def setUp(self) -> None:
        invalidate_provisioned_rights_cache()
        self.addCleanup(invalidate_provisioned_rights_cache)

    def test_no_ledger_path_reads_nothing(self) -> None:
        with hermetic_env(), patch(
            "director_orchestrator.config._read_provisioned_rights",
            side_effect=AssertionError("an opted-out deployment must not read"),
        ):
            self.assertEqual(current_provisioned_rights("provider"), {})
            self.assertEqual(current_provisioned_rights("project"), {})

    def test_a_ledger_without_an_active_build_reads_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rights.sqlite3"
            RightsLedger(path).provision(verification())
            with hermetic_env(RIGHTS_LEDGER_PATH=str(path)), patch(
                "director_orchestrator.config._read_provisioned_rights",
                side_effect=AssertionError("no active build must not read"),
            ):
                self.assertEqual(current_provisioned_rights("provider"), {})


class ActiveBuildConfigurationTests(unittest.TestCase):
    def test_active_build_map_is_strict_and_canonical(self) -> None:
        self.assertEqual(
            parse_active_build_digests(active_builds()),
            {(SubjectKind.PROVIDER, "antigravity"): DIGEST_A},
        )
        for raw in (
            "not-json",
            "[]",
            '{"provider:../escape":"' + DIGEST_A + '"}',
            '{"unknown:antigravity":"' + DIGEST_A + '"}',
            '{"provider:antigravity":"not-a-digest"}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(RightsProvisioningError):
                    parse_active_build_digests(raw)


class BuildDigestTests(unittest.TestCase):
    def test_digest_is_content_bound_and_order_sensitive(self) -> None:
        self.assertEqual(build_digest_for([b"ab"]), build_digest_for([b"a", b"b"]))
        self.assertNotEqual(build_digest_for([b"ab"]), build_digest_for([b"ba"]))
        self.assertRegex(build_digest_for([b""]), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:12 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 10:56 UTC | END]]
# [[OpenAI | GPT-5.6 | 2026-09-04 | 11:03 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | END]]
