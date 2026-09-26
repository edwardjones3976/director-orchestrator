# [[Cursor | Composer 2.5 | 2026-09-26 | 14:50 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 02:52 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 01:40 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 00:40 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-26 | 00:35 UTC | BEGIN]]
"""Unit tests for score_packets.py (inline fixtures only).

    python projects/homemade-llm/eval/test_score_packets.py
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_packets  # noqa: E402


def _minimal_ref(packet_id: str = "p1", objective: str = "Do the thing.") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": packet_id,
        "assignment_id": "a1",
        "project": "demo",
        "repository": "org/repo",
        "starting_ref": "main",
        "objective": objective,
        "non_goals": ["skip"],
        "authority_snapshot": [
            {"path": "AGENTS.md", "commit_sha": "a" * 40, "blob_sha": "b" * 40}
        ],
        "authority_snapshot_digest": "sha256:" + "c" * 64,
        "dependencies": [],
        "constraints": ["stdlib only"],
        "prohibited_actions": [],
        "allowed_tools": [],
        "allowed_external_effects": [],
        "acceptance_checks": ["tests pass"],
        "evidence_requirements": ["command output"],
        "approval_requirements": [],
        "limits": {
            "budget_usd": 0,
            "timeout_seconds": 60,
            "max_hops": 1,
            "max_revisions": 0,
            "retry_limit": 1,
        },
        "idempotency_key": "k1",
        "correlation": {"correlation_id": "c1", "source_event_id": "e1"},
    }


class ScorePacketsTests(unittest.TestCase):
    def _run_score(self, refs: dict, cands: dict, grades: list[dict] | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            refs_dir = tmp_path / "refs"
            cands_dir = tmp_path / "cands"
            out_dir = tmp_path / "out"
            refs_dir.mkdir()
            cands_dir.mkdir()
            for pid, payload in refs.items():
                (refs_dir / f"{pid}.json").write_text(json.dumps(payload), encoding="utf-8")
            for pid, payload in cands.items():
                (cands_dir / f"{pid}.json").write_text(payload, encoding="utf-8")
            grades_path = None
            if grades is not None:
                grades_path = tmp_path / "grades.jsonl"
                grades_path.write_text(
                    "\n".join(json.dumps(row) for row in grades) + "\n",
                    encoding="utf-8",
                )
            summary = score_packets.score(
                refs,
                cands_dir,
                out_dir,
                score_packets.DEFAULT_FIELDS,
                grades_path,
                score_packets.DEFAULT_SCHEMA,
            )
            fields_rows = list(csv.DictReader((out_dir / "fields.csv").read_text(encoding="utf-8").splitlines()))
            packet_rows = list(csv.DictReader((out_dir / "packets.csv").read_text(encoding="utf-8").splitlines()))
            return summary, fields_rows, packet_rows

    def test_identical_packets_zero_change(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        summary, fields_rows, packet_rows = self._run_score({"p1": ref}, {"p1": cand})
        self.assertEqual(summary["missing_or_invalid_candidates"], 0)
        for row in fields_rows:
            self.assertEqual(float(row["similarity"]), 1.0)
            self.assertEqual(float(row["changed_fraction"]), 0.0)
        self.assertEqual(float(packet_rows[0]["text_changed_fraction"]), 0.0)

    def test_single_changed_field(self):
        ref = _minimal_ref()
        changed = dict(ref)
        changed["objective"] = "Do something else entirely."
        summary, fields_rows, _ = self._run_score({"p1": ref}, {"p1": json.dumps(changed)})
        self.assertEqual(summary["missing_or_invalid_candidates"], 0)
        by_field = {r["field"]: r for r in fields_rows}
        self.assertLess(float(by_field["objective"]["similarity"]), 1.0)
        self.assertEqual(float(by_field["non_goals"]["similarity"]), 1.0)

    def test_missing_candidate_counts_failure(self):
        ref = _minimal_ref()
        summary, fields_rows, packet_rows = self._run_score({"p1": ref}, {})
        self.assertEqual(summary["missing_or_invalid_candidates"], 1)
        self.assertEqual(fields_rows[0]["candidate_present"], "false")
        self.assertEqual(packet_rows[0]["candidate_present"], "false")
        self.assertEqual(float(packet_rows[0]["text_changed_fraction"]), 1.0)

    def test_invalid_json_counts_failure(self):
        ref = _minimal_ref()
        summary, _, packet_rows = self._run_score({"p1": ref}, {"p1": "{not json"})
        self.assertEqual(summary["missing_or_invalid_candidates"], 1)
        self.assertEqual(float(packet_rows[0]["text_changed_fraction"]), 1.0)

    def test_non_object_json_candidate_counts_invalid(self):
        ref = _minimal_ref()
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        for payload in ("[]", "42"):
            summary, fields_rows, packet_rows = self._run_score(
                {"p1": ref},
                {"p1": payload},
                grades,
            )
            self.assertEqual(summary["missing_or_invalid_candidates"], 1)
            self.assertEqual(packet_rows[0]["candidate_present"], "true")
            self.assertEqual(packet_rows[0]["accepted_as_is"], "false")
            self.assertEqual(float(packet_rows[0]["text_changed_fraction"]), 1.0)
            self.assertEqual(float(fields_rows[0]["similarity"]), 0.0)

    def test_numeric_grader_on_accepted_row_malformed_not_provider_identity(self):
        review_grades = [
            {"id": "p1", "grader": 1, "field": "objective", "accepted": True},
            {"id": "p1", "grader": "g2", "field": "objective", "accepted": True},
        ]
        self.assertFalse(
            score_packets.packet_accepted_as_is("p1", ("objective",), review_grades)
        )
        self.assertEqual(score_packets.count_malformed_grade_rows(review_grades), 1)

        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": 1, "field": "objective", "accepted": True},
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")
        self.assertEqual(summary["malformed_grade_rows"], 1)

    def test_accepted_grade_row_missing_grader_fails_closed(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "field": fields[0], "accepted": True},
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")
        self.assertEqual(summary["malformed_grade_rows"], 1)

    def test_two_grader_rules(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        base_grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ]
        summary_one, _, packets_one = self._run_score({"p1": ref}, {"p1": cand}, base_grades)
        self.assertEqual(summary_one["pct_accepted_as_is"], 0.0)
        self.assertEqual(packets_one[0]["accepted_as_is"], "false")

        disagree = base_grades + [
            {"id": "p1", "grader": "g2", "field": "objective", "accepted": False},
        ]
        _, _, packets_dis = self._run_score({"p1": ref}, {"p1": cand}, disagree)
        self.assertEqual(packets_dis[0]["accepted_as_is"], "false")

        both = base_grades + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary_both, _, packets_both = self._run_score({"p1": ref}, {"p1": cand}, both)
        self.assertEqual(packets_both[0]["accepted_as_is"], "true")
        self.assertEqual(summary_both["pct_accepted_as_is"], 100.0)

    def test_ten_percent_threshold_boundary(self):
        ref = _minimal_ref(objective="a" * 100)
        # Substituting 10 characters counts as 20 reference units of edit (delete + insert).
        changed = dict(ref)
        changed["objective"] = "b" * 10 + ("a" * 90)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            refs_dir = tmp_path / "refs"
            cands_dir = tmp_path / "cands"
            out_dir = tmp_path / "out"
            refs_dir.mkdir()
            cands_dir.mkdir()
            (refs_dir / "p1.json").write_text(json.dumps(ref), encoding="utf-8")
            (cands_dir / "p1.json").write_text(json.dumps(changed), encoding="utf-8")
            summary = score_packets.score(
                {"p1": ref},
                cands_dir,
                out_dir,
                ("objective",),
                None,
                score_packets.DEFAULT_SCHEMA,
            )
            with (out_dir / "packets.csv").open(encoding="utf-8") as fh:
                packet_rows = list(csv.DictReader(fh))
            frac = float(packet_rows[0]["text_changed_fraction"])
        self.assertAlmostEqual(frac, 0.2, places=6)
        self.assertGreater(frac, score_packets.CHANGE_BAR)
        self.assertEqual(summary["pct_remaining_at_or_under_10pct_text_change"], 0.0)

    def test_two_graders_each_skipped_one_field_not_accepted(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True}
            for f in fields[:-1]
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True}
            for f in fields[1:]
        ]
        _, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")

    def test_string_false_not_accepted_and_malformed(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": fields[0], "accepted": "false"},
        ] + [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields[1:]
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")
        self.assertEqual(summary["malformed_grade_rows"], 1)

    def test_three_graders_partial_rejection_not_accepted(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "grader": "g3", "field": fields[0], "accepted": False},
        ]
        _, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")

    def test_two_complete_graders_all_accepting(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary, _, packets = self._run_score({"p1": ref}, {"p1": cand}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "true")
        self.assertEqual(summary["pct_accepted_as_is"], 100.0)
        self.assertEqual(summary["malformed_grade_rows"], 0)

    def test_missing_candidate_two_accepting_graders_not_accepted(self):
        ref = _minimal_ref()
        fields = list(score_packets.DEFAULT_FIELDS)
        grades = [
            {"id": "p1", "grader": "g1", "field": f, "accepted": True} for f in fields
        ] + [
            {"id": "p1", "grader": "g2", "field": f, "accepted": True} for f in fields
        ]
        summary, _, packets = self._run_score({"p1": ref}, {}, grades)
        self.assertEqual(packets[0]["accepted_as_is"], "false")
        self.assertEqual(summary["pct_accepted_as_is"], 0.0)
        self.assertEqual(summary["accepted_as_is_count"], 0)

    def test_per_field_mean_similarity_counts_missing_as_zero(self):
        ref = _minimal_ref()
        cand = json.dumps(ref)
        summary, _, _ = self._run_score({"p1": ref, "p2": ref}, {"p1": cand})
        for field in score_packets.DEFAULT_FIELDS:
            self.assertEqual(summary["per_field_mean_similarity"][field], 0.5)

    def test_whitespace_reformatting_positive_changed_fraction(self):
        ref = _minimal_ref(objective="one two three")
        changed = dict(ref)
        changed["objective"] = "one  two\nthree"
        _, fields_rows, _ = self._run_score({"p1": ref}, {"p1": json.dumps(changed)})
        by_field = {r["field"]: r for r in fields_rows}
        self.assertGreater(float(by_field["objective"]["changed_fraction"]), 0.0)

    def test_insert_ten_chars_gives_ten_percent_changed_fraction(self):
        ref = _minimal_ref(objective="a" * 100)
        changed = dict(ref)
        changed["objective"] = ("a" * 100) + ("x" * 10)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            refs_dir = tmp_path / "refs"
            cands_dir = tmp_path / "cands"
            out_dir = tmp_path / "out"
            refs_dir.mkdir()
            cands_dir.mkdir()
            (refs_dir / "p1.json").write_text(json.dumps(ref), encoding="utf-8")
            (cands_dir / "p1.json").write_text(json.dumps(changed), encoding="utf-8")
            summary = score_packets.score(
                {"p1": ref},
                cands_dir,
                out_dir,
                ("objective",),
                None,
                score_packets.DEFAULT_SCHEMA,
            )
            fields_rows = list(
                csv.DictReader((out_dir / "fields.csv").read_text(encoding="utf-8").splitlines())
            )
            packet_rows = list(
                csv.DictReader((out_dir / "packets.csv").read_text(encoding="utf-8").splitlines())
            )
        self.assertAlmostEqual(float(fields_rows[0]["changed_fraction"]), 0.10, places=6)
        pkt_frac = float(packet_rows[0]["text_changed_fraction"])
        self.assertAlmostEqual(pkt_frac, 0.10, places=6)
        self.assertLessEqual(pkt_frac, score_packets.CHANGE_BAR)
        self.assertEqual(summary["pct_remaining_at_or_under_10pct_text_change"], 100.0)


if __name__ == "__main__":
    unittest.main()

# [[Cursor | cursor-agent | 2026-09-26 | 00:35 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 00:40 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 01:40 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 14:50 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 02:52 UTC | END]]
