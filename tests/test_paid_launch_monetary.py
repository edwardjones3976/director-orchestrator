# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 02:52 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-07 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 09:20 UTC | BEGIN]]
"""Real-PostgreSQL monetary admission for the paid launch campaign.

Every test here runs against the sanctioned disposable lane; nothing is faked,
and no reservation is ever released by a later failure.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from director_orchestrator.antigravity_team import (
    CAMPAIGN_LIMIT_MICROUSD,
    CampaignAttemptAdmission,
    CampaignBudget,
    PrimaryMonetaryAdmissions,
    PrimaryProviderBudgetAuthority,
    ProviderBudget,
    SubscriptionPolicy,
    TeamAuthorizationError,
    team_ddl,
)
from director_orchestrator.provider_validation import (
    SCHEMA,
    PostgresValidationStore,
    ValidationBridgeService,
    ValidationError,
    ValidationRequest,
    digest,
)
from tests import test_entitlement_control_plane as fixtures
from tests.test_antigravity_team import signed
from tests.test_provider_validation import CANDIDATE, KEY, OWNER, TREE, binding, work_packet

PROJECT = "uppercut"
CAMPAIGN = "paid-launch-2026-09"
RESERVATION_MICROUSD = 60
# One hour after this pinned UTC instant is the next authoritative budget day.
DAY_ONE = datetime.now(timezone.utc).replace(hour=23, minute=30, second=0, microsecond=0)
DAY_TWO = DAY_ONE + timedelta(hours=1)


def campaign(**changes) -> CampaignBudget:
    values = dict(schema_version="primary-provider-campaign/v1", campaign_id=CAMPAIGN,
                  project_id=PROJECT, campaign_limit_microusd=100, owner_signature="0" * 64)
    values.update(changes)
    return signed(CampaignBudget(**values), fixtures.OWNER_SECRET)


def provider_budget(task_id, *, provider="claude", day=DAY_ONE, campaign_id=CAMPAIGN,
                    daily=5_000_000, task=1_000_000, **changes) -> ProviderBudget:
    values = dict(schema_version="primary-provider-budget/v1",
                  grant_digest=hashlib.sha256(f"{task_id}/{provider}/{day.date()}".encode()).hexdigest(),
                  provider_id=provider, project_id=PROJECT, task_id=task_id,
                  day=day.date().isoformat(), task_limit_microusd=task,
                  daily_limit_microusd=daily, attempt_reservation_microusd=RESERVATION_MICROUSD,
                  token_limit=1000, campaign_id=campaign_id, owner_signature="0" * 64)
    values.update(changes)
    return signed(ProviderBudget(**values), fixtures.OWNER_SECRET)


class CampaignContractTests(unittest.TestCase):
    def test_campaign_ceiling_is_ten_usd_and_cannot_be_signed_higher(self):
        self.assertEqual(CAMPAIGN_LIMIT_MICROUSD, 10_000_000)
        authority = PrimaryProviderBudgetAuthority.__new__(PrimaryProviderBudgetAuthority)
        authority.entitlements = type("E", (), {"_owner_authority_secret": fixtures.OWNER_SECRET})()
        with self.assertRaisesRegex(TeamAuthorizationError, "launch ceiling"):
            authority.register_campaign(None, campaign(campaign_limit_microusd=10_000_001))

    def test_trusted_subscription_billing_requires_explicit_owner_bounds(self):
        with self.assertRaises(Exception):
            provider_budget("task-sub", billing_mode="owner-bounded-subscription")
        with self.assertRaises(Exception):
            provider_budget("task-sub", subscription_policy=SubscriptionPolicy(
                schema_version="owner-bounded-subscription/v1", max_calls=1, max_turns=1,
                max_seconds=1, max_output_bytes=1))
        bounded = provider_budget("task-sub", billing_mode="owner-bounded-subscription",
            subscription_policy=SubscriptionPolicy(schema_version="owner-bounded-subscription/v1",
                max_calls=1, max_turns=2, max_seconds=60, max_output_bytes=4096))
        # A subscription lane has no vendor USD guarantee, and the application
        # admission cap is still a positive reservation, never zero.
        self.assertGreater(bounded.attempt_reservation_microusd, 0)


class PaidLaunchMonetaryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.EntitlementControlPlanePostgresTests.setUpClass()
        cls.validation = PostgresValidationStore(fixtures.TEST_DSN)
        cls.validation.bootstrap()

    @classmethod
    def tearDownClass(cls):
        fixtures.EntitlementControlPlanePostgresTests.tearDownClass()

    def setUp(self):
        fixtures.EntitlementControlPlanePostgresTests.setUp(self)
        self.pool = self.handle.pool
        self.clock = patch.object(self.store, "_clock", lambda: DAY_ONE)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.budgets = PrimaryProviderBudgetAuthority(self.store, primary_schema=self.schema)
        with self.pool.connection() as connection, connection.transaction():
            for sql in team_ddl(self.schema):
                connection.execute(sql)
            connection.execute(
                f"CREATE TABLE {self.schema}.director_primary_provider_attempt(attempt_digest text PRIMARY KEY)")
            self.budgets.register_campaign(connection, campaign())

    def tearDown(self):
        fixtures.EntitlementControlPlanePostgresTests.tearDown(self)

    # -- helpers -----------------------------------------------------------

    def register(self, budget: ProviderBudget) -> ProviderBudget:
        with self.pool.connection() as connection, connection.transaction():
            self.budgets.register(connection, budget)
        return budget

    def generation(self, budget: ProviderBudget, *, campaign_id=CAMPAIGN, provider="claude"):
        identifier = secrets.token_hex(32)
        lanes = {provider: {"binding": asdict(binding(provider)), "configuration_digest": "c" * 64,
                            "cost_units": 2, "work_packet": work_packet()}}
        if campaign_id is not None:
            lanes[provider]["budget_authority_digest"] = budget.grant_digest
        self.validation.create_generation(generation=identifier, candidate=CANDIDATE, tree=TREE,
            owner_token=OWNER, expires_at=int(time.time()) + 600, budget_units=10, lanes=lanes,
            campaign_id=campaign_id)
        return identifier

    def admission(self, campaign_id=CAMPAIGN):
        return CampaignAttemptAdmission(self.budgets, campaign_id)

    def admit(self, generation, *, provider="claude", monetary=True, campaign_id=CAMPAIGN):
        return self.validation.admit(
            ValidationRequest(generation=generation, provider=provider), OWNER,
            monetary_admission=self.admission(campaign_id) if monetary else None)

    def campaign_spend(self) -> int:
        with self.pool.connection() as connection:
            row = connection.execute(f"""SELECT COALESCE(sum(s.reserved_microusd),0) AS total
                FROM {self.schema}.director_primary_provider_spend s
                JOIN {self.schema}.director_primary_provider_campaign_member m
                    ON m.authority_digest=s.grant_digest""").fetchone()
        return int(row["total"])

    def attempts(self, generation) -> int:
        with self.validation.connect() as db:
            return db.execute(f"SELECT count(*) FROM {SCHEMA}.attempts WHERE generation=%s",
                              (generation,)).fetchone()[0]

    # -- tests -------------------------------------------------------------

    def test_two_generation_campaign_race_funds_exactly_one_attempt(self):
        """One campaign ceiling binds two independent validation generations."""
        first = self.register(provider_budget("campaign-task-a"))
        second = self.register(provider_budget("campaign-task-b"))
        generations = (self.generation(first), self.generation(second))

        def attempt(identifier):
            try:
                self.admit(identifier)
                return True
            except (ValidationError, TeamAuthorizationError):
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(attempt, generations))
        self.assertEqual(outcomes, [False, True])
        self.assertEqual(self.campaign_spend(), RESERVATION_MICROUSD)
        self.assertEqual(sum(self.attempts(identifier) for identifier in generations), 1)

    def test_day_and_task_ceilings_race_across_providers_of_one_project(self):
        """The daily project ceiling is shared by every provider and task."""
        wide = "daily-race-campaign"
        with self.pool.connection() as connection, connection.transaction():
            self.budgets.register_campaign(connection, campaign(campaign_id=wide, campaign_limit_microusd=1000))
        self.register(provider_budget("daily-task-a", provider="claude", daily=100, task=100, campaign_id=wide))
        self.register(provider_budget("daily-task-b", provider="openai", daily=100, task=100, campaign_id=wide))

        def reserve(spec):
            task_id, provider = spec
            digest = provider_budget(task_id, provider=provider).grant_digest
            try:
                with self.pool.connection() as connection, connection.transaction():
                    self.budgets.reserve(connection, authority_digest=digest, provider=provider,
                        task_id=task_id, attempt_digest="sha256:" + hashlib.sha256(task_id.encode()).hexdigest())
                return True
            except TeamAuthorizationError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(reserve, (("daily-task-a", "claude"), ("daily-task-b", "openai"))))
        self.assertEqual(outcomes, [False, True])
        self.assertEqual(self.campaign_spend(), RESERVATION_MICROUSD)

    def test_utc_day_rollover_resets_the_day_but_never_the_campaign(self):
        yesterday = self.register(provider_budget("rollover-task-a", daily=100, task=100))
        with self.pool.connection() as connection, connection.transaction():
            self.budgets.reserve(connection, authority_digest=yesterday.grant_digest,
                provider="claude", task_id="rollover-task-a", attempt_digest="sha256:" + "1" * 64)
        with patch.object(self.store, "_clock", lambda: DAY_TWO):
            self.assertNotEqual(DAY_ONE.date(), DAY_TWO.date())
            tomorrow = self.register(provider_budget("rollover-task-b", day=DAY_TWO, daily=100, task=100))
            with self.assertRaisesRegex(TeamAuthorizationError, "campaign monetary authority exhausted"):
                with self.pool.connection() as connection, connection.transaction():
                    self.budgets.reserve(connection, authority_digest=tomorrow.grant_digest,
                        provider="claude", task_id="rollover-task-b", attempt_digest="sha256:" + "2" * 64)
            # The stale day is refused outright rather than charged twice.
            with self.assertRaisesRegex(TeamAuthorizationError, "binding expired or changed"):
                with self.pool.connection() as connection, connection.transaction():
                    self.budgets.reserve(connection, authority_digest=yesterday.grant_digest,
                        provider="claude", task_id="rollover-task-a", attempt_digest="sha256:" + "3" * 64)
        self.assertEqual(self.campaign_spend(), RESERVATION_MICROUSD)

    def test_resolver_uses_authority_utc_day_in_non_utc_session_and_rechecks_rollover(self):
        midnight = datetime(2030, 1, 2, 0, 30, tzinfo=timezone.utc)
        previous = midnight - timedelta(hours=1)
        yesterday = self.register(provider_budget("midnight-task", day=previous))
        today = self.register(provider_budget("midnight-task", day=midnight))
        self.register(provider_budget("stale-only-task", day=previous))
        packet = {"task_id": "midnight-task", "project": PROJECT}
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL TIME ZONE 'America/Los_Angeles'")
            local_day = connection.execute(
                "SELECT (%s::timestamptz)::date AS day", (midnight,)
            ).fetchone()["day"]
            self.assertEqual(local_day, previous.date())
            self.assertNotEqual(local_day, midnight.date())

            class SessionPool:
                @contextmanager
                def connection(self):
                    yield connection

            resolver = PrimaryMonetaryAdmissions(self.budgets, pool=SessionPool())
            with patch.object(self.store, "_clock", lambda: previous):
                stale = resolver.admissions("claude", packet)["budget_admission"]
                self.assertEqual(stale.authority_digest, yesterday.grant_digest)
            # The authority clock may report a non-UTC offset for the same instant.
            offset_midnight = midnight.astimezone(timezone(timedelta(hours=-8)))
            with patch.object(self.store, "_clock", lambda: offset_midnight):
                current = resolver.admissions("claude", packet)["budget_admission"]
                self.assertEqual(current.authority_digest, today.grant_digest)
                with self.assertRaisesRegex(TeamAuthorizationError, "one authoritative daily task budget"):
                    resolver.admissions("claude", {"task_id": "stale-only-task", "project": PROJECT})
                with self.assertRaisesRegex(TeamAuthorizationError, "binding expired or changed"):
                    stale.admit(connection, task_id="midnight-task", provider="claude",
                                attempt_digest="sha256:" + "7" * 64)
                current.admit(connection, task_id="midnight-task", provider="claude",
                              attempt_digest="sha256:" + "8" * 64)
        self.assertEqual(self.campaign_spend(), RESERVATION_MICROUSD)

    def test_restart_after_ambiguity_retains_the_money_and_refuses_redispatch(self):
        budget = self.register(provider_budget("ambiguous-task"))
        identifier = self.generation(budget)
        admitted = self.admit(identifier)
        self.validation.claim_execution(admitted)
        self.validation.fail(admitted["reservation"])  # process died mid-flight
        restarted = PostgresValidationStore(fixtures.TEST_DSN)
        with self.assertRaisesRegex(ValidationError, "already admitted"):
            restarted.admit(ValidationRequest(generation=identifier, provider="claude"), OWNER,
                            monetary_admission=self.admission())
        with self.validation.connect() as db:
            state = db.execute(f"SELECT state FROM {SCHEMA}.attempts WHERE reservation=%s",
                               (admitted["reservation"],)).fetchone()[0]
        self.assertEqual(state, "ambiguous")
        # Ambiguity costs money: nothing refunds the reservation on restart.
        self.assertEqual(self.campaign_spend(), RESERVATION_MICROUSD)

    def test_revoked_generation_and_wrong_identity_reserve_nothing(self):
        budget = self.register(provider_budget("revoked-task"))
        identifier = self.generation(budget)
        with self.validation.connect() as db:
            db.execute(f"UPDATE {SCHEMA}.generations SET revoked=true WHERE generation=%s", (identifier,))
        with self.assertRaisesRegex(ValidationError, "unauthorized or expired"):
            self.admit(identifier)
        self.assertEqual(self.campaign_spend(), 0)

        other = self.generation(self.register(provider_budget("identity-task")))
        with self.assertRaisesRegex(TeamAuthorizationError, "another campaign"):
            self.admit(other, campaign_id="some-other-campaign")
        with self.assertRaisesRegex(ValidationError, "unauthorized or expired"):
            self.validation.admit(ValidationRequest(generation=other, provider="claude"), "wrong-owner-token-value",
                                  monetary_admission=self.admission())
        self.assertEqual(self.campaign_spend(), 0)
        self.assertEqual(self.attempts(other), 0)

    def test_wrong_provider_budget_cannot_fund_another_lane(self):
        claude = self.register(provider_budget("shared-task", provider="claude"))
        identifier = self.generation(claude, provider="openai")
        with self.assertRaisesRegex(TeamAuthorizationError, "binding expired or changed"):
            self.admit(identifier, provider="openai")
        self.assertEqual(self.campaign_spend(), 0)
        self.assertEqual(self.attempts(identifier), 0)

    def test_missing_monetary_hook_rejects_before_any_validation_io(self):
        budget = self.register(provider_budget("unfunded-task"))
        identifier = self.generation(budget)
        with self.assertRaisesRegex(ValidationError, "authoritative monetary admission"):
            self.admit(identifier, monetary=False)
        self.assertEqual(self.attempts(identifier), 0)
        self.assertEqual(self.campaign_spend(), 0)

    def test_unfunded_generation_cannot_borrow_monetary_authority(self):
        identifier = self.generation(None, campaign_id=None)
        with self.assertRaisesRegex(ValidationError, "unfunded generation"):
            self.admit(identifier)
        self.assertEqual(self.attempts(identifier), 0)

    def test_legacy_unbound_generation_cannot_reach_executor_or_consume_attempts(self):
        """Pre-campaign rows without campaign binding must not dispatch or spend attempts."""
        identifier = secrets.token_hex(32)
        lanes = {"claude": {"binding": asdict(binding()), "configuration_digest": "c" * 64,
                            "cost_units": 2, "work_packet": work_packet()}}
        with self.validation.connect() as db:
            db.execute(
                f"INSERT INTO {SCHEMA}.generations "
                "(generation,candidate,tree,owner_digest,expires_at,budget_units,configuration,campaign_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,NULL)",
                (identifier, CANDIDATE, TREE, digest(OWNER.encode()), int(time.time()) + 600,
                 10, json.dumps(lanes)))
        with self.assertRaisesRegex(ValidationError, "authoritative monetary admission"):
            self.validation.admit(ValidationRequest(generation=identifier, provider="claude"), OWNER)
        self.assertEqual(self.attempts(identifier), 0)
        service = ValidationBridgeService(
            store=self.validation, production_executor=object(), capture_key=KEY, owner_token=OWNER,
            account_files={}, monetary_admission=self.admission())
        with self.assertRaisesRegex(ValidationError, "unfunded generation"):
            service.run(ValidationRequest(generation=identifier, provider="claude"), OWNER)
        self.assertEqual(self.attempts(identifier), 0)

    def test_owner_bounded_subscription_admits_by_call_bound_not_by_price(self):
        policy = SubscriptionPolicy(schema_version="owner-bounded-subscription/v1", max_calls=1,
                                    max_turns=2, max_seconds=60, max_output_bytes=4096)
        budget = self.register(provider_budget("subscription-task", campaign_id=None,
            billing_mode="owner-bounded-subscription", subscription_policy=policy))
        with self.pool.connection() as connection, connection.transaction():
            self.budgets.reserve(connection, authority_digest=budget.grant_digest, provider="claude",
                task_id="subscription-task", attempt_digest="sha256:" + "4" * 64)
        with self.assertRaisesRegex(TeamAuthorizationError, "subscription call budget exhausted"):
            with self.pool.connection() as connection, connection.transaction():
                self.budgets.reserve(connection, authority_digest=budget.grant_digest, provider="claude",
                    task_id="subscription-task", attempt_digest="sha256:" + "5" * 64)
        with self.pool.connection() as connection:
            reserved = connection.execute(
                f"SELECT reserved_microusd FROM {self.schema}.director_primary_provider_spend "
                "WHERE attempt_digest=%s", ("sha256:" + "4" * 64,)).fetchone()["reserved_microusd"]
        # Vendor billing is not modelled; the application cap is still charged.
        self.assertEqual(int(reserved), RESERVATION_MICROUSD)

    def test_campaign_membership_is_immutable_across_generations(self):
        budget = self.register(provider_budget("membership-task"))
        moved = signed(budget.model_copy(update={"campaign_id": "another-campaign"}), fixtures.OWNER_SECRET)
        with self.pool.connection() as connection, connection.transaction():
            self.budgets.register_campaign(connection, campaign(campaign_id="another-campaign"))
        with self.assertRaises(Exception):
            with self.pool.connection() as connection, connection.transaction():
                self.budgets.register(connection, moved)
        with self.pool.connection() as connection:
            row = connection.execute(
                f"""SELECT c.campaign_id FROM {self.schema}.director_primary_provider_campaign_member m
                JOIN {self.schema}.director_primary_provider_campaign c USING(campaign_digest)
                WHERE m.authority_digest=%s""", (budget.grant_digest,)).fetchone()
        self.assertEqual(row["campaign_id"], CAMPAIGN)

    def test_paid_primary_attempt_without_a_hook_never_reaches_a_provider(self):
        from tests.test_primary_workload import DIGEST, PostgresAdmissionTests
        from director_orchestrator.primary_parallel import ParallelDispatchError
        fixture = PostgresAdmissionTests()
        fixture.dsn = fixtures.TEST_DSN
        fixture.setUp()
        try:
            task, writer = fixture.task("claude")
            reservation = fixture.ready((task, writer))
            with self.assertRaisesRegex(ParallelDispatchError, "authoritative monetary admission"):
                fixture.parallel.reserve_provider_attempt(reservation=reservation, writer=writer,
                    attempt_ordinal=1, format_roster=(("model-work-packet-v1", "packet-boundary-adapter"),),
                    idempotency_key_digest=DIGEST, budget_evidence_digest=DIGEST, paid_execution=True)
            self.assertEqual(fixture.count("director_primary_provider_attempt"), 0)
            self.assertEqual(fixture.count("director_primary_provider_spend"), 0)
        finally:
            fixture.tearDown()

    def test_team_independent_resolver_requires_one_daily_task_budget(self):
        self.register(provider_budget("resolved-task"))
        resolver = PrimaryMonetaryAdmissions(self.budgets, pool=self.pool)
        packet = {"task_id": "resolved-task", "project": PROJECT}
        with self.assertRaisesRegex(TeamAuthorizationError, "one authoritative daily task budget"):
            resolver.admissions("claude", {"task_id": "absent-task", "project": PROJECT})
        admissions = resolver.admissions("claude", packet)
        self.assertEqual(set(admissions), {"budget_admission"})


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 09:20 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-07 | 09:14 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 02:52 UTC | END]]
