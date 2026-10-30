from dataclasses import replace
import json
import unittest

from pydantic import ValidationError

from director_orchestrator.config import Settings
from director_orchestrator.models import IntakeAuthentication, SlackIntake, TaskRecord, TaskStatus
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_default import _live_parallel_plan
from director_orchestrator.primary_roster import (
    ALLOWED_COPILOT_ISSUE_KINDS,
    IssueRef,
    PrimaryRosterHold,
    is_valid_issue_ref,
    issue_scope_error,
    select_primary_roster,
)


class CopilotIssueScopeTests(unittest.TestCase):
    ENTERPRISE_IN_BAND = ("openai",) * 4 + ("claude",) * 5

    def select(self, **kw):
        args = dict(
            available=("openai", "claude", "cursor", "github-copilot"),
            allocation_profile="enterprise",
            history=self.ENTERPRISE_IN_BAND,
            trusted_cost_ranks={"openai": 4, "claude": 3, "cursor": 2, "github-copilot": 0},
        )
        args.update(kw)
        return select_primary_roster(**args)

    def test_copilot_refused_without_explicit_issue_scope(self):
        # Even with lowest cost rank (0), Copilot is never selected without explicit issue scope
        roster = self.select()
        self.assertNotEqual(roster.producer, "github-copilot")
        self.assertIn(roster.producer, {"openai", "claude", "cursor"})

    def test_copilot_refused_for_repository_assistance_alone(self):
        # repository_assistance alone does not grant issue scope and cannot select Copilot
        roster = self.select(required_capabilities=("repository_assistance",))
        self.assertNotEqual(roster.producer, "github-copilot")
        self.assertIn(roster.producer, {"openai", "claude", "cursor"})

    def test_copilot_requires_issue_ref_when_issue_scope_requested(self):
        # github_issue_work without issue_ref raises PrimaryRosterHold
        with self.assertRaisesRegex(PrimaryRosterHold, "issue_scope"):
            self.select(required_capabilities=("github_issue_work",))

        # Empty dict or invalid issue_ref raises PrimaryRosterHold
        with self.assertRaisesRegex(PrimaryRosterHold, "issue_scope"):
            self.select(required_capabilities=("github_issue_work",), issue_ref={})

        with self.assertRaisesRegex(PrimaryRosterHold, "issue_scope"):
            self.select(
                required_capabilities=("github_issue_work",),
                issue_ref={"repository": "", "issue_number": 1},
            )

        with self.assertRaisesRegex(PrimaryRosterHold, "issue_scope"):
            self.select(
                required_capabilities=("github_issue_work",),
                issue_ref={"repository": "owner/repo", "issue_number": -1},
            )

        with self.assertRaisesRegex(PrimaryRosterHold, "issue_scope"):
            self.select(
                required_capabilities=("github_issue_work",),
                issue_ref={"repository": "owner/repo", "kind": "unsupported_kind", "issue_number": 1},
            )

    def test_copilot_selected_for_all_allowed_issue_kinds(self):
        for kind in ALLOWED_COPILOT_ISSUE_KINDS:
            with self.subTest(kind=kind):
                # drafting needs a typed packet; every other kind needs the issue number.
                packet = {"title": "Draft"} if kind == "drafting" else None
                issue_ref = IssueRef(repository="owner/repo", issue_number=42, kind=kind, packet=packet)
                roster = self.select(
                    required_capabilities=("github_issue_work",),
                    issue_ref=issue_ref,
                )
                self.assertEqual(roster.producer, "github-copilot")
                self.assertIn(roster.reviewer, {"openai", "claude", "cursor", "grok"})

    def test_copilot_selected_with_dict_issue_ref_and_drafting_packet(self):
        # Dict issue_ref with issue number
        roster = self.select(
            required_capabilities=("github_issue_work",),
            issue_ref={"repository": "owner/repo", "issue_number": 101, "kind": "triage"},
        )
        self.assertEqual(roster.producer, "github-copilot")

        # Dict issue_ref for drafting without issue number
        roster_draft = self.select(
            required_capabilities=("github_issue_work",),
            issue_ref={
                "repository": "owner/repo",
                "kind": "drafting",
                "packet": {"title": "New issue draft", "body": "Details"},
            },
        )
        self.assertEqual(roster_draft.producer, "github-copilot")

    def test_other_lanes_selection_unchanged_versus_baseline(self):
        # Baseline standard selection without issue scope; derived from baseline fe25119 behavior
        standard = self.select(
            available=("openai", "claude", "cursor", "grok"),
            history=(),
            trusted_cost_ranks={"openai": 0, "claude": 2, "cursor": 1, "grok": 3},
        )
        self.assertEqual((standard.producer, standard.reviewer), ("openai", "claude"))

    def test_is_valid_issue_ref_edge_cases(self):
        self.assertFalse(is_valid_issue_ref(None))
        self.assertFalse(is_valid_issue_ref("string_not_mapping"))
        self.assertFalse(is_valid_issue_ref({"repo": "owner/repo"}))
        # kind is required; a bare repository plus number is not a typed scope.
        self.assertFalse(is_valid_issue_ref({"repo": "owner/repo", "issue": 1}))
        self.assertFalse(is_valid_issue_ref(IssueRef(repository="owner/repo", issue_number=1)))
        self.assertTrue(is_valid_issue_ref({"repo": "owner/repo", "issue": 1, "kind": "triage"}))
        self.assertTrue(is_valid_issue_ref(IssueRef(repository="owner/repo", issue_number=1, kind="triage")))
        self.assertTrue(
            is_valid_issue_ref(IssueRef(repository="owner/repo", kind="drafting", packet={"title": "test"}))
        )
        # Explicit None is the same as absent.
        self.assertTrue(is_valid_issue_ref(
            {"repository": "owner/repo", "kind": "drafting", "issue_number": None, "packet": {"title": "t"}}))
        self.assertFalse(is_valid_issue_ref(IssueRef(repository="owner/repo", kind="drafting")))
        self.assertFalse(is_valid_issue_ref(IssueRef(repository="owner/repo", issue_number=True, kind="triage")))

    def test_selection_holds_on_each_invalid_shared_scope(self):
        for name, scope in sorted(INVALID_SCOPES.items()):
            with self.subTest(name=name), self.assertRaisesRegex(PrimaryRosterHold, "primary_issue_scope_required"):
                self.select(required_capabilities=("github_issue_work",), issue_ref=scope)

    def test_selection_admits_copilot_for_each_valid_shared_scope(self):
        for name, scope in sorted(VALID_SCOPES.items()):
            with self.subTest(name=name):
                roster = self.select(required_capabilities=("github_issue_work",), issue_ref=scope)
                self.assertEqual(roster.producer, "github-copilot")

    def test_issue_ref_and_issue_scope_together_hold(self):
        scope = VALID_SCOPES["int_issue_number"]
        with self.assertRaisesRegex(PrimaryRosterHold, "primary_issue_scope_required"):
            self.select(required_capabilities=("github_issue_work",), issue_ref=scope, issue_scope=scope)


