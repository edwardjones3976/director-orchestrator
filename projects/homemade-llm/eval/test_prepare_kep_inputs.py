"""Synthetic archive/source fixtures only; no corpus, model or held-out reads."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import io
import json
import os
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
            parent = f"keps/{sig}/{config.get('directory_number', number)}-synthetic"
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


@unittest.skipUnless(os.name == "nt", "Two-file publication qualified on Windows only; no POSIX birth implementation")
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

    def test_provider_sources_keep_eligible_input_and_all_ineligible_census_entries(self):
        items = [dict(number=423, sig="provider-aws", status="implementable", text=source(423)),
                 dict(number=19, sig="sig-example", status="implemented", text=source(19))]
        items.extend(dict(number=n, sig="provider-aws", status="provisional", text="Not an eligible body.")
                     for n in (2313, 2314, 2315, 629, 630))
        fixture = Fixture(self.root, items)
        result = tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertEqual([(r["kep_number"], r["selection_rank"]) for r in rows], [(423, 0), (19, 1)])
        self.assertEqual(rows[0]["source_ref"], "keps/provider-aws/423-synthetic/README.md")
        self.assertEqual(rows[0]["source_group"], "licensed/kubernetes/enhancements/kep-423")
        self.assertEqual(result["metadata_candidates"], 7)
        self.assertEqual(result["status_candidates"], 2)
        self.assertEqual(result["usable_inputs"], 2)
        self.assertEqual(result["refused_by_reason"], {"INELIGIBLE_STATUS": 5})
        census = manifest["qualification"]["census"]
        self.assertEqual(len(census), 7)
        provider = [entry for entry in census if entry["sig"] == "provider-aws"]
        self.assertEqual(len(provider), 6)
        self.assertEqual(sum(entry["reason"] == "INELIGIBLE_STATUS" for entry in provider), 5)
        self.assertTrue(all(entry["selection_rank"] is None for entry in provider if entry["reason"]))
        self.assertEqual({entry["kep_number"] for entry in provider}, {423, 2313, 2314, 2315, 629, 630})

    def test_replaced_and_implemented_identity_has_one_eligible_group(self):
        fixture = Fixture(self.root, [dict(number=2133, sig="sig-legacy", status="replaced", text="Not read."),
                                      dict(number=2133, sig="sig-current", status="implemented", text=source(2133))])
        result = tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertEqual(result["metadata_candidates"], 2)
        self.assertEqual(result["usable_inputs"], 1)
        self.assertEqual([row["kep_number"] for row in rows], [2133])
        self.assertEqual(result["refused_by_reason"], {"INELIGIBLE_STATUS": 1})
        self.assertEqual([item["kep_number"] for item in manifest["qualification"]["census"]], [2133, 2133])

    def test_provider_buckets_keep_frozen_round_robin_rank_and_census_order_independence(self):
        items = [dict(number=2313, sig="provider-aws", status="implemented", text=source(2313).replace("## Non-Goals", "## Unmapped")),
                 dict(number=423, sig="provider-aws", status="implementable", text=source(423)),
                 dict(number=29, sig="provider-z", status="implemented", text=source(29)),
                 dict(number=39, sig="sig-a", status="implemented", text=source(39)),
                 dict(number=19, sig="sig-a", status="implemented", text=source(19)),
                 dict(number=9, sig="sig-b", status="implemented", text=source(9))]
        fixture = Fixture(self.root, items)
        tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        expected = [(423, 0), (29, 1), (19, 2), (9, 3), (39, 5)]
        self.assertEqual([(r["kep_number"], r["selection_rank"]) for r in rows], expected)
        refused = next(entry for entry in manifest["qualification"]["census"] if entry["kep_number"] == 2313)
        self.assertEqual(refused["sig"], "provider-aws")
        self.assertEqual(refused["selection_rank"], 4)
        self.assertEqual(refused["reason"], "MISSING_OR_EMPTY_SUPPORT_SECTION")
        reverse_root = self.root / "reverse"
        reverse_root.mkdir()
        reverse = Fixture(reverse_root, list(reversed(items)))
        tool.prepare(**reverse.args())
        _, reverse_rows, reverse_manifest = self.load(reverse_root / "out")
        self.assertEqual([(r["kep_number"], r["selection_rank"]) for r in reverse_rows], expected)
        self.assertEqual(manifest["order_sha256"], reverse_manifest["order_sha256"])

    def test_provider_extension_refuses_unsafe_or_unrecognized_namespace_and_path(self):
        for index, (namespace, leaf) in enumerate((
            ("team-aws", "19-synthetic"), ("provider-", "19-synthetic"),
            ("provider-AWS", "19-synthetic"), ("provider_aws", "19-synthetic"),
            ("provider-aws", ".."), ("provider-aws", "."),
            ("provider-aws", "nested/19-synthetic"), ("provider-aws", "19\\escape"),
        )):
            with self.subTest(namespace=namespace, leaf=leaf):
                case_root = self.root / str(index)
                case_root.mkdir()
                fixture = Fixture(case_root)
                # Mutate metadata only, leaving the independently valid tree intact:
                # census validation, not an archive failure, must catch this.
                fixture.census["items"][0].update(sig=namespace, path=f"keps/{namespace}/{leaf}/kep.yaml", status="provisional")
                fixture.refresh()
                with self.assertRaisesRegex(tool.Refusal, "INVALID_CENSUS"):
                    tool.prepare(**fixture.args())
                self.assertFalse((case_root / "out").exists())

    def test_provider_metadata_bucket_mismatch_and_duplicate_path_still_refused(self):
        for mutation in ("bucket", "duplicate"):
            with self.subTest(mutation=mutation):
                case_root = self.root / mutation
                case_root.mkdir()
                fixture = Fixture(case_root, [dict(number=423, sig="provider-aws", status="implementable", text=source(423))])
                if mutation == "bucket":
                    fixture.census["items"][0]["sig"] = "sig-aws"
                else:
                    fixture.census["items"].append(copy.deepcopy(fixture.census["items"][0]))
                fixture.refresh()
                with self.assertRaisesRegex(tool.Refusal, "INVALID_CENSUS"):
                    tool.prepare(**fixture.args())
                self.assertFalse((case_root / "out").exists())

    def test_duplicate_positive_identity_refused(self):
        fixture = Fixture(self.root, [dict(number=19, sig="sig-a", status="implemented", text=source()),
                                      dict(number=19, sig="sig-b", status="implemented", text=source())])
        with self.assertRaisesRegex(tool.Refusal, "DUPLICATE_KEP_ID"):
            tool.prepare(**fixture.args())
        self.assertFalse((self.root / "out").exists())

    def test_directory_disagreement_is_counted_without_reidentifying_or_aborting(self):
        for status in ("implementable", "provisional"):
            with self.subTest(status=status):
                root = self.root / status
                root.mkdir()
                items = [dict(number=1884, directory_number=2043, sig="sig-example", status=status,
                              text="Refused source body must not enter the input artifact."),
                         dict(number=19, sig="sig-example", status="implemented", text=source(19)),
                         dict(number=29, sig="provider-aws", status="implementable", text=source(29))]
                items.extend(dict(number=0, directory_number="0000", sig=sig, status="implemented", text=source(0))
                             for sig in ("sig-zero-a", "sig-zero-b", "sig-zero-c"))
                fixture = Fixture(root, items)
                result = tool.prepare(**fixture.args())
                raw, rows, manifest = self.load(root / "out")
                self.assertEqual(result["metadata_candidates"], 6)
                self.assertEqual(result["status_candidates"], 6 if status == "implementable" else 5)
                self.assertEqual(result["usable_inputs"], 2)
                self.assertEqual(result["refused"], 4)
                self.assertEqual(result["refused_by_reason"], {"DIRECTORY_ID_MISMATCH": 1, "NONPOSITIVE_ID": 3})
                decision = next(item for item in manifest["qualification"]["census"] if "2043-synthetic" in item["path"])
                self.assertEqual(decision["kep_number"], 1884)
                self.assertEqual(decision["reason"], "DIRECTORY_ID_MISMATCH")
                self.assertIsNone(decision["selection_rank"])
                self.assertFalse(decision["section_qualified"])
                self.assertEqual(decision["disposition"], "refused")
                self.assertEqual({row["kep_number"] for row in rows}, {19, 29})
                self.assertTrue(all(set(row) == tool.RECORD_KEYS and "target" not in row for row in rows))
                self.assertNotIn(b"Refused source body", raw)
                self.assertEqual(manifest["inputs_sha256"], tool.digest(raw))

    def test_directory_refusal_still_authenticates_metadata_and_unique_eligible_ids(self):
        for mutation, expected in (("census_id", "CENSUS_METADATA_MISMATCH"),
                                   ("status", "CENSUS_METADATA_MISMATCH"),
                                   ("hash", "METADATA_PIN_MISMATCH"),
                                   ("duplicate", "DUPLICATE_KEP_ID")):
            with self.subTest(mutation=mutation):
                root = self.root / mutation
                root.mkdir()
                fixture = Fixture(root, [dict(number=1884, directory_number=2043, sig="sig-example",
                                             status="implementable", text=source(1884)),
                                        dict(number=19, sig="sig-other", status="implemented", text=source(19))])
                item = fixture.census["items"][0]
                if mutation == "census_id":
                    item["kep_id"] = "1885"
                elif mutation == "status":
                    item["status"] = "provisional"
                elif mutation == "hash":
                    item["metadata_sha256"] = "0" * 64
                else:
                    fixture.census["items"][1]["kep_id"] = "1884"
                fixture.refresh()
                with self.assertRaisesRegex(tool.Refusal, expected):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_pinned_metadata_semantics_not_just_hash_including_ineligible(self):
        for index, (status, raw) in enumerate((
            ("implemented", b"kep-number: 29\nstatus: implemented\n"),
            ("implemented", b"kep-number: 19\nstatus: provisional\n"),
            ("provisional", b"kep-number: 19\nstatus: implemented\n"),
        )):
            with self.subTest(status=status, case=index):
                root = self.root / str(index)
                root.mkdir()
                fixture = Fixture(root, [dict(number=19, sig="sig-example", status=status, text=source())])
                path = fixture.census["items"][0]["path"]
                fixture.files[path] = raw
                fixture.make_archive()
                next(item for item in fixture.tree["tree"] if item["path"] == path)["sha"] = fixture.blob(raw)
                fixture.census["items"][0]["metadata_sha256"] = tool.digest(raw)
                fixture.census["archive_sha256"] = tool.digest(fixture.paths["archive"].read_bytes())
                fixture.refresh()
                with self.assertRaisesRegex(tool.Refusal, "CENSUS_METADATA_MISMATCH"):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_yaml_nodes_refuse_ambiguous_identity_without_constructing_dates(self):
        self.assertEqual(tool.metadata_identity(b"kep-number: '19'\nstatus: \"implemented\"\ncreation-date: 2025-15-01\n"), (19, "implemented"))
        for raw in (b"kep-number: 19\nstatus: implemented\nstatus: provisional\n",
                    b"kep-number: 19\nstatus: |\n  implemented\n",
                    b"kep-number: 19\nstatus: [implemented]\n",
                    b"kep-number: 19\nstatus: implemented\n<<: {status: provisional}\n",
                    b"kep-number: 19\nstatus: !!python/object:unknown {}\n"):
            with self.subTest(raw=raw), self.assertRaisesRegex(tool.Refusal, "INVALID_METADATA"):
                tool.metadata_identity(raw)

    def test_fence_comment_literals_do_not_hide_later_sections(self):
        for fence in ("```", "~~~~"):
            for newline in ("\n", "\r\n"):
                with self.subTest(fence=fence, newline=newline):
                    root = self.root / (str(len(list(self.root.iterdir()))))
                    root.mkdir()
                    text = source(newline=newline).replace("## Summary", newline.join((fence, "<!--", "## Non-Goals", fence, "## Summary")))
                    fixture = Fixture(root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
                    self.assertEqual(tool.prepare(**fixture.args())["usable_inputs"], 1)
                    _, rows, _ = self.load(root / "out")
                    tool.validate_record(rows[0], text)
        self.assertFalse(tool.visibly_nonempty("<!--\n```\n-->"))
        self.assertTrue(tool.visibly_nonempty("```\n<!-- literal text\n```\n"))
        _, sections = tool.source_sections("```html <!-- literal\ncode\n```\n## Goals\nReal objective\n")
        self.assertIn("Goals", sections)

    def test_unterminated_actual_comments_are_invisible_through_eof(self):
        self.assertFalse(tool.visibly_nonempty("<!-- secret comment\n### fake heading"))
        text = source().replace(f"Test synthetic endpoints. {SENTINEL}", "<!-- hidden comment without close")
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
        with self.assertRaises(tool.Refusal) as raised:
            tool.prepare(**fixture.args())
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"MISSING_OR_EMPTY_SUPPORT_SECTION": 1})
        _, sections = tool.source_sections("# Title\n<!--\n## Goals\nHidden objective\n")
        self.assertNotIn("Goals", sections)

    def test_optional_graduation_parent_preserves_original_spans(self):
        text = source().replace("## Graduation Criteria", "## Graduation Criteria (Optional) ###")
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
        self.assertEqual(tool.prepare(**fixture.args())["usable_inputs"], 1)
        _, rows, _ = self.load()
        tool.validate_record(rows[0], text)

    def test_closing_hashes_require_whitespace_and_title_is_exact(self):
        for suffix, qualifies in (("#", False), ("###", False), (" ###", True), ("\t##\t", True)):
            with self.subTest(suffix=suffix):
                text = source().replace("## Goals\n", "## Goals" + suffix + "\n")
                _, sections = tool.source_sections(text)
                self.assertEqual("Goals" in sections, qualifies)
        text = "# Title# ###\n## Goals\nObjective\n"
        title, _ = tool.source_sections(text)
        self.assertEqual(text[title["start"]:title["end"]], "Title#")

    def test_selected_title_is_not_a_goals_or_summary_section(self):
        for title_name in ("Goals", "Summary"):
            with self.subTest(title_name=title_name):
                text = source().replace("# Synthetic KEP 19 – café", "# " + title_name)
                title, sections = tool.source_sections(text)
                self.assertEqual(text[title["start"]:title["end"]], title_name)
                self.assertGreater(sections[title_name]["start"], title["end"])
        text = source().replace("# Synthetic KEP 19 – café", "# Goals").replace("## Goals\nBound synthetic scope 19.\n", "")
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
        with self.assertRaises(tool.Refusal) as raised:
            tool.prepare(**fixture.args())
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"INPUT_GOALS_MISSING": 1})

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
            "ambiguous_first_title": (source().replace("# Synthetic KEP 19 – café", "# First <!-- hidden --> title\n# Later title"), "AMBIGUOUS_INPUT_TITLE"),
            "empty_fenced_section": (source().replace("Do not expand unrelated work.", "```text\n```"), "MISSING_OR_EMPTY_SUPPORT_SECTION"),
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

    def test_fence_delimiters_do_not_count_as_content_but_literal_body_does(self):
        self.assertFalse(tool.visibly_nonempty("```text\n```\n"))
        self.assertFalse(tool.visibly_nonempty("~~~markdown\n \n~~~\n"))
        self.assertTrue(tool.visibly_nonempty("```text\nactual body\n```\n"))
        self.assertTrue(tool.visibly_nonempty("```text\n<!-- literal body -->\n```\n"))

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
        native_create = tool._WindowsDirectories.create_directory
        def race(api, parent, name):
            if name == raced.name:
                raced.mkdir()
                (raced / "sentinel").write_text("racer")
            return native_create(api, parent, name)
        with patch.object(tool._WindowsDirectories, "create_directory", race), self.assertRaisesRegex(tool.Refusal, "OUTPUT_EXISTS"):
            tool.prepare(**fixture.args(out=raced))
        self.assertEqual((raced / "sentinel").read_text(), "racer")
        self.assertFalse((raced / "inputs.jsonl").exists())

    def test_atomic_birth_blocks_output_and_new_ancestor_swap_before_first_identity_query(self):
        for target in ("output", "new_ancestor"):
            with self.subTest(target=target):
                root = self.root / target
                root.mkdir()
                fixture = Fixture(root)
                foreign, moved = root / "synthetic-foreign-empty", root / "moved-reservation"
                foreign.mkdir()
                out = root / "out" if target == "output" else root / "new-parent" / "out"
                attacked = out if target == "output" else out.parent
                native_create = tool._WindowsDirectories.create_directory
                attempted = False
                def swap(api, parent, name):
                    nonlocal attempted
                    handle = native_create(api, parent, name)
                    if name == attacked.name:
                        attempted = True
                        # This is the old gap: immediately after creation, before first identity query.
                        # The returned birth handle already denies rename, so the foreign object stays separate.
                        with self.assertRaises(PermissionError):
                            attacked.rename(moved)
                        with self.assertRaises(OSError):
                            foreign.rename(attacked)
                    return handle
                with patch.object(tool._WindowsDirectories, "create_directory", swap):
                    result = tool.prepare(**fixture.args(out=out))
                self.assertTrue(attempted)
                self.assertEqual(result["usable_inputs"], 1)
                self.assertEqual(list(foreign.iterdir()), [])
                self.assertFalse(moved.exists())
                self.assertTrue((out / "manifest.json").is_file())

    def test_foreign_directory_inserted_before_atomic_create_is_never_opened(self):
        for target, expected in (("output", "OUTPUT_EXISTS"), ("new_ancestor", "OUTPUT_PATH_RACE")):
            with self.subTest(target=target):
                root = self.root / target
                root.mkdir()
                fixture = Fixture(root)
                foreign = root / "synthetic-foreign-empty"
                foreign.mkdir()
                out = root / "out" if target == "output" else root / "new-parent" / "out"
                attacked = out if target == "output" else out.parent
                native_create = tool._WindowsDirectories.create_directory
                def swap(api, parent, name):
                    if name == attacked.name:
                        foreign.rename(attacked)
                    return native_create(api, parent, name)
                with patch.object(tool._WindowsDirectories, "create_directory", swap), self.assertRaisesRegex(tool.Refusal, expected):
                    tool.prepare(**fixture.args(out=out))
                self.assertEqual(list(attacked.iterdir()), [])

    def test_ancestor_swap_before_handle_acquisition_is_refused(self):
        fixture = Fixture(self.root)
        parent, moved = self.root / "publish-parent", self.root / "moved-parent"
        parent.mkdir()
        original = tool._Publication._take_directory
        def swap(publication, path, parent_handle, expected):
            if path == parent:
                parent.rename(moved)
                parent.mkdir()
                (parent / "sentinel").write_text("replacement")
            return original(publication, path, parent_handle, expected)
        with patch.object(tool._Publication, "_take_directory", swap), self.assertRaisesRegex(tool.Refusal, "OUTPUT_PATH_RACE"):
            tool.prepare(**fixture.args(out=parent / "out"))
        self.assertEqual((parent / "sentinel").read_text(), "replacement")
        self.assertEqual(list(moved.iterdir()), [])
        self.assertFalse((parent / "out").exists())

    def test_acquired_output_and_ancestor_swaps_cannot_redirect_publication(self):
        for target in ("output", "ancestor"):
            with self.subTest(target=target):
                root = self.root / target
                root.mkdir()
                fixture = Fixture(root)
                parent = root / "publish-parent"
                parent.mkdir()
                out = parent / "out"
                moved = root / "moved"
                original = tool._Publication._write_temp
                attempted = False
                def swap(publication, raw):
                    nonlocal attempted
                    if not attempted:
                        attempted = True
                        attacked = out if target == "output" else parent
                        with self.assertRaises(PermissionError):
                            attacked.rename(moved)
                    return original(publication, raw)
                with patch.object(tool._Publication, "_write_temp", swap):
                    tool.prepare(**fixture.args(out=out))
                self.assertTrue(attempted)
                self.assertTrue((out / "manifest.json").is_file())
                self.assertFalse(moved.exists())

    def test_interrupted_publication_has_no_manifest_and_refuses_retry(self):
        fixture = Fixture(self.root)
        original = tool.os.link
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic interruption")
            return original(*args, **kwargs)
        with patch.object(tool.os, "link", fail_second), self.assertRaises(OSError):
            tool.prepare(**fixture.args())
        self.assertTrue((self.root / "out" / "inputs.jsonl").is_file())
        self.assertFalse((self.root / "out" / "manifest.json").exists())
        with self.assertRaisesRegex(tool.Refusal, "OUTPUT_EXISTS"):
            tool.prepare(**fixture.args())

    def test_junction_ancestor_refused_without_foreign_writes(self):
        import subprocess
        fixture = Fixture(self.root)
        foreign, junction = self.root / "synthetic-foreign-empty", self.root / "junction"
        foreign.mkdir()
        creation = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(foreign)],
                                  capture_output=True, check=True, timeout=15)
        self.assertEqual(creation.returncode, 0)
        self.assertTrue(junction.is_junction())
        with self.assertRaisesRegex(tool.Refusal, "PROTECTED_OUTPUT_PATH"):
            tool.prepare(**fixture.args(out=junction / "out"))
        self.assertEqual(list(foreign.iterdir()), [])

    def test_artifact_junction_refused_before_target_read(self):
        import subprocess
        foreign, junction = self.root / "artifact-foreign", self.root / "artifact-junction"
        foreign.mkdir()
        raw = b"foreign synthetic artifact"
        (foreign / "artifact").write_bytes(raw)
        creation = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(foreign)],
                                  capture_output=True, text=True)
        self.assertEqual(creation.returncode, 0, creation.stderr)
        with patch.object(tool.os, "read", side_effect=AssertionError("must not read foreign target")), self.assertRaisesRegex(tool.Refusal, "ARTIFACT_REPARSE"):
            tool.pinned_file(junction / "artifact", tool.digest(raw))
        self.assertEqual((foreign / "artifact").read_bytes(), raw)

    def test_artifact_reads_hold_ancestors_and_deny_leaf_writers(self):
        parent = self.root / "artifact-parent"
        parent.mkdir()
        raw, path = b"synthetic pinned artifact", parent / "artifact"
        path.write_bytes(raw)
        original = tool.os.read
        attempts = []
        def checked_read(fd, count):
            with self.assertRaises(OSError):
                parent.rename(self.root / "displaced-artifact-parent")
            with self.assertRaises(OSError):
                with path.open("r+b") as writer:
                    writer.write(b"FOREIGN")
            attempts.append(True)
            return original(fd, count)
        with patch.object(tool.os, "read", side_effect=checked_read):
            self.assertEqual(tool.pinned_file(path, tool.digest(raw)), raw)
        self.assertTrue(attempts)
        self.assertEqual(path.read_bytes(), raw)

    def test_staged_files_deny_writers_and_preserve_final_hardlink(self):
        out = self.root / "staged-output"
        with tool._Publication(out) as publication:
            name = publication._write_temp(b"owned bytes")
            with self.assertRaises(OSError):
                with (out / name).open("r+b") as writer:
                    writer.write(b"FOREIGN")
            os.link(out / name, out / "inputs.jsonl")
        self.assertFalse((out / name).exists())
        self.assertEqual((out / "inputs.jsonl").read_bytes(), b"owned bytes")

    def test_cleanup_does_not_unlink_foreign_post_close_replacement(self):
        out = self.root / "cleanup-output"
        publication = tool._Publication(out)
        publication.__enter__()
        name = publication._write_temp(b"owned temporary")
        fd = publication.files[0][1]
        os.link(out / name, out / "inputs.jsonl")
        original_close = tool.os.close
        def inject_after_close(value):
            original_close(value)
            if value == fd:
                self.assertFalse((out / name).exists())
                (out / name).write_bytes(b"foreign replacement retained")
        with patch.object(tool.os, "close", side_effect=inject_after_close):
            publication.__exit__(None, None, None)
        self.assertEqual((out / name).read_bytes(), b"foreign replacement retained")
        self.assertEqual((out / "inputs.jsonl").read_bytes(), b"owned temporary")

    def test_foreign_manifest_collision_is_refused_without_foreign_deletion(self):
        # The completion-authority defect is deliberately unresolved, not qualified.
        out = self.root / "foreign-marker-output"
        original_link = tool.os.link
        foreign = b'{"foreign_complete":true}\n'
        def inject_manifest(src, dst, *args, **kwargs):
            if Path(dst).name == "manifest.json":
                Path(dst).write_bytes(foreign)
            return original_link(src, dst, *args, **kwargs)
        with self.assertRaises(FileExistsError):
            with tool._Publication(out) as publication:
                with patch.object(tool.os, "link", side_effect=inject_manifest):
                    publication.publish(b"owned input\n", b'{"state":"complete"}\n')
        self.assertEqual((out / "manifest.json").read_bytes(), foreign)
        self.assertEqual((out / "inputs.jsonl").read_bytes(), b"owned input\n")

    def test_dry_run_occupied_destination_remains_unchanged(self):
        fixture = Fixture(self.root)
        out = self.root / "occupied"
        out.mkdir()
        (out / "sentinel").write_bytes(b"retained foreign bytes")
        result = tool.prepare(**fixture.args(out=out, dry_run=True))
        self.assertTrue(result["dry_run"])
        self.assertEqual({path.name for path in out.iterdir()}, {"sentinel"})
        self.assertEqual((out / "sentinel").read_bytes(), b"retained foreign bytes")

    def test_context_includes_original_roles_but_objective_is_body_only(self):
        fixture = Fixture(self.root)
        tool.prepare(**fixture.args())
        row = json.loads((self.root / "out" / "inputs.jsonl").read_text().splitlines()[0])
        original = source()
        contexts = [block["text"] for block in row["input"]["context"]]
        for name in ("Summary", "Motivation", "Goals", "Non-Goals", "Test Plan"):
            self.assertTrue(any(text.startswith("## " + name + "\n") for text in contexts), name)
        self.assertTrue(any(text.startswith("## Notes/Constraints/Caveats (Optional)\n") for text in contexts))
        alpha = next(text for text in contexts if text.startswith("## Graduation Criteria\n"))
        self.assertIn("### Alpha\n", alpha)
        self.assertNotIn("### Beta", alpha)
        self.assertEqual(row["input"]["objective"], "Bound synthetic scope 19.")
        for block, span in zip(row["input"]["context"], row["context_spans"]):
            self.assertEqual(block["text"], original[span["start"]:span["end"]])
        empty = source().replace("Do not expand unrelated work.", "")
        title, sections = tool.source_sections(empty)
        span = sections["Non-Goals"]
        self.assertFalse(tool.visibly_nonempty(empty[span["body_start"]:span["end"]]))

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

    def test_actual_main_completed_result_binds_exact_two_file_bytes(self):
        from contextlib import redirect_stdout, redirect_stderr
        fixture = Fixture(self.root)
        argv = [part for key, path in fixture.paths.items() for part in ("--" + key, str(path))]
        argv += ["--policy-sha256", fixture.policy_sha, "--out", str(self.root / "out")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(tool, "PINS", fixture.pins), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(tool.main(argv), 0)
        result = tool.strict_json(stdout.getvalue().encode("utf-8"))
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result["protocol"], "kep-input-completion/v1")
        self.assertEqual(result["completion_status"], "completed")
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["policy_sha256"], fixture.policy_sha)
        out = self.root / "out"
        self.assertEqual({p.name for p in out.iterdir()}, {"inputs.jsonl", "manifest.json"})
        for name, field in (("inputs.jsonl", "inputs_sha256"), ("manifest.json", "manifest_sha256")):
            raw = (out / name).read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertEqual(result[field], tool.digest(raw))
            self.assertNotEqual(result[field], tool.digest(raw[:-1]))
        self.assertEqual(result["usable_inputs"], 1)
        # A caller-fabricated/modified artifact does not match the returned binding.
        (out / "inputs.jsonl").write_bytes(b"foreign changed bytes\n")
        self.assertNotEqual(result["inputs_sha256"], tool.digest((out / "inputs.jsonl").read_bytes()))

    def test_actual_main_dry_run_never_returns_publication_hashes(self):
        from contextlib import redirect_stdout, redirect_stderr
        fixture = Fixture(self.root)
        argv = [part for key, path in fixture.paths.items() for part in ("--" + key, str(path))]
        argv += ["--policy-sha256", fixture.policy_sha, "--out", str(self.root / "out"), "--dry-run"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(tool, "PINS", fixture.pins), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(tool.main(argv), 0)
        result = tool.strict_json(stdout.getvalue().encode("utf-8"))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["completion_status"], "not_completed")
        self.assertNotIn("inputs_sha256", result)
        self.assertNotIn("manifest_sha256", result)
        self.assertFalse((self.root / "out").exists())
        self.assertEqual(stderr.getvalue(), "")

    def test_actual_main_foreign_marker_collision_has_no_completion_output(self):
        from contextlib import redirect_stdout, redirect_stderr
        fixture = Fixture(self.root)
        out = self.root / "out"
        argv = [part for key, path in fixture.paths.items() for part in ("--" + key, str(path))]
        argv += ["--policy-sha256", fixture.policy_sha, "--out", str(out)]
        stdout, stderr = io.StringIO(), io.StringIO()
        original_link, foreign = tool.os.link, b'{"foreign_complete":true}\n'
        def inject_manifest(src, dst, *args, **kwargs):
            if Path(dst).name == "manifest.json":
                Path(dst).write_bytes(foreign)
            return original_link(src, dst, *args, **kwargs)
        with patch.object(tool, "PINS", fixture.pins), patch.object(tool.os, "link", side_effect=inject_manifest), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(tool.main(argv), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(tool.strict_json(stderr.getvalue().encode("utf-8")), {"refused": "INVALID_CONSTRUCTION_EVIDENCE"})
        self.assertEqual((out / "manifest.json").read_bytes(), foreign)
        self.assertTrue((out / "inputs.jsonl").exists())

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


class PublicationPlatformTests(unittest.TestCase):
    def test_non_windows_publication_refuses_without_any_directory_operation(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-publication-platform-") as folder:
            out = Path(folder) / "out"
            with patch.object(tool.os, "name", "posix"), patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")):
                with self.assertRaisesRegex(tool.Refusal, "OUTPUT_PLATFORM_UNQUALIFIED"):
                    with tool._Publication(out):
                        self.fail("unqualified publication allowed")
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
