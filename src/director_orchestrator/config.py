# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:05 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]
from __future__ import annotations

import hmac
import json
import math
import os
import re
import time
from pathlib import Path
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _positive_float(name: str, default: float) -> float:
    value = _float(name, default)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return value


def _csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def _csv_exact(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _repository_path_map(name: str) -> tuple[tuple[str, str], ...]:
    """Parse an explicit repository-to-worktree map without guessing local paths."""

    raw = os.getenv(name, "{}").strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    pairs: list[tuple[str, str]] = []
    for repository, path in payload.items():
        if not isinstance(repository, str) or not isinstance(path, str):
            return ()
        normalized_repository = repository.strip().lower().removesuffix(".git")
        normalized_path = path.strip()
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", normalized_repository):
            return ()
        if not normalized_path or not os.path.isabs(normalized_path):
            return ()
        pairs.append((normalized_repository, normalized_path))
    return tuple(sorted(pairs))


# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.realpath(value.strip())))


def _paths_are_isolated(left: str, right: str) -> bool:
    """Compare path spelling and file identity, failing closed on stat races."""

    normalized_left = _normalized_path(left)
    normalized_right = _normalized_path(right)
    if normalized_left == normalized_right:
        return False
    left_exists = os.path.exists(normalized_left)
    right_exists = os.path.exists(normalized_right)
    if left_exists and right_exists:
        try:
            return not os.path.samefile(normalized_left, normalized_right)
        except OSError:
            return False
    # A path that disappears during identity inspection is not proven isolated.
    if left_exists != os.path.exists(normalized_left) or right_exists != os.path.exists(
        normalized_right
    ):
        return False
    return True


def langgraph_resume_claim_path(checkpoint_path: str) -> str:
    return checkpoint_path.strip() + ".resume-claims.sqlite3"


def _default_langgraph_checkpoint_path() -> str:
    legacy_path = os.getenv("SQLITE_PATH", "/data/director.sqlite3").strip()
    base, extension = os.path.splitext(legacy_path)
    return f"{base}.langgraph-shadow{extension or '.sqlite3'}"


def _default_langgraph_checkpoint_namespace() -> str:
    profile = os.getenv("APP_ENV", "local").strip().lower() or "local"
    safe_profile = "-".join(part for part in profile.replace("_", "-").split("-") if part)
    return f"director-{safe_profile}-shadow-v1"
# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]


# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
def _sibling_database_path(suffix: str) -> str:
    """Derive a distinct sibling database beside the legacy task store."""

    legacy_path = os.getenv("SQLITE_PATH", "/data/director.sqlite3").strip()
    base, extension = os.path.splitext(legacy_path)
    return f"{base}.{suffix}{extension or '.sqlite3'}"


def _default_langgraph_runtime_checkpoint_path() -> str:
    return _sibling_database_path("langgraph-runtime")


def _default_langgraph_runtime_ledger_path() -> str:
    return _sibling_database_path("langgraph-runtime-ledger")


def _default_completion_gate_store_path() -> str:
    return _sibling_database_path("completion-gates")


def _default_completion_worker_store_path() -> str:
    return _sibling_database_path("completion-workers")


def _default_langgraph_runtime_namespace() -> str:
    profile = os.getenv("APP_ENV", "local").strip().lower() or "local"
    safe_profile = "-".join(part for part in profile.replace("_", "-").split("-") if part)
    return f"director-{safe_profile}-runtime-v1"


def _default_langgraph_primary_namespace() -> str:
    profile = os.getenv("APP_ENV", "local").strip().lower() or "local"
    safe_profile = "-".join(part for part in profile.replace("_", "-").split("-") if part)
    return f"director-{safe_profile}-primary-v1"


def _pairwise_isolated(paths: tuple[str, ...]) -> bool:
    """Every configured store must be a distinct file from every other one.

    Fails closed on an empty entry, on spelling collisions, and on hard/soft
    links that resolve to one inode, because a shared database would let a
    second writer observe or overwrite the coordinator's durable evidence.
    """

    candidates = [item.strip() for item in paths]
    if any(not item for item in candidates):
        return False
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if not _paths_are_isolated(left, right):
                return False
    return True
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    orchestrator_base_url: str = os.getenv("ORCHESTRATOR_BASE_URL", "http://orchestrator:8080")
    shared_secret: str = os.getenv("ORCHESTRATOR_SHARED_SECRET", "")
    allow_paid_runs: bool = _bool("ALLOW_PAID_RUNS", False)
    max_agent_hops: int = _int("MAX_AGENT_HOPS", 6)
    max_revisions: int = _int("MAX_REVISIONS", 2)
    max_task_budget_usd: float = _float("MAX_TASK_BUDGET_USD", 2.0)
    max_daily_provider_budget_usd: float = _positive_float(
        "MAX_DAILY_PROVIDER_BUDGET_USD", 1_000.0
    )
    # The owner-approved workload router is an active deterministic gate. Shadow
    # mode remains available for observation-only comparison, but new deployments
    # enforce the policy unless an operator deliberately selects shadow mode.
    workload_policy_enabled: bool = field(
        default_factory=lambda: _bool("WORKLOAD_POLICY_ENABLED", True)
    )
    workload_policy_mode: str = field(
        default_factory=lambda: os.getenv(
            "WORKLOAD_POLICY_MODE", "enforced"
        ).strip().lower()
    )
    workload_policy_window_assignments: int = field(
        default_factory=lambda: _int("WORKLOAD_POLICY_WINDOW_ASSIGNMENTS", 20)
    )
    workload_policy_shadow_assignments: int = field(
        default_factory=lambda: _int("WORKLOAD_POLICY_SHADOW_ASSIGNMENTS", 5)
    )
    provider_poll_seconds: int = _int("PROVIDER_POLL_SECONDS", 15)
    provider_timeout_seconds: int = _int("PROVIDER_TIMEOUT_SECONDS", 3600)
    sqlite_path: str = os.getenv("SQLITE_PATH", "/data/director.sqlite3")
    # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
    langgraph_shadow_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_SHADOW_ENABLED", True)
    )
    langgraph_approval_resume_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_APPROVAL_RESUME_ENABLED", True)
    )
    langgraph_checkpoint_path: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_CHECKPOINT_PATH", _default_langgraph_checkpoint_path()
        ).strip()
    )
    langgraph_checkpoint_namespace: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_CHECKPOINT_NAMESPACE", _default_langgraph_checkpoint_namespace()
        ).strip()
    )
    # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]
    # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
    # Runtime activation is a second, stricter switch than the shadow lane. Both
    # default off: an operator must opt in explicitly, and live provider dispatch
    # needs its own flag on top of that.
    langgraph_runtime_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_RUNTIME_ENABLED", False)
    )
    langgraph_runtime_live_dispatch_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED", False)
    )
    langgraph_runtime_task_writer_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED", False)
    )
    # The primary runtime is separate from the historical SQLite packet runtime.
    # Both switches default off. The second switch is deliberately narrower: it
    # authorizes an atomic ownership transition only after the application has
    # constructed and validated the production coordinator.
    langgraph_primary_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_PRIMARY_ENABLED", False)
    )
    langgraph_primary_new_intake_enabled: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED", False)
    )
    langgraph_primary_offline_simulation_enabled: bool = field(
        default_factory=lambda: _bool(
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED", False
        )
    )
    langgraph_primary_live_provider_dispatch_enabled: bool = field(
        default_factory=lambda: _bool(
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", False
        )
    )
    langgraph_primary_required_providers: tuple[str, ...] = field(
        default_factory=lambda: _csv(
            "LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS",
            "openai,claude,cursor,grok,antigravity,perplexity,firecrawl,github-copilot,microsoft-365-copilot",
        )
    )
    langgraph_primary_provider_activation_bindings_json: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON", "{}"
        ).strip()
    )
    # Optional immutable canary authority. Both values are required together;
    # ordinary deployments retain origin-tracking branch verification.
    langgraph_primary_acceptance_mode: str = field(default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1"))
    langgraph_primary_acceptance_registry_path: str = field(default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH", ""))
    langgraph_primary_acceptance_registry_sha256: str = field(default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256", ""))
    langgraph_primary_acceptance_journal_path: str = field(default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH", ""))

    langgraph_primary_pinned_commit_sha: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA", ""
        ).strip().lower()
    )
    langgraph_primary_pinned_tree_sha: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_PINNED_TREE_SHA", ""
        ).strip().lower()
    )
    langgraph_primary_snapshot_max_files: int = field(
        default_factory=lambda: _int("LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES", 512)
    )
    langgraph_primary_postgres_dsn: str = field(
        default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_POSTGRES_DSN", "")
    )
    langgraph_primary_postgres_schema: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA", "director_primary_v1"
        ).strip()
    )
    langgraph_primary_checkpoint_namespace: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE",
            _default_langgraph_primary_namespace(),
        ).strip()
    )
    langgraph_primary_owner_token: str = field(
        default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_OWNER_TOKEN", "")
    )
    langgraph_primary_adapter_callback_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET", ""
        )
    )
    langgraph_primary_independent_review_evidence_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET", ""
        )
    )
    langgraph_primary_ci_evidence_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET", ""
        )
    )
    langgraph_primary_security_evidence_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET", ""
        )
    )
    langgraph_primary_completion_evidence_actor_allowlist_json: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON", "{}"
        ).strip()
    )
    langgraph_primary_effect_reconciliation_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET", ""
        )
    )
    langgraph_primary_host_bridge_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT", ""
        ).strip()
    )
    langgraph_primary_host_bridge_secret: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET", ""
        )
    )
    langgraph_primary_allow_insecure_local_postgres: bool = field(
        default_factory=lambda: _bool(
            "LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES", False
        )
    )
    # Director-owned mutation target for the typed GitHub external effect. Zero
    # means unconfigured, and every GitHub effect then fails closed: the pull
    # request a live mark-ready may touch is never taken from provider output.
    langgraph_primary_external_effect_pull_number: int = field(
        default_factory=lambda: _int(
            "LANGGRAPH_PRIMARY_EXTERNAL_EFFECT_PULL_NUMBER", 0
        )
    )
    langgraph_primary_external_effect_base_sha: str = field(
        default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_EXTERNAL_EFFECT_BASE_SHA", "").strip()
    )
    langgraph_primary_external_effect_head_sha: str = field(
        default_factory=lambda: os.getenv("LANGGRAPH_PRIMARY_EXTERNAL_EFFECT_HEAD_SHA", "").strip()
    )
    langgraph_runtime_checkpoint_path: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_RUNTIME_CHECKPOINT_PATH",
            _default_langgraph_runtime_checkpoint_path(),
        ).strip()
    )
    langgraph_runtime_ledger_path: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_RUNTIME_LEDGER_PATH", _default_langgraph_runtime_ledger_path()
        ).strip()
    )
    langgraph_runtime_checkpoint_namespace: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_RUNTIME_CHECKPOINT_NAMESPACE",
            _default_langgraph_runtime_namespace(),
        ).strip()
    )
    langgraph_runtime_writer_owner_id: str = field(
        default_factory=lambda: os.getenv(
            "LANGGRAPH_RUNTIME_WRITER_OWNER_ID", "langgraph-packet-runtime"
        ).strip()
    )
    completion_gates_enabled: bool = field(
        default_factory=lambda: _bool("COMPLETION_GATES_ENABLED", False)
    )
    completion_gate_isolated_proof_enabled: bool = field(
        default_factory=lambda: _bool(
            "COMPLETION_GATE_ISOLATED_PROOF_ENABLED", False
        )
    )
    completion_gate_store_path: str = field(
        default_factory=lambda: os.getenv(
            "COMPLETION_GATE_STORE_PATH", _default_completion_gate_store_path()
        ).strip()
    )
    completion_gate_result_max_age_seconds: int = field(
        default_factory=lambda: _int("COMPLETION_GATE_RESULT_MAX_AGE_SECONDS", 86_400)
    )
    langgraph_runtime_writer_secret: str = field(
        default_factory=lambda: os.getenv("LANGGRAPH_RUNTIME_WRITER_SECRET", "")
    )
    repository_snapshot_root: str = field(
        default_factory=lambda: os.getenv("REPOSITORY_SNAPSHOT_ROOT", "").strip()
    )
    completion_gate_execution_tiers: tuple[str, ...] = field(
        default_factory=lambda: _csv("COMPLETION_GATE_EXECUTION_TIERS", "container")
    )
    completion_gate_container_image: str = field(
        default_factory=lambda: os.getenv("COMPLETION_GATE_CONTAINER_IMAGE", "").strip()
    )
    # [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:06 UTC | BEGIN]]
    completion_gate_workers_enabled: bool = field(
        default_factory=lambda: _bool("COMPLETION_GATE_WORKERS_ENABLED", False)
    )
    completion_gate_worker_store_path: str = field(
        default_factory=lambda: os.getenv(
            "COMPLETION_GATE_WORKER_STORE_PATH",
            _default_completion_worker_store_path(),
        ).strip()
    )
    completion_gate_host_broker_path: str = field(
        default_factory=lambda: os.getenv(
            "COMPLETION_GATE_HOST_BROKER_PATH", ""
        ).strip()
    )
    completion_gate_container_runtime_path: str = field(
        default_factory=lambda: os.getenv(
            "COMPLETION_GATE_CONTAINER_RUNTIME_PATH", ""
        ).strip()
    )
    # [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:06 UTC | END]]
    langgraph_parallel_external_dispatch_enabled: bool = field(
        default_factory=lambda: _bool(
            "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED", False
        )
    )
    langgraph_parallel_kill_switch: bool = field(
        default_factory=lambda: _bool("LANGGRAPH_PARALLEL_KILL_SWITCH", True)
    )
    langgraph_parallel_initial_max_concurrency: int = field(
        default_factory=lambda: _int(
            "LANGGRAPH_PARALLEL_INITIAL_MAX_CONCURRENCY", 2
        )
    )
    langgraph_parallel_auto_scale_max_concurrency: int = field(
        default_factory=lambda: _int(
            "LANGGRAPH_PARALLEL_AUTO_SCALE_MAX_CONCURRENCY", 3
        )
    )
    langgraph_parallel_clean_batches_to_scale: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_CLEAN_BATCHES_TO_SCALE", 10)
    )
    langgraph_parallel_recovery_clean_batches: int = field(
        default_factory=lambda: _int(
            "LANGGRAPH_PARALLEL_RECOVERY_CLEAN_BATCHES", 3
        )
    )
    langgraph_parallel_openai_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_OPENAI_CAP", 1)
    )
    langgraph_parallel_claude_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_CLAUDE_CAP", 1)
    )
    langgraph_parallel_cursor_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_CURSOR_CAP", 1)
    )
    langgraph_parallel_gemini_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_GEMINI_CAP", 1)
    )
    langgraph_parallel_grok_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_GROK_CAP", 1)
    )
    langgraph_parallel_perplexity_cap: int = field(
        default_factory=lambda: _int("LANGGRAPH_PARALLEL_PERPLEXITY_CAP", 1)
    )
    # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
    project_allowlist: tuple[str, ...] = _csv("PROJECT_ALLOWLIST", "")
    repository_allowlist: tuple[str, ...] = _csv("REPOSITORY_ALLOWLIST", "")
    allowed_starting_refs: tuple[str, ...] = _csv_exact("ALLOWED_STARTING_REFS", "main")
    entitlement_control_plane_enabled: bool = field(
        default_factory=lambda: _bool("ENTITLEMENT_CONTROL_PLANE_ENABLED", False)
    )
    entitlement_postgres_dsn_file: str = field(
        default_factory=lambda: os.getenv("ENTITLEMENT_POSTGRES_DSN_FILE", "").strip()
    )
    entitlement_postgres_schema: str = field(
        default_factory=lambda: os.getenv(
            "ENTITLEMENT_POSTGRES_SCHEMA", "entitlement_control_plane_v1"
        ).strip()
    )
    entitlement_trusted_verifier_secret_file: str = field(
        default_factory=lambda: os.getenv(
            "ENTITLEMENT_TRUSTED_VERIFIER_SECRET_FILE", ""
        ).strip()
    )
    entitlement_route_verifier_secret_file: str = field(
        default_factory=lambda: os.getenv(
            "ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE", ""
        ).strip()
    )
    entitlement_owner_authority_secret_file: str = field(
        default_factory=lambda: os.getenv(
            "ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE", ""
        ).strip()
    )
    entitlement_allow_insecure_local_postgres: bool = field(
        default_factory=lambda: _bool(
            "ENTITLEMENT_ALLOW_INSECURE_LOCAL_POSTGRES", False
        )
    )

    oracle_automerge_enabled: bool = _bool("ORACLE_AUTOMERGE_ENABLED", False)
    oracle_automerge_kill_switch: bool = _bool("ORACLE_AUTOMERGE_KILL_SWITCH", True)
    oracle_automerge_github_token: str = os.getenv("ORACLE_AUTOMERGE_GITHUB_TOKEN", "")
    oracle_automerge_github_app_id: str = os.getenv(
        "ORACLE_AUTOMERGE_GITHUB_APP_ID", ""
    ).strip()
    oracle_automerge_github_installation_id: str = os.getenv(
        "ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID", ""
    ).strip()
    oracle_automerge_github_app_private_key_b64: str = os.getenv(
        "ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64", ""
    ).strip()
    oracle_automerge_review_secret: str = os.getenv("ORACLE_AUTOMERGE_REVIEW_SECRET", "")
    oracle_automerge_project: str = os.getenv(
        "ORACLE_AUTOMERGE_PROJECT", "edge-oracle"
    )
    oracle_automerge_repository: str = os.getenv(
        "ORACLE_AUTOMERGE_REPOSITORY", "edwardjones3976/edge_engine"
    )
    oracle_automerge_base_ref: str = os.getenv("ORACLE_AUTOMERGE_BASE_REF", "main")
    oracle_automerge_branch_prefix: str = os.getenv(
        "ORACLE_AUTOMERGE_BRANCH_PREFIX", "oracle/"
    )
    oracle_automerge_allowed_paths: tuple[str, ...] = _csv_exact(
        "ORACLE_AUTOMERGE_ALLOWED_PATHS", "oracle/**,docs/oracle/**,tests/oracle/**"
    )
    oracle_automerge_required_checks: tuple[str, ...] = _csv_exact(
        "ORACLE_AUTOMERGE_REQUIRED_CHECKS", ""
    )
    oracle_automerge_required_check_app: str = os.getenv(
        "ORACLE_AUTOMERGE_REQUIRED_CHECK_APP", "github-actions"
    ).strip()
    oracle_automerge_claude_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_CLAUDE_ACTORS", ""
    )
    oracle_automerge_cursor_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_CURSOR_ACTORS", ""
    )
    oracle_automerge_grok_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_GROK_ACTORS", ""
    )
    oracle_automerge_gemini_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_GEMINI_ACTORS", ""
    )
    oracle_automerge_openai_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_OPENAI_ACTORS", ""
    )

    usage_check_enabled: bool = _bool("USAGE_CHECK_ENABLED", False)
    usage_check_interval_minutes: int = _int("USAGE_CHECK_INTERVAL_MINUTES", 360)
    usage_check_initial_delay_seconds: int = _int("USAGE_CHECK_INITIAL_DELAY_SECONDS", 60)
    usage_check_provider_delay_seconds: int = _int("USAGE_CHECK_PROVIDER_DELAY_SECONDS", 5)
    usage_check_timeout_seconds: int = _int("USAGE_CHECK_TIMEOUT_SECONDS", 300)
    usage_check_max_output_tokens: int = _int("USAGE_CHECK_MAX_OUTPUT_TOKENS", 96)
    usage_check_providers: tuple[str, ...] = _csv(
        "USAGE_CHECK_PROVIDERS", "openai,claude,cursor,grok"
    )

    primary_team: tuple[str, ...] = _csv(
        "PRIMARY_TEAM", "openai,claude,cursor,grok"
    )
    primary_workhorse: str = os.getenv(
        "PRIMARY_WORKHORSE", "cheapest_capable"
    ).strip().lower()
    # Operator-authored ordinal ranks are intentionally required instead of a
    # built-in provider order. Lower positive integers are cheaper, and ranks
    # must be unique so the runtime never invents a tie-breaking cost order.
    trusted_provider_cost_ranks_json: str = os.getenv(
        "TRUSTED_PROVIDER_COST_RANKS", ""
    ).strip()
    claude_account_context: str = os.getenv("CLAUDE_ACCOUNT_CONTEXT", "Claude Max 5x")
    openai_account_context: str = os.getenv(
        "OPENAI_ACCOUNT_CONTEXT", "OpenAI Pro 20x; OpenAI API usage is billed separately"
    )
    other_provider_account_context: str = os.getenv(
        "OTHER_PROVIDER_ACCOUNT_CONTEXT",
        "Lower-capacity paid services per owner; API billing and quotas must be verified separately",
    )

    slack_app_token: str = os.getenv("SLACK_APP_TOKEN", "")
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_channel_id: str = os.getenv("SLACK_DIRECTOR_CHANNEL_ID", "")
    slack_owner_user_id: str = os.getenv("SLACK_OWNER_USER_ID", "")
    slack_gateway_secret: str = os.getenv("SLACK_GATEWAY_SECRET", "")
    n8n_webhook_url: str = os.getenv("N8N_DIRECTOR_WEBHOOK_URL", "")
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
    chatgpt_bridge_enabled: bool = _bool("CHATGPT_BRIDGE_ENABLED", False)
    chatgpt_bridge_slack_user_id: str = os.getenv(
        "CHATGPT_BRIDGE_SLACK_USER_ID", "U0B9KFJPST1"
    ).strip()
    chatgpt_bridge_channel_ids: tuple[str, ...] = _csv_exact(
        "CHATGPT_BRIDGE_CHANNEL_IDS", ""
    ) or _csv_exact("SLACK_DIRECTOR_CHANNEL_ID", "")
    chatgpt_bridge_model: str = os.getenv(
        "OPENAI_CHATGPT_BRIDGE_MODEL", os.getenv("OPENAI_DIRECTOR_MODEL", "gpt-5.6")
    ).strip()
    chatgpt_bridge_max_prompt_chars: int = _int(
        "CHATGPT_BRIDGE_MAX_PROMPT_CHARS", 4000
    )
    chatgpt_bridge_max_output_tokens: int = _int(
        "CHATGPT_BRIDGE_MAX_OUTPUT_TOKENS", 600
    )
    chatgpt_bridge_requests_per_minute: int = _int(
        "CHATGPT_BRIDGE_REQUESTS_PER_MINUTE", 4
    )
    chatgpt_bridge_estimated_cost_usd: float = _float(
        "CHATGPT_BRIDGE_ESTIMATED_COST_USD", 0.10
    )
    chatgpt_bridge_daily_budget_usd: float = _float(
        "CHATGPT_BRIDGE_DAILY_BUDGET_USD", 2.00
    )
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    # Leadership is current; runtime selection still requires a separately
    # enrolled authenticated Claude transport. This switch does not enroll one.
    claude_director_enabled: bool = field(
        default_factory=lambda: _bool("CLAUDE_DIRECTOR_ENABLED", False)
    )
    langgraph_primary_openai_transport: str = os.getenv("LANGGRAPH_PRIMARY_OPENAI_TRANSPORT", "responses-api")
    openai_model: str = os.getenv("OPENAI_DIRECTOR_MODEL", "gpt-5.6")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "medium")
    # Explicit accounting estimates for Director calls. Defaults preserve the
    # existing deployment budget while allowing operators to reserve a
    # conservative amount for every planning/review call.
    openai_plan_estimated_cost_usd: float = _float(
        "OPENAI_PLAN_ESTIMATED_COST_USD", 0.0
    )
    # Every accepted or revised assignment costs one Director review call, so a
    # zero reservation under-reserves the plan envelope by exactly the number of
    # reviews the plan will make. The floor is enforced in `policy.py` as well, so
    # a misconfigured deployment cannot reserve nothing.
    openai_review_estimated_cost_usd: float = _positive_float(
        "OPENAI_REVIEW_ESTIMATED_COST_USD", 0.01
    )
    openai_debate_turn_estimated_cost_usd: float = _positive_float(
        "OPENAI_DEBATE_TURN_ESTIMATED_COST_USD", 0.05
    )

    cursor_api_key: str = os.getenv("CURSOR_API_KEY", "")
    cursor_api_base: str = os.getenv("CURSOR_API_BASE", "https://api.cursor.com")
    cursor_dispatch_max_attempts: int = _int("CURSOR_DISPATCH_MAX_ATTEMPTS", 4)
    cursor_dispatch_max_retry_delay_seconds: int = _int(
        "CURSOR_DISPATCH_MAX_RETRY_DELAY_SECONDS", 60
    )

    claude_routine_url: str = os.getenv("CLAUDE_ROUTINE_URL", "")
    claude_routine_token: str = os.getenv("CLAUDE_ROUTINE_TOKEN", "")
    claude_callback_secret: str = os.getenv("CLAUDE_CALLBACK_SECRET", "")
    claude_routine_beta: str = os.getenv(
        "CLAUDE_ROUTINE_BETA", "experimental-cc-routine-2026-04-01"
    )

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_api_base: str = os.getenv(
        "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
    )
    langgraph_primary_antigravity_team_enabled: bool = os.getenv("LANGGRAPH_PRIMARY_ANTIGRAVITY_TEAM_ENABLED", "false").lower() == "true"
    gemini_agent: str = os.getenv("GEMINI_AGENT", "antigravity-preview-05-2026")
    gemini_api_revision: str = os.getenv("GEMINI_API_REVISION", "2026-05-20")
    gemini_usage_model: str = os.getenv(
        "GEMINI_USAGE_MODEL", "gemini-3.5-flash-lite"
    )
    gemini_usage_max_attempts: int = _int("GEMINI_USAGE_MAX_ATTEMPTS", 3)
    gemini_usage_max_retry_delay_seconds: int = _int(
        "GEMINI_USAGE_MAX_RETRY_DELAY_SECONDS", 8
    )
    gemini_max_total_tokens: int = _int("GEMINI_MAX_TOTAL_TOKENS", 20_000)
    gemini_daily_budget_usd: float = _float("GEMINI_DAILY_BUDGET_USD", 2.0)
    gemini_daily_request_limit: int = _int("GEMINI_DAILY_REQUEST_LIMIT", 50)
    gemini_mode: str = os.getenv(
        "GEMINI_MODE", "antigravity_subordinate"
    ).strip().lower()
    gemini_allow_review_verdict: bool = _bool("GEMINI_ALLOW_REVIEW_VERDICT", False)
    gemini_allow_primary_authority: bool = _bool(
        "GEMINI_ALLOW_PRIMARY_AUTHORITY", False
    )

    grok_enabled: bool = _bool("GROK_ENABLED", False)
    grok_api_key: str = os.getenv("XAI_API_KEY", "")
    grok_api_base: str = os.getenv("GROK_API_BASE", "https://api.x.ai/v1").rstrip("/")
    grok_model: str = os.getenv("GROK_MODEL", "grok-4.6").strip()
    grok_mode: str = os.getenv("GROK_MODE", "cursor_family_primary").strip().lower()
    grok_timeout_seconds: int = _int("GROK_TIMEOUT_SECONDS", 180)
    grok_max_output_tokens: int = _int("GROK_MAX_OUTPUT_TOKENS", 4_096)
    grok_max_context_chars: int = _int("GROK_MAX_CONTEXT_CHARS", 262_144)
    grok_daily_budget_usd: float = _float("GROK_DAILY_BUDGET_USD", 2.0)
    grok_daily_request_limit: int = _int("GROK_DAILY_REQUEST_LIMIT", 50)
    grok_request_cost_floor_usd: float = _positive_float(
        "GROK_REQUEST_COST_FLOOR_USD", 0.01
    )
    grok_allow_tools: bool = _bool("GROK_ALLOW_TOOLS", False)
    grok_allow_repository_write: bool = _bool("GROK_ALLOW_REPOSITORY_WRITE", False)
    grok_allow_review_verdict: bool = _bool("GROK_ALLOW_REVIEW_VERDICT", False)
    grok_cli_enabled: bool = _bool("GROK_CLI_ENABLED", False)
    grok_cli_executable: str = os.getenv("GROK_CLI_EXECUTABLE", "").strip()
    grok_cli_executable_sha256: str = os.getenv(
        "GROK_CLI_EXECUTABLE_SHA256", ""
    ).strip().lower()
    grok_cli_repository_roots: tuple[tuple[str, str], ...] = field(
        default_factory=lambda: _repository_path_map("GROK_CLI_REPOSITORY_MAP_JSON")
    )
    grok_cli_timeout_seconds: int = _int("GROK_CLI_TIMEOUT_SECONDS", 3_600)
    grok_cli_max_turns: int = _int("GROK_CLI_MAX_TURNS", 20)

    perplexity_enabled: bool = _bool("PERPLEXITY_ENABLED", False)
    perplexity_api_key: str = os.getenv("PERPLEXITY_API_KEY", "")
    perplexity_api_base: str = os.getenv("PERPLEXITY_API_BASE", "https://api.perplexity.ai")
    perplexity_preset: str = os.getenv("PERPLEXITY_PRESET", "fast-search")
    perplexity_mode: str = os.getenv("PERPLEXITY_MODE", "research_only").strip().lower()
    perplexity_max_results: int = _int("PERPLEXITY_MAX_RESULTS", 10)
    perplexity_search_context_size: str = os.getenv(
        "PERPLEXITY_SEARCH_CONTEXT_SIZE", "high"
    ).strip().lower()
    perplexity_timeout_seconds: int = _int("PERPLEXITY_TIMEOUT_SECONDS", 180)
    perplexity_daily_budget_usd: float = _float("PERPLEXITY_DAILY_BUDGET_USD", 2.0)
    perplexity_daily_request_limit: int = _int(
        "PERPLEXITY_DAILY_REQUEST_LIMIT", 100
    )
    microsoft_365_copilot_delegated_token_file: str = os.getenv(
        "MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE", ""
    ).strip()
    microsoft_365_copilot_tenant_id: str = os.getenv(
        "MICROSOFT_365_COPILOT_TENANT_ID", ""
    ).strip().lower()
    microsoft_365_copilot_expected_subject: str = os.getenv(
        "MICROSOFT_365_COPILOT_EXPECTED_SUBJECT", ""
    ).strip()
    microsoft_365_copilot_expected_object_id: str = os.getenv(
        "MICROSOFT_365_COPILOT_EXPECTED_OBJECT_ID", ""
    ).strip().lower()

    def completion_evidence_actor_allowlist(
        self,
    ) -> dict[str, dict[str, frozenset[str]]]:
        """Return distinct trusted issuers and principals for each authority."""

        try:
            payload = json.loads(
                self.langgraph_primary_completion_evidence_actor_allowlist_json
            )
        except json.JSONDecodeError as exc:
            raise ValueError("completion evidence actor allowlist is invalid JSON") from exc
        required = {"independent_review", "ci", "security"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError(
                "completion evidence actor allowlist must contain exactly "
                "independent_review, ci, and security"
            )
        result: dict[str, dict[str, frozenset[str]]] = {}
        for kind, policy in payload.items():
            if not isinstance(policy, dict) or set(policy) != {"issuers", "principals"}:
                raise ValueError(f"completion evidence {kind} policy is invalid")
            normalized: dict[str, frozenset[str]] = {}
            for role in ("issuers", "principals"):
                actors = policy[role]
                if (
                    not isinstance(actors, list)
                    or not actors
                    or not all(
                        isinstance(actor, str)
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}", actor)
                        for actor in actors
                    )
                    or len(set(actors)) != len(actors)
                ):
                    raise ValueError(
                        f"completion evidence {kind} {role} are invalid"
                    )
                normalized[role] = frozenset(actors)
            result[kind] = normalized
        for role in ("issuers", "principals"):
            values = [result[kind][role] for kind in sorted(required)]
            if any(
                left & right
                for index, left in enumerate(values)
                for right in values[index + 1 :]
            ):
                raise ValueError(
                    f"completion evidence {role} must be disjoint across authorities"
                )
        return result
    perplexity_allow_repository_write: bool = _bool(
        "PERPLEXITY_ALLOW_REPOSITORY_WRITE", False
    )
    perplexity_allow_review_verdict: bool = _bool(
        "PERPLEXITY_ALLOW_REVIEW_VERDICT", False
    )

    def gemini_supplemental_readiness(self) -> dict[str, bool]:
        return {
            "credential_configured": bool(self.gemini_api_key),
            "agent_configured": bool(self.gemini_agent),
            "usage_model_configured": bool(self.gemini_usage_model),
            "usage_retry_attempts_valid": 1 <= self.gemini_usage_max_attempts <= 5,
            "usage_retry_delay_valid": 0
            <= self.gemini_usage_max_retry_delay_seconds
            <= 60,
            "token_budget_valid": 1 <= self.gemini_max_total_tokens <= 1_000_000,
            "daily_budget_valid": 0 < self.gemini_daily_budget_usd <= 1_000,
            "daily_request_limit_valid": 1
            <= self.gemini_daily_request_limit
            <= 100_000,
            "subordinate_mode": self.gemini_mode == "antigravity_subordinate",
            "review_verdict_disabled": not self.gemini_allow_review_verdict,
            "primary_authority_disabled": not self.gemini_allow_primary_authority,
        }

    def perplexity_research_readiness(self) -> dict[str, bool]:
        return {
            "enabled": self.perplexity_enabled,
            "credential_configured": bool(self.perplexity_api_key),
            "research_only_mode": self.perplexity_mode == "research_only",
            "result_limit_valid": 1 <= self.perplexity_max_results <= 20,
            "search_context_size_valid": self.perplexity_search_context_size
            in {"low", "medium", "high"},
            "timeout_valid": 1 <= self.perplexity_timeout_seconds <= 1_800,
            "budget_valid": 0 < self.perplexity_daily_budget_usd <= 1_000,
            "daily_request_limit_valid": 1
            <= self.perplexity_daily_request_limit
            <= 100_000,
            "repository_write_disabled": not self.perplexity_allow_repository_write,
            "review_verdict_disabled": not self.perplexity_allow_review_verdict,
        }

    def grok_primary_readiness(self) -> dict[str, bool]:
        repository_transport_required = (
            self.grok_allow_repository_write or self.grok_allow_review_verdict
        )
        repository_transport_ready = (
            self.grok_cli_enabled
            and bool(self.grok_cli_executable)
            and Path(self.grok_cli_executable).expanduser().is_absolute()
            and Path(self.grok_cli_executable).expanduser().is_file()
            and bool(re.fullmatch(r"[0-9a-f]{64}", self.grok_cli_executable_sha256))
            and bool(self.grok_cli_repository_roots)
            and 30 <= self.grok_cli_timeout_seconds <= 7_200
            and 1 <= self.grok_cli_max_turns <= 100
        )
        return {
            "enabled": self.grok_enabled,
            "credential_configured": bool(self.grok_api_key),
            "official_api_endpoint": self.grok_api_base == "https://api.x.ai/v1",
            "model_configured": bool(self.grok_model)
            and "/" not in self.grok_model
            and not any(character.isspace() for character in self.grok_model),
            "cursor_family_primary_mode": self.grok_mode == "cursor_family_primary",
            "timeout_valid": 1 <= self.grok_timeout_seconds <= 1_800,
            "output_limit_valid": 16 <= self.grok_max_output_tokens <= 32_768,
            "context_limit_valid": 1_000 <= self.grok_max_context_chars <= 262_144,
            "budget_valid": 0 < self.grok_daily_budget_usd <= 1_000,
            "daily_request_limit_valid": 1 <= self.grok_daily_request_limit <= 100_000,
            "request_cost_floor_valid": 0 < self.grok_request_cost_floor_usd
            <= self.grok_daily_budget_usd,
            "tools_disabled": not self.grok_allow_tools,
            "repository_transport_ready": (
                repository_transport_ready or not repository_transport_required
            ),
        }

    def grok_cli_repository_map(self) -> dict[str, str]:
        return dict(self.grok_cli_repository_roots)

    def readiness(self) -> dict[str, bool]:
        slack_gateway_ready = bool(
            len(self.slack_gateway_secret) >= 32
            and (
                not self.shared_secret
                or not hmac.compare_digest(
                    self.slack_gateway_secret, self.shared_secret
                )
            )
        )
        return {
            "slack": bool(
                self.slack_app_token
                and self.slack_bot_token
                and slack_gateway_ready
            ),
            "openai_director": bool(self.openai_api_key),
            "cursor": bool(self.cursor_api_key),
            "claude": bool(self.claude_routine_url and self.claude_routine_token),
            "gemini": all(self.gemini_supplemental_readiness().values()),
            "grok": all(self.grok_primary_readiness().values()),
            "perplexity": all(self.perplexity_research_readiness().values()),
        }

    def usage_check_readiness(self) -> dict[str, bool]:
        readiness = self.readiness()
        return {
            "openai": readiness["openai_director"],
            "cursor": readiness["cursor"],
            "claude": readiness["claude"],
            "gemini": readiness["gemini"],
            "grok": readiness["grok"],
            "perplexity": readiness["perplexity"],
        }

    def workload_policy_readiness(
        self, *, effective_mode: str | None = None
    ) -> dict[str, bool]:
        """Expose only non-sensitive workload-enforcement readiness booleans."""

        if effective_mode is not None and effective_mode not in {"enforced", "shadow"}:
            raise ValueError("effective workload mode must be enforced or shadow")
        active_mode = effective_mode or self.workload_policy_mode
        try:
            cost_ranks = self.trusted_provider_cost_ranks()
        except ValueError:
            cost_ranks = {}
        ready_specialists = {
            provider
            for provider in ("claude", "cursor", "gemini", "grok", "perplexity")
            if self.readiness().get(provider, False)
        }
        return {
            "enabled": self.workload_policy_enabled,
            "enforced": (
                self.workload_policy_enabled
                and active_mode == "enforced"
            ),
            "shadow": (
                self.workload_policy_enabled and active_mode == "shadow"
            ),
            "mode_valid": self.workload_policy_mode in {"enforced", "shadow"},
            "window_valid": self.workload_policy_window_assignments == 20,
            "shadow_sample_valid": self.workload_policy_shadow_assignments == 5,
            "selector_mode_valid": self.primary_workhorse == "cheapest_capable",
            "cost_ranks_valid": bool(cost_ranks),
            "ready_provider_ranks_complete": bool(ready_specialists)
            and ready_specialists.issubset(cost_ranks),
        }

    def trusted_provider_cost_ranks(self) -> dict[str, int]:
        """Parse explicit operator-controlled comparable cost ranks.

        The setting is a JSON object such as ``{"claude": 2, "cursor": 1}``.
        No default order exists: missing, malformed, unknown, duplicate-after-
        normalization, boolean, zero, and negative values all fail closed.
        """

        raw = self.trusted_provider_cost_ranks_json
        if not raw:
            raise ValueError("TRUSTED_PROVIDER_COST_RANKS must be configured")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(
                        "TRUSTED_PROVIDER_COST_RANKS provider names must be unique"
                    )
                value[key] = item
            return value

        try:
            payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "TRUSTED_PROVIDER_COST_RANKS must be a JSON object"
            ) from exc
        if not isinstance(payload, dict) or not payload:
            raise ValueError(
                "TRUSTED_PROVIDER_COST_RANKS must be a non-empty JSON object"
            )
        allowed = {"openai", "claude", "cursor", "gemini", "antigravity", "grok", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot"}
        parsed: dict[str, int] = {}
        for raw_provider, raw_rank in payload.items():
            if not isinstance(raw_provider, str):
                raise ValueError(
                    "TRUSTED_PROVIDER_COST_RANKS provider names must be strings"
                )
            provider = raw_provider.strip().lower()
            if provider == "antigravity":
                provider = "gemini"
            if provider not in allowed:
                raise ValueError(
                    "TRUSTED_PROVIDER_COST_RANKS contains an unsupported provider"
                )
            if provider in parsed:
                raise ValueError(
                    "TRUSTED_PROVIDER_COST_RANKS provider names must be unique"
                )
            if (
                isinstance(raw_rank, bool)
                or not isinstance(raw_rank, int)
                or raw_rank < 1
            ):
                raise ValueError(
                    "TRUSTED_PROVIDER_COST_RANKS values must be positive integers"
                )
            parsed[provider] = raw_rank
        if len(set(parsed.values())) != len(parsed):
            raise ValueError(
                "TRUSTED_PROVIDER_COST_RANKS values must define a unique total order"
            )
        return dict(sorted(parsed.items()))

    def validate_workload_policy_configuration(self) -> None:
        readiness = self.workload_policy_readiness()
        if not readiness["mode_valid"]:
            raise ValueError(
                "WORKLOAD_POLICY_MODE must be either 'enforced' or 'shadow'"
            )
        if not readiness["window_valid"]:
            raise ValueError(
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS must remain exactly 20"
            )
        if not readiness["shadow_sample_valid"]:
            raise ValueError(
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS must remain exactly 5"
            )
        # Dry-run/local validation remains possible without paid-provider
        # credentials. A paid runtime, however, must have an operator-supplied
        # selector and comparable ranks before even the OpenAI planning call.
        if self.allow_paid_runs and self.workload_policy_enabled:
            if not readiness["selector_mode_valid"]:
                raise ValueError(
                    "PRIMARY_WORKHORSE must be 'cheapest_capable' for paid workload routing"
                )
            if not readiness["cost_ranks_valid"]:
                # Re-run the parser to preserve the precise structural error.
                self.trusted_provider_cost_ranks()
            if not readiness["ready_provider_ranks_complete"]:
                raise ValueError(
                    "TRUSTED_PROVIDER_COST_RANKS must cover every configured ready specialist"
                )

    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
    def chatgpt_bridge_readiness(self) -> dict[str, bool]:
        return {
            "enabled": self.chatgpt_bridge_enabled,
            "paid_execution_enabled": self.allow_paid_runs,
            "slack_transport_configured": self.readiness()["slack"],
            "internal_auth_configured": bool(self.shared_secret),
            "openai_configured": bool(self.openai_api_key),
            "model_configured": bool(self.chatgpt_bridge_model),
            "owner_configured": re.fullmatch(
                r"U[A-Z0-9]+", self.slack_owner_user_id
            )
            is not None,
            "channels_allowlisted": bool(self.chatgpt_bridge_channel_ids)
            and all(
                re.fullmatch(r"[CG][A-Z0-9]+", item) is not None
                for item in self.chatgpt_bridge_channel_ids
            ),
            "alias_user_configured": re.fullmatch(
                r"U[A-Z0-9]+", self.chatgpt_bridge_slack_user_id
            )
            is not None,
            "prompt_limit_valid": 1 <= self.chatgpt_bridge_max_prompt_chars <= 20_000,
            "output_limit_valid": 1 <= self.chatgpt_bridge_max_output_tokens <= 4_096,
            "rate_limit_valid": 1 <= self.chatgpt_bridge_requests_per_minute <= 60,
            "cost_estimate_valid": 0 < self.chatgpt_bridge_estimated_cost_usd <= 10
            and self.chatgpt_bridge_estimated_cost_usd
            <= self.chatgpt_bridge_daily_budget_usd,
            "daily_budget_valid": 0 < self.chatgpt_bridge_daily_budget_usd <= 100,
        }
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]

    def execution_profile_readiness(self) -> dict[str, bool]:
        return {
            "projects_configured": bool(self.project_allowlist),
            "repositories_configured": bool(self.repository_allowlist),
            "starting_refs_configured": bool(self.allowed_starting_refs),
            "claude_callback_configured": bool(self.claude_callback_secret),
        }

    # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
    def langgraph_shadow_readiness(self) -> dict[str, bool]:
        """Report configuration only; the legacy engine remains the sole writer."""

        checkpoint_path = self.langgraph_checkpoint_path.strip()
        checkpoint_configured = bool(checkpoint_path)
        checkpoint_isolated = checkpoint_configured and _paths_are_isolated(
            checkpoint_path, self.sqlite_path
        )
        claim_path = (
            langgraph_resume_claim_path(checkpoint_path)
            if checkpoint_configured
            else ""
        )
        claim_isolated = checkpoint_configured and all(
            (
                _paths_are_isolated(claim_path, self.sqlite_path),
                _paths_are_isolated(claim_path, checkpoint_path),
            )
        )
        namespace_configured = bool(self.langgraph_checkpoint_namespace.strip())
        feature_enabled = (
            self.langgraph_shadow_enabled or self.langgraph_approval_resume_enabled
        )
        return {
            "shadow_enabled": self.langgraph_shadow_enabled,
            "approval_resume_enabled": self.langgraph_approval_resume_enabled,
            "checkpoint_path_configured": checkpoint_configured,
            "checkpoint_path_isolated": checkpoint_isolated,
            "checkpoint_namespace_configured": namespace_configured,
            "configuration_valid": not feature_enabled
            or (
                checkpoint_configured
                and checkpoint_isolated
                and claim_isolated
                and namespace_configured
            ),
            # The shadow lane itself never writes. These two now report the
            # separately gated packet coordinator so `/health` cannot claim the
            # legacy engine is the sole writer once that coordinator is active.
            "runtime_active": self.langgraph_runtime_readiness()["runtime_active"],
            "legacy_sole_writer": not self.langgraph_runtime_readiness()[
                "live_provider_dispatch"
            ],
        }

    def validate_langgraph_configuration(self) -> None:
        """Fail startup closed when an enabled proof lane is not isolated."""

        readiness = self.langgraph_shadow_readiness()
        if readiness["configuration_valid"]:
            return

        problems: list[str] = []
        if not readiness["checkpoint_path_configured"]:
            problems.append("LANGGRAPH_CHECKPOINT_PATH must be configured")
        elif not readiness["checkpoint_path_isolated"]:
            problems.append("LANGGRAPH_CHECKPOINT_PATH must differ from SQLITE_PATH")
        elif not _paths_are_isolated(
            langgraph_resume_claim_path(self.langgraph_checkpoint_path),
            self.sqlite_path,
        ) or not _paths_are_isolated(
            langgraph_resume_claim_path(self.langgraph_checkpoint_path),
            self.langgraph_checkpoint_path,
        ):
            problems.append(
                "LangGraph resume claim database must differ from SQLITE_PATH and checkpoint database"
            )
        if not readiness["checkpoint_namespace_configured"]:
            problems.append("LANGGRAPH_CHECKPOINT_NAMESPACE must be configured")
        raise ValueError("Invalid enabled LangGraph configuration: " + "; ".join(problems))
    # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]

    # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
    def _runtime_store_paths(self) -> tuple[str, ...]:
        """Every durable store the activated runtime must not share."""

        return (
            self.sqlite_path,
            self.langgraph_checkpoint_path,
            langgraph_resume_claim_path(self.langgraph_checkpoint_path),
            self.langgraph_runtime_checkpoint_path,
            self.langgraph_runtime_ledger_path,
        )

    def langgraph_runtime_readiness(self) -> dict[str, bool]:
        """Report whether the packet coordinator may own one dispatch lane.

        `runtime_active` is the only claim that turns the coordinator on, and it
        requires a valid isolated configuration. `live_provider_dispatch` is a
        strictly narrower claim: it additionally requires its own flag, paid
        execution, and a configured execution profile, so an operator cannot reach
        a provider by enabling the coordinator alone.
        """

        checkpoint_configured = bool(self.langgraph_runtime_checkpoint_path.strip())
        ledger_configured = bool(self.langgraph_runtime_ledger_path.strip())
        namespace_configured = bool(
            self.langgraph_runtime_checkpoint_namespace.strip()
        )
        writer_owner_configured = bool(self.langgraph_runtime_writer_owner_id.strip())
        writer_secret_configured = len(self.langgraph_runtime_writer_secret) >= 32
        snapshot_root_configured = bool(self.repository_snapshot_root.strip())
        namespaces_isolated = namespace_configured and (
            self.langgraph_runtime_checkpoint_namespace.strip()
            != self.langgraph_checkpoint_namespace.strip()
        )
        stores_isolated = (
            checkpoint_configured
            and ledger_configured
            and _pairwise_isolated(self._runtime_store_paths())
        )
        configuration_valid = (
            checkpoint_configured
            and ledger_configured
            and namespace_configured
            and namespaces_isolated
            and writer_owner_configured
            and stores_isolated
        )
        runtime_active = self.langgraph_runtime_enabled and configuration_valid
        execution_profile_ready = all(self.execution_profile_readiness().values())
        return {
            "runtime_enabled": self.langgraph_runtime_enabled,
            "live_dispatch_enabled": self.langgraph_runtime_live_dispatch_enabled,
            "task_writer_enabled": self.langgraph_runtime_task_writer_enabled,
            "paid_execution_enabled": self.allow_paid_runs,
            "checkpoint_path_configured": checkpoint_configured,
            "ledger_path_configured": ledger_configured,
            "checkpoint_namespace_configured": namespace_configured,
            "checkpoint_namespace_isolated": namespaces_isolated,
            "writer_owner_configured": writer_owner_configured,
            "writer_secret_configured": writer_secret_configured,
            "snapshot_root_configured": snapshot_root_configured,
            "stores_isolated": stores_isolated,
            "single_writer_token_required": True,
            "packet_validation_required": True,
            "configuration_valid": configuration_valid,
            "runtime_active": runtime_active,
            "live_provider_dispatch": (
                runtime_active
                and self.langgraph_runtime_live_dispatch_enabled
                and self.langgraph_runtime_task_writer_enabled
                and self.allow_paid_runs
                and execution_profile_ready
                and writer_secret_configured
                and snapshot_root_configured
            ),
            "task_writer_active": (
                runtime_active
                and self.langgraph_runtime_live_dispatch_enabled
                and self.langgraph_runtime_task_writer_enabled
                and self.allow_paid_runs
                and execution_profile_ready
                and writer_secret_configured
                and snapshot_root_configured
            ),
        }

    def validate_langgraph_runtime_configuration(self) -> None:
        """Fail startup closed when an enabled coordinator is not isolated."""

        if not self.langgraph_runtime_enabled:
            return
        readiness = self.langgraph_runtime_readiness()
        problems: list[str] = []
        if not readiness["checkpoint_path_configured"]:
            problems.append("LANGGRAPH_RUNTIME_CHECKPOINT_PATH must be configured")
        if not readiness["ledger_path_configured"]:
            problems.append("LANGGRAPH_RUNTIME_LEDGER_PATH must be configured")
        if not readiness["checkpoint_namespace_configured"]:
            problems.append("LANGGRAPH_RUNTIME_CHECKPOINT_NAMESPACE must be configured")
        elif not readiness["checkpoint_namespace_isolated"]:
            problems.append(
                "LANGGRAPH_RUNTIME_CHECKPOINT_NAMESPACE must differ from the shadow namespace"
            )
        if not readiness["writer_owner_configured"]:
            problems.append("LANGGRAPH_RUNTIME_WRITER_OWNER_ID must be configured")
        if self.langgraph_runtime_live_dispatch_enabled and not readiness[
            "writer_secret_configured"
        ]:
            problems.append(
                "LANGGRAPH_RUNTIME_WRITER_SECRET must contain at least 32 characters"
            )
        if self.langgraph_runtime_live_dispatch_enabled and not readiness[
            "snapshot_root_configured"
        ]:
            problems.append("REPOSITORY_SNAPSHOT_ROOT must be configured")
        if (
            self.langgraph_runtime_live_dispatch_enabled
            and not self.langgraph_runtime_task_writer_enabled
        ):
            problems.append(
                "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED must be true whenever live "
                "dispatch is enabled"
            )
        if not readiness["stores_isolated"]:
            problems.append(
                "runtime checkpoint, runtime ledger, shadow checkpoint, shadow resume "
                "claim, and SQLITE_PATH databases must all be distinct files"
            )
        if problems:
            raise ValueError(
                "Invalid enabled LangGraph runtime configuration: "
                + "; ".join(problems)
            )

    def langgraph_primary_configuration_readiness(self) -> dict[str, bool]:
        """Report configuration prerequisites without claiming live authority."""

        dsn_configured = bool(self.langgraph_primary_postgres_dsn.strip())
        schema_configured = bool(
            re.fullmatch(
                r"[a-z][a-z0-9_]{0,62}",
                self.langgraph_primary_postgres_schema,
            )
        )
        namespace_configured = bool(
            self.langgraph_primary_checkpoint_namespace.strip()
        )
        namespace_isolated = namespace_configured and all(
            self.langgraph_primary_checkpoint_namespace.strip() != item
            for item in (
                self.langgraph_checkpoint_namespace.strip(),
                self.langgraph_runtime_checkpoint_namespace.strip(),
            )
        )
        owner_token_configured = len(self.langgraph_primary_owner_token) >= 32
        adapter_callback_secret_configured = (
            len(self.langgraph_primary_adapter_callback_secret) >= 32
        )
        completion_authority_secrets = (
            self.langgraph_primary_independent_review_evidence_secret,
            self.langgraph_primary_ci_evidence_secret,
            self.langgraph_primary_security_evidence_secret,
        )
        completion_evidence_secret_configured = (
            all(len(value) >= 32 for value in completion_authority_secrets)
            and len(set(completion_authority_secrets)) == 3
        )
        try:
            completion_evidence_actor_allowlist_configured = bool(
                self.completion_evidence_actor_allowlist()
            )
        except ValueError:
            completion_evidence_actor_allowlist_configured = False
        effect_reconciliation_secret_configured = (
            len(self.langgraph_primary_effect_reconciliation_secret) >= 32
        )
        authority_root_configured = bool(self.repository_snapshot_root.strip())
        single_repository_configured = len(self.repository_allowlist) == 1
        single_starting_ref_configured = len(self.allowed_starting_refs) == 1
        pinned_commit = self.langgraph_primary_pinned_commit_sha.strip()
        pinned_tree = self.langgraph_primary_pinned_tree_sha.strip()
        authority_pins_valid = (not pinned_commit and not pinned_tree) or bool(
            re.fullmatch(r"[0-9a-fA-F]{40}", pinned_commit)
            and re.fullmatch(r"[0-9a-fA-F]{40}", pinned_tree)
        )
        snapshot_max_files_valid = (
            isinstance(self.langgraph_primary_snapshot_max_files, int)
            and not isinstance(self.langgraph_primary_snapshot_max_files, bool)
            and 1 <= self.langgraph_primary_snapshot_max_files <= 4096
        )
        from .primary_roster import KNOWN_LANES, core_inventory_ready
        configured_pool = set(self.langgraph_primary_required_providers)
        required_providers_valid = (
            len(configured_pool) == len(self.langgraph_primary_required_providers)
            and configured_pool.issubset(KNOWN_LANES)
            and core_inventory_ready(tuple(configured_pool))
        )
        try:
            bound_pool = set(self.primary_provider_activation_bindings())
            activation_bindings_configured = bound_pool.issubset(configured_pool) and core_inventory_ready(tuple(bound_pool))
        except ValueError:
            activation_bindings_configured = False
        configuration_valid = (
            dsn_configured
            and schema_configured
            and namespace_configured
            and namespace_isolated
            and owner_token_configured
            and authority_root_configured
            and single_repository_configured
            and single_starting_ref_configured
            and authority_pins_valid
            and snapshot_max_files_valid
        )
        return {
            "enabled": self.langgraph_primary_enabled,
            "new_intake_requested": self.langgraph_primary_new_intake_enabled,
            "postgres_dsn_configured": dsn_configured,
            "postgres_schema_configured": schema_configured,
            "checkpoint_namespace_configured": namespace_configured,
            "checkpoint_namespace_isolated": namespace_isolated,
            "owner_token_configured": owner_token_configured,
            "adapter_callback_secret_configured": (
                adapter_callback_secret_configured
            ),
            "completion_evidence_secret_configured": (
                completion_evidence_secret_configured
            ),
            "completion_evidence_actor_allowlist_configured": (
                completion_evidence_actor_allowlist_configured
            ),
            "effect_reconciliation_secret_configured": (
                effect_reconciliation_secret_configured
            ),
            "authority_root_configured": authority_root_configured,
            "single_repository_configured": single_repository_configured,
            "single_starting_ref_configured": single_starting_ref_configured,
            "authority_pins_valid": authority_pins_valid,
            "snapshot_max_files_valid": snapshot_max_files_valid,
            "required_providers_valid": required_providers_valid,
            "provider_activation_bindings_configured": (
                activation_bindings_configured
            ),
            "offline_simulation_requested": (
                self.langgraph_primary_offline_simulation_enabled
            ),
            "live_provider_dispatch_requested": (
                self.langgraph_primary_live_provider_dispatch_enabled
            ),
            "configuration_valid": configuration_valid,
            "startup_allowed": (
                not self.langgraph_primary_enabled or configuration_valid
            ),
            # Ownership is observed from Postgres by the activation component;
            # configuration alone can never claim it.
            "primary_runtime_active": False,
            "legacy_new_intake_disabled": False,
        }

    def validate_langgraph_primary_configuration(self) -> None:
        """Reject incomplete primary activation before opening PostgreSQL."""

        readiness = self.langgraph_primary_configuration_readiness()
        if not readiness["snapshot_max_files_valid"]:
            raise ValueError(
                "LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES must be between 1 and 4096"
            )
        if not self.langgraph_primary_enabled:
            if (
                self.langgraph_primary_new_intake_enabled
                or self.langgraph_primary_offline_simulation_enabled
                or self.langgraph_primary_live_provider_dispatch_enabled
            ):
                raise ValueError(
                    "A LangGraph primary execution switch requires "
                    "LANGGRAPH_PRIMARY_ENABLED"
                )
            return
        problems: list[str] = []
        if (
            self.langgraph_primary_offline_simulation_enabled
            and self.langgraph_primary_live_provider_dispatch_enabled
        ):
            problems.append(
                "offline simulation and live provider dispatch are mutually exclusive"
            )
        if not readiness["required_providers_valid"]:
            problems.append(
                "LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS must be non-empty and unique"
            )
        if self.langgraph_primary_live_provider_dispatch_enabled:
            if not self.allow_paid_runs:
                problems.append(
                    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED requires ALLOW_PAID_RUNS"
                )
            if not readiness["provider_activation_bindings_configured"]:
                problems.append(
                    "live provider dispatch requires exact provider activation bindings"
                )
            if readiness["provider_activation_bindings_configured"] and "microsoft-365-copilot" in self.primary_provider_activation_bindings():
                if not self.microsoft_365_copilot_delegated_token_file:
                    problems.append(
                        "Microsoft 365 Copilot requires a protected delegated token file"
                    )
                if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                    self.microsoft_365_copilot_tenant_id,
                ):
                    problems.append("Microsoft 365 Copilot tenant id is invalid")
                if not self.microsoft_365_copilot_expected_subject:
                    problems.append("Microsoft 365 Copilot expected subject is required")
                if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                    self.microsoft_365_copilot_expected_object_id,
                ):
                    problems.append("Microsoft 365 Copilot expected object id is invalid")
            if len(self.langgraph_primary_adapter_callback_secret) < 32:
                problems.append(
                    "live provider dispatch requires a 32-character "
                    "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET"
                )
            if not readiness["completion_evidence_secret_configured"]:
                problems.append(
                    "live provider dispatch requires a 32-character "
                    "three distinct completion authority evidence secrets"
                )
            if not readiness["completion_evidence_actor_allowlist_configured"]:
                problems.append(
                    "live provider dispatch requires an exact completion evidence "
                    "actor allowlist"
                )
            if len(self.langgraph_primary_effect_reconciliation_secret) < 32:
                problems.append(
                    "live provider dispatch requires a 32-character "
                    "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET"
                )
            bridge_endpoint = self.langgraph_primary_host_bridge_endpoint
            bridge_secret = self.langgraph_primary_host_bridge_secret
            if bool(bridge_endpoint) != bool(bridge_secret):
                problems.append(
                    "host provider bridge endpoint and secret must be configured together"
                )
            if bridge_secret and len(bridge_secret) < 32:
                problems.append(
                    "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET must contain at least 32 characters"
                )
            ingress_secrets = tuple(
                value
                for value in (
                    self.shared_secret,
                    self.slack_gateway_secret,
                    self.langgraph_primary_adapter_callback_secret,
                    self.langgraph_primary_independent_review_evidence_secret,
                    self.langgraph_primary_ci_evidence_secret,
                    self.langgraph_primary_security_evidence_secret,
                    self.langgraph_primary_effect_reconciliation_secret,
                    bridge_secret,
                )
                if value
            )
            if len(set(ingress_secrets)) != len(ingress_secrets):
                problems.append("primary ingress secrets must be pairwise distinct")
        if not readiness["postgres_dsn_configured"]:
            problems.append("LANGGRAPH_PRIMARY_POSTGRES_DSN must be configured")
        if not readiness["postgres_schema_configured"]:
            problems.append(
                "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA must be a safe versioned identifier"
            )
        if not readiness["checkpoint_namespace_configured"]:
            problems.append(
                "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE must be configured"
            )
        elif not readiness["checkpoint_namespace_isolated"]:
            problems.append(
                "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE must differ from shadow and runtime namespaces"
            )
        if not readiness["owner_token_configured"]:
            problems.append(
                "LANGGRAPH_PRIMARY_OWNER_TOKEN must contain at least 32 characters"
            )
        if not readiness["authority_root_configured"]:
            problems.append("REPOSITORY_SNAPSHOT_ROOT must be configured")
        if not readiness["single_repository_configured"]:
            problems.append(
                "REPOSITORY_ALLOWLIST must contain exactly one repository"
            )
        if not readiness["single_starting_ref_configured"]:
            problems.append(
                "ALLOWED_STARTING_REFS must contain exactly one starting ref"
            )
        if not readiness["authority_pins_valid"]:
            problems.append(
                "LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA and "
                "LANGGRAPH_PRIMARY_PINNED_TREE_SHA must be supplied together "
                "as 40-character hexadecimal values"
            )
        if problems:
            raise ValueError(
                "Invalid enabled LangGraph primary configuration: "
                + "; ".join(problems)
            )

    def primary_provider_activation_bindings(self):
        """Parse bounded, non-secret provider activation identities."""

        from .provider_activation import ProviderActivationBinding

        try:
            payload = json.loads(
                self.langgraph_primary_provider_activation_bindings_json or "{}"
            )
        except json.JSONDecodeError as exc:
            raise ValueError("provider activation bindings must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("provider activation bindings must be an object")
        expected_fields = {
            "workspace_id",
            "provider_version",
            "app_version",
            "runtime_adapter_version",
            "workspace_version",
            "binding_version",
        }
        bindings = {}
        for provider_id, raw in payload.items():
            if not isinstance(provider_id, str) or not isinstance(raw, dict):
                raise ValueError("provider activation binding is malformed")
            if set(raw) != expected_fields:
                raise ValueError("provider activation binding fields are invalid")
            bindings[provider_id] = ProviderActivationBinding(
                provider_id=provider_id,
                **raw,
            )
        return bindings

    def completion_gate_readiness(self) -> dict[str, bool]:
        """Report Completion Gate evidence readiness without claiming authority.

        `completion_authority` is permanently false here. Gate evidence proves that
        required checks passed at one exact head; it never authorizes acceptance,
        merge, deployment, or project completion on its own.
        """

        store_configured = bool(self.completion_gate_store_path.strip())
        store_isolated = store_configured and _pairwise_isolated(
            (self.completion_gate_store_path, *self._runtime_store_paths())
        )
        freshness_valid = 1 <= self.completion_gate_result_max_age_seconds <= 2_592_000
        tiers = tuple(dict.fromkeys(self.completion_gate_execution_tiers))
        tiers_valid = bool(tiers) and set(tiers).issubset({"host", "container"})
        container_required = "container" in tiers
        container_image_pinned = bool(
            re.fullmatch(
                r"[^\s@]+@sha256:[0-9a-f]{64}", self.completion_gate_container_image
            )
        )
        execution_configuration_valid = (
            tiers_valid and (not container_required or container_image_pinned)
        )
        # Worker execution is a separately switched production boundary. Merely
        # recording gate evidence or enabling the older isolated-proof contract
        # never constructs a host broker or a container runtime.
        worker_store_configured = bool(self.completion_gate_worker_store_path.strip())
        worker_store_isolated = worker_store_configured and _pairwise_isolated(
            (
                self.completion_gate_worker_store_path,
                self.completion_gate_store_path,
                *self._runtime_store_paths(),
            )
        )
        host_required = "host" in tiers
        container_runtime_required = "container" in tiers
        host_broker_configured = bool(
            self.completion_gate_host_broker_path
            and os.path.isabs(self.completion_gate_host_broker_path)
            and os.path.isfile(self.completion_gate_host_broker_path)
        )
        container_runtime_configured = bool(
            self.completion_gate_container_runtime_path
            and os.path.isabs(self.completion_gate_container_runtime_path)
            and os.path.isfile(self.completion_gate_container_runtime_path)
        )
        worker_configuration_valid = (
            not self.completion_gate_workers_enabled
            or (
                self.completion_gates_enabled
                and self.completion_gate_isolated_proof_enabled
                and execution_configuration_valid
                and worker_store_isolated
                and not host_required
                and (
                    not container_runtime_required
                    or container_runtime_configured
                )
            )
        )
        configuration_valid = worker_configuration_valid and (
            not self.completion_gates_enabled
            or (
                store_configured
                and store_isolated
                and freshness_valid
                and (
                    not self.completion_gate_isolated_proof_enabled
                    or execution_configuration_valid
                )
            )
        )
        return {
            "enabled": self.completion_gates_enabled,
            "isolated_proof_enabled": self.completion_gate_isolated_proof_enabled,
            "store_configured": store_configured,
            "store_isolated": store_isolated,
            "result_freshness_valid": freshness_valid,
            "execution_tiers_valid": tiers_valid,
            "container_image_pinned": container_image_pinned,
            "execution_configuration_valid": execution_configuration_valid,
            "workers_enabled": self.completion_gate_workers_enabled,
            "worker_store_configured": worker_store_configured,
            "worker_store_isolated": worker_store_isolated,
            "host_broker_configured": host_broker_configured,
            "container_runtime_configured": container_runtime_configured,
            "worker_configuration_valid": worker_configuration_valid,
            "configuration_valid": configuration_valid,
            "evidence_recording_active": (
                self.completion_gates_enabled and configuration_valid
            ),
            "execution_active": (
                self.completion_gates_enabled
                and self.completion_gate_isolated_proof_enabled
                and configuration_valid
            ),
            "worker_execution_active": (
                self.completion_gate_workers_enabled
                and self.completion_gates_enabled
                and self.completion_gate_isolated_proof_enabled
                and worker_configuration_valid
                and configuration_valid
            ),
            "completion_authority": False,
        }

    def validate_completion_gate_configuration(self) -> None:
        """Fail startup closed when enabled gate evidence is not isolated."""

        readiness = self.completion_gate_readiness()
        if readiness["configuration_valid"]:
            return
        problems: list[str] = []
        if not readiness["store_configured"]:
            problems.append("COMPLETION_GATE_STORE_PATH must be configured")
        elif not readiness["store_isolated"]:
            problems.append(
                "COMPLETION_GATE_STORE_PATH must differ from SQLITE_PATH and every "
                "LangGraph shadow or runtime database"
            )
        if not readiness["result_freshness_valid"]:
            problems.append(
                "COMPLETION_GATE_RESULT_MAX_AGE_SECONDS must be between 1 and 2592000"
            )
        if self.completion_gate_isolated_proof_enabled and not readiness[
            "execution_tiers_valid"
        ]:
            problems.append(
                "COMPLETION_GATE_EXECUTION_TIERS must contain only host and/or container"
            )
        if (
            self.completion_gate_isolated_proof_enabled
            and "container" in self.completion_gate_execution_tiers
            and not readiness["container_image_pinned"]
        ):
            problems.append(
                "COMPLETION_GATE_CONTAINER_IMAGE must use an exact sha256 digest"
            )
        if self.completion_gate_workers_enabled:
            if not self.completion_gates_enabled:
                problems.append(
                    "COMPLETION_GATES_ENABLED must be true before workers are enabled"
                )
            if not self.completion_gate_isolated_proof_enabled:
                problems.append(
                    "COMPLETION_GATE_ISOLATED_PROOF_ENABLED must be true before workers are enabled"
                )
            if not readiness["worker_store_configured"]:
                problems.append("COMPLETION_GATE_WORKER_STORE_PATH must be configured")
            elif not readiness["worker_store_isolated"]:
                problems.append(
                    "COMPLETION_GATE_WORKER_STORE_PATH must differ from the gate, "
                    "task, and every LangGraph database"
                )
            if "host" in self.completion_gate_execution_tiers and not readiness[
                "host_broker_configured"
            ]:
                problems.append(
                    "COMPLETION_GATE_HOST_BROKER_PATH must name an existing absolute file"
                )
            if "host" in self.completion_gate_execution_tiers:
                problems.append(
                    "the audited host tier is not activatable until its OS controls "
                    "can be independently verified; select container only"
                )
            if "container" in self.completion_gate_execution_tiers and not readiness[
                "container_runtime_configured"
            ]:
                problems.append(
                    "COMPLETION_GATE_CONTAINER_RUNTIME_PATH must name an existing absolute file"
                )
        raise ValueError(
            "Invalid enabled Completion Gate configuration: " + "; ".join(problems)
        )

    def parallel_dispatch_readiness(self) -> dict[str, bool | int]:
        """Report bounded parallel-dispatch readiness without dispatching."""

        provider_caps = self.parallel_provider_caps()
        limits_valid = (
            self.langgraph_parallel_initial_max_concurrency == 2
            and self.langgraph_parallel_auto_scale_max_concurrency == 3
            and self.langgraph_parallel_clean_batches_to_scale == 10
            and self.langgraph_parallel_recovery_clean_batches == 3
            and all(value >= 1 for value in provider_caps.values())
        )
        task_writer_active = bool(
            self.langgraph_runtime_readiness()["task_writer_active"]
        )
        return {
            "enabled": self.langgraph_parallel_external_dispatch_enabled,
            "kill_switch_open": not self.langgraph_parallel_kill_switch,
            "limits_valid": limits_valid,
            "task_writer_active": task_writer_active,
            "initial_max_concurrency": self.langgraph_parallel_initial_max_concurrency,
            "auto_scale_max_concurrency": (
                self.langgraph_parallel_auto_scale_max_concurrency
            ),
            "clean_batches_to_scale": self.langgraph_parallel_clean_batches_to_scale,
            "recovery_clean_batches": self.langgraph_parallel_recovery_clean_batches,
            "active": (
                self.langgraph_parallel_external_dispatch_enabled
                and not self.langgraph_parallel_kill_switch
                and limits_valid
                and task_writer_active
            ),
        }

    def parallel_dispatch_health_readiness(self) -> dict[str, bool]:
        """Expose only boolean parallel-dispatch readiness on public health."""

        readiness = self.parallel_dispatch_readiness()
        return {
            key: bool(readiness[key])
            for key in (
                "enabled",
                "kill_switch_open",
                "limits_valid",
                "task_writer_active",
                "active",
            )
        }

    def validate_parallel_dispatch_configuration(self) -> None:
        readiness = self.parallel_dispatch_readiness()
        if not self.langgraph_parallel_external_dispatch_enabled or readiness["limits_valid"]:
            return
        raise ValueError(
            "Invalid enabled parallel dispatch configuration: caps must start at 2, "
            "scale to 3 after 10 clean batches, recover after 3 clean batches, and "
            "every provider cap must be positive"
        )

    def parallel_provider_caps(self) -> dict[str, int]:
        return {
            "openai": self.langgraph_parallel_openai_cap,
            "claude": self.langgraph_parallel_claude_cap,
            "cursor": self.langgraph_parallel_cursor_cap,
            "gemini": self.langgraph_parallel_gemini_cap,
            "grok": self.langgraph_parallel_grok_cap,
            "perplexity": self.langgraph_parallel_perplexity_cap,
        }
    # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]

    def oracle_merge_readiness(self) -> dict[str, bool]:
        app_parts = (
            bool(self.oracle_automerge_github_app_id),
            bool(self.oracle_automerge_github_installation_id),
            bool(self.oracle_automerge_github_app_private_key_b64),
        )
        app_configured = all(app_parts)
        static_token_configured = bool(self.oracle_automerge_github_token)
        credential_configured = (app_configured and not static_token_configured) or (
            static_token_configured and not any(app_parts)
        )
        return {
            "enabled": self.oracle_automerge_enabled,
            "kill_switch_open": not self.oracle_automerge_kill_switch,
            "github_credential_configured": credential_configured,
            "github_app_configured": app_configured and not static_token_configured,
            "github_static_token_configured": static_token_configured and not any(app_parts),
            "review_secret_configured": bool(self.oracle_automerge_review_secret),
            "required_checks_configured": bool(self.oracle_automerge_required_checks),
            "required_check_app_configured": bool(self.oracle_automerge_required_check_app),
            "allowed_paths_configured": bool(self.oracle_automerge_allowed_paths),
            "provider_actor_bindings_configured": bool(
                self.oracle_automerge_claude_actors
                and self.oracle_automerge_cursor_actors
            ),
        }

    def oracle_provider_actor_map(self) -> dict[str, frozenset[str]]:
        return {
            "claude": frozenset(item.lower() for item in self.oracle_automerge_claude_actors),
            "cursor": frozenset(item.lower() for item in self.oracle_automerge_cursor_actors),
            "grok": frozenset(item.lower() for item in self.oracle_automerge_grok_actors),
            "gemini": frozenset(item.lower() for item in self.oracle_automerge_gemini_actors),
            "openai": frozenset(item.lower() for item in self.oracle_automerge_openai_actors),
        }

    def oracle_github_credential_configured(self) -> bool:
        return self.oracle_merge_readiness()["github_credential_configured"]

    def provider_strategy(self) -> dict[str, dict[str, object]]:
        return {
            "claude": {
                "team": "primary",
                "priority": "acting_director_orchestrator",
                "project_role": "acting_director",
                "runtime_selection": (
                    "native_transport_injection_required" if self.claude_director_enabled
                    else "disabled_legacy_openai_runtime"
                ),
                "account_context": self.claude_account_context,
                "strengths": [
                    "orchestration and bounded planning",
                    "routing, recovery, and evidence reconciliation",
                    "independent acceptance review when not a material author",
                    "milestone reporting and owner escalation",
                ],
            },
            "openai": {
                "team": "primary",
                "priority": "bounded_engineering_and_independent_review",
                "project_role": "bounded_implementer_and_non_author_reviewer",
                "runtime_selection": (
                    "not_selected_as_director" if self.claude_director_enabled
                    else "legacy_responses_api_director"
                ),
                "worker_registration": "separate_authorized_adapter_required",
                "account_context": self.openai_account_context,
                "strengths": [
                    "bounded coding implementation",
                    "technical reproduction and quality assurance",
                    "independent review when not a material author",
                ],
            },
            "cursor": {
                "team": "primary",
                "priority": "primary_full_spectrum_repository_partner",
                "account_context": self.other_provider_account_context,
                "capability_parity_target": 1.0,
                "eligible_complexity": ["low", "medium", "high", "critical"],
                "strengths": [
                    "research and evidence verification",
                    "deep technical reasoning and planning support",
                    "complex implementation, debugging, and refactoring",
                    "architecture, reliability, and security-adjacent analysis",
                    "cross-platform quality assurance",
                    "independent exact-SHA review",
                    "technical leadership and coordination",
                    "isolated repository execution and draft pull requests",
                ],
            },
            "gemini": {
                "team": "supplemental",
                "priority": "subordinate_boilerplate_and_alternate_ideation",
                "account_context": self.other_provider_account_context,
                "strengths": [
                    "bounded boilerplate and scaffolding in predefined public fixture lanes",
                    "low-level mechanical implementation in those lanes",
                    "non-authoritative alternate ideas in those lanes",
                    "bounded sandbox execution for the above",
                ],
                "constraints": [
                    "supplemental only; never a primary-team substitute",
                    "work assignments must feed a later OpenAI, Claude, Cursor, or Grok integration assignment",
                    "cannot issue review, acceptance, or merge verdicts",
                    "cannot define contract or methodology truth",
                    "cannot initiate, self-route, widen, or autonomously continue a conversation",
                    "after a persisted Director invitation, may exchange bounded debate questions, options, accepted prior contributions, and one bounded Director revision request",
                    "debate and advisory votes stay non-authoritative at the configured weight",
                    "ordinary work receives no prose: no task, acceptance, objective, dependency result, Slack text, or repository context",
                    "dispatch is driven by antigravity_directive, a closed fixture lane plus bounded numeric controls",
                    "repository, starting_ref, allow_draft_pr, and public_research_query must stay unset",
                ],
            },
            "grok": {
                "team": "primary",
                "family": "cursor",
                "priority": "primary_cursor_family_repository_partner",
                "account_context": self.other_provider_account_context,
                "capability_parity_target": 1.0,
                "eligible_complexity": ["low", "medium", "high", "critical"],
                "eligible_responsibilities": [
                    "research",
                    "evidence_verification",
                    "programming",
                    "deep_reasoning",
                    "innovation",
                    "development",
                    "quality_assurance",
                    "leadership",
                ],
                "strengths": [
                    "full-spectrum repository implementation",
                    "independent technical reasoning and planning",
                    "architecture, reliability, and security-adjacent analysis",
                    "cross-platform quality assurance and exact-candidate review",
                    "technical leadership and coordination",
                ],
                "separation_of_duties": [
                    "cannot independently accept or merge its own candidate",
                    "cannot replace the executing Director's acceptance role",
                    "external writes still require the assigned execution surface and current deterministic gates",
                ],
                "current_transport_constraints": [
                    "the in-process Responses API contact is synchronous and tool-free",
                    "repository execution uses the separately authenticated Grok Build or Grok Bot contact",
                    "the configured API model must appear in the authenticated xAI model catalog before the first paid request in each process",
                ],
            },
            "perplexity": {
                "team": "supplemental",
                "priority": "optional_current_research_specialist",
                "account_context": self.other_provider_account_context,
                "strengths": ["current web research", "source gathering", "fact verification"],
            },
        }
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:34 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
