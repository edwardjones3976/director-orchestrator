# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | BEGIN]]
"""Fixed GitHub OIDC trust and CI-only ephemeral acceptance issuer.

Only owner-installed policy is trusted. Candidate artifacts are data, never code.
The journal must persist the resolved authority atomically with acceptance.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, fields
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .provider_acceptance_v2 import (
    DOMAIN, VERSION, IssuerAuthority, ProviderAcceptanceError, ReviewContext, canonical,
)

OIDC_VERSION = "director-ci-oidc/v1"
ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
API = "https://api.github.com"
PRODUCTION_CI_CHECKS = frozenset({
    "Contribution attribution", "Python 3.12 (ubuntu-latest)",
    "Python 3.12 (windows-latest)", "Compose and container build",
    "Secret hygiene", "Cross-process recovery probe",
})


@dataclass(frozen=True)
class GitHubOIDCPolicy:
    repository: str
    repository_id: str
    repository_owner_id: str
    subject: str
    workflow_ref: str
    workflow_sha: str
    principal: str
    deployment: str
    required_ci_checks: frozenset[str]
    check_workflow_id: int
    check_workflow_path: str
    check_workflow_commit: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ProviderAcceptanceError("invalid CI repository policy")
        if any(not re.fullmatch(r"[1-9][0-9]*", v) for v in (self.repository_id, self.repository_owner_id)):
            raise ProviderAcceptanceError("immutable GitHub IDs required")
        # GitHub supports customized and immutable-ID subject formats. The full
        # subject is owner-pinned; repository/name/IDs are checked independently.
        if not isinstance(self.subject, str) or not self.subject or len(self.subject) > 1024 or any(c in self.subject for c in "\r\n"):
            raise ProviderAcceptanceError("exact owner-pinned CI subject required")
        if not self.workflow_ref.startswith(self.repository + "/.github/workflows/") or "@" not in self.workflow_ref:
            raise ProviderAcceptanceError("exact issuer workflow ref required")
        if any(re.fullmatch(r"[0-9a-f]{40}", v) is None for v in (self.workflow_sha, self.check_workflow_commit)):
            raise ProviderAcceptanceError("immutable CI source commits required")
        if type(self.check_workflow_id) is not int or self.check_workflow_id < 1:
            raise ProviderAcceptanceError("immutable check workflow ID required")
        if re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", self.check_workflow_path) is None:
            raise ProviderAcceptanceError("invalid check workflow path")
        self.authority(bytes(32))  # Reuse closed CI identity/check validation.

    @classmethod
    def from_document(cls, document: dict) -> GitHubOIDCPolicy:
        if not isinstance(document, dict) or set(document) != {f.name for f in fields(cls)}:
            raise ProviderAcceptanceError("invalid CI policy fields")
        values = dict(document)
        checks = values["required_ci_checks"]
        if not isinstance(checks, (list, frozenset)) or len(checks) != len(set(checks)):
            raise ProviderAcceptanceError("invalid CI check policy")
        values["required_ci_checks"] = frozenset(checks)
        return cls(**values)

    def authority(self, key: bytes) -> IssuerAuthority:
        return IssuerAuthority("ci", "github-actions", self.principal, self.deployment,
                               key, self.required_ci_checks, self.workflow_ref, self.workflow_sha)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderAcceptanceError("issuer HTTP redirects are forbidden")


def _read_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ProviderAcceptanceError("issuer response exceeds bound")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ProviderAcceptanceError("issuer response must be an object")
    return result


def oidc_audience(public_key: str, expected: ReviewContext) -> str:
    return OIDC_VERSION + ":" + hashlib.sha256(canonical(
        {"public_key": public_key, "context": expected.document()})).hexdigest()


def resolve_ci_identity(envelope: dict, *, expected: ReviewContext,
                        policy: GitHubOIDCPolicy, now: int) -> tuple[IssuerAuthority, str]:
    """Authenticate with fresh JWKS at a caller-trusted evaluation time.

    Readiness may supply the original acceptance time, but must still verify the
    original JWT and compare this result to its stored authority. A database key
    snapshot alone is not authentication. Historical key removal fails closed.
    """
    try:
        if not isinstance(envelope, dict) or set(envelope) != {"version", "public_key", "token"}:
            raise ValueError("invalid envelope")
        if envelope["version"] != OIDC_VERSION or expected.repository != policy.repository:
            raise ValueError("identity context mismatch")
        if not expected.issued_at <= now < expected.expires_at:
            raise ValueError("expired review context")
        key = base64.b64decode(envelope["public_key"], validate=True)
        if len(key) != 32 or base64.b64encode(key).decode() != envelope["public_key"]:
            raise ValueError("invalid ephemeral key")
        token = envelope["token"]
        if not isinstance(token, str) or len(token) > 32768:
            raise ValueError("invalid OIDC token")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("invalid OIDC algorithm")
        keys = _read_json(JWKS_URL).get("keys", [])
        matches = [item for item in keys if item.get("kid") == header["kid"] and item.get("kty") == "RSA"]
        if len(matches) != 1:
            raise ValueError("unknown OIDC signing key")
        public = jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key
        claims = jwt.decode(token, public, algorithms=["RS256"], issuer=ISSUER,
            audience=oidc_audience(envelope["public_key"], expected),
            options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud", "jti"],
                     "verify_exp": False, "verify_iat": False, "verify_nbf": False})
        for name, value in {"repository": policy.repository, "repository_id": policy.repository_id,
                "repository_owner_id": policy.repository_owner_id, "sub": policy.subject,
                "workflow_ref": policy.workflow_ref, "workflow_sha": policy.workflow_sha}.items():
            if claims.get(name) != value:
                raise ValueError("OIDC policy mismatch")
        if any(type(claims[k]) is not int for k in ("iat", "nbf", "exp")):
            raise ValueError("OIDC timestamps must be integers")
        if not claims["iat"] <= now < claims["exp"] or claims["nbf"] > now or not 0 < claims["exp"] - claims["iat"] <= 600:
            raise ValueError("OIDC lifetime invalid")
        if not all(isinstance(claims.get(k), str) and re.fullmatch(r"[1-9][0-9]*", claims[k]) for k in ("run_id", "run_attempt")):
            raise ValueError("OIDC run identity missing")
        return policy.authority(key), f"github-actions:{claims['run_id']}:{claims['run_attempt']}"
    except (ValueError, TypeError, KeyError, jwt.PyJWTError) as exc:
        raise ProviderAcceptanceError("GitHub OIDC identity rejected") from exc


def resolve_ci_authority(envelope: dict, *, expected: ReviewContext,
                         policy: GitHubOIDCPolicy, now: int) -> IssuerAuthority:
    """Compatibility wrapper; package verifiers must also bind issuing session."""
    return resolve_ci_identity(envelope, expected=expected, policy=policy, now=now)[0]


class GitHubEvidenceClient:
    """Authenticated fixed-host API reads only; no arbitrary URL or pagination link."""
    def __init__(self, token: str):
        if not token or any(c in token for c in "\r\n"):
            raise ProviderAcceptanceError("GitHub API credential required")
        self._headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"}

    def get(self, path: str) -> dict:
        if not path.startswith("/repos/") or ".." in path:
            raise ProviderAcceptanceError("invalid GitHub evidence path")
        return _read_json(API + path, self._headers)


def collect_ci_evidence(client: GitHubEvidenceClient, *, expected: ReviewContext,
                        policy: GitHubOIDCPolicy, run_id: int, run_attempt: int) -> dict:
    if expected.repository != policy.repository or any(type(n) is not int or n < 1 for n in (run_id, run_attempt)):
        raise ProviderAcceptanceError("invalid CI run binding")
    root = "/repos/" + policy.repository
    repo = client.get(root)
    if str(repo.get("id")) != policy.repository_id or str(repo.get("owner", {}).get("id")) != policy.repository_owner_id:
        raise ProviderAcceptanceError("CI repository identity changed")
    run = client.get(f"{root}/actions/runs/{run_id}/attempts/{run_attempt}")
    if (run.get("id") != run_id or run.get("run_attempt") != run_attempt
            or run.get("workflow_id") != policy.check_workflow_id
            or run.get("head_sha") != expected.activation.candidate_commit
            or run.get("repository", {}).get("id") != int(policy.repository_id)
            or run.get("event") != "push"
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ProviderAcceptanceError("CI run is not exact successful candidate evidence")
    pull = client.get(f"{root}/pulls/{expected.pull_request_number}")
    if (pull.get("number") != expected.pull_request_number
            or pull.get("head", {}).get("sha") != expected.activation.candidate_commit
            or pull.get("head", {}).get("repo", {}).get("id") != int(policy.repository_id)
            or pull.get("base", {}).get("repo", {}).get("id") != int(policy.repository_id)):
        raise ProviderAcceptanceError("pull request does not bind same-repository candidate")
    workflow = client.get(f"{root}/actions/workflows/{policy.check_workflow_id}")
    if workflow.get("id") != policy.check_workflow_id or workflow.get("path") != policy.check_workflow_path:
        raise ProviderAcceptanceError("check workflow identity changed")
    # Authenticate exact candidate tree and unchanged owner-reviewed CI source.
    commit = client.get(f"{root}/git/commits/{expected.activation.candidate_commit}")
    if commit.get("sha") != expected.activation.candidate_commit or commit.get("tree", {}).get("sha") != expected.activation.candidate_tree:
        raise ProviderAcceptanceError("candidate tree changed")
    blobs = [client.get(f"{root}/contents/{policy.check_workflow_path}?ref={ref}").get("sha")
             for ref in (expected.activation.candidate_commit, policy.check_workflow_commit)]
    if not isinstance(blobs[0], str) or re.fullmatch(r"[0-9a-f]{40}", blobs[0]) is None or blobs[0] != blobs[1]:
        raise ProviderAcceptanceError("candidate check workflow differs from trusted source")
    jobs = []
    for page in range(1, 11):
        result = client.get(f"{root}/actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100&page={page}")
        batch = result.get("jobs")
        if not isinstance(batch, list):
            raise ProviderAcceptanceError("invalid CI jobs")
        jobs.extend(batch)
        if len(jobs) == result.get("total_count"):
            break
    else:
        raise ProviderAcceptanceError("CI job evidence exceeds bound")
    checks = []
    for name in sorted(policy.required_ci_checks):
        selected = [job for job in jobs if job.get("name") == name]
        if len(selected) != 1:
            raise ProviderAcceptanceError("required CI job absent or duplicated")
        job = selected[0]
        if (job.get("run_id") != run_id or job.get("run_attempt") != run_attempt
                or job.get("head_sha") != expected.activation.candidate_commit
                or job.get("status") != "completed" or job.get("conclusion") != "success"):
            raise ProviderAcceptanceError("required CI job not successful for exact attempt")
        checks.append(dict(name=name, run_id=run_id, status="COMPLETED", conclusion="SUCCESS"))
    return {"run_id": run_id, "run_attempt": run_attempt, "workflow_id": policy.check_workflow_id,
            "head_sha": expected.activation.candidate_commit, "workflow_blob": blobs[0], "checks": checks}


def issue_ci_receipt(*, expected: ReviewContext, policy: GitHubOIDCPolicy,
                     run_id: int, run_attempt: int, github_token: str,
                     oidc_request_url: str, oidc_request_token: str) -> dict:
    """Observe API evidence, create one key, obtain bound OIDC, sign only CI."""
    evidence = collect_ci_evidence(GitHubEvidenceClient(github_token), expected=expected,
                                  policy=policy, run_id=run_id, run_attempt=run_attempt)
    key = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    parsed = urllib.parse.urlsplit(oidc_request_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".actions.githubusercontent.com") or parsed.username or parsed.password or parsed.fragment:
        raise ProviderAcceptanceError("invalid Actions OIDC request endpoint")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != "audience"] + [("audience", oidc_audience(encoded, expected))]
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    token = _read_json(url, {"Authorization": "Bearer " + oidc_request_token})["value"]
    envelope = dict(version=OIDC_VERSION, public_key=encoded, token=token)
    authority, issuer_session = resolve_ci_identity(envelope, expected=expected, policy=policy, now=int(time.time()))
    receipt = dict(role="ci", provider="github-actions", principal=authority.principal,
        deployment=authority.deployment, session_id=issuer_session,
        transport_evidence_digest=hashlib.sha256(canonical(evidence)).hexdigest(),
        verdict="SUCCESS", ci_checks=evidence["checks"], ci_workflow_ref=policy.workflow_ref,
        ci_workflow_commit=policy.workflow_sha)
    receipt["signature"] = base64.b64encode(key.sign(DOMAIN + canonical(
        dict(version=VERSION, context=expected.document(), receipt=receipt)))).decode()
    return {"receipt": receipt, "ci_identity": envelope}


def main() -> None:
    """Trusted workflow entrypoint; inputs are JSON data, never import targets."""
    import argparse
    import os
    from pathlib import Path
    from .primary_activation_evidence import ActivationIdentity

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    policy = GitHubOIDCPolicy.from_document(json.loads(os.environ["CI_ISSUER_POLICY_JSON"]))
    if not PRODUCTION_CI_CHECKS <= policy.required_ci_checks:
        raise ProviderAcceptanceError("production CI policy omits mandatory baseline checks")
    document = json.loads(os.environ["CI_REVIEW_CONTEXT_JSON"])
    document["activation"] = ActivationIdentity(**document["activation"])
    document["material_author_providers"] = frozenset(document["material_author_providers"])
    expected = ReviewContext(**document)
    result = issue_ci_receipt(expected=expected, policy=policy,
        run_id=int(os.environ["CI_CANDIDATE_RUN_ID"]),
        run_attempt=int(os.environ["CI_CANDIDATE_RUN_ATTEMPT"]),
        github_token=os.environ["GITHUB_TOKEN"],
        oidc_request_url=os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"],
        oidc_request_token=os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
    Path(args.output).write_bytes(canonical(result))


if __name__ == "__main__":
    main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:17 UTC | END]]
