# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
"""Production packet transports for the primary repository providers.

The packet coordinator owns idempotency and timeout decisions.  These transports
only adapt an already validated v1 packet to the existing authenticated provider
clients and normalize the terminal provider evidence back into the v1 result
contract.  Gemini and Perplexity remain on their narrower typed/public-only
lanes: a generic repository packet must never bypass those privacy contracts.
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .interactions import (
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_identifier_digest,
    start_interaction,
    transition_interaction,
)
from .langgraph_runtime import PacketBoundaryAdapter, ProviderAdapterContext
from .models import Assignment, ProviderName, ProviderRun
from .packet_contracts import canonical_json_bytes, validate_packet
from .packet_routing import AuthoritySnapshotResolver, PacketRoutingError
from .providers import ProviderAdapter
from .store import TaskStore


_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_TERMINAL_SUCCESS = {"FINISHED", "completed"}
_TERMINAL_FAILURE = {
    "ERROR", "CANCELLED", "EXPIRED", "failed", "cancelled", "incomplete",
    "requires_action", "budget_exceeded",
}


class LocalGitAuthoritySnapshotResolver(AuthoritySnapshotResolver):
    """Bind a single allowlisted repository to its clean local exact HEAD."""

    def __init__(self, repository: str, root: str) -> None:
        if not repository.strip() or not root.strip():
            raise ValueError("repository and snapshot root are required")
        self.repository = repository.strip().removesuffix(".git").lower()
        self.root = Path(root).resolve(strict=True)

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-c", f"safe.directory={self.root}", *arguments),
            cwd=self.root, shell=False, check=False,
            capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0:
            raise PacketRoutingError("repository authority snapshot could not be authenticated")
        return completed.stdout

    @staticmethod
    def _normalize_remote(remote: str) -> str:
        value = remote.strip().removesuffix(".git")
        if value.startswith("git@github.com:"):
            value = value.removeprefix("git@github.com:")
        elif "github.com/" in value:
            value = value.split("github.com/", 1)[1]
        return value.strip("/").lower()

    def resolve(self, record: Any, assignment: Assignment) -> list[dict[str, str]]:
        if (assignment.repository or "").lower().removesuffix(".git") != self.repository:
            raise PacketRoutingError("assignment repository is outside the snapshot resolver")
        remote = self._normalize_remote(self._git("remote", "get-url", "origin"))
        if remote != self.repository:
            raise PacketRoutingError("local repository remote does not match assignment authority")
        status = self._git("status", "--porcelain=v2", "--untracked-files=all")
        if status:
            raise PacketRoutingError("authority snapshot requires a clean repository worktree")
        starting_ref = assignment.starting_ref.strip()
        head = self._git("rev-parse", "--verify", f"{starting_ref}^{{commit}}").strip()
        if not _GIT_SHA.fullmatch(head):
            raise PacketRoutingError("repository starting ref is not an exact commit")
        remote_refs = {
            item.strip()
            for item in self._git(
                "for-each-ref",
                "--contains",
                head,
                "--format=%(refname)",
                "refs/remotes/origin/",
            ).splitlines()
            if item.strip() and item.strip() != "refs/remotes/origin/HEAD"
        }
        if not remote_refs:
            raise PacketRoutingError(
                "repository starting commit is not present on an origin tracking ref"
            )
        if not _GIT_SHA.fullmatch(starting_ref):
            normalized_ref = starting_ref.removeprefix("refs/heads/").removeprefix(
                "origin/"
            )
            expected_remote_ref = f"refs/remotes/origin/{normalized_ref}"
            remote_head = self._git(
                "rev-parse", "--verify", f"{expected_remote_ref}^{{commit}}"
            ).strip()
            if remote_head != head:
                raise PacketRoutingError(
                    "repository starting ref does not match its origin tracking ref"
                )
        tree = self._git("ls-tree", "-r", "--full-tree", head)
        snapshot: list[dict[str, str]] = []
        for line in tree.splitlines():
            metadata, separator, path = line.partition("\t")
            parts = metadata.split()
            if not separator or len(parts) != 3 or parts[1] != "blob":
                continue
            blob_sha = parts[2]
            if not _GIT_SHA.fullmatch(blob_sha):
                raise PacketRoutingError("repository tree contains a malformed blob identity")
            snapshot.append({"path": path, "commit_sha": head, "blob_sha": blob_sha})
            if len(snapshot) > 512:
                raise PacketRoutingError("repository snapshot exceeds the bounded packet inventory")
        if not snapshot:
            raise PacketRoutingError("repository snapshot contains no tracked files")
        return snapshot


class LegacyProviderPacketTransport:
    """Run one legacy provider adapter behind the validated packet boundary."""

    def __init__(
        self,
        adapter: ProviderAdapter,
        *,
        task_store: TaskStore,
        poll_seconds: int,
    ) -> None:
        if adapter.name == ProviderName.OPENAI:
            raise ValueError("OpenAI Director review is not a provider assignment transport")
        self.adapter = adapter
        self.task_store = task_store
        self.poll_seconds = max(1, int(poll_seconds))

    def _assignment(self, packet: Mapping[str, Any], provider: ProviderName) -> Assignment:
        sequence_text = str(packet["assignment_id"]).rsplit("-", 1)[-1]
        if not sequence_text.isdigit():
            raise ValueError("packet assignment identity lacks a numeric sequence")
        sequence = int(sequence_text)
        record = self.task_store.get(str(packet["task_id"]))
        if record is None or record.plan is None:
            raise ValueError("packet task plan is unavailable")
        assignment = next(
            (item for item in record.plan.assignments if item.sequence == sequence),
            None,
        )
        if assignment is None or assignment.agent != provider:
            raise ValueError("packet assignment no longer matches the Director plan")
        assignment_digest = hashlib.sha256(
            canonical_json_bytes(assignment.model_dump(mode="json"))
        ).hexdigest()
        if not str(packet["idempotency_key"]).endswith(":" + assignment_digest):
            raise ValueError("packet assignment digest is stale")
        return assignment.model_copy(update={"starting_ref": str(packet["starting_ref"])})

    def _record_claude_dispatch(
        self,
        packet: Mapping[str, Any],
        context: ProviderAdapterContext,
        assignment: Assignment,
        run: ProviderRun,
    ) -> None:
        record = self.task_store.get(str(packet["task_id"]))
        if record is None:
            raise RuntimeError("Claude callback task record is unavailable")
        run.assignment_sequence = assignment.sequence
        run.interaction_id = str(packet["correlation"]["correlation_id"])
        run.evidence_source = "provider_authenticated"
        backend = build_transport_evidence(
            transport=ProviderTransport.CLAUDE_ROUTINE,
            source=TransportEvidenceSource.BACKEND_OBSERVATION,
            idempotency_key=context.idempotency_key,
        )
        event = start_interaction(
            task_id=record.task_id,
            interaction_id=run.interaction_id,
            plan_generation=record.plan_generation,
            assignment_sequence=assignment.sequence,
            provider=assignment.agent.value,
            phase=ProviderInteractionPhase.DISPATCH,
            attempt=1,
            transport_evidence=backend,
            effect_key=canonical_identifier_digest(context.idempotency_key),
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
        for state in (
            ProviderInteractionState.ATTEMPTED,
            ProviderInteractionState.DISPATCHED,
            ProviderInteractionState.ACKNOWLEDGED,
        ):
            event = transition_interaction(
                record.provider_interaction_ledger,
                interaction_id=run.interaction_id,
                next_state=state,
                transport_evidence=backend,
            )
            record.provider_interaction_ledger = append_interaction_event(
                record.provider_interaction_ledger, event
            )
        if any(existing.run_id == run.run_id for existing in record.provider_runs):
            raise RuntimeError("Claude provider run identity is already recorded")
        record.provider_runs.append(run)
        self.task_store.save(record)

    async def _wait(self, packet: Mapping[str, Any], run: ProviderRun, deadline: float) -> ProviderRun:
        if run.status == "WAITING_CALLBACK":
            while time.monotonic() < deadline:
                record = self.task_store.get(str(packet["task_id"]))
                if record is None:
                    raise RuntimeError("Claude callback task record disappeared")
                observed = next(
                    (item for item in record.provider_runs if item.run_id == run.run_id),
                    None,
                )
                if observed is None:
                    raise RuntimeError("Claude callback run binding disappeared")
                if observed.status != "WAITING_CALLBACK":
                    return observed
                await asyncio.sleep(self.poll_seconds)
            raise TimeoutError("Claude callback did not arrive before the packet deadline")

        current = run
        no_response = 0
        while current.status not in _TERMINAL_SUCCESS | _TERMINAL_FAILURE:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{current.provider.value} packet run timed out")
            await asyncio.sleep(self.poll_seconds)
            try:
                observed = await self.adapter.poll(current)
            except Exception:
                no_response += 1
                if no_response >= 3:
                    raise
                continue
            if observed.provider != current.provider or observed.run_id != current.run_id:
                raise RuntimeError("provider poll changed the immutable run identity")
            current = observed
        return current

    @staticmethod
    def _usage(raw: Mapping[str, Any]) -> dict[str, Any] | None:
        value = raw.get("usage")
        if not isinstance(value, Mapping):
            return None
        normalized = {
            "model": value.get("model"),
            "input_tokens": value.get("input_tokens", value.get("prompt_tokens")),
            "output_tokens": value.get("output_tokens", value.get("completion_tokens")),
            "total_tokens": value.get("total_tokens"),
            "cost_usd": value.get("cost_usd"),
        }
        return {key: item for key, item in normalized.items() if item is not None} or None

    def _result(
        self, packet: Mapping[str, Any], run: ProviderRun
    ) -> Mapping[str, Any]:
        provider = self.adapter.name
        if run.provider != provider:
            raise ValueError("authenticated provider readback identity changed")
        failed = run.status in _TERMINAL_FAILURE or not run.result
        if run.status == "WAITING_CALLBACK":
            raise ValueError("provider readback is not terminal")
        unverified_effects = bool(packet["allowed_external_effects"]) and not failed
        packet_status = (
            "FAILED"
            if failed
            else "OWNER_DECISION_REQUIRED"
            if unverified_effects
            else "SUCCEEDED"
        )
        result: dict[str, Any] = {
            "schema_version": "1.0",
            "task_id": packet["task_id"],
            "assignment_id": packet["assignment_id"],
            "idempotency_key": packet["idempotency_key"],
            "authority_snapshot_digest": packet["authority_snapshot_digest"],
            "correlation": dict(packet["correlation"]),
            "provider": provider.value,
            "run_reference": run.run_id,
            "status": packet_status,
            "outcome": run.result or f"Provider ended with status {run.status}",
            "artifacts": (
                [{"kind": "provider_session", "location": run.url, "digest": None}]
                if run.url else []
            ),
            "checks": [],
            "acceptance": [
                {
                    "check": str(check),
                    "status": "BLOCKED" if failed else "NOT_RUN",
                    "evidence": (
                        run.result
                        if failed
                        else "Provider output was recorded; deterministic Director review "
                        "must evaluate this check."
                    ),
                }
                for check in packet["acceptance_checks"]
            ],
            "risks": (
                ["Provider output did not include machine-verifiable side-effect evidence."]
                if unverified_effects else []
            ),
            "blockers": (
                [f"provider terminal status: {run.status}"]
                if failed
                else ["Allowed external effects require authenticated reconciliation."]
                if unverified_effects
                else []
            ),
            "requested_decisions": (
                ["Reconcile the provider's authenticated external-effect record."]
                if unverified_effects else []
            ),
            "side_effects": [],
            "usage": self._usage(run.raw),
            "revision_recommended": failed,
        }
        return validate_packet("result", result)

    def authenticated_readback(
        self,
        work_packet_json: bytes,
        provider_run: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        packet = validate_packet("work", json.loads(work_packet_json.decode("utf-8")))
        run = ProviderRun.model_validate(provider_run)
        self._assignment(packet, self.adapter.name)
        return self._result(packet, run)

    def send(
        self, work_packet_json: bytes, *, context: ProviderAdapterContext
    ) -> Mapping[str, Any]:
        packet = validate_packet("work", json.loads(work_packet_json.decode("utf-8")))
        provider = self.adapter.name
        assignment = self._assignment(packet, provider)
        handoff = (
            f"Validated provider-neutral work packet {context.work_packet_digest}; "
            f"task {context.task_id}; assignment {context.assignment_id}."
        )

        async def execute() -> ProviderRun:
            run = await self.adapter.dispatch(assignment, context=handoff)
            if provider == ProviderName.CLAUDE:
                self._record_claude_dispatch(packet, context, assignment, run)
            return await self._wait(
                packet, run, time.monotonic() + context.timeout_seconds
            )

        run = asyncio.run(execute())
        return self._result(packet, run)


def build_primary_packet_adapters(
    providers: Mapping[ProviderName, ProviderAdapter],
    *,
    task_store: TaskStore,
    poll_seconds: int,
) -> dict[str, PacketBoundaryAdapter]:
    result: dict[str, PacketBoundaryAdapter] = {}
    for provider in (
        ProviderName.CLAUDE,
        ProviderName.CURSOR,
        ProviderName.GEMINI,
        ProviderName.PERPLEXITY,
    ):
        adapter = providers.get(provider)
        if adapter is None:
            continue
        result[provider.value] = PacketBoundaryAdapter(
            provider.value,
            LegacyProviderPacketTransport(
                adapter, task_store=task_store, poll_seconds=poll_seconds
            ),
        )
    return result
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
