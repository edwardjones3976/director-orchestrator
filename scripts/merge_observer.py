# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
"""Run the local merge observer: the sole holder of the observer private key.

This process answers MergeExecutionBoundary's fresh challenge with a signed
MergeObservation. It binds loopback only, reuses the Oracle gate's GitHub
credential for its live reads, and is armed for exactly one assignment via
environment configuration. Deploying it (and creating the real key pair) is a
separate owner step; this entry point just runs it.
"""

from __future__ import annotations

import argparse
import signal
import sys

from director_orchestrator.config import Settings
from director_orchestrator.merge_observer import (
    MergeObserverService,
    load_observer_private_key,
    serve_merge_observer,
)
from director_orchestrator.oracle_merge import GitHubOracleGateway


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Serve signed merge observations on loopback for the standing "
            "merge boundary. Only this process reads the private key file."
        )
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Loopback bind address (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, required=True, help="Loopback bind port"
    )
    parser.add_argument(
        "--private-key-path",
        default=None,
        help="Ed25519 private key file; defaults to ORACLE_OBSERVER_PRIVATE_KEY_PATH",
    )
    parser.add_argument(
        "--producer-principal",
        default=None,
        help="Producing principal for the armed assignment; defaults to "
        "ORACLE_STANDING_MERGE_PRODUCER_PRINCIPAL",
    )
    args = parser.parse_args()

    settings = Settings()
    key_path = args.private_key_path or settings.oracle_observer_private_key_path
    if not key_path:
        print(
            "merge observer requires a private key path "
            "(--private-key-path or ORACLE_OBSERVER_PRIVATE_KEY_PATH)",
            file=sys.stderr,
        )
        return 2
    producer = args.producer_principal or settings.oracle_standing_merge_producer_principal
    if not producer:
        print(
            "merge observer requires a producer principal "
            "(--producer-principal or ORACLE_STANDING_MERGE_PRODUCER_PRINCIPAL)",
            file=sys.stderr,
        )
        return 2
    if not settings.oracle_github_credential_configured():
        print(
            "merge observer requires the Oracle gate's configured GitHub "
            "credential; it adds no new credential path",
            file=sys.stderr,
        )
        return 2
    for name, value in settings.standing_merge_boundary_readiness().items():
        if not value and name != "observer_public_key_configured":
            print(
                f"merge observer is not armed: {name} is not configured",
                file=sys.stderr,
            )
            return 2

    private_key = load_observer_private_key(key_path)
    gateway = GitHubOracleGateway.from_settings(settings)
    service = MergeObserverService(
        gateway=gateway,
        private_key=private_key,
        repository=settings.oracle_automerge_repository,
        environment=settings.oracle_standing_merge_environment,
        assignment_id=settings.oracle_standing_merge_assignment_id,
        producer_principal=producer,
        security_reviewer_principals=frozenset(
            settings.oracle_standing_merge_security_actors
        ),
        trusted_check_app=settings.oracle_automerge_required_check_app,
    )
    try:
        server = serve_merge_observer(service, host=args.host, port=args.port)
    except ValueError as exc:
        print(f"merge observer refused to start: {exc}", file=sys.stderr)
        return 2

    def stop(*_: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        f"merge observer serving on {args.host}:{args.port} for assignment "
        f"{settings.oracle_standing_merge_assignment_id!r}; loopback only",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
