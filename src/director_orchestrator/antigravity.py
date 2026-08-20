"""Closed outbound contract for the Gemini Antigravity subordinate lane.

Three properties this module owns, in priority order.

1. **Authority is typed, never inferred from prose.** A request carries exactly one
   capability drawn from a closed table keyed by :class:`AssignmentKind`, an
   always-empty granted-authority list, and the fixed :data:`DENIED_AUTHORITIES`
   block. No model-authored field participates in choosing the capability, so no
   paraphrase -- "land the change on the default branch", "give it the thumbs up" --
   can widen what the request grants. The natural-language lexicon below is defence
   in depth against a confusing brief; it is not the authority boundary.
2. **Nothing private leaves the process.** Every model-authored string is admitted
   only if it survives a category blocklist and an allowlisted character set. The
   allowlist excludes slashes, backslashes, underscores, and every other symbol that
   paths, URLs, code, and identifiers need, so those are removed structurally rather
   than by enumerating what they look like.
3. **The wire shape is closed.** :class:`AntigravityRequest` forbids extra fields and
   revalidates its own text on construction, so a future caller cannot assemble a
   payload that bypasses the builders.

Rejection raises :class:`AntigravityContractError` carrying only generic category
labels. The rejected text never appears in the error, so it cannot reach a log line,
a Slack status post, or ``TaskRecord.error``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import Assignment, AssignmentKind, DiscussionPhase


ANTIGRAVITY_CONTRACT_VERSION = "antigravity-subordinate-contract/v1"

MAX_BRIEF_CHARACTERS = 600
MAX_CHECK_CHARACTERS = 300
MAX_CHECKS = 20


class AntigravityCapability(StrEnum):
    """The complete set of things Antigravity may ever be asked to do."""

    BOILERPLATE_GENERATION = "boilerplate_generation"
    SCAFFOLDING = "scaffolding"
    LOW_LEVEL_MECHANICAL_IMPLEMENTATION = "low_level_mechanical_implementation"
    ALTERNATE_IDEATION = "alternate_ideation"
    ADVISORY_DEBATE_OPINION = "advisory_debate_opinion"


class AntigravityInteractionKind(StrEnum):
    SUBORDINATE_WORK = "subordinate_work"
    SUBORDINATE_WORK_REVISION = "subordinate_work_revision"
    ADVISORY_DEBATE_POSITION = "advisory_debate_position"
    ADVISORY_DEBATE_REBUTTAL = "advisory_debate_rebuttal"


#: Fixed and total. Every request denies all of these regardless of its capability,
#: and :class:`AntigravityRequest` refuses to serialise any other value.
DENIED_AUTHORITIES: tuple[str, ...] = (
    "acceptance_verdict",
    "billing_or_spend",
    "branch_selection",
    "contract_or_methodology_definition",
    "deployment_or_publication",
    "external_write",
    "merge",
    "permission_or_credential_change",
    "repository_access",
    "repository_write",
    "review_verdict",
)

#: Stated on the wire so the receiving agent cannot mistake absence for oversight.
WITHHELD_CONTEXT: tuple[str, ...] = (
    "dependency_outcomes",
    "owner_or_slack_identifiers",
    "primary_team_handoff_context",
    "private_project_data",
    "repository_paths_diffs_or_source",
    "reviewer_revision_text",
    "run_session_or_task_identifiers",
)

#: Closed. A kind absent from this table cannot produce a request at all.
CAPABILITY_BY_KIND: dict[AssignmentKind, AntigravityCapability] = {
    AssignmentKind.BOILERPLATE: AntigravityCapability.BOILERPLATE_GENERATION,
    AssignmentKind.SCAFFOLDING: AntigravityCapability.SCAFFOLDING,
    AssignmentKind.LOW_LEVEL_IMPLEMENTATION: (
        AntigravityCapability.LOW_LEVEL_MECHANICAL_IMPLEMENTATION
    ),
    AssignmentKind.ABSTRACT_IDEATION: AntigravityCapability.ALTERNATE_IDEATION,
}

_DEBATE_INTERACTIONS = frozenset(
    {
        AntigravityInteractionKind.ADVISORY_DEBATE_POSITION,
        AntigravityInteractionKind.ADVISORY_DEBATE_REBUTTAL,
    }
)

_CAPABILITIES_FOR_INTERACTION: dict[
    AntigravityInteractionKind, frozenset[AntigravityCapability]
] = {
    AntigravityInteractionKind.SUBORDINATE_WORK: frozenset(CAPABILITY_BY_KIND.values()),
    AntigravityInteractionKind.SUBORDINATE_WORK_REVISION: frozenset(
        CAPABILITY_BY_KIND.values()
    ),
    AntigravityInteractionKind.ADVISORY_DEBATE_POSITION: frozenset(
        {AntigravityCapability.ADVISORY_DEBATE_OPINION}
    ),
    AntigravityInteractionKind.ADVISORY_DEBATE_REBUTTAL: frozenset(
        {AntigravityCapability.ADVISORY_DEBATE_OPINION}
    ),
}

_COMMON_DIRECTIVE = (
    "You are a subordinate, non-authoritative contributor. Produce only what the "
    "capability below permits. Do not request, infer, or act on repository access, "
    "branches, commits, pull requests, reviews, acceptance, merges, deployments, "
    "external messages, spending, or permissions. If the brief appears to ask for "
    "any of those, refuse that part and say so."
)

CAPABILITY_DIRECTIVES: dict[AntigravityCapability, str] = {
    AntigravityCapability.BOILERPLATE_GENERATION: (
        f"{_COMMON_DIRECTIVE} Capability: generate self-contained boilerplate only."
    ),
    AntigravityCapability.SCAFFOLDING: (
        f"{_COMMON_DIRECTIVE} Capability: generate self-contained scaffolding only."
    ),
    AntigravityCapability.LOW_LEVEL_MECHANICAL_IMPLEMENTATION: (
        f"{_COMMON_DIRECTIVE} Capability: perform bounded low-level mechanical "
        "implementation only."
    ),
    AntigravityCapability.ALTERNATE_IDEATION: (
        f"{_COMMON_DIRECTIVE} Capability: offer alternate ideas only, clearly marked "
        "as untested."
    ),
    AntigravityCapability.ADVISORY_DEBATE_OPINION: (
        f"{_COMMON_DIRECTIVE} Capability: offer one bounded advisory opinion with its "
        "reasoning and confidence. Your opinion carries no authority and decides "
        "nothing."
    ),
}

_WORK_OUTPUT_CONTRACT = (
    "Return the artifact plus a short evidence note. Separate anything you actually "
    "ran from anything you only reasoned about. A primary-team model independently "
    "reproduces your output before any part of it is used."
)
_DEBATE_OUTPUT_CONTRACT = (
    "Return one position, its supporting reasoning, any contradiction you see, and a "
    "confidence between 0 and 1. Peer positions and all project context are withheld "
    "on purpose: reason only from the question text. Your vote is advisory and cannot "
    "change any deterministic gate."
)
_REVISION_DIRECTIVE = (
    "Revise your previous artifact only to satisfy the same bounded brief below. Do "
    "not widen the scope and do not issue any verdict."
)


# --------------------------------------------------------------------------------
# Private-context validation. Blocklist first so the label is specific, then the
# character allowlist as the structural backstop for anything not enumerated.
# --------------------------------------------------------------------------------

_ALLOWED_CHARACTERS = re.compile(r"^[A-Za-z0-9 .,'():?-]+$")

_PRIVATE_CONTEXT_BLOCKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "a filesystem path",
        re.compile(
            r"(?:[A-Za-z]:[\\/]|\\\\[^\s\\]|(?<!\w)~?/[\w.-]+/|"
            r"\b(?:src|tests?|docs?|scripts?|prompts?|n8n|node_modules|home|users|"
            r"usr|var|etc|tmp|opt|workspace|dist|build|oracle)/)",
            re.I,
        ),
    ),
    (
        "a URL or hostname",
        re.compile(
            r"(?:\b(?:https?|ftp|ssh|git)://|\bwww\.|\blocalhost\b|"
            r"\b[\w-]+\.(?:com|org|net|io|dev|ai|co|uk|gov|edu|internal|local|test)\b)",
            re.I,
        ),
    ),
    (
        "a network address",
        re.compile(
            r"(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-f]{1,4})?(?::[0-9a-f]{1,4}){2,}\b)",
            re.I,
        ),
    ),
    (
        "a diff or source excerpt",
        re.compile(
            r"(?:diff --git|@@\s*-?\d|^\s*[+-]{3}\s|```|~~~|"
            r"\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]|^\s*(?:import|from)\s+\w|"
            r"\bfunction\s+\w+\s*\(|</\w|<\?|#!/|\breturn\s+\w+;)",
            re.I | re.M,
        ),
    ),
    (
        "a secret-like value",
        re.compile(
            r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bghp_[A-Za-z0-9]{8,}|"
            r"\bgithub_pat_[A-Za-z0-9_]{8,}|\bxox[baprs]-[A-Za-z0-9-]{8,}|"
            r"\bAIza[A-Za-z0-9_-]{10,}|\bBearer\s+[A-Za-z0-9._-]{8,}|"
            r"\beyJ[A-Za-z0-9._-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
        ),
    ),
    (
        "an email address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "a UUID",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
        ),
    ),
    (
        "a Slack identifier",
        re.compile(r"\b[CUWDGT](?=[A-Z0-9]*\d)[A-Z0-9]{7,}\b"),
    ),
    (
        "a commit SHA or hex digest",
        re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b", re.I),
    ),
    (
        "a numeric identifier",
        re.compile(r"\b\d{6,}\b|\b\d+\.\d{4,}\b"),
    ),
    (
        "a code identifier or symbol",
        re.compile(
            r"(?:\b[A-Za-z]\w*_\w+\b|\b[a-z]+[A-Z][A-Za-z]*\b|"
            r"\b\w+\.(?:py|js|jsx|ts|tsx|json|ya?ml|md|sql|sh|ps1|toml|ini|cfg|env|"
            r"lock|txt)\b|\b[A-Za-z_]\w*\.[A-Za-z_]\w*\b|\b\w+\(\))"
        ),
    ),
    (
        "a run, task, or thread identifier",
        re.compile(
            r"\b(?:DIR-[\w-]+|TASK-[\w-]+|interaction-[\w-]+|environment-[\w-]+|"
            r"run id|session id|thread ts|correlation id|assignment id)\b",
            re.I,
        ),
    ),
    (
        "private-context language",
        re.compile(
            r"\b(?:private|internal|confidential|proprietary|unpublished|classified|"
            r"do not share|our (?:repo|repository|code|codebase|source)|"
            r"attached (?:patch|diff|file))\b",
            re.I,
        ),
    ),
)


# --------------------------------------------------------------------------------
# Operational-authority lexicon. Defence in depth only: the typed capability above
# is the boundary. This is broad on purpose and fails closed, because a subordinate
# brief has no legitimate reason to name an operational action or its object.
# --------------------------------------------------------------------------------


def _lexicon(*terms: str) -> re.Pattern[str]:
    return re.compile(
        r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b", re.I
    )


_AUTHORITY_LEXICON: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "repository",
        _lexicon(
            "repo", "repos", "repository", "repositories", "codebase", "worktree",
            "submodule", "remote", "origin", "upstream", "fork", "forks", "clone",
            "checkout", "working tree", "source tree", "monorepo",
        ),
    ),
    (
        "branch",
        _lexicon(
            "branch", "branches", "main", "master", "trunk", "default branch",
            "base branch", "feature branch", "head", "ref", "refs",
        ),
    ),
    (
        "repository-write",
        _lexicon(
            "commit", "commits", "committed", "committing", "push", "pushes",
            "pushed", "pushing", "force push", "rewrite history", "revert",
            "reverts", "rebase", "cherry-pick", "cherry pick", "squash", "amend",
            "tag", "tags", "land", "lands", "landed", "landing", "patch",
            "patches", "diff", "diffs", "pull request", "pull-request", "pr",
            "prs", "draft pr", "write to disk", "check in", "check it in",
        ),
    ),
    (
        "review",
        _lexicon(
            "review", "reviews", "reviewed", "reviewing", "reviewer", "reviewers",
            "verdict", "verdicts", "audit", "audits", "audited", "code review",
            "exact-sha", "exact sha", "sha", "peer review",
        ),
    ),
    (
        "acceptance",
        _lexicon(
            "accept", "accepts", "accepted", "acceptance", "approve", "approves",
            "approved", "approval", "approvals", "sign off", "sign-off", "signoff",
            "certify", "certifies", "certified", "certification", "adjudicate",
            "adjudication", "ratify", "endorse", "veto", "bless", "green light",
            "green-light", "greenlight", "go ahead", "go-ahead", "thumbs up",
            "thumbs-up", "rubber stamp", "rubber-stamp", "good to go", "final",
            "finalize", "finalise", "final say", "make the call", "decide",
            "decides", "decided", "decision", "decisions", "determine",
            "determines", "judge", "judges", "judgment", "arbitrate", "sanction",
            "waive", "override", "bypass", "gate", "gates", "gating",
            "call it done", "mark it done", "declare it ready",
        ),
    ),
    (
        "merge",
        _lexicon("merge", "merges", "merged", "merging", "mergeable", "integrate into"),
    ),
    (
        "deployment",
        _lexicon(
            "deploy", "deploys", "deployed", "deploying", "deployment", "release",
            "releases", "released", "rollout", "roll out", "roll it out", "promote",
            "promotion", "production", "prod", "staging", "publish", "publishes",
            "published", "publication", "launch", "launches", "live", "go live",
            "go-live", "cutover", "hotfix", "ship", "ships", "shipped", "shipping",
            "out the door", "flip the switch", "cut a release", "make it official",
            "turn it on",
        ),
    ),
    (
        "external-write",
        _lexicon(
            "post", "posts", "posted", "posting", "email", "emails", "slack",
            "message", "messages", "notify", "notification", "notifications",
            "send", "sends", "sending", "reply", "replies", "comment", "comments",
            "webhook", "upload", "uploads", "tweet", "announce", "broadcast",
            "customer", "customers", "client", "clients", "public", "vendor",
            "stakeholder", "stakeholders", "press",
        ),
    ),
    (
        "billing",
        _lexicon(
            "billing", "invoice", "invoices", "payment", "payments", "purchase",
            "purchases", "buy", "buys", "subscribe", "subscription", "charge",
            "charges", "spend", "spends", "refund", "license", "licence",
            "plan upgrade", "upgrade", "quota", "budget",
        ),
    ),
    (
        "permission",
        _lexicon(
            "credential", "credentials", "secret", "secrets", "token", "tokens",
            "password", "passwords", "api key", "permission", "permissions",
            "scope", "scopes", "access", "admin", "administrator", "root", "sudo",
            "grant", "grants", "granted", "revoke", "revokes", "oauth", "iam",
            "ssh", "role", "roles", "privilege", "privileges",
        ),
    ),
    (
        "contract or methodology",
        _lexicon(
            "contract", "contracts", "methodology", "methodologies",
            "source of truth", "canonical", "governance", "standard of record",
            "define the rules", "set the policy", "policy of record",
        ),
    ),
)

#: A debate turn is text-only advisory input, so the two categories that describe a
#: debate turn itself -- forming a view and voting on it -- are not treated as
#: authority there. Every other category still fails closed.
_DEBATE_EXEMPT_CATEGORIES = frozenset({"review", "acceptance"})


class AntigravityContractError(ValueError):
    """Fail-closed rejection carrying generic category labels only."""

    def __init__(self, labels: list[str]) -> None:
        self.labels = tuple(labels)
        super().__init__("; ".join(labels))


def normalize_text(value: str) -> str:
    """Collapse every whitespace run to one space so no layout survives the hop."""
    return re.sub(r"\s+", " ", value).strip()


def private_context_labels(value: str) -> list[str]:
    """Generic labels for the private context found in ``value``, never the text."""
    labels = [label for label, pattern in _PRIVATE_CONTEXT_BLOCKS if pattern.search(value)]
    normalized = normalize_text(value)
    if normalized and not _ALLOWED_CHARACTERS.match(normalized):
        labels.append("an unsupported character outside the safe text set")
    return labels


def authority_labels(value: str, *, debate: bool = False) -> list[str]:
    normalized = normalize_text(value)
    return [
        f"cannot receive {category} authority"
        for category, pattern in _AUTHORITY_LEXICON
        if not (debate and category in _DEBATE_EXEMPT_CATEGORIES)
        and pattern.search(normalized)
    ]


def _field_labels(value: str, *, field: str, limit: int, debate: bool) -> list[str]:
    normalized = normalize_text(value)
    if not normalized:
        return [f"{field} is empty after normalization"]
    labels: list[str] = []
    if len(normalized) > limit:
        labels.append(f"{field} exceeds the bounded contract length")
    labels.extend(f"{field} contains {label}" for label in private_context_labels(value))
    labels.extend(authority_labels(value, debate=debate))
    return labels


class AntigravityRequest(BaseModel):
    """The only object this process may serialise onto an Antigravity interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["antigravity-subordinate-contract/v1"] = (
        ANTIGRAVITY_CONTRACT_VERSION
    )
    interaction_kind: AntigravityInteractionKind
    capability: AntigravityCapability
    assignment_sequence: int = Field(ge=1, le=20)
    directive: str = Field(min_length=1, max_length=2_000)
    brief: str = Field(min_length=1, max_length=MAX_BRIEF_CHARACTERS)
    checks: list[str] = Field(default_factory=list, max_length=MAX_CHECKS)
    granted_authorities: list[str] = Field(default_factory=list, max_length=0)
    denied_authorities: list[str] = Field(
        default_factory=lambda: list(DENIED_AUTHORITIES)
    )
    withheld_context: list[str] = Field(default_factory=lambda: list(WITHHELD_CONTEXT))
    output_contract: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _validate_closed_contract(self) -> AntigravityRequest:
        labels: list[str] = []
        if self.granted_authorities:
            labels.append("cannot grant any authority")
        if tuple(self.denied_authorities) != DENIED_AUTHORITIES:
            labels.append("must carry the fixed denied-authority block")
        if tuple(self.withheld_context) != WITHHELD_CONTEXT:
            labels.append("must carry the fixed withheld-context block")
        if self.capability not in _CAPABILITIES_FOR_INTERACTION[self.interaction_kind]:
            labels.append("capability does not match the interaction kind")
        debate = self.interaction_kind in _DEBATE_INTERACTIONS
        expected_directive = CAPABILITY_DIRECTIVES[self.capability]
        if self.interaction_kind == AntigravityInteractionKind.SUBORDINATE_WORK_REVISION:
            expected_directive = f"{_REVISION_DIRECTIVE} {expected_directive}"
        if self.directive != expected_directive:
            labels.append("directive is not the Director-generated capability text")
        expected_output = _DEBATE_OUTPUT_CONTRACT if debate else _WORK_OUTPUT_CONTRACT
        if self.output_contract != expected_output:
            labels.append("output contract is not the Director-generated text")
        labels.extend(
            _field_labels(
                self.brief, field="brief", limit=MAX_BRIEF_CHARACTERS, debate=debate
            )
        )
        for check in self.checks:
            labels.extend(
                _field_labels(
                    check,
                    field="acceptance check",
                    limit=MAX_CHECK_CHARACTERS,
                    debate=debate,
                )
            )
        if labels:
            raise AntigravityContractError(sorted(set(labels)))
        return self


