# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings, _positive_float
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
)
from director_orchestrator.policy import (
    MINIMUM_OPENAI_REVIEW_COST_USD,
    estimated_plan_cost_usd,
)
from director_orchestrator.providers import (
    ProviderDailyLimitExceeded,
    ProviderDailySpendLedger,
    ProviderReservationIdentityConflict,
)


class ProviderDailySpendLedgerTests(unittest.TestCase):
    def test_prospective_cost_includes_revision_dispatches_and_every_review(self):
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="bounded work",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=6,
            max_revisions=2,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CLAUDE,
                    task="Implement",
                    acceptance=["Tests pass"],
                    estimated_cost_usd=0.10,
                ),
                Assignment(
                    sequence=2,
                    agent=ProviderName.CURSOR,
                    task="Review",
                    acceptance=["Evidence returned"],
                    estimated_cost_usd=0.20,
                ),
            ],
            final_acceptance=["Accepted"],
        )

        self.assertAlmostEqual(
            estimated_plan_cost_usd(plan, openai_review_cost_usd=0.05),
            1.20,
        )

    def test_review_reservation_never_falls_below_the_deterministic_floor(self):
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="bounded work",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=6,
            max_revisions=2,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CLAUDE,
                    task="Implement",
                    acceptance=["Tests pass"],
                    estimated_cost_usd=0.10,
                ),
            ],
            final_acceptance=["Accepted"],
        )
        # One assignment x three attempts = three dispatches and three reviews.
        dispatches = 0.30

        # A zero or negative review estimate under-reserves by exactly the number
        # of review calls the plan will make, so the floor replaces it.
        for understated in (0.0, -1.0):
            with self.subTest(openai_review_cost_usd=understated):
                self.assertAlmostEqual(
                    estimated_plan_cost_usd(plan, openai_review_cost_usd=understated),
                    dispatches + 3 * MINIMUM_OPENAI_REVIEW_COST_USD,
                )

        # The default is the floor, and a higher operator estimate is preserved.
        self.assertAlmostEqual(
            estimated_plan_cost_usd(plan),
            dispatches + 3 * MINIMUM_OPENAI_REVIEW_COST_USD,
        )
        self.assertAlmostEqual(
            estimated_plan_cost_usd(plan, openai_review_cost_usd=0.05),
            dispatches + 3 * 0.05,
        )
        self.assertGreater(MINIMUM_OPENAI_REVIEW_COST_USD, 0)

    def test_the_configured_review_reservation_is_positive(self):
        # The default is at least the floor, and the setting is read through
        # `_positive_float`, so a deployment cannot configure zero or a negative
        # reservation without failing closed at configuration load.
        self.assertGreaterEqual(
            Settings().openai_review_estimated_cost_usd,
            MINIMUM_OPENAI_REVIEW_COST_USD,
        )
        for rejected in ("0", "0.0", "-0.01"):
            with self.subTest(value=rejected):
                with patch.dict(
                    os.environ, {"OPENAI_REVIEW_ESTIMATED_COST_USD": rejected}
                ):
                    with self.assertRaises(ValueError):
                        _positive_float("OPENAI_REVIEW_ESTIMATED_COST_USD", 0.01)

    def test_budget_enforcement_still_applies_with_the_floor(self):
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="bounded work",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=6,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CLAUDE,
                    task="Implement",
                    acceptance=["Tests pass"],
                    estimated_cost_usd=0.10,
                ),
            ],
            final_acceptance=["Accepted"],
        )
        # The floor raises the reserved envelope above a budget the bare dispatch
        # cost alone would have slipped under.
        self.assertGreater(estimated_plan_cost_usd(plan), 0.10)
        self.assertLess(estimated_plan_cost_usd(plan), 0.12)

    def test_reservation_is_durable_idempotent_and_provider_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "tasks.sqlite3")
            effect_key = hashlib.sha256(b"dispatch-one").hexdigest()
            first = ProviderDailySpendLedger(path)
            now = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)

            self.assertEqual(
                first.reserve(
                    provider=ProviderName.GEMINI,
                    effect_key=effect_key,
                    estimated_cost_usd=0.25,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                ),
                (1, 0.25),
            )
            restarted = ProviderDailySpendLedger(path)
            self.assertEqual(
                restarted.reserve(
                    provider=ProviderName.GEMINI,
                    effect_key=effect_key,
                    estimated_cost_usd=0.25,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                ),
                (1, 0.25),
            )
            self.assertEqual(
                restarted.reserve(
                    provider=ProviderName.PERPLEXITY,
                    effect_key=effect_key,
                    estimated_cost_usd=0.50,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                ),
                (1, 0.50),
            )

    def test_request_and_budget_ceilings_fail_closed_before_insertion(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderDailySpendLedger(str(Path(directory) / "tasks.sqlite3"))
            now = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
            first_key = hashlib.sha256(b"first").hexdigest()
            second_key = hashlib.sha256(b"second").hexdigest()
            third_key = hashlib.sha256(b"third").hexdigest()
            ledger.reserve(
                provider=ProviderName.GEMINI,
                effect_key=first_key,
                estimated_cost_usd=0.60,
                request_limit=2,
                budget_usd=1.0,
                now=now,
            )
            with self.assertRaisesRegex(ProviderDailyLimitExceeded, "budget"):
                ledger.reserve(
                    provider=ProviderName.GEMINI,
                    effect_key=second_key,
                    estimated_cost_usd=0.50,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                )
            self.assertEqual(
                ledger.reserve(
                    provider=ProviderName.GEMINI,
                    effect_key=second_key,
                    estimated_cost_usd=0.40,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                ),
                (2, 1.0),
            )
            with self.assertRaisesRegex(ProviderDailyLimitExceeded, "request limit"):
                ledger.reserve(
                    provider=ProviderName.GEMINI,
                    effect_key=third_key,
                    estimated_cost_usd=0,
                    request_limit=2,
                    budget_usd=1.0,
                    now=now,
                )

    def test_effect_key_accounting_mismatch_is_an_integrity_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderDailySpendLedger(str(Path(directory) / "tasks.sqlite3"))
            effect_key = hashlib.sha256(b"stable-effect").hexdigest()
            first_day = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
            ledger.reserve(
                provider=ProviderName.GEMINI,
                effect_key=effect_key,
                estimated_cost_usd=0.25,
                request_limit=2,
                budget_usd=1.0,
                now=first_day,
            )

            for changed_cost, changed_day in (
                (0.50, first_day),
                (0.25, datetime(2026, 8, 25, 12, tzinfo=timezone.utc)),
            ):
                with self.subTest(cost=changed_cost, day=changed_day.date()):
                    with self.assertRaises(ProviderReservationIdentityConflict) as caught:
                        ledger.reserve(
                            provider=ProviderName.GEMINI,
                            effect_key=effect_key,
                            estimated_cost_usd=changed_cost,
                            request_limit=2,
                            budget_usd=1.0,
                            now=changed_day,
                        )
                    self.assertNotIsInstance(caught.exception, ProviderDailyLimitExceeded)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
