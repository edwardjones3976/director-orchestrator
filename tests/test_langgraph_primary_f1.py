"""F1 investigation: lose acceptance pending writes after the handler returns.

Fake handlers only. A separate process exits at the SqliteSaver persistence
boundary; the parent opens a new connection and runtime over the durable files.
The at-most-once assertion intentionally fails on the investigation baseline.

Research checkpoint completed before this file was written (Firecrawl,
2026-10-22; original receipts in the F1-2026-10-22-I1 task tool transcript):
* Search 01a16453-4280-731f-aaa2-21024a913b12, 2 credits:
  site:docs.langchain.com oss python langgraph durable execution replay
  side effects idempotent. Primary source:
  https://docs.langchain.com/oss/python/langgraph/functional-api#idempotency
  Unfinished tasks can run again; side effects require idempotency. This is
  external context, verified here against pinned langgraph 1.2.10 and SQLite
  saver 3.1.1 rather than assumed to be a version-specific guarantee.
* Scrape 01a16453-e9d7-71cf-9eba-05956e691d17, 5 credits:
  https://developers.openai.com/api/docs/guides/error-codes
  Query: guarantees of idempotency/deduplicated billing on timeout/crash,
  and retry guidance. The page recommends retries for transient errors but
  establishes no deduplicated-billing guarantee. This absence is not proof
  that every OpenAI endpoint lacks idempotency.

Scope: the current default acceptance_from_result handler checks authenticated
stored evidence locally, and PrimaryHandlers describes its handlers as pure.
This test proves replay of an injected handler, not a paid call in that default
composition. The existing SQL claim fixture models insert-once durability;
production Postgres admission and multi-host concurrency are outside this test.
"""

import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from multiprocessing import get_context
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    PrimaryPhase,
    TerminalClassification,
    WorkflowRunStatus,
)
from tests.test_langgraph_primary import (
    _SqlConditionalClaimStore,
    _digest,
    _handlers,
    _request,
    _runtime,
)


@contextmanager
def _connection(path, **kwargs):
    connection = sqlite3.connect(path, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _acceptance_handlers(ledger):
    def acceptance(context):
        # This committed receipt stands in for a completed paid operation.
        # It survives os._exit independently of the graph checkpoint.
        with _connection(ledger) as connection:
            connection.execute(
                "INSERT INTO calls VALUES (?, ?, ?)",
                (context.task_id, context.candidate_sha, context.revision_count),
            )
        return AcceptanceResult(
            _digest("f1-acceptance"), context.candidate_sha, AcceptanceDecision.ACCEPT
        )

    return _handlers(openai_acceptance_revision=acceptance)


class _CrashBeforeAcceptanceWrites(SqliteSaver):
    def put_writes(self, config, writes, task_id, task_path=""):
        if any(key == "acceptance_digest" and value is not None for key, value in writes):
            # The handler has returned. Neither its pending writes nor its node
            # checkpoint can become durable. No checkpoint is manually erased.
            os._exit(73)
        return super().put_writes(config, writes, task_id, task_path)


def _crash_worker(database, ledger, store, request):
    connection = sqlite3.connect(database, check_same_thread=False)
    runtime = _runtime(
        _acceptance_handlers(ledger),
        saver=_CrashBeforeAcceptanceWrites(connection),
        claim_store=store,
    )
    runtime.start(request)


class AcceptanceCrashReplayTests(unittest.TestCase):
    def _exercise(self, crash):
        with tempfile.TemporaryDirectory(prefix="f1-acceptance-") as directory:
            database = str(Path(directory) / "checkpoints.sqlite3")
            ledger = str(Path(directory) / "fake-paid-calls.sqlite3")
            with _connection(ledger) as connection:
                connection.execute(
                    "CREATE TABLE calls (task_id TEXT, candidate_sha TEXT, revision INTEGER)"
                )
            store = _SqlConditionalClaimStore()
            self.addCleanup(store.close)
            request = _request("DIR-F1-ACCEPTANCE-CRASH")
            with _connection(database, check_same_thread=False) as connection:
                initial = _runtime(
                    _acceptance_handlers(ledger),
                    saver=SqliteSaver(connection),
                    claim_store=store,
                    interrupt_after=[PrimaryPhase.EVIDENCE_DEBATE.value],
                )
                paused = initial.start(request)
                self.assertEqual(paused.run_status, WorkflowRunStatus.PAUSED)
                self.assertIsNone(initial.compact_state(request.task_id)["acceptance_digest"])
            if crash:
                process = get_context("spawn").Process(
                    target=_crash_worker, args=(database, ledger, store, request)
                )
                process.start()
                try:
                    process.join(30)
                    self.assertFalse(process.is_alive(), "crash worker timed out")
                    self.assertEqual(process.exitcode, 73, "acceptance persistence boundary not reached")
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join(10)
                    process.close()
                with _connection(ledger) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 1)
            with _connection(database, check_same_thread=False) as connection:
                restarted = _runtime(
                    _acceptance_handlers(ledger), saver=SqliteSaver(connection), claim_store=store
                )
                self.assertIsNone(restarted.compact_state(request.task_id)["acceptance_digest"])
                completed = restarted.start(request)
                self.assertEqual(completed.terminal_classification, TerminalClassification.SUCCEEDED)
            # Reopen again: a fully recorded terminal result is a negative control.
            with _connection(database, check_same_thread=False) as connection:
                replay = _runtime(
                    _acceptance_handlers(ledger), saver=SqliteSaver(connection), claim_store=store
                ).start(request)
                self.assertEqual(replay.state_digest, completed.state_digest)
            with _connection(ledger) as connection:
                calls = connection.execute("SELECT * FROM calls").fetchall()
            self.assertTrue(all(row == (request.task_id, request.starting_sha, 0) for row in calls))
            self.assertEqual(store.rows(), 1, "provider dispatch fence should remain held")
            self.assertEqual(len(calls), 1, f"same task/candidate/revision acceptance calls: {calls!r}")

    def test_crash_before_acceptance_persistence_does_not_repeat_handler(self):
        self._exercise(crash=True)

    def test_completed_acceptance_checkpoint_does_not_repeat_handler(self):
        self._exercise(crash=False)