def _construct(**fields: object) -> AntigravityRequest:
    """Construct the request without ever letting pydantic echo the input back.

    ``ValidationError`` renders the offending value inline, so a schema failure here
    would put rejected text into the traceback, the Slack status post, and
    ``TaskRecord.error``. The builders validate first, so reaching this handler means
    a programming error rather than bad model output; either way only a label leaves.
    """
    try:
        return AntigravityRequest(**fields)
    except (AntigravityContractError, ValidationError):
        raise AntigravityContractError(
            ["request failed the closed Antigravity contract schema"]
        ) from None


def _structural_labels(assignment: Assignment) -> list[str]:
    """Typed denials that never consult a single word of model-authored prose."""
    labels: list[str] = []
    if assignment.repository:
        labels.append("cannot receive repository context")
    if assignment.starting_ref != "main":
        labels.append("cannot select a starting ref")
    if assignment.allow_draft_pr:
        labels.append("cannot create a draft pull request")
    if assignment.public_research_query is not None:
        labels.append("cannot receive a public research query")
    return labels


def _text_labels(assignment: Assignment, *, debate: bool) -> list[str]:
    labels = _field_labels(
        assignment.task, field="brief", limit=MAX_BRIEF_CHARACTERS, debate=debate
    )
    for check in assignment.acceptance:
        labels.extend(
            _field_labels(
                check,
                field="acceptance check",
                limit=MAX_CHECK_CHARACTERS,
                debate=debate,
            )
        )
    return labels


