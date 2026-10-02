"""Synthetic tests for item_score.py. Fixtures stay in temporary directories."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "item_score.py"
FIELDS = ("non_goals", "constraints", "acceptance_checks", "evidence_requirements")
PER_FIELD_KEYS = {
    "reference_items",
    "candidate_items",
    "matched",
    "misrouted",
    "omitted",
    "correct",
    "wrong_field",
    "duplicate",
    "spurious",
    "near_right_field",
    "precision",
    "recall",
}
TOP_KEYS = {
    "packets_scored",
    "packets_missing_candidate",
    "packets_malformed_candidate",
    "packets_unreadable_reference",
    "reference_items",
    "candidate_items",
    "per_field",
    "repeated_across_fields",
    "zero_error_packets",
    "zero_omission_packets",
    "packet_recall",
    "overall_recall",
    "overall_precision",
}
TOP_KEYS_INPUTS = TOP_KEYS | {"spurious_in_context", "spurious_not_in_context"}


def empty_packet(**fields):
    packet = {name: [] for name in FIELDS}
    packet.update(fields)
    return packet


class ItemScoreTests(unittest.TestCase):
    def setUp(self):
        self.secrets = []
        self.ids = []
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.refs = self.root / "refs"
        self.cands = self.root / "cands"
        self.inputs = self.root / "inputs"
        self.outdirs = self.root / "outs"
        self.refs.mkdir()
        self.cands.mkdir()
        self.inputs.mkdir()
        self.outdirs.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _mark(self, token):
        self.secrets.append(token)
        return token

    def _write(self, folder, item_id, obj):
        self.ids.append(item_id)
        path = folder / f"{item_id}.json"
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    def _out_path(self, name="score.json"):
        return self.outdirs / name

    def _assert_clean(self, *parts):
        blob = "".join(parts)
        for secret in self.secrets:
            self.assertNotIn(secret, blob)
        for item_id in self.ids:
            self.assertNotIn(item_id, blob)

    def _run(self, out, extra=None, expect_out=True):
        cmd = [
            sys.executable,
            str(TOOL),
            "--refs",
            str(self.refs),
            "--candidates",
            str(self.cands),
            "--out",
            str(out),
        ]
        if extra:
            cmd.extend(extra)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        out_text = ""
        if expect_out and out.exists() and out.is_file():
            out_text = out.read_text(encoding="utf-8")
        self._assert_clean(proc.stdout, proc.stderr, out_text)
        return proc, out_text

    def _load(self, proc, out_text):
        self.assertEqual(proc.stdout, out_text)
        return json.loads(proc.stdout)

    def test_hand_computed_every_category(self):
        marker = self._mark("SECRETMARKER-4c1e")
        item_id = "hid-corpus-a1"
        ref = empty_packet(
            non_goals=[f"Keep it simple {marker}"],
            constraints=[f"Must use Python {marker}"],
            acceptance_checks=[f"Return exit code zero {marker}"],
            evidence_requirements=[f"Cite sources {marker}"],
        )
        cand = empty_packet(
            non_goals=[f"Keep it simple {marker}", f"Keep it simple {marker}"],
            constraints=[f"Return exit code zero {marker}"],
            acceptance_checks=[f"Something extra {marker}"],
            evidence_requirements=[f"Cite sources {marker}"],
        )
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        data = self._load(proc, text)
        self.assertEqual(data["packets_scored"], 1)
        self.assertEqual(data["packets_missing_candidate"], 0)
        self.assertEqual(data["packets_malformed_candidate"], 0)
        self.assertEqual(data["reference_items"], 4)
        self.assertEqual(data["candidate_items"], 5)
        self.assertEqual(data["repeated_across_fields"], 0)
        self.assertEqual(data["zero_error_packets"], 0)
        self.assertEqual(data["zero_omission_packets"], 0)
        self.assertEqual(data["overall_recall"], 0.5)
        self.assertEqual(data["overall_precision"], 0.4)
        self.assertEqual(data["packet_recall"]["median"], 0.5)
        self.assertEqual(data["packet_recall"]["min"], 0.5)
        self.assertEqual(data["packet_recall"]["max"], 0.5)
        ng = data["per_field"]["non_goals"]
        self.assertEqual(ng["reference_items"], 1)
        self.assertEqual(ng["candidate_items"], 2)
        self.assertEqual(ng["matched"], 1)
        self.assertEqual(ng["misrouted"], 0)
        self.assertEqual(ng["omitted"], 0)
        self.assertEqual(ng["correct"], 1)
        self.assertEqual(ng["wrong_field"], 0)
        self.assertEqual(ng["duplicate"], 1)
        self.assertEqual(ng["spurious"], 0)
        self.assertEqual(ng["near_right_field"], 0)
        self.assertEqual(ng["precision"], 0.5)
        self.assertEqual(ng["recall"], 1.0)
        cons = data["per_field"]["constraints"]
        self.assertEqual(cons["reference_items"], 1)
        self.assertEqual(cons["candidate_items"], 1)
        self.assertEqual(cons["matched"], 0)
        self.assertEqual(cons["misrouted"], 0)
        self.assertEqual(cons["omitted"], 1)
        self.assertEqual(cons["correct"], 0)
        self.assertEqual(cons["wrong_field"], 1)
        self.assertEqual(cons["duplicate"], 0)
        self.assertEqual(cons["spurious"], 0)
        self.assertEqual(cons["near_right_field"], 0)
        self.assertEqual(cons["precision"], 0.0)
        self.assertEqual(cons["recall"], 0.0)
        acc = data["per_field"]["acceptance_checks"]
        self.assertEqual(acc["reference_items"], 1)
        self.assertEqual(acc["candidate_items"], 1)
        self.assertEqual(acc["matched"], 0)
        self.assertEqual(acc["misrouted"], 1)
        self.assertEqual(acc["omitted"], 0)
        self.assertEqual(acc["correct"], 0)
        self.assertEqual(acc["wrong_field"], 0)
        self.assertEqual(acc["duplicate"], 0)
        self.assertEqual(acc["spurious"], 1)
        self.assertEqual(acc["near_right_field"], 0)
        self.assertEqual(acc["precision"], 0.0)
        self.assertEqual(acc["recall"], 0.0)
        ev = data["per_field"]["evidence_requirements"]
        self.assertEqual(ev["reference_items"], 1)
        self.assertEqual(ev["candidate_items"], 1)
        self.assertEqual(ev["matched"], 1)
        self.assertEqual(ev["misrouted"], 0)
        self.assertEqual(ev["omitted"], 0)
        self.assertEqual(ev["correct"], 1)
        self.assertEqual(ev["wrong_field"], 0)
        self.assertEqual(ev["duplicate"], 0)
        self.assertEqual(ev["spurious"], 0)
        self.assertEqual(ev["near_right_field"], 0)
        self.assertEqual(ev["precision"], 1.0)
        self.assertEqual(ev["recall"], 1.0)
        fail = subprocess.run(
            [sys.executable, str(TOOL), "--refs", str(self.refs), "--candidates", str(self.cands)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(fail.returncode, 1)
        self._assert_clean(fail.stdout, fail.stderr)

    def test_cross_field_not_credited_to_precision(self):
        marker = self._mark("SECRETMARKER-xf91")
        item_id = "hid-xfield-b2"
        ref = empty_packet(
            non_goals=[f"only in non goals {marker}"],
            constraints=[f"only in constraints {marker}"],
        )
        cand = empty_packet(non_goals=[f"only in constraints {marker}"])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        ng = data["per_field"]["non_goals"]
        self.assertEqual(ng["correct"], 0)
        self.assertEqual(ng["wrong_field"], 1)
        self.assertEqual(ng["candidate_items"], 1)
        self.assertEqual(ng["precision"], 0.0)
        self.assertEqual(data["per_field"]["constraints"]["misrouted"], 1)
        self.assertEqual(data["per_field"]["constraints"]["matched"], 0)
        self.assertEqual(data["overall_precision"], 0.0)

    def test_duplicate_not_zero_error(self):
        marker = self._mark("SECRETMARKER-dup3")
        item_id = "hid-dup-c3"
        ref = empty_packet(non_goals=[f"same item {marker}"])
        cand = empty_packet(non_goals=[f"same item {marker}", f"same item {marker}"])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        ng = data["per_field"]["non_goals"]
        self.assertEqual(ng["correct"], 1)
        self.assertEqual(ng["duplicate"], 1)
        self.assertEqual(ng["precision"], 0.5)
        self.assertEqual(data["zero_error_packets"], 0)
        self.assertEqual(data["zero_omission_packets"], 1)
        self.assertLess(data["overall_precision"], 1.0)

    def test_multiset_needs_two_copies(self):
        marker = self._mark("SECRETMARKER-ms44")
        item_id = "hid-multi-d4"
        phrase = f"needs two copies {marker}"
        ref = empty_packet(constraints=[phrase, phrase])
        cand = empty_packet(constraints=[phrase])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        cons = data["per_field"]["constraints"]
        self.assertEqual(cons["reference_items"], 2)
        self.assertEqual(cons["candidate_items"], 1)
        self.assertEqual(cons["matched"], 1)
        self.assertEqual(cons["omitted"], 1)
        self.assertEqual(cons["correct"], 1)
        self.assertEqual(cons["duplicate"], 0)
        self.assertEqual(cons["recall"], 0.5)
        self.assertEqual(cons["precision"], 1.0)

    def test_near_tier_informational_only(self):
        marker = self._mark("SECRETMARKER-near5")
        item_id = "hid-near-e5"
        ref_text = f"alpha beta gamma delta epsilon NEAR {marker}"
        cand_text = f"alpha beta gamma delta epsilon NEAZ {marker}"
        ref = empty_packet(non_goals=[ref_text])
        cand = empty_packet(non_goals=[cand_text])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        ng = data["per_field"]["non_goals"]
        self.assertEqual(ng["omitted"], 1)
        self.assertEqual(ng["matched"], 0)
        self.assertEqual(ng["correct"], 0)
        self.assertEqual(ng["spurious"], 1)
        self.assertEqual(ng["wrong_field"], 0)
        self.assertEqual(ng["duplicate"], 0)
        self.assertEqual(ng["near_right_field"], 1)
        self.assertEqual(ng["precision"], 0.0)
        self.assertEqual(ng["recall"], 0.0)
        self.assertEqual(data["zero_error_packets"], 0)

    def test_inputs_splits_spurious(self):
        marker = self._mark("SECRETMARKER-ctx6")
        item_id = "hid-ctx-f6"
        inside = f"inside context phrase {marker}"
        outside = f"outside context phrase {marker}"
        real = f"real reference item {marker}"
        ref = empty_packet(non_goals=[real])
        cand = empty_packet(non_goals=[real, inside, outside])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        self._write(
            self.inputs,
            item_id,
            {"context": [{"text": f"prefix {inside} suffix"}]},
        )
        out_no = self._out_path("no_inputs.json")
        proc, text = self._run(out_no)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        self.assertNotIn("spurious_in_context", data)
        self.assertNotIn("spurious_not_in_context", data)
        self.assertEqual(data["per_field"]["non_goals"]["spurious"], 2)
        self.assertEqual(set(data.keys()), TOP_KEYS)
        out_yes = self._out_path("with_inputs.json")
        proc, text = self._run(
            out_yes,
            extra=["--inputs", str(self.inputs)],
        )
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        self.assertEqual(data["spurious_in_context"], 1)
        self.assertEqual(data["spurious_not_in_context"], 1)
        self.assertEqual(data["per_field"]["non_goals"]["spurious"], 2)
        self.assertEqual(set(data.keys()), TOP_KEYS_INPUTS)

    def test_exclude_missing_malformed_exit_2(self):
        marker = self._mark("SECRETMARKER-ex7a")
        kept = "hid-keep-g7"
        missing_id = "hid-miss-g7"
        bad_id = "hid-bad-g7"
        skipped = "hid-skip-g7"
        self._write(self.refs, kept, empty_packet(non_goals=[f"kept {marker}"]))
        self._write(self.cands, kept, empty_packet(non_goals=[f"kept {marker}"]))
        self._write(self.refs, missing_id, empty_packet(constraints=[f"missing cand {marker}"]))
        self._write(self.refs, bad_id, empty_packet(acceptance_checks=[f"bad cand {marker}"]))
        self._write(self.cands, bad_id, {"non_goals": "not-a-list", "constraints": [1]})
        self._write(self.refs, skipped, empty_packet(evidence_requirements=[f"skip me {marker}"]))
        self._write(self.cands, skipped, empty_packet(evidence_requirements=[f"skip me {marker}"]))
        exclude_path = self.root / "exclude.json"
        exclude_path.write_text(
            json.dumps([{"id": skipped, "reason": f"not needed {marker}"}]),
            encoding="utf-8",
        )
        out = self._out_path("mixed.json")
        proc, text = self._run(out, extra=["--exclude", str(exclude_path)])
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        self.assertEqual(data["packets_scored"], 1)
        self.assertEqual(data["packets_missing_candidate"], 1)
        self.assertEqual(data["packets_malformed_candidate"], 1)
        self.assertEqual(data["zero_error_packets"], 1)
        self.refs.joinpath(f"{kept}.json").unlink()
        self.cands.joinpath(f"{kept}.json").unlink()
        exclude_obj = self.root / "exclude_obj.json"
        exclude_obj.write_text(
            json.dumps({"excluded": [{"id": skipped, "reason": f"still {marker}"}]}),
            encoding="utf-8",
        )
        out2 = self._out_path("none_scored.json")
        proc, text = self._run(out2, extra=["--exclude", str(exclude_obj)])
        self.assertEqual(proc.returncode, 2)
        data = self._load(proc, text)
        self.assertEqual(data["packets_scored"], 0)
        self.assertEqual(data["packets_missing_candidate"], 1)
        self.assertEqual(data["packets_malformed_candidate"], 1)

    def test_atomic_output_existing_refused(self):
        marker = self._mark("SECRETMARKER-at8")
        item_id = "hid-atom-h8"
        self._write(self.refs, item_id, empty_packet(non_goals=[f"atomic {marker}"]))
        self._write(self.cands, item_id, empty_packet(non_goals=[f"atomic {marker}"]))
        isolated = self.root / "out_only"
        isolated.mkdir()
        out = isolated / "score.json"
        original = b"KEEP-ME-BYTES-unchanged"
        out.write_bytes(original)
        before = {p.name for p in isolated.iterdir()}
        proc, _text = self._run(out, expect_out=False)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out.read_bytes(), original)
        after = {p.name for p in isolated.iterdir()}
        self.assertEqual(before, after)
        leftovers = [
            p.name
            for p in isolated.iterdir()
            if p.name != "score.json"
        ]
        self.assertEqual(leftovers, [])
        self._assert_clean(proc.stdout, proc.stderr, original.decode("utf-8"))

    def test_printed_json_matches_file_documented_keys(self):
        marker = self._mark("SECRETMARKER-keys9")
        item_id = "hid-keys-i9"
        self._write(self.refs, item_id, empty_packet(non_goals=[f"keys {marker}"]))
        self._write(self.cands, item_id, empty_packet(non_goals=[f"keys {marker}"]))
        self._write(self.inputs, item_id, {"context": [{"text": f"unused {marker}"}]})
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, text)
        self.assertEqual(text, out.read_text(encoding="utf-8"))
        data = json.loads(text)
        self.assertEqual(set(data.keys()), TOP_KEYS)
        self.assertEqual(set(data["per_field"].keys()), set(FIELDS))
        for field in FIELDS:
            self.assertEqual(set(data["per_field"][field].keys()), PER_FIELD_KEYS)
        self.assertEqual(set(data["packet_recall"].keys()), {"median", "min", "max"})
        out2 = self._out_path("with_inputs.json")
        proc, text = self._run(out2, extra=["--inputs", str(self.inputs)])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, text)
        data = json.loads(text)
        self.assertEqual(set(data.keys()), TOP_KEYS_INPUTS)

    def test_repeated_across_fields(self):
        marker = self._mark("SECRETMARKER-rep0")
        item_id = "hid-rep-j0"
        shared = f"copied across fields {marker}"
        ref = empty_packet(non_goals=[shared], constraints=[f"other {marker}"])
        cand = empty_packet(non_goals=[shared], constraints=[shared])
        self._write(self.refs, item_id, ref)
        self._write(self.cands, item_id, cand)
        out = self._out_path()
        proc, text = self._run(out)
        self.assertEqual(proc.returncode, 0)
        data = self._load(proc, text)
        self.assertEqual(data["repeated_across_fields"], 2)
        self.assertEqual(data["per_field"]["constraints"]["wrong_field"], 0)
        self.assertEqual(data["per_field"]["constraints"]["spurious"], 1)

    def test_unreadable_reference_counted(self):
        marker = self._mark("SECRETMARKER-unreadable-ref")
        self._write(
            self.refs,
            "hid-good",
            empty_packet(non_goals=[f"Keep it simple {marker}"]),
        )
        self._write(self.cands, "hid-good", empty_packet())
        (self.refs / "hid-bad-json.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        self._write(
            self.refs,
            "hid-bad-fields",
            empty_packet(non_goals="not-a-list"),
        )
        out = self._out_path()
        proc, out_text = self._run(out)
        data = self._load(proc, out_text)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(data["packets_unreadable_reference"], 2)
        self.assertEqual(data["packets_scored"], 1)
        self.assertNotIn(marker, proc.stdout)


if __name__ == "__main__":
    unittest.main()
