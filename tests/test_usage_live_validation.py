from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from director_orchestrator.usage_live_validation import (
    COOLDOWN_WINDOW,
    UsageLiveValidationError,
    assert_cooling_success_evidence,
    assert_scheduled_cycle_evidence,
    evaluate_usage_live_validation,
    validate_provider_partition,
)


def _ts(now: datetime, *, minutes_ago: float) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class UsageProviderPartitionTests(unittest.TestCase):
    def test_accepts_disjoint_cover_of_primary_providers(self):
        scheduled, cooling = validate_provider_partition(
            scheduled_providers=["openai"],
            cooling_down_providers=["claude", "cursor"],
        )
        self.assertEqual(scheduled, ["openai"])
        self.assertEqual(cooling, ["claude", "cursor"])

    def test_rejects_unknown_provider_instead_of_silent_discard(self):
        with self.assertRaisesRegex(UsageLiveValidationError, "Unknown providers"):
            validate_provider_partition(
                scheduled_providers=["openai", "gemini"],
                cooling_down_providers=["claude", "cursor"],
            )

    def test_rejects_overlap_duplicates_and_incomplete_cover(self):
        with self.assertRaisesRegex(UsageLiveValidationError, "overlap"):
            validate_provider_partition(
                scheduled_providers=["openai", "claude"],
                cooling_down_providers=["claude", "cursor"],
            )
        with self.assertRaisesRegex(UsageLiveValidationError, "duplicates"):
            validate_provider_partition(
                scheduled_providers=["openai", "openai"],
                cooling_down_providers=["claude", "cursor"],
            )
        with self.assertRaisesRegex(UsageLiveValidationError, "must partition"):
            validate_provider_partition(
                scheduled_providers=["openai"],
                cooling_down_providers=["claude"],
            )


class CoolingEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)

    def test_recent_ok_and_prompted_pass(self):
        records = [
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-AAA1111111",
            },
            {
                "provider": "cursor",
                "status": "prompted",
                "checked_at": _ts(self.now, minutes_ago=14.9),
                "cycle_id": "USAGE-BBB2222222",
            },
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=0),
                "cycle_id": "USAGE-CCC3333333",
            },
        ]
        accepted = assert_cooling_success_evidence(
            providers=["claude", "cursor", "openai"],
            latest_records=records,
            now=self.now,
        )
        self.assertEqual(len(accepted), 3)

    def test_recent_error_cannot_pass_not_due(self):
        records = [
            {
                "provider": "claude",
                "status": "error",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-ERR0000001",
            },
            {
                "provider": "cursor",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-OK00000001",
            },
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-OK00000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "error"):
            evaluate_usage_live_validation(
                run_status="not_due",
                scheduled_providers=[],
                cooling_down_providers=["claude", "cursor", "openai"],
                latest_records=records,
                now=self.now,
            )

    def test_recent_skipped_cannot_pass_not_due(self):
        records = [
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-OK00000001",
            },
            {
                "provider": "cursor",
                "status": "skipped",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-SKIP000001",
            },
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-OK00000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "skipped"):
            evaluate_usage_live_validation(
                run_status="not_due",
                scheduled_providers=[],
                cooling_down_providers=["claude", "cursor", "openai"],
                latest_records=records,
                now=self.now,
            )

    def test_missing_stale_duplicate_and_boundary_fail_closed(self):
        with self.assertRaisesRegex(UsageLiveValidationError, "Missing latest"):
            assert_cooling_success_evidence(
                providers=["claude"],
                latest_records=[],
                now=self.now,
            )

        stale = [
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=16),
                "cycle_id": "USAGE-STALE00001",
            }
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "outside the 15-minute"):
            assert_cooling_success_evidence(
                providers=["claude"],
                latest_records=stale,
                now=self.now,
            )

        # Exactly 15 minutes is due again under monitor semantics, not cooling evidence.
        boundary = [
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": (self.now - COOLDOWN_WINDOW).isoformat(),
                "cycle_id": "USAGE-BOUND00001",
            }
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "outside the 15-minute"):
            assert_cooling_success_evidence(
                providers=["claude"],
                latest_records=boundary,
                now=self.now,
            )

        duplicate = [
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=1),
                "cycle_id": "USAGE-DUP0000001",
            },
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=2),
                "cycle_id": "USAGE-DUP0000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "Ambiguous"):
            assert_cooling_success_evidence(
                providers=["claude"],
                latest_records=duplicate,
                now=self.now,
            )

        with self.assertRaisesRegex(UsageLiveValidationError, "checked_at"):
            assert_cooling_success_evidence(
                providers=["claude"],
                latest_records=[{"provider": "claude", "status": "ok"}],
                now=self.now,
            )


class PartialDueEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
        self.started = self.now - timedelta(minutes=2)

    def test_partial_due_requires_cooling_success_as_well_as_cycle_success(self):
        records = [
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=0.5),
                "cycle_id": "USAGE-PARTIAL001",
            },
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=3),
                "cycle_id": "USAGE-OLD0000001",
            },
            {
                "provider": "cursor",
                "status": "prompted",
                "checked_at": _ts(self.now, minutes_ago=4),
                "cycle_id": "USAGE-OLD0000002",
            },
        ]
        accepted = evaluate_usage_live_validation(
            run_status="accepted",
            scheduled_providers=["openai"],
            cooling_down_providers=["claude", "cursor"],
            latest_records=records,
            cycle_id="USAGE-PARTIAL001",
            started_at=self.started,
            now=self.now,
        )
        self.assertEqual(len(accepted), 3)

    def test_partial_due_rejects_cooling_error_even_when_scheduled_ok(self):
        records = [
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=0.5),
                "cycle_id": "USAGE-PARTIAL001",
            },
            {
                "provider": "claude",
                "status": "error",
                "checked_at": _ts(self.now, minutes_ago=3),
                "cycle_id": "USAGE-OLD0000001",
            },
            {
                "provider": "cursor",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=4),
                "cycle_id": "USAGE-OLD0000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "Cooling provider claude"):
            evaluate_usage_live_validation(
                run_status="accepted",
                scheduled_providers=["openai"],
                cooling_down_providers=["claude", "cursor"],
                latest_records=records,
                cycle_id="USAGE-PARTIAL001",
                started_at=self.started,
                now=self.now,
            )

    def test_partial_due_rejects_cooling_skipped_even_when_scheduled_ok(self):
        records = [
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=0.5),
                "cycle_id": "USAGE-PARTIAL001",
            },
            {
                "provider": "claude",
                "status": "ok",
                "checked_at": _ts(self.now, minutes_ago=3),
                "cycle_id": "USAGE-OLD0000001",
            },
            {
                "provider": "cursor",
                "status": "skipped",
                "checked_at": _ts(self.now, minutes_ago=4),
                "cycle_id": "USAGE-OLD0000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "Cooling provider cursor"):
            evaluate_usage_live_validation(
                run_status="accepted",
                scheduled_providers=["openai"],
                cooling_down_providers=["claude", "cursor"],
                latest_records=records,
                cycle_id="USAGE-PARTIAL001",
                started_at=self.started,
                now=self.now,
            )

    def test_accepted_cooling_age_uses_started_at_not_post_wait_now(self):
        """Waiting for scheduled probes must not expire accept-time cooling evidence."""
        started = self.now
        post_wait = self.now + timedelta(minutes=6)
        records = [
            {
                "provider": "openai",
                "status": "ok",
                "checked_at": _ts(post_wait, minutes_ago=0.5),
                "cycle_id": "USAGE-PARTIAL001",
            },
            {
                "provider": "claude",
                "status": "ok",
                # ~14 minutes old at started_at (still cooling); ~20 at post_wait.
                "checked_at": _ts(started, minutes_ago=14),
                "cycle_id": "USAGE-OLD0000001",
            },
            {
                "provider": "cursor",
                "status": "prompted",
                "checked_at": _ts(started, minutes_ago=14),
                "cycle_id": "USAGE-OLD0000002",
            },
        ]
        with self.assertRaisesRegex(UsageLiveValidationError, "outside the 15-minute"):
            assert_cooling_success_evidence(
                providers=["claude", "cursor"],
                latest_records=records,
                now=post_wait,
            )
        accepted = evaluate_usage_live_validation(
            run_status="accepted",
            scheduled_providers=["openai"],
            cooling_down_providers=["claude", "cursor"],
            latest_records=records,
            cycle_id="USAGE-PARTIAL001",
            started_at=started,
            now=post_wait,
        )
        self.assertEqual(len(accepted), 3)

    def test_scheduled_evidence_rejects_wrong_cycle_stale_and_unsuccessful(self):
        with self.assertRaisesRegex(UsageLiveValidationError, "Missing exact-cycle"):
            assert_scheduled_cycle_evidence(
                providers=["openai"],
                cycle_id="USAGE-PARTIAL001",
                records=[
                    {
                        "provider": "openai",
                        "status": "ok",
                        "checked_at": _ts(self.now, minutes_ago=0.5),
                        "cycle_id": "USAGE-OTHER0001",
                    }
                ],
                started_at=self.started,
            )

        with self.assertRaisesRegex(UsageLiveValidationError, "Missing exact-cycle"):
            assert_scheduled_cycle_evidence(
                providers=["openai"],
                cycle_id="USAGE-PARTIAL001",
                records=[
                    {
                        "provider": "openai",
                        "status": "ok",
                        "checked_at": _ts(self.started, minutes_ago=1),
                        "cycle_id": "USAGE-PARTIAL001",
                    }
                ],
                started_at=self.started,
            )

        with self.assertRaisesRegex(UsageLiveValidationError, "status is 'error'"):
            assert_scheduled_cycle_evidence(
                providers=["openai"],
                cycle_id="USAGE-PARTIAL001",
                records=[
                    {
                        "provider": "openai",
                        "status": "error",
                        "checked_at": _ts(self.now, minutes_ago=0.5),
                        "cycle_id": "USAGE-PARTIAL001",
                    }
                ],
                started_at=self.started,
            )


if __name__ == "__main__":
    unittest.main()
