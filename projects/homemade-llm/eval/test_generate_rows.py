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
from unittest.mock import patch

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
ISSUE_CREATED_AT = "2026-09-27T10:00:00Z"
FIRST_COMMIT_AT = "2026-09-28T12:00:00Z"


def install_pre_work_assignment(root: Path) -> tuple[str, str, dict]:
    """Write assignment bytes; GitHub chronology is mocked in this offline test."""
    assignment_path = root / "assignments" / "wo-77.md"
    assignment_path.parent.mkdir(parents=True, exist_ok=True)
    assignment_path.write_bytes(
        f"# Webhook secret rotation\n\n{ASSIGNMENT_EXCERPT}\n".encode("utf-8")
    )
    source_ref = "assignments/wo-77.md"
    source_sha256 = hashlib.sha256(assignment_path.read_bytes()).hexdigest()
    provenance = {"github_issue": 77, "first_work_pr": 88}
    return source_ref, source_sha256, provenance


def scenario(group: str = "seed-001", root: Path | None = None, **overrides) -> dict:
    if root is None:
        raise ValueError("scenario fixtures require a dataset root with assignment sources")
    source_ref, source_sha256, provenance = install_pre_work_assignment(root)
    row = {
        "source_group": group,
        "split": "train",
        "source_provenance": provenance,
        "input": {
            "objective": "Rotate the webhook signing secret for the Slack gateway without dropping in-flight callbacks.",
            "repository": "edwardjones3976/director-orchestrator",
            "title": "Webhook secret rotation with overlap window",
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
                "createdAt": ISSUE_CREATED_AT, "lastEditedAt": None,
                "userContentEdits": {"totalCount": 0},
                "body": "# Webhook secret rotation\n\n" + ASSIGNMENT_EXCERPT + "\n",
            },
            "pullRequest": {"createdAt": "2026-09-28T12:01:00Z", "commits": {"nodes": [{"commit": {
                "oid": "a" * 40, "committedDate": FIRST_COMMIT_AT,
            }}]}},
        }
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | END]]
