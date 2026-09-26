# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | BEGIN]]
# Additive: same-process authority, binding and reversed-time negatives only.
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | BEGIN]]
# Additive prospective baseline/after collector composition test only.
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]]
# Additive status-receipt correlation / zero-work test only.
# [[Claude | Sonnet 5 | 2026-09-21 | 22:05 UTC | BEGIN]]
# Fallback-author correction: concurrent-fixture import race only.
# [[Claude | Sonnet 5 | 2026-09-21 | 20:57 UTC | BEGIN]]
# Fallback-author correction: frozen/slotted store fixture only.
# [[Claude | Sonnet 5 | 2026-09-21 | 20:50 UTC | BEGIN]]
# Fallback-author corrections only; material implementation remains Cursor-authored.
# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]]
# Administrative attribution only; material implementation remains Cursor-authored.
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | BEGIN]]
from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from director_orchestrator.approvals import (
    build_slack_status_receipt_attestation,
)
from director_orchestrator.langgraph_postgres import PostgresControlPlaneError
from director_orchestrator.models import (
    IntakeAuthentication,
    SlackStatusReceipt,
    slack_status_text_digest,
)
from tests import test_primary_default as primary_fixtures
from tests import test_slack_intake_receipts as slack_fixtures


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()


class StatusReceiptPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for this zero-skip suite"
            )

    def setUp(self) -> None:
        self.fixture = primary_fixtures.DefaultPrimaryApplicationIntegrationTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.settings = replace(
            self.fixture.settings,
            langgraph_primary_postgres_schema=self.fixture.schema,
            langgraph_primary_new_intake_enabled=True,
            allow_paid_runs=True,
            slack_channel_id="CDEFAULT",
            slack_owner_user_id="UOWNER",
            slack_gateway_secret="synthetic-gateway-signing-secret-32-characters",
            shared_secret="synthetic-shared-secret",
            orchestrator_base_url="http://director.invalid",
        )
        self.activation = SimpleNamespace(
            handle=self.fixture.handle,
            coordinator=self.fixture.coordinator,
            application=self.fixture.application,
        )
        self.task = self.fixture.application.create_intake(
            self.fixture.intake(),
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
        )
        self._task_status = self.fixture.application.get_task(self.task.task_id).status

    def receipt(self, **updates) -> SlackStatusReceipt:
        base = SlackStatusReceipt(
            source_event_id="CDEFAULT:1720000099.000001",
            channel_id="CDEFAULT",
            thread_ts="1720000000.000001",
            user_id="UOWNER",
            task_id=self.task.task_id,
            text_digest=slack_status_text_digest("synthetic status update"),
        )
        return base.model_copy(update=updates)

    def headers(
        self, receipt: SlackStatusReceipt, *, settings=None
    ) -> dict[str, str]:
        active = settings or self.settings
        return {
            "Authorization": f"Bearer {active.shared_secret}",
            **build_slack_status_receipt_attestation(
                active.slack_gateway_secret,
                active.shared_secret,
                receipt,
            ),
        }

    def counts(self) -> dict[str, int]:
        schema = self.fixture.schema
        with self.fixture.handle.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT count(*) AS n FROM {schema}.director_primary_task_projection"
                )
                tasks = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"SELECT count(*) AS n FROM {schema}.director_primary_slack_status_receipt"
                )
                receipts = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"SELECT count(*) AS n FROM {schema}.director_primary_provider_attempt"
                )
                attempts = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""
                    SELECT count(*) AS n
                    FROM {schema}.director_primary_workload_assignment
                    """
                )
                assignments = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""
                    SELECT count(*) AS n
                    FROM {schema}.director_primary_provider_attempt
                    WHERE status = 'STARTED'
                    """
                )
                started_attempts = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""
                    SELECT count(*) AS n
                    FROM {schema}.director_primary_budget
                    """
                )
                budget_rows = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(budget_limit_units), 0) AS limit_units,
                        COALESCE(SUM(budget_reserved_units), 0) AS reserved_units
                    FROM {schema}.director_primary_budget
                    """
                )
                budget_totals = cursor.fetchone()
                budget_limit_units = int(budget_totals["limit_units"])
                budget_reserved_units = int(budget_totals["reserved_units"])
        return {
            "tasks": tasks,
            "receipts": receipts,
            "attempts": attempts,
            "assignments": assignments,
            "started_attempts": started_attempts,
            "budget_rows": budget_rows,
            "budget_limit_units": budget_limit_units,
            "budget_reserved_units": budget_reserved_units,
        }

    @contextmanager
    def open_api(self, *, settings=None, activation=None):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(
            patch.dict(os.environ, {"SQLITE_PATH": directory + "/legacy.db"}, clear=True)
        )
        sys.modules.pop("director_orchestrator.main", None)
        main = importlib.import_module("director_orchestrator.main")
        stack.enter_context(patch.object(main, "settings", settings or self.settings))
        stack.enter_context(
            patch.object(
                main,
                "primary_default_activation",
                activation or self.activation,
            )
        )
        yield TestClient(main.app)

    def assert_no_work_side_effects(self, before: dict[str, int]) -> None:
        after = self.counts()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(before["assignments"], after["assignments"])
        self.assertEqual(before["attempts"], after["attempts"])
        self.assertEqual(before["started_attempts"], after["started_attempts"])
        self.assertEqual(before["budget_rows"], after["budget_rows"])
        self.assertEqual(before["budget_limit_units"], after["budget_limit_units"])
        self.assertEqual(before["budget_reserved_units"], after["budget_reserved_units"])
        record = self.fixture.application.get_task(self.task.task_id)
        self.assertEqual(self._task_status, record.status)

    def test_binding_lookup_and_receipt_without_side_effects(self) -> None:
        before = self.counts()
        with self.open_api() as api:
            binding = api.get(
                "/v1/slack-thread-bindings",
                headers={"Authorization": f"Bearer {self.settings.shared_secret}"},
                params={"channel_id": "CDEFAULT", "thread_ts": "1720000000.000001"},
            )
            self.assertEqual(200, binding.status_code, binding.text)
            self.assertEqual(self.task.task_id, binding.json()["task_id"])

            receipt = self.receipt()
            first = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertEqual(200, first.status_code, first.text)
            self.assertFalse(first.json()["deduplicated"])
            replay = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertTrue(replay.json()["deduplicated"])
            self.assertEqual(first.json()["receipt_id"], replay.json()["receipt_id"])
            self.assertEqual(first.json()["created_at"], replay.json()["created_at"])
        self.assert_no_work_side_effects(before)
        self.assertEqual(before["receipts"] + 1, self.counts()["receipts"])

    def test_status_receipt_correlates_through_the_readback_with_a_measured_zero_work_delta(self) -> None:
        # Prospective zero-work evidence: a before-baseline from the authoritative
        # tables, one signed status submission plus its replay, then an after-state
        # read through the opt-in READ ONLY readback. StatusReceiptRecord itself
        # carries no counters, so the delta is measured here, never inferred.
        from director_orchestrator.models import QualificationEvidenceReadback

        before = self.counts()
        with self.open_api() as api:
            receipt = self.receipt()
            first = api.post("/v1/status-receipts", headers=self.headers(receipt),
                             json=receipt.model_dump(mode="json"))
            self.assertEqual(200, first.status_code, first.text)
            replay = api.post("/v1/status-receipts", headers=self.headers(receipt),
                              json=receipt.model_dump(mode="json"))
            self.assertEqual(200, replay.status_code, replay.text)
            self.assertTrue(replay.json()["deduplicated"])
            self.assertEqual(first.json()["receipt_id"], replay.json()["receipt_id"])
            readback = api.get(f"/v1/tasks/{self.task.task_id}",
                               headers={"Authorization": f"Bearer {self.settings.shared_secret}"},
                               params={"include_qualification_evidence": "true"})
            self.assertEqual(200, readback.status_code, readback.text)
            evidence = QualificationEvidenceReadback.model_validate(readback.json()["qualification_evidence"])
            plain = api.get(f"/v1/tasks/{self.task.task_id}",
                            headers={"Authorization": f"Bearer {self.settings.shared_secret}"})
        after = self.counts()
        self.assert_no_work_side_effects(before)
        # Exactly one durable, deduplicated receipt, bound to this task and source event.
        self.assertEqual(before["receipts"] + 1, after["receipts"])
        self.assertEqual([first.json()["receipt_id"]], [row.receipt_id for row in evidence.status_receipts])
        self.assertEqual([receipt.source_event_id], [row.source_event_id for row in evidence.status_receipts])
        self.assertEqual(self.task.task_id, evidence.task_id)
        self.assertEqual(readback.headers["X-Primary-Projection-Digest"], evidence.projection_digest)
        # Zero new tasks/assignments/attempts, measured from the same authoritative tables.
        self.assertEqual((before["tasks"], before["assignments"], before["attempts"]),
                         (after["tasks"], after["assignments"], after["attempts"]))
        self.assertEqual([], evidence.provider_attempts)
        self.assertTrue(evidence.complete)
        # The readback did not touch the ordinary record or its persisted digest.
        self.assertNotIn("qualification_evidence", plain.json())
        self.assertEqual(plain.headers["X-Primary-Projection-Digest"], readback.headers["X-Primary-Projection-Digest"])

    def test_prospective_baseline_and_after_readback_prove_zero_work_through_the_collector(self) -> None:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "offline_slack_trial_collector_status",
            Path(__file__).resolve().parents[1] / "scripts/collect-offline-slack-trials.py")
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        auth = {"Authorization": f"Bearer {self.settings.shared_secret}"}
        query = {"include_qualification_evidence": "true"}
        with self.open_api() as api:
            before = api.get(f"/v1/tasks/{self.task.task_id}", headers=auth, params=query)
            self.assertEqual(200, before.status_code, before.text)
            baseline = before.json()["qualification_evidence"]
            receipt = self.receipt()
            first = api.post("/v1/status-receipts", headers=self.headers(receipt), json=receipt.model_dump(mode="json"))
            self.assertEqual(200, first.status_code, first.text)
            replay = api.post("/v1/status-receipts", headers=self.headers(receipt), json=receipt.model_dump(mode="json"))
            self.assertTrue(replay.json()["deduplicated"])
            after = api.get(f"/v1/tasks/{self.task.task_id}", headers=auth, params=query)
            self.assertEqual(200, after.status_code, after.text)
        receipt_id = first.json()["receipt_id"]
        # Same-process authenticated reads on both sides: the same synthetic
        # runtime/configuration binding is declared for before and after.
        runtime = {"container_id": "c", "image_id": "sha256:" + "a" * 64, "runtime_head": "b" * 40,
                   "started_at": None, "offline_configured": True, "paid_configured": False}
        binding = collector.revision_binding(runtime, {"ALLOW_PAID_RUNS": "false", "INTAKE_ALLOCATION_PROFILE": "engineering"})

        def run_collector(**options):
            return collector.summarize(
                self.task.intake.thread_ts, self.task.task_id, after.json(), [], [], True,
                "UOWNER", "UBOT", dict(after.headers), "CDEFAULT", **options)["qualification_evidence"]

        evidence = run_collector(baseline=baseline, receipt_id=receipt_id,
                                 baseline_authority=collector.SAME_PROCESS_AUTHORITY,
                                 before_binding=binding, after_binding=binding)
        self.assertEqual("collected", evidence["readback"]["state"], evidence["readback"]["reason"])
        status = evidence["status_receipts"]
        self.assertTrue(status["baseline"]["proof"], status["baseline"])
        self.assertEqual((0, 0, 0, 1), (status["tasks_created"], status["assignments_created"],
                                        status["provider_attempts_created"], status["deduplicated_receipts"]))
        self.assertEqual([receipt_id], evidence["chain"]["status_receipt_ids"])
        observed = status["baseline"]["observed_delta"]
        self.assertLess(observed["baseline_captured_at"], observed["receipt_created_at"])
        self.assertLessEqual(observed["receipt_created_at"], observed["after_captured_at"])
        row = {**collector.summarize(
            self.task.intake.thread_ts, self.task.task_id, after.json(), [], [], True,
            "UOWNER", "UBOT", dict(after.headers), "CDEFAULT", baseline=baseline, receipt_id=receipt_id,
            baseline_authority=collector.SAME_PROCESS_AUTHORITY, before_binding=binding, after_binding=binding)}
        # A caller-supplied file with identical content is only an observed delta.
        filed = run_collector(baseline=baseline, receipt_id=receipt_id, before_binding=binding, after_binding=binding)
        self.assertIsNone(filed["status_receipts"]["baseline"]["proof"])
        self.assertIsNone(filed["status_receipts"]["tasks_created"])
        self.assertEqual(observed, filed["status_receipts"]["baseline"]["observed_delta"])
        # Runtime/configuration drift, reversed order and after-only collection are not proof.
        drifted = collector.revision_binding(runtime, {"ALLOW_PAID_RUNS": "true"})
        self.assertIsNone(run_collector(baseline=baseline, receipt_id=receipt_id,
                                        baseline_authority=collector.SAME_PROCESS_AUTHORITY,
                                        before_binding=binding, after_binding=drifted)["status_receipts"]["baseline"]["observed_delta"])
        self.assertIsNone(run_collector(baseline=baseline, receipt_id=receipt_id,
                                        baseline_authority=collector.SAME_PROCESS_AUTHORITY,
                                        before_binding=collector.revision_binding({**runtime, "runtime_head": "c" * 40}, {}),
                                        after_binding=binding)["status_receipts"]["baseline"]["observed_delta"])
        self.assertIsNone(run_collector()["status_receipts"]["tasks_created"])
        late = run_collector(baseline=after.json()["qualification_evidence"], receipt_id=receipt_id,
                             baseline_authority=collector.SAME_PROCESS_AUTHORITY,
                             before_binding=binding, after_binding=binding)
        self.assertIsNone(late["status_receipts"]["baseline"]["proof"])
        self.assertIsNone(late["status_receipts"]["baseline"]["observed_delta"])
        collector.validate_observations({"schema": "director-slack-trial-observations/v1",
            "observed_at": collector.utc(), "expires_at": collector.utc(), "requested_trials": 1, "observed_trials": 1,
            "rubric_sha256": None, "collection_runtime_before": {"container_id": "c", "image_id": "sha256:" + "a" * 64,
                "runtime_head": "b" * 40, "started_at": None, "offline_configured": True, "paid_configured": False},
            "collection_runtime_after": None, "runtime_collection_binding_stable": None,
            "runtime_health_at_collection": {"paid_runs_enabled": None, "ambiguous_effect_count": None,
                "reconciliation_hold_reason": None, "primary_runtime_active": None},
            "collector_sha256": None, "read_only": True, "slack_writes": 0, "provider_dispatches": 0,
            "scorecard_mutations": 0, "qualified_run_count": None, "ten_consecutive_runs_verified": False,
            "native_source_reads": [], "trials": [{**row, "input_rubric_sha256": None,
                "rubric_evaluation": collector.evaluate_rubric(row, {"declared_at": "2020-01-01T00:00:00+00:00"}, "0" * 64),
                "source_errors": [], "runtime_collection_binding_stable": None}], "limitations": []})

    def test_replay_preserves_original_created_at_with_new_client_timestamp(self) -> None:
        receipt = self.receipt(
            received_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        )
        before = self.counts()
        with self.open_api() as api:
            first = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertEqual(200, first.status_code)
            later = receipt.model_copy(
                update={
                    "received_at": datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
                }
            )
            replay = api.post(
                "/v1/status-receipts",
                headers=self.headers(later),
                json=later.model_dump(mode="json"),
            )
        self.assertTrue(replay.json()["deduplicated"])
        self.assertEqual(first.json()["created_at"], replay.json()["created_at"])
        self.assertEqual(before["receipts"] + 1, self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

    def test_unknown_wrong_and_ambiguous_bindings_fail_closed(self) -> None:
        with self.open_api() as api:
            unknown = api.get(
                "/v1/slack-thread-bindings",
                headers={"Authorization": f"Bearer {self.settings.shared_secret}"},
                params={"channel_id": "CDEFAULT", "thread_ts": "1999999999.000001"},
            )
            self.assertEqual(404, unknown.status_code)

            other_task = self.fixture.application.create_intake(
                self.fixture.intake().model_copy(
                    update={
                        "source_event_id": "event-second-task",
                        "thread_ts": "1720000002.000001",
                    }
                ),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
            before_wrong = self.counts()
            wrong_task = self.receipt(task_id=other_task.task_id)
            wrong_response = api.post(
                "/v1/status-receipts",
                headers=self.headers(wrong_task),
                json=wrong_task.model_dump(mode="json"),
            )
            self.assertEqual(403, wrong_response.status_code)
            self.assertEqual(before_wrong["receipts"], self.counts()["receipts"])
            self.assert_no_work_side_effects(before_wrong)

            self.fixture.application.create_intake(
                self.fixture.intake().model_copy(
                    update={"source_event_id": "event-ambiguous-binding"}
                ),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
            ambiguous = api.get(
                "/v1/slack-thread-bindings",
                headers={"Authorization": f"Bearer {self.settings.shared_secret}"},
                params={"channel_id": "CDEFAULT", "thread_ts": "1720000000.000001"},
            )
            self.assertEqual(409, ambiguous.status_code)

            before_ambiguous = self.counts()
            ambiguous_receipt = self.receipt(
                source_event_id="CDEFAULT:1720000300.000001"
            )
            ambiguous_response = api.post(
                "/v1/status-receipts",
                headers=self.headers(ambiguous_receipt),
                json=ambiguous_receipt.model_dump(mode="json"),
            )
            self.assertEqual(409, ambiguous_response.status_code)
            self.assertEqual(
                before_ambiguous["receipts"], self.counts()["receipts"]
            )
            self.assert_no_work_side_effects(before_ambiguous)

    def test_missing_and_forged_authentication(self) -> None:
        receipt = self.receipt()
        body = receipt.model_dump(mode="json")
        dry_settings = replace(self.settings, allow_paid_runs=False)
        before = self.counts()
        for settings in (self.settings, dry_settings):
            with self.subTest(allow_paid_runs=settings.allow_paid_runs), self.open_api(
                settings=settings
            ) as api:
                partial = {"Authorization": f"Bearer {self.settings.shared_secret}"}
                self.assertEqual(
                    403,
                    api.post("/v1/status-receipts", headers=partial, json=body).status_code,
                )
                headers = self.headers(receipt)
                signature_name = next(
                    name for name in headers if "signature" in name.lower()
                )
                headers[signature_name] = "0" * 64
                self.assertEqual(
                    403,
                    api.post("/v1/status-receipts", headers=headers, json=body).status_code,
                )
        self.assertEqual(before["receipts"], self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

    def test_conflicting_replay_and_concurrent_delivery(self) -> None:
        with self.open_api() as api:
            receipt = self.receipt()
            first = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertEqual(200, first.status_code)
            conflict = receipt.model_copy(
                update={"text_digest": slack_status_text_digest("different prose")}
            )
            self.assertEqual(
                409,
                api.post(
                    "/v1/status-receipts",
                    headers=self.headers(conflict),
                    json=conflict.model_dump(mode="json"),
                ).status_code,
            )

            concurrent = self.receipt(source_event_id="CDEFAULT:1720000100.000001")

            def submit(_: int) -> bool:
                response = api.post(
                    "/v1/status-receipts",
                    headers=self.headers(concurrent),
                    json=concurrent.model_dump(mode="json"),
                )
                self.assertIn(response.status_code, (200, 409))
                if response.status_code == 200:
                    return response.json()["deduplicated"]
                return True

            with ThreadPoolExecutor(max_workers=4) as pool:
                dedup_flags = list(pool.map(submit, range(4)))
        self.assertEqual(1, dedup_flags.count(False))
        self.assertEqual(3, dedup_flags.count(True))

    def test_store_conflict_surfaces_as_http_conflict(self) -> None:
        receipt = self.receipt(source_event_id="CDEFAULT:1720000101.000001")
        patched_activation = SimpleNamespace(
            handle=self.activation.handle,
            coordinator=self.fixture.coordinator,
            application=self.fixture.application,
        )
        with patch.object(
            self.fixture.coordinator,
            "record_slack_status_receipt",
            side_effect=PostgresControlPlaneError(
                "status receipt source event was replayed with conflicting payload"
            ),
        ), self.open_api(activation=patched_activation) as api:
            response = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
        self.assertEqual(409, response.status_code)

    def test_digest_only_persistence(self) -> None:
        receipt = self.receipt(source_event_id="CDEFAULT:1720000102.000001")
        with self.open_api() as api:
            response = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
        self.assertEqual(200, response.status_code)
        schema = self.fixture.schema
        with self.fixture.handle.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT text_digest
                    FROM {schema}.director_primary_slack_status_receipt
                    WHERE source_event_id = %(source_event_id)s
                    """,
                    {"source_event_id": receipt.source_event_id},
                )
                row = cursor.fetchone()
        self.assertEqual(receipt.text_digest, row["text_digest"])
        self.assertTrue(row["text_digest"].startswith("sha256:"))

    def test_cross_channel_same_timestamp_isolated(self) -> None:
        other_intake = self.fixture.intake().model_copy(
            update={
                "source_event_id": "event-other-channel",
                "channel_id": "COTHER",
                "thread_ts": "1720000200.000001",
            }
        )
        other_task = self.fixture.application.create_intake(
            other_intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        other_settings = replace(self.settings, slack_channel_id="COTHER")
        same_ts = "1720000200.000099"
        first = self.receipt(
            source_event_id=f"CDEFAULT:{same_ts}",
            channel_id="CDEFAULT",
        )
        second = self.receipt(
            source_event_id=f"COTHER:{same_ts}",
            channel_id="COTHER",
            task_id=other_task.task_id,
            thread_ts=other_intake.thread_ts,
        )
        with self.open_api() as default_api:
            self.assertEqual(
                200,
                default_api.post(
                    "/v1/status-receipts",
                    headers=self.headers(first),
                    json=first.model_dump(mode="json"),
                ).status_code,
            )
            self.assertEqual(
                403,
                default_api.post(
                    "/v1/status-receipts",
                    headers=self.headers(second, settings=other_settings),
                    json=second.model_dump(mode="json"),
                ).status_code,
            )
        with self.open_api(settings=other_settings) as other_api:
            self.assertEqual(
                200,
                other_api.post(
                    "/v1/status-receipts",
                    headers=self.headers(second, settings=other_settings),
                    json=second.model_dump(mode="json"),
                ).status_code,
            )
        self.assertEqual(2, self.counts()["receipts"])

    def test_gateway_attestation_rejects_wrong_actor_thread_and_channel(self) -> None:
        before = self.counts()
        receipt = self.receipt()
        with self.open_api() as api:
            wrong_actor = receipt.model_copy(update={"user_id": "UOTHER"})
            self.assertEqual(
                403,
                api.post(
                    "/v1/status-receipts",
                    headers=self.headers(wrong_actor),
                    json=wrong_actor.model_dump(mode="json"),
                ).status_code,
            )
            wrong_thread = receipt.model_copy(
                update={"thread_ts": "1720000999.000001"}
            )
            self.assertEqual(
                404,
                api.post(
                    "/v1/status-receipts",
                    headers=self.headers(wrong_thread),
                    json=wrong_thread.model_dump(mode="json"),
                ).status_code,
            )
            wrong_channel = receipt.model_copy(update={"channel_id": "CWRONG"})
            self.assertEqual(
                403,
                api.post(
                    "/v1/status-receipts",
                    headers=self.headers(wrong_channel),
                    json=wrong_channel.model_dump(mode="json"),
                ).status_code,
            )
            partial_headers = {
                "Authorization": f"Bearer {self.settings.shared_secret}",
            }
            self.assertEqual(
                403,
                api.post(
                    "/v1/status-receipts",
                    headers=partial_headers,
                    json=receipt.model_dump(mode="json"),
                ).status_code,
            )
        self.assertEqual(before["receipts"], self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

    def test_durable_commit_survives_lost_client_response(self) -> None:
        """A gateway-side response loss after the durable write commits must
        not duplicate the receipt; redelivery must surface the original row."""

        receipt = self.receipt(source_event_id="CDEFAULT:1720000400.000001")
        application = self.fixture.application
        real_create = application.create_status_receipt
        captured: dict[str, object] = {}

        def lossy_create(payload):
            record = real_create(payload)
            captured["record"] = record
            raise httpx.ReadError(
                "synthetic gateway response loss after durable commit"
            )

        before = self.counts()
        with patch.object(
            application, "create_status_receipt", side_effect=lossy_create
        ), self.open_api() as api:
            lost = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
        self.assertEqual(503, lost.status_code)
        self.assertIn("record", captured)
        self.assertEqual(before["receipts"] + 1, self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

        with self.open_api() as api:
            redelivery = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
        self.assertEqual(200, redelivery.status_code, redelivery.text)
        body = redelivery.json()
        expected = captured["record"].model_dump(mode="json")
        self.assertTrue(body["deduplicated"])
        self.assertEqual(expected["receipt_id"], body["receipt_id"])
        self.assertEqual(expected["created_at"], body["created_at"])
        self.assertEqual(before["receipts"] + 1, self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

    def test_durable_store_failure_then_successful_retry(self) -> None:
        # The claim store is a frozen, slotted dataclass: instances refuse
        # attribute assignment, so the flaky method must be installed on the
        # class itself (real setattr/delattr on a class works normally) and
        # take the instance explicitly rather than patch.object(instance, ...).
        receipt = self.receipt(source_event_id="CDEFAULT:1720000401.000001")
        store = self.fixture.handle.claim_store
        store_cls = type(store)
        real_record = store_cls.record_primary_slack_status_receipt
        attempts = {"count": 0}

        def flaky_record(self_store, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("synthetic durable-store write failure")
            return real_record(self_store, **kwargs)

        before = self.counts()
        with patch.object(
            store_cls, "record_primary_slack_status_receipt", new=flaky_record
        ), self.open_api() as api:
            first = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertEqual(503, first.status_code)
            self.assertEqual(before["receipts"], self.counts()["receipts"])
            second = api.post(
                "/v1/status-receipts",
                headers=self.headers(receipt),
                json=receipt.model_dump(mode="json"),
            )
            self.assertEqual(200, second.status_code, second.text)
            self.assertFalse(second.json()["deduplicated"])
        self.assertEqual(2, attempts["count"])
        self.assertEqual(before["receipts"] + 1, self.counts()["receipts"])
        self.assert_no_work_side_effects(before)

    def test_concurrent_receipt_writes_linearize_on_binding_recheck(self) -> None:
        # `open_api` mutates os.environ / sys.modules and reimports `main`;
        # calling it from each worker races that shared process-global state
        # (KeyError inside importlib). Build the client once, outside the
        # pool, and only overlap the actual concurrent HTTP requests --
        # matching the established pattern in
        # test_conflicting_replay_and_concurrent_delivery.
        other_task = self.fixture.application.create_intake(
            self.fixture.intake().model_copy(
                update={
                    "source_event_id": "event-concurrent-binding",
                    "thread_ts": "1720000500.000001",
                }
            ),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        before = self.counts()
        barrier = threading.Barrier(2)

        def submit(api, source_suffix: str, task_id: str, thread_ts: str) -> int:
            payload = self.receipt(
                source_event_id=f"CDEFAULT:1720000501.{source_suffix}",
                task_id=task_id,
                thread_ts=thread_ts,
            )
            barrier.wait(timeout=5)
            response = api.post(
                "/v1/status-receipts",
                headers=self.headers(payload),
                json=payload.model_dump(mode="json"),
            )
            return response.status_code

        with self.open_api() as api:
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(
                    pool.map(
                        lambda item: submit(api, item[0], item[1], item[2]),
                        (
                            ("000010", self.task.task_id, "1720000000.000001"),
                            ("000011", other_task.task_id, "1720000500.000001"),
                        ),
                    )
                )
        self.assertEqual({200}, set(statuses))
        self.assertEqual(before["receipts"] + 2, self.counts()["receipts"])
        self.assert_no_work_side_effects(before)


class SlackGatewayStatusReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway_fixture = slack_fixtures.SlackIntakeReceiptTests()
        self.gateway_fixture.setUp()
        self.slack_bot = self.gateway_fixture.slack_bot
        self.event = {
            "channel": "CFIXTURE",
            "user": "UFIXTURE",
            "text": "<@UBOT> synthetic status only",
            "ts": "1788810001.234567",
            "thread_ts": "1788810000.000001",
        }
        self.client = Mock()
        self.slack_bot.settings.orchestrator_base_url = "http://director.invalid"

    def test_mention_routes_status_without_intake(self) -> None:
        binding = httpx.Response(
            200,
            json={
                "channel_id": "CFIXTURE",
                "thread_ts": "1788810000.000001",
                "task_id": "DIR-LG-ABCDEF0123",
            },
            request=httpx.Request("GET", "http://director.invalid/v1/slack-thread-bindings"),
        )
        receipt = httpx.Response(
            200,
            json={
                "receipt_id": "status-deadbeef",
                "task_id": "DIR-LG-ABCDEF0123",
                "deduplicated": False,
                "created_at": "2026-09-21T20:00:00+00:00",
            },
            request=httpx.Request("POST", "http://director.invalid/v1/status-receipts"),
        )

        with (
            patch.object(self.slack_bot.httpx, "get", return_value=binding) as get,
            patch.object(self.slack_bot.httpx, "post", return_value=receipt) as post,
        ):
            self.slack_bot.mention_handler(self.event, self.client)
        get.assert_called_once()
        post.assert_called_once()
        self.assertIn("/v1/status-receipts", post.call_args.args[0])
        self.assertNotIn("text", post.call_args.kwargs["json"])
        self.assertIn("text_digest", post.call_args.kwargs["json"])
        message = self.client.chat_postMessage.call_args.kwargs["text"]
        self.assertIn("STATUS RECORDED", message)

    def test_failed_status_write_releases_claim_for_redelivery(self) -> None:
        binding = httpx.Response(
            200,
            json={
                "channel_id": "CFIXTURE",
                "thread_ts": "1788810000.000001",
                "task_id": "DIR-LG-ABCDEF0123",
            },
            request=httpx.Request("GET", "http://director.invalid/v1/slack-thread-bindings"),
        )

        receipt = httpx.Response(
            200,
            json={
                "receipt_id": "status-deadbeef",
                "task_id": "DIR-LG-ABCDEF0123",
                "deduplicated": False,
                "created_at": "2026-09-21T20:00:00+00:00",
            },
            request=httpx.Request("POST", "http://director.invalid/v1/status-receipts"),
        )
        posts = [httpx.ConnectError("synthetic write failure"), receipt]

        def post_side_effect(*args, **kwargs):
            outcome = posts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            patch.object(self.slack_bot.httpx, "get", return_value=binding),
            patch.object(self.slack_bot.httpx, "post", side_effect=post_side_effect) as post,
        ):
            self.slack_bot.mention_handler(self.event, self.client)
            self.assertIn("BLOCKED", self.client.chat_postMessage.call_args.kwargs["text"])
            self.slack_bot.mention_handler(self.event, self.client)
        self.assertEqual(2, post.call_count)

    def test_intake_transport_failure_keeps_claim_and_suppresses_retry(self) -> None:
        intake_event = {
            **self.event,
            "text": "DIRECTOR synthetic fixture work order",
            "client_msg_id": "synthetic-intake-event",
        }
        self.gateway_fixture.reset_case()
        error = httpx.ConnectError("synthetic intake failure")
        with (
            patch.object(self.slack_bot.httpx, "post", side_effect=error) as post,
            patch.object(self.slack_bot.log, "warning"),
        ):
            self.slack_bot._forward(intake_event, self.client, require_prefix=True)
            self.slack_bot._forward(intake_event, self.client, require_prefix=True)
        post.assert_called_once()

    def test_command_boundary_still_uses_intake(self) -> None:
        event = {
            **self.event,
            "text": "<@UBOT> DIRECTOR synthetic work order",
            "client_msg_id": "synthetic-intake-event",
        }
        intake = httpx.Response(
            202,
            json={"task_id": "DIR-LG-INTAKE1234"},
            request=httpx.Request("POST", "http://director.invalid/v1/intake"),
        )
        with patch.object(self.slack_bot.httpx, "post", return_value=intake) as post:
            self.slack_bot.mention_handler(event, self.client)
        post.assert_called_once()
        self.assertIn("/v1/intake", post.call_args.args[0])

    def _overlap_delivery_fixtures(self):
        binding = httpx.Response(
            200,
            json={
                "channel_id": "CFIXTURE",
                "thread_ts": "1788810000.000001",
                "task_id": "DIR-LG-ABCDEF0123",
            },
            request=httpx.Request("GET", "http://director.invalid/v1/slack-thread-bindings"),
        )
        receipt = httpx.Response(
            200,
            json={
                "receipt_id": "status-deadbeef",
                "task_id": "DIR-LG-ABCDEF0123",
                "deduplicated": False,
                "created_at": "2026-09-21T20:00:00+00:00",
            },
            request=httpx.Request("POST", "http://director.invalid/v1/status-receipts"),
        )
        return binding, receipt

    def _assert_single_status_post(self, first_handler, second_handler, first_event, second_event) -> None:
        binding, receipt = self._overlap_delivery_fixtures()
        self.slack_bot._seen_events.clear()
        with (
            patch.object(self.slack_bot.httpx, "get", return_value=binding),
            patch.object(self.slack_bot.httpx, "post", return_value=receipt) as post,
        ):
            first_handler(first_event, self.client)
            second_handler(second_event, self.client)
        post.assert_called_once()
        self.assertIn("/v1/status-receipts", post.call_args.args[0])

    def test_overlap_handlers_deduplicate_on_canonical_source_event(self) -> None:
        mention_delivery = {**self.event, "client_msg_id": "mention-only-id"}
        message_delivery = {**self.event, "client_msg_id": "message-only-id"}
        for first, second in (
            (self.slack_bot.mention_handler, self.slack_bot.message_handler),
            (self.slack_bot.message_handler, self.slack_bot.mention_handler),
        ):
            with self.subTest(first=first.__name__, second=second.__name__):
                self._assert_single_status_post(
                    first,
                    second,
                    mention_delivery,
                    message_delivery,
                )

    def test_overlap_handlers_keep_intake_boundary_for_director_commands(self) -> None:
        command_event = {
            **self.event,
            "text": "<@UBOT> DIRECTOR synthetic work order",
            "client_msg_id": "command-overlap-id",
        }
        intake = httpx.Response(
            202,
            json={"task_id": "DIR-LG-INTAKE1234"},
            request=httpx.Request("POST", "http://director.invalid/v1/intake"),
        )
        self.slack_bot._seen_events.clear()
        with patch.object(self.slack_bot.httpx, "post", return_value=intake) as post:
            self.slack_bot.mention_handler(command_event, self.client)
            self.slack_bot.message_handler(command_event, self.client)
        post.assert_called_once()
        self.assertIn("/v1/intake", post.call_args.args[0])

    def test_wrong_actor_never_posts_status_receipt(self) -> None:
        event = {**self.event, "user": "UOTHER"}
        with patch.object(self.slack_bot.httpx, "post") as post:
            self.slack_bot.mention_handler(event, self.client)
        post.assert_not_called()

    def test_unknown_binding_then_legitimate_on_redelivery(self) -> None:
        missing = httpx.Response(
            404,
            request=httpx.Request("GET", "http://director.invalid/v1/slack-thread-bindings"),
        )
        binding = httpx.Response(
            200,
            json={
                "channel_id": "CFIXTURE",
                "thread_ts": "1788810000.000001",
                "task_id": "DIR-LG-ABCDEF0123",
            },
            request=httpx.Request("GET", "http://director.invalid/v1/slack-thread-bindings"),
        )
        receipt = httpx.Response(
            200,
            json={
                "receipt_id": "status-deadbeef",
                "task_id": "DIR-LG-ABCDEF0123",
                "deduplicated": False,
                "created_at": "2026-09-21T20:00:00+00:00",
            },
            request=httpx.Request("POST", "http://director.invalid/v1/status-receipts"),
        )
        self.slack_bot._seen_events.clear()
        with (
            patch.object(self.slack_bot.httpx, "get", side_effect=[missing, binding]) as get,
            patch.object(self.slack_bot.httpx, "post", return_value=receipt) as post,
        ):
            self.slack_bot.mention_handler(self.event, self.client)
            self.slack_bot.mention_handler(self.event, self.client)
        self.assertEqual(2, get.call_count)
        post.assert_called_once()

    def test_edited_and_deleted_events_are_ignored(self) -> None:
        binding = httpx.Response(
            200,
            json={"task_id": "DIR-LG-ABCDEF0123"},
            request=httpx.Request("GET", "http://director.invalid/bindings"),
        )
        for subtype in ("message_changed", "message_deleted"):
            with self.subTest(subtype=subtype):
                self.gateway_fixture.reset_case()
                event = {**self.event, "subtype": subtype}
                with (
                    patch.object(self.slack_bot.httpx, "get", return_value=binding),
                    patch.object(self.slack_bot.httpx, "post") as post,
                ):
                    self.slack_bot.mention_handler(event, self.client)
                post.assert_not_called()


class WorkIntakeCommandBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway_fixture = slack_fixtures.SlackIntakeReceiptTests()
        self.gateway_fixture.setUp()
        self.slack_bot = self.gateway_fixture.slack_bot

    def test_explicit_director_token_only(self) -> None:
        positive = (
            "DIRECTOR",
            "director plan this",
            "  DIRECTOR   work",
            "/director approve",
        )
        negative = (
            "",
            "DIRECTORIAL memo",
            "DIRECTORY listing",
            "please DIRECTOR later",
            "synthetic status only",
        )
        for text in positive:
            with self.subTest(text=text):
                self.assertTrue(self.slack_bot._is_work_intake_command(text))
        for text in negative:
            with self.subTest(text=text):
                self.assertFalse(self.slack_bot._is_work_intake_command(text))


if __name__ == "__main__":
    unittest.main()
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]]
# [[Claude | Sonnet 5 | 2026-09-21 | 20:50 UTC | END]]
# [[Claude | Sonnet 5 | 2026-09-21 | 20:57 UTC | END]]
# [[Claude | Sonnet 5 | 2026-09-21 | 22:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
