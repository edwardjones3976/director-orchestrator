# [[Codex | GPT-5.6 | 2026-08-24 | 17:18 UTC | BEGIN]]
from __future__ import annotations

import json
import unittest

from director_orchestrator.approvals import assignment_approval_digest
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    canonical_opaque_identifier_digest,
    canonical_task_effect_digest,
)
from director_orchestrator.review_handoff import (
    bind_review_handoff,
    reconstruct_review_handoff,
)
from tests.test_team_operating_policy import (
    accepted_provider_outcome,
    bind_authenticated_director_acceptance_review,
    bind_verified_interaction,
    review_candidate_payload,
)


class ReviewHandoffEffectAccountingTests(unittest.TestCase):
    ACTORS = {
        "cursor": frozenset({"cursor-agent"}),
        "claude": frozenset({"claude-agent"}),
    }

    def fixture(self) -> tuple[TaskRecord, Assignment]:
        producer = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Implement one exact review candidate",
            acceptance=["Return an authenticated candidate artifact"],
            repository="example/demo",
            starting_ref="main",
            requires_approval=True,
            allow_draft_pr=True,
            estimated_cost_usd=0.5,
        )
        reviewer = Assignment(
            sequence=2,
            agent=ProviderName.CLAUDE,
            task="Reserved independent review slot",
            acceptance=["Review the exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[1],
            estimated_cost_usd=0.25,
        )
        record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-handoff-effects",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: implement and review",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Implement and independently review",
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                max_hops=2,
                max_revisions=1,
                assignments=[producer, reviewer],
                final_acceptance=["Exact authenticated review evidence remains current"],
            ),
            approved_assignments=[1],
            approved_assignment_digests={1: assignment_approval_digest(producer)},
        )
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="cursor-accounted-run",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(
                review_candidate_payload(),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        bind_verified_interaction(
            record,
            run,
            label="accounted-producer",
            account_effect=True,
        )
        sequence, bound = bind_review_handoff(
            record,
            producer,
            run,
            trusted_provider_actors=self.ACTORS,
        ) or (None, None)
        self.assertEqual(sequence, 2)
        self.assertIsNotNone(bound)
        record.review_handoff_digests[2] = json.loads(bound.task)["artifact_digest"]
        outcome = accepted_provider_outcome(record, run)
        bind_authenticated_director_acceptance_review(record, outcome)
        record.assignment_outcomes.append(outcome)
        record.provider_runs.append(run)
        return record, reviewer

    @staticmethod
    def clone(record: TaskRecord) -> TaskRecord:
        return TaskRecord.model_validate_json(record.model_dump_json())

    @staticmethod
    def effect_keys(record: TaskRecord) -> tuple[str, str]:
        outcome = record.assignment_outcomes[0]
        producer_event = next(
            item
            for item in reversed(record.provider_interaction_ledger)
            if item.interaction_id == outcome.interaction_id
        )
        review_event = next(
            item
            for item in reversed(record.provider_interaction_ledger)
            if item.interaction_id == outcome.director_review_interaction_id
        )
        assert producer_event.effect_key is not None
        assert review_event.effect_key is not None
        return producer_event.effect_key, review_event.effect_key

    @staticmethod
    def replace_effect(
        record: TaskRecord,
        *,
        effect_key: str,
        kind: str,
        **updates: object,
    ) -> None:
        index = next(
            index
            for index, item in enumerate(record.task_effect_ledger)
            if item.effect_key == effect_key and item.kind == kind
        )
        payload = record.task_effect_ledger[index].model_dump(
            mode="python", exclude={"effect_digest"}
        )
        payload.update(updates)
        payload["effect_digest"] = canonical_task_effect_digest(payload)
        record.task_effect_ledger[index] = TaskEffectEvidence.model_validate(payload)

    def reconstruct(self, record: TaskRecord, reviewer: Assignment) -> Assignment:
        return reconstruct_review_handoff(
            record,
            reviewer,
            trusted_provider_actors=self.ACTORS,
        )

    def test_fully_accounted_handoff_reconstructs(self):
        record, reviewer = self.fixture()

        reconstructed = self.reconstruct(record, reviewer)

        self.assertEqual(reconstructed.agent, ProviderName.CLAUDE)
        self.assertEqual(json.loads(reconstructed.task)["head_sha"], "b" * 40)

    def test_missing_or_duplicate_effect_pair_fails_closed(self):
        record, reviewer = self.fixture()
        producer_key, review_key = self.effect_keys(record)
        cases = (
            (producer_key, "provider_intent", "producer"),
            (producer_key, "provider_observation", "producer"),
            (review_key, "director_intent", "acceptance review"),
            (review_key, "director_observation", "acceptance review"),
        )
        for effect_key, kind, label in cases:
            with self.subTest(effect_key=effect_key, kind=kind):
                candidate = self.clone(record)
                target = next(
                    item
                    for item in candidate.task_effect_ledger
                    if item.effect_key == effect_key and item.kind == kind
                )
                candidate.task_effect_ledger.remove(target)
                with self.assertRaisesRegex(
                    ValueError,
                    f"{label} effect accounting is missing or duplicated",
                ):
                    self.reconstruct(candidate, candidate.plan.assignments[1])

                duplicate = self.clone(record)
                target = next(
                    item
                    for item in duplicate.task_effect_ledger
                    if item.effect_key == effect_key and item.kind == kind
                )
                duplicate.task_effect_ledger.append(target)
                with self.assertRaisesRegex(
                    ValueError,
                    f"{label} effect accounting is missing or duplicated",
                ):
                    self.reconstruct(duplicate, duplicate.plan.assignments[1])

    def test_wrong_effect_identity_or_outcome_fails_closed(self):
        record, reviewer = self.fixture()
        producer_key, review_key = self.effect_keys(record)
        mutations = (
            (
                producer_key,
                "provider_observation",
                {"run_id_digest": canonical_opaque_identifier_digest("wrong-run")},
                "producer",
            ),
            (
                producer_key,
                "provider_intent",
                {"assignment_sequence": 2},
                "producer",
            ),
            (
                producer_key,
                "provider_observation",
                {"plan_generation": record.plan_generation + 1},
                "producer",
            ),
            (
                producer_key,
                "provider_intent",
                {"provider": ProviderName.CLAUDE},
                "producer",
            ),
            (
                producer_key,
                "provider_observation",
                {"phase": "revision"},
                "producer",
            ),
            (
                review_key,
                "director_observation",
                {"run_id_digest": canonical_opaque_identifier_digest("wrong-review")},
                "acceptance review",
            ),
            (
                review_key,
                "director_intent",
                {"assignment_sequence": 1},
                "acceptance review",
            ),
            (
                review_key,
                "director_observation",
                {"plan_generation": record.plan_generation + 1},
                "acceptance review",
            ),
            (
                review_key,
                "director_intent",
                {"provider": ProviderName.CURSOR},
                "acceptance review",
            ),
            (
                review_key,
                "director_observation",
                {"outcome": "submitted"},
                "acceptance review",
            ),
        )
        for effect_key, kind, updates, label in mutations:
            with self.subTest(effect_key=effect_key, kind=kind, updates=updates):
                candidate = self.clone(record)
                self.replace_effect(
                    candidate,
                    effect_key=effect_key,
                    kind=kind,
                    **updates,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    f"{label} effect accounting changed",
                ):
                    self.reconstruct(candidate, candidate.plan.assignments[1])


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 17:18 UTC | END]]
