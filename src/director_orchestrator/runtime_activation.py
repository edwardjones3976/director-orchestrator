# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Fail-closed activation for the packet coordinator and Completion Gate store.

Nothing here reaches a provider, a network, a merge, or a deployment. This module
decides only *whether* two durable local components may exist at all, and it
refuses to build either one unless its own explicit flag is set and its databases
are provably isolated from every other store the process owns.

Three separate switches are deliberately not collapsed into one:

* ``LANGGRAPH_RUNTIME_ENABLED`` turns the coordinator on as an internal
  coordinator with its own checkpoint and dispatch ledger.
* ``LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED`` additionally permits live provider
  dispatch, and only together with ``ALLOW_PAID_RUNS`` and a configured execution
  profile.
* ``COMPLETION_GATES_ENABLED`` turns on durable gate evidence, and
  ``COMPLETION_GATE_ISOLATED_PROOF_ENABLED`` is required on top of it before any
  runner subprocess may execute.

A missing or invalid configuration is a startup failure, not a silent downgrade:
an operator who believes the coordinator is running must not get a process that
quietly kept the legacy engine as the writer, and an operator who believes it is
off must not get one that dispatches.

# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
Live dispatch additionally requires a wired ``PacketTaskRouter``. Turning the
live flags on without one would surrender legacy dispatch ownership to a
coordinator that has no way to convert an intake task into a work packet, which
leaves no engine executing anything. That configuration is therefore refused at
startup: legacy ownership is preserved and the failure is explicit, rather than
every pending assignment silently stopping for an owner decision.
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Protocol

from .completion_gates import RunnerRegistry, completion_runtime_readiness
from .completion_store import CompletionGateStore
from .config import Settings
from .langgraph_runtime import (
    LangGraphPacketRuntime,
    PacketBoundaryAdapter,
    WriterOwnershipToken,
)


class RuntimeActivationError(RuntimeError):
    """An enabled component could not be built, so the process must not start."""


# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
class PacketTaskRouter(Protocol):
    """Translates one authorized Director assignment into the v1 packet lane.

    The router holds no dispatch, merge, approval, or budget authority. It only
    produces the three artifacts ``LangGraphPacketRuntime.run_packet`` requires:
    a work packet carrying its pinned authority snapshot, the task writer token,
    and the v1 boundary adapter for the assigned provider. Every reservation,
    dispatch, and ledger transition still belongs to the coordinator.

    A router that cannot produce any one of them raises, and the caller fails
    closed rather than falling back to the legacy writer.
    """

    def work_packet(self, record: Any, assignment: Any) -> Mapping[str, Any]:
        """Return the `model-work-packet-v1` document for this assignment."""

    def writer_token(self, record: Any) -> WriterOwnershipToken:
        """Return the single side-effect writer token bound to this task."""

    def boundary_adapter(self, provider: str) -> PacketBoundaryAdapter:
        """Return the enforced v1 packet boundary for one provider."""
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]


