# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | BEGIN]]
"""Unit tests for the M2-G1 exemplar harness. No provider is contacted.

    python projects/homemade-llm/eval/test_generate_rows.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_rows  # noqa: E402
import validate_rows  # noqa: E402
from test_validate_rows import UTC, write_sets  # noqa: E402

TARGET = {
    "non_goals": ["do not change the Slack app manifest or add a second signing key"],
    "constraints": ["compare every signature with a constant time function and log no secret bytes"],
    "acceptance_checks": ["callbacks signed with either secret verify during the overlap hour"],
    "evidence_requirements": ["unit test output for both secrets and the rotation timestamp"],
}

ASSIGNMENT_EXCERPT = "Both secrets are accepted for one hour after rotation."
ASSIGNMENT_OBJECTIVE = "Rotate the webhook signing secret for the Slack gateway without dropping in-flight callbacks."
ASSIGNMENT_TITLE = "Webhook secret rotation with overlap window"
ASSIGNMENT_BODY = f"# {ASSIGNMENT_TITLE}\n\n{ASSIGNMENT_OBJECTIVE}\n\n{ASSIGNMENT_EXCERPT}\n"
ISSUE_CREATED_AT = "2026-09-27T10:00:00Z"
FIRST_COMMIT_AT = "2026-09-28T12:00:00Z"


def install_pre_work_assignment(root: Path) -> tuple[str, str, dict]:
    """Write assignment bytes; GitHub chronology is mocked in this offline test."""
    assignment_path = root / "assignments" / "wo-77.md"
    assignment_path.parent.mkdir(parents=True, exist_ok=True)
    assignment_path.write_bytes(ASSIGNMENT_BODY.encode("utf-8"))
    source_ref = "assignments/wo-77.md"
    source_sha256 = hashlib.sha256(assignment_path.read_bytes()).hexdigest()
    provenance = {"github_issue": 77, "first_work_pr": 88}
    return source_ref, source_sha256, provenance


def scenario(group: str = "github/edwardjones3976/director-orchestrator/issues/77", root: Path | None = None, **overrides) -> dict:
    if root is None:
        raise ValueError("scenario fixtures require a dataset root with assignment sources")
    source_ref, source_sha256, provenance = install_pre_work_assignment(root)
    row = {
        "source_group": group,
        "split": "train",
        "source_provenance": provenance,
        "input": {
            "objective": ASSIGNMENT_OBJECTIVE,
            "repository": "edwardjones3976/director-orchestrator",
            "title": ASSIGNMENT_TITLE,
            "context": [{
                "kind": "assignment_packet",
                "text": ASSIGNMENT_EXCERPT,
                "source_ref": source_ref,
                "source_sha256": source_sha256,
            }],
        },
    }
    row.update(overrides)
    return row


class GenerateRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.calls: list[str] = []
        self.github_record = {
            "issue": {
                "createdAt": ISSUE_CREATED_AT, "lastEditedAt": None, "title": ASSIGNMENT_TITLE,
                "userContentEdits": {"totalCount": 0},
                "body": ASSIGNMENT_BODY,
                "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [
                    {"source": {"__typename": "PullRequest", "number": 66,
                                "body": "Discussion of #77; not a work claim", "createdAt": "2026-09-26T12:00:00Z",
                                "commits": {"pageInfo": {"hasNextPage": False}, "nodes": [
                                    {"commit": {"committedDate": "2026-09-26T11:00:00Z"}},
                                ]}}},
                    {"source": {"__typename": "PullRequest", "number": 88,
                                "body": "Issue: #77\n", "createdAt": "2026-09-28T12:01:00Z",
                                "commits": {"pageInfo": {"hasNextPage": False}, "nodes": [
                                    {"commit": {"committedDate": "2026-09-28T12:30:00Z"}},
                                    {"commit": {"committedDate": FIRST_COMMIT_AT}},
                                ]}}},
                ]},
            },
        }
        for event in self.github_record["issue"]["timelineItems"]["nodes"]:
            pr = event["source"]
            pr.update(repository={"nameWithOwner": "edwardjones3976/director-orchestrator"},
                      lastEditedAt=None, userContentEdits={"totalCount": 0},
                      closingIssuesReferences={"pageInfo": {"hasNextPage": False}, "nodes": []})
            for node in pr["commits"]["nodes"]:
                node["commit"]["authoredDate"] = node["commit"]["committedDate"]
        mocked = patch.object(generate_rows, "github_chronology", return_value=self.github_record)
        mocked.start()
        self.addCleanup(mocked.stop)

    def write_scenarios(self, rows: list[dict]) -> Path:
        path = self.root / "scenarios.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def receipt(self) -> Path:
        path = self.root / "receipts" / "anthropic" / "20260929T053200Z.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": "claude-sonnet-5"}), encoding="utf-8")
        return path

    def dispatch(self, text: str | None = None):
        receipt = self.receipt()

        def _call(provider: str, packet: str, purpose: str) -> generate_rows.DispatchResult:
            self.calls.append(provider)
            self.assertIn("objective", packet)
            self.assertTrue(purpose.startswith("homemade-llm M2 M2-G1: exemplar "))
            return generate_rows.DispatchResult(text or json.dumps(TARGET), receipt, "claude-sonnet-5")

        return _call

    def generate(self, rows: list[dict], **kwargs):
        caller = kwargs.pop("dispatch", None) or self.dispatch()
        return generate_rows.generate(
            generate_rows.load_scenarios(self.write_scenarios(rows), kwargs.pop("max_rows", 1)),
            provider=kwargs.pop("provider", "anthropic_api"),
            set_a=self.set_a, set_b=self.set_b, out=self.out,
            terms_verified_at=UTC, generated_at=UTC,
            dry_run=kwargs.pop("dry_run", False),
            dispatch=caller,
            scrub=kwargs.pop("scrub", lambda folder: "reports/scan-dataset-test.json"),
            source_root=self.root,
            **kwargs,
        )

    def default_scenario(self, **overrides) -> dict:
        return scenario(root=self.root, **overrides)

    def test_dry_run_writes_nothing_and_calls_no_provider(self):
        report = self.generate([self.default_scenario()], dry_run=True, dispatch=self.dispatch())
        self.assertEqual(report, {"dry_run": True, "rows": 0, "receipts": 0})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_an_excluded_provider_is_refused_before_a_call(self):
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()], provider="xai")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_a_clean_call_writes_one_schema_valid_row(self):
        report = self.generate([self.default_scenario()])
        self.assertEqual(report, {"dry_run": False, "rows": 1, "receipts": 1})
        row = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(row["source_provider"], "anthropic_api")
        self.assertEqual(row["source_model"], "claude-sonnet-5")
        self.assertEqual(row["receipt_ref"], "receipts/anthropic/20260929T053200Z.json")
        self.assertEqual(row["row_id"], validate_rows.canonical_row_id(row["input"], row["target"]))
        self.assertEqual(row["scrub"]["scan_report_ref"], "reports/scan-dataset-test.json")
        self.assertEqual(self.calls, ["anthropic_api"])

    def test_a_leak_writes_no_file(self):
        leaked = self.default_scenario()
        leaked["input"]["title"] = "Mirror task migration to the new G drive layout"
        with self.assertRaises(SystemExit):
            self.generate([leaked])
        self.assertFalse(self.out.exists())

    def test_a_bad_answer_writes_no_file(self):
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()], dispatch=self.dispatch(json.dumps({"non_goals": ["only one field"]})))
        self.assertFalse(self.out.exists())

    def test_more_than_eight_scenarios_are_refused_before_a_call(self):
        rows = [self.default_scenario(group=f"seed-{index:03d}") for index in range(9)]
        with self.assertRaises(SystemExit):
            generate_rows.load_scenarios(self.write_scenarios(rows), generate_rows.MAX_ROWS_PER_CALL)
        self.assertEqual(self.calls, [])

    def test_a_markdown_receipt_supplies_its_tagged_model(self):
        path = self.root / "receipts" / "codex" / "codex-1.out.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[[OpenAI Codex | GPT-6 | 2026-09-28 | 03:16 UTC | BEGIN]]\n\n{\"ok\": true}\n",
            encoding="utf-8",
        )
        self.assertEqual(generate_rows.model_from_receipt(path), "GPT-6")

    def test_a_markdown_receipt_without_a_model_tag_is_refused(self):
        path = self.root / "receipts" / "codex" / "codex-bare.out.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("final message without a model label\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            generate_rows.model_from_receipt(path)

    def test_a_secret_marker_is_refused_before_a_call(self):
        marked = self.default_scenario()
        marked["input"]["objective"] = "Store the value as API_KEY=hidden"
        with self.assertRaises(SystemExit):
            generate_rows.load_scenarios(self.write_scenarios([marked]), 1)
        self.assertEqual(self.calls, [])

    def test_forged_source_hash_is_refused_before_provider_call(self):
        bad = self.default_scenario()
        bad["input"]["context"][0]["source_sha256"] = "0" * 64
        with self.assertRaises(SystemExit):
            self.generate([bad])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_pr_body_source_is_refused_before_provider_call(self):
        raw = self.root / "raw" / "pr-bodies.jsonl"
        raw.parent.mkdir(parents=True, exist_ok=True)
        body = "Context taken from a completed pull request body.\n"
        raw.write_bytes(body.encode("utf-8"))
        row = self.default_scenario()
        row["input"]["context"] = [{
            "kind": "assignment_packet",
            "text": body.strip(),
            "source_ref": "raw/pr-bodies.jsonl",
            "source_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        }]
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])

    def test_pulls_pointer_is_refused_before_provider_call(self):
        pulls = self.root / "pulls" / "360" / "body.md"
        pulls.parent.mkdir(parents=True, exist_ok=True)
        pulls.write_text("# PR body stand-in\n\nDo not treat this as pre-work assignment.\n", encoding="utf-8")
        row = self.default_scenario()
        digest = hashlib.sha256(pulls.read_bytes()).hexdigest()
        row["input"]["context"] = [{
            "kind": "assignment_packet",
            "text": "Do not treat this as pre-work assignment.",
            "source_ref": "pulls/360/body.md",
            "source_sha256": digest,
        }]
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])

    def test_missing_chronology_is_refused_before_provider_call(self):
        row = self.default_scenario()
        row.pop("source_provenance")
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])

    def test_locally_forged_earlier_timestamps_cannot_override_github(self):
        row = self.default_scenario()
        row["source_provenance"]["assignment_captured_at"] = "2026-09-26T01:00:00Z"
        row["source_provenance"]["first_work_at"] = "2026-09-28T12:00:00Z"
        self.github_record["issue"]["createdAt"] = "2026-09-29T20:00:00Z"
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])

    def test_edited_issue_body_is_refused_before_provider_call(self):
        self.github_record["issue"]["lastEditedAt"] = "2026-09-27T11:00:00Z"
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_earlier_linked_work_claim_is_refused(self):
        self.github_record["issue"]["timelineItems"]["nodes"].append({"source": {
            "__typename": "PullRequest", "number": 55, "body": "Issue: #77\n",
            "repository": {"nameWithOwner": "edwardjones3976/director-orchestrator"},
            "lastEditedAt": None, "userContentEdits": {"totalCount": 0},
            "closingIssuesReferences": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "createdAt": "2026-09-27T12:00:00Z",
            "commits": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"commit": {"committedDate": "2026-09-27T11:00:00Z"}},
            ]},
        }})
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_unordered_earlier_commit_is_refused(self):
        work_pr = self.github_record["issue"]["timelineItems"]["nodes"][1]["source"]
        work_pr["commits"]["nodes"].append({"commit": {
            "committedDate": "2026-09-26T11:00:00Z",
        }})
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_incomplete_cross_references_are_refused(self):
        self.github_record["issue"]["timelineItems"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_rebased_or_missing_author_date_is_refused_before_provider_call(self):
        commit = self.github_record["issue"]["timelineItems"]["nodes"][1]["source"]["commits"]["nodes"][0]["commit"]
        for authored in ("2026-09-26T11:00:00Z", None):
            with self.subTest(authored=authored):
                commit["authoredDate"] = authored
                with self.assertRaises(SystemExit):
                    self.generate([self.default_scenario()])
                self.assertEqual(self.calls, [])
                self.assertFalse(self.out.exists())

    def test_cross_set_reference_id_collision_is_refused_before_provider_call(self):
        # Replacing Set A's target with Set B's different target must not hide a leak.
        original = json.loads((self.set_a / "ew-alpha.json").read_text(encoding="utf-8"))
        replacement = json.loads((self.set_b / "pr-beta.json").read_text(encoding="utf-8"))
        (self.set_b / "ew-alpha.json").write_text(json.dumps(replacement), encoding="utf-8")
        row = self.default_scenario()
        leaked = original["constraints"][0]
        row["input"]["context"][0]["text"] = leaked
        source = self.root / row["input"]["context"][0]["source_ref"]
        source.write_bytes((self.github_record["issue"]["body"] + leaked).encode("utf-8"))
        row["input"]["context"][0]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.github_record["issue"]["body"] = source.read_text(encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_same_assignment_cannot_use_aliases_for_train_and_dev(self):
        rows = [self.default_scenario(), self.default_scenario(group="dev-alias", split="dev")]
        caller = self.dispatch()
        answers = iter([json.dumps(TARGET), json.dumps(dict(TARGET,
            evidence_requirements=["retain the signature rotation logs and both verification test transcripts"]))])

        def distinct_targets(*args):
            result = caller(*args)
            result.text = next(answers)
            return result

        with self.assertRaises(SystemExit):
            self.generate(rows, max_rows=2, dispatch=distinct_targets)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_canonical_group_straddling_is_refused_before_provider_call(self):
        rows = [self.default_scenario(), self.default_scenario(split="dev")]
        with self.assertRaises(SystemExit):
            self.generate(rows, max_rows=2)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_held_out_input_title_objective_and_near_duplicate_are_refused_before_call(self):
        held = generate_rows.validate_rows.HeldOutSet("a", self.set_a)
        objective = held.references["ew-alpha"]["objective"]
        for field, value in (("title", held.titles["ew-alpha"]), ("objective", objective),
                             ("objective", objective.rstrip(".") + " safely.")):
            with self.subTest(field=field, value=value):
                row = self.default_scenario()
                row["input"][field] = value
                body = f"# {row['input']['title']}\n\n{row['input']['objective']}\n\n{ASSIGNMENT_EXCERPT}\n"
                self.github_record["issue"].update(title=row["input"]["title"], body=body)
                source = self.root / row["input"]["context"][0]["source_ref"]
                source.write_bytes(body.encode("utf-8"))
                row["input"]["context"][0]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                with self.assertRaises(SystemExit):
                    self.generate([row])
                self.assertEqual(self.calls, [])
                self.assertFalse(self.out.exists())

    def test_training_cannot_claim_a_held_out_own_source(self):
        row = self.default_scenario()
        held_out_id = generate_rows.build_inputs._reference_ids(self.set_a)[0]
        row["source_group"] = held_out_id
        meta = self.set_a / "metadata" / f"{held_out_id}.meta.json"
        meta.parent.mkdir(exist_ok=True)
        context = row["input"]["context"][0]
        meta.write_text(json.dumps({"source_file": context["source_ref"],
                                   "source_sha256": context["source_sha256"]}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.generate([row])
        self.assertEqual(self.calls, [])

    def test_objective_and_title_must_come_from_the_assignment(self):
        invalid = "A reconstructed completion report absent from the original assignment"
        for field, value in (("objective", invalid), ("objective", ""),
                             ("objective", None), ("title", invalid)):
            with self.subTest(field=field, value=value):
                row = self.default_scenario()
                row["input"][field] = value
                with self.assertRaises(SystemExit):
                    self.generate([row])
                self.assertEqual(self.calls, [])

    def test_standard_closing_claim_cannot_hide_earlier_work(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        for keyword in ("Fixes", "Closes", "Resolves", "FIXED:", "close", "resolved"):
            with self.subTest(keyword=keyword):
                earlier["body"] = f"{keyword} #77"
                # A PR targeting another branch may not have GitHub closing links.
                earlier["closingIssuesReferences"]["nodes"] = []
                with self.assertRaises(SystemExit):
                    self.generate([self.default_scenario()])
                self.assertEqual(self.calls, [])

    def test_manually_linked_issue_cannot_hide_earlier_work(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        earlier["closingIssuesReferences"]["nodes"] = [{
            "number": 77, "repository": {"nameWithOwner": "edwardjones3976/director-orchestrator"}}]
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_edited_out_work_claim_is_ambiguous_and_refused(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        earlier["lastEditedAt"] = "2026-09-28T13:00:00Z"
        earlier["userContentEdits"]["totalCount"] = 1
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_foreign_repository_pr_number_cannot_bind_the_work(self):
        work = self.github_record["issue"]["timelineItems"]["nodes"][1]["source"]
        work["repository"]["nameWithOwner"] = "someone-else/unrelated"
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_renamed_issue_title_is_not_immutable_input(self):
        self.github_record["issue"]["timelineItems"]["nodes"].append({
            "__typename": "RenamedTitleEvent"})
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])


class GitHubLookupTests(unittest.TestCase):
    def test_query_reads_cross_references_and_handles_graphql_errors(self):
        response = {"data": {"repository": {"issue": {"createdAt": ISSUE_CREATED_AT}}}}
        with patch.object(generate_rows.subprocess, "run", return_value=Mock(
            returncode=0, stdout=json.dumps(response),
        )) as call:
            self.assertEqual(
                generate_rows.github_chronology("edwardjones3976/director-orchestrator", 77),
                response["data"]["repository"],
            )
        command = call.call_args.args[0]
        self.assertTrue(any("timelineItems" in part and "commits(first:100)" in part
                            for part in command))
        with patch.object(generate_rows.subprocess, "run", return_value=Mock(
            returncode=0, stdout=json.dumps({"errors": [{"message": "denied"}]}),
        )):
            with self.assertRaises(SystemExit):
                generate_rows.github_chronology("edwardjones3976/director-orchestrator", 77)


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | END]]
