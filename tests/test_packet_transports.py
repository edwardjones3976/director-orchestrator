# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from director_orchestrator.callbacks import CallbackConflict, apply_claude_callback
from director_orchestrator.interactions import (
    ProviderInteractionState,
    TransportEvidenceSource,
    project_interaction,
)
from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.models import (
    Assignment,
    ClaudeCallback,
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


class CapturingProvider:
    def __init__(self, name: ProviderName) -> None:
        self.name = name
        self.context: str | None = None

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.context = context
        return ProviderRun(
            provider=self.name,
            run_id=f"{self.name.value}-run-1",
            status="FINISHED",
            result="bounded result",
            evidence_source="provider_authenticated",
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("terminal provider runs must not be polled")


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
    @staticmethod
    def _context(record, packet) -> ProviderAdapterContext:
        return ProviderAdapterContext(
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

    @staticmethod
    def _packet(root, store, provider: ProviderName):
        assignment = Assignment(
            sequence=1,
            agent=provider,
            task="change one file",
            acceptance=["focused test passes"],
            repository="example/demo",
            starting_ref="main",
            allow_draft_pr=True,
            estimated_cost_usd=0.2,
        )
        record, _ = store.create(record_for(assignment))
        packet = DirectorPacketTaskRouter(
            authority_snapshots=ScriptedSnapshotResolver(root),
            boundary_adapters={},
            writer_owner_id="test-writer",
            writer_secret="x" * 32,
            timeout_seconds=30,
        ).work_packet(record, assignment)
        return record, packet

    @staticmethod
    def _effect_evidence(
        packet, run_id: str, effects, *, provider="cursor", result="bounded result"
    ):
        return {
            "schema_version": "1.0",
            "task_id": packet["task_id"],
            "assignment_id": packet["assignment_id"],
            "packet_idempotency_key": packet["idempotency_key"],
            "authority_snapshot_digest": packet["authority_snapshot_digest"],
            "correlation_id": packet["correlation"]["correlation_id"],
            "repository": packet["repository"],
            "starting_ref": packet["starting_ref"],
            "provider": provider,
            "provider_run_id": run_id,
            "result_digest": hashlib.sha256(result.encode("utf-8")).hexdigest(),
            "effects": effects,
        }

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

    def test_cursor_and_claude_receive_the_complete_canonical_packet(self) -> None:
        for provider_name in (ProviderName.CURSOR, ProviderName.CLAUDE):
            with self.subTest(provider=provider_name.value), tempfile.TemporaryDirectory() as root:
                store = TaskStore(str(Path(root) / "tasks.sqlite3"))
                record, packet = self._packet(root, store, provider_name)
                provider = CapturingProvider(provider_name)
                transport = LegacyProviderPacketTransport(
                    provider, task_store=store, poll_seconds=1
                )
                transport.send(
                    canonical_json_bytes(packet),
                    context=self._context(record, packet),
                )

                self.assertIsNotNone(provider.context)
                marker = "DIRECTOR_WORK_PACKET_V1_JSON="
                _, found, encoded = provider.context.partition(marker)
                self.assertEqual(marker, found)
                effect_marker = "\nDIRECTOR_SIDE_EFFECT_EVIDENCE_CONTRACT_V1_JSON="
                encoded_packet, found_effect_contract, encoded_effect_contract = (
                    encoded.partition(effect_marker)
                )
                self.assertEqual(effect_marker, found_effect_contract)
                transmitted = json.loads(encoded_packet)
                effect_contract = json.loads(encoded_effect_contract)
                self.assertEqual(packet, transmitted)
                for field in (
                    "constraints",
                    "prohibited_actions",
                    "allowed_tools",
                    "allowed_external_effects",
                    "evidence_requirements",
                    "limits",
                    "approval_requirements",
                    "authority_snapshot",
                    "correlation",
                ):
                    self.assertEqual(packet[field], transmitted[field])
                self.assertEqual(
                    packet["idempotency_key"],
                    effect_contract["bindings"]["packet_idempotency_key"],
                )
                self.assertEqual(
                    {
                        kind: f"{packet['idempotency_key']}:effect:{kind.lower()}"
                        for kind in packet["allowed_external_effects"]
                    },
                    effect_contract["effect_idempotency_keys"],
                )

    def test_authenticated_complete_side_effect_evidence_can_progress(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CURSOR),
                task_store=store,
                poll_seconds=1,
            )
            effects = [
                {
                    "kind": "WRITE_ISOLATED_BRANCH",
                    "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:write_isolated_branch"
                    ),
                    "external_id": "codex/focused",
                },
                {
                    "kind": "OPEN_DRAFT_PULL_REQUEST",
                    "target": f"example/demo@{packet['starting_ref']}#123",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                    ),
                    "external_id": "123",
                },
            ]
            run_id = "cursor-effects-1"

            result = transport._result(
                packet,
                ProviderRun(
                    provider=ProviderName.CURSOR,
                    run_id=run_id,
                    status="FINISHED",
                    result="bounded result",
                    evidence_source="provider_authenticated",
                    raw={
                        "side_effect_evidence": self._effect_evidence(
                            packet, run_id, effects
                        )
                    },
                ),
            )

        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual(effects, result["side_effects"])
        self.assertEqual([], result["requested_decisions"])

    def test_missing_or_extra_side_effect_evidence_requires_owner(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CURSOR),
                task_store=store,
                poll_seconds=1,
            )
            allowed_branch = {
                "kind": "WRITE_ISOLATED_BRANCH",
                "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:write_isolated_branch"
                ),
                "external_id": "codex/focused",
            }
            allowed_pr = {
                "kind": "OPEN_DRAFT_PULL_REQUEST",
                "target": f"example/demo@{packet['starting_ref']}#123",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                ),
                "external_id": "123",
            }
            unauthorized_merge = {
                "kind": "MERGE_PULL_REQUEST",
                "target": f"example/demo@{packet['starting_ref']}#123",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:merge_pull_request"
                ),
            }
            run_ids = {label: f"cursor-{label}" for label in ("missing", "partial", "extra")}
            cases = {
                "missing": {},
                "partial": {
                    "side_effect_evidence": self._effect_evidence(
                        packet, run_ids["partial"], [allowed_branch]
                    )
                },
                "extra": {
                    "side_effect_evidence": self._effect_evidence(
                        packet,
                        run_ids["extra"],
                        [allowed_branch, allowed_pr, unauthorized_merge],
                    )
                },
            }
            for label, raw in cases.items():
                with self.subTest(case=label):
                    result = transport._result(
                        packet,
                        ProviderRun(
                            provider=ProviderName.CURSOR,
                            run_id=run_ids[label],
                            status="FINISHED",
                            result="bounded result",
                            evidence_source="provider_authenticated",
                            raw=raw,
                        ),
                    )
                    self.assertEqual("OWNER_DECISION_REQUIRED", result["status"])
                    self.assertEqual([], result["side_effects"])
                    self.assertTrue(result["blockers"])
                    self.assertTrue(result["requested_decisions"])

    def test_effect_evidence_must_bind_packet_target_and_director_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CURSOR),
                task_store=store,
                poll_seconds=1,
            )
            run_id = "cursor-binding"
            branch = {
                "kind": "WRITE_ISOLATED_BRANCH",
                "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:write_isolated_branch"
                ),
                "external_id": "codex/focused",
            }
            draft_pr = {
                "kind": "OPEN_DRAFT_PULL_REQUEST",
                "target": f"example/demo@{packet['starting_ref']}#123",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                ),
                "external_id": "123",
            }
            valid = self._effect_evidence(packet, run_id, [branch, draft_pr])
            cases = {
                "repository": {**valid, "repository": "other/repo"},
                "snapshot": {**valid, "authority_snapshot_digest": "sha256:" + "0" * 64},
                "correlation": {**valid, "correlation_id": "stale-correlation"},
                "target": {
                    **valid,
                    "effects": [
                        {**branch, "target": f"other/repo@{packet['starting_ref']}:codex/focused"},
                        draft_pr,
                    ],
                },
                "effect_key": {
                    **valid,
                    "effects": [
                        {**branch, "idempotency_key": "provider-selected-key"},
                        draft_pr,
                    ],
                },
            }
            for label, evidence in cases.items():
                with self.subTest(case=label):
                    result = transport._result(
                        packet,
                        ProviderRun(
                            provider=ProviderName.CURSOR,
                            run_id=run_id,
                            status="FINISHED",
                            result="bounded result",
                            evidence_source="provider_authenticated",
                            raw={"side_effect_evidence": evidence},
                        ),
                    )
                    self.assertEqual("OWNER_DECISION_REQUIRED", result["status"])
                    self.assertEqual([], result["side_effects"])
                    self.assertTrue(result["blockers"])

    def test_claude_waiting_callback_persists_authenticated_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            record, packet = self._packet(root, store, ProviderName.CLAUDE)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CLAUDE),
                task_store=store,
                poll_seconds=1,
            )
            run = ProviderRun(
                provider=ProviderName.CLAUDE,
                run_id="claude-session-1",
                session_id="claude-session-1",
                status="WAITING_CALLBACK",
                raw={"claude_code_session_id": "claude-session-1"},
            )

            transport._record_claude_dispatch(
                packet, self._context(record, packet), record.plan.assignments[0], run
            )

            persisted = store.get(record.task_id)
            persisted_run = persisted.provider_runs[-1]
            self.assertEqual("WAITING_CALLBACK", persisted_run.status)
            self.assertEqual("unverified", persisted_run.evidence_source)
            acknowledgement = project_interaction(
                persisted.provider_interaction_ledger,
                persisted_run.interaction_id,
            )
            self.assertEqual(ProviderInteractionState.ACKNOWLEDGED, acknowledgement.state)
            self.assertEqual(
                TransportEvidenceSource.AUTHENTICATED_PROVIDER,
                persisted.provider_interaction_ledger[-1].transport_evidence.source,
            )

            self.assertTrue(
                apply_claude_callback(
                    persisted,
                    ClaudeCallback(
                        run_id="claude-session-1",
                        status="completed",
                        result="bounded completion evidence",
                    ),
                )
            )
            self.assertEqual(
                "provider_authenticated", persisted.provider_runs[-1].evidence_source
            )
            self.assertEqual(
                ProviderInteractionState.VERIFIED,
                project_interaction(
                    persisted.provider_interaction_ledger,
                    persisted_run.interaction_id,
                ).state,
            )

    def test_claude_initial_response_effects_never_become_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            record, packet = self._packet(root, store, ProviderName.CLAUDE)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CLAUDE),
                task_store=store,
                poll_seconds=1,
            )
            result_text = "bounded completion evidence"
            effects = [
                {
                    "kind": "WRITE_ISOLATED_BRANCH",
                    "target": f"example/demo@{packet['starting_ref']}:claude/focused",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:write_isolated_branch"
                    ),
                    "external_id": "claude/focused",
                },
                {
                    "kind": "OPEN_DRAFT_PULL_REQUEST",
                    "target": f"example/demo@{packet['starting_ref']}#123",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                    ),
                    "external_id": "123",
                },
            ]
            run_id = "claude-predeclared-effects"
            initial_evidence = self._effect_evidence(
                packet,
                run_id,
                effects,
                provider="claude",
                result=result_text,
            )
            transport._record_claude_dispatch(
                packet,
                self._context(record, packet),
                record.plan.assignments[0],
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=run_id,
                    status="WAITING_CALLBACK",
                    raw={"side_effect_evidence": initial_evidence},
                ),
            )
            persisted = store.get(record.task_id)

            self.assertTrue(
                apply_claude_callback(
                    persisted,
                    ClaudeCallback(
                        run_id=run_id,
                        status="completed",
                        result=result_text,
                    ),
                )
            )
            terminal_run = persisted.provider_runs[-1]
            self.assertNotIn("side_effect_evidence", terminal_run.raw)
            normalized = transport._result(packet, terminal_run)
            self.assertEqual("OWNER_DECISION_REQUIRED", normalized["status"])
            self.assertEqual([], normalized["side_effects"])

    def test_claude_terminal_callback_effects_are_bound_and_replay_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            record, packet = self._packet(root, store, ProviderName.CLAUDE)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CLAUDE),
                task_store=store,
                poll_seconds=1,
            )
            run_id = "claude-terminal-effects"
            result_text = "bounded completion evidence"
            effects = [
                {
                    "kind": "WRITE_ISOLATED_BRANCH",
                    "target": f"example/demo@{packet['starting_ref']}:claude/focused",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:write_isolated_branch"
                    ),
                    "external_id": "claude/focused",
                },
                {
                    "kind": "OPEN_DRAFT_PULL_REQUEST",
                    "target": f"example/demo@{packet['starting_ref']}#123",
                    "status": "COMPLETED",
                    "idempotency_key": (
                        f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                    ),
                    "external_id": "123",
                },
            ]
            evidence = self._effect_evidence(
                packet,
                run_id,
                effects,
                provider="claude",
                result=result_text,
            )
            transport._record_claude_dispatch(
                packet,
                self._context(record, packet),
                record.plan.assignments[0],
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=run_id,
                    status="WAITING_CALLBACK",
                ),
            )
            persisted = store.get(record.task_id)
            callback = ClaudeCallback(
                run_id=run_id,
                status="completed",
                result=result_text,
                side_effect_evidence=evidence,
            )

            self.assertTrue(apply_claude_callback(persisted, callback))
            normalized = transport._result(packet, persisted.provider_runs[-1])
            self.assertEqual("SUCCEEDED", normalized["status"])
            self.assertEqual(effects, normalized["side_effects"])
            self.assertFalse(apply_claude_callback(persisted, callback))

            changed = callback.model_copy(deep=True)
            changed.side_effect_evidence.effects[0].status = "FAILED"
            with self.assertRaisesRegex(CallbackConflict, "different terminal result"):
                apply_claude_callback(persisted, changed)

    def test_claude_callback_effect_binding_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            record, packet = self._packet(root, store, ProviderName.CLAUDE)
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CLAUDE),
                task_store=store,
                poll_seconds=1,
            )
            run_id = "claude-mismatched-effects"
            result_text = "bounded completion evidence"
            effect = {
                "kind": "WRITE_ISOLATED_BRANCH",
                "target": f"example/demo@{packet['starting_ref']}:claude/focused",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:write_isolated_branch"
                ),
                "external_id": "claude/focused",
            }
            second_effect = {
                "kind": "OPEN_DRAFT_PULL_REQUEST",
                "target": f"example/demo@{packet['starting_ref']}#123",
                "status": "COMPLETED",
                "idempotency_key": (
                    f"{packet['idempotency_key']}:effect:open_draft_pull_request"
                ),
                "external_id": "123",
            }
            valid = self._effect_evidence(
                packet,
                run_id,
                [effect, second_effect],
                provider="claude",
                result=result_text,
            )
            transport._record_claude_dispatch(
                packet,
                self._context(record, packet),
                record.plan.assignments[0],
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=run_id,
                    status="WAITING_CALLBACK",
                ),
            )
            persisted = store.get(record.task_id)

            for field, value in (
                ("provider_run_id", "other-run"),
                ("correlation_id", "other-correlation"),
                ("result_digest", "0" * 64),
            ):
                with self.subTest(field=field):
                    mismatched = {**valid, field: value}
                    with self.assertRaisesRegex(
                        CallbackConflict, "does not match its terminal run"
                    ):
                        apply_claude_callback(
                            persisted,
                            ClaudeCallback(
                                run_id=run_id,
                                status="completed",
                                result=result_text,
                                side_effect_evidence=mismatched,
                            ),
                        )
                    self.assertEqual(
                        "WAITING_CALLBACK", persisted.provider_runs[-1].status
                    )
                    self.assertEqual(
                        "unverified", persisted.provider_runs[-1].evidence_source
                    )

            outside = {
                **valid,
                "effects": [
                    {**effect, "target": "other/repo@main:claude/focused"},
                    second_effect,
                ],
            }
            self.assertTrue(
                apply_claude_callback(
                    persisted,
                    ClaudeCallback(
                        run_id=run_id,
                        status="completed",
                        result=result_text,
                        side_effect_evidence=outside,
                    ),
                )
            )
            normalized = transport._result(packet, persisted.provider_runs[-1])
            self.assertEqual("OWNER_DECISION_REQUIRED", normalized["status"])
            self.assertEqual([], normalized["side_effects"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:07 UTC | END]]
