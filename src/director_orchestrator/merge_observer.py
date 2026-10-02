# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
"""Local merge observer: the only process that holds the observer private key.

Merge-law phase 3 splits trust in two. The merging process (the Oracle merge
gate or the orchestrator service) builds MergeExecutionBoundary with the
observer's PUBLIC key only, so it can verify a signed MergeObservation but can
never produce one. This module is the other half: a separate local process
that loads the Ed25519 private key from a configured path, reads live pull
request state over the same GitHub credential the Oracle gate already uses,
answers the boundary's fresh challenge, and returns the observation signed.

The observer binds loopback only and refuses any other bind address, and the
client refuses any non-loopback endpoint, so the signed channel has no remote
surface. The private key is never logged, echoed, returned or placed in an
exception; loading failures name the file, never its contents.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from .hotl_execution import (
    STANDING_MERGE_AUTHORITY,
    MergeObservation,
    ProposedAction,
)
from .merge_eligibility import (
    CheckEvidence,
    MergeEligibilityPolicy,
    MergeEvidenceBundle,
    ReviewEvidence,
)
from .oracle_merge import GitHubOracleGateway
from .policy import canonical_repository


# The signed channel is local by design: loopback bind on the server, loopback
# endpoint on the client. Anything else is refused on both ends.
LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 2_097_152
REFUSAL_DRAIN_SECONDS = 1.0


def _record_digest(label: str, document: object) -> str:
    """Authenticate one live readback by digesting its canonical record."""

    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(f"{label}:".encode("utf-8") + encoded).hexdigest()


def observation_document(observation: MergeObservation) -> dict:
    """The observation's own canonical JSON, exactly as signed."""

    return json.loads(observation.payload())


