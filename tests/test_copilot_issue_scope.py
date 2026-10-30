import unittest

from director_orchestrator.primary_roster import (
    ALLOWED_COPILOT_ISSUE_KINDS,
    IssueRef,
    PrimaryRosterHold,
    is_valid_issue_ref,
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
                issue_ref = IssueRef(repository="owner/repo", issue_number=42, kind=kind)
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
        self.assertTrue(is_valid_issue_ref({"repo": "owner/repo", "issue": 1}))
        self.assertTrue(is_valid_issue_ref(IssueRef(repository="owner/repo", issue_number=1)))
        self.assertTrue(
            is_valid_issue_ref(IssueRef(repository="owner/repo", kind="drafting", packet={"title": "test"}))
        )
