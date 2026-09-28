# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 06:47 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:36 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

import director_orchestrator.main as director_main
from director_orchestrator.callbacks import CallbackConflict, apply_claude_callback
from director_orchestrator.config import Settings
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
from director_orchestrator.packet_contracts import canonical_json_bytes, validate_packet
from director_orchestrator.packet_routing import DirectorPacketTaskRouter, PacketRoutingError
from director_orchestrator.packet_transports import (
    GitHubRepositoryEffectReadback,
    GitHubReadOnlyRepositoryEffectVerifier,
    LegacyProviderPacketTransport,
    LocalGitAuthoritySnapshotResolver,
    build_primary_packet_adapters,
)
from director_orchestrator.store import TaskStore


COMMIT = "a" * 40
BLOB = "b" * 40
HEAD = "c" * 40


class FakeGitHubEffectVerifier:
    def __init__(
        self,
        readback=None,
        error: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.value = readback if readback is not None else GitHubRepositoryEffectReadback(
            repository="example/demo",
            base_ref="main",
            base_sha=COMMIT,
            branch="codex/focused",
            head_sha=HEAD,
            compare_base_sha=COMMIT,
            compare_head_sha=HEAD,
            base_is_ancestor=True,
            pull_number=123,
            pull_state="open",
            pull_is_draft=True,
            pull_is_merged=False,
            pull_head_repository="example/demo",
            pull_head_ref="codex/focused",
            pull_head_sha=HEAD,
            pull_base_repository="example/demo",
            pull_base_ref="main",
            pull_base_sha=COMMIT,
        )
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[str, str, str, str, int | None]] = []

    async def readback(self, repository, *, starting_ref, base_ref, branch, pull_number):
        self.calls.append((repository, starting_ref, base_ref, branch, pull_number))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return self.value


class ScriptedSnapshotResolver(LocalGitAuthoritySnapshotResolver):
    def __init__(
        self,
        root: str,
        *,
        remote_head: str = COMMIT,
        contained: bool = True,
        file_count: int = 1,
        max_files: int = 512,
    ):
        super().__init__("example/demo", root, max_files=max_files)
        self.remote_head = remote_head
        self.contained = contained
        self.file_count = file_count

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
            if self.file_count == 1:
                return f"100644 blob {BLOB}\tREADME.md\n"
            return "".join(
                f"100644 blob {BLOB}\tfile-{index:04d}.txt\n"
                for index in range(self.file_count)
            )
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
    def test_remote_normalization_requires_an_exact_github_host(self) -> None:
        normalize = LocalGitAuthoritySnapshotResolver._normalize_remote

        self.assertEqual(
            "example/demo",
            normalize("https://github.com/Example/Demo.git"),
        )
        self.assertEqual(
            "example/demo",
            normalize("git@github.com:Example/Demo.git"),
        )
        for untrusted in (
            "https://example.invalid/github.com/example/demo.git",
            "https://github.com.example.invalid/example/demo.git",
            "ssh://git@github.com/example/demo.git",
            "https://origin.cursor.com/example/demo.git",
            "https://gıthub.com/example/demo.git",
            "httpſ://github.com/example/demo.git",
        ):
            with self.subTest(remote=untrusted):
                self.assertIsNone(normalize(untrusted))

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

    def test_default_snapshot_inventory_rejects_more_than_512_files(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="inspect",
            acceptance=["report"],
            repository="example/demo",
            starting_ref="main",
        )
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(PacketRoutingError, "bounded packet inventory"):
                ScriptedSnapshotResolver(root, file_count=513).resolve(
                    record_for(assignment), assignment
                )

    def test_configured_snapshot_inventory_returns_all_711_files(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="inspect",
            acceptance=["report"],
            repository="example/demo",
            starting_ref="main",
        )
        with tempfile.TemporaryDirectory() as root:
            snapshot = ScriptedSnapshotResolver(
                root, file_count=711, max_files=711
            ).resolve(record_for(assignment), assignment)

        self.assertEqual(711, len(snapshot))
        self.assertEqual("file-0000.txt", snapshot[0]["path"])
        self.assertEqual("file-0710.txt", snapshot[-1]["path"])
        self.assertEqual(711, len({item["path"] for item in snapshot}))

    def test_snapshot_git_calls_spend_the_callers_deadline(self) -> None:
        import subprocess as subprocess_module
        import time as time_module
        from unittest.mock import patch
        from director_orchestrator.packet_routing import PacketRoutingError, blob_read_deadline

        timeouts = []

        def run(*args, **kwargs):
            timeouts.append(kwargs["timeout"])
            return subprocess_module.CompletedProcess((), 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as root:
            resolver = LocalGitAuthoritySnapshotResolver("example/demo", root)
            with patch("director_orchestrator.packet_transports.subprocess.run", side_effect=run):
                self.assertEqual("ok", resolver._git("status"))
                started = time_module.monotonic()
                deadline = started + 0.3
                with blob_read_deadline(deadline):
                    resolver._git("status")
            self.assertEqual(30.0, timeouts[0])
            self.assertLessEqual(timeouts[1], deadline - started)
            with patch("director_orchestrator.packet_transports.subprocess.run",
                       side_effect=AssertionError("spawned")), \
                    blob_read_deadline(time_module.monotonic() - 1), \
                    self.assertRaises(PacketRoutingError):
                resolver._git("status")
            with patch("director_orchestrator.packet_transports.subprocess.run",
                       side_effect=subprocess_module.TimeoutExpired("git", 0.1)), \
                    self.assertRaises(PacketRoutingError):
                resolver._git("status")

    def test_snapshot_inventory_limit_rejects_values_outside_hard_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for invalid in (0, 4097, True):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "between 1 and 4096"):
                        LocalGitAuthoritySnapshotResolver(
                            "example/demo", root, max_files=invalid
                        )


