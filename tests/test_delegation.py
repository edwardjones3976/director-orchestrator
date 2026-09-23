# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 05:44 UTC | BEGIN]]
# [[Z.ai | glm-5.3-flash via Claude Code | 2026-09-13 | 05:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 19:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 16:10 UTC | BEGIN]]
"""Phase 1 of human-out-of-the-loop operation: schema, validation, readiness.

Every case here asserts that something stays closed. Phase 1 executes no
decision, so the tests that matter are the ones proving a grant cannot widen by
accident and that anything short of a complete, live, valid grant reads as
ATTENDED.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from director_orchestrator.config import Settings
from director_orchestrator.delegation import (
    GRANT_SCHEMA_VERSION,
    DelegationConfigurationError,
    DelegationGrant,
    DelegationState,
    Tier1Class,
    configured_state,
    delegation_readiness,
    parse_grant,
)

NOW = datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=12)


def grant_document(**overrides) -> str:
    payload = {
        "schema_version": GRANT_SCHEMA_VERSION,
        "grant_id": "grant-001",
        "repository": "edwardjones3976/director-orchestrator",
        "branch_pattern": "claude/*",
        "classes": [Tier1Class.MARK_PULL_REQUEST_READY.value],
        "providers": ["openai", "cursor"],
        "expires_at": LATER.isoformat(),
        "max_exercises": 5,
        "revoked": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


class GrantSchemaTests(unittest.TestCase):
    def test_a_complete_grant_parses(self):
        grant = parse_grant(grant_document())
        self.assertIsInstance(grant, DelegationGrant)
        self.assertEqual({Tier1Class.MARK_PULL_REQUEST_READY}, grant.classes)
        self.assertTrue(grant.live_at(NOW))

    def test_absent_or_blank_is_no_delegation_rather_than_an_error(self):
        for document in (None, "", "   "):
            with self.subTest(document=document):
                self.assertIsNone(parse_grant(document))

    def test_tier_one_is_a_closed_enumeration(self):
        """An unknown class is refused, never dropped or mapped to a neighbour.

        Dropping it would quietly narrow a grant the owner believed was wider;
        mapping it would widen one they believed was narrower.
        """

        self.assertEqual(
            {
                "merge_pull_request_into_non_protected_branch",
                "mark_pull_request_ready_for_review",
                "post_to_project_designated_slack_channel",
                "execute_small_read_only_task",
            },
            {item.value for item in Tier1Class},
        )
        for classes in (["deploy_to_production"], ["routine_change"], []):
            with self.subTest(classes=classes):
                with self.assertRaises(DelegationConfigurationError):
                    parse_grant(grant_document(classes=classes))

    def test_field_set_must_be_exact(self):
        """A superset is refused: an unrecognised member may be an intended
        widening, and ignoring it silently is worse than refusing the grant."""

        payload = json.loads(grant_document())
        payload["also_allow"] = "deployment"
        with self.assertRaises(DelegationConfigurationError):
            parse_grant(json.dumps(payload))

        del payload["also_allow"]
        del payload["revoked"]
        with self.assertRaises(DelegationConfigurationError):
            parse_grant(json.dumps(payload))

    def test_a_grant_must_name_one_eligible_provider(self):
        """The evidence bar needs one ACCEPT from an eligible provider, so a
        zero-provider grant can never be satisfied, while a one-provider
        grant parses."""

        with self.assertRaises(DelegationConfigurationError):
            parse_grant(grant_document(providers=[]))
        grant = parse_grant(grant_document(providers=["openai"]))
        self.assertEqual(frozenset({"openai"}), grant.providers)

    def test_every_axis_is_bounded(self):
        for field, value in (
            ("schema_version", "director-delegation-grant/v2"),
            ("grant_id", "not a bounded identifier!"),
            ("repository", "not-a-repository"),
            ("branch_pattern", "claude/*/deep*"),
            ("max_exercises", 0),
            ("max_exercises", 100_000),
            ("max_exercises", True),
            ("revoked", "false"),
            ("expires_at", "not-a-date"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(DelegationConfigurationError):
                    parse_grant(grant_document(**{field: value}))

    def test_a_naive_expiry_is_rejected_rather_than_repaired(self):
        """Stamping UTC onto a naive value invents an offset the owner did not
        write, and a grant meant to expire at 17:00 local would stay live."""

        with self.assertRaises(DelegationConfigurationError):
            parse_grant(grant_document(expires_at="2026-09-09T04:00:00"))

    def test_non_string_fields_are_refused_rather_than_coerced(self):
        """str() would turn a JSON number, list or object into text that then
        passes the bounding regexes."""

        for field, value in (
            ("schema_version", 1),
            ("grant_id", 123),
            ("repository", ["edwardjones3976", "director-orchestrator"]),
            ("branch_pattern", {"claude/": True}),
            ("providers", ["openai", 123]),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(DelegationConfigurationError):
                    parse_grant(grant_document(**{field: value}))

    def test_oversized_and_non_object_documents_are_refused(self):
        with self.assertRaises(DelegationConfigurationError):
            parse_grant(json.dumps({"padding": "x" * 20_000}))
        with self.assertRaises(DelegationConfigurationError):
            parse_grant("[]")
        with self.assertRaises(DelegationConfigurationError):
            parse_grant("{not json")


class GrantScopeTests(unittest.TestCase):
    def grant(self, **overrides) -> DelegationGrant:
        return parse_grant(grant_document(**overrides))

    def test_authority_does_not_extend_by_analogy(self):
        """Authority for one repository, class, or branch is never authority
        for another. Grants do not compose, inherit, or extend."""

        grant = self.grant()
        self.assertTrue(
            grant.covers(
                Tier1Class.MARK_PULL_REQUEST_READY,
                repository="edwardjones3976/director-orchestrator",
                branch="claude/feature",
            )
        )
        # A class the grant is silent on.
        self.assertFalse(
            grant.covers(
                Tier1Class.MERGE_UNPROTECTED_BRANCH,
                repository="edwardjones3976/director-orchestrator",
                branch="claude/feature",
            )
        )
        # Another repository, and another branch.
        self.assertFalse(
            grant.covers(
                Tier1Class.MARK_PULL_REQUEST_READY,
                repository="edwardjones3976/project-uppercut",
                branch="claude/feature",
            )
        )
        self.assertFalse(
            grant.covers(
                Tier1Class.MARK_PULL_REQUEST_READY,
                repository="edwardjones3976/director-orchestrator",
                branch="main",
            )
        )

    def test_a_branch_pattern_cannot_widen_past_its_segment(self):
        """A trailing "*" means this segment prefix and below. "main*" would
        swallow the sibling branch "maintenance", so a widening pattern is
        refused at configuration rather than narrowed at match time."""

        for pattern in ("main*", "claude/hotl*", "*"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(DelegationConfigurationError):
                    parse_grant(grant_document(branch_pattern=pattern))

        grant = self.grant(branch_pattern="main/*")
        for branch, covered in (
            ("main/topic", True),
            ("maintenance", False),
            ("main", False),
        ):
            with self.subTest(branch=branch):
                self.assertEqual(
                    covered,
                    grant.covers(
                        Tier1Class.MARK_PULL_REQUEST_READY,
                        repository="edwardjones3976/director-orchestrator",
                        branch=branch,
                    ),
                )

    def test_expired_and_revoked_grants_are_not_live(self):
        self.assertFalse(self.grant(expires_at=NOW.isoformat()).live_at(NOW))
        self.assertFalse(self.grant(revoked=True).live_at(NOW))


class OperatingStateTests(unittest.TestCase):
    def test_default_is_attended_and_every_shortfall_returns_to_it(self):
        grant = parse_grant(grant_document())
        for label, kwargs in (
            ("switch off", {"grant": grant, "enabled": False, "suspended": False}),
            ("no grant", {"grant": None, "enabled": True, "suspended": False}),
            (
                "expired grant",
                {
                    "grant": parse_grant(grant_document(expires_at=NOW.isoformat())),
                    "enabled": True,
                    "suspended": False,
                },
            ),
            (
                "revoked grant",
                {
                    "grant": parse_grant(grant_document(revoked=True)),
                    "enabled": True,
                    "suspended": False,
                },
            ),
        ):
            with self.subTest(label):
                self.assertEqual(
                    DelegationState.ATTENDED, configured_state(moment=NOW, **kwargs)
                )

    def test_suspended_outranks_a_live_grant(self):
        """There is no path from SUSPENDED to DELEGATED that does not pass
        through an explicit owner action, so it is never inferred away."""

        self.assertEqual(
            DelegationState.SUSPENDED,
            configured_state(
                parse_grant(grant_document()),
                enabled=True,
                suspended=True,
                moment=NOW,
            ),
        )

    def test_delegated_requires_everything_at_once(self):
        self.assertEqual(
            DelegationState.DELEGATED,
            configured_state(
                parse_grant(grant_document()),
                enabled=True,
                suspended=False,
                moment=NOW,
            ),
        )


class ReadinessTests(unittest.TestCase):
    def test_shipped_default_is_attended_with_nothing_configured(self):
        readiness = Settings().delegation_readiness()
        self.assertEqual("ATTENDED", readiness["configured_state"])
        self.assertFalse(readiness["enabled_requested"])
        self.assertFalse(readiness["grant_configured"])
        self.assertFalse(readiness["grant_malformed"])
        self.assertEqual(1, readiness["phase"])
        self.assertFalse(readiness["executes_decisions"])

    def test_a_malformed_grant_reads_as_attended_and_says_so(self):
        readiness = delegation_readiness(
            enabled=True, suspended=False, grant_document="{not json", moment=NOW
        )
        self.assertEqual("ATTENDED", readiness["configured_state"])
        self.assertTrue(readiness["grant_malformed"])
        self.assertFalse(readiness["grant_live"])

    def test_readiness_never_reports_grant_contents(self):
        """/health returns readiness booleans only, never secret or allowlist
        values, and a grant names real repositories and providers."""

        readiness = delegation_readiness(
            enabled=True, suspended=False, grant_document=grant_document(), moment=NOW
        )
        serialized = json.dumps(readiness)
        for secret in (
            "edwardjones3976/director-orchestrator",
            "claude/*",
            "grant-001",
            "openai",
            "cursor",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)
        # The counts and class names that remain are not identifying.
        self.assertEqual(2, readiness["eligible_provider_count"])
        self.assertEqual(
            ["mark_pull_request_ready_for_review"], readiness["classes_configured"]
        )

    def test_the_reported_state_is_configuration_not_authority(self):
        """Phase 4 is the first phase that resolves a decision, so a live grant
        reports what the owner configured and never permission to act."""

        readiness = delegation_readiness(
            enabled=True, suspended=False, grant_document=grant_document(), moment=NOW
        )
        self.assertEqual("DELEGATED", readiness["configured_state"])
        self.assertFalse(readiness["executes_decisions"])
        # No bare "state" a future caller could read as "this may act".
        self.assertNotIn("state", readiness)

        from director_orchestrator import delegation

        self.assertFalse(hasattr(delegation, "resolve_state"))

    def test_phase_one_exposes_no_decision_entry_point(self):
        """Phase 4 is the first phase that resolves a decision. Phase 1 must not
        ship one early, so the module surface is asserted directly."""

        from director_orchestrator import delegation

        for forbidden in ("execute", "resolve_decision", "merge", "approve", "decide"):
            matches = [
                name
                for name in dir(delegation)
                if forbidden in name and not name.startswith("_")
            ]
            self.assertEqual([], matches, f"phase 1 exposes {matches}")


if __name__ == "__main__":
    unittest.main()

# [[Claude Code | Claude Opus 5 | 2026-09-08 | 16:10 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 19:54 UTC | END]]
# [[Z.ai | glm-5.3-flash via Claude Code | 2026-09-13 | 05:44 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 05:44 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | END]]
