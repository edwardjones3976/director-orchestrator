"""Synthetic archive/source fixtures only; no corpus, model or held-out reads."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_kep_inputs as tool

SENTINEL = "PRIVATE_SYNTHETIC_SENTINEL"


def source(number=19, newline="\n"):
    return newline.join([
        f"# Synthetic KEP {number} – café", "", "## Summary", "A synthetic description.",
        "## Motivation", "A synthetic need.", "## Goals", f"Bound synthetic scope {number}.",
        "## Non-Goals", "Do not expand unrelated work.", "## Notes/Constraints/Caveats (Optional)",
        "The synthetic platform might require support.", "## Graduation Criteria", "### Alpha",
        "Validate before enabling the feature.", "### Beta", "Unrelated later duties.",
        "## Test Plan", f"Test synthetic endpoints. {SENTINEL}", "",
    ])


class Fixture:
    def __init__(self, root, items=None):
        self.root = root
        self.items = items or [dict(number=19, sig="sig-example", status="implemented", text=source())]
        self.files = {"LICENSE": b"Apache-2.0 synthetic fixture\n"}
        census = []
        for config in self.items:
            number, sig = config["number"], config["sig"]
            parent = f"keps/{sig}/{number}-synthetic"
            path = parent + "/kep.yaml"
            raw = f"kep-number: {number}\nstatus: {config['status']}\ncreation-date: 2025-15-01\n".encode()
            self.files[path] = raw
            self.files[parent + "/README.md"] = config["text"].encode("utf-8")
            census.append({"path": path, "sig": sig, "kep_id": str(number), "status": config["status"],
                           "metadata_sha256": tool.digest(raw), "metadata_eligible": False,
                           "creation_date": "2025-15-01"})
        self.paths = {name: root / (name + (".tar.gz" if name == "archive" else ".json"))
                      for name in ("archive", "tree", "census", "policy", "license")}
        self.make_archive()
        self.tree = {"truncated": False, "tree": [{"path": path, "type": "blob", "sha": self.blob(raw)} for path, raw in self.files.items()]}
        self.census = {"repo": tool.REPOSITORY, "commit": tool.COMMIT,
                       "archive_sha256": tool.digest(self.paths["archive"].read_bytes()), "items": census}
        self.policy = {"protocol": "kep-input-selection/v2", "repository": tool.REPOSITORY,
                       "commit": tool.COMMIT, "statuses": ["implemented", "implementable"],
                       "creation_date_filter": None, "required_sections": list(tool.SUPPORT_SECTIONS),
                       "order": tool.ORDER, "frozen_at": "2020-01-01T00:00:00Z",
                       "authority": "issue299/comment5973258630"}
        self.paths["license"].write_bytes(self.files["LICENSE"])
        self.refresh()

    @staticmethod
    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()

    def make_archive(self, extra=None):
        with tarfile.open(self.paths["archive"], "w:gz") as archive:
            for path, raw in self.files.items():
                info = tarfile.TarInfo("enhancements-" + tool.COMMIT + "/" + path)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            if extra:
                archive.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)

    def refresh(self):
        for name in ("tree", "census", "policy"):
            self.paths[name].write_bytes(tool.json_bytes(getattr(self, name)))
        self.pins = {name: tool.digest(self.paths[name].read_bytes()) for name in tool.PINS}
        self.policy_sha = tool.digest(self.paths["policy"].read_bytes())

    def args(self, **overrides):
        moment = datetime(2020, 1, 2, tzinfo=timezone.utc)
        def clock():
            nonlocal moment
            moment += timedelta(seconds=1)
            return moment.isoformat().replace("+00:00", "Z")
        values = {"archive": self.paths["archive"], "tree": self.paths["tree"],
                  "census": self.paths["census"], "license_file": self.paths["license"],
                  "policy": self.paths["policy"], "policy_sha256": self.policy_sha,
                  "out": self.root / "out", "clock": clock, "_pins": self.pins}
        values.update(overrides)
        return values


class PrepareKepInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-kep-inputs-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, out=None):
        out = out or self.root / "out"
        raw = (out / "inputs.jsonl").read_bytes()
        return raw, [json.loads(line) for line in raw.splitlines()], json.loads((out / "manifest.json").read_bytes())

    def test_target_free_shared_shape_unicode_spans_and_exact_hashes(self):
        text = source(newline="\r\n")
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implementable", text=text)])
        result = tool.prepare(**fixture.args())
        raw, rows, manifest = self.load()
        self.assertEqual(result["usable_inputs"], 1)
        row = rows[0]
        self.assertEqual(set(row), tool.RECORD_KEYS)
        self.assertEqual(set(row["input"]), {"objective", "repository", "title", "context"})
        self.assertEqual(tool.prompt_template.input_contract(row["input"]), "D-M2-10")
        self.assertEqual(tool.prompt_template.context_shape_errors(row["input"]["context"]), [])
        self.assertIn("café", row["input"]["title"])
        tool.validate_record(row, text)
        self.assertEqual(manifest["inputs_sha256"], tool.digest(raw))
        self.assertEqual(manifest["records"][0]["sha256"], tool.digest(raw.splitlines(keepends=True)[0]))
        self.assertEqual(manifest["qualified_selection_sha256"], tool.digest(tool.json_bytes(manifest["qualification"])))
        self.assertEqual(row["qualified_selection_sha256"], manifest["qualified_selection_sha256"])
        self.assertEqual(manifest["summary"]["status_candidates"], 1)
        self.assertNotIn(b"\r\n", raw)  # CRLF excerpts remain escaped JSON, not JSONL delimiters.

    def test_metadata_v2_ignores_dates_and_v1_eligible_flag_preserves_refusals(self):
        items = [dict(number=19, sig="sig-example", status="implementable", text=source(19)),
                 dict(number=29, sig="sig-example", status="implemented", text=source(29)),
                 dict(number=39, sig="sig-example", status="withdrawn", text=source(39)),
                 dict(number=0, sig="sig-example", status="implemented", text=source(0)),
                 dict(number=49, sig="sig-example", status="imlpemented", text=source(49))]
        fixture = Fixture(self.root, items)
        result = tool.prepare(**fixture.args())
        self.assertEqual(result["usable_inputs"], 2)
        self.assertEqual(result["status_candidates"], 3)
        self.assertEqual(result["refused_by_reason"], {"INELIGIBLE_STATUS": 2, "NONPOSITIVE_ID": 1})
        self.assertEqual(result["metadata_candidates"], result["usable_inputs"] + result["refused"])

    def test_order_retains_original_rank_after_qualification_refusal(self):
        items = [dict(number=39, sig="sig-a", status="implemented", text=source(39)),
                 dict(number=29, sig="sig-b", status="implemented", text=source(29)),
                 dict(number=19, sig="sig-a", status="implemented", text=source(19).replace("## Non-Goals", "## Unmapped"))]
        fixture = Fixture(self.root, items)
        tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertEqual([(row["kep_number"], row["selection_rank"]) for row in rows], [(29, 1), (39, 2)])
        self.assertEqual(manifest["summary"]["section_qualified"], 2)
        reversed_root = self.root / "reversed"
        reversed_root.mkdir()
        reverse = Fixture(reversed_root, list(reversed(items)))
        tool.prepare(**reverse.args())
        _, reverse_rows, reverse_manifest = self.load(reversed_root / "out")
        self.assertEqual([(row["kep_number"], row["selection_rank"]) for row in reverse_rows], [(29, 1), (39, 2)])
        self.assertEqual(manifest["order_sha256"], reverse_manifest["order_sha256"])

    def test_duplicate_positive_identity_refused(self):
        fixture = Fixture(self.root, [dict(number=19, sig="sig-a", status="implemented", text=source()),
                                      dict(number=19, sig="sig-b", status="implemented", text=source())])
        with self.assertRaisesRegex(tool.Refusal, "DUPLICATE_KEP_ID"):
            tool.prepare(**fixture.args())
        self.assertFalse((self.root / "out").exists())

    def test_heading_and_input_refusals_with_reconciled_zero_count(self):
        cases = {
            "missing": (source().replace("## Non-Goals", "## Other"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
            "comment": (source().replace("## Non-Goals", "<!--\n## Non-Goals\n-->"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
            "fence": (source().replace("## Non-Goals", "```markdown\n## Non-Goals\n```"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
            "duplicate": (source() + "## Non-Goals\nDuplicate.\n", "AMBIGUOUS_SECTION"),
            "empty": (source().replace("Do not expand unrelated work.", "<!-- comment only -->"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
            "beta": (source().replace("### Alpha", "### Gamma"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
            "goals": (source().replace("## Goals", "## Other Goals"), "INPUT_GOALS_MISSING"),
            "title": (source().replace("# Synthetic KEP", "Synthetic KEP", 1), "INPUT_TITLE_MISSING"),
        }
        for name, (text, reason) in cases.items():
            with self.subTest(name=name):
                root = self.root / name
                root.mkdir()
                fixture = Fixture(root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
                with self.assertRaises(tool.Refusal) as raised:
                    tool.prepare(**fixture.args())
                self.assertEqual(str(raised.exception), "NO_USABLE_INPUTS")
                self.assertEqual(raised.exception.summary["refused_by_reason"], {reason: 1})
                self.assertFalse((root / "out").exists())

    def test_explicit_alpha_heading_and_optional_annotation(self):
        text = source().replace("## Graduation Criteria\n### Alpha", "## Alpha Graduation Criteria")
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
        self.assertEqual(tool.prepare(**fixture.args())["usable_inputs"], 1)

    def test_artifact_digest_and_pinned_tree_fail_before_output(self):
        fixture = Fixture(self.root)
        for key in ("archive", "tree", "census", "license"):
            with self.subTest(key=key):
                bad = dict(fixture.pins)
                bad[key] = "0" * 64
                with self.assertRaisesRegex(tool.Refusal, "ARTIFACT_PIN_MISMATCH"):
                    tool.prepare(**fixture.args(_pins=bad))
        fixture.tree["tree"][-1]["sha"] = "0" * 40
        fixture.refresh()
        with self.assertRaisesRegex(tool.Refusal, "SOURCE_GIT_BLOB_MISMATCH"):
            tool.prepare(**fixture.args())
        self.assertFalse((self.root / "out").exists())

    def test_archive_links_escapes_and_duplicate_members_are_refused(self):
        for kind in ("symlink", "hardlink", "escape", "duplicate"):
            with self.subTest(kind=kind):
                root = self.root / kind
                root.mkdir()
                fixture = Fixture(root)
                prefix = "enhancements-" + tool.COMMIT + "/"
                info = tarfile.TarInfo(prefix + ("../evil" if kind == "escape" else "LICENSE" if kind == "duplicate" else "linked"))
                if kind in ("symlink", "hardlink"):
                    info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    info.linkname = "outside"
                else:
                    info.size = 1
                fixture.make_archive(info)
                fixture.census["archive_sha256"] = tool.digest(fixture.paths["archive"].read_bytes())
                fixture.refresh()
                with self.assertRaisesRegex(tool.Refusal, "INVALID_ARCHIVE_MEMBER"):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_invalid_utf8_and_secret_pattern_refused_without_text_output(self):
        fixture = Fixture(self.root)
        path = next(path for path in fixture.files if path.endswith("README.md"))
        fixture.files[path] = b"\xff"
        fixture.make_archive()
        fixture.tree["tree"][-1]["sha"] = fixture.blob(b"\xff")
        fixture.census["archive_sha256"] = tool.digest(fixture.paths["archive"].read_bytes())
        fixture.refresh()
        with self.assertRaises(tool.Refusal) as raised:
            tool.prepare(**fixture.args())
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"INVALID_UTF8": 1})
        self.assertNotIn(SENTINEL, str(raised.exception))
        other = self.root / "secret"
        other.mkdir()
        fixture = Fixture(other, [dict(number=19, sig="sig-example", status="implemented", text=source() + "PASSWORD=synthetic\n")])
        with self.assertRaises(tool.Refusal) as raised:
            tool.prepare(**fixture.args())
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"SECRET_PATTERN": 1})

    def test_record_mutation_and_target_keys_cannot_pass(self):
        fixture = Fixture(self.root)
        tool.prepare(**fixture.args())
        _, rows, _ = self.load()
        for mutation in ("target", "context", "span", "future", "identity"):
            with self.subTest(mutation=mutation):
                row = copy.deepcopy(rows[0])
                if mutation == "target":
                    row["target"] = {}
                elif mutation == "context":
                    row["input"]["context"][0]["text"] = "rewritten"
                elif mutation == "span":
                    row["objective_span"]["start"] += 1
                elif mutation == "future":
                    row["generated_at"] = "2099-01-01T00:00:00Z"
                else:
                    row["source_group"] = "licensed/other/task"
                with self.assertRaises(tool.Refusal):
                    tool.validate_record(row, source())
        row = copy.deepcopy(rows[0])
        row["input"]["constraints"] = []
        with self.assertRaisesRegex(tool.Refusal, "INVALID_INPUT_SHAPE"):
            tool.validate_record(row, source())

    def test_distinct_per_record_clocks_and_invalid_chronology(self):
        fixture = Fixture(self.root, [dict(number=n, sig="sig-example", status="implemented", text=source(n)) for n in (19, 29)])
        tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertNotEqual(rows[0]["generated_at"], rows[1]["generated_at"])
        for row in rows:
            self.assertLess(tool.utc(manifest["policy_frozen_at"]), tool.utc(row["source_captured_at"]))
            self.assertLessEqual(tool.utc(row["source_captured_at"]), tool.utc(row["generated_at"]))
        with self.assertRaisesRegex(tool.Refusal, "INVALID_CHRONOLOGY"):
            tool.prepare(**fixture.args(out=self.root / "bad", clock=lambda: "2019-01-01T00:00:00Z"))

    def test_existing_destination_and_publication_race_never_clobber(self):
        fixture = Fixture(self.root)
        out = self.root / "out"
        out.mkdir()
        (out / "sentinel").write_text("existing")
        with self.assertRaisesRegex(tool.Refusal, "OUTPUT_EXISTS"):
            tool.prepare(**fixture.args())
        self.assertEqual((out / "sentinel").read_text(), "existing")
        raced = self.root / "raced"
        real_mkdir = Path.mkdir
        def race(path, *args, **kwargs):
            if path == raced and not path.exists():
                real_mkdir(path)
                (path / "sentinel").write_text("racer")
            return real_mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", race), self.assertRaisesRegex(tool.Refusal, "OUTPUT_EXISTS"):
            tool.prepare(**fixture.args(out=raced))
        self.assertEqual((raced / "sentinel").read_text(), "racer")
        self.assertFalse((raced / "inputs.jsonl").exists())

    def test_dry_run_has_no_outputs_network_subprocess_or_protected_imports(self):
        fixture = Fixture(self.root)
        before = {path.name: path.read_bytes() for path in fixture.paths.values()}
        with patch("socket.create_connection", side_effect=AssertionError("network")), patch("subprocess.run", side_effect=AssertionError("subprocess")):
            result = tool.prepare(**fixture.args(dry_run=True))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["usable_inputs"], 1)
        self.assertFalse((self.root / "out").exists())
        self.assertEqual(before, {path.name: path.read_bytes() for path in fixture.paths.values()})
        self.assertNotIn("generate_rows", tool.__dict__)
        self.assertNotIn("validate_rows", tool.__dict__)

    def test_policy_is_exact_and_cli_errors_are_sanitized(self):
        fixture = Fixture(self.root)
        fixture.policy["target"] = {"constraints": [SENTINEL]}
        fixture.refresh()
        with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
            tool.prepare(**fixture.args())
        stdout, stderr = io.StringIO(), io.StringIO()
        from contextlib import redirect_stdout, redirect_stderr
        argv = [part for key, path in fixture.paths.items() for part in ("--" + key, str(path))]
        argv += ["--policy-sha256", fixture.policy_sha, "--out", str(self.root / "out")]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(tool.main(argv), 1)  # CLI intentionally does not accept fixture pins.
        self.assertNotIn(SENTINEL, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["refused"], "ARTIFACT_PIN_MISMATCH")


if __name__ == "__main__":
    unittest.main()
