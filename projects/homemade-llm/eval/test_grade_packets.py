"""Tests for grade_packets.py. Synthetic fixtures only; run from eval with python -m unittest."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import grade_packets

MARKER = "SECRETMARKER-7f3a"
TOOL = Path(__file__).resolve().parent / "grade_packets.py"
FIELDS = ("non_goals", "constraints", "acceptance_checks", "evidence_requirements")
RUBRIC_SHA_LIT = "b21255594b5901af96ce4e62c695b51a3106182fdf63e572d883433351302aa7"
SUMMARY_KEYS = {
    "expected_keys",
    "distinct_keys",
    "missing",
    "unknown",
    "outside_roster",
    "bad_field",
    "non_bool",
    "malformed",
    "duplicate",
    "conflicting",
    "foreign_label",
    "missing_reply_files",
}
BANNED = ("a3", "oracle", "refs", "copy", "ceiling", "regrade", "baseline", "control", "arm")


def write_item(refs: Path, cands: Path, iid: str, *, missing: bool = False, bad: bool = False) -> None:
    ref = {f: f"{MARKER}-{iid}-{f}" for f in FIELDS}
    (refs / f"{iid}.json").write_text(json.dumps(ref), encoding="utf-8")
    if missing:
        return
    if bad:
        (cands / f"{iid}.json").write_text("{ " + MARKER + " not-json", encoding="utf-8")
        return
    cand = {f: f"{MARKER}-{iid}-{f}-c" for f in FIELDS}
    (cands / f"{iid}.json").write_text(json.dumps(cand), encoding="utf-8")


def row(iid: str, field: str, accepted: object, grader: object | None = "__GRADER__") -> str:
    obj: dict = {"id": iid, "field": field, "accepted": accepted}
    if grader is not None:
        obj["grader"] = grader
        keys = ["id", "field", "grader", "accepted"]
        obj = {k: obj[k] for k in keys if k in obj}
    return json.dumps(obj, ensure_ascii=False)


def reply_body(items: list[str], *, accepted: bool = True, grader: str | None = "__GRADER__", extra: bool = False) -> str:
    parts: list[str] = []
    if extra:
        parts.append(f"prose around rows {MARKER}")
    for iid in items:
        for field in FIELDS:
            parts.append(row(iid, field, accepted, grader=grader))
    if extra:
        parts.append(f"trailing {MARKER}")
    return "\n".join(parts) + "\n"


def write_manifest(path: Path, packets: list[tuple[str, list[str]]], label: str = "run1", set_name: str = "ab") -> None:
    items: list[str] = []
    for _, ids in packets:
        items.extend(ids)
    man = {
        "label": label,
        "set": set_name,
        "rubric_sha256": RUBRIC_SHA_LIT,
        "fields": list(FIELDS),
        "n_items": len(items),
        "excluded": [],
        "packets": [
            {"packet": name, "items": ids, "sha256": "0" * 64}
            for name, ids in packets
        ],
    }
    path.write_text(json.dumps(man, indent=1), encoding="utf-8")


class TestGradePackets(unittest.TestCase):
    def run_tool(self, args: list[str]) -> subprocess.CompletedProcess:
        p = subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertNotIn(MARKER, p.stdout or "")
        self.assertNotIn(MARKER, p.stderr or "")
        return p

    def assert_no_tmps(self, directory: Path) -> None:
        leftover = []
        for p in directory.rglob("*"):
            n = p.name
            if n.endswith(".tmp") or n.startswith(".merge-") or n.startswith(".gpk-"):
                leftover.append(n)
        self.assertEqual(leftover, [])

    def test_01_packet_format_byte_exact(self) -> None:
        self.assertEqual(grade_packets.RUBRIC_SHA, RUBRIC_SHA_LIT)
        self.assertEqual(
            hashlib.sha256(grade_packets.RUBRIC.encode("utf-8")).hexdigest(),
            RUBRIC_SHA_LIT,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            out = root / "odir"
            refs.mkdir()
            cands.mkdir()
            ref = {
                "non_goals": f"{MARKER}-ng",
                "constraints": f"{MARKER}-co",
                "acceptance_checks": f"{MARKER}-ac",
                "evidence_requirements": f"{MARKER}-ev",
            }
            cand = {
                "non_goals": f"{MARKER}-ng-c",
                "constraints": f"{MARKER}-co-c",
                "acceptance_checks": f"{MARKER}-ac-c",
                "evidence_requirements": f"{MARKER}-ev-c",
            }
            (refs / "i1.json").write_text(json.dumps(ref), encoding="utf-8")
            (cands / "i1.json").write_text(json.dumps(cand), encoding="utf-8")
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(p.returncode, 0)
            self.assertEqual((p.stdout or "").strip(), "1 items 1 packets")
            packet = (out / "packet-run1-set-ab-b01.md").read_bytes()
            expected = (
                "GRADING PACKET homemade-llm M1 S1 | run run1 | set ab | batch 1 of 1\n"
                "You are one of two independent graders. Judge each candidate field against its reference field only. Do not rewrite anything; do not consult any other source; do not grade the objective.\n"
                f"RUBRIC (sha256 {RUBRIC_SHA_LIT}): Accept a field only if a Director could dispatch it unedited. Wording may differ; content may not: every requirement, prohibition, deadline, artifact binding and evidence obligation in the reference must be present, and nothing unsupported may be added.\n"
                "OUTPUT: for every item and each of the four fields, exactly one JSON line: "
                '{"id": "<id>", "field": "<field>", "grader": "__GRADER__", "accepted": true|false}. '
                "4 lines total, nothing else before or after them. A field missing from the candidate is accepted:false.\n"
                "\n"
                "=== ITEM i1 ===\n"
                "--- field non_goals\n"
                f'REFERENCE: "{MARKER}-ng"\n'
                f'CANDIDATE: "{MARKER}-ng-c"\n'
                "--- field constraints\n"
                f'REFERENCE: "{MARKER}-co"\n'
                f'CANDIDATE: "{MARKER}-co-c"\n'
                "--- field acceptance_checks\n"
                f'REFERENCE: "{MARKER}-ac"\n'
                f'CANDIDATE: "{MARKER}-ac-c"\n'
                "--- field evidence_requirements\n"
                f'REFERENCE: "{MARKER}-ev"\n'
                f'CANDIDATE: "{MARKER}-ev-c"\n'
            ).encode("utf-8")
            self.assertEqual(packet, expected)
            man = json.loads((out / "manifest-run1-set-ab.json").read_text(encoding="utf-8"))
            self.assertEqual(man["rubric_sha256"], RUBRIC_SHA_LIT)
            self.assertEqual(man["label"], "run1")
            self.assertEqual(man["set"], "ab")
            self.assertEqual(man["n_items"], 1)
            self.assertEqual(len(man["packets"]), 1)
            self.assertEqual(man["packets"][0]["sha256"], hashlib.sha256(packet).hexdigest())
            self.assertEqual(list(man["fields"]), list(FIELDS))

    def test_02_label_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            bad_labels = []
            for b in BANNED:
                bad_labels.append(b)
                bad_labels.append(b.upper())
                bad_labels.append(b[:1].upper() + b[1:])
                if b != "a3":
                    bad_labels.append("x" + b + "y")
            bad_labels.extend(["r", "R1", "r1_r", "r1-x", "r1.r", "r1r1r1r1r1r1r1r1x", ""])
            for lab in bad_labels:
                out = root / f"out_{hashlib.sha256(lab.encode()).hexdigest()[:8]}"
                with self.subTest(lab=lab):
                    args = [
                        "build",
                        "--refs",
                        str(refs),
                        "--candidates",
                        str(cands),
                        "--label",
                        lab,
                        "--set",
                        "ab",
                        "--out",
                        str(out),
                    ]
                    p = self.run_tool(args)
                    self.assertEqual(p.returncode, 1)
                    self.assertFalse(out.exists())

    def test_03_neutral_packets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "UNIQUE_REFS_DIRNAME_ZXQV"
            cands = root / "UNIQUE_CAND_DIRNAME_ZXQV"
            out = root / "UNIQUE_OUT_DIRNAME_ZXQV"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(p.returncode, 0)
            leaked = [
                str(refs),
                str(cands),
                str(out),
                refs.name,
                cands.name,
                out.name,
            ]
            header = None
            for pkt in out.glob("packet-*.md"):
                text = pkt.read_text(encoding="utf-8")
                for token in leaked:
                    self.assertNotIn(token, text)
                header = text.splitlines()[0]
                self.assertIn("run run1", header)
                self.assertIn("set ab", header)
                self.assertIn("batch 1", header)
                self.assertNotIn("/", header)
                self.assertNotIn("\\", header)
            self.assertIsNotNone(header)

    def test_04_exclusions_only_batch_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            for i in range(1, 6):
                write_item(refs, cands, f"t{i}", missing=(i == 4), bad=(i == 5))
            excl = root / "ex.json"
            excl.write_text(
                json.dumps([{"id": "t2", "reason": f"{MARKER}-reason"}]),
                encoding="utf-8",
            )
            only = root / "only.txt"
            only.write_text("t1\nt2\nt3\nt4\nt5\n", encoding="utf-8")
            out = root / "odir"
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--exclude",
                    str(excl),
                    "--only",
                    str(only),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(out),
                    "--batch",
                    "2",
                ]
            )
            self.assertEqual(p.returncode, 0)
            self.assertEqual((p.stdout or "").strip(), "4 items 2 packets")
            man = json.loads((out / "manifest-run1-set-ab.json").read_text(encoding="utf-8"))
            self.assertEqual(man["excluded"], ["t2"])
            self.assertEqual(man["n_items"], 4)
            self.assertEqual(len(man["packets"]), 2)
            self.assertEqual(man["packets"][0]["items"], ["t1", "t3"])
            self.assertEqual(man["packets"][1]["items"], ["t4", "t5"])
            text4 = (out / man["packets"][1]["packet"]).read_text(encoding="utf-8")
            self.assertIn("<missing or invalid candidate>", text4)
            self.assertIn("=== ITEM t4 ===", text4)
            self.assertIn("=== ITEM t5 ===", text4)
            out2 = root / "odir2"
            refs2 = root / "r2"
            cands2 = root / "c2"
            refs2.mkdir()
            cands2.mkdir()
            for i in range(1, 6):
                write_item(refs2, cands2, f"u{i}")
            p2 = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs2),
                    "--candidates",
                    str(cands2),
                    "--label",
                    "run1",
                    "--set",
                    "s1",
                    "--out",
                    str(out2),
                    "--batch",
                    "2",
                ]
            )
            self.assertEqual(p2.returncode, 0)
            self.assertEqual((p2.stdout or "").strip(), "5 items 3 packets")
            man2 = json.loads((out2 / "manifest-run1-set-s1.json").read_text(encoding="utf-8"))
            self.assertEqual(len(man2["packets"]), 3)
            self.assertEqual(man2["packets"][0]["items"], ["u1", "u2"])
            self.assertEqual(man2["packets"][1]["items"], ["u3", "u4"])
            self.assertEqual(man2["packets"][2]["items"], ["u5"])

    def test_05_atomic_builder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            out = root / "odir"
            out.mkdir()
            keep = out / "keep.txt"
            keep.write_text(f"{MARKER}-keep", encoding="utf-8")
            before = keep.read_bytes()
            names_before = sorted(p.name for p in out.iterdir())
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(p.returncode, 1)
            self.assertEqual(keep.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in out.iterdir()), names_before)
            bad_out = root / "freshout"
            bad_refs = root / "badrefs"
            bad_refs.mkdir()
            (bad_refs / "i1.json").write_text("{ " + MARKER, encoding="utf-8")
            p2 = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(bad_refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(bad_out),
                ]
            )
            self.assertEqual(p2.returncode, 1)
            self.assertFalse(bad_out.exists())
            self.assert_no_tmps(root)

    def _build_tiny(self, root: Path, ids: list[str], batch: int = 16) -> Path:
        refs = root / "rdir"
        cands = root / "cdir"
        out = root / "odir"
        refs.mkdir()
        cands.mkdir()
        for iid in ids:
            write_item(refs, cands, iid)
        p = self.run_tool(
            [
                "build",
                "--refs",
                str(refs),
                "--candidates",
                str(cands),
                "--label",
                "run1",
                "--set",
                "ab",
                "--out",
                str(out),
                "--batch",
                str(batch),
            ]
        )
        self.assertEqual(p.returncode, 0)
        return out

    def test_06_merge_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            built = self._build_tiny(root, ["t2", "t1"])
            man_path = built / "manifest-run1-set-ab.json"
            man = json.loads(man_path.read_text(encoding="utf-8"))
            replies = root / "replies"
            replies.mkdir()
            for pkt in man["packets"]:
                items = list(pkt["items"])
                parts = []
                for iid in reversed(items):
                    for field in reversed(FIELDS):
                        parts.append(row(iid, field, True, grader="__GRADER__"))
                (replies / (Path(pkt["packet"]).stem + ".txt")).write_text(
                    "\n".join(parts) + "\n", encoding="utf-8", newline="\n"
                )
            outp = root / "grades.jsonl"
            p = self.run_tool(
                [
                    "merge",
                    "--manifest",
                    str(man_path),
                    "--replies",
                    str(replies),
                    "--grader",
                    "gleft",
                    "--out",
                    str(outp),
                ]
            )
            self.assertEqual(p.returncode, 0)
            summary = json.loads((p.stdout or "").strip())
            self.assertEqual(set(summary), SUMMARY_KEYS)
            self.assertEqual(summary["missing"], 0)
            self.assertEqual(summary["malformed"], 0)
            data = outp.read_bytes()
            self.assertNotIn(b"\r", data)
            lines = data.decode("utf-8").split("\n")
            self.assertEqual(lines[-1], "")
            objs = [json.loads(ln) for ln in lines if ln]
            self.assertEqual(len(objs), 8)
            pairs = [(o["id"], o["field"]) for o in objs]
            self.assertEqual(pairs, sorted(pairs))
            for o in objs:
                self.assertEqual(o["grader"], "gleft")
                self.assertIs(o["accepted"], True)
                self.assertEqual(set(o.keys()), {"id", "field", "grader", "accepted"})

    def _merge_case(
        self,
        root: Path,
        packets: list[tuple[str, list[str]]],
        replies_map: dict[str, str],
        extra_args: list[str] | None = None,
        out_name: str = "grades.jsonl",
        grader: str = "gleft",
    ) -> subprocess.CompletedProcess:
        man_path = root / "man.json"
        write_manifest(man_path, packets)
        replies = root / "replies"
        replies.mkdir(exist_ok=True)
        for name, body in replies_map.items():
            (replies / name).write_text(body, encoding="utf-8", newline="\n")
        outp = root / out_name
        args = [
            "merge",
            "--manifest",
            str(man_path),
            "--replies",
            str(replies),
            "--grader",
            grader,
            "--out",
            str(outp),
        ]
        if extra_args:
            args.extend(extra_args)
        p = self.run_tool(args)
        if p.returncode != 0:
            self.assertFalse(outp.exists())
            self.assert_no_tmps(root)
        return p

    def test_07_merge_refusals(self) -> None:
        pkt = "packet-run1-set-ab-b01.md"
        pkt2 = "packet-run1-set-ab-b02.md"
        stem = Path(pkt).stem + ".txt"
        stem2 = Path(pkt2).stem + ".txt"
        items = ["t1"]

        def full(iid: str = "t1", grader: str | None = "__GRADER__", skip: str | None = None) -> str:
            parts = [f"prose {MARKER}"]
            for field in FIELDS:
                if field == skip:
                    continue
                parts.append(row(iid, field, True, grader=grader))
            parts.append(f"end {MARKER}")
            return "\n".join(parts) + "\n"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {stem: full(skip="constraints")})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertEqual(set(s), SUMMARY_KEYS)
            self.assertGreaterEqual(s["missing"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = full() + row("zz", "non_goals", True) + "\n"
            p = self._merge_case(root, [(pkt, items)], {stem: body})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["unknown"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = full("t2")
            p = self._merge_case(root, [(pkt, ["t1"]), (pkt2, ["t2"])], {stem: body, stem2: full("t2")})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["outside_roster"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parts = [row("t1", "objective", True)]
            for field in FIELDS:
                parts.append(row("t1", field, True))
            p = self._merge_case(root, [(pkt, items)], {stem: "\n".join(parts) + "\n"})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["bad_field"], 1)

        for bad_acc in ("true", 1, None):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                parts = [f"prose {MARKER}"]
                parts.append(row("t1", "non_goals", bad_acc))
                for field in FIELDS:
                    if field == "non_goals":
                        continue
                    parts.append(row("t1", field, True))
                p = self._merge_case(root, [(pkt, items)], {stem: "\n".join(parts) + "\n"})
                self.assertEqual(p.returncode, 2, msg=repr(bad_acc))
                s = json.loads((p.stdout or "").strip())
                self.assertGreaterEqual(s["non_bool"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = '{"id": "t1", "field": "non_goals", "accepted": true, "extra": 1}'
            body = bad + "\n" + full()
            p = self._merge_case(root, [(pkt, items)], {stem: body})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dup = full() + row("t1", "non_goals", True) + "\n"
            p = self._merge_case(root, [(pkt, items)], {stem: dup})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["duplicate"], 1)

        r_true = row("t1", "non_goals", True)
        r_false = row("t1", "non_goals", False)
        rest = "\n".join(row("t1", f, True) for f in FIELDS if f != "non_goals")
        for order in (r_true + "\n" + r_false + "\n" + rest + "\n", r_false + "\n" + r_true + "\n" + rest + "\n"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p = self._merge_case(root, [(pkt, items)], {stem: f"prose {MARKER}\n" + order})
                self.assertEqual(p.returncode, 2)
                s = json.loads((p.stdout or "").strip())
                self.assertGreaterEqual(s["conflicting"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["missing_reply_files"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {stem: full(grader="eve")})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["foreign_label"], 1)
            self.assertNotIn("eve", p.stdout or "")
            self.assertNotIn("eve", p.stderr or "")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = "\n".join(row("t1", f, True, grader="eve") for f in FIELDS) + "\n"
            p = self._merge_case(
                root,
                [(pkt, items)],
                {stem: body},
                extra_args=["--allow-label", "eve"],
                out_name="ok.jsonl",
            )
            self.assertEqual(p.returncode, 0)
            outp = root / "ok.jsonl"
            self.assertTrue(outp.is_file())
            text = outp.read_text(encoding="utf-8")
            self.assertIn('"grader": "gleft"', text)
            self.assertNotIn("eve", text)

    def test_08_merge_refuses_existing_out(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkt = "packet-run1-set-ab-b01.md"
            stem = Path(pkt).stem + ".txt"
            man_path = root / "man.json"
            write_manifest(man_path, [(pkt, ["t1"])])
            replies = root / "replies"
            replies.mkdir()
            (replies / stem).write_text(reply_body(["t1"]), encoding="utf-8", newline="\n")
            outp = root / "grades.jsonl"
            original = (MARKER + "-preexisting\n").encode("utf-8")
            outp.write_bytes(original)
            p = self.run_tool(
                [
                    "merge",
                    "--manifest",
                    str(man_path),
                    "--replies",
                    str(replies),
                    "--grader",
                    "gleft",
                    "--out",
                    str(outp),
                ]
            )
            self.assertEqual(p.returncode, 1)
            self.assertEqual(outp.read_bytes(), original)
            self.assert_no_tmps(root)

    def test_09_two_independent_graders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            built = self._build_tiny(root, ["t1"])
            man_path = built / "manifest-run1-set-ab.json"
            man = json.loads(man_path.read_text(encoding="utf-8"))
            replies = root / "replies"
            replies.mkdir()
            for pkt in man["packets"]:
                (replies / (Path(pkt["packet"]).stem + ".txt")).write_text(
                    reply_body(pkt["items"]), encoding="utf-8", newline="\n"
                )
            out_a = root / "ga.jsonl"
            out_b = root / "gb.jsonl"
            pa = self.run_tool(
                ["merge", "--manifest", str(man_path), "--replies", str(replies), "--grader", "ga", "--out", str(out_a)]
            )
            pb = self.run_tool(
                ["merge", "--manifest", str(man_path), "--replies", str(replies), "--grader", "gb", "--out", str(out_b)]
            )
            self.assertEqual(pa.returncode, 0)
            self.assertEqual(pb.returncode, 0)
            a_lines = [json.loads(ln) for ln in out_a.read_text(encoding="utf-8").splitlines() if ln]
            b_lines = [json.loads(ln) for ln in out_b.read_text(encoding="utf-8").splitlines() if ln]
            self.assertEqual(len(a_lines), 4)
            self.assertEqual(len(b_lines), 4)
            self.assertTrue(all(o["grader"] == "ga" for o in a_lines))
            self.assertTrue(all(o["grader"] == "gb" for o in b_lines))
            self.assertTrue(all(o["grader"] != "gb" for o in a_lines))
            self.assertTrue(all(o["grader"] != "ga" for o in b_lines))

    def test_10_summary_keys_only_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkt = "packet-run1-set-ab-b01.md"
            stem = Path(pkt).stem + ".txt"
            p = self._merge_case(
                root,
                [(pkt, ["t1"])],
                {stem: reply_body(["t1"])},
                out_name="ok.jsonl",
            )
            self.assertEqual(p.returncode, 0)
            summary = json.loads((p.stdout or "").strip())
            self.assertEqual(set(summary), SUMMARY_KEYS)
            for k, v in summary.items():
                self.assertIsInstance(k, str)
                self.assertIsInstance(v, int)
            with tempfile.TemporaryDirectory() as td2:
                root2 = Path(td2)
                p2 = self._merge_case(root2, [(pkt, ["t1"])], {})
                self.assertEqual(p2.returncode, 2)
                summary2 = json.loads((p2.stdout or "").strip())
                self.assertEqual(set(summary2), SUMMARY_KEYS)
                for v in summary2.values():
                    self.assertIsInstance(v, int)

    def test_11_reply_grammar(self) -> None:
        pkt = "packet-run1-set-ab-b01.md"
        stem = Path(pkt).stem + ".txt"
        items = ["t1"]
        accs = {
            "non_goals": True,
            "constraints": False,
            "acceptance_checks": True,
            "evidence_requirements": False,
        }

        def valid_rows() -> str:
            return "\n".join(row("t1", f, accs[f]) for f in FIELDS) + "\n"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = valid_rows() + '{"id": "t1", "field": "non_goals", "accepted": true\n'
            p = self._merge_case(root, [(pkt, items)], {stem: body})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parts = []
            for field in FIELDS:
                inner = {"id": "t1", "field": field, "accepted": True, "grader": "__GRADER__"}
                parts.append(json.dumps({"data": inner}))
            p = self._merge_case(root, [(pkt, items)], {stem: "\n".join(parts) + "\n"})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        rest = "\n".join(row("t1", f, True) for f in FIELDS if f != "non_goals")
        for blob in (
            '{"id": "t1", "field": "non_goals", "accepted": false, "accepted": true}',
            '{"id": "t1", "field": "non_goals", "accepted": true, "accepted": false}',
        ):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p = self._merge_case(root, [(pkt, items)], {stem: blob + "\n" + rest + "\n"})
                self.assertEqual(p.returncode, 2)
                s = json.loads((p.stdout or "").strip())
                self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {stem: f"please grade {MARKER}\n" + valid_rows()})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {stem: valid_rows() + f"thanks {MARKER}\n"})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        fence = "`" * 3
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(root, [(pkt, items)], {stem: fence + "json\n" + valid_rows()})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            listed = '{"id": "t1", "field": "non_goals", "accepted": [true, false]}'
            p = self._merge_case(root, [(pkt, items)], {stem: listed + "\n" + rest + "\n"})
            self.assertEqual(p.returncode, 2)
            s = json.loads((p.stdout or "").strip())
            self.assertGreaterEqual(s["malformed"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = self._merge_case(
                root,
                [(pkt, items)],
                {stem: valid_rows()},
                out_name="mixed.jsonl",
            )
            self.assertEqual(p.returncode, 0)
            outp = root / "mixed.jsonl"
            got = [json.loads(ln) for ln in outp.read_text(encoding="utf-8").splitlines() if ln]
            expected = [
                {"id": "t1", "field": field, "grader": "gleft", "accepted": accs[field]}
                for field in sorted(FIELDS)
            ]
            self.assertEqual(got, expected)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = "\n".join(["", fence + "json", "", *valid_rows().splitlines(), "", fence, ""]) + "\n"
            p = self._merge_case(
                root,
                [(pkt, items)],
                {stem: body},
                out_name="fenced.jsonl",
            )
            self.assertEqual(p.returncode, 0)
            outp = root / "fenced.jsonl"
            got = [json.loads(ln) for ln in outp.read_text(encoding="utf-8").splitlines() if ln]
            expected = [
                {"id": "t1", "field": field, "grader": "gleft", "accepted": accs[field]}
                for field in sorted(FIELDS)
            ]
            self.assertEqual(got, expected)

    def test_12_no_clobber_publish(self) -> None:
        pkt = "packet-run1-set-ab-b01.md"
        stem = Path(pkt).stem + ".txt"
        real_link = os.link
        real_rename = os.rename

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            man_path = root / "man.json"
            write_manifest(man_path, [(pkt, ["t1"])])
            replies = root / "replies"
            replies.mkdir()
            (replies / stem).write_text(reply_body(["t1"]), encoding="utf-8", newline="\n")
            outp = root / "grades.jsonl"
            sentinel = b"OTHER_WRITER_SENTINEL"

            def wrap_create(src, dst, *args, **kwargs):
                Path(dst).write_bytes(sentinel)
                return real_link(src, dst, *args, **kwargs)

            buf_out = io.StringIO()
            buf_err = io.StringIO()
            args = [
                "merge",
                "--manifest",
                str(man_path),
                "--replies",
                str(replies),
                "--grader",
                "gleft",
                "--out",
                str(outp),
            ]
            with unittest.mock.patch.object(grade_packets.os, "link", wrap_create):
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = grade_packets.main(args)
            self.assertNotIn(MARKER, buf_out.getvalue())
            self.assertNotIn(MARKER, buf_err.getvalue())
            self.assertEqual(rc, 1)
            self.assertEqual(outp.read_bytes(), sentinel)
            self.assert_no_tmps(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            man_path = root / "man.json"
            write_manifest(man_path, [(pkt, ["t1"])])
            replies = root / "replies"
            replies.mkdir()
            (replies / stem).write_text(reply_body(["t1"]), encoding="utf-8", newline="\n")
            outp = root / "grades.jsonl"

            def boom(src, dst, *args, **kwargs):
                raise OSError("boom")

            buf_out = io.StringIO()
            buf_err = io.StringIO()
            args = [
                "merge",
                "--manifest",
                str(man_path),
                "--replies",
                str(replies),
                "--grader",
                "gleft",
                "--out",
                str(outp),
            ]
            with unittest.mock.patch.object(grade_packets.os, "link", boom):
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = grade_packets.main(args)
            self.assertNotIn(MARKER, buf_out.getvalue())
            self.assertNotIn(MARKER, buf_err.getvalue())
            self.assertEqual(rc, 1)
            self.assertFalse(outp.exists())
            self.assert_no_tmps(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            out = root / "odir"

            def wrap_rename(src, dst, *args, **kwargs):
                dest = Path(dst)
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "sentinel.txt").write_text("OTHER_WRITER_SENTINEL", encoding="utf-8")
                return real_rename(src, dst, *args, **kwargs)

            buf_out = io.StringIO()
            buf_err = io.StringIO()
            args = [
                "build",
                "--refs",
                str(refs),
                "--candidates",
                str(cands),
                "--label",
                "run1",
                "--set",
                "ab",
                "--out",
                str(out),
            ]
            with unittest.mock.patch.object(grade_packets.os, "rename", wrap_rename):
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = grade_packets.main(args)
            self.assertNotIn(MARKER, buf_out.getvalue())
            self.assertNotIn(MARKER, buf_err.getvalue())
            self.assertEqual(rc, 1)
            self.assertEqual((out / "sentinel.txt").read_text(encoding="utf-8"), "OTHER_WRITER_SENTINEL")
            self.assert_no_tmps(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            out = root / "odir"

            def boom_rename(src, dst, *args, **kwargs):
                raise OSError("boom")

            buf_out = io.StringIO()
            buf_err = io.StringIO()
            args = [
                "build",
                "--refs",
                str(refs),
                "--candidates",
                str(cands),
                "--label",
                "run1",
                "--set",
                "ab",
                "--out",
                str(out),
            ]
            with unittest.mock.patch.object(grade_packets.os, "rename", boom_rename):
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = grade_packets.main(args)
            self.assertNotIn(MARKER, buf_out.getvalue())
            self.assertNotIn(MARKER, buf_err.getvalue())
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())
            self.assert_no_tmps(root)

    def test_13_set_neutrality(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            for name in BANNED:
                out = root / f"out_{name}"
                with self.subTest(name=name):
                    p = self.run_tool(
                        [
                            "build",
                            "--refs",
                            str(refs),
                            "--candidates",
                            str(cands),
                            "--label",
                            "run1",
                            "--set",
                            name,
                            "--out",
                            str(out),
                        ]
                    )
                    self.assertEqual(p.returncode, 1)
                    self.assertIn("invalid set", p.stderr or "")
                    self.assertFalse(out.exists())
            out_ok = root / "out_s1"
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "s1",
                    "--out",
                    str(out_ok),
                ]
            )
            self.assertEqual(p.returncode, 0)
            self.assertTrue(out_ok.is_dir())

    def _write_dup_constraints_candidate(self, cands: Path, iid: str, first: str, second: str) -> None:
        parts = [f'"{f}": []' for f in FIELDS if f != "constraints"]
        parts.append(f'"constraints": {json.dumps([first])}')
        parts.append(f'"constraints": {json.dumps([second])}')
        (cands / f"{iid}.json").write_text("{" + ", ".join(parts) + "}", encoding="utf-8")

    def test_14_duplicate_json_candidate_invalid_packet(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            ref = {f: f"{MARKER}-ref-{f}" for f in FIELDS}
            (refs / "i1.json").write_text(json.dumps(ref), encoding="utf-8")
            bad = f"{MARKER}-bad-c"
            good = f"{MARKER}-good-c"
            for first, second in ((bad, good), (good, bad)):
                with self.subTest(order=(first, second)):
                    out = root / f"out_{hash(first)}"
                    self._write_dup_constraints_candidate(cands, "i1", first, second)
                    p = self.run_tool(
                        [
                            "build",
                            "--refs",
                            str(refs),
                            "--candidates",
                            str(cands),
                            "--label",
                            "run1",
                            "--set",
                            "ab",
                            "--out",
                            str(out),
                        ]
                    )
                    self.assertEqual(p.returncode, 0)
                    packet = (out / "packet-run1-set-ab-b01.md").read_text(encoding="utf-8")
                    self.assertIn("<missing or invalid candidate>", packet)
                    self.assertNotIn(bad, packet)
                    self.assertNotIn(good, packet)

    def test_15_duplicate_json_reference_refuses_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            out = root / "odir"
            refs.mkdir()
            cands.mkdir()
            write_item(refs, cands, "i1")
            dup_ref = (
                '{"non_goals": ["'
                + f"{MARKER}-a"
                + '"], "non_goals": ["'
                + f"{MARKER}-b"
                + '"], "constraints": [], "acceptance_checks": [], "evidence_requirements": []}'
            )
            (refs / "i2.json").write_text(dup_ref, encoding="utf-8")
            p = self.run_tool(
                [
                    "build",
                    "--refs",
                    str(refs),
                    "--candidates",
                    str(cands),
                    "--label",
                    "run1",
                    "--set",
                    "ab",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(p.returncode, 1)
            self.assertEqual((p.stderr or "").strip(), "usage error")
            self.assertFalse(out.exists())
            self.assert_no_tmps(root)

    def test_16_valid_control_packet_unchanged(self) -> None:
        self.test_01_packet_format_byte_exact()

    def _build_with_exclude(self, root: Path, exclude_text: str) -> subprocess.CompletedProcess:
        refs = root / "rdir"
        cands = root / "cdir"
        refs.mkdir(exist_ok=True)
        cands.mkdir(exist_ok=True)
        write_item(refs, cands, "i1")
        write_item(refs, cands, "i2")
        excl = root / "ex.json"
        excl.write_text(exclude_text, encoding="utf-8")
        out = root / "odir"
        return self.run_tool(
            [
                "build",
                "--refs",
                str(refs),
                "--candidates",
                str(cands),
                "--exclude",
                str(excl),
                "--label",
                "run1",
                "--set",
                "ab",
                "--out",
                str(out),
            ]
        )

    def test_17_exclude_strict_refusals(self) -> None:
        bad_cases = [
            ('{"excluded": "not-a-list"}', "non_list_excluded"),
            ('{"exclude": [{"id": "i1"}]}', "misspelled_key"),
            (
                '{"excluded": [{"id": "i1"}], "extra": 1}',
                "extra_top_level",
            ),
            ('["not-a-row"]', "row_string"),
            ('[{"reason": "x"}]', "row_lacks_id"),
            ('{"id": "i1", "id": "i2"}', "duplicate_members"),
            ("{not json", "invalid_json"),
            ('[{"id": "i1"}, {"id": "i1"}]', "duplicate_id"),
        ]
        for body, _name in bad_cases:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p = self._build_with_exclude(root, body)
                self.assertEqual(p.returncode, 1, msg=body)
                self.assertEqual((p.stderr or "").strip(), "usage error")
                self.assertFalse((root / "odir").exists())
                self.assert_no_tmps(root)

    def test_18_exclude_valid_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            refs = root / "rdir"
            cands = root / "cdir"
            refs.mkdir()
            cands.mkdir()
            for iid in ("keep", "drop"):
                write_item(refs, cands, iid)
            for idx, body in enumerate(
                (
                    json.dumps([{"id": "drop"}]),
                    json.dumps({"excluded": [{"id": "drop"}]}),
                )
            ):
                with self.subTest(body=body):
                    excl = root / f"ex{idx}.json"
                    excl.write_text(body, encoding="utf-8")
                    out = root / f"odir{idx}"
                    p = self.run_tool(
                        [
                            "build",
                            "--refs",
                            str(refs),
                            "--candidates",
                            str(cands),
                            "--exclude",
                            str(excl),
                            "--label",
                            "run1",
                            "--set",
                            "ab",
                            "--out",
                            str(out),
                        ]
                    )
                    self.assertEqual(p.returncode, 0)
                    man = json.loads(
                        (out / "manifest-run1-set-ab.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(man["excluded"], ["drop"])
                    all_items: list[str] = []
                    for pkt in man["packets"]:
                        all_items.extend(pkt["items"])
                    self.assertEqual(all_items, ["keep"])
                    self.assertNotIn("drop", all_items)

    def _merge_bad_manifest(self, man_obj: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            man_path = root / "man.json"
            man_path.write_text(json.dumps(man_obj, indent=1), encoding="utf-8")
            replies = root / "replies"
            replies.mkdir()
            pkt = man_obj["packets"][0]["packet"]
            stem = Path(pkt).stem + ".txt"
            (replies / stem).write_text(reply_body(["t1"]), encoding="utf-8", newline="\n")
            outp = root / "grades.jsonl"
            p = self.run_tool(
                [
                    "merge",
                    "--manifest",
                    str(man_path),
                    "--replies",
                    str(replies),
                    "--grader",
                    "gleft",
                    "--out",
                    str(outp),
                ]
            )
            self.assertFalse(outp.exists())
            self.assert_no_tmps(root)
            return p

    def test_19_merge_manifest_validation(self) -> None:
        base_packets = [("packet-run1-set-ab-b01.md", ["t1"])]
        base = {
            "label": "run1",
            "set": "ab",
            "rubric_sha256": RUBRIC_SHA_LIT,
            "fields": list(FIELDS),
            "n_items": 1,
            "excluded": [],
            "packets": [
                {
                    "packet": "packet-run1-set-ab-b01.md",
                    "items": ["t1"],
                    "sha256": "0" * 64,
                }
            ],
        }
        bad = dict(base)
        bad["rubric_sha256"] = "f" * 64
        p = self._merge_bad_manifest(bad)
        self.assertEqual(p.returncode, 1)
        self.assertEqual((p.stderr or "").strip(), "usage error")

        bad = dict(base)
        bad["fields"] = list(reversed(FIELDS))
        p = self._merge_bad_manifest(bad)
        self.assertEqual(p.returncode, 1)

        bad = dict(base)
        bad["n_items"] = 99
        p = self._merge_bad_manifest(bad)
        self.assertEqual(p.returncode, 1)

        bad = dict(base)
        bad["packets"] = [
            {
                "packet": "packet-run1-set-ab-b01.md",
                "items": ["t1"],
                "sha256": "0" * 64,
            },
            {
                "packet": "packet-run1-set-ab-b02.md",
                "items": ["t1"],
                "sha256": "0" * 64,
            },
        ]
        bad["n_items"] = 2
        p = self._merge_bad_manifest(bad)
        self.assertEqual(p.returncode, 1)

        dup_member = (
            '{"label": "run1", "set": "ab", "rubric_sha256": "'
            + RUBRIC_SHA_LIT
            + '", "fields": '
            + json.dumps(list(FIELDS))
            + ', "n_items": 1, "packets": [{"packet": "packet-run1-set-ab-b01.md", '
            + '"items": ["t1"], "sha256": "'
            + "0" * 64
            + '", "sha256": "'
            + "1" * 64
            + '"}]}'
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            man_path = root / "man.json"
            man_path.write_text(dup_member, encoding="utf-8")
            replies = root / "replies"
            replies.mkdir()
            (replies / "packet-run1-set-ab-b01.txt").write_text(
                reply_body(["t1"]), encoding="utf-8", newline="\n"
            )
            outp = root / "grades.jsonl"
            p = self.run_tool(
                [
                    "merge",
                    "--manifest",
                    str(man_path),
                    "--replies",
                    str(replies),
                    "--grader",
                    "gleft",
                    "--out",
                    str(outp),
                ]
            )
            self.assertEqual(p.returncode, 1)
            self.assertFalse(outp.exists())

        bad = dict(base)
        bad["packets"] = [
            {"packet": "packet-run1-set-ab-b01.md", "items": ["t1"]},
        ]
        p = self._merge_bad_manifest(bad)
        self.assertEqual(p.returncode, 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            built = self._build_tiny(root, ["t1", "t2"])
            man_path = built / "manifest-run1-set-ab.json"
            replies = root / "replies"
            replies.mkdir()
            man = json.loads(man_path.read_text(encoding="utf-8"))
            for pkt in man["packets"]:
                (replies / (Path(pkt["packet"]).stem + ".txt")).write_text(
                    reply_body(pkt["items"]), encoding="utf-8", newline="\n"
                )
            outp = root / "grades.jsonl"
            p = self.run_tool(
                [
                    "merge",
                    "--manifest",
                    str(man_path),
                    "--replies",
                    str(replies),
                    "--grader",
                    "gleft",
                    "--out",
                    str(outp),
                ]
            )
            self.assertEqual(p.returncode, 0)
            self.assertTrue(outp.is_file())


if __name__ == "__main__":
    unittest.main()
