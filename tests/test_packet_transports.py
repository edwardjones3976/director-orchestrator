# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | BEGIN]]
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from director_orchestrator.langgraph_runtime import ProviderAdapterContext
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
from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.packet_routing import DirectorPacketTaskRouter, PacketRoutingError
from director_orchestrator.packet_transports import (
    LegacyProviderPacketTransport,
    LocalGitAuthoritySnapshotResolver,
)
from director_orchestrator.store import TaskStore


COMMIT = "a" * 40
BLOB = "b" * 40


class ScriptedSnapshotResolver(LocalGitAuthoritySnapshotResolver):
    def __init__(self, root: str, *, remote_head: str = COMMIT, contained: bool = True):
        super().__init__("example/demo", root)
        self.remote_head = remote_head
        self.contained = contained

    def _git(self, *arguments: str) -> str:
        if arguments == ("remote", "get-url", "origin"):
            return "https://github.com/example/demo.git\n"
        if arguments[:2] == ("status", "--porcelain=v2"):
            return ""
        if arguments[:2] == ("rev-parse", "--verify"):
            return (self.remote_head if "refs/remotes/origin/" in arguments[2] else COMMIT) + "\n"
        if arguments[0] == "for-each-ref":
            return "refs/remotes/origin/main\n" if self.contained else ""
        if arguments[:3] == ("ls-tree", "-r", "--full-tree"):
            return f"100644 blob {BLOB}\tREADME.md\n"
        raise AssertionError(arguments)


class FinishedCursor:
    name = ProviderName.CURSOR

    def __init__(self) -> None:
        self.assignment: Assignment | None = None

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.assignment = assignment
        return ProviderRun(
            provider=self.name,
            run_id="cursor-run-1",
            status="FINISHED",
            result="bounded result",
            evidence_source="provider_authenticated",
            raw={"usage": {"input_tokens": 4, "output_tokens": 2}},
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("terminal Cursor runs must not be polled")


def record_for(assignment: Assignment) -> TaskRecord:
    return TaskRecord(
        task_id="DIR-TRANSPORT",
        status=TaskStatus.RUNNING,
        intake=SlackIntake(
            source_event_id="evt-transport",
            channel_id="C1",
            thread_ts="1.1",
            user_id="U1",
            text="DIRECTOR run the bounded task",
        ),
        plan_generation=1,
        plan=DirectorPlan(
            project="demo",
            normalized_outcome="bounded task",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=3,
            max_revisions=1,
            assignments=[assignment],
            final_acceptance=["Result is recorded"],
        ),
    )


class LocalGitAuthoritySnapshotTests(unittest.TestCase):
    def test_snapshot_binds_the_requested_origin_ref_and_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            resolver = ScriptedSnapshotResolver(root)
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="inspect",
                acceptance=["report"],
                repository="example/demo",
                starting_ref="main",
            )

            snapshot = resolver.resolve(record_for(assignment), assignment)

        self.assertEqual(
            snapshot,
            [{"path": "README.md", "commit_sha": COMMIT, "blob_sha": BLOB}],
        )

    def test_unpushed_or_uncontained_starting_commit_fails_closed(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="inspect",
            acceptance=["report"],
            repository="example/demo",
            starting_ref="main",
        )
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(PacketRoutingError, "origin tracking ref"):
                ScriptedSnapshotResolver(root, remote_head="c" * 40).resolve(
                    record_for(assignment), assignment
                )
            with self.assertRaisesRegex(PacketRoutingError, "not present"):
                ScriptedSnapshotResolver(root, contained=False).resolve(
                    record_for(assignment), assignment
                )


class LegacyProviderPacketTransportTests(unittest.TestCase):
    def test_terminal_provider_result_is_normalized_without_another_poll(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="inspect",
                acceptance=["report"],
                repository="example/demo",
                starting_ref="main",
                estimated_cost_usd=0.2,
            )
            record, _ = store.create(record_for(assignment))
            resolver = ScriptedSnapshotResolver(root)
            provider = FinishedCursor()
            transport = LegacyProviderPacketTransport(
                provider, task_store=store, poll_seconds=1
            )
            router = DirectorPacketTaskRouter(
                authority_snapshots=resolver,
                boundary_adapters={},
                writer_owner_id="test-writer",
                writer_secret="x" * 32,
                timeout_seconds=30,
            )
            packet = router.work_packet(record, assignment)
            context = ProviderAdapterContext(
                task_id=record.task_id,
                assignment_id=packet["assignment_id"],
                idempotency_key=packet["idempotency_key"],
                work_packet_digest="sha256:" + "d" * 64,
                writer_token_digest="sha256:" + "e" * 64,
                timeout_seconds=30,
                budget_usd=0.2,
                max_hops=3,
                max_revisions=1,
                allowed_tools=tuple(packet["allowed_tools"]),
                allowed_external_effects=tuple(packet["allowed_external_effects"]),
            )

            result = transport.send(canonical_json_bytes(packet), context=context)

        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["provider"], "cursor")
        self.assertEqual(result["run_reference"], "cursor-run-1")
        self.assertEqual(result["usage"]["input_tokens"], 4)
        self.assertEqual(result["acceptance"][0]["status"], "NOT_RUN")
        self.assertEqual(provider.assignment.starting_ref, COMMIT)
        json.dumps(result)

    def test_unverified_write_effects_stop_for_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="change one file",
                acceptance=["focused test passes"],
                repository="example/demo",
                starting_ref="main",
                allow_draft_pr=True,
                estimated_cost_usd=0.2,
            )
            record, _ = store.create(record_for(assignment))
            provider = FinishedCursor()
            transport = LegacyProviderPacketTransport(
                provider, task_store=store, poll_seconds=1
            )
            packet = DirectorPacketTaskRouter(
                authority_snapshots=ScriptedSnapshotResolver(root),
                boundary_adapters={},
                writer_owner_id="test-writer",
                writer_secret="x" * 32,
                timeout_seconds=30,
            ).work_packet(record, assignment)
            context = ProviderAdapterContext(
                task_id=record.task_id,
                assignment_id=packet["assignment_id"],
                idempotency_key=packet["idempotency_key"],
                work_packet_digest="sha256:" + "d" * 64,
                writer_token_digest="sha256:" + "e" * 64,
                timeout_seconds=30,
                budget_usd=0.2,
                max_hops=3,
                max_revisions=1,
                allowed_tools=tuple(packet["allowed_tools"]),
                allowed_external_effects=tuple(packet["allowed_external_effects"]),
            )

            result = transport.send(canonical_json_bytes(packet), context=context)

        self.assertEqual("OWNER_DECISION_REQUIRED", result["status"])
        self.assertEqual("NOT_RUN", result["acceptance"][0]["status"])
        self.assertEqual([], result["side_effects"])
        self.assertTrue(result["requested_decisions"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | END]]