class RuntimeActivation:
    """Owns the optional coordinator and gate store for one process."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner_registry: RunnerRegistry | None = None,
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
        packet_task_router: PacketTaskRouter | None = None,
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
    ) -> None:
        self.settings = settings
        self._runner_registry = runner_registry
        self._packet_task_router = packet_task_router
        self._connection: sqlite3.Connection | None = None
        self.runtime: LangGraphPacketRuntime | None = None
        self.completion_store: CompletionGateStore | None = None

        # Validate before building anything so a misconfigured process fails at
        # import time rather than at the first dispatch.
        settings.validate_langgraph_runtime_configuration()
        settings.validate_completion_gate_configuration()

        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
        if (
            settings.langgraph_runtime_readiness()["live_provider_dispatch"]
            and packet_task_router is None
        ):
            raise RuntimeActivationError(
                "live provider dispatch is enabled but no packet task router is "
                "wired, so no engine could execute an intake task. Wire a packet "
                "task router before enabling live dispatch, or disable "
                "LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED so the legacy engine "
                "keeps dispatch ownership."
            )
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]

        if settings.langgraph_runtime_readiness()["runtime_active"]:
            self.runtime = self._build_runtime()
        if settings.completion_gate_readiness()["evidence_recording_active"]:
            self.completion_store = self._build_completion_store()

    def _build_runtime(self) -> LangGraphPacketRuntime:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise RuntimeActivationError(
                "the LangGraph SQLite checkpointer is required to activate the runtime"
            ) from exc
        readiness = self.settings.langgraph_runtime_readiness()
        checkpoint_path = Path(self.settings.langgraph_runtime_checkpoint_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        try:
            runtime = LangGraphPacketRuntime(
                SqliteSaver(connection),
                ledger_database_path=self.settings.langgraph_runtime_ledger_path,
                legacy_database_path=self.settings.sqlite_path,
                checkpoint_namespace=(
                    self.settings.langgraph_runtime_checkpoint_namespace
                ),
                runtime_active=True,
                allow_live_provider_dispatch=readiness["live_provider_dispatch"],
                require_packet_boundary_adapter=True,
            )
        except Exception as exc:
            connection.close()
            raise RuntimeActivationError(
                "the LangGraph packet runtime could not be activated"
            ) from exc
        self._connection = connection
        return runtime

    def _build_completion_store(self) -> CompletionGateStore:
        try:
            return CompletionGateStore(self.settings.completion_gate_store_path)
        except Exception as exc:
            raise RuntimeActivationError(
                "the Completion Gate evidence store could not be opened"
            ) from exc

    @property
    def runner_registry(self) -> RunnerRegistry | None:
        return self._runner_registry

    # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
    @property
    def packet_task_router(self) -> PacketTaskRouter | None:
        return self._packet_task_router

    def coordinator_owns_dispatch(self) -> bool:
        """True when the coordinator, not the legacy engine, owns live dispatch."""

        if self.runtime is None:
            return False
        return bool(self.runtime.readiness().live_provider_dispatch)
    # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]

    def readiness(self) -> dict[str, Any]:
        """One honest surface for `/health`; it never claims provider health."""

        runtime_readiness = self.settings.langgraph_runtime_readiness()
        completion_readiness = self.settings.completion_gate_readiness()
        runtime_surface: dict[str, Any] = dict(runtime_readiness)
        runtime_surface["coordinator_constructed"] = self.runtime is not None
        if self.runtime is not None:
            # The coordinator's own view must agree with configuration. A
            # disagreement means the process is not in the state it advertises.
            observed = self.runtime.readiness().as_dict()
            runtime_surface["packet_transport"] = observed["packet_transport"]
            runtime_surface["checkpoint_namespace_matches"] = (
                observed["checkpoint_namespace"]
                == self.settings.langgraph_runtime_checkpoint_namespace
            )
            runtime_surface["live_provider_dispatch"] = bool(
                observed["live_provider_dispatch"]
            )
        else:
            runtime_surface["packet_transport"] = None
            runtime_surface["checkpoint_namespace_matches"] = False
            runtime_surface["live_provider_dispatch"] = False
        completion_surface: dict[str, Any] = dict(completion_readiness)
        completion_surface.update(
            completion_runtime_readiness(
                enabled=completion_readiness["enabled"],
                isolated_proof_enabled=completion_readiness["isolated_proof_enabled"],
                store_configured=self.completion_store is not None,
                runner_registry=self._runner_registry,
            )
        )
        return {
            "langgraph_runtime": runtime_surface,
            "completion_gates": completion_surface,
        }

    def single_writer_conflict(self) -> str | None:
        """Return why the legacy engine must not dispatch, or None.

        Exactly one engine may own provider dispatch and external side effects for
        a task. When the packet coordinator holds live dispatch authority, the
        legacy engine surrenders it rather than racing for the same effect keys.

        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
        Surrender is not the end of the story: the caller routes the assignment
        through the coordinator instead. The router-less branch below cannot be
        reached by a process that started normally, because ``__init__`` refuses
        that configuration outright; it exists so a directly constructed
        activation still fails closed instead of handing a live lane back to the
        legacy writer.
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
        """

        if not self.coordinator_owns_dispatch():
            return None
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
        if self._packet_task_router is None:
            return (
                "The LangGraph packet coordinator holds live provider dispatch "
                "authority for this deployment but no packet task router is wired, "
                "so no engine can execute this assignment. Wire a packet task "
                "router or disable LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED to "
                "return dispatch authority to the legacy engine."
            )
        # [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
        return (
            "The LangGraph packet coordinator holds live provider dispatch "
            "authority for this deployment, so the legacy engine will not dispatch. "
            "Disable LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED to return dispatch "
            "authority to the legacy engine."
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self.runtime = None
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