def build_work_request(
    assignment: Assignment, *, revision: bool = False
) -> AntigravityRequest:
    """Build the closed work request, or fail closed with generic labels."""
    labels = _structural_labels(assignment) + _text_labels(assignment, debate=False)
    capability = CAPABILITY_BY_KIND.get(assignment.kind)
    if capability is None:
        labels.append(
            "requires kind boilerplate, scaffolding, low_level_implementation, "
            "or abstract_ideation"
        )
    if labels:
        raise AntigravityContractError(sorted(set(labels)))

    interaction = (
        AntigravityInteractionKind.SUBORDINATE_WORK_REVISION
        if revision
        else AntigravityInteractionKind.SUBORDINATE_WORK
    )
    directive = CAPABILITY_DIRECTIVES[capability]
    if revision:
        directive = f"{_REVISION_DIRECTIVE} {directive}"
    return _construct(
        interaction_kind=interaction,
        capability=capability,
        assignment_sequence=assignment.sequence,
        directive=directive,
        brief=normalize_text(assignment.task),
        checks=[normalize_text(item) for item in assignment.acceptance],
        output_contract=_WORK_OUTPUT_CONTRACT,
    )


def build_debate_request(assignment: Assignment) -> AntigravityRequest:
    """Build the minimal advisory debate packet, or fail closed.

    The packet deliberately carries no peer positions and no engine context. A
    rebuttal is therefore an independent second look at the same public question
    rather than a channel for another provider's raw output.
    """
    labels = _structural_labels(assignment) + _text_labels(assignment, debate=True)
    interaction = {
        DiscussionPhase.POSITION: AntigravityInteractionKind.ADVISORY_DEBATE_POSITION,
        DiscussionPhase.REBUTTAL: AntigravityInteractionKind.ADVISORY_DEBATE_REBUTTAL,
    }.get(assignment.discussion_phase)
    if interaction is None:
        labels.append("requires a position or rebuttal discussion phase")
    if labels:
        raise AntigravityContractError(sorted(set(labels)))

    return _construct(
        interaction_kind=interaction,
        capability=AntigravityCapability.ADVISORY_DEBATE_OPINION,
        assignment_sequence=assignment.sequence,
        directive=CAPABILITY_DIRECTIVES[AntigravityCapability.ADVISORY_DEBATE_OPINION],
        brief=normalize_text(assignment.task),
        checks=[normalize_text(item) for item in assignment.acceptance],
        output_contract=_DEBATE_OUTPUT_CONTRACT,
    )


def antigravity_request_for(
    assignment: Assignment, *, revision: bool = False
) -> AntigravityRequest:
    """The one entry point every outbound Antigravity call goes through.

    `revision` only distinguishes the work directive. A debate revision re-issues the
    identical advisory packet: the prior turn is already chained by
    `previous_interaction_id`, and the reviewer's revision text is exactly the
    untrusted, potentially private material this contract exists to withhold.
    """
    if assignment.discussion_phase == DiscussionPhase.WORK:
        return build_work_request(assignment, revision=revision)
    return build_debate_request(assignment)


def antigravity_contract_labels(
    assignment: Assignment, *, revision: bool = False
) -> list[str]:
    """Generic rejection labels for one assignment, or ``[]`` when it is admissible."""
    try:
        antigravity_request_for(assignment, revision=revision)
    except AntigravityContractError as exc:
        return list(exc.labels)
    return []
