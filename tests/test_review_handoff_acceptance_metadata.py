# [[Codex | GPT-5.6 | 2026-08-24 | 17:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:10 UTC | BEGIN]]
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
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.review_handoff import (
    bind_review_handoff,
    reconstruct_review_handoff,
)
from tests.test_team_operating_policy import (
    accepted_provider_outcome,
    bind_verified_interaction,
    review_candidate_payload,
)


class ReviewHandoffAcceptanceMetadataTests(unittest.TestCase):
    ACTORS = {
        "cursor": frozenset({"cursor-agent"}),
        "claude": frozenset({"claude-agent"}),
    }

    def setUp(self) -> None:
        producer = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Implement on an isolated branch and create one draft PR",
            acceptance=["Return a review candidate artifact"],
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
            acceptance=["Review exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[1],
            estimated_cost_usd=0.25,
        )
        self.record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-handoff-metadata",
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
                final_acceptance=["Exact review, security, and CI remain current"],
            ),
            approved_assignments=[1],
            approved_assignment_digests={1: assignment_approval_digest(producer)},
        )
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="cursor-run-metadata",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(
                review_candidate_payload(),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        bind_verified_interaction(
            self.record,
            run,
            label="producer-metadata",
            account_effect=True,
        )
        sequence, bound = bind_review_handoff(
            self.record,
            producer,
            run,
            trusted_provider_actors=self.ACTORS,
        ) or (None, None)
        self.assertEqual(sequence, 2)
        self.assertIsNotNone(bound)
        handoff = json.loads(bound.task)
        self.record.review_handoff_digests[2] = handoff["artifact_digest"]
        self.record.assignment_outcomes.append(
            accepted_provider_outcome(self.record, run)
        )
        self.record.provider_runs.append(run)
        self.reviewer = next(
            item for item in self.record.plan.assignments if item.sequence == 2
        )

    def reconstruct(self) -> None:
        reconstruct_review_handoff(
            self.record,
            self.reviewer,
            trusted_provider_actors=self.ACTORS,
        )

    def test_marker_rejects_entirely_missing_director_acceptance_metadata(self):
        with self.assertRaisesRegex(
            ValueError,
            "lacks complete authenticated Director review metadata",
        ):
            self.reconstruct()

    def test_marker_rejects_every_partial_director_acceptance_metadata_shape(self):
        fields = (
            "director_review_interaction_id",
            "verified_director_review_event_digest",
            "director_review_result_digest",
        )
        for mask in range(1, 2 ** len(fields) - 1):
            with self.subTest(mask=mask):
                outcome = self.record.assignment_outcomes[0]
                for index, field in enumerate(fields):
                    setattr(outcome, field, "a" * 64 if mask & (1 << index) else None)
                with self.assertRaisesRegex(
                    ValueError,
                    "lacks complete authenticated Director review metadata",
                ):
                    self.reconstruct()


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 17:10 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:26 UTC | END]]