# Copied verbatim from the provider-dispatch staging tests/test_copilot_guard.py
# (WO-20261029-004): the roster and dispatch_guard.copilot_issue_scope_error must
# accept and refuse exactly the same scopes.
VALID_REPO = "edwardjones3976/director-orchestrator"
INVALID_SCOPES = {
    "not_a_mapping": ["investigation", VALID_REPO, 42],
    "bool_issue_number": {"kind": "investigation", "repository": VALID_REPO, "issue_number": True},
    "false_issue_number": {"kind": "investigation", "repository": VALID_REPO, "issue_number": False},
    "zero_issue_number": {"kind": "investigation", "repository": VALID_REPO, "issue_number": 0},
    "negative_issue_number": {"kind": "investigation", "repository": VALID_REPO, "issue_number": -5},
    "negative_numeric_string": {"kind": "investigation", "repository": VALID_REPO, "issue_reference": "-5"},
    "zero_string": {"kind": "investigation", "repository": VALID_REPO, "issue_reference": "0"},
    "non_numeric_string": {"kind": "investigation", "repository": VALID_REPO, "issue_reference": "issue-42"},
    "padded_numeric_string": {"kind": "investigation", "repository": VALID_REPO, "issue_reference": " 42 "},
    "unicode_digit_string": {"kind": "investigation", "repository": VALID_REPO, "issue_reference": "²"},
    "float_issue_number": {"kind": "investigation", "repository": VALID_REPO, "issue_number": 42.0},
    "repository_without_owner": {"kind": "investigation", "repository": "director-orchestrator", "issue_number": 42},
    "repository_with_extra_segment": {"kind": "investigation", "repository": "a/b/c", "issue_number": 42},
    "repository_with_space": {"kind": "investigation", "repository": "owner/my repo", "issue_number": 42},
    "repository_trailing_newline": {"kind": "investigation", "repository": VALID_REPO + "\n", "issue_number": 42},
    "repository_not_string": {"kind": "investigation", "repository": ["owner/name"], "issue_number": 42},
    "kind_missing": {"repository": VALID_REPO, "issue_number": 42},
    "kind_unknown": {"kind": "coding", "repository": VALID_REPO, "issue_number": 42},
    "drafting_without_packet": {"kind": "drafting", "repository": VALID_REPO},
    "drafting_with_only_issue_number": {"kind": "drafting", "repository": VALID_REPO, "issue_number": 42},
    "drafting_packet_truthy_string": {"kind": "drafting", "repository": VALID_REPO, "packet": "draft it"},
    "drafting_packet_without_title": {"kind": "drafting", "repository": VALID_REPO, "packet": {"body": "b"}},
    "drafting_packet_empty_title": {"kind": "drafting", "repository": VALID_REPO, "packet": {"title": "  "}},
    "drafting_packet_title_not_string": {"kind": "drafting", "repository": VALID_REPO, "packet": {"title": 7}},
    "drafting_with_invalid_issue_number": {"kind": "drafting", "repository": VALID_REPO, "issue_number": -1,
                                           "packet": {"title": "t"}},
    "ambiguous_issue_aliases": {"kind": "investigation", "repository": VALID_REPO, "issue_number": 42,
                                "issue_reference": "43"},
    "ambiguous_repository_aliases": {"kind": "investigation", "repository": VALID_REPO, "repo": "x/y",
                                     "issue_number": 42},
}
VALID_SCOPES = {
    "int_issue_number": {"kind": "triage", "repository": VALID_REPO, "issue_number": 42},
    "numeric_string": {"kind": "evidence", "repository": VALID_REPO, "issue_reference": "42"},
    "repo_alias": {"kind": "tracking", "repo": "a.b_c-d/e.f_g-h", "issue": 1},
    "drafting_packet": {"kind": "drafting", "repository": VALID_REPO, "packet": {"title": "Draft"}},
    "drafting_packet_alias_with_issue": {"kind": "drafting", "repository": VALID_REPO, "issue_number": 7,
                                         "drafting_packet": {"title": "Draft"}},
}


