# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.models import (
    Assignment,
    AssignmentKind,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.productive_work import (
    ExternalOpenAIProductionAuthorization,
    ExternalOpenAIProductionEvidence,
    ExternalOwnerAuthorizationSource,
)
from director_orchestrator.store import TaskStore, WorkloadReservationConflict
from director_orchestrator.workload_policy import (
    DirectProductionProvider,
    Provider,
    WorkSize,
)


class ExternalOpenAIProductiveWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "director.sqlite3")
        self.store = TaskStore(self.path)
        self.task = self._task("DIR-EXTERNAL-OPENAI")
        self.store.create(self.task)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _task(task_id: str) -> TaskRecord:
        return TaskRecord(
            task_id=task_id,
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id=f"evt-{task_id}",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="Authorize one exact external OpenAI candidate.",
            ),
            plan=DirectorPlan(
                project="external-openai-accounting",
                normalized_outcome="Account for one exact accepted candidate.",
                risk_level=RiskLevel.LOW,
                approval_required=True,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CURSOR,
                        kind=AssignmentKind.EXACT_SHA_REVIEW,
                        workload_size=WorkSize.SIMPLE,
                        effort_points=1,
                        task="Independently review the exact candidate.",
                        acceptance=["Return ACCEPT only for the exact candidate."],
                    )
                ],
                final_acceptance=["All exact evidence is durable."],
                workload_policy_version="v1",
            ),
            plan_generation=1,
        )

    def authorization(
        self, **changes: object
    ) -> ExternalOpenAIProductionAuthorization:
        values: dict[str, object] = {
            "authorization_id": "owner-auth-1",
            "owner_authorization_digest": "a" * 64,
            "owner_user_id_digest": "b" * 64,
            "source": ExternalOwnerAuthorizationSource.SLACK_COMMAND,
            "task_id": self.task.task_id,
            "plan_generation": 1,
            "assignment_sequence": 1,
            "repository": "edwardjones3976/director-orchestrator",
            "base_sha": "b" * 40,
            "candidate_head_sha": "c" * 40,
            "candidate_digest": None,
            "producer": DirectProductionProvider.EXTERNAL_OPENAI,
            "effort_units": 5,
            "reviewer_provider": Provider.CURSOR,
        }
        values.update(changes)
        return ExternalOpenAIProductionAuthorization(**values)  # type: ignore[arg-type]

    def evidence(
        self, **changes: object
    ) -> ExternalOpenAIProductionEvidence:
        values: dict[str, object] = {
            "replay_id": "external-openai-run-1",
            "authorization_id": "owner-auth-1",
            "owner_authorization_digest": "a" * 64,
            "task_id": self.task.task_id,
            "plan_generation": 1,
            "assignment_sequence": 1,
            "repository": "edwardjones3976/director-orchestrator",
            "base_sha": "b" * 40,
            "candidate_head_sha": "c" * 40,
            "candidate_digest": None,
            "producer": DirectProductionProvider.EXTERNAL_OPENAI,
            "effort_units": 5,
            "reviewer_provider": Provider.CURSOR,
            "producer_event_digest": "d" * 64,
            "producer_result_digest": "e" * 64,
            "acceptance_event_digest": "f" * 64,
            "acceptance_result_digest": "1" * 64,
            "ci_evidence_digest": "2" * 64,
            "security_evidence_digest": "3" * 64,
            "review_verdict": "ACCEPT",
            "ci_passed": True,
            "security_cleared": True,
        }
        values.update(changes)
        return ExternalOpenAIProductionEvidence(**values)  # type: ignore[arg-type]

    def test_exact_authorization_and_credit_replays_are_idempotent(self) -> None:
        authorization = self.authorization()
        evidence = self.evidence()

        self.assertTrue(
            self.store.record_external_openai_production_authorization(authorization)
        )
        self.assertFalse(
            self.store.record_external_openai_production_authorization(authorization)
        )
        self.assertTrue(self.store.record_external_openai_production_credit(evidence))
        self.assertFalse(self.store.record_external_openai_production_credit(evidence))

        credits = self.store.list_recent_direct_production()
        self.assertEqual(len(credits), 1)
        self.assertEqual(
            credits[0].provider, DirectProductionProvider.EXTERNAL_OPENAI
        )
        self.assertEqual(credits[0].effort_units, 5)

    def test_changed_authorization_or_credit_replay_fails_closed(self) -> None:
        authorization = self.authorization()
        evidence = self.evidence()
        self.store.record_external_openai_production_authorization(authorization)
        self.store.record_external_openai_production_credit(evidence)

        with self.assertRaisesRegex(
            WorkloadReservationConflict, "authorization replay identity changed"
        ):
            self.store.record_external_openai_production_authorization(
                replace(authorization, effort_units=6)
            )
        with self.assertRaisesRegex(
            WorkloadReservationConflict, "production replay identity changed"
        ):
            self.store.record_external_openai_production_credit(
                replace(evidence, ci_evidence_digest="4" * 64)
            )
        with self.assertRaisesRegex(
            WorkloadReservationConflict, "production replay identity changed"
        ):
            self.store.record_external_openai_production_credit(
                replace(evidence, replay_id="external-openai-run-2")
            )

    def test_new_transport_ids_cannot_double_credit_the_same_productive_unit(
        self,
    ) -> None:
        authorization = self.authorization()
        evidence = self.evidence()
        self.store.record_external_openai_production_authorization(authorization)
        self.store.record_external_openai_production_credit(evidence)

        with self.assertRaisesRegex(
            WorkloadReservationConflict, "authorization replay identity changed"
        ):
            self.store.record_external_openai_production_authorization(
                replace(
                    authorization,
                    authorization_id="owner-auth-2",
                    owner_authorization_digest="5" * 64,
                )
            )
        with self.assertRaisesRegex(
            WorkloadReservationConflict, "production replay identity changed"
        ):
            self.store.record_external_openai_production_credit(
                replace(evidence, replay_id="external-openai-run-2")
            )
        self.assertEqual(len(self.store.list_recent_direct_production()), 1)

    def test_external_openai_candidate_share_does_not_block_transaction(self) -> None:
        authorization = self.authorization(effort_units=1)
        evidence = self.evidence(effort_units=1)
        self.store.record_external_openai_production_authorization(authorization)

        with self.store._connect() as connection:
            recorded_at = "2026-08-31T04:50:00+00:00"
            for index in range(19):
                provider = "external_openai" if index < 6 else "claude"
                key = f"seed-direct-{index}"
                digest = f"{index + 10:064x}"
                connection.execute(
                    """INSERT INTO direct_production_credits(
                        credit_key, task_id, plan_generation,
                        assignment_sequence, assignment_id, provider,
                        effort_units, provider_event_digest,
                        provider_result_digest, review_event_digest,
                        review_result_digest, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key,
                        self.task.task_id,
                        1,
                        1,
                        key,
                        provider,
                        1,
                        digest,
                        digest,
                        digest,
                        digest,
                        recorded_at,
                    ),
                )

        self.assertTrue(self.store.record_external_openai_production_credit(evidence))

        with self.store._connect() as connection:
            credit_count = connection.execute(
                "SELECT COUNT(*) AS count FROM direct_production_credits"
            ).fetchone()["count"]
            evidence_count = connection.execute(
                "SELECT COUNT(*) AS count FROM external_openai_production_evidence"
            ).fetchone()["count"]
        self.assertEqual(credit_count, 20)
        self.assertEqual(evidence_count, 1)

    def test_new_stale_authority_fails_but_completed_exact_replay_survives(self) -> None:
        authorization = self.authorization()
        evidence = self.evidence()
        self.store.record_external_openai_production_authorization(authorization)
        self.store.record_external_openai_production_credit(evidence)

        current = self.store.get(self.task.task_id)
        current.plan_generation = 2
        self.store.save(current)

        self.assertFalse(
            self.store.record_external_openai_production_authorization(authorization)
        )
        self.assertFalse(self.store.record_external_openai_production_credit(evidence))
        with self.assertRaisesRegex(
            WorkloadReservationConflict, "stale plan generation"
        ):
            self.store.record_external_openai_production_authorization(
                self.authorization(
                    authorization_id="owner-auth-stale",
                    owner_authorization_digest="5" * 64,
                )
            )

    def test_unaccepted_or_unverified_evidence_is_rejected_by_contract(self) -> None:
        invalid_evidence = (
            {"reviewer_provider": Provider.GEMINI},
            {"review_verdict": "CHANGES_REQUIRED"},
            {"ci_passed": False},
            {"security_cleared": False},
        )
        for changes in invalid_evidence:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence(**changes)
        with self.assertRaises(ValueError):
            self.authorization(reviewer_provider=Provider.OPENAI)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
