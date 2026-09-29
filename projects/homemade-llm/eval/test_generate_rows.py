# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | BEGIN]]
"""Unit tests for the M2-G1 exemplar harness. No provider is contacted.

    python projects/homemade-llm/eval/test_generate_rows.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_rows  # noqa: E402
import validate_rows  # noqa: E402
from test_validate_rows import SHA, UTC, write_sets  # noqa: E402

TARGET = {
    "non_goals": ["do not change the Slack app manifest or add a second signing key"],
    "constraints": ["compare every signature with a constant time function and log no secret bytes"],
    "acceptance_checks": ["callbacks signed with either secret verify during the overlap hour"],
    "evidence_requirements": ["unit test output for both secrets and the rotation timestamp"],
}


def scenario(group: str = "seed-001") -> dict:
    return {
        "source_group": group,
        "split": "train",
        "input": {
            "objective": "Rotate the webhook signing secret for the Slack gateway without dropping in-flight callbacks.",
            "repository": "edwardjones3976/director-orchestrator",
            "title": "Webhook secret rotation with overlap window",
            "context": [{
                "kind": "assignment_packet",
                "text": "Both secrets are accepted for one hour after rotation.",
                "source_ref": "docs/work-orders/wo-77.md",
                "source_sha256": SHA,
            }],
        },
    }


class GenerateRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.calls: list[str] = []

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
            **kwargs,
        )

    def test_dry_run_writes_nothing_and_calls_no_provider(self):
        report = self.generate([scenario()], dry_run=True, dispatch=self.dispatch())
        self.assertEqual(report, {"dry_run": True, "rows": 0, "receipts": 0})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_an_excluded_provider_is_refused_before_a_call(self):
        with self.assertRaises(SystemExit):
            self.generate([scenario()], provider="xai")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_a_clean_call_writes_one_schema_valid_row(self):
        report = self.generate([scenario()])
        self.assertEqual(report, {"dry_run": False, "rows": 1, "receipts": 1})
        row = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(row["source_provider"], "anthropic_api")
        self.assertEqual(row["source_model"], "claude-sonnet-5")
        self.assertEqual(row["receipt_ref"], "receipts/anthropic/20260929T053200Z.json")
        self.assertEqual(row["row_id"], validate_rows.canonical_row_id(row["input"], row["target"]))
        self.assertEqual(row["scrub"]["scan_report_ref"], "reports/scan-dataset-test.json")
        self.assertEqual(self.calls, ["anthropic_api"])

    def test_a_leak_writes_no_file(self):
        leaked = scenario()
        leaked["input"]["title"] = "Mirror task migration to the new G drive layout"
        with self.assertRaises(SystemExit):
            self.generate([leaked])
        self.assertFalse(self.out.exists())

    def test_a_bad_answer_writes_no_file(self):
        with self.assertRaises(SystemExit):
            self.generate([scenario()], dispatch=self.dispatch(json.dumps({"non_goals": ["only one field"]})))
        self.assertFalse(self.out.exists())

    def test_more_than_eight_scenarios_are_refused_before_a_call(self):
        rows = [scenario(f"seed-{index:03d}") for index in range(9)]
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
        marked = scenario()
        marked["input"]["objective"] = "Store the value as API_KEY=hidden"
        with self.assertRaises(SystemExit):
            generate_rows.load_scenarios(self.write_scenarios([marked]), 1)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | END]]
