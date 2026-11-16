import asyncio
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from director_orchestrator import main
from director_orchestrator.approvals import (
    StaleApproval, build_neutral_intake_proof, build_slack_intake_attestation,
    verify_neutral_intake_proof, verify_slack_intake_attestation,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    IntakeAuthentication, NeutralIntake, NeutralIntakeRequest, SlackIntake,
    TaskRecord, TaskStatus,
)
from director_orchestrator.primary_default import DefaultPrimaryApplication
from director_orchestrator.store import SlackIntakeReplayConflict, TaskStore


SECRET, SHARED = "g" * 32, "b" * 32


class NeutralIntakeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "intake.sqlite3")
        self.store = TaskStore(self.database)
        budget = main.DailyTaskBudgetLedger(self.database)
        self.settings = replace(
            main.settings, shared_secret=SHARED, slack_gateway_secret=SECRET,
            slack_owner_user_id="native-owner", allow_paid_runs=True,
            langgraph_primary_postgres_dsn="",
        )
        now = datetime.now(timezone.utc)
        self.intake = NeutralIntake(
            source_event_id="native-event", provider="workspace-provider",
            actor_id="native-owner", recipient_id="director-orchestrator",
            conversation_id="native-conversation", text="bounded request",
            received_at=now, deadline_at=now + timedelta(hours=1),
        )
        self.run = AsyncMock()
        for context in (
            patch.object(main, "settings", self.settings),
            patch.object(main, "store", self.store),
            patch.object(main, "daily_task_budget", budget),
            patch.object(main, "primary_default_activation", None),
            patch.object(main.engine, "run", self.run),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def request(self, intake=None, *, nonce="n" * 32, **proof_terms):
        intake = intake or self.intake
        return NeutralIntakeRequest(
            intake=intake, gateway_proof=build_neutral_intake_proof(
                SECRET, SHARED, intake, nonce=nonce, **proof_terms,
            ),
        )

    def post(self, request, **kwargs):
        return self.client.post(
            "/v2/intake", json=request.model_dump(mode="json"),
            headers={"Authorization": "Bearer " + SHARED}, **kwargs,
        )

    def counts(self):
        with sqlite3.connect(self.database) as connection:
            return tuple(connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                         for name in ("tasks", "slack_intake_claims", "paid_task_daily_budget"))

    def test_authenticated_request_persists_native_identity_and_hold(self):
        response = self.post(self.request())
        self.assertEqual(202, response.status_code, response.text)
        body = response.json()
        self.assertEqual("neutral_gateway_attested", body["intake_authentication"])
        self.assertEqual(self.intake.model_dump(mode="json"), body["intake"])
        self.assertEqual("OWNER_DECISION_REQUIRED", body["status"])
        self.assertIn("HOLD", body["message"])
        self.assertIn("owner=Claude Orchestrator", body["message"])
        self.assertIn("checkpoint=", body["message"])
        self.assertFalse(body["owner_approved"])
        self.assertEqual([], body["provider_interaction_ledger"])
        self.assertEqual((1, 1, 0), self.counts())
        self.run.assert_not_called()

    def test_exact_duplicate_after_restart_and_fresh_proof_reuses_original_task(self):
        request = self.request()
        first = self.post(request).json()
        with patch.object(main, "store", TaskStore(self.database)):
            replay = self.post(request)
            self.assertEqual(first, replay.json())
            fresh = request.model_copy(update={"gateway_proof": build_neutral_intake_proof(
                SECRET, SHARED, self.intake, nonce="f" * 32,
            )})
            self.assertEqual(first, self.post(fresh).json())
        stored = TaskStore(self.database).get(first["task_id"])
        self.assertIsInstance(stored.intake, NeutralIntake)
        self.assertEqual(self.intake.deadline_at, stored.intake.deadline_at)
        self.assertEqual((1, 2, 0), self.counts())
        self.run.assert_not_called()

    def test_valid_changed_body_same_event_conflicts(self):
        self.assertEqual(202, self.post(self.request()).status_code)
        for field, value in (("text", "changed"), ("conversation_id", "elsewhere"),
                             ("deadline_at", self.intake.deadline_at + timedelta(hours=1))):
            with self.subTest(field=field):
                altered = self.intake.model_copy(update={field: value})
                response = self.post(self.request(altered, nonce="c" * 32))
                self.assertEqual(409, response.status_code)
        self.assertEqual((1, 1, 0), self.counts())

    def test_nonce_reuse_on_different_event_conflicts(self):
        self.post(self.request())
        changed = self.intake.model_copy(update={"source_event_id": "other-event"})
        self.assertEqual(409, self.post(self.request(changed)).status_code)
        self.assertEqual((1, 1, 0), self.counts())

    def test_missing_proof_bearer_or_claimed_role_has_no_effect(self):
        payload = self.request().model_dump(mode="json")
        missing = dict(payload)
        del missing["gateway_proof"]
        for body in (missing, {**payload, "role": "owner"},
                     {**payload, "intake": {**payload["intake"], "role": "admin"}}):
            response = self.client.post("/v2/intake", json=body,
                                        headers={"Authorization": "Bearer " + SHARED})
            self.assertEqual(422, response.status_code)
        self.assertEqual(401, self.client.post("/v2/intake", json=payload).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_body_identity_and_recipient_tampering_has_no_effect(self):
        request = self.request()
        for field, value in (("text", "changed"), ("actor_id", "attacker"),
                             ("provider", "openai"), ("conversation_id", "elsewhere"),
                             ("source_event_id", "another"), ("recipient_id", "other")):
            with self.subTest(field=field):
                body = request.model_dump(mode="json")
                body["intake"][field] = value
                response = self.client.post("/v2/intake", json=body,
                                            headers={"Authorization": "Bearer " + SHARED})
                self.assertIn(response.status_code, (403, 422))
        self.assertEqual((0, 0, 0), self.counts())

    def test_signed_wrong_actor_is_not_authorized(self):
        request = self.request(self.intake.model_copy(update={"actor_id": "admin"}))
        self.assertEqual(403, self.post(request).status_code)
        with patch.object(main, "settings", replace(self.settings, slack_owner_user_id="")):
            self.assertEqual(403, self.post(self.request()).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_stale_future_and_invalid_signature_have_no_effect(self):
        now = int(datetime.now(timezone.utc).timestamp())
        for issued_at in (now - 3600, now + 3600):
            self.assertEqual(403, self.post(self.request(issued_at=issued_at)).status_code)
        request = self.request()
        request.gateway_proof.signature = "0" * 64
        self.assertEqual(403, self.post(request).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_malformed_proof_and_unconfigured_or_reused_gateway_key_fail_closed(self):
        payload = self.request().model_dump(mode="json")
        for field, value in (("nonce", "short"), ("issued_at", "123"),
                             ("signature", "malformed"), ("version", "slack-v1")):
            body = {**payload, "gateway_proof": {**payload["gateway_proof"], field: value}}
            response = self.client.post("/v2/intake", json=body,
                                        headers={"Authorization": "Bearer " + SHARED})
            self.assertEqual(422, response.status_code)
        for secret in ("", SHARED):
            with patch.object(main, "settings", replace(self.settings, slack_gateway_secret=secret)):
                self.assertEqual(403, self.post(self.request()).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_wrong_method_path_and_domain_cannot_reuse_proof(self):
        request = self.request()
        body_digest = hashlib.sha256(json.dumps(
            self.intake.model_dump(mode="json"), ensure_ascii=True,
            separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
        envelope = dict(version=request.gateway_proof.version, method="POST", path="/v2/intake",
                        issued_at=request.gateway_proof.issued_at, nonce=request.gateway_proof.nonce,
                        body_digest=body_digest)
        for field, value in (("method", "GET"), ("path", "/v1/intake"), ("version", "slack-intake-v1")):
            altered = {**envelope, field: value}
            signature = hmac.new(SECRET.encode(), json.dumps(
                altered, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
            ).encode(), hashlib.sha256).hexdigest()
            forged = request.model_copy(deep=True)
            forged.gateway_proof.signature = signature
            self.assertEqual(403, self.post(forged).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_independent_wire_signature_requires_method_and_path(self):
        payload = {
            "intake": self.intake.model_dump(mode="json"),
            "gateway_proof": {
                "version": "director-neutral-intake-v2",
                "issued_at": int(datetime.now(timezone.utc).timestamp()),
                "nonce": "w" * 32,
            },
        }
        body_digest = hashlib.sha256(json.dumps(
            payload["intake"], ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
        envelope = {**payload["gateway_proof"], "method": "POST",
                    "path": "/v2/intake", "body_digest": body_digest}
        for omitted in (("method",), ("path",), ("method", "path"), ()):
            with self.subTest(omitted=omitted):
                signed = {key: value for key, value in envelope.items() if key not in omitted}
                payload["gateway_proof"]["signature"] = hmac.new(
                    SECRET.encode(), json.dumps(signed, ensure_ascii=True,
                                               separators=(",", ":"), sort_keys=True).encode(),
                    hashlib.sha256,
                ).hexdigest()
                response = self.client.post(
                    "/v2/intake", json=payload,
                    headers={"Authorization": "Bearer " + SHARED},
                )
                self.assertEqual(403 if omitted else 202, response.status_code, response.text)
                self.assertEqual((0, 0, 0) if omitted else (1, 1, 0), self.counts())
        self.run.assert_not_called()

    def test_valid_proof_signed_with_reused_bearer_key_is_refused(self):
        request = self.request(nonce="k" * 32)
        body_digest = hashlib.sha256(json.dumps(
            request.intake.model_dump(mode="json"), ensure_ascii=True,
            separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
        envelope = dict(version="director-neutral-intake-v2", method="POST", path="/v2/intake",
                        issued_at=request.gateway_proof.issued_at,
                        nonce=request.gateway_proof.nonce, body_digest=body_digest)
        request.gateway_proof.signature = hmac.new(
            SHARED.encode(), json.dumps(envelope, ensure_ascii=True,
                                        separators=(",", ":"), sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        with patch.object(main, "settings", replace(self.settings, slack_gateway_secret=SHARED)):
            self.assertEqual(403, self.post(request).status_code)
        self.assertEqual((0, 0, 0), self.counts())
        self.run.assert_not_called()

    def test_same_event_id_from_two_providers_has_distinct_replay_identity(self):
        other = self.intake.model_copy(update={"provider": "other-workspace-provider"})
        requests = [self.request(), self.request(other, nonce="p" * 32)]
        responses = [self.post(request) for request in requests]
        self.assertEqual([202, 202], [response.status_code for response in responses])
        task_ids = [response.json()["task_id"] for response in responses]
        self.assertNotEqual(*task_ids)
        with patch.object(main, "store", TaskStore(self.database)):
            for request, task_id in zip(requests, task_ids):
                replay = self.post(request)
                self.assertEqual(202, replay.status_code, replay.text)
                self.assertEqual(task_id, replay.json()["task_id"])
                self.assertEqual(request.intake.provider, replay.json()["intake"]["provider"])
        self.assertEqual((2, 2, 0), self.counts())
        self.run.assert_not_called()

    def test_cross_transport_proofs_fail_in_both_directions(self):
        slack = SlackIntake(source_event_id="native-event", channel_id="channel",
                            thread_ts="1", user_id="native-owner", text=self.intake.text,
                            received_at=self.intake.received_at)
        signed = build_slack_intake_attestation(SECRET, SHARED, slack, nonce="n" * 32)
        neutral = self.request()
        original = neutral.gateway_proof.signature
        neutral.gateway_proof.signature = signed["X-Director-Intake-Signature"]
        self.assertEqual(403, self.post(neutral).status_code)
        with self.assertRaises(StaleApproval):
            verify_slack_intake_attestation(
                SECRET, SHARED, slack, expected_owner_user_id="native-owner",
                expected_channel_id="channel", issued_at=str(neutral.gateway_proof.issued_at),
                nonce="n" * 32, signature=original,
            )
        self.assertEqual((0, 0, 0), self.counts())

    def test_concurrent_deliveries_use_one_durable_task(self):
        request = self.request()
        nonce, digest = verify_neutral_intake_proof(
            SECRET, SHARED, request, expected_actor_id="native-owner",
        )
        stores = [TaskStore(self.database), TaskStore(self.database)]
        barrier = threading.Barrier(2)

        def claim(index):
            record = TaskRecord(task_id="candidate-" + str(index),
                                status=TaskStatus.OWNER_DECISION_REQUIRED, intake=self.intake,
                                intake_authentication=IntakeAuthentication.NEUTRAL_GATEWAY_ATTESTED)
            barrier.wait(timeout=5)
            return stores[index].create_signed_intake(record, nonce_digest=nonce, payload_digest=digest)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, (0, 1)))
        self.assertEqual(1, sum(created for _, created in results))
        self.assertEqual(1, len({record.task_id for record, _ in results}))
        self.assertEqual((1, 1, 0), self.counts())

    def test_concurrent_conflicting_deliveries_cannot_create_second_task(self):
        barrier = threading.Barrier(2)
        stores = [TaskStore(self.database), TaskStore(self.database)]

        def claim(index):
            intake = self.intake.model_copy(update={"text": "request-" + str(index)})
            request = self.request(intake)
            nonce, digest = verify_neutral_intake_proof(
                SECRET, SHARED, request, expected_actor_id="native-owner",
            )
            record = TaskRecord(task_id="conflict-" + str(index),
                                status=TaskStatus.OWNER_DECISION_REQUIRED, intake=intake,
                                intake_authentication=IntakeAuthentication.NEUTRAL_GATEWAY_ATTESTED)
            barrier.wait(timeout=5)
            try:
                return stores[index].create_signed_intake(record, nonce_digest=nonce, payload_digest=digest)[1]
            except SlackIntakeReplayConflict:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual([False, True], sorted(executor.map(claim, (0, 1))))
        self.assertEqual((1, 1, 0), self.counts())

    def test_slack_and_neutral_event_and_nonce_namespaces_remain_separate(self):
        request = self.request()
        first = self.post(request).json()
        slack = SlackIntake(source_event_id=self.intake.source_event_id, channel_id="channel",
                            thread_ts="1", user_id="native-owner", text=self.intake.text,
                            received_at=self.intake.received_at)
        signed = build_slack_intake_attestation(SECRET, SHARED, slack, nonce="n" * 32)
        nonce, digest = verify_slack_intake_attestation(
            SECRET, SHARED, slack, expected_owner_user_id="native-owner",
            expected_channel_id="channel", issued_at=signed["X-Director-Intake-Issued-At"],
            nonce="n" * 32, signature=signed["X-Director-Intake-Signature"],
        )
        stored, created = self.store.create_signed_slack_intake(
            TaskRecord(task_id="slack-task", status=TaskStatus.QUEUED, intake=slack,
                       intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED),
            nonce_digest=nonce, payload_digest=digest,
        )
        self.assertTrue(created)
        self.assertNotEqual(first["task_id"], stored.task_id)
        self.assertEqual(first, self.post(request).json())
        self.assertEqual((2, 2, 0), self.counts())

    def test_primary_fence_holds_before_admission_and_never_uses_legacy_fallback(self):
        activation = MagicMock()
        with patch.object(main, "primary_default_activation", activation):
            response = self.post(self.request())
            self.assertEqual(503, response.status_code)
            self.assertIn("HOLD", response.text)
            activation.application.create_intake.assert_not_called()
        with patch.object(main, "settings", replace(self.settings, langgraph_primary_postgres_dsn="configured")):
            self.assertEqual(503, self.post(self.request()).status_code)
        self.assertEqual((0, 0, 0), self.counts())

    def test_direct_primary_and_engine_recovery_do_not_dispatch_neutral_intake(self):
        with self.assertRaisesRegex(ValueError, "HOLD"):
            DefaultPrimaryApplication.create_intake(
                object(), self.intake,
                intake_authentication=IntakeAuthentication.NEUTRAL_GATEWAY_ATTESTED,
            )
        response = self.post(self.request())
        record = self.store.get(response.json()["task_id"])
        fake_engine = MagicMock()
        fake_engine.store = self.store
        asyncio.run(OrchestrationEngine._run_locked(fake_engine, record.task_id))
        fake_engine._settle_not_created_dispatches.assert_not_called()
        self.assertEqual(record, self.store.get(record.task_id))

    def test_limits_and_timezones_refuse_instead_of_truncating(self):
        for update in ({"text": "x" * 100_001}, {"received_at": datetime.now()},
                       {"deadline_at": self.intake.received_at}):
            with self.assertRaises(ValidationError):
                NeutralIntake.model_validate({**self.intake.model_dump(), **update})


if __name__ == "__main__":
    unittest.main()
