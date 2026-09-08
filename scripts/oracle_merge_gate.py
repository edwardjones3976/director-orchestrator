# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
from __future__ import annotations

import argparse
import json
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.oracle_merge import (
    GitHubOracleGateway,
    OracleMergePolicy,
    OracleMergeService,
    OracleMergeStatus,
    OracleMergeStore,
    OracleReviewArtifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate one immutable OpenAI, Claude, Cursor, or Grok review artifact and fail closed before any merge."
    )
    parser.add_argument("artifact", type=Path, help="Path to the typed review-artifact JSON file")
    parser.add_argument(
        "--database",
        help="SQLite audit path; defaults to SQLITE_PATH from the active isolated profile",
    )
    args = parser.parse_args()

    artifact = OracleReviewArtifact.model_validate_json(
        args.artifact.read_text(encoding="utf-8")
    )
    settings = Settings()
    policy = OracleMergePolicy(settings)
    store = OracleMergeStore(args.database or settings.sqlite_path)

    github = None
    if (
        policy.enabled
        and not policy.kill_switch
        and settings.oracle_github_credential_configured()
    ):
        github = GitHubOracleGateway.from_settings(settings)

    result = OracleMergeService(policy, store, github).process(artifact)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == OracleMergeStatus.MERGED else 2


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