class GitHubReadOnlyRepositoryEffectVerifierTests(unittest.TestCase):
    def test_readback_resolves_the_authorized_base_ref_independently(self) -> None:
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            payloads = {
                "/repos/example/demo/git/ref/heads/main": {
                    "object": {"sha": COMMIT}
                },
                "/repos/example/demo/git/ref/heads/codex/focused": {
                    "object": {"sha": HEAD}
                },
                f"/repos/example/demo/compare/{COMMIT}...{HEAD}": {
                    "status": "ahead",
                    "base_commit": {"sha": COMMIT},
                    "merge_base_commit": {"sha": COMMIT},
                    "commits": [{"sha": HEAD}],
                },
                "/repos/example/demo/pulls/123": {
                    "state": "open",
                    "draft": True,
                    "merged": False,
                    "head": {
                        "ref": "codex/focused",
                        "sha": HEAD,
                        "repo": {"full_name": "example/demo"},
                    },
                    "base": {
                        "ref": "main",
                        "sha": COMMIT,
                        "repo": {"full_name": "example/demo"},
                    },
                },
            }
            return httpx.Response(200, json=payloads[request.url.path])

        verifier = GitHubReadOnlyRepositoryEffectVerifier(
            token="configured-secret",
            api_base="https://api.github.test",
            transport=httpx.MockTransport(handler),
        )
        observed = asyncio.run(
            verifier.readback(
                "example/demo",
                starting_ref=COMMIT,
                base_ref="main",
                branch="codex/focused",
                pull_number=123,
            )
        )

        self.assertEqual("main", observed.base_ref)
        self.assertEqual(COMMIT, observed.base_sha)
        self.assertTrue(observed.base_is_ancestor)
        self.assertEqual(
            [
                "/repos/example/demo/git/ref/heads/main",
                "/repos/example/demo/git/ref/heads/codex/focused",
                f"/repos/example/demo/compare/{COMMIT}...{HEAD}",
                "/repos/example/demo/pulls/123",
            ],
            requested_paths,
        )