class SharedIssueScopeParityTests(unittest.TestCase):
    """The same 27 invalid and 5 valid scopes the dispatch guard is tested with."""

    def test_table_size_matches_the_guard_table(self):
        self.assertEqual((27, 5), (len(INVALID_SCOPES), len(VALID_SCOPES)))

    def test_roster_validator_refuses_every_invalid_scope(self):
        for name, scope in sorted(INVALID_SCOPES.items()):
            with self.subTest(name=name):
                self.assertIsNotNone(issue_scope_error(scope))
                self.assertFalse(is_valid_issue_ref(scope))

    def test_roster_validator_accepts_every_valid_scope(self):
        for name, scope in sorted(VALID_SCOPES.items()):
            with self.subTest(name=name):
                self.assertIsNone(issue_scope_error(scope))
                self.assertTrue(is_valid_issue_ref(scope))


class IntakeIssueScopeTests(unittest.TestCase):
    """Production intake carries a typed issue scope through to roster selection."""

    COST_RANKS = '{"openai":5,"claude":4,"cursor":3,"grok":6,"github-copilot":1}'
    AVAILABLE = ("openai", "claude", "cursor", "github-copilot")

    def _record(self, settings, **intake_fields):
        intake = SlackIntake.model_validate_json(json.dumps({
            "source_event_id": "issue-scope-event",
            "channel_id": "C0BAV9CMC80",
            "user_id": "U123",
            "text": "Implement the bounded change",
            "thread_ts": "1787450001.123456",
            "allocation_profile": "enterprise",
            **intake_fields,
        }))
        record = TaskRecord(task_id="DIR-LG-ISSUESCOPE", status=TaskStatus.QUEUED, intake=intake,
                            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        record.plan = preview_plan(intake.text, settings)
        return record

    def _settings(self):
        return replace(Settings(), repository_allowlist=("owner/repository",),
                       trusted_provider_cost_ranks_json=self.COST_RANKS)

    def _plan(self, record, settings):
        _live_parallel_plan(record, settings, available=self.AVAILABLE,
                            history=CopilotIssueScopeTests.ENTERPRISE_IN_BAND)
        return record.plan.assignments[0].primary_provider_lane

    def test_intake_with_valid_issue_scope_selects_copilot(self):
        settings = self._settings()
        for name, scope in sorted(VALID_SCOPES.items()):
            with self.subTest(name=name):
                record = self._record(settings, primary_required_capabilities=["github_issue_work"],
                                      primary_issue_ref=scope)
                self.assertEqual(scope, record.intake.primary_issue_ref)
                self.assertEqual("github-copilot", self._plan(record, settings))

    def test_intake_without_issue_scope_does_not_select_copilot(self):
        settings = self._settings()
        self.assertNotEqual("github-copilot", self._plan(self._record(settings), settings))
        record = self._record(settings, primary_required_capabilities=["repository_assistance"])
        self.assertNotEqual("github-copilot", self._plan(record, settings))
        # The capability without a scope holds before dispatch rather than selecting Copilot.
        record = self._record(settings, primary_required_capabilities=["github_issue_work"])
        with self.assertRaisesRegex(PrimaryRosterHold, "primary_issue_scope_required"):
            self._plan(record, settings)

    def test_intake_refuses_each_invalid_issue_scope(self):
        settings = self._settings()
        for name, scope in sorted(INVALID_SCOPES.items()):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self._record(settings, primary_required_capabilities=["github_issue_work"],
                             primary_issue_ref=scope)

    def test_intake_refuses_issue_scope_without_the_capability(self):
        with self.assertRaisesRegex(ValidationError, "requires the github_issue_work capability"):
            self._record(self._settings(), primary_issue_ref=VALID_SCOPES["int_issue_number"])

    def test_intake_without_issue_scope_keeps_its_serialized_bytes(self):
        dumped = self._record(self._settings()).intake.model_dump()
        self.assertNotIn("primary_issue_ref", dumped)
