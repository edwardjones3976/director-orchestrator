# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | BEGIN]]
"""Report every unmet live-dispatch prerequisite at once, and change nothing.

The prerequisites for paid, live provider dispatch are enforced by several
validators that each raise on the first problem they find, so an operator
learns them one process death at a time. This script asks those same
validators and readiness functions what they would say, and prints all of
their verdicts together in the order startup reaches them.

Read-only by construction. It sets no environment variable, writes no file,
opens no socket, touches no database, and calls no provider. It removes,
relaxes and defaults nothing: every verdict here is the deterministic
validator's own, reported verbatim. Enabling live dispatch remains an owner
decision taken elsewhere.

Usage:

    PYTHONPATH=src python scripts/live-dispatch-preflight.py

Exit status is 0 when nothing is outstanding and 1 when anything is.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from dataclasses import replace

from director_orchestrator.config import Settings


# The switches that must be on together for live provider dispatch. The
# preflight evaluates the rest of the configuration as though these were
# already set, which is what makes one pass report every prerequisite.
TARGET_SWITCHES: tuple[tuple[str, str], ...] = (
    ("ALLOW_PAID_RUNS", "allow_paid_runs"),
    ("LANGGRAPH_PRIMARY_ENABLED", "langgraph_primary_enabled"),
    (
        "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
        "langgraph_primary_new_intake_enabled",
    ),
    (
        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
        "langgraph_primary_live_provider_dispatch_enabled",
    ),
)

# Variables named by a problem whose own text names none. The validators own
# every rule; this table only restores the operator-facing variable names.
IMPLIED_VARIABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "mutually exclusive",
        (
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
        ),
    ),
    (
        "provider activation bindings",
        ("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON",),
    ),
    ("delegated token file", ("MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE",)),
    ("tenant id", ("MICROSOFT_365_COPILOT_TENANT_ID",)),
    ("expected subject", ("MICROSOFT_365_COPILOT_EXPECTED_SUBJECT",)),
    ("expected object id", ("MICROSOFT_365_COPILOT_EXPECTED_OBJECT_ID",)),
    (
        "completion authority evidence secrets",
        (
            "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
        ),
    ),
    (
        "completion evidence actor allowlist",
        ("LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON",),
    ),
    (
        "host provider bridge endpoint and secret",
        (
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
        ),
    ),
    (
        "ingress secrets must be pairwise distinct",
        (
            "ORCHESTRATOR_SHARED_SECRET",
            "SLACK_GATEWAY_SECRET",
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
            "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
        ),
    ),
    (
        "caps must start at 2",
        (
            "LANGGRAPH_PARALLEL_INITIAL_MAX_CONCURRENCY",
            "LANGGRAPH_PARALLEL_AUTO_SCALE_MAX_CONCURRENCY",
            "LANGGRAPH_PARALLEL_CLEAN_BATCHES_TO_SCALE",
            "LANGGRAPH_PARALLEL_RECOVERY_CLEAN_BATCHES",
        ),
    ),
    (
        "must all be distinct files",
        (
            "SQLITE_PATH",
            "LANGGRAPH_CHECKPOINT_PATH",
            "LANGGRAPH_RUNTIME_CHECKPOINT_PATH",
            "LANGGRAPH_RUNTIME_LEDGER_PATH",
        ),
    ),
    (
        "resume claim database",
        ("SQLITE_PATH", "LANGGRAPH_CHECKPOINT_PATH"),
    ),
)

# Booleans that gate live dispatch before any provider call. Reported by
# `/health` as `execution_profile`; an empty allowlist fails closed.
EXECUTION_PROFILE: tuple[tuple[str, str], ...] = (
    ("projects_configured", "PROJECT_ALLOWLIST"),
    ("repositories_configured", "REPOSITORY_ALLOWLIST"),
    ("starting_refs_configured", "ALLOWED_STARTING_REFS"),
    ("claude_callback_configured", "CLAUDE_CALLBACK_SECRET"),
)

VARIABLE_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# Field names whose value must never reach the output under any drift: no
# secret, token, endpoint, path, allowlist entry, or owner identifier.
SENSITIVE_FIELD = re.compile(
    r"secret|token|key|password|dsn|url|endpoint|path|allowlist"
    r"|owner|tenant|subject|object_id|actor"
)


def redact(text: str, settings: Settings) -> str:
    """Replace any configured sensitive value that reached `text`.

    Nothing should ever match: the validators raise static prose. This is the
    standing guarantee that a future message interpolating a value cannot turn
    the preflight into a secret printer.
    """

    for spec in dataclasses.fields(settings):
        if not SENSITIVE_FIELD.search(spec.name):
            continue
        value = getattr(settings, spec.name)
        items = value if isinstance(value, tuple) else (value,)
        for item in items:
            if isinstance(item, str) and len(item) >= 6 and item in text:
                text = text.replace(item, "<redacted>")
    return text


def variables(problem: str) -> tuple[str, ...]:
    """The exact environment variable names a problem is about."""

    named = tuple(dict.fromkeys(VARIABLE_NAME.findall(problem)))
    if named:
        return named
    for fragment, names in IMPLIED_VARIABLES:
        if fragment in problem:
            return names
    return ()


def split(validate) -> list[str]:
    """Run one validator and return its problems, one per entry."""

    try:
        validate()
    except ValueError as exc:
        _, joined, tail = str(exc).partition(" configuration: ")
        body = tail if joined else str(exc)
        return [item.strip() for item in body.split("; ") if item.strip()]
    return []


def unmet(settings: Settings) -> list[tuple[str, str]]:
    """Every unmet prerequisite as (stage, problem), in startup order."""

    problems: list[tuple[str, str]] = []
    for name, attribute in TARGET_SWITCHES:
        if not getattr(settings, attribute):
            problems.append(("live dispatch switches", f"{name} must be enabled"))
    # main.py runs these three before anything is constructed.
    target = replace(
        settings, **{attribute: True for _, attribute in TARGET_SWITCHES}
    )
    for stage, validate in (
        ("workload policy", target.validate_workload_policy_configuration),
        ("langgraph shadow lane", target.validate_langgraph_configuration),
        ("langgraph primary", target.validate_langgraph_primary_configuration),
    ):
        problems.extend((stage, item) for item in split(validate))
    # main.py then builds DefaultPrimaryActivation, which refuses paid runs
    # without live dispatch requested. Evaluated on the configured settings,
    # not the target: this one is about the deployment as it stands now.
    if (
        settings.langgraph_primary_enabled
        and settings.allow_paid_runs
        and not settings.langgraph_primary_live_provider_dispatch_enabled
    ):
        problems.append(
            (
                "paid/live coupling",
                "ALLOW_PAID_RUNS is enabled but "
                "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED is disabled: "
                "startup refuses this pair, so the deployment is down until "
                "both move together",
            )
        )
    for stage, validate in (
        ("parallel dispatch", target.validate_parallel_dispatch_configuration),
        ("langgraph runtime", target.validate_langgraph_runtime_configuration),
    ):
        problems.extend((stage, item) for item in split(validate))
    profile = settings.execution_profile_readiness()
    for key, name in EXECUTION_PROFILE:
        if not profile[key]:
            problems.append(
                (
                    "execution profile",
                    f"{name} must be configured: an empty execution profile "
                    "fails closed before any provider call",
                )
            )
    return problems


def report(settings: Settings, problems: list[tuple[str, str]]) -> list[str]:
    """The whole preflight as output lines. Names and booleans only."""

    lines = [
        "Live dispatch preflight: reports only, enables nothing.",
        "",
        "Configured switches:",
    ]
    for name, attribute in TARGET_SWITCHES + (
        (
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
            "langgraph_primary_offline_simulation_enabled",
        ),
        (
            "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED",
            "langgraph_parallel_external_dispatch_enabled",
        ),
    ):
        lines.append(f"  {name}: {str(getattr(settings, attribute)).lower()}")
    lines.append("")
    if not problems:
        lines.append("No unmet prerequisite. Configuration is not authority:")
        lines.append("acceptance, evidence, and owner approval are separate gates.")
        return lines
    lines.append(f"Unmet prerequisites ({len(problems)}), in startup order:")
    for index, (stage, problem) in enumerate(problems, start=1):
        lines.append(f"  {index:>2}. [{stage}] {redact(problem, settings)}")
        named = variables(problem)
        if named:
            lines.append(f"      variables: {', '.join(named)}")
    return lines


def main() -> int:
    try:
        settings = Settings()
    except ValueError as exc:
        print(f"Settings could not be constructed: {exc}")
        return 1
    problems = unmet(settings)
    print("\n".join(report(settings, problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | END]]
