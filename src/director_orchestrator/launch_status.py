# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:22 UTC | BEGIN]]
"""Measure launch readiness instead of asserting it.

Three things this answers, each of which was previously a claim in prose:

Which of the five "done" terms hold, read from the live health document rather
than from memory of the last time someone looked.

How many owner touchpoints remain on the launch path. That count, k, dominates
the schedule: T is roughly k times the owner's round-trip plus agent work, so
batching k from about twenty into about three is worth more than any amount of
parallelism. Amdahl caps agent speedup at about 1.25x while four fifths of the
remaining wall time is the serial owner-gated chain.

Whether an optional re-check is worth running. A check earns its cost only when
its result could change a decision, and with no state change since the last
verified result that probability is zero. Security, approval and exact-SHA gates
are never optional and are refused by name.

Every term is derived from an input, never hard-coded to a hoped-for value. A
term with no machine-readable source is reported as attested, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Source(StrEnum):
    MEASURED = "measured"      # read from the live health document
    ATTESTED = "attested"      # requires a human or an out-of-band record


# Never skippable, whatever the stopping rule computes.
NON_OPTIONAL_CHECKS: frozenset[str] = frozenset(
    {"security", "approval", "exact_sha_review", "ci"}
)


@dataclass(frozen=True, slots=True)
class Term:
    name: str
    holds: bool
    source: Source
    evidence: str


def done_terms(health: dict, *, restore_drill_recorded: bool = False) -> tuple[Term, ...]:
    """The five-term conjunction, read from a live health document."""

    primary = health.get("langgraph_primary", {})
    owner = primary.get("new_intake_owner")
    runtime_active = bool(primary.get("primary_runtime_active"))
    live_requested = bool(primary.get("live_provider_dispatch_requested"))
    ledger = bool(primary.get("effect_ledger_ready"))
    bindings = bool(primary.get("provider_activation_bindings_configured"))

    return (
        Term(
            "intake_reaches_the_primary_plane",
            owner == "langgraph",
            Source.MEASURED,
            f"new_intake_owner={owner!r}",
        ),
        Term(
            "real_execution_of_a_wave",
            runtime_active and live_requested,
            Source.MEASURED,
            f"primary_runtime_active={runtime_active}, "
            f"live_provider_dispatch_requested={live_requested}",
        ),
        Term(
            "durable_results",
            ledger,
            Source.MEASURED,
            f"effect_ledger_ready={ledger}",
        ),
        Term(
            "required_controls_installed",
            bindings,
            Source.MEASURED,
            f"provider_activation_bindings_configured={bindings}",
        ),
        Term(
            # No health field reports a restore drill, so this cannot be
            # measured here. Reporting it as attested keeps the gap visible
            # rather than letting a default stand in for evidence.
            "recovery_proof",
            bool(restore_drill_recorded),
            Source.ATTESTED,
            "restore drill record supplied by the caller",
        ),
    )


# The launch path, as enumerated by the owner. `probe` names the health field
# that settles it, or None when only the owner can.
OWNER_TOUCHPOINTS: tuple[tuple[str, str | None], ...] = (
    ("acceptance_mode_choice", None),
    ("drive_root_ace", None),
    ("four_service_accounts", None),
    ("protected_runtimes", None),
    ("issuer_install_app", None),
    ("issuer_install_runtime_adapter", None),
    ("native_login_codex", None),
    ("native_login_cursor", None),
    ("broker_init_and_registry_digest", None),
    ("ci_issuer_adoption", None),
    ("snapshot_root", "authority_root_configured"),
    ("authority_pins", "authority_pins_valid"),
    ("provider_binding_map", "provider_activation_bindings_configured"),
    ("effect_gateway_decision", None),
    ("actor_allowlist", "completion_evidence_actor_allowlist_configured"),
    ("receipt_one", None),
    ("receipt_two", None),
    ("receipt_three", None),
    ("accept", None),
    ("activate", "live_provider_dispatch_requested"),
    ("token_rotation", None),
    ("merge_pull_requests", None),
    ("m0_redeploy", None),
)


@dataclass(frozen=True, slots=True)
class Touchpoint:
    name: str
    satisfied: bool
    source: Source


def owner_touchpoints(health: dict) -> tuple[Touchpoint, ...]:
    primary = health.get("langgraph_primary", {})
    out = []
    for name, probe in OWNER_TOUCHPOINTS:
        if probe is None:
            out.append(Touchpoint(name, False, Source.ATTESTED))
        else:
            out.append(Touchpoint(name, bool(primary.get(probe)), Source.MEASURED))
    return tuple(out)


def remaining_k(health: dict) -> int:
    """Outstanding owner touchpoints. This is the term that sets the schedule."""

    return sum(1 for t in owner_touchpoints(health) if not t.satisfied)


def estimated_duration(k: int, owner_round_trip_hours: float, agent_work_hours: float) -> float:
    """T = k * L_owner + sum(w_i)."""

    if k < 0 or owner_round_trip_hours < 0 or agent_work_hours < 0:
        raise ValueError("durations and counts cannot be negative")
    return k * owner_round_trip_hours + agent_work_hours


def max_speedup(serial_fraction: float, agents: int) -> float:
    """Amdahl. `serial_fraction` is the owner-gated chain that cannot parallelise."""

    if not 0.0 <= serial_fraction <= 1.0:
        raise ValueError("serial fraction must be between 0 and 1")
    if agents < 1:
        raise ValueError("agents must be at least 1")
    parallel = 1.0 - serial_fraction
    return 1.0 / (serial_fraction + parallel / agents)


def should_recheck(
    check: str,
    *,
    state_changed_since_last_result: bool,
    probability_result_changes_decision: float,
    cost_of_wrong_decision: float,
    cost_of_check: float,
) -> tuple[bool, str]:
    """Run an optional check only when its expected value exceeds its cost."""

    if check in NON_OPTIONAL_CHECKS:
        return True, f"{check} is never optional"
    if not 0.0 <= probability_result_changes_decision <= 1.0:
        raise ValueError("probability must be between 0 and 1")
    if not state_changed_since_last_result:
        # Nothing has moved, so the result cannot differ from the verified one.
        return False, "no state change since the last verified result"
    expected = probability_result_changes_decision * cost_of_wrong_decision
    if expected > cost_of_check:
        return True, f"expected value {expected:g} exceeds cost {cost_of_check:g}"
    return False, f"expected value {expected:g} does not exceed cost {cost_of_check:g}"
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:22 UTC | END]]
