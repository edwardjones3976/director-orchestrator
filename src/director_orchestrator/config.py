from __future__ import annotations

import os
from dataclasses import dataclass


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
    n8n_webhook_url: str = os.getenv("N8N_DIRECTOR_WEBHOOK_URL", "")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_DIRECTOR_MODEL", "gpt-5.6")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "medium")

    cursor_api_key: str = os.getenv("CURSOR_API_KEY", "")
    cursor_api_base: str = os.getenv("CURSOR_API_BASE", "https://api.cursor.com")

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

    perplexity_api_key: str = os.getenv("PERPLEXITY_API_KEY", "")
    perplexity_api_base: str = os.getenv("PERPLEXITY_API_BASE", "https://api.perplexity.ai")
    perplexity_preset: str = os.getenv("PERPLEXITY_PRESET", "fast-search")

    def readiness(self) -> dict[str, bool]:
        return {
            "slack": bool(self.slack_app_token and self.slack_bot_token),
            "openai_director": bool(self.openai_api_key),
            "cursor": bool(self.cursor_api_key),
            "claude": bool(self.claude_routine_url and self.claude_routine_token),
            "gemini": bool(self.gemini_api_key),
            "perplexity": bool(self.perplexity_api_key),
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

    def execution_profile_readiness(self) -> dict[str, bool]:
        return {
            "projects_configured": bool(self.project_allowlist),
            "repositories_configured": bool(self.repository_allowlist),
            "starting_refs_configured": bool(self.allowed_starting_refs),
            "claude_callback_configured": bool(self.claude_callback_secret),
        }

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
                "priority": "primary_repository_teammate_and_independent_second_pass",
                "account_context": self.other_provider_account_context,
                "strengths": ["isolated repository execution", "parallel implementation", "draft pull requests"],
            },
            "gemini": {
                "team": "supplemental",
                "priority": "optional_sandbox_and_prototype_specialist",
                "account_context": self.other_provider_account_context,
                "strengths": ["sandbox execution", "visual/browser validation", "alternate prototypes"],
            },
            "perplexity": {
                "team": "supplemental",
                "priority": "optional_current_research_specialist",
                "account_context": self.other_provider_account_context,
                "strengths": ["current web research", "source gathering", "fact verification"],
            },
        }
