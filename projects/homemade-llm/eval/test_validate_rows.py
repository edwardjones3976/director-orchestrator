"""Fixture-only tests: python -m unittest discover -s projects/homemade-llm/eval -p test_validate_rows.py -v"""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_inputs  # noqa: E402
import validate_rows  # noqa: E402
from test_build_inputs import _minimal_ref  # noqa: E402

SHA = "0123456789abcdef" * 4
UTC = "2026-09-28T20:00:00Z"

HELD_OUT = {
    "a": {
        "ew-alpha": dict(
            objective="Migrate the nightly mirror task to the new G drive layout without losing any report history.",
            title="Mirror task migration to the new G drive layout",
            non_goals=["do not change the archive drive layout or the restore drill schedule this week"],
            constraints=["keep every mirror log under the reports folder and never write outside C llm"],
            acceptance_checks=["the scheduled task runs once and mirrors every file with zero failures reported"],
            evidence_requirements=["attach the robocopy log and the restore drill sha256 from the run"],
        ),
    },
    "b": {
        "pr-beta": dict(
            objective="Add a spend meter window flag so the M2 receipts can be totalled separately from M1.",
            title="Spend meter window flag for M2 receipts",
            non_goals=["do not rename any existing receipt file or change the M1 window totals"],
            constraints=["the meter must read receipts read only and never modify a receipt on disk"],
            acceptance_checks=["the meter reports the M2 window total and the M1 total unchanged in one run"],
            evidence_requirements=["paste the meter output for both windows with the receipt count per window"],
        ),
    },
}


def write_sets(root: Path) -> tuple[Path, Path]:
    dirs = []
    for name, items in HELD_OUT.items():
        refs = root / f"set-{name}" / "refs"
        (refs / "metadata").mkdir(parents=True)
        for item_id, spec in items.items():
            spec = dict(spec)
            title = spec.pop("title")
            (refs / f"{item_id}.json").write_text(json.dumps(_minimal_ref(item_id, **spec)), encoding="utf-8")
            if name == "a":
                source = refs.parent / "packets" / f"{item_id}.md"
                source.parent.mkdir(exist_ok=True)
                source.write_text(f"# {title}\n\nBody.\n", encoding="utf-8")
                (refs / "metadata" / f"{item_id}.meta.json").write_text(
                    json.dumps({"source_file": f"packets/{item_id}.md"}), encoding="utf-8")
            else:
                (refs / "metadata" / f"{item_id}.meta.json").write_text(
                    json.dumps({"source_title": title}), encoding="utf-8")
        dirs.append(refs)
    return dirs[0], dirs[1]


