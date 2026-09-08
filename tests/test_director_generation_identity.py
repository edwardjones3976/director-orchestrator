# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
"""Offline identity/replay tests; fake Directors perform no provider calls."""
from dataclasses import replace
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from director_orchestrator.approvals import record_task_effect_observation, reserve_task_effect
from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    CommunicationEvidenceFailure, DirectorIdentity, ProviderInteractionEvent,
    ProviderInteractionState, ProviderTransport, director_evidence_matches,
    project_interaction,
)
from director_orchestrator.models import (
    Assignment, DebateActorRole, ProviderName, SlackIntake, TaskRecord, TaskStatus,
)
from director_orchestrator.policy import preview_plan
from director_orchestrator.store import TaskStore


class DirectorGenerationIdentityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.settings = replace(Settings(), sqlite_path=str(Path(scratch.name) / 'tasks.sqlite3'))
        self.store = TaskStore(self.settings.sqlite_path)

    def engine(self, provider='openai', model='fixture-model'):
        fake = SimpleNamespace(
            provider=ProviderName(provider), model_name=model,
            transport=(ProviderTransport.CLAUDE_NATIVE_HOST if provider == 'claude'
                       else ProviderTransport.OPENAI_RESPONSES_API),
        )
        with patch('director_orchestrator.engine.build_director', return_value=fake):
            return OrchestrationEngine(self.settings, self.store)

    def record(self):
        record = TaskRecord(
            task_id='DIR-IDENTITY', status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id='identity-event', channel_id='C1',
                               thread_ts='1.0', user_id='U1', text='Review repository health'),
        )
        return self.store.create(record)[0]

    def materialize(self, engine, *, historical=False, plan=None):
        record = self.record()
        if historical:
            record.plan_generation = 1
            key = 'a' * 64
            reserve_task_effect(record, effect_key=key, kind='director_intent',
                                phase='plan', provider=ProviderName.OPENAI,
                                estimated_cost_usd=0, hop_units=1)
        else:
            record, key = engine._reserve_director_effect(
                record, phase='plan', identity={'fixture': 'plan'}, estimated_cost_usd=0,
            )
        target = 1
        interaction = engine._begin_director_interaction(
            record, phase='plan', effect_key=key, plan_generation=target,
        )
        plan = plan if plan is not None else preview_plan(record.intake.text, self.settings)
        verified = engine._complete_director_interaction(record, interaction_id=interaction, result=plan)
        record.plan, record.plan_generation, record.plan_interaction_id = plan, target, interaction
        binding = engine._plan_materialization_binding(
            record, verified_event_digest=verified.event_digest,
            verified_result_digest=verified.transport_evidence.result_digest, effect_key=key,
        )
        record_task_effect_observation(
            record, effect_key=key, intent_kind='director_intent',
            observation_kind='director_observation', phase='plan', outcome='completed', run_id=binding,
        )
        self.store.save(record)
        return self.store.get(record.task_id), verified, binding

    def test_new_claude_plan_and_evidence_bind_actual_runtime_and_lead(self):
        engine = self.engine('claude', 'fixture-claude-model')
        record, event, binding = self.materialize(engine)
        identity = record.director_generations[1]
        self.assertEqual(identity.acting_project_director, 'claude')
        self.assertEqual(identity.provider, 'claude')
        self.assertEqual(identity.model_name, 'fixture-claude-model')
        self.assertEqual(event.event_version, 'v3')
        self.assertEqual(event.director_identity, identity)
        self.assertEqual(event.transport_evidence.transport, ProviderTransport.CLAUDE_NATIVE_HOST)
        self.assertEqual(json.loads(binding)['director_identity'], identity.model_dump(mode='json'))
        engine._require_verified_plan_materialization(record)

    def test_new_openai_execution_keeps_actual_provider_and_claude_lead_separate(self):
        record, event, _ = self.materialize(self.engine())
        self.assertEqual(record.director_generations[1].provider, 'openai')
        self.assertEqual(event.director_identity.acting_project_director, 'claude')
        self.assertEqual(event.transport_evidence.transport, ProviderTransport.OPENAI_RESPONSES_API)

    def test_historical_openai_replay_ignores_changed_runtime_settings(self):
        record, event, binding = self.materialize(self.engine(), historical=True)
        original = [item.canonical_payload() for item in record.provider_interaction_ledger]
        self.assertEqual(record.director_generations, {})
        self.assertEqual(json.loads(binding)['binding_version'], 'director-plan-materialization-v1')
        self.assertNotIn('director_identity', event.canonical_payload())
        self.assertEqual(event.event_version, 'v2')
        claude = self.engine('claude')
        claude._require_verified_plan_materialization(record)
        with self.assertRaises(CommunicationEvidenceFailure):
            claude._begin_director_interaction(record, phase='acceptance_review', effect_key='b' * 64)
        self.assertEqual(original, [item.canonical_payload() for item in record.provider_interaction_ledger])
        self.assertEqual(record.director_generations, {})

    def test_new_generation_model_change_blocks_call_but_does_not_relabel_replay(self):
        record, _, _ = self.materialize(self.engine('claude', 'model-a'))
        changed = self.engine('claude', 'model-b')
        changed._require_verified_plan_materialization(record)
        with self.assertRaises(CommunicationEvidenceFailure):
            changed._begin_director_interaction(record, phase='acceptance_review', effect_key='b' * 64)
        self.assertEqual(record.director_generations[1].model_name, 'model-a')

    def test_prior_generation_evidence_cannot_authorize_new_same_provider_plan(self):
        engine = self.engine('claude')
        record, event, _ = self.materialize(engine)
        record.plan_generation = 2
        record.director_generations[2] = record.director_generations[1]
        self.assertFalse(engine._director_event_matches(record, event))
        with self.assertRaises(CommunicationEvidenceFailure):
            engine._require_verified_plan_materialization(record)

    def test_store_refuses_identity_change_deletion_and_historical_backfill(self):
        record, _, _ = self.materialize(self.engine('claude'))
        for replacement in ({}, {1: DirectorIdentity(provider='openai',
                            transport=ProviderTransport.OPENAI_RESPONSES_API, model_name='forged')}):
            changed = self.store.get(record.task_id)
            changed.director_generations = replacement
            with self.assertRaises(CommunicationEvidenceFailure):
                self.store.save(changed)
        current = self.store.get(record.task_id)
        with self.assertRaises(CommunicationEvidenceFailure):
            self.store.mutate_if_version(current.task_id, current._store_version,
                lambda item: item.director_generations.clear())
        current.director_generations[0] = current.director_generations[1]
        with self.assertRaises(CommunicationEvidenceFailure):
            self.store.save(current)

    def test_relabeling_or_removing_identity_breaks_exact_event_digest(self):
        record, event, _ = self.materialize(self.engine('claude'))
        changed = event.model_dump(mode='json')
        changed['director_identity']['model_name'] = 'forged'
        with self.assertRaises(ValidationError):
            ProviderInteractionEvent.model_validate(changed)
        changed = event.model_dump(mode='json')
        changed['director_identity'] = None
        with self.assertRaises(ValidationError):
            ProviderInteractionEvent.model_validate(changed)
        self.assertFalse(director_evidence_matches(event, None))
        self.assertTrue(director_evidence_matches(event, record.director_generations[1]))

    def test_unknown_claude_result_preserves_native_transport_and_identity(self):
        engine = self.engine('claude')
        record, _, _ = self.materialize(engine)
        interaction = engine._begin_director_interaction(record, phase='acceptance_review', effect_key='b' * 64)
        engine._fail_director_interaction(record, interaction_id=interaction, exc=RuntimeError('fixture'))
        projection = project_interaction(record.provider_interaction_ledger, interaction)
        self.assertEqual(projection.state, ProviderInteractionState.UNKNOWN)
        event = record.provider_interaction_ledger[-1]
        self.assertEqual(event.director_identity, record.director_generations[1])
        self.assertEqual(event.transport_evidence.transport, ProviderTransport.CLAUDE_NATIVE_HOST)

    def test_new_director_cannot_accept_own_or_dependency_authored_work(self):
        engine = self.engine('claude')
        record, _, _ = self.materialize(engine)
        authored = Assignment(sequence=1, agent=ProviderName.CLAUDE, task='Implement', acceptance=['tested'])
        review = Assignment(sequence=2, agent=ProviderName.CURSOR, task='Review', acceptance=['reviewed'], depends_on=[1])
        record.plan.assignments = [authored, review]
        for assignment in (authored, review):
            with self.assertRaises(CommunicationEvidenceFailure):
                engine._require_non_author_director(record, assignment)

    def test_historical_generation_blocks_new_self_acceptance_and_keeps_replay(self):
        engine = self.engine('openai')
        record, event, binding = self.materialize(engine, historical=True)
        original = [item.canonical_payload() for item in record.provider_interaction_ledger]
        authored = Assignment(sequence=1, agent=ProviderName.OPENAI,
                              task='Implement the bounded change', acceptance=['tested'])
        with self.assertRaises(CommunicationEvidenceFailure):
            engine._require_non_author_director(record, authored)
        # Refusing a new review never relabels or invalidates the prior proof.
        engine._require_verified_plan_materialization(record)
        self.assertEqual(record.director_generations, {})
        self.assertEqual(original, [item.canonical_payload() for item in record.provider_interaction_ledger])
        self.assertTrue(director_evidence_matches(event, None))
        self.assertEqual(json.loads(binding)['binding_version'], 'director-plan-materialization-v1')

    def test_new_claude_roles_do_not_rename_historical_serialized_roles(self):
        engine = self.engine('claude')
        record, _, _ = self.materialize(engine)
        self.assertEqual(engine._director_debate_role(record, 'director'), DebateActorRole.CLAUDE_DIRECTOR)
        self.assertEqual(DebateActorRole('openai_director'), DebateActorRole.OPENAI_DIRECTOR)
        self.assertEqual(DebateActorRole.OPENAI_PARTICIPANT.value, 'openai_participant')

    def test_new_charter_tie_resolution_calls_actual_runtime_handler(self):
        from director_orchestrator.team_charter import AdvisoryVoteTally
        from tests.test_slack_debate_bus import DebateDirector, debate_plan

        for provider in ('openai', 'claude'):
            with self.subTest(provider=provider):
                self.settings = replace(self.settings, sqlite_path=str(
                    Path(self.settings.sqlite_path).with_name(f'tie-{provider}.sqlite3')
                ))
                self.store = TaskStore(self.settings.sqlite_path)
                engine = self.engine(provider)
                record, _, _ = self.materialize(engine, plan=debate_plan())
                director = DebateDirector()
                engine.director = director
                tally = AdvisoryVoteTally(
                    configured_weight_total=8.5, cast_weight_total=6,
                    option_weights={'A': 3, 'B': 3}, abstaining=[], unavailable=[],
                    winning_options=['A', 'B'], resolution='claude_director_judgment',
                    authorizes_external_action=False,
                )
                captured_votes = []

                def capture_tie(votes, **kwargs):
                    captured_votes.extend(votes)
                    return tally

                # Isolate resolution dispatch; the separate debate suites cover
                # vote construction. Director handlers and evidence run normally.
                with patch('director_orchestrator.engine.tally_advisory_votes', side_effect=capture_tie):
                    continued = asyncio.run(engine._finalize_debate(record, record.plan.assignments[-1]))
                self.assertTrue(continued)
                self.assertEqual([vote.member for vote in captured_votes].count(provider), 1)
                if provider == 'openai':
                    self.assertEqual({vote.member for vote in captured_votes}, {'openai', 'claude', 'cursor'})
                else:
                    self.assertEqual({vote.member for vote in captured_votes}, {'claude', 'cursor'})
                self.assertEqual(director.tie_judgment_calls, 1)
                persisted = self.store.get(record.task_id)
                self.assertEqual(persisted.debate_tie_judgment.chosen_option, 'B')
                event = engine._director_interaction_event(
                    persisted, persisted.debate_tie_judgment_interaction_id
                )
                self.assertEqual(event.provider.value, provider)
                self.assertEqual(event.director_identity, persisted.director_generations[1])


if __name__ == '__main__':
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
