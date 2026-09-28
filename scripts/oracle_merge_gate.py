# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.merge_observer import build_standing_merge_boundary
from director_orchestrator.oracle_merge import (
    GitHubOracleGateway,
    OracleMergePolicy,
    OracleMergeService,
    OracleMergeStatus,
    OracleMergeStore,
    OracleReviewArtifact,
)


def build_service(settings: Settings, database: str):
    """Build the gate's service, or fail closed with an explicit error.

    Returns (service, None) on success and (None, message) when the enabled
    gate cannot build the standing merge boundary: no service exists without
    the boundary, so no merge can happen.
    """

    policy = OracleMergePolicy(settings)
    store = OracleMergeStore(database)
    github = None
    standing_boundary = None
    if (
        policy.enabled
        and not policy.kill_switch
        and settings.oracle_github_credential_configured()
    ):
        github = GitHubOracleGateway.from_settings(settings)
        # Issue #306: this gate merges only through the standing boundary.
        # Unbuildable configuration is an explicit error and no merge at all,
        # never a legacy no-boundary fallback.
        try:
            standing_boundary = build_standing_merge_boundary(
                settings, github=github
            )
        except RuntimeError as exc:
            return None, f"oracle merge gate failed closed: {exc}"
    service = OracleMergeService(
        policy, store, github, standing_merge_boundary=standing_boundary
    )
    return service, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate one immutable OpenAI, Claude, Cursor, Grok, or Antigravity review artifact and fail closed before any merge."
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
    service, failure = build_service(
        settings, args.database or settings.sqlite_path
    )
    if failure is not None:
        print(failure, file=sys.stderr)
        return 2

    result = service.process(artifact)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == OracleMergeStatus.MERGED else 2


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:10 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
