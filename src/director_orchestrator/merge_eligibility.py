# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""Provider-neutral, deterministic merge eligibility from authenticated evidence.

This module decides one question and performs no action: given evidence that the
caller has already authenticated, may a specific candidate head be merged?

Three properties make it safe to reuse across projects and providers:

* **Provider-neutral.** No provider name, model, vendor API, or account appears
  anywhere. Evidence carries opaque authenticated principals and record digests.
  A reviewer is "independent" because its principal is not a material author, not
  because of which company produced it.
* **Deterministic.** Same inputs, same verdict. There is no clock, no network, no
  randomness, and no filesystem access, so a verdict can be recomputed and
  audited later from the recorded evidence alone.
* **Fail-closed.** `eligible` is true only when every required authority is
  present *and* bound to the exact candidate head. Absent, stale, malformed, and
  contradictory evidence all produce the same answer: not eligible, with the
  reasons listed.

Deliberate non-goals: this module never merges, never opens or updates a pull
request, never contacts a forge, and never grants approval. It also never
upgrades assignment acceptance into project completion — that distinction is
preserved from `completion_gates` and re-checked here.
"""

from __future__ import annotations

import fnmatch
import hmac
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class MergeEligibilityError(ValueError):
    """The evidence bundle is structurally unusable and cannot be evaluated."""


def _exact(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass(frozen=True)
class MergeEligibilityPolicy:
    """Deterministic, owner-owned constraints. Never derived from model output."""

    allowed_base_refs: tuple[str, ...] = ("main",)
    allowed_path_globs: tuple[str, ...] = ()
    required_check_names: tuple[str, ...] = ()
    required_branch_prefix: str = ""
    require_owner_authorization: bool = True
    kill_switch_engaged: bool = True

    def __post_init__(self) -> None:
        if not self.allowed_base_refs:
            raise MergeEligibilityError("at least one allowed base ref is required")
        if len(set(self.allowed_path_globs)) != len(self.allowed_path_globs):
            raise MergeEligibilityError("allowed path globs must be unique")
        if len(set(self.required_check_names)) != len(self.required_check_names):
            raise MergeEligibilityError("required check names must be unique")


@dataclass(frozen=True)
class ReviewEvidence:
    """One authenticated review bound to an exact candidate head."""

    authenticated_principal: str
    authentication_record_digest: str
    reviewed_head_sha: str
    verdict: str
    record_digest: str


@dataclass(frozen=True)
class CheckEvidence:
    """One authenticated trusted-CI check observed at an exact candidate head."""

    check_name: str
    conclusion: str
    observed_head_sha: str
    trusted_app_verified: bool
    response_digest: str


@dataclass(frozen=True)
class MergeEvidenceBundle:
    """Everything the evaluator is permitted to consider."""

    repository: str
    base_ref: str
    head_ref: str
    candidate_head_sha: str
    changed_paths: tuple[str, ...]
    material_author_principals: frozenset[str]
    assignment_status: Mapping[str, Any]
    independent_reviews: tuple[ReviewEvidence, ...] = ()
    security_reviews: tuple[ReviewEvidence, ...] = ()
    checks: tuple[CheckEvidence, ...] = ()
    owner_authorization_digest: str | None = None
    authenticated_record_digests: frozenset[str] = field(default_factory=frozenset)


def _structural_reasons(bundle: MergeEvidenceBundle) -> list[str]:
    reasons: list[str] = []
    if not bundle.repository.strip():
        reasons.append("repository_missing")
    if not _SHA1.fullmatch(bundle.candidate_head_sha):
        reasons.append("candidate_head_sha_malformed")
    if not bundle.changed_paths:
        reasons.append("changed_paths_missing")
    elif len(set(bundle.changed_paths)) != len(bundle.changed_paths):
        reasons.append("changed_paths_not_unique")
    return reasons


def _authenticated(bundle: MergeEvidenceBundle, digest: str) -> bool:
    return _SHA256_DIGEST.fullmatch(digest) is not None and any(
        _exact(digest, known) for known in bundle.authenticated_record_digests
    )


def _bound_reviews(
    bundle: MergeEvidenceBundle,
    reviews: Sequence[ReviewEvidence],
    *,
    require_independence: bool,
) -> list[ReviewEvidence]:
    """Keep only reviews that are authenticated, approving, and head-exact."""

    accepted: list[ReviewEvidence] = []
    for review in reviews:
        if review.verdict != "APPROVED":
            continue
        if not _SHA1.fullmatch(review.reviewed_head_sha):
            continue
        if not _exact(review.reviewed_head_sha, bundle.candidate_head_sha):
            # Evidence for a superseded candidate never carries forward.
            continue
        if not _authenticated(bundle, review.authentication_record_digest):
            continue
        if not _authenticated(bundle, review.record_digest):
            continue
        if (
            require_independence
            and review.authenticated_principal in bundle.material_author_principals
        ):
            continue
        accepted.append(review)
    return accepted


def _check_reasons(
    bundle: MergeEvidenceBundle, policy: MergeEligibilityPolicy
) -> list[str]:
    reasons: list[str] = []
    if not policy.required_check_names:
        reasons.append("required_checks_not_configured")
        return reasons
    observed: dict[str, CheckEvidence] = {}
    for check in bundle.checks:
        if check.check_name in observed:
            reasons.append("duplicate_check_evidence")
            return reasons
        observed[check.check_name] = check
    for name in policy.required_check_names:
        check = observed.get(name)
        if check is None:
            reasons.append(f"required_check_missing:{name}")
            continue
        if not _exact(check.observed_head_sha, bundle.candidate_head_sha):
            reasons.append(f"required_check_head_mismatch:{name}")
            continue
        if not check.trusted_app_verified:
            reasons.append(f"required_check_untrusted:{name}")
            continue
        if check.conclusion != "SUCCESS":
            reasons.append(f"required_check_not_successful:{name}")
            continue
        if not _authenticated(bundle, check.response_digest):
            reasons.append(f"required_check_readback_unauthenticated:{name}")
    return reasons


def _path_reasons(
    bundle: MergeEvidenceBundle, policy: MergeEligibilityPolicy
) -> list[str]:
    if not policy.allowed_path_globs:
        return ["allowed_paths_not_configured"]
    outside = [
        path
        for path in bundle.changed_paths
        if not any(
            fnmatch.fnmatchcase(path, pattern) for pattern in policy.allowed_path_globs
        )
    ]
    return ["changed_path_outside_allowlist"] if outside else []


def evaluate_merge_eligibility(
    bundle: MergeEvidenceBundle, *, policy: MergeEligibilityPolicy
) -> dict[str, Any]:
    """Return a deterministic, fail-closed merge verdict. Nothing is merged.

    `eligible` is true only when the kill switch is open, the base ref and every
    changed path are inside owner-owned policy, assignment acceptance is already
    authorized on Completion Gate evidence, an independent non-author review and a
    security review are both bound to this exact head, every required trusted
    check succeeded at this exact head, and owner authorization is present when
    the policy requires it.
    """

    reasons: list[str] = _structural_reasons(bundle)

    if policy.kill_switch_engaged:
        reasons.append("kill_switch_engaged")
    if bundle.base_ref not in policy.allowed_base_refs:
        reasons.append("base_ref_not_allowlisted")
    if policy.required_branch_prefix and not bundle.head_ref.startswith(
        policy.required_branch_prefix
    ):
        reasons.append("head_ref_prefix_not_allowlisted")
    reasons.extend(_path_reasons(bundle, policy))

    status = bundle.assignment_status
    if status.get("scope") != "assignment":
        reasons.append("assignment_status_scope_invalid")
    if status.get("assignment_acceptance_authorized") is not True:
        reasons.append("assignment_acceptance_not_authorized")
    if status.get("gate_evidence_complete") is not True:
        reasons.append("gate_evidence_incomplete")
    # Assignment acceptance never implies project completion. Any bundle that
    # claims otherwise is contradictory evidence and fails closed.
    if status.get("project_completion_authorized") is True:
        reasons.append("assignment_status_claims_project_completion")

    independent = _bound_reviews(
        bundle, bundle.independent_reviews, require_independence=True
    )
    if not independent:
        reasons.append("independent_exact_sha_review_missing")
    security = _bound_reviews(
        bundle, bundle.security_reviews, require_independence=True
    )
    if not security:
        reasons.append("security_review_missing")

    reasons.extend(_check_reasons(bundle, policy))

    if policy.require_owner_authorization:
        digest = bundle.owner_authorization_digest
        if digest is None or not _authenticated(bundle, digest):
            reasons.append("owner_authorization_missing")

    unique_reasons = sorted(set(reasons))
    return {
        "eligible": not unique_reasons,
        "candidate_head_sha": bundle.candidate_head_sha,
        "repository": bundle.repository,
        "independent_review_principals": sorted(
            {review.authenticated_principal for review in independent}
        ),
        "security_review_principals": sorted(
            {review.authenticated_principal for review in security}
        ),
        "blocking_reasons": unique_reasons,
        # This module composes evidence. Performing the merge is a separate,
        # separately gated action that this verdict does not authorize.
        "merge_performed": False,
        "authorizes_deployment": False,
        "authorizes_project_completion": False,
    }
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