class LegacyProviderPacketTransportTests(unittest.TestCase):
    def test_primary_packet_builder_registers_grok(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = type("GrokTestAdapter", (), {"name": ProviderName.GROK})()
            adapters = build_primary_packet_adapters(
                {ProviderName.GROK: adapter},
                task_store=store,
                poll_seconds=1,
            )

        self.assertEqual(set(adapters), {"grok"})

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

    def test_legacy_v1_read_only_handoff_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)

        legacy_read_only = dict(packet)
        legacy_read_only.pop("authority_base_ref")
        legacy_read_only["allowed_external_effects"] = []
        validated = validate_packet("work", legacy_read_only)

        handoff = LegacyProviderPacketTransport._provider_handoff(validated)

        self.assertIn("DIRECTOR_WORK_PACKET_V1_JSON=", handoff)
        legacy_write = dict(validated)
        legacy_write["allowed_external_effects"] = ["WRITE_ISOLATED_BRANCH"]
        with self.assertRaisesRegex(ValueError, "base-ref authority"):
            LegacyProviderPacketTransport._provider_handoff(legacy_write)

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
                github_effect_verifier=FakeGitHubEffectVerifier(),
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
        self.assertEqual(
            [("example/demo", COMMIT, "main", "codex/focused", 123)],
            transport.github_effect_verifier.calls,
        )

    def test_async_authenticated_readback_verifies_completed_repository_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)
            verifier = FakeGitHubEffectVerifier()
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CURSOR),
                task_store=store,
                poll_seconds=1,
                github_effect_verifier=verifier,
            )
            effects = [
                {
                    "kind": "WRITE_ISOLATED_BRANCH",
                    "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                    "status": "COMPLETED",
                    "idempotency_key": f"{packet['idempotency_key']}:effect:write_isolated_branch",
                    "external_id": "codex/focused",
                },
                {
                    "kind": "OPEN_DRAFT_PULL_REQUEST",
                    "target": f"example/demo@{packet['starting_ref']}#123",
                    "status": "COMPLETED",
                    "idempotency_key": f"{packet['idempotency_key']}:effect:open_draft_pull_request",
                    "external_id": "123",
                },
            ]
            run_id = "cursor-late-effects"
            run = ProviderRun(
                provider=ProviderName.CURSOR,
                run_id=run_id,
                status="FINISHED",
                result="bounded result",
                assignment_sequence=1,
                interaction_id=packet["correlation"]["correlation_id"],
                evidence_source="provider_authenticated",
                raw={
                    "side_effect_evidence": self._effect_evidence(
                        packet, run_id, effects
                    )
                },
            )

            result = asyncio.run(
                transport.authenticated_readback(
                    canonical_json_bytes(packet), run.model_dump(mode="json")
                )
            )

        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual(effects, result["side_effects"])
        self.assertEqual(1, len(verifier.calls))

    def test_completed_repository_effects_require_independent_readback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(str(Path(root) / "tasks.sqlite3"))
            _, packet = self._packet(root, store, ProviderName.CURSOR)
            effects = [
                {
                    "kind": "WRITE_ISOLATED_BRANCH",
                    "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                    "status": "COMPLETED",
                    "idempotency_key": f"{packet['idempotency_key']}:effect:write_isolated_branch",
                    "external_id": "codex/focused",
                },
                {
                    "kind": "OPEN_DRAFT_PULL_REQUEST",
                    "target": f"example/demo@{packet['starting_ref']}#123",
                    "status": "COMPLETED",
                    "idempotency_key": f"{packet['idempotency_key']}:effect:open_draft_pull_request",
                    "external_id": "123",
                },
            ]
            run_id = "cursor-unverified-effects"
            transport = LegacyProviderPacketTransport(
                CapturingProvider(ProviderName.CURSOR),
                task_store=store,
                poll_seconds=1,
            )

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

        self.assertEqual("OWNER_DECISION_REQUIRED", result["status"])
        self.assertEqual([], result["side_effects"])
        self.assertIn("Independent GitHub", result["blockers"][0])

    def test_github_readback_mismatch_or_unavailability_fails_closed(self) -> None:
        mismatch_values = {
            "base SHA": replace(
                FakeGitHubEffectVerifier().value, base_sha="d" * 40
            ),
            "pull base repository": replace(
                FakeGitHubEffectVerifier().value, pull_base_repository="other/repo"
            ),
            "pull base ref": replace(
                FakeGitHubEffectVerifier().value, pull_base_ref="release"
            ),
            "paired wrong base refs": replace(
                FakeGitHubEffectVerifier().value,
                base_ref="release",
                pull_base_ref="release",
            ),
            "pull base SHA": replace(
                FakeGitHubEffectVerifier().value, pull_base_sha="d" * 40
            ),
            "compare base SHA": replace(
                FakeGitHubEffectVerifier().value, compare_base_sha="d" * 40
            ),
            "unrelated ancestry": replace(
                FakeGitHubEffectVerifier().value, base_is_ancestor=False
            ),
            "head SHA": replace(
                FakeGitHubEffectVerifier().value, pull_head_sha="d" * 40
            ),
            "branch": replace(
                FakeGitHubEffectVerifier().value, pull_head_ref="cursor/other"
            ),
            "pull request": replace(
                FakeGitHubEffectVerifier().value, pull_number=124
            ),
            "not draft": replace(
                FakeGitHubEffectVerifier().value, pull_is_draft=False
            ),
        }
        scenarios = {
            **{
                label: FakeGitHubEffectVerifier(value)
                for label, value in mismatch_values.items()
            },
            "timeout": FakeGitHubEffectVerifier(error=TimeoutError("readback timed out")),
            "malformed": FakeGitHubEffectVerifier(readback={"repository": "example/demo"}),
            "hang": FakeGitHubEffectVerifier(delay_seconds=1.0),
        }
        for label, verifier in scenarios.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                store = TaskStore(str(Path(root) / "tasks.sqlite3"))
                _, packet = self._packet(root, store, ProviderName.CURSOR)
                effects = [
                    {
                        "kind": "WRITE_ISOLATED_BRANCH",
                        "target": f"example/demo@{packet['starting_ref']}:codex/focused",
                        "status": "COMPLETED",
                        "idempotency_key": f"{packet['idempotency_key']}:effect:write_isolated_branch",
                        "external_id": "codex/focused",
                    },
                    {
                        "kind": "OPEN_DRAFT_PULL_REQUEST",
                        "target": f"example/demo@{packet['starting_ref']}#123",
                        "status": "COMPLETED",
                        "idempotency_key": f"{packet['idempotency_key']}:effect:open_draft_pull_request",
                        "external_id": "123",
                    },
                ]
                run_id = f"cursor-readback-{label}"
                transport = LegacyProviderPacketTransport(
                    CapturingProvider(ProviderName.CURSOR),
                    task_store=store,
                    poll_seconds=1,
                    github_effect_verifier=verifier,
                    github_effect_timeout_seconds=0.01,
                )
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
                self.assertEqual("OWNER_DECISION_REQUIRED", result["status"])
                self.assertEqual([], result["side_effects"])
                self.assertEqual(1, len(verifier.calls))

    def test_production_github_effect_verifier_injection_and_absence(self) -> None:
        base = Settings()
        absent = replace(
            base,
            oracle_automerge_github_token="",
            oracle_automerge_github_app_id="",
            oracle_automerge_github_installation_id="",
            oracle_automerge_github_app_private_key_b64="",
        )
        self.assertIsNone(director_main._build_github_effect_verifier(absent))

        configured = replace(
            absent,
            oracle_automerge_github_token="configured-secret",
            provider_timeout_seconds=8,
        )
        sentinel = object()
        with patch.object(
            GitHubReadOnlyRepositoryEffectVerifier,
            "from_settings",
            return_value=sentinel,
        ) as factory:
            self.assertIs(
                sentinel,
                director_main._build_github_effect_verifier(configured),
            )
        factory.assert_called_once_with(configured, request_timeout_seconds=8.0)

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
            self.assertEqual(record.plan_generation, persisted_run.plan_generation)
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

    def test_claude_acknowledgement_rejects_reset_before_first_write(self) -> None:
        from director_orchestrator.approvals import reset_for_owner_clarification

        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as root:
                store = TaskStore(str(Path(root) / "tasks.sqlite3"))
                record, packet = self._packet(root, store, ProviderName.CLAUDE)
                assignment = record.plan.assignments[0]
                plan = record.plan.model_copy(deep=True)
                transport = LegacyProviderPacketTransport(
                    CapturingProvider(ProviderName.CLAUDE), task_store=store, poll_seconds=1)
                reset_for_owner_clarification(record)
                if replacement:
                    record.plan = plan
                    record.plan_generation += 1
                store.save(record)
                run = ProviderRun(provider=ProviderName.CLAUDE, run_id="stale-claude",
                                  status="WAITING_CALLBACK", result="erased-claude-content")
                with patch.object(store, "save", wraps=store.save) as save:
                    with self.assertRaisesRegex(RuntimeError, "inactive plan generation"):
                        transport._record_claude_dispatch(
                            packet, self._context(record, packet), assignment, run)
                    save.assert_not_called()
                current = store.get(record.task_id)
                self.assertEqual(current.provider_runs, [])
                self.assertNotIn("erased-claude-content", current.model_dump_json())

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
                github_effect_verifier=FakeGitHubEffectVerifier(
                    replace(
                        FakeGitHubEffectVerifier().value,
                        branch="claude/focused",
                        pull_head_ref="claude/focused",
                    )
                ),
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
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:36 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:25 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 06:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:39 UTC | END]]
