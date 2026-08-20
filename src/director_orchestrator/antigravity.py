"""Closed outbound contract for the Gemini Antigravity subordinate lane.

The boundary is composition, not inspection.

An earlier version of this module validated model-authored prose with a category
blocklist and a character allowlist and then forwarded whatever survived. That is
unsound: a sentence can be syntactically ordinary and still be a confidential
project fact. "Blue Cedar acquisition valuation is 42 million dollars" contains no
path, no identifier, no secret pattern, and no operational keyword, yet disclosing
it is exactly the harm the lane exists to prevent. No filter can prove arbitrary
prose is public, so no filter is used for that purpose here.

Instead **nothing model-authored is transmitted at all**. An outbound request is
assembled only from:

* fixed strings that live in this file,
* values of closed enums declared in this file or in `models.py`,
* integers inside declared bounds.

`Assignment.task`, `Assignment.acceptance`, the owner objective, dependency
results, Slack text, and repository context never reach the wire in any phase.
The Director's influence is limited to selecting a typed fixture lane, a language,
and two bounded counts -- roughly fifteen bits drawn from a closed vocabulary,
which cannot carry a sentence.

`_assert_closed_vocabulary` enforces this mechanically rather than by review: it
walks the fully serialised payload and rejects any string that is not a member of
`APPROVED_STRINGS`, a registry computed at import time from the constant tables
below. A string that is not literally present in this repository cannot be sent.

Authority remains typed exactly as before: one capability from a closed table keyed
by `AssignmentKind`, an always-empty granted-authority list, and the fixed
`DENIED_AUTHORITIES` block. Repository, starting ref, draft PR, and public research
query are refused as fields.

Debate is not available to Antigravity. A useful position or rebuttal requires the
question, the options, and the peer arguments, all of which are model-authored
prose about private work. A fixed generic prompt with no subject would produce an
opinion about nothing that the runtime would then weight as a vote, which is worse
than not voting. `build_debate_request` therefore always fails closed, and
`execution_policy_errors` refuses to admit a plan that seats Antigravity in a
debate. Its configured advisory weight is simply never cast, exactly like an
unavailable member, so the fixed committee denominator is untouched.

Rejection raises `AntigravityContractError` carrying only generic labels drawn from
this file, so the rejected assignment's text cannot reach a log line, a Slack status
post, `TaskRecord.error`, or `ProviderContactAttempt.error`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import (
    AntigravityFixtureLane,
    AntigravityLanguage,
    Assignment,
    AssignmentKind,
    DiscussionPhase,
)


ANTIGRAVITY_CONTRACT_VERSION = "antigravity-subordinate-contract/v2"

NON_AUTHORITATIVE_NOTICE = (
    "NON-AUTHORITATIVE ANTIGRAVITY ARTIFACT: This output is subordinate input. "
    "Claude or Cursor must independently verify and integrate or reject it before use."
)


class AntigravityCapability(StrEnum):
    """The complete set of things Antigravity may ever be asked to do."""

    BOILERPLATE_GENERATION = "boilerplate_generation"
    SCAFFOLDING = "scaffolding"
    LOW_LEVEL_MECHANICAL_IMPLEMENTATION = "low_level_mechanical_implementation"
    ALTERNATE_IDEATION = "alternate_ideation"


class AntigravityInteractionKind(StrEnum):
    SUBORDINATE_WORK = "subordinate_work"
    SUBORDINATE_WORK_REVISION = "subordinate_work_revision"


#: Fixed and total. Every request denies all of these regardless of its capability.
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

#: Stated on the wire so the receiving agent cannot mistake absence for oversight,
#: and so it does not ask for any of it.
WITHHELD_CONTEXT: tuple[str, ...] = (
    "acceptance_criteria",
    "assignment_task_text",
    "dependency_outcomes",
    "owner_objective",
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

#: Which lanes each capability may draw from. A lane that does not fit the typed
#: kind fails closed rather than quietly widening what the capability means.
LANES_FOR_CAPABILITY: dict[AntigravityCapability, frozenset[AntigravityFixtureLane]] = {
    AntigravityCapability.BOILERPLATE_GENERATION: frozenset(
        {
            AntigravityFixtureLane.UNIT_TEST_FIXTURE_SET,
            AntigravityFixtureLane.DATA_RECORD_SAMPLES,
            AntigravityFixtureLane.CONFIG_TEMPLATE,
        }
    ),
    AntigravityCapability.SCAFFOLDING: frozenset(
        {
            AntigravityFixtureLane.MODULE_SKELETON,
            AntigravityFixtureLane.STATE_MACHINE_SKELETON,
            AntigravityFixtureLane.CONFIG_TEMPLATE,
        }
    ),
    AntigravityCapability.LOW_LEVEL_MECHANICAL_IMPLEMENTATION: frozenset(
        {
            AntigravityFixtureLane.VALIDATION_RULE_STUBS,
            AntigravityFixtureLane.PAGINATION_HELPERS,
            AntigravityFixtureLane.RETRY_BACKOFF_VARIANTS,
        }
    ),
    AntigravityCapability.ALTERNATE_IDEATION: frozenset(
        {
            AntigravityFixtureLane.RETRY_BACKOFF_VARIANTS,
            AntigravityFixtureLane.ERROR_TAXONOMY_OPTIONS,
            AntigravityFixtureLane.NAMING_ALTERNATIVES,
        }
    ),
}

#: The work statement itself. Repository-owned, generic, and public by inspection:
#: each one is meaningful without any caller context, which is what makes the lane
#: system a replacement for prose rather than a euphemism for it.
LANE_SPECIFICATIONS: dict[AntigravityFixtureLane, str] = {
    AntigravityFixtureLane.UNIT_TEST_FIXTURE_SET: (
        "Produce a self-contained set of unit-test fixtures for a small generic "
        "component of your own invention. Cover the empty case, one typical case, "
        "one boundary case, and one malformed case."
    ),
    AntigravityFixtureLane.DATA_RECORD_SAMPLES: (
        "Produce sample data records for a small generic schema of your own "
        "invention, using obviously synthetic values. Include one record per "
        "distinct shape you introduce."
    ),
    AntigravityFixtureLane.MODULE_SKELETON: (
        "Produce an empty module skeleton for a small generic component of your own "
        "invention: entry points, type signatures, and docstrings, with every body "
        "left unimplemented."
    ),
    AntigravityFixtureLane.CONFIG_TEMPLATE: (
        "Produce a commented configuration template for a small generic service of "
        "your own invention. Use placeholder values only and never a real endpoint, "
        "account, or credential."
    ),
    AntigravityFixtureLane.VALIDATION_RULE_STUBS: (
        "Produce validation-rule stubs for a small generic input schema of your own "
        "invention: one stub per rule, each with its failure message."
    ),
    AntigravityFixtureLane.STATE_MACHINE_SKELETON: (
        "Produce a state-machine skeleton for a small generic workflow of your own "
        "invention: the state enum, the legal transition table, and unimplemented "
        "transition handlers."
    ),
    AntigravityFixtureLane.PAGINATION_HELPERS: (
        "Produce generic pagination helpers: cursor encoding and decoding, page-size "
        "clamping, and last-page detection, with no I/O and no external calls."
    ),
    AntigravityFixtureLane.RETRY_BACKOFF_VARIANTS: (
        "Produce distinct retry-backoff strategies as pure functions from attempt "
        "number to delay. State the trade-offs of each. Perform no I/O."
    ),
    AntigravityFixtureLane.ERROR_TAXONOMY_OPTIONS: (
        "Propose distinct ways to organise an error taxonomy for a generic "
        "long-running job runner. State the trade-offs of each. Produce no code that "
        "touches a real system."
    ),
    AntigravityFixtureLane.NAMING_ALTERNATIVES: (
        "Propose distinct naming schemes for a generic set of related concepts of "
        "your own invention, and state what each scheme optimises for."
    ),
}

_COMMON_DIRECTIVE = (
    "You are a subordinate, non-authoritative contributor. Work only from the "
    "specification below, which is complete: there is no caller context, and you "
    "must not ask for any, infer any, or invent a caller. Do not request or act on "
    "repository access, branches, commits, pull requests, reviews, acceptance, "
    "merges, deployments, external messages, spending, or permissions."
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
}

_REVISION_DIRECTIVE = (
    "Revise your previous artifact only to satisfy the same specification below. Do "
    "not widen the scope and do not issue any verdict."
)

_OUTPUT_CONTRACT = (
    "Return the artifact plus a short evidence note. Separate anything you actually "
    "ran from anything you only reasoned about. A primary-team model adapts your "
    "output to its real setting and independently reproduces it before any part of "
    "it is used, so a generic, self-contained answer is the correct answer."
)

#: Why the debate lane is closed. Sent nowhere; surfaced in plan-admission errors.
DEBATE_UNAVAILABLE_LABEL = (
    "cannot participate in a debate: a position or rebuttal cannot be formed "
    "without the question, options, and peer arguments, and none of those may be "
    "transmitted"
)


def _approved_strings() -> frozenset[str]:
    """Every string this process is permitted to place on the Antigravity wire.

    Computed from the constant tables above, so adding a field that carries
    model-authored text makes `_assert_closed_vocabulary` fail rather than silently
    widening the boundary.
    """
    approved: set[str] = {
        ANTIGRAVITY_CONTRACT_VERSION,
        NON_AUTHORITATIVE_NOTICE,
        _OUTPUT_CONTRACT,
    }
    approved.update(DENIED_AUTHORITIES)
    approved.update(WITHHELD_CONTEXT)
    approved.update(CAPABILITY_DIRECTIVES.values())
    approved.update(
        f"{_REVISION_DIRECTIVE} {directive}"
        for directive in CAPABILITY_DIRECTIVES.values()
    )
    approved.update(LANE_SPECIFICATIONS.values())
    approved.update(item.value for item in AntigravityCapability)
    approved.update(item.value for item in AntigravityInteractionKind)
    approved.update(item.value for item in AntigravityFixtureLane)
    approved.update(item.value for item in AntigravityLanguage)
    return frozenset(approved)


APPROVED_STRINGS: frozenset[str] = _approved_strings()

#: Field name plus inclusive bounds for every integer permitted on the wire.
APPROVED_INTEGERS: dict[str, tuple[int, int]] = {
    "assignment_sequence": (1, 20),
    "variant_count": (1, 10),
    "max_lines_per_artifact": (10, 400),
}


class AntigravityContractError(ValueError):
    """Fail-closed rejection carrying generic labels only."""

    def __init__(self, labels: list[str]) -> None:
        self.labels = tuple(labels)
        super().__init__("; ".join(labels))


def _assert_closed_vocabulary(payload: object, *, field: str = "") -> None:
    """Reject any leaf that is not a repository constant or a bounded integer.

    This is the mechanical form of the whole design. A confidential sentence is not
    in `APPROVED_STRINGS`, so it cannot reach the wire no matter which field a
    future change routes it through.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(key, str):
                raise AntigravityContractError(["contract key is not a fixed name"])
            _assert_closed_vocabulary(value, field=key)
        return
    if isinstance(payload, list):
        for item in payload:
            _assert_closed_vocabulary(item, field=field)
        return
    if isinstance(payload, bool):
        return
    if isinstance(payload, int):
        bounds = APPROVED_INTEGERS.get(field)
        if bounds is None or not bounds[0] <= payload <= bounds[1]:
            raise AntigravityContractError(
                ["contract carries an integer outside its declared bounds"]
            )
        return
    if isinstance(payload, str):
        if payload not in APPROVED_STRINGS:
            raise AntigravityContractError(
                ["contract carries text that is not a Director-owned constant"]
            )
        return
    raise AntigravityContractError(["contract carries an unsupported value type"])


