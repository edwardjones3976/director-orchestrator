# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 00:18 UTC | BEGIN]]
from __future__ import annotations

import copy
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.langgraph_runtime import (
    PACKET_TRANSPORT_VERSION,
    LangGraphPacketRuntime,
    ProviderAdapterContext,
    RuntimeContractError,
    RuntimeExecutionMode,
    RuntimeExecutionStatus,
    WriterOwnershipToken,
)
from director_orchestrator.packet_contracts import (
    PacketContractError,
    document_digest,
    load_json_document,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs" / "schemas"


class FakeAdapter:
    provider = "claude"

    def __init__(
        self,
        result: Mapping[str, Any],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.result = copy.deepcopy(dict(result))
        self.failure = failure
        self.calls = 0
        self.contexts: list[ProviderAdapterContext] = []

    def dispatch(
        self,
        work_packet: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
    ) -> Mapping[str, Any]:
        self.calls += 1
        self.contexts.append(context)
        if self.failure is not None:
            raise self.failure
        return copy.deepcopy(self.result)


class LangGraphPacketRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.checkpoint_path = self.root / "runtime-checkpoints.sqlite3"
        self.ledger_path = self.root / "runtime-ledger.sqlite3"
        self.legacy_path = self.root / "legacy.sqlite3"
        self.legacy_path.touch()
        self.work = load_json_document(
            SCHEMAS / "model-work-packet-v1.example.json"
        )
        self.result = load_json_document(
            SCHEMAS / "model-result-packet-v1.example.json"
        )
        self.connection = sqlite3.connect(self.checkpoint_path, check_same_thread=False)
        self.runtime = LangGraphPacketRuntime(
            SqliteSaver(self.connection),
            ledger_database_path=str(self.ledger_path),
            legacy_database_path=str(self.legacy_path),
            checkpoint_namespace="test-runtime-v1",
            runtime_active=True,
            allow_live_provider_dispatch=True,
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.directory.cleanup()

    def _token(self, owner: str = "langgraph") -> WriterOwnershipToken:
        return WriterOwnershipToken.issue(task_id=self.work["task_id"], owner_id=owner)

    def test_readiness_is_explicit_and_does_not_claim_provider_health(self):
        readiness = self.runtime.readiness().as_dict()
        self.assertEqual(readiness["runtime_active"], True)
        self.assertEqual(readiness["single_writer"], True)
        self.assertEqual(readiness["packet_transport"], PACKET_TRANSPORT_VERSION)
        self.assertEqual(readiness["live_provider_dispatch"], True)
        self.assertNotIn("provider", readiness)
        self.assertNotIn("healthy", readiness)

    def test_offline_replay_never_calls_an_adapter_and_is_idempotent(self):
        first = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
            offline_result_packet=self.result,
        )
        second = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
            offline_result_packet=self.result,
        )

        self.assertEqual(first.status, RuntimeExecutionStatus.COMPLETED)
        self.assertEqual(second.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(first.result_packet, self.result)
        self.assertEqual(first.result_packet_digest, second.result_packet_digest)

    def test_live_adapter_receives_limits_and_dispatches_once(self):
        token = self._token()
        adapter = FakeAdapter(self.result)

        first = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        second = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )

        self.assertEqual(first.status, RuntimeExecutionStatus.COMPLETED)
        self.assertEqual(second.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(adapter.calls, 1)
        context = adapter.contexts[0]
        self.assertEqual(context.timeout_seconds, self.work["limits"]["timeout_seconds"])
        self.assertEqual(context.budget_usd, self.work["limits"]["budget_usd"])
        self.assertEqual(context.idempotency_key, self.work["idempotency_key"])
        self.assertNotEqual(context.writer_token_digest, token.value)
        self.assertEqual(context.allowed_tools, tuple(self.work["allowed_tools"]))
        self.assertEqual(context.allowed_external_effects, ())

    def test_restart_replays_completed_result_without_dispatch(self):
        token = self._token()
        first_adapter = FakeAdapter(self.result)
        completed = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=first_adapter,
            writer_token=token,
        )
        self.assertEqual(completed.status, RuntimeExecutionStatus.COMPLETED)
        self.connection.close()

        self.connection = sqlite3.connect(
            self.checkpoint_path, check_same_thread=False
        )
        restarted = LangGraphPacketRuntime(
            SqliteSaver(self.connection),
            ledger_database_path=str(self.ledger_path),
            legacy_database_path=str(self.legacy_path),
            checkpoint_namespace="test-runtime-v1",
            runtime_active=True,
            allow_live_provider_dispatch=True,
        )
        second_adapter = FakeAdapter(self.result)
        replay = restarted.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=second_adapter,
            writer_token=token,
        )

        self.assertEqual(replay.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(second_adapter.calls, 0)

    def test_ambiguous_dispatch_requires_reconciliation_and_never_retries(self):
        token = self._token()
        failing = FakeAdapter(self.result, failure=TimeoutError("unknown provider state"))
        first = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=failing,
            writer_token=token,
        )
        second = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=failing,
            writer_token=token,
        )

        self.assertEqual(first.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(second.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(failing.calls, 1)

        reconciled = self.runtime.reconcile_result(
            self.work,
            self.result,
            provider="claude",
            writer_token=token,
        )
        replay = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=failing,
            writer_token=token,
        )
        self.assertEqual(reconciled.status, RuntimeExecutionStatus.COMPLETED)
        self.assertEqual(replay.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(failing.calls, 1)

    def test_different_writer_cannot_take_the_same_task(self):
        first = self._token("legacy-engine")
        second = self._token("langgraph-engine")
        self.runtime.bind_writer(first)
        with self.assertRaisesRegex(RuntimeContractError, "different side-effect writer"):
            self.runtime.bind_writer(second)

    def test_raw_writer_token_is_never_persisted(self):
        token = self._token()
        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=FakeAdapter(self.result),
            writer_token=token,
        )
        self.assertEqual(outcome.status, RuntimeExecutionStatus.COMPLETED)
        connection = sqlite3.connect(self.ledger_path)
        try:
            ledger_dump = repr(connection.execute("SELECT * FROM runtime_writer_ownership").fetchall())
            ledger_dump += repr(connection.execute("SELECT * FROM runtime_dispatch_ledger").fetchall())
        finally:
            connection.close()
        self.assertNotIn(token.value, ledger_dump)
        self.assertNotIn(token.value, repr(token))

    def test_duplicate_acceptance_checks_fail_before_reservation(self):
        invalid = copy.deepcopy(self.work)
        invalid["acceptance_checks"].append(invalid["acceptance_checks"][0])
        with self.assertRaisesRegex(RuntimeContractError, "checks must be unique"):
            self.runtime.run_packet(
                invalid,
                provider="claude",
                execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
                offline_result_packet=self.result,
            )
        connection = sqlite3.connect(self.ledger_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM runtime_dispatch_ledger"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_invalid_result_is_terminal_and_not_resent(self):
        token = self._token()
        invalid = copy.deepcopy(self.result)
        invalid["assignment_id"] = "DIFFERENT-ASSIGNMENT"
        adapter = FakeAdapter(invalid)
        first = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        second = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        self.assertEqual(first.status, RuntimeExecutionStatus.REJECTED)
        self.assertEqual(second.status, RuntimeExecutionStatus.REJECTED)
        self.assertEqual(adapter.calls, 1)

    def test_missing_acceptance_disposition_is_rejected(self):
        token = self._token()
        invalid = copy.deepcopy(self.result)
        invalid["acceptance"] = invalid["acceptance"][:1]
        adapter = FakeAdapter(invalid)
        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        self.assertEqual(outcome.status, RuntimeExecutionStatus.REJECTED)
        self.assertEqual(adapter.calls, 1)

    def test_unallowed_reported_side_effect_is_rejected(self):
        token = self._token()
        invalid = copy.deepcopy(self.result)
        invalid["side_effects"] = [
            {
                "kind": "MERGE_PULL_REQUEST",
                "target": "example/1",
                "status": "COMPLETED",
                "idempotency_key": "merge-example-1",
                "external_id": "1",
            }
        ]
        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=FakeAdapter(invalid),
            writer_token=token,
        )
        self.assertEqual(outcome.status, RuntimeExecutionStatus.REJECTED)

    def test_reported_cost_cannot_exceed_packet_budget(self):
        token = self._token()
        invalid = copy.deepcopy(self.result)
        invalid["usage"]["cost_usd"] = self.work["limits"]["budget_usd"] + 0.01
        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=FakeAdapter(invalid),
            writer_token=token,
        )
        self.assertEqual(outcome.status, RuntimeExecutionStatus.REJECTED)

    def test_concurrent_runtime_sees_one_durable_dispatch_claim(self):
        token = self._token()
        entered = threading.Event()
        release = threading.Event()

        class BlockingAdapter(FakeAdapter):
            def dispatch(self, work_packet, *, context):
                self.calls += 1
                self.contexts.append(context)
                entered.set()
                self.assert_release()
                return copy.deepcopy(self.result)

            @staticmethod
            def assert_release():
                if not release.wait(timeout=5):
                    raise TimeoutError("test did not release provider")

        adapter = BlockingAdapter(self.result)
        second_connection = sqlite3.connect(
            self.checkpoint_path, check_same_thread=False
        )
        try:
            second_runtime = LangGraphPacketRuntime(
                SqliteSaver(second_connection),
                ledger_database_path=str(self.ledger_path),
                legacy_database_path=str(self.legacy_path),
                checkpoint_namespace="test-runtime-v1",
                runtime_active=True,
                allow_live_provider_dispatch=True,
            )
            first_outcome: list[object] = []

            def run_first():
                first_outcome.append(
                    self.runtime.run_packet(
                        self.work,
                        provider="claude",
                        execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                        adapter=adapter,
                        writer_token=token,
                    )
                )

            thread = threading.Thread(target=run_first)
            thread.start()
            self.assertTrue(entered.wait(timeout=5))
            competing = second_runtime.run_packet(
                self.work,
                provider="claude",
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                adapter=adapter,
                writer_token=token,
            )
            release.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(
                competing.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
            )
            self.assertEqual(first_outcome[0].status, RuntimeExecutionStatus.COMPLETED)
            self.assertEqual(adapter.calls, 1)
        finally:
            release.set()
            second_connection.close()

    def test_reusing_idempotency_key_for_other_work_fails_closed(self):
        self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
            offline_result_packet=self.result,
        )
        changed = copy.deepcopy(self.work)
        changed["objective"] = "Different work under the same key"
        with self.assertRaisesRegex(RuntimeContractError, "different execution"):
            self.runtime.run_packet(
                changed,
                provider="claude",
                execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
                offline_result_packet=self.result,
            )

    def test_runtime_storage_must_be_separate_from_legacy_and_checkpoints(self):
        with self.assertRaisesRegex(ValueError, "must be isolated"):
            LangGraphPacketRuntime(
                SqliteSaver(self.connection),
                ledger_database_path=str(self.checkpoint_path),
                legacy_database_path=str(self.legacy_path),
            )

    def test_disabled_runtime_rejects_live_but_allows_offline_conformance(self):
        disabled_ledger = self.root / "disabled-ledger.sqlite3"
        disabled = LangGraphPacketRuntime(
            SqliteSaver(self.connection),
            ledger_database_path=str(disabled_ledger),
            legacy_database_path=str(self.legacy_path),
            runtime_active=False,
            allow_live_provider_dispatch=False,
        )
        with self.assertRaisesRegex(RuntimeContractError, "not active"):
            disabled.run_packet(
                self.work,
                provider="claude",
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                adapter=FakeAdapter(self.result),
                writer_token=self._token(),
            )
        offline = disabled.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
            offline_result_packet=self.result,
        )
        self.assertEqual(offline.status, RuntimeExecutionStatus.COMPLETED)


# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | BEGIN]]
class _RuntimeFixture:
    """Its own runtime, so the suites above are not collected a second time."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.ledger_path = root / "recovery-ledger.sqlite3"
        legacy_path = root / "recovery-legacy.sqlite3"
        legacy_path.touch()
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.connection = sqlite3.connect(
            root / "recovery-checkpoints.sqlite3", check_same_thread=False
        )
        self.addCleanup(self.connection.close)
        self.runtime = LangGraphPacketRuntime(
            SqliteSaver(self.connection),
            ledger_database_path=str(self.ledger_path),
            legacy_database_path=str(legacy_path),
            checkpoint_namespace="recovery-runtime-v1",
            runtime_active=True,
            allow_live_provider_dispatch=True,
        )

    def _token(self, owner: str = "langgraph") -> WriterOwnershipToken:
        return WriterOwnershipToken.issue(task_id=self.work["task_id"], owner_id=owner)

    def _ledger_status(self, key: str) -> str:
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            row = connection.execute(
                "SELECT status FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        return "" if row is None else str(row[0])


class StrandedClaimRecoveryTests(_RuntimeFixture, unittest.TestCase):
    """A crashed claim must never strand work and never redispatch ambiguously."""

    def _force_claimed(self, key: str) -> None:
        """Simulate a ledger written before live dispatch claimed atomically."""

        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute(
                "UPDATE runtime_dispatch_ledger SET status='CLAIMED' WHERE idempotency_key=?",
                (key,),
            )
            connection.commit()

    def test_live_dispatch_claims_the_key_as_dispatching_in_one_step(self):
        token = self._token()
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))

        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )

        # There is no window in which a crash could leave CLAIMED behind: the
        # ambiguous failure moved straight to the reconciliation state.
        self.assertEqual(outcome.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(
            self._ledger_status(self.work["idempotency_key"]),
            "RECONCILIATION_REQUIRED",
        )

    def test_a_stranded_live_claim_is_recoverable_without_redispatch(self):
        token = self._token()
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self.work["idempotency_key"]
        self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        self._force_claimed(key)

        # Before recovery the key is unresolvable and must not be resent.
        stuck = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        self.assertEqual(stuck.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(adapter.calls, 1)

        recovered = self.runtime.open_reconciliation(
            self.work, provider="claude", writer_token=token
        )
        self.assertEqual(
            recovered.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
        )
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")

        reconciled = self.runtime.reconcile_result(
            self.work, self.result, provider="claude", writer_token=token
        )
        self.assertEqual(reconciled.status, RuntimeExecutionStatus.COMPLETED)
        # Recovery binds a readback; it never sends the packet again.
        self.assertEqual(adapter.calls, 1)

    def test_recovery_requires_the_writer_token_and_a_recoverable_state(self):
        token = self._token()
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )

        # Already reconciling: recovery is a no-op, not a second transition.
        already = self.runtime.open_reconciliation(
            self.work, provider="claude", writer_token=token
        )
        self.assertEqual(already.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)

        foreign = WriterOwnershipToken.issue(
            task_id=self.work["task_id"], owner_id="someone-else"
        )
        with self.assertRaises(RuntimeContractError):
            self.runtime.open_reconciliation(
                self.work, provider="claude", writer_token=foreign
            )

    def test_recovery_refuses_an_unknown_dispatch(self):
        with self.assertRaisesRegex(RuntimeContractError, "no dispatch exists"):
            self.runtime.open_reconciliation(
                self.work, provider="claude", writer_token=self._token()
            )

    def test_an_interrupted_offline_replay_resumes_without_any_dispatch(self):
        key = self.work["idempotency_key"]
        # An offline replay contacts nothing, so a CLAIMED row left by a crash is
        # safe to resume rather than being stranded for owner reconciliation.
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute(
                "INSERT INTO runtime_dispatch_ledger("
                "idempotency_key, task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, status"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'CLAIMED')",
                (
                    key,
                    self.work["task_id"],
                    self.work["assignment_id"],
                    "claude",
                    RuntimeExecutionMode.OFFLINE_REPLAY.value,
                    document_digest(self.work),
                    self.work["authority_snapshot_digest"],
                ),
            )
            connection.commit()

        resumed = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.OFFLINE_REPLAY,
            offline_result_packet=self.result,
        )
        self.assertEqual(resumed.status, RuntimeExecutionStatus.COMPLETED)
        self.assertEqual(self._ledger_status(key), "COMPLETED")


# [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | BEGIN]]
class StrandedDispatchRecoveryTests(_RuntimeFixture, unittest.TestCase):
    """A hard kill after the atomic DISPATCHING reservation must not strand work.

    `run_packet` only reaches `RECONCILIATION_REQUIRED` through its exception
    handler. A SIGKILL, power loss, or container eviction between the committed
    `DISPATCHING` reservation and that handler leaves a durable `DISPATCHING` row
    that no other edge accepts.
    """

    def setUp(self) -> None:
        super().setUp()
        self.token = self._token()

    def _hard_killed_dispatch(self, adapter: FakeAdapter) -> str:
        """Leave the exact row a hard kill would leave, using the real reserve path.

        The row is written by `_reserve` itself, so every binding (task, assignment,
        provider, execution mode, packet digest, authority digest, writer digest) is
        the production one; only the exception handler's later write is undone.
        """

        key = self.work["idempotency_key"]
        self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=self.token,
        )
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute(
                "UPDATE runtime_dispatch_ledger SET status='DISPATCHING', "
                "error_code=NULL WHERE idempotency_key=?",
                (key,),
            )
            connection.commit()
        self.assertEqual(self._ledger_status(key), "DISPATCHING")
        return key

    def _recover(self, **overrides):
        arguments = {
            "provider": "claude",
            "writer_token": self.token,
            "dispatch_process_confirmed_stopped": True,
        }
        arguments.update(overrides)
        packet = arguments.pop("work_packet", self.work)
        return self.runtime.recover_stranded_dispatch(packet, **arguments)

    def test_a_hard_killed_dispatching_row_is_stranded_by_every_other_edge(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        self._hard_killed_dispatch(adapter)

        # `run_packet` refuses to resend rather than duplicating a paid dispatch.
        stuck = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=self.token,
        )
        self.assertEqual(stuck.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        # The legacy claim edge only accepts CLAIMED.
        with self.assertRaisesRegex(RuntimeContractError, "recoverable legacy claim"):
            self.runtime.open_reconciliation(
                self.work, provider="claude", writer_token=self.token
            )
        # Reconciliation only accepts RECONCILIATION_REQUIRED.
        with self.assertRaisesRegex(RuntimeContractError, "awaiting reconciliation"):
            self.runtime.reconcile_result(
                self.work, self.result, provider="claude", writer_token=self.token
            )
        self.assertEqual(adapter.calls, 1)

    def test_recovery_reopens_reconciliation_without_any_further_dispatch(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        recovered = self._recover()

        self.assertEqual(
            recovered.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
        )
        self.assertIsNone(recovered.result_packet)
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")
        # Recovery infers nothing about the provider: no result is recorded.
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            row = connection.execute(
                "SELECT result_packet_json, result_packet_digest, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertEqual(row[2], "StrandedDispatchRecovered")

        reconciled = self.runtime.reconcile_result(
            self.work, self.result, provider="claude", writer_token=self.token
        )
        self.assertEqual(reconciled.status, RuntimeExecutionStatus.COMPLETED)
        # One dispatch before the kill; recovery and reconciliation add none.
        self.assertEqual(adapter.calls, 1)

    def test_recovery_still_requires_authenticated_readback_evidence(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)
        self._recover()

        unlinked = copy.deepcopy(self.result)
        unlinked["correlation"]["correlation_id"] = "corr-SOMETHING-ELSE"
        with self.assertRaises(PacketContractError):
            self.runtime.reconcile_result(
                self.work, unlinked, provider="claude", writer_token=self.token
            )

        foreign_provider = copy.deepcopy(self.result)
        foreign_provider["provider"] = "openai"
        with self.assertRaises(PacketContractError):
            self.runtime.reconcile_result(
                self.work, foreign_provider, provider="claude", writer_token=self.token
            )

        # Recovery widened nothing: the row still holds no result.
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")
        self.assertEqual(adapter.calls, 1)

    def test_recovery_requires_an_explicit_attestation(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        # The default is deliberately unusable: a caller cannot recover by accident.
        with self.assertRaisesRegex(RuntimeContractError, "explicit attestation"):
            self.runtime.recover_stranded_dispatch(
                self.work, provider="claude", writer_token=self.token
            )
        with self.assertRaisesRegex(RuntimeContractError, "explicit attestation"):
            self._recover(dispatch_process_confirmed_stopped=False)
        self.assertEqual(self._ledger_status(key), "DISPATCHING")

    def test_recovery_fails_closed_on_a_wrong_writer(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        foreign = WriterOwnershipToken.issue(
            task_id=self.work["task_id"], owner_id="someone-else"
        )
        with self.assertRaisesRegex(RuntimeContractError, "writer ownership"):
            self._recover(writer_token=foreign)

        forged = WriterOwnershipToken(
            task_id=self.token.task_id, owner_id=self.token.owner_id, value="forged"
        )
        with self.assertRaisesRegex(RuntimeContractError, "writer ownership"):
            self._recover(writer_token=forged)

        other_task = WriterOwnershipToken.issue(
            task_id="DIR-OTHER-TASK", owner_id=self.token.owner_id
        )
        with self.assertRaisesRegex(RuntimeContractError, "task writer token"):
            self._recover(writer_token=other_task)

        self.assertEqual(self._ledger_status(key), "DISPATCHING")

    def test_recovery_fails_closed_on_wrong_packet_or_provider_bindings(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        with self.assertRaisesRegex(RuntimeContractError, "different execution"):
            self._recover(provider="openai")

        mutated = copy.deepcopy(self.work)
        mutated["objective"] = "A different objective under the same idempotency key."
        with self.assertRaisesRegex(RuntimeContractError, "different execution"):
            self._recover(work_packet=mutated)

        unknown_key = copy.deepcopy(self.work)
        unknown_key["idempotency_key"] = "DIR-EXAMPLE:1:never-dispatched"
        with self.assertRaisesRegex(RuntimeContractError, "no dispatch exists"):
            self._recover(work_packet=unknown_key)

        self.assertEqual(self._ledger_status(key), "DISPATCHING")

    def test_recovery_refuses_terminal_and_legacy_states(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        for status, expected in (
            ("REJECTED", "recoverable live dispatch"),
            ("CLAIMED", "recoverable live dispatch"),
        ):
            with closing(sqlite3.connect(self.ledger_path)) as connection:
                connection.execute(
                    "UPDATE runtime_dispatch_ledger SET status=? WHERE idempotency_key=?",
                    (status, key),
                )
                connection.commit()
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeContractError, expected):
                    self._recover()
                self.assertEqual(self._ledger_status(key), status)
        self.assertEqual(adapter.calls, 1)

    def test_recovery_of_a_completed_dispatch_replays_instead_of_transitioning(self):
        adapter = FakeAdapter(self.result)
        completed = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=self.token,
        )
        self.assertEqual(completed.status, RuntimeExecutionStatus.COMPLETED)

        replayed = self._recover()

        self.assertEqual(replayed.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(
            replayed.result_packet_digest, completed.result_packet_digest
        )
        self.assertEqual(
            self._ledger_status(self.work["idempotency_key"]), "COMPLETED"
        )
        self.assertEqual(adapter.calls, 1)

    def test_replayed_recovery_is_idempotent(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        first = self._recover()
        second = self._recover()
        third = self._recover()

        self.assertEqual(first.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED)
        for repeat in (second, third):
            self.assertEqual(
                repeat.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
            )
            self.assertEqual(repeat.reason, "dispatch is already awaiting reconciliation")
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")
        self.assertEqual(adapter.calls, 1)

    def test_concurrent_recovery_transitions_the_row_exactly_once(self):
        adapter = FakeAdapter(self.result, failure=TimeoutError("unknown state"))
        key = self._hard_killed_dispatch(adapter)

        workers = 6
        start = threading.Barrier(workers)
        outcomes: list[Any] = [None] * workers
        errors: list[BaseException] = []

        def recover(index: int) -> None:
            try:
                start.wait(timeout=30)
                outcomes[index] = self._recover()
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(exc)

        threads = [
            threading.Thread(target=recover, args=(index,)) for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(self._ledger_status(key), "RECONCILIATION_REQUIRED")
        transitioned = [
            outcome
            for outcome in outcomes
            if outcome is not None
            and outcome.reason.startswith("stranded dispatch moved to reconciliation")
        ]
        self.assertEqual(len(transitioned), 1)
        for outcome in outcomes:
            self.assertIsNotNone(outcome)
            self.assertEqual(
                outcome.status, RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
            )
        self.assertEqual(adapter.calls, 1)
# [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | END]]


class TransportPayloadTests(_RuntimeFixture, unittest.TestCase):
    def test_a_non_object_provider_reply_fails_the_packet_contract(self):
        token = self._token()

        class NonObjectAdapter:
            provider = "claude"
            calls = 0

            def dispatch(self, work_packet, *, context):
                type(self).calls += 1
                return ["not", "an", "object"]

        outcome = self.runtime.run_packet(
            self.work,
            provider="claude",
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=NonObjectAdapter(),
            writer_token=token,
        )

        # A decoded list is not a schema failure the validator can describe, but it
        # still has to land on the same fail-closed path rather than escaping as an
        # unclassified error.
        self.assertEqual(outcome.status, RuntimeExecutionStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-25 | 00:18 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | END]]
