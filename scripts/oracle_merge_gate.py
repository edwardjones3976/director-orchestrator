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
        description="Evaluate one immutable Claude review artifact and fail closed before any merge."
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
    if policy.enabled and not policy.kill_switch and settings.oracle_automerge_github_token:
        github = GitHubOracleGateway(settings.oracle_automerge_github_token)

    result = OracleMergeService(policy, store, github).process(artifact)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == OracleMergeStatus.MERGED else 2


if __name__ == "__main__":
    raise SystemExit(main())
