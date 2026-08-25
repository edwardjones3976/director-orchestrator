# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Deterministic dependency-ready scheduling semantics for one plan.

This module answers a single question with no side effects: given a plan and the
set of assignments that are already accepted or already deferred, which
assignments are *ready* to run, which are *blocked*, and which single one runs
next.

It deliberately does **not** dispatch anything in parallel.
`parallel_external_dispatch_permitted` is a constant `False` and is returned in
every decision so the boundary is explicit at the call site rather than implied
by absence. Concurrent external dispatch would require idempotency, effect-key,
budget-reservation, and ambiguity-recovery semantics that hold across
simultaneous in-flight provider calls; none of that exists yet, so the scheduler
exposes readiness while execution stays strictly one assignment at a time.

Readiness is computed from declared dependencies only. Deferral is transitive: an
assignment whose dependency was deferred for provider unavailability is itself
deferred rather than blocked, so independent safe work can continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import Assignment


#: Concurrent external provider dispatch is out of scope for this scheduler.
PARALLEL_EXTERNAL_DISPATCH_PERMITTED = False


class SchedulingError(ValueError):
    """The declared dependency graph is unusable, so nothing may be scheduled."""


@dataclass(frozen=True)
class SchedulingDecision:
    """A complete, deterministic view of what may run now."""

    ready: tuple[int, ...]
    blocked: Mapping[int, tuple[int, ...]]
    deferred: tuple[int, ...]
    completed: tuple[int, ...]
    next_sequence: int | None
    parallel_external_dispatch_permitted: bool = (
        PARALLEL_EXTERNAL_DISPATCH_PERMITTED
    )

    def is_ready(self, sequence: int) -> bool:
        return sequence in self.ready

    def missing_dependencies(self, sequence: int) -> tuple[int, ...]:
        return tuple(self.blocked.get(sequence, ()))


def _validate_graph(assignments: Sequence[Assignment]) -> dict[int, Assignment]:
    by_sequence: dict[int, Assignment] = {}
    for assignment in assignments:
        if assignment.sequence in by_sequence:
            raise SchedulingError("assignment sequences must be unique")
        by_sequence[assignment.sequence] = assignment
    for assignment in assignments:
        declared = list(assignment.depends_on)
        if len(set(declared)) != len(declared):
            raise SchedulingError(
                f"assignment {assignment.sequence}: dependencies must be unique"
            )
        for dependency in declared:
            if dependency not in by_sequence:
                raise SchedulingError(
                    f"assignment {assignment.sequence}: dependency {dependency} does not exist"
                )
            if dependency >= assignment.sequence:
                # Enforced by execution policy as well. Repeated here so a
                # scheduler caller cannot be handed a cyclic graph by a
                # hand-built or migrated plan.
                raise SchedulingError(
                    f"assignment {assignment.sequence}: dependency {dependency} "
                    "must be an earlier assignment"
                )
    return by_sequence


def dependency_ready_decision(
    assignments: Sequence[Assignment],
    *,
    completed: Iterable[int],
    deferred: Iterable[int] = (),
) -> SchedulingDecision:
    """Classify every pending assignment as ready, deferred, or blocked.

    `completed` is the set of assignments with an accepted outcome. `deferred` is
    the set stood down for provider unavailability; deferral propagates to
    dependants so unrelated safe work stays eligible.
    """

    by_sequence = _validate_graph(assignments)
    completed_set = {int(item) for item in completed}
    unknown = completed_set - set(by_sequence)
    if unknown:
        raise SchedulingError(
            "completed set names assignments outside this plan: "
            + ", ".join(str(item) for item in sorted(unknown))
        )
    deferred_set = {int(item) for item in deferred} & set(by_sequence)

    ready: list[int] = []
    blocked: dict[int, tuple[int, ...]] = {}
    # Ascending order makes deferral propagation a single pass: every dependency
    # is a strictly earlier sequence, so it is already classified.
    for sequence in sorted(by_sequence):
        if sequence in completed_set:
            continue
        missing = tuple(
            dependency
            for dependency in sorted(by_sequence[sequence].depends_on)
            if dependency not in completed_set
        )
        if not missing:
            if sequence not in deferred_set:
                ready.append(sequence)
            continue
        if any(dependency in deferred_set for dependency in missing):
            deferred_set.add(sequence)
            continue
        blocked[sequence] = missing

    return SchedulingDecision(
        ready=tuple(ready),
        blocked=dict(blocked),
        deferred=tuple(sorted(deferred_set)),
        completed=tuple(sorted(completed_set)),
        # Exactly one assignment is handed out. Even when several are ready, the
        # runtime dispatches them sequentially in ascending sequence order.
        next_sequence=ready[0] if ready else None,
    )
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