class AntigravityRequest(BaseModel):
    """The only object this process may serialise onto an Antigravity interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["antigravity-subordinate-contract/v2"] = (
        ANTIGRAVITY_CONTRACT_VERSION
    )
    notice: Literal[NON_AUTHORITATIVE_NOTICE] = NON_AUTHORITATIVE_NOTICE
    interaction_kind: AntigravityInteractionKind
    capability: AntigravityCapability
    lane: AntigravityFixtureLane
    language: AntigravityLanguage
    assignment_sequence: int = Field(ge=1, le=20)
    variant_count: int = Field(ge=1, le=10)
    max_lines_per_artifact: int = Field(ge=10, le=400)
    directive: str
    specification: str
    granted_authorities: list[str] = Field(default_factory=list, max_length=0)
    denied_authorities: list[str] = Field(
        default_factory=lambda: list(DENIED_AUTHORITIES)
    )
    withheld_context: list[str] = Field(default_factory=lambda: list(WITHHELD_CONTEXT))
    output_contract: str = _OUTPUT_CONTRACT

    @model_validator(mode="after")
    def _validate_closed_contract(self) -> AntigravityRequest:
        labels: list[str] = []
        if self.granted_authorities:
            labels.append("cannot grant any authority")
        if tuple(self.denied_authorities) != DENIED_AUTHORITIES:
            labels.append("must carry the fixed denied-authority block")
        if tuple(self.withheld_context) != WITHHELD_CONTEXT:
            labels.append("must carry the fixed withheld-context block")
        if self.lane not in LANES_FOR_CAPABILITY[self.capability]:
            labels.append("fixture lane does not match the typed capability")
        expected_directive = CAPABILITY_DIRECTIVES[self.capability]
        if self.interaction_kind == AntigravityInteractionKind.SUBORDINATE_WORK_REVISION:
            expected_directive = f"{_REVISION_DIRECTIVE} {expected_directive}"
        if self.directive != expected_directive:
            labels.append("directive is not the Director-owned capability text")
        if self.specification != LANE_SPECIFICATIONS[self.lane]:
            labels.append("specification is not the Director-owned lane text")
        if self.output_contract != _OUTPUT_CONTRACT:
            labels.append("output contract is not the Director-owned text")
        if labels:
            raise AntigravityContractError(sorted(set(labels)))
        _assert_closed_vocabulary(self.model_dump(mode="json"))
        return self


def _construct(**fields: object) -> AntigravityRequest:
    """Construct without ever letting pydantic echo the input back.

    `ValidationError` renders the offending value inline, so a schema failure here
    would put the rejected value into the traceback, the Slack status post, and
    `TaskRecord.error`. The builders validate first, so reaching this handler means
    a programming error rather than bad model output; either way only a label
    leaves.
    """
    try:
        return AntigravityRequest(**fields)
    except (AntigravityContractError, ValidationError):
        raise AntigravityContractError(
            ["request failed the closed Antigravity contract schema"]
        ) from None


def _structural_labels(assignment: Assignment) -> list[str]:
    """Typed denials. Not one of these consults a word of model-authored prose."""
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


def build_work_request(
    assignment: Assignment, *, revision: bool = False
) -> AntigravityRequest:
    """Build the closed work request, or fail closed with generic labels."""
    labels = _structural_labels(assignment)
    capability = CAPABILITY_BY_KIND.get(assignment.kind)
    if capability is None:
        labels.append(
            "requires kind boilerplate, scaffolding, low_level_implementation, "
            "or abstract_ideation"
        )
    directive = assignment.antigravity_directive
    if directive is None:
        labels.append(
            "requires a typed antigravity_directive selecting a fixture lane, "
            "because no assignment prose is transmitted"
        )
    elif capability is not None and directive.lane not in LANES_FOR_CAPABILITY[capability]:
        labels.append("fixture lane does not match the typed capability")
    if labels:
        raise AntigravityContractError(sorted(set(labels)))
    assert capability is not None and directive is not None  # narrowed above

    interaction = (
        AntigravityInteractionKind.SUBORDINATE_WORK_REVISION
        if revision
        else AntigravityInteractionKind.SUBORDINATE_WORK
    )
    capability_directive = CAPABILITY_DIRECTIVES[capability]
    if revision:
        capability_directive = f"{_REVISION_DIRECTIVE} {capability_directive}"
    return _construct(
        interaction_kind=interaction,
        capability=capability,
        lane=directive.lane,
        language=directive.language,
        assignment_sequence=assignment.sequence,
        variant_count=directive.variant_count,
        max_lines_per_artifact=directive.max_lines_per_artifact,
        directive=capability_directive,
        specification=LANE_SPECIFICATIONS[directive.lane],
    )


def build_debate_request(assignment: Assignment) -> AntigravityRequest:
    """Always fails closed. Antigravity holds no debate turn.

    Kept as a named function so the refusal is a deliberate, testable decision
    rather than an accidental gap: any caller reaching for a debate packet gets the
    reason instead of a payload.
    """
    raise AntigravityContractError([DEBATE_UNAVAILABLE_LABEL])


def antigravity_request_for(
    assignment: Assignment, *, revision: bool = False
) -> AntigravityRequest:
    """The one entry point every outbound Antigravity call goes through."""
    if assignment.discussion_phase == DiscussionPhase.WORK:
        return build_work_request(assignment, revision=revision)
    return build_debate_request(assignment)


def antigravity_contract_labels(
    assignment: Assignment, *, revision: bool = False
) -> list[str]:
    """Generic rejection labels for one assignment, or `[]` when it is admissible."""
    try:
        antigravity_request_for(assignment, revision=revision)
    except AntigravityContractError as exc:
        return list(exc.labels)
    return []


def antigravity_wire_input(
    assignment: Assignment, *, revision: bool = False
) -> str:
    """The exact `input` string for an Antigravity interaction.

    Pure JSON of the closed contract. Nothing is concatenated around it, so the
    whole outbound string is covered by `_assert_closed_vocabulary`.
    """
    request = antigravity_request_for(assignment, revision=revision)
    return json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