def make_row(set_a: Path, set_b: Path, **overrides) -> dict:
    row = {
        "schema_version": "homemade-llm-director-row/v1",
        "split": "train",
        "source_kind": "provider_exemplar",
        "source_provider": "openai",
        "source_model": "gpt-6",
        "terms_tier": "competing_only_clause",
        "terms_verified_at": UTC,
        "generated_at": UTC,
        "receipt_ref": "receipts/openai/20260928T200000Z.json",
        "source_group": "seed-001",
        "input": {
            "objective": "Rotate the webhook signing secret for the Slack gateway without dropping in-flight callbacks.",
            "repository": "edwardjones3976/director-orchestrator",
            "title": "Webhook secret rotation with overlap window",
            "context": [{"kind": "assignment_packet", "text": "Both secrets are accepted for one hour after rotation.",
                         "source_ref": "docs/work-orders/wo-77.md", "source_sha256": SHA}],
        },
        "target": {
            "non_goals": ["do not change the Slack app manifest or add a second signing key"],
            "constraints": ["compare every signature with a constant time function and log no secret bytes"],
            "acceptance_checks": ["callbacks signed with either secret verify during the overlap hour"],
            "evidence_requirements": ["unit test output for both secrets and the rotation timestamp"],
        },
        "scrub": {"gitleaks_clean": True, "redact_pass": True, "scrubbed_at": UTC},
        "heldout_check": {
            "set_a_manifest": build_inputs.manifest(set_a),
            "set_b_manifest": build_inputs.manifest(set_b),
            "source_lineage": "clear",
            "near_duplicate": {"status": "clear", "max_overlap": 0.0, "threshold": 0.5, "quarantine_band": 0.2},
            "title_objective_match": False,
            "overlap": "none",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(row.get(key), dict):
            row[key] = {**row[key], **value}
        else:
            row[key] = value
    row["row_id"] = validate_rows.canonical_row_id(row["input"], row["target"])
    return row


class ValidateRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.rows_path = self.root / "rows.jsonl"
        self.out = self.root / "validated"
        self.runs = 0

    def row(self, **overrides) -> dict:
        return make_row(self.set_a, self.set_b, **overrides)

    def run_validator(self, rows: list, *extra: str) -> tuple[int, dict, str]:
        self.runs += 1
        out = self.out.with_name(f"validated-{self.runs}")
        self.rows_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = validate_rows.validate([
                "--rows", str(self.rows_path), "--set-a", str(self.set_a), "--set-b", str(self.set_b),
                "--out", str(out), *extra,
            ])
        report = json.loads((out / "report.json").read_text(encoding="utf-8")) if (out / "report.json").exists() else {}
        self.last_out = out
        return code, report, errors.getvalue()

    def read(self, name: str) -> list[dict]:
        return [json.loads(line) for line in (self.last_out / name).read_text(encoding="utf-8").splitlines()]

    def test_clean_rows_are_accepted_with_group_counts(self):
        rows = [self.row(), self.row(source_group="seed-002", split="dev",
                                     input={"objective": "Add a retry budget to the n8n intake webhook."},
                                     target={"non_goals": ["no change to the Slack copy"]})]
        code, report, stderr = self.run_validator(rows)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(report["n_accepted"], 2)
        self.assertEqual(report["n_rejected"], 0)
        self.assertEqual(report["n_quarantined"], 0)
        self.assertEqual(report["unique_source_groups_per_split"], {"train": 1, "dev": 1})
        self.assertEqual(report["set_a_manifest"], build_inputs.manifest(self.set_a))
        self.assertEqual([r["row_id"] for r in self.read("accepted.jsonl")], [r["row_id"] for r in rows])
        self.assertEqual(self.read("quarantine.jsonl"), [])

    def test_verbatim_held_out_row_fails(self):
        spec = HELD_OUT["a"]["ew-alpha"]
        target = {field: [spec[field][0]] for field in build_inputs.SCORED_FIELDS}
        row = self.row(input={"objective": spec["objective"], "title": spec["title"]}, target=target,
                       source_group="fresh-group", receipt_ref="receipts/openai/x.json")
        code, report, stderr = self.run_validator([row])
        self.assertEqual(code, 1)
        reasons = report["rejected"][0]["reasons"]
        self.assertTrue(any(r.startswith("title_objective_match: title equals set a item ew-alpha") for r in reasons), reasons)
        self.assertTrue(any(r.startswith("title_objective_match: objective equals set a item ew-alpha") for r in reasons), reasons)
        self.assertTrue(any(r.startswith("near_duplicate: overlap 1.000") for r in reasons), reasons)
        self.assertTrue(any(r.startswith("scored_text_leak: set a item ew-alpha") for r in reasons), reasons)
        self.assertEqual(self.read("accepted.jsonl"), [])
        for reason in reasons:  # no held-out text in the report, only ids and fields
            self.assertNotIn("mirror log", reason)

    def test_retitled_lightly_rewritten_held_out_example_fails_on_shingles(self):
        spec = HELD_OUT["b"]["pr-beta"]
        def tweak(text: str) -> str:
            words = text.split()
            words[-1] = words[-1] + "s"  # one word changed at the end of each field
            return " ".join(words)
        target = {field: [tweak(spec[field][0])] for field in build_inputs.SCORED_FIELDS}
        row = self.row(input={"objective": tweak(spec["objective"]), "title": "A completely different title now"},
                       target=target, source_group="rewritten-group", receipt_ref="receipts/openai/y.json")
        code, report, _ = self.run_validator([row])
        self.assertEqual(code, 1)
        reasons = report["rejected"][0]["reasons"]
        self.assertFalse(any(r.startswith("title_objective_match") for r in reasons), reasons)
        self.assertTrue(any(r.startswith("near_duplicate: overlap") and "nearest b/pr-beta/" in r for r in reasons), reasons)
        self.assertEqual(self.read("accepted.jsonl"), [])

    def test_source_id_lineage_fails_even_with_new_wording(self):
        cases = {
            "receipt_ref is a held-out id": {"receipt_ref": "pr-beta"},
            "receipt_ref path ends in a held-out id": {"receipt_ref": "receipts/openai/ew-alpha.json"},
            "source_group is a held-out id": {"source_group": "ew-alpha"},
            "source_group is a held-out source file stem": {"source_group": "EW_ALPHA"},
            "context source_ref is a held-out source file": {"input": {"context": [
                {"kind": "assignment_packet", "text": "Fresh wording only.", "source_ref": "packets/ew-alpha.md",
                 "source_sha256": SHA}]}},
            "context source_ref is a held-out title": {"input": {"context": [
                {"kind": "project_policy", "text": "Fresh wording only.",
                 "source_ref": "Spend meter window flag for M2 receipts", "source_sha256": SHA}]}},
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                code, report, _ = self.run_validator([self.row(**overrides)])
                self.assertEqual(code, 1)
                reasons = report["rejected"][0]["reasons"]
                self.assertTrue(any(r.startswith("lineage:") for r in reasons), reasons)
                self.assertEqual(self.read("accepted.jsonl"), [])

    def test_planted_excluded_tier_row_fails_on_schema(self):
        cases = {
            "xai provider": {"source_provider": "xai"},
            "excluded tier": {"terms_tier": "excluded"},
            "unclear tier": {"terms_tier": "unclear"},
            "perplexity": {"source_provider": "perplexity"},
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                code, report, _ = self.run_validator([self.row(**overrides)])
                self.assertEqual(code, 1)
                self.assertTrue(any(r.startswith("schema:") for r in report["rejected"][0]["reasons"]))
                self.assertEqual(self.read("accepted.jsonl"), [])

    def test_near_threshold_row_lands_in_quarantine_not_output(self):
        spec = HELD_OUT["a"]["ew-alpha"]
        words = spec["constraints"][0].split()
        # Two seven-word runs copied from the field with filler between them: enough shared
        # five-word shingles to land inside the band, but no eight-word run, so the outright
        # leak check stays quiet and the near-duplicate check decides.
        rewritten = " ".join(words[:7]) + " meanwhile also " + " ".join(words[7:]) + " for now"
        row = self.row(target={"constraints": [rewritten]}, source_group="band-group")
        overlap = validate_rows.shingle_containment(rewritten, spec["constraints"][0])
        self.assertGreaterEqual(overlap, 0.3)
        self.assertLess(overlap, 0.5)
        code, report, stderr = self.run_validator([row, self.row(source_group="clean-group")])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(report["n_quarantined"], 1)
        self.assertEqual(report["n_accepted"], 1)
        accepted = self.read("accepted.jsonl")
        self.assertEqual([r["source_group"] for r in accepted], ["clean-group"])
        quarantined = self.read("quarantine.jsonl")
        self.assertEqual(len(quarantined), 1)
        near = quarantined[0]["heldout_check"]["near_duplicate"]
        self.assertEqual(quarantined[0]["split"], "quarantine")
        self.assertEqual(near["status"], "quarantined")
        self.assertEqual(near["nearest_item"], "a/ew-alpha/constraints")
        self.assertAlmostEqual(near["max_overlap"], overlap)
        self.assertEqual(quarantined[0]["heldout_check"]["overlap"], "quarantined")
        # A row the producer already quarantined stays in quarantine even when it recomputes clear.
        declared = self.row(source_group="declared-group", split="quarantine", heldout_check={
            **self.row()["heldout_check"], "overlap": "quarantined",
            "near_duplicate": {"status": "quarantined", "max_overlap": 0.35, "threshold": 0.5,
                               "quarantine_band": 0.2, "nearest_item": "b/pr-beta/objective"}})
        code, report, stderr = self.run_validator([declared])
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report["n_quarantined"], report["n_accepted"]), (1, 0))

    def test_duplicate_and_wrong_row_ids_fail(self):
        row = self.row()
        forged = self.row(source_group="other-group")
        forged["row_id"] = "0" * 16
        code, report, _ = self.run_validator([row, copy.deepcopy(row), forged])
        self.assertEqual(code, 1)
        self.assertEqual(report["n_accepted"], 1)
        reasons = {entry["line"]: entry["reasons"] for entry in report["rejected"]}
        self.assertTrue(any(r.startswith("row_id: duplicate of line 1") for r in reasons[2]))
        self.assertTrue(any(r.startswith("row_id: does not equal") for r in reasons[3]))

    def test_provider_row_without_terms_verified_at_fails(self):
        row = self.row()
        del row["terms_verified_at"]
        code, report, _ = self.run_validator([row])
        self.assertEqual(code, 1)
        self.assertTrue(any("terms_verified_at" in r for r in report["rejected"][0]["reasons"]))
        history = self.row(source_kind="real_history", source_provider="repository_history",
                           source_model="n/a", terms_tier="repository_history", source_group="history-group")
        del history["terms_verified_at"]
        code, report, stderr = self.run_validator([history])
        self.assertEqual(code, 0, stderr)

    def test_source_group_on_both_sides_rejects_the_group(self):
        rows = [self.row(), self.row(split="dev", input={"title": "Second row of the same seed"}),
                self.row(source_group="lone-group", split="dev", input={"title": "Lone row"})]
        code, report, _ = self.run_validator(rows)
        self.assertEqual(code, 1)
        self.assertEqual(report["straddling_source_groups"], ["seed-001"])
        self.assertEqual(report["n_accepted"], 1)
        self.assertEqual(report["unique_source_groups_per_split"], {"train": 0, "dev": 1})
        self.assertEqual([r["source_group"] for r in self.read("accepted.jsonl")], ["lone-group"])

    def test_manifest_binding_and_run_parameters_are_enforced(self):
        stale = self.row(heldout_check={"set_a_manifest": "f" * 64})
        code, report, _ = self.run_validator([stale])
        self.assertEqual(code, 1)
        self.assertTrue(any("set_a_manifest" in r for r in report["rejected"][0]["reasons"]))
        code, _, stderr = self.run_validator([self.row()], "--set-b-manifest", "e" * 64)
        self.assertEqual(code, 2)
        self.assertIn("set b manifest mismatch", stderr)
        code, report, _ = self.run_validator([self.row()], "--threshold", "0.6")
        self.assertEqual(code, 1)
        self.assertTrue(any("threshold or quarantine_band" in r for r in report["rejected"][0]["reasons"]))

    def test_out_must_be_empty_and_malformed_lines_are_reported(self):
        self.out.mkdir()
        (self.out / "stale").write_text("x", encoding="utf-8")
        self.rows_path.write_text(json.dumps(self.row()) + "\n", encoding="utf-8")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = validate_rows.validate(["--rows", str(self.rows_path), "--set-a", str(self.set_a),
                                           "--set-b", str(self.set_b), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertIn("refusing non-empty --out", errors.getvalue())
        self.rows_path.write_text("not json\n[1, 2]\n" + json.dumps(self.row()) + "\n", encoding="utf-8")
        code, report, _ = self.run_validator([])  # run_validator rewrites rows; bypass by writing again
        self.rows_path.write_text("not json\n[1, 2]\n" + json.dumps(self.row()) + "\n", encoding="utf-8")
        self.runs += 1
        out = self.out.with_name(f"validated-{self.runs}")
        with redirect_stderr(io.StringIO()):
            code = validate_rows.validate(["--rows", str(self.rows_path), "--set-a", str(self.set_a),
                                           "--set-b", str(self.set_b), "--out", str(out)])
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(report["n_accepted"], 1)
        self.assertEqual([e["line"] for e in report["rejected"]], [1, 2])
        self.assertTrue(all(e["reasons"][0].startswith("json:") for e in report["rejected"]))


if __name__ == "__main__":
    unittest.main()