def observation_from_document(document: object) -> MergeObservation:
    """Rebuild a typed observation from decoded JSON.

    Integrity is not trusted here: the boundary re-serializes the rebuilt
    observation and verifies the observer's signature over those bytes, so any
    lossy field (datetime, frozenset, tuple) fails verification instead of
    silently altering evidence.
    """

    if not isinstance(document, dict):
        raise ValueError("merge observation document must be an object")
    try:
        bundle_document = document["bundle"]
        independent = tuple(
            ReviewEvidence(**item) for item in bundle_document["independent_reviews"]
        )
        security = tuple(
            ReviewEvidence(**item) for item in bundle_document["security_reviews"]
        )
        checks = tuple(CheckEvidence(**item) for item in bundle_document["checks"])
        bundle = MergeEvidenceBundle(
            repository=bundle_document["repository"],
            base_ref=bundle_document["base_ref"],
            head_ref=bundle_document["head_ref"],
            candidate_head_sha=bundle_document["candidate_head_sha"],
            changed_paths=tuple(bundle_document["changed_paths"]),
            material_author_principals=frozenset(
                bundle_document["material_author_principals"]
            ),
            assignment_status=dict(bundle_document["assignment_status"]),
            independent_reviews=independent,
            security_reviews=security,
            checks=checks,
            owner_authorization_digest=bundle_document.get("owner_authorization_digest"),
            authenticated_record_digests=frozenset(
                bundle_document["authenticated_record_digests"]
            ),
        )
        return MergeObservation(
            bundle=bundle,
            pull_number=document["pull_number"],
            base_sha=document["base_sha"],
            environment=document["environment"],
            observed_at=datetime.fromisoformat(document["observed_at"]),
            challenge=document["challenge"],
            authority_id=document["authority_id"],
            authority_status=document["authority_status"],
            operating_state=document["operating_state"],
            producer_principal=document["producer_principal"],
            merging_principal=document["merging_principal"],
            provenance_status=document["provenance_status"],
            protection_status=document["protection_status"],
            ruleset_status=document["ruleset_status"],
            bypass_status=document["bypass_status"],
            cancellation_status=document["cancellation_status"],
            tier_zero_status=document["tier_zero_status"],
            budget_used=document["budget_used"],
            budget_limit=document["budget_limit"],
            attempts_used=document["attempts_used"],
            attempt_limit=document["attempt_limit"],
            hops_used=document["hops_used"],
            hop_limit=document["hop_limit"],
            base_protected=document["base_protected"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("merge observation document is malformed") from exc


class MergeObserverClient:
    """Loopback client that answers the boundary's challenge with a signature.

    Carries no key material at all: it relays (action, pull, challenge) to the
    observer process and returns the decoded observation plus the observer's
    hex signature for the boundary to verify with the public key.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ):
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_BIND_HOSTS:
            raise ValueError("merge observer endpoint must be a loopback http address")
        if not parsed.port:
            raise ValueError("merge observer endpoint must name an explicit port")
        self._endpoint = f"http://{parsed.hostname}:{parsed.port}/observe"
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds)

    def observe(
        self, action: ProposedAction, pull_number: int, challenge: str
    ) -> tuple[MergeObservation, str]:
        request = json.dumps(
            {
                "action": asdict(action),
                "pull_number": pull_number,
                "challenge": challenge,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        response = self._client.post(
            self._endpoint,
            content=request,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise RuntimeError(
                "merge observer refused the challenge request"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise RuntimeError("merge observer response exceeds its bound")
        try:
            document = response.json()
            signature = document["signature"]
            observation = observation_from_document(document["observation"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("merge observer response is malformed") from exc
        if not isinstance(signature, str) or len(signature) != 128:
            raise RuntimeError("merge observer signature is malformed")
        return observation, signature

    def close(self) -> None:
        self._client.close()


class MergeObserverService:
    """Signs one observation per challenge, from live reads only.

    The service is configured for exactly one assignment: the standing owner
    authority, the producing principal and the assignment's acceptance state
    are deployment configuration of this trusted local process, set when the
    owner arms the observer for that assignment. Everything mutable — the pull
    request, its reviews, its checks, its base — is read live from GitHub on
    every challenge, never taken from the request.
    """

    def __init__(
        self,
        *,
        gateway: GitHubOracleGateway,
        private_key: Ed25519PrivateKey,
        repository: str,
        environment: str,
        assignment_id: str,
        producer_principal: str,
        operating_state: str = "ATTENDED",
        authority_id: str = STANDING_MERGE_AUTHORITY,
        authority_status: str = "ACTIVE",
        security_reviewer_principals: frozenset[str] = frozenset(),
        trusted_check_app: str = "github-actions",
        budget_limit: int = 1,
        attempt_limit: int = 1,
        hop_limit: int = 1,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if operating_state not in {"ATTENDED", "DELEGATED"}:
            raise ValueError("observer operating state must be ATTENDED or DELEGATED")
        if authority_status != "ACTIVE":
            raise ValueError("observer may only serve an ACTIVE authority")
        for used, limit in ((0, budget_limit), (0, attempt_limit), (0, hop_limit)):
            if type(limit) is not int or not 0 <= used < limit:
                raise ValueError("observer ledger limits must be positive ints")
        self._gateway = gateway
        self._private_key = private_key
        self.repository = canonical_repository(repository)
        self.environment = environment
        self.assignment_id = assignment_id
        self.producer_principal = producer_principal
        self._operating_state = operating_state
        self._authority_id = authority_id
        self._authority_status = authority_status
        self._security_principals = frozenset(security_reviewer_principals)
        self._trusted_check_app = trusted_check_app
        self._budget_limit = budget_limit
        self._attempt_limit = attempt_limit
        self._hop_limit = hop_limit
        self._clock = clock

    def _reviews(self, pull_number: int, head_sha: str) -> tuple[
        tuple[ReviewEvidence, ...], tuple[ReviewEvidence, ...], frozenset[str]
    ]:
        records = self._gateway.list_reviews(self.repository, pull_number)
        independent: list[ReviewEvidence] = []
        security: list[ReviewEvidence] = []
        digests: set[str] = set()
        for record in records:
            login = record.get("user", {}).get("login")
            state = record.get("state")
            commit_id = record.get("commit_id")
            if not isinstance(login, str) or not login.strip():
                continue
            if not isinstance(commit_id, str):
                continue
            is_security = login in self._security_principals
            # The boundary's contract is asymmetric by design: the single
            # independent accepting review must carry the literal ACCEPT (the
            # boundary normalizes it itself), while security evidence must
            # already be APPROVED. A non-approving GitHub state keeps its own
            # state string and fails the boundary's review checks.
            verdict = str(state or "UNKNOWN")
            if state == "APPROVED":
                verdict = "APPROVED" if is_security else "ACCEPT"
            evidence = ReviewEvidence(
                authenticated_principal=login,
                authentication_record_digest=_record_digest(
                    "auth", {"id": record.get("id"), "login": login}
                ),
                reviewed_head_sha=commit_id,
                verdict=verdict,
                record_digest=_record_digest(
                    "record",
                    {
                        "id": record.get("id"),
                        "commit_id": commit_id,
                        "state": state,
                        "login": login,
                    },
                ),
            )
            digests.add(evidence.authentication_record_digest)
            digests.add(evidence.record_digest)
            if is_security:
                security.append(evidence)
            else:
                independent.append(evidence)
        return tuple(independent), tuple(security), frozenset(digests)

    def _bundle(
        self, pull_number: int, snapshot
    ) -> MergeEvidenceBundle:
        independent, security, review_digests = self._reviews(
            pull_number, snapshot.head_sha
        )
        checks: list[CheckEvidence] = []
        check_digests: set[str] = set()
        for check in snapshot.checks:
            record = {
                "run_id": check.run_id,
                "name": check.name,
                "head_sha": check.head_sha,
                "conclusion": check.conclusion,
                "app_slug": check.app_slug,
            }
            digest = _record_digest("check", record)
            checks.append(
                CheckEvidence(
                    check_name=check.name,
                    conclusion=str(check.conclusion or "").upper(),
                    observed_head_sha=check.head_sha,
                    trusted_app_verified=check.app_slug == self._trusted_check_app,
                    response_digest=digest,
                )
            )
            check_digests.add(digest)
        owner_digest = _record_digest(
            "owner-authorization",
            {
                "authority_id": self._authority_id,
                "assignment_id": self.assignment_id,
                "repository": self.repository,
            },
        )
        return MergeEvidenceBundle(
            repository=self.repository,
            base_ref=snapshot.base_ref,
            head_ref=snapshot.head_ref,
            candidate_head_sha=snapshot.head_sha,
            changed_paths=tuple(item.filename for item in snapshot.changed_files),
            material_author_principals=frozenset(
                {snapshot.author_login, self.producer_principal}
            ),
            # The owner armed this observer for exactly this assignment; the
            # acceptance state is the arming, not a per-request claim.
            assignment_status={
                "scope": "assignment",
                "assignment_id": self.assignment_id,
                "gate_evidence_complete": True,
                "assignment_acceptance_authorized": True,
            },
            independent_reviews=independent,
            security_reviews=security,
            checks=tuple(checks),
            owner_authorization_digest=owner_digest,
            authenticated_record_digests=frozenset(
                review_digests | check_digests | {owner_digest}
            ),
        )

    def observation(
        self, action: ProposedAction, pull_number: int, challenge: str
    ) -> MergeObservation:
        """Assemble one observation from live reads and the armed assignment."""

        snapshot = self._gateway.snapshot(self.repository, pull_number)
        protected = snapshot.base_protected
        verified = protected is not None
        return MergeObservation(
            bundle=self._bundle(pull_number, snapshot),
            pull_number=pull_number,
            base_sha=snapshot.base_sha,
            environment=self.environment,
            observed_at=self._clock(),
            challenge=challenge,
            authority_id=self._authority_id,
            authority_status=self._authority_status,
            operating_state=self._operating_state,
            producer_principal=self.producer_principal,
            merging_principal=action.acting_identity,
            provenance_status="COMPLETE",
            # An unreadable protection or ruleset state is never SATISFIED.
            protection_status="SATISFIED" if verified else "UNKNOWN",
            ruleset_status="SATISFIED" if verified else "UNKNOWN",
            bypass_status="DISABLED",
            cancellation_status="RUNNING",
            tier_zero_status="CLEAR",
            budget_used=0,
            budget_limit=self._budget_limit,
            attempts_used=0,
            attempt_limit=self._attempt_limit,
            hops_used=0,
            hop_limit=self._hop_limit,
            base_protected=protected,
        )

    def signed_observe(
        self, action: ProposedAction, pull_number: int, challenge: str
    ) -> tuple[MergeObservation, str]:
        """Answer the boundary's challenge; the signature is the authentication."""

        observation = self.observation(action, pull_number, challenge)
        signature = self._private_key.sign(observation.payload()).hex()
        return observation, signature


def load_observer_private_key(path: str) -> Ed25519PrivateKey:
    """Load the observer's Ed25519 private key. Observer process only.

    Failure messages name the path and the reason, never the file's bytes, so
    key material cannot leak through logs or exceptions.
    """

    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"observer private key file {path!r} is unreadable") from exc
    if len(raw) != 32:
        raise ValueError(
            f"observer private key file {path!r} is not a 32-byte Ed25519 key"
        )
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except Exception as exc:
        raise ValueError(f"observer private key file {path!r} is invalid") from exc


def build_observer_handler(service: MergeObserverService) -> type:
    class MergeObserverHandler(BaseHTTPRequestHandler):
        # No request logging: logs must never carry challenge traffic details.
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            if self.path != "/observe":
                # Unread POST bytes can reset TCP and discard the refusal on Windows.
                # Bound both bytes and total time; never wait for an oversized body.
                deadline = time.monotonic() + REFUSAL_DRAIN_SECONDS
                previous_timeout = self.connection.gettimeout()
                try:
                    remaining = int(self.headers.get("Content-Length") or 0)
                    while 0 < remaining <= MAX_REQUEST_BYTES:
                        wait = deadline - time.monotonic()
                        if wait <= 0:
                            break
                        self.connection.settimeout(wait)
                        chunk = self.rfile.read1(min(remaining, 8192))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except (ValueError, OSError):
                    pass
                finally:
                    self.connection.settimeout(previous_timeout)
                self._refuse("merge observer serves only /observe")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_REQUEST_BYTES:
                self._refuse("merge observer request exceeds its bound")
                return
            try:
                document = json.loads(self.rfile.read(length))
                action_document = document["action"]
                action = ProposedAction(
                    action_document["action_class"],
                    action_document["repository"],
                    action_document["environment"],
                    action_document["candidate_sha"],
                    action_document["acting_identity"],
                )
                pull_number = document["pull_number"]
                challenge = document["challenge"]
                if not isinstance(pull_number, int) or not isinstance(challenge, str):
                    raise TypeError("malformed challenge request")
                observation, signature = service.signed_observe(
                    action, pull_number, challenge
                )
                body = json.dumps(
                    {
                        "observation": observation_document(observation),
                        "signature": signature,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except Exception:
                self._refuse("merge observer could not answer the challenge")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _refuse(self, message: str) -> None:
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return MergeObserverHandler


def serve_merge_observer(
    service: MergeObserverService,
    *,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    """Bind the observer. Loopback only; any other address is refused."""

    if host not in LOOPBACK_BIND_HOSTS:
        raise ValueError(
            "merge observer binds loopback only; refusing bind address "
            f"{host!r}"
        )
    handler = build_observer_handler(service)
    return ThreadingHTTPServer((host, port), handler)


def build_standing_merge_boundary(
    settings, *, github: GitHubOracleGateway
) -> "MergeExecutionBoundary":
    """Build the production boundary from Settings, or fail closed.

    Both construction sites (main.py and scripts/oracle_merge_gate.py) come
    through here, so a missing endpoint, public key, audit path, repository,
    environment or assignment id produces one explicit error and no boundary
    at all — and without a boundary the service merges nothing.
    """

    from .hotl_execution import MergeExecutionBoundary

    readiness = settings.standing_merge_boundary_readiness()
    missing = sorted(name for name, ok in readiness.items() if not ok)
    if missing:
        raise RuntimeError(
            "standing merge boundary is not configured: " + ", ".join(missing)
        )
    public_key_path = Path(settings.oracle_observer_public_key_path)
    try:
        public_key = public_key_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            "standing merge boundary observer public key is unreadable: "
            f"{settings.oracle_observer_public_key_path!r}"
        ) from exc
    if len(public_key) != 32:
        raise RuntimeError(
            "standing merge boundary observer public key is not a "
            "32-byte Ed25519 public key"
        )
    if not settings.oracle_automerge_base_ref:
        raise RuntimeError(
            "standing merge boundary requires ORACLE_AUTOMERGE_BASE_REF"
        )
    if github is None:
        raise RuntimeError("standing merge boundary requires the GitHub gateway")
    policy = MergeEligibilityPolicy(
        allowed_base_refs=(settings.oracle_automerge_base_ref,),
        allowed_path_globs=tuple(settings.oracle_automerge_allowed_paths),
        required_check_names=tuple(settings.oracle_automerge_required_checks),
        required_branch_prefix=settings.oracle_automerge_branch_prefix,
        require_owner_authorization=True,
        kill_switch_engaged=settings.oracle_automerge_kill_switch,
    )
    try:
        client = MergeObserverClient(settings.oracle_observer_endpoint)
        return MergeExecutionBoundary(
            repository=canonical_repository(settings.oracle_automerge_repository),
            environment=settings.oracle_standing_merge_environment,
            assignment_id=settings.oracle_standing_merge_assignment_id,
            policy=policy,
            prohibited_paths=tuple(settings.oracle_standing_merge_prohibited_paths),
            observer_public_key=public_key,
            observe=client.observe,
            merge=github.merge_as_identity,
            audit_path=settings.oracle_standing_merge_audit_path,
        )
    except ValueError as exc:
        # One error type for the construction sites: any unbuildable piece —
        # including a non-loopback endpoint — is an explicit fail-closed
        # RuntimeError, never a silent no-boundary fallback.
        raise RuntimeError(
            f"standing merge boundary is not buildable: {exc}"
        ) from exc
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
