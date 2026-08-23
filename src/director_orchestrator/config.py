# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]
from __future__ import annotations

import hmac
import os
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
import re
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
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


def _csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def _csv_exact(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


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
    project_allowlist: tuple[str, ...] = _csv("PROJECT_ALLOWLIST", "")
    repository_allowlist: tuple[str, ...] = _csv("REPOSITORY_ALLOWLIST", "")
    allowed_starting_refs: tuple[str, ...] = _csv_exact("ALLOWED_STARTING_REFS", "main")

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
    oracle_automerge_gemini_actors: tuple[str, ...] = _csv(
        "ORACLE_AUTOMERGE_GEMINI_ACTORS", ""
    )

    usage_check_enabled: bool = _bool("USAGE_CHECK_ENABLED", False)
    usage_check_interval_minutes: int = _int("USAGE_CHECK_INTERVAL_MINUTES", 360)
    usage_check_initial_delay_seconds: int = _int("USAGE_CHECK_INITIAL_DELAY_SECONDS", 60)
    usage_check_provider_delay_seconds: int = _int("USAGE_CHECK_PROVIDER_DELAY_SECONDS", 5)
    usage_check_timeout_seconds: int = _int("USAGE_CHECK_TIMEOUT_SECONDS", 300)
    usage_check_max_output_tokens: int = _int("USAGE_CHECK_MAX_OUTPUT_TOKENS", 96)
    usage_check_providers: tuple[str, ...] = _csv(
        "USAGE_CHECK_PROVIDERS", "openai,claude,cursor"
    )

    primary_team: tuple[str, ...] = _csv("PRIMARY_TEAM", "openai,claude,cursor")
    primary_workhorse: str = os.getenv("PRIMARY_WORKHORSE", "claude").strip().lower()
    claude_account_context: str = os.getenv("CLAUDE_ACCOUNT_CONTEXT", "Claude Pro 20x")
    openai_account_context: str = os.getenv(
        "OPENAI_ACCOUNT_CONTEXT", "ChatGPT Plus; OpenAI API usage is billed separately"
    )
    other_provider_account_context: str = os.getenv(
        "OTHER_PROVIDER_ACCOUNT_CONTEXT",
        "Comparable paid individual tiers; API billing and quotas must be verified separately",
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
    openai_model: str = os.getenv("OPENAI_DIRECTOR_MODEL", "gpt-5.6")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "medium")
    # Explicit accounting estimates for Director calls. Defaults preserve the
    # existing deployment budget while allowing operators to reserve a
    # conservative amount for every planning/review call.
    openai_plan_estimated_cost_usd: float = _float(
        "OPENAI_PLAN_ESTIMATED_COST_USD", 0.0
    )
    openai_review_estimated_cost_usd: float = _float(
        "OPENAI_REVIEW_ESTIMATED_COST_USD", 0.0
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
            "perplexity": all(self.perplexity_research_readiness().values()),
        }

    def usage_check_readiness(self) -> dict[str, bool]:
        readiness = self.readiness()
        return {
            "openai": readiness["openai_director"],
            "cursor": readiness["cursor"],
            "claude": readiness["claude"],
            "gemini": readiness["gemini"],
            "perplexity": readiness["perplexity"],
        }

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
            "runtime_active": False,
            "legacy_sole_writer": True,
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
            "gemini": frozenset(item.lower() for item in self.oracle_automerge_gemini_actors),
        }

    def oracle_github_credential_configured(self) -> bool:
        return self.oracle_merge_readiness()["github_credential_configured"]

    def provider_strategy(self) -> dict[str, dict[str, object]]:
        return {
            "claude": {
                "team": "primary",
                "priority": "primary_workhorse",
                "account_context": self.claude_account_context,
                "strengths": [
                    "coding implementation",
                    "repository-scale reasoning",
                    "code and document review",
                    "long-form analysis",
                ],
            },
            "openai": {
                "team": "primary",
                "priority": "director_planning_and_acceptance_review",
                "account_context": self.openai_account_context,
                "strengths": ["orchestration", "planning", "structured acceptance review"],
            },
            "cursor": {
                "team": "primary",
                "priority": "primary_full_spectrum_repository_partner",
                "account_context": self.other_provider_account_context,
                "capability_parity_target": 0.9,
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
                    "work assignments must feed a later Claude or Cursor integration assignment",
                    "cannot issue review, acceptance, or merge verdicts",
                    "cannot define contract or methodology truth",
                    "advisory debate votes stay non-authoritative at the configured weight",
                    "receives no prose at all: no task, acceptance, objective, dependency result, Slack text, or repository context",
                    "dispatch is driven by antigravity_directive, a closed fixture lane plus bounded numeric controls",
                    "repository, starting_ref, allow_draft_pr, and public_research_query must stay unset",
                    "cannot be seated in a debate; its advisory weight stays in the denominator and is never cast",
                ],
            },
            "perplexity": {
                "team": "supplemental",
                "priority": "optional_current_research_specialist",
                "account_context": self.other_provider_account_context,
                "strengths": ["current web research", "source gathering", "fact verification"],
            },
        }
