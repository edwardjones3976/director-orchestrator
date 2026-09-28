# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 12:34 UTC | BEGIN]]
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

    def test_admission_counts_each_reservation_at_its_actual_charge(self):
        """Review 4111357778: a $0.50 charge on a $0.01 probe reservation plus $0.75 of work is $1.25 of exposure; the
        same reconciliation gates reserve(), inside its BEGIN IMMEDIATE write lock."""
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderDailySpendLedger(str(Path(directory) / "tasks.sqlite3"))
            probe, work = "a" * 64, "b" * 64
            self.assertEqual(ledger.reserve(provider=ProviderName.GROK, effect_key=probe, estimated_cost_usd=0.01,
                                            request_limit=10, budget_usd=1.0), (1, 0.01))
            ledger.record_charge(provider=ProviderName.GROK, effect_key=probe, actual_cost_usd=0.50)
            ledger.record_charge(provider=ProviderName.GROK, effect_key=probe, actual_cost_usd=0.10)  # never lowered
            self.assertEqual(ledger.exposure(ProviderName.GROK), (1, 0.5))
            # $0.50 + $0.75 exceeds the $1 cap: refused, where the estimates alone ($0.76) would have admitted it.
            with self.assertRaises(ProviderDailyLimitExceeded):
                ledger.reserve(provider=ProviderName.GROK, effect_key=work, estimated_cost_usd=0.75,
                               request_limit=10, budget_usd=1.0)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (1, 0.5))
            ledger.reserve(provider=ProviderName.GROK, effect_key=work, estimated_cost_usd=0.45,
                           request_limit=10, budget_usd=1.0)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (2, 0.95))
            # A charge the ledger never reserved (a gemini probe) is its own row at a zero reservation.
            ledger.record_charge(provider=ProviderName.GEMINI, effect_key="c" * 64, actual_cost_usd=0.3)
            self.assertEqual(ledger.exposure(ProviderName.GEMINI), (1, 0.3))
            for bad in (-0.1, float("nan"), float("inf"), True):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    ledger.record_charge(provider=ProviderName.GROK, effect_key=probe, actual_cost_usd=bad)

    def test_concurrent_reservations_cannot_both_pass_the_reconciled_cap(self):
        """Two admissions racing for the last $0.40 under a $1 cap (exposure $0.60): exactly one is admitted."""
        import threading
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "tasks.sqlite3")
            ledger = ProviderDailySpendLedger(path)
            ledger.reserve(provider=ProviderName.GROK, effect_key="a" * 64, estimated_cost_usd=0.01,
                           request_limit=100, budget_usd=1.0)
            ledger.record_charge(provider=ProviderName.GROK, effect_key="a" * 64, actual_cost_usd=0.60)
            barrier, outcomes = threading.Barrier(8), []

            def admit(index):
                barrier.wait()
                try:
                    ProviderDailySpendLedger(path).reserve(
                        provider=ProviderName.GROK, effect_key=format(index + 1, "x") * 64,
                        estimated_cost_usd=0.40, request_limit=100, budget_usd=1.0)
                    outcomes.append("admitted")
                except ProviderDailyLimitExceeded:
                    outcomes.append("refused")

            threads = [threading.Thread(target=admit, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(sorted(outcomes), ["admitted"] + ["refused"] * 7)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (2, 1.0))

    def test_an_existing_ledger_gains_the_charge_column_in_place(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "tasks.sqlite3")
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    """CREATE TABLE provider_daily_spend_reservations (
                        provider TEXT NOT NULL, effect_key TEXT NOT NULL, utc_day TEXT NOT NULL,
                        estimated_cost_usd REAL NOT NULL CHECK(estimated_cost_usd >= 0),
                        recorded_at TEXT NOT NULL, PRIMARY KEY(provider, effect_key))""")
                connection.execute(
                    "INSERT INTO provider_daily_spend_reservations VALUES ('grok', ?, ?, 0.2, 'x')",
                    ("a" * 64, datetime.now(timezone.utc).date().isoformat()))
            ledger = ProviderDailySpendLedger(path)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (1, 0.2))  # a NULL charge counts at its reservation

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
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 12:34 UTC | END]]
