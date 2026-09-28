# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:54 UTC | BEGIN]]
"""Native packet metering survives ordinary, parallel and late-readback settlement."""
import copy
import hashlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import ProviderName, ProviderRun, TaskStatus
from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.packet_transports import LegacyProviderPacketTransport
from director_orchestrator.providers import ProviderDailyLimitExceeded
from director_orchestrator.runtime_activation import RuntimeActivation
from director_orchestrator.store import TaskStore
from tests.plan_binding_helpers import seed_production_plan_materialization
from tests import test_engine_packet_runtime_routing as routing


class LateNativeTransport(LegacyProviderPacketTransport):
    def send(self, work_packet_json, *, context):
        raise TimeoutError("completion requires authenticated readback")


class PacketNativeSpendTests(unittest.IsolatedAsyncioTestCase):
    setUp = routing.PacketRuntimeRoutingTests.setUp
    _settings = routing.PacketRuntimeRoutingTests._settings
    _record = routing.PacketRuntimeRoutingTests._record
    _engine = routing.PacketRuntimeRoutingTests._engine

    def test_packet_settlement_reads_native_metering_from_the_latest_record(self):
        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as root:
                self.root = Path(root)
                engine, store = self._engine(self._settings(), None)
                record, assignment = self._record(agent=provider, estimated_cost_usd=0.1)
                store.create(record)
                key = hashlib.sha256(b"late-native").hexdigest()
                engine.provider_spend.reserve(provider=provider, effect_key=key,
                                              estimated_cost_usd=0.1, request_limit=50, budget_usd=0.5)
                latest = store.get(record.task_id)
                latest.provider_runs.append(ProviderRun(
                    provider=provider, run_id="late-native", status="completed", raw={"usage": {"cost": 0.6}}))
                store.save(latest)
                outcome = SimpleNamespace(result_packet={"run_reference": "late-native", "usage": {"cost_usd": 0}})
                engine._settle_packet_spend(record, assignment, key, outcome)
                engine._settle_packet_spend(record, assignment, key, outcome)
                self.assertEqual(engine.provider_spend.exposure(provider), (1, 0.6))
                with self.assertRaises(ProviderDailyLimitExceeded):
                    engine.provider_spend.reserve(provider=provider, effect_key=hashlib.sha256(b"next").hexdigest(),
                                                  estimated_cost_usd=0.01, request_limit=50, budget_usd=0.5)

    async def test_native_metering_for_every_provider_and_settlement_path(self):
        # These synthetic packet transports have a fixed bounded request. Real
        # opaque packet/search transports still serialize even with token prices.
        pricing = patch("director_orchestrator.engine.assignment_request_cost_bound", return_value=0.1)
        pricing.start()
        self.addCleanup(pricing.stop)
        providers = (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY)
        for provider in providers:
            for path in ("ordinary", "parallel", "late"):
                for cost in (0.60, None):
                    with self.subTest(provider=provider, path=path, cost=cost), tempfile.TemporaryDirectory() as root:
                        self.root = Path(root)
                        self.ledger_path = self.root / "runtime-ledger.sqlite3"
                        settings = self._settings(
                            gemini_input_usd_per_mtok=1.0, gemini_output_usd_per_mtok=1.0,
                            grok_input_usd_per_mtok=1.0, grok_output_usd_per_mtok=1.0,
                            perplexity_input_usd_per_mtok=1.0, perplexity_output_usd_per_mtok=1.0,
                            langgraph_runtime_enabled=True,
                            langgraph_runtime_live_dispatch_enabled=True,
                            langgraph_runtime_task_writer_enabled=True,
                            langgraph_parallel_external_dispatch_enabled=True,
                            langgraph_parallel_kill_switch=False,
                            gemini_daily_budget_usd=1.0,
                            grok_daily_budget_usd=1.0,
                            perplexity_daily_budget_usd=1.0,
                            gemini_usage_probe_cost_ceiling_usd=0.01,
                            perplexity_usage_probe_cost_ceiling_usd=0.01,
                            grok_request_cost_floor_usd=0.01,
                            max_task_budget_usd=1.0,
                            max_daily_provider_budget_usd=5.0,
                        )
                        record, assignment = self._record(agent=provider, estimated_cost_usd=0.1)
                        assignments = [assignment]
                        if path == "parallel":
                            sibling = providers[(providers.index(provider) + 1) % len(providers)]
                            assignments.append(assignment.model_copy(update={"sequence": 2, "agent": sibling}))
                        record.plan.assignments = assignments
                        record.plan.max_hops = 3
                        record.task_effect_ledger = []
                        record.provider_interaction_ledger = []
                        record.plan_interaction_id = None
                        record.plan_generation = 0
                        seed_production_plan_materialization(record)
                        store = TaskStore(settings.sqlite_path)
                        store.create(record)
                        packets, adapters, natives = {}, {}, {}
                        for item in assignments:
                            work = copy.deepcopy(self.work)
                            work.update(task_id=record.task_id,
                                        assignment_id=f"{record.task_id}-{item.sequence}",
                                        objective=item.task, acceptance_checks=item.acceptance,
                                        allowed_external_effects=[])
                            digest = hashlib.sha256(canonical_json_bytes(item.model_dump(mode="json"))).hexdigest()
                            work["idempotency_key"] = f"{record.task_id}:{record.plan_generation}:{item.sequence}:{digest}"
                            work["correlation"]["correlation_id"] = (
                                f"{record.task_id}:generation-{record.plan_generation}:assignment-{item.sequence}"
                            )
                            native = ProviderRun(
                                provider=item.agent, assignment_sequence=item.sequence,
                                interaction_id=work["correlation"]["correlation_id"],
                                run_id=f"native-{item.sequence}", status="completed", result="done",
                                evidence_source="provider_authenticated",
                                raw={"usage": {"cost_usd": 0, **({"cost": cost} if cost is not None else {})}},
                            )
                            adapter = AsyncMock()
                            adapter.name = item.agent
                            adapter.dispatch.return_value = native
                            transport_type = LateNativeTransport if path == "late" else LegacyProviderPacketTransport
                            transport = transport_type(adapter, task_store=store, poll_seconds=1)
                            packets[item.sequence] = work
                            adapters[item.agent.value] = PacketBoundaryAdapter(item.agent.value, transport)
                            natives[item.sequence] = native
                        router = routing.MultiPacketTaskRouter(packets, adapters)
                        consumer = routing.RecordingResultConsumer()
                        activation = RuntimeActivation(settings, packet_task_router=router, packet_result_consumer=consumer)
                        try:
                            engine, store = self._engine(settings, activation)
                            engine._publish_delegation = AsyncMock(return_value=True)
                            other = hashlib.sha256(b"other").hexdigest()
                            engine.provider_spend.reserve(provider=provider, effect_key=other,
                                                          estimated_cost_usd=0.5, request_limit=50, budget_usd=1.0)
                            if path == "parallel":
                                await engine._dispatch_parallel_packet_batch(store.get(record.task_id))
                            else:
                                await engine._route_live_dispatch(store.get(record.task_id), assignment)
                                if path == "late":
                                    late = store.get(record.task_id)
                                    late.status = TaskStatus.RUNNING
                                    late.provider_runs.append(natives[1])
                                    store.save(late)
                                    await engine._route_live_dispatch(store.get(record.task_id), assignment)
                            self.assertEqual(consumer.calls, len(assignments))
                            ledger = engine.provider_spend
                            self.assertEqual(len(ledger.reservations(provider)), 2)
                            reservation = next(value for key, value in ledger.reservations(provider) if key != other)
                            expected = 0.5 + max(reservation, cost or 0)
                            self.assertAlmostEqual(ledger.exposure(provider)[1], expected)
                            self.assertEqual(len(ledger.unreconciled(provider, stale_after=timedelta(hours=1))),
                                             1 if cost is None else 0)
                            # Re-read the same authenticated native run: one durable charge, never additive.
                            await adapters[provider.value].authenticated_readback(
                                packets[1], natives[1].model_dump(mode="json"))
                            self.assertEqual(len(ledger.reservations(provider)), 2)
                            self.assertAlmostEqual(ledger.exposure(provider)[1], expected)
                            if cost is not None:
                                with self.assertRaises(ProviderDailyLimitExceeded):
                                    ledger.reserve(provider=provider, effect_key=hashlib.sha256(b"next").hexdigest(),
                                                   estimated_cost_usd=0.01, request_limit=50, budget_usd=1.0)
                        finally:
                            activation.close()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:55 UTC | END]]
