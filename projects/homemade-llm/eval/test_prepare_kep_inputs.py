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

    def make_archive(self, extra=None, *, before=False):
        extras = extra if isinstance(extra, list) else [extra] if extra else []
        with tarfile.open(self.paths["archive"], "w:gz") as archive:
            def add_extras():
                for member in extras:
                    archive.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
            if before:
                add_extras()
            for path, raw in self.files.items():
                info = tarfile.TarInfo("enhancements-" + tool.COMMIT + "/" + path)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            if not before:
                add_extras()

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


# -- Rule v3 (length-budgeted, two-alias) synthetic fixtures. Nothing above
# this point is touched: v2's `source`/`Fixture`/`SENTINEL` stay exactly as
# they were, so every existing v2 test keeps exercising the exact same
# fixtures it always has. --
TEMPLATE_LINES = [
    "# KEP-NNNN: Template title", "",
    "## Summary", "", "Describe the summary here.", "",
    "## Motivation", "", "Describe the motivation here.", "",
    "## Goals", "", "Describe the goal here.", "",
    "## Non-Goals", "", "Describe the non-goal here.", "",
    "## Notes/Constraints/Caveats (Optional)", "", "Describe constraints here.", "",
    "## Graduation Criteria", "### Alpha", "", "Describe alpha criteria here.", "",
    "## Test Plan", "", "Describe the test plan here.", "",
]
TEMPLATE_BODY = "\n".join(TEMPLATE_LINES)


def v3_body(number=7000, newline="\n", **overrides):
    """A synthetic v3 KEP body. Every required/optional section keeps the
    pinned template's own boilerplate line as its first paragraph (an exact
    line match, so v3 template-line removal deletes it) followed by the
    author's real paragraph(s); pass a section's keyword (title, summary,
    motivation, goals, non_goals, notes, alpha, test_plan) as None to leave
    ONLY the boilerplate (nothing real added), or as a string to replace the
    real paragraph(s) that follow the boilerplate line."""
    def body(role_text, boiler):
        if role_text is None:
            return boiler
        return boiler + "\n\n" + role_text
    lines = [
        overrides.get("title", f"# Synthetic v3 KEP {number}"), "",
        "## Summary", "", body(overrides.get("summary", f"Short synthetic summary {number}."), "Describe the summary here."), "",
        "## Motivation", "", body(overrides.get("motivation", f"Motivating need {number}."), "Describe the motivation here."), "",
        "## Goals", "", body(overrides.get("goals", f"Bound synthetic scope {number}."), "Describe the goal here."), "",
        "## Non-Goals", "", body(overrides.get("non_goals", "Do not expand unrelated work."), "Describe the non-goal here."), "",
        "## Notes/Constraints/Caveats (Optional)", "", body(overrides.get("notes", "The synthetic platform might require support."), "Describe constraints here."), "",
        "## Graduation Criteria", "### Alpha", "", body(overrides.get("alpha", "Validate before enabling the feature."), "Describe alpha criteria here."), "",
        "## Test Plan", "", body(overrides.get("test_plan", f"Test synthetic endpoints. {SENTINEL}"), "Describe the test plan here."), "",
    ]
    return newline.join(lines)


def nest_section(body, heading, after_text, attach):
    """Move one level-2 section of a v3_body (from `heading` up to the next level-2 heading) so it becomes a level-3 heading placed directly after
    `after_text`, either as its own block (attach=False, blank-line separated) or joined to that paragraph (attach=True, no blank line)."""
    start = body.index(heading)
    following = body.find("\n## ", start + 1)
    end = following + 1 if following != -1 else len(body)
    block = body[start:end].replace(heading, "#" + heading, 1).strip("\n")
    rest = body[:start] + body[end:]
    at = rest.index(after_text) + len(after_text)
    return rest[:at] + ("\n" if attach else "\n\n") + block + "\n" + rest[at:]


def v3_policy(template, frozen_at="2020-01-01T00:00:00Z"):
    return {"protocol": "kep-input-selection/v3", "authority": tool.V3_AUTHORITY,
            "repository": tool.REPOSITORY, "commit": tool.COMMIT,
            "statuses": ["implemented", "implementable"], "creation_date_filter": None,
            "order": tool.ORDER, "frozen_at": frozen_at,
            "required_sections": list(tool.SUPPORT_SECTIONS), "section_aliases": tool.V3_SECTION_ALIASES,
            "alias_precedence": tool.V3_ALIAS_PRECEDENCE, "template": template, "trim": tool.V3_TRIM,
            "postconstruction_token_gate": tool.V3_TOKEN_GATE}


class V3Fixture(Fixture):
    """Same archive/tree/census/license machinery as Fixture, plus the pinned
    template member every v3 policy requires, and a v3 (not v2) policy. The
    template's real pinned sha256/git_blob never match the hardcoded
    production V3_TEMPLATE constant (that constant pins the real upstream
    kubernetes/enhancements template, unavailable here); every test using
    this fixture must call fixture.prepare(...), which patches
    tool.V3_TEMPLATE to this fixture's own synthetic pin for the duration
    of the call, exactly as the base Fixture's _pins parameter substitutes
    synthetic archive/tree/census/license pins."""
    def __init__(self, root, items=None, template_text=None, frozen_at="2020-01-01T00:00:00Z"):
        self.template_text = TEMPLATE_BODY if template_text is None else template_text
        super().__init__(root, items)
        raw_template = self.template_text.encode("utf-8")
        self.template_pin = {"path": tool.TEMPLATE_PATH, "sha256": tool.digest(raw_template), "git_blob": self.blob(raw_template)}
        self.policy = v3_policy(self.template_pin, frozen_at=frozen_at)
        self.refresh()

    def make_archive(self, extra=None, *, before=False):
        self.files[tool.TEMPLATE_PATH] = self.template_text.encode("utf-8")
        super().make_archive(extra, before=before)

    def prepare(self, **prepare_overrides):
        with patch.object(tool, "V3_TEMPLATE", self.template_pin):
            return tool.prepare(**self.args(**prepare_overrides))


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

    def test_beta_before_alpha_is_refused_not_cited_as_alpha_context(self):
        text = source().replace("### Alpha", "### Beta\nUnrelated earlier duties.\n### Alpha")
        with self.assertRaisesRegex(tool.Refusal, "ALPHA_PRECEDING_SIBLING"):
            tool.source_sections(text)
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=text)])
        with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as refusal:
            tool.prepare(**fixture.args(dry_run=True))
        self.assertEqual(refusal.exception.summary["usable_inputs"], 0)
        self.assertEqual(refusal.exception.summary["refused_by_reason"], {"ALPHA_PRECEDING_SIBLING": 1})
        self.assertFalse((self.root / "out").exists())

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

    @staticmethod
    def symlink_member(path):
        member = tarfile.TarInfo("enhancements-" + tool.COMMIT + "/" + path)
        member.type = tarfile.SYMTYPE
        member.linkname = "../../DO_NOT_READ_SYNTHETIC_LINK_TARGET"
        return member

    def refresh_link_archive(self, fixture, members, *, before=False):
        fixture.make_archive(members, before=before)
        fixture.census["archive_sha256"] = tool.digest(fixture.paths["archive"].read_bytes())
        fixture.refresh()

    def test_unrelated_symlinks_are_excluded_with_sorted_relative_diagnostics(self):
        fixture = Fixture(self.root)
        paths = ["unrelated/z-link", "unrelated/a-link"]
        self.refresh_link_archive(fixture, [self.symlink_member(path) for path in paths])
        result = tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertEqual(len(rows), 1)
        for diagnostic in (result, manifest["summary"], manifest["qualification"]):
            self.assertEqual(diagnostic["excluded_symlink_members"], 2)
            self.assertEqual(diagnostic["excluded_symlink_member_paths"], sorted(paths))
            self.assertNotIn("DO_NOT_READ_SYNTHETIC_LINK_TARGET", json.dumps(diagnostic))

    def test_no_symlink_has_zero_count_and_empty_paths(self):
        fixture = Fixture(self.root)
        result = tool.prepare(**fixture.args())
        _, _, manifest = self.load()
        for diagnostic in (result, manifest["summary"], manifest["qualification"]):
            self.assertEqual(diagnostic["excluded_symlink_members"], 0)
            self.assertEqual(diagnostic["excluded_symlink_member_paths"], [])

    def test_symlink_diagnostic_paths_bounded_but_all_links_counted(self):
        fixture = Fixture(self.root)
        paths = [f"unrelated/link-{i:04d}" for i in reversed(range(tool.MAX_EXCLUDED_PATHS + 3))]
        self.refresh_link_archive(fixture, [self.symlink_member(path) for path in paths])
        result = tool.prepare(**fixture.args())
        _, _, manifest = self.load()
        for diagnostic in (result, manifest["summary"], manifest["qualification"]):
            self.assertEqual(diagnostic["excluded_symlink_members"], tool.MAX_EXCLUDED_PATHS + 3)
            self.assertEqual(diagnostic["excluded_symlink_member_paths"], sorted(paths)[:tool.MAX_EXCLUDED_PATHS])

    def test_duplicate_regular_and_symlink_member_refused_in_both_orders(self):
        for before in (False, True):
            with self.subTest(link_before_regular=before):
                root = self.root / str(before)
                root.mkdir()
                fixture = Fixture(root)
                self.refresh_link_archive(fixture, self.symlink_member("LICENSE"), before=before)
                with self.assertRaisesRegex(tool.Refusal, "INVALID_ARCHIVE_MEMBER"):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_linked_readme_is_censused_while_regular_other_kep_is_retained(self):
        fixture = Fixture(self.root, [dict(number=19, sig="sig-example", status="implemented", text=source(19)),
                                     dict(number=29, sig="sig-example", status="implemented", text=source(29))])
        linked = "keps/sig-example/19-synthetic/README.md"
        fixture.files.pop(linked)
        self.refresh_link_archive(fixture, self.symlink_member(linked))
        result = tool.prepare(**fixture.args())
        _, rows, manifest = self.load()
        self.assertEqual([row["kep_number"] for row in rows], [29])
        decision = next(item for item in manifest["qualification"]["census"] if item["kep_number"] == 19)
        self.assertEqual(decision["reason"], "SYMLINK_MEMBER")
        self.assertEqual(result["refused_by_reason"], {"SYMLINK_MEMBER": 1})
        self.assertEqual(result["excluded_symlink_member_paths"], [linked])

    def test_only_linked_readme_refuses_with_retained_census_counter(self):
        fixture = Fixture(self.root)
        linked = "keps/sig-example/19-synthetic/README.md"
        fixture.files.pop(linked)
        self.refresh_link_archive(fixture, self.symlink_member(linked))
        with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as raised:
            tool.prepare(**fixture.args())
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"SYMLINK_MEMBER": 1})
        self.assertEqual(raised.exception.summary["excluded_symlink_members"], 1)
        self.assertEqual(raised.exception.summary["excluded_symlink_member_paths"], [linked])
        self.assertFalse((self.root / "out").exists())

    def test_license_and_metadata_symlinks_refuse_globally(self):
        for linked in ("LICENSE", "keps/sig-example/19-synthetic/kep.yaml"):
            with self.subTest(path=linked):
                root = self.root / ("license" if linked == "LICENSE" else "metadata")
                root.mkdir()
                fixture = Fixture(root)
                fixture.files.pop(linked)
                self.refresh_link_archive(fixture, self.symlink_member(linked))
                with self.assertRaisesRegex(tool.Refusal, "SYMLINK_MEMBER"):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_symlink_escaped_or_root_member_refused_without_target_read(self):
        for name in ("enhancements-" + tool.COMMIT, "enhancements-" + tool.COMMIT + "/../escape"):
            with self.subTest(name=name):
                root = self.root / ("root-link" if name.endswith(tool.COMMIT) else "escape-link")
                root.mkdir()
                fixture = Fixture(root)
                member = self.symlink_member("unused")
                member.name = name
                self.refresh_link_archive(fixture, member)
                with self.assertRaisesRegex(tool.Refusal, "INVALID_ARCHIVE_MEMBER"):
                    tool.prepare(**fixture.args())
                self.assertFalse((root / "out").exists())

    def test_archive_hardlinks_escapes_and_duplicate_members_are_refused(self):
        for kind in ("hardlink", "escape", "duplicate"):
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

    def test_windows_lexical_aliases_refused_before_artifact_or_output_access(self):
        fixture = Fixture(self.root)
        for component in ("heldout", "HELDOUT", "heldout.", "heldout "):
            with self.subTest(component=component):
                alias = self.root / component
                with patch.object(tool, "_read_artifact", side_effect=AssertionError("artifact access")), self.assertRaisesRegex(tool.Refusal, "PROTECTED_PATH"):
                    tool.pinned_file(alias / "artifact", tool.digest(b"synthetic"))
                with patch.object(tool, "pinned_file", side_effect=AssertionError("input access")), self.assertRaisesRegex(tool.Refusal, "PROTECTED_OUTPUT_PATH"):
                    tool.prepare(**fixture.args(out=alias / "out", dry_run=True))
                with patch.object(tool._WindowsDirectories, "open", side_effect=AssertionError("directory access")), self.assertRaisesRegex(tool.Refusal, "PROTECTED_OUTPUT_PATH"):
                    with tool._Publication(alias / "out"):
                        self.fail("protected publication allowed")
                self.assertFalse(alias.exists())

    def test_nonprotected_tilde_digit_path_reads_and_publishes(self):
        benign = self.root / "RUNNER~1"
        benign.mkdir()
        fixture = Fixture(benign)
        self.assertEqual(tool.pinned_file(fixture.paths["tree"], fixture.pins["tree"]), tool.json_bytes(fixture.tree))
        result = tool.prepare(**fixture.args())
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, manifest = self.load(benign / "out")
        self.assertEqual(len(rows), 1)
        self.assertEqual(manifest["summary"]["usable_inputs"], 1)

    def test_native_acquired_normalized_heldout_name_is_refused(self):
        # Only this temporary synthetic heldout directory is touched, never real custody.
        protected = self.root / "heldout"
        protected.mkdir()
        windows = tool._WindowsDirectories()
        handle = windows.create(str(protected), 0x80000000, 3, None, 3, 0x02200000, None)
        self.assertNotEqual(handle, windows.ctypes.c_void_p(-1).value)
        try:
            with self.assertRaisesRegex(tool.Refusal, "PROTECTED_PATH"):
                windows.guard_handle_path(handle)
        finally:
            windows.close(handle)
        self.assertEqual(list(protected.iterdir()), [])

    def test_native_resolved_alias_refused_before_read_or_publication(self):
        alias = self.root / "HELDOU~1"
        alias.mkdir()
        artifact = alias / "safe-artifact"
        artifact.write_bytes(b"synthetic")
        fixture = Fixture(self.root)
        def protected_name(_handle, buffer, _size, flags):
            self.assertEqual(flags, 0)
            buffer.value = str(self.root / "heldout" / "artifact")
            return len(buffer.value)
        original_init = tool._WindowsDirectories.__init__
        def init(windows):
            original_init(windows)
            windows.final_name = protected_name
        with patch.object(tool._WindowsDirectories, "__init__", init), patch.object(tool.os, "read", side_effect=AssertionError("content access")):
            with self.assertRaisesRegex(tool.Refusal, "PROTECTED_PATH"):
                tool.pinned_file(artifact, tool.digest(b"synthetic"))
        with patch.object(tool._WindowsDirectories, "__init__", init), patch.object(tool._WindowsDirectories, "create_directory", side_effect=AssertionError("publication access")):
            with self.assertRaisesRegex(tool.Refusal, "PROTECTED_PATH"):
                with tool._Publication(alias / "out"):
                    self.fail("protected acquired object allowed")
        self.assertFalse((alias / "out").exists())
        self.assertEqual(artifact.read_bytes(), b"synthetic")

    def test_native_final_name_resizes_and_failure_closes_without_fallback(self):
        windows = tool._WindowsDirectories()
        original = windows.final_name
        sizes = []
        def resized(handle, buffer, size, flags):
            sizes.append(size)
            self.assertEqual(flags, 0)
            return size + 1 if len(sizes) == 1 else original(handle, buffer, size, flags)
        with patch.object(windows, "final_name", side_effect=resized):
            handle = windows.open(self.root)
            windows.close(handle)
        self.assertEqual(sizes, [1024, 1026])
        with patch.object(windows, "final_name", return_value=0), patch.object(windows, "close", wraps=windows.close) as close:
            with self.assertRaises(OSError):
                windows.open(self.root)
            close.assert_called_once()

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


@unittest.skipUnless(os.name == "nt", "Two-file publication qualified on Windows only; no POSIX birth implementation")
class KepInputSelectionV3Tests(unittest.TestCase):
    """Rule v3 (length-budgeted, two-alias): a standalone class, not a subclass
    of PrepareKepInputsTests, so none of the v2 tests above are re-run here;
    those keep proving v2 unchanged on their own. Every fixture is synthetic."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-kep-inputs-v3-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, out=None):
        out = out or self.root / "out"
        raw = (out / "inputs.jsonl").read_bytes()
        return raw, [json.loads(line) for line in raw.splitlines()], json.loads((out / "manifest.json").read_bytes())

    # -- 1. v2 policy unchanged. --
    def test_v2_policy_unchanged_still_passes_and_yields_same_records(self):
        """The same fixture and assertions as the existing v2 happy-path test
        (PrepareKepInputsTests.test_target_free_shared_shape_unicode_spans_and_exact_hashes),
        run again here through the unmodified source()/Fixture helpers and the
        unmodified v2 branch of prepare(), to confirm the v3 additions above
        changed nothing about v2's behavior or output."""
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
        self.assertNotIn(b"\r\n", raw)
        self.assertNotIn("stage_counts", manifest)
        self.assertNotIn("stage_counts", result)

    # -- 2. Protocol separation. --
    def test_unknown_protocol_and_v3_keys_in_v2_policy_refuse(self):
        fixture = V3Fixture(self.root)
        unknown = dict(fixture.policy, protocol="kep-input-selection/v9")
        fixture.policy = unknown
        fixture.refresh()
        with patch.object(tool, "V3_TEMPLATE", fixture.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
                tool.prepare(**fixture.args())

        root = self.root / "v2-with-v3-keys"
        root.mkdir()
        v2fixture = Fixture(root)
        v2fixture.policy = dict(v2fixture.policy, section_aliases=tool.V3_SECTION_ALIASES)
        v2fixture.refresh()
        with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
            tool.prepare(**v2fixture.args())

    def test_v3_constant_mutations_and_bad_frozen_at_and_duplicate_key_refuse(self):
        root1 = self.root / "bad-alias"
        root1.mkdir()
        f1 = V3Fixture(root1)
        f1.policy = dict(f1.policy, section_aliases={"Non-Goals": ["Other Heading"]})
        f1.refresh()
        with patch.object(tool, "V3_TEMPLATE", f1.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
                tool.prepare(**f1.args())

        old_caps = {"objective": 400, "Non-Goals": 300, "Notes/Constraints/Caveats": 450, "Alpha Graduation Criteria": 450, "Test Plan": 500,
                    "Summary": 200, "Motivation": 200}

        def trim_without_allocation():
            t = dict(tool.V3_TRIM); del t["allocation"]; return t

        def alloc(**over):
            a = dict(tool.V3_ALLOCATION); a.update(over); return a

        def alloc_without(key):
            a = dict(tool.V3_ALLOCATION); del a[key]; return a
        mutations = {
            "old-fixed-caps": lambda p: dict(p, trim=dict(trim_without_allocation(), character_budgets=old_caps)),
            "both-schemes": lambda p: dict(p, trim=dict(tool.V3_TRIM, character_budgets=old_caps)),
            "old-authority-new-allocation": lambda p: dict(p, authority="issue299/comment6000111441"),
            "missing-mode": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc_without("mode"))),
            "missing-allocation": lambda p: dict(p, trim=trim_without_allocation()),
            "changed-fill-order": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(support_fill_order=list(reversed(tool.SUPPORT_SECTIONS))))),
            "changed-reservation-order": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(mandatory_reservation_order=["Non-Goals", "objective", *tool.SUPPORT_SECTIONS[1:]]))),
            "changed-mandatory-budget": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(mandatory_character_budget=2101))),
            "float-budget": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(mandatory_character_budget=2100.0))),
            "bool-budget": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(total_character_budget=True))),
            "int-for-bool-transfer": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(optional_budget_transfer=0))),
            "changed-optional-budget": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(optional_character_budgets={"Summary": 201, "Motivation": 200}))),
            "nested-unknown": lambda p: dict(p, trim=dict(tool.V3_TRIM, allocation=alloc(extra=1))),
            "missing-usable-floor": lambda p: dict(p, postconstruction_token_gate={k: v for k, v in tool.V3_TOKEN_GATE.items() if k != "minimum_usable_inputs"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(policy=label):
                root2 = self.root / ("policy-" + label)
                root2.mkdir()
                f2 = V3Fixture(root2)
                f2.policy = mutate(f2.policy)
                f2.refresh()
                with patch.object(tool, "V3_TEMPLATE", f2.template_pin):
                    with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
                        tool.prepare(**f2.args())

        root3 = self.root / "unknown-key"
        root3.mkdir()
        f3 = V3Fixture(root3)
        f3.policy = dict(f3.policy, extra_field=True)
        f3.refresh()
        with patch.object(tool, "V3_TEMPLATE", f3.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
                tool.prepare(**f3.args())

        root4 = self.root / "bad-frozen"
        root4.mkdir()
        f4 = V3Fixture(root4, frozen_at="2020-01-01T00:00:00+01:00")
        with patch.object(tool, "V3_TEMPLATE", f4.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "INVALID_TIMESTAMP"):
                tool.prepare(**f4.args())

        root5 = self.root / "dup-key"
        root5.mkdir()
        f5 = V3Fixture(root5)
        raw = tool.json_bytes(f5.policy)
        dup_raw = raw[:-1] + b',"protocol":"kep-input-selection/v3"}'
        f5.paths["policy"].write_bytes(dup_raw)
        f5.policy_sha = tool.digest(dup_raw)
        with patch.object(tool, "V3_TEMPLATE", f5.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "DUPLICATE_JSON_MEMBER"):
                tool.prepare(**f5.args())

    # -- 3. Alias precedence. --
    def test_alias_precedence_canonical_over_alias_and_heading_text_preserved(self):
        base = v3_body(7300)
        with_both = base.replace(
            "## Test Plan",
            "## Risks and Mitigations\n\nAlias-sourced content that must never surface.\n\n## Test Plan")
        fixture = V3Fixture(self.root, [dict(number=7300, sig="sig-example", status="implemented", text=with_both)])
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, _ = self.load()
        contexts = [block["text"] for block in rows[0]["input"]["context"]]
        self.assertTrue(any("The synthetic platform might require support." in text for text in contexts))
        self.assertFalse(any("Alias-sourced content" in text for text in contexts))

        root2 = self.root / "alias-only"
        root2.mkdir()
        alias_only = base.replace("## Notes/Constraints/Caveats (Optional)", "## Risks and Mitigations")
        fixture2 = V3Fixture(root2, [dict(number=7301, sig="sig-example", status="implemented", text=alias_only)])
        result2 = fixture2.prepare()
        self.assertEqual(result2["usable_inputs"], 1)
        _, rows2, _ = self.load(root2 / "out")
        contexts2 = [block["text"] for block in rows2[0]["input"]["context"]]
        self.assertTrue(any(text.startswith("## Risks and Mitigations") for text in contexts2))
        self.assertFalse(any("Notes/Constraints/Caveats" in text for text in contexts2))

    def test_alias_duplicate_canonical_duplicate_alias_and_substring_refuse(self):
        base = v3_body(7302)
        dup_canonical = base.replace(
            "## Test Plan",
            "## Notes/Constraints/Caveats (Optional)\n\nSecond canonical occurrence.\n\n## Test Plan")
        root1 = self.root / "dup-canonical"
        root1.mkdir()
        f1 = V3Fixture(root1, [dict(number=7302, sig="sig-example", status="implemented", text=dup_canonical)])
        with self.assertRaises(tool.Refusal) as raised1:
            f1.prepare()
        self.assertEqual(raised1.exception.summary["refused_by_reason"], {"AMBIGUOUS_SECTION": 1})

        dup_alias_source = base.replace("## Notes/Constraints/Caveats (Optional)", "## Risks and Mitigations").replace(
            "## Test Plan", "## Risks and Mitigations\n\nSecond alias occurrence.\n\n## Test Plan")
        root2 = self.root / "dup-alias"
        root2.mkdir()
        f2 = V3Fixture(root2, [dict(number=7303, sig="sig-example", status="implemented", text=dup_alias_source)])
        with self.assertRaises(tool.Refusal) as raised2:
            f2.prepare()
        self.assertEqual(raised2.exception.summary["refused_by_reason"], {"AMBIGUOUS_ALIAS": 1})

        substring = base.replace("## Notes/Constraints/Caveats (Optional)", "## Notes/Constraints/Caveats Overview")
        root3 = self.root / "substring"
        root3.mkdir()
        f3 = V3Fixture(root3, [dict(number=7304, sig="sig-example", status="implemented", text=substring)])
        with self.assertRaises(tool.Refusal) as raised3:
            f3.prepare()
        self.assertEqual(raised3.exception.summary["refused_by_reason"], {"MISSING_OR_EMPTY_SUPPORT_SECTION": 1})

    def test_alias_post_removal_empty_canonical_does_not_fall_back(self):
        base = v3_body(7305, notes=None)
        with_healthy_alias = base.replace(
            "## Test Plan",
            "## Risks and Mitigations\n\nHealthy alias content that must never be used.\n\n## Test Plan")
        fixture = V3Fixture(self.root, [dict(number=7305, sig="sig-example", status="implemented", text=with_healthy_alias)])
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"EMPTY_AFTER_TEMPLATE_REMOVAL": 1})

    # -- 4. Comments and fences. --
    def test_fence_and_comment_headings_are_not_real_sections(self):
        body = v3_body(7400)
        hidden = "\n".join(["```", "## Non-Goals", "```", "<!--", "## Risks and Mitigations", "-->"])
        text = body.replace("## Non-Goals", hidden + "\n\n## Non-Goals")
        fixture = V3Fixture(self.root, [dict(number=7400, sig="sig-example", status="implemented", text=text)])
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, _ = self.load()
        tool.validate_record(rows[0], text)

    def test_surviving_comment_text_does_not_satisfy_a_role(self):
        text = v3_body(7401, non_goals="<!-- Do not expand unrelated work. -->")
        fixture = V3Fixture(self.root, [dict(number=7401, sig="sig-example", status="implemented", text=text)])
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"EMPTY_AFTER_TEMPLATE_REMOVAL": 1})

    # -- 5. Template removal. --
    def test_template_only_body_refuses_empty_after_removal(self):
        text = v3_body(7500, non_goals=None)
        fixture = V3Fixture(self.root, [dict(number=7500, sig="sig-example", status="implemented", text=text)])
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"EMPTY_AFTER_TEMPLATE_REMOVAL": 1})

    def test_near_match_template_line_survives_no_whitespace_folding(self):
        variants = [
            ("Describe the non-goal here!", "punctuation differs by one character"),
            ("Describe the non-goal here. ", "trailing space differs"),
        ]
        for index, (near_match, label) in enumerate(variants):
            with self.subTest(label=label):
                root = self.root / f"near-match-{index}"
                root.mkdir()
                number = 7501 + index
                text = v3_body(number, non_goals=near_match)
                fixture = V3Fixture(root, [dict(number=number, sig="sig-example", status="implemented", text=text)])
                result = fixture.prepare()
                self.assertEqual(result["usable_inputs"], 1)
                _, rows, _ = self.load(root / "out")
                contexts = [block["text"] for block in rows[0]["input"]["context"]]
                self.assertTrue(any(near_match in text for text in contexts))

    # -- 6. Paragraph budgets. --
    def _mandatory_total(self, row):
        """Code points of the objective plus every context item (callers use bodies whose optional sections are boilerplate only)."""
        return len(row["input"]["objective"]) + sum(len(block["text"]) for block in row["input"]["context"])

    def _run_one(self, name, number, text):
        root = self.root / name
        root.mkdir()
        fixture = V3Fixture(root, [dict(number=number, sig="sig-example", status="implemented", text=text)])
        return fixture, root

    def test_mandatory_minima_exactly_2100_fit_and_2101_refuse(self):
        base = v3_body(7600, summary=None, motivation=None)
        fixture, root = self._run_one("baseline", 7600, base)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        baseline = self._mandatory_total(rows[0])
        default_first = "Do not expand unrelated work."
        fill = len(default_first) + (tool.V3_MANDATORY_BUDGET - baseline)
        exact = v3_body(7601, non_goals="N" * fill, summary=None, motivation=None)
        fixture, root = self._run_one("exact", 7601, exact)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        self.assertEqual(self._mandatory_total(rows[0]), tool.V3_MANDATORY_BUDGET)
        for span, block in zip(rows[0]["context_spans"], rows[0]["input"]["context"]):
            self.assertEqual(block["text"], exact[span["start"]:span["end"]])
        over = v3_body(7602, non_goals="N" * (fill + 1), summary=None, motivation=None)
        fixture, _ = self._run_one("over", 7602, over)
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"MANDATORY_MINIMA_OVER_BUDGET": 1})

    def test_reserve_before_fill_and_ordered_support_fill_without_skip(self):
        base = v3_body(7603, summary=None, motivation=None)
        fixture, root = self._run_one("fill-baseline", 7603, base)
        fixture.prepare()
        _, rows, _ = self.load(root / "out")
        remaining = tool.V3_MANDATORY_BUDGET - self._mandatory_total(rows[0])
        first = "Do not expand unrelated work."
        # Non-Goals: a second paragraph exactly filling the remaining pool (its own newline included) is taken
        exact_second = "E" * (remaining - 1)
        text = v3_body(7604, non_goals=first + "\n\n" + exact_second, summary=None, motivation=None)
        fixture, root = self._run_one("fill-exact", 7604, text)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        self.assertIn(exact_second + "\n", [b["text"] for b in rows[0]["input"]["context"]])
        self.assertEqual(self._mandatory_total(rows[0]), tool.V3_MANDATORY_BUDGET)
        # one character over stops Non-Goals; a shorter later Non-Goals paragraph is NOT taken; the next role still fills from what remains;
        # Test Plan's reserved first paragraph is present although Non-Goals had more text than the pool
        over_second = "O" * remaining
        later_short = "Later short non-goal never selected."
        notes_second = "Second notes paragraph fits."
        text = v3_body(7605, non_goals=first + "\n\n" + over_second + "\n\n" + later_short,
                       notes="The synthetic platform might require support.\n\n" + notes_second, summary=None, motivation=None)
        fixture, root = self._run_one("fill-stop", 7605, text)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        contexts = [b["text"] for b in rows[0]["input"]["context"]]
        self.assertFalse(any(over_second in c for c in contexts))
        self.assertFalse(any(later_short in c for c in contexts))
        self.assertIn(notes_second + "\n", contexts)
        self.assertTrue(any(SENTINEL in c for c in contexts))
        self.assertLessEqual(self._mandatory_total(rows[0]), tool.V3_MANDATORY_BUDGET)
        # the objective never takes a later Goals paragraph
        text = v3_body(7606, goals="Only the first goal.\n\nA later goal paragraph.", summary=None, motivation=None)
        fixture, root = self._run_one("objective-first-only", 7606, text)
        fixture.prepare()
        _, rows, _ = self.load(root / "out")
        self.assertEqual(rows[0]["input"]["objective"], "Only the first goal.")
        self.assertFalse(any("A later goal paragraph." in b["text"] for b in rows[0]["input"]["context"]))

    def test_optional_budgets_are_separate_200_and_never_transfer(self):
        heading = "## Summary\n"
        fit = "S" * (tool.V3_OPTIONAL_BUDGETS["Summary"] - len(heading) - len("\n"))
        text = v3_body(7607, summary=fit, motivation=None)
        fixture, root = self._run_one("summary-200", 7607, text)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        contexts = [b["text"] for b in rows[0]["input"]["context"]]
        self.assertIn(heading, contexts)
        self.assertIn(fit + "\n", contexts)
        text = v3_body(7608, summary=fit + "S", motivation=None)
        fixture, root = self._run_one("summary-201", 7608, text)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        contexts = [b["text"] for b in rows[0]["input"]["context"]]
        self.assertNotIn(heading, contexts)
        self.assertFalse(any(fit in c for c in contexts))
        # an unused optional budget never rescues mandatory minima that exceed 2,100
        base = v3_body(7609, summary=None, motivation=None)
        fixture, root = self._run_one("no-transfer-baseline", 7609, base)
        fixture.prepare()
        _, rows, _ = self.load(root / "out")
        fill = len("Do not expand unrelated work.") + (tool.V3_MANDATORY_BUDGET - self._mandatory_total(rows[0])) + 1
        fixture, _ = self._run_one("no-transfer-over", 7610, v3_body(7610, non_goals="N" * fill, summary=None, motivation=None))
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"MANDATORY_MINIMA_OVER_BUDGET": 1})

    def test_code_point_accounting_crlf_astral_quotes_and_order(self):
        base = v3_body(7611, newline="\r\n", summary=None, motivation=None)
        fixture, root = self._run_one("crlf-baseline", 7611, base)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        row = rows[0]
        self.assertTrue(any("\r\n" in b["text"] for b in row["input"]["context"]))
        starts = [s["start"] for s in row["context_spans"]]
        self.assertEqual(starts, sorted(starts))
        baseline = self._mandatory_total(row)
        default_first = "Do not expand unrelated work."
        unit = "\U0001F600\"\\"                                     # an astral character, a quote and a backslash: 3 code points
        fill = len(default_first) + (tool.V3_MANDATORY_BUDGET - baseline)
        lead = "Visible words "                                    # a run of symbols alone is not visible content under the existing rule
        body = lead + unit * ((fill - len(lead)) // 3) + "x" * ((fill - len(lead)) % 3)
        exact = v3_body(7612, newline="\r\n", non_goals=body, summary=None, motivation=None)
        fixture, root = self._run_one("crlf-astral-exact", 7612, exact)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        self.assertEqual(self._mandatory_total(rows[0]), tool.V3_MANDATORY_BUDGET)
        for span, block in zip(rows[0]["context_spans"], rows[0]["input"]["context"]):
            self.assertEqual(block["text"], exact[span["start"]:span["end"]])
        fixture, _ = self._run_one("crlf-astral-over", 7613, v3_body(7613, newline="\r\n", non_goals=body + "x", summary=None, motivation=None))
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"MANDATORY_MINIMA_OVER_BUDGET": 1})

    def test_nested_alpha_prefix_over_450_kept_whole_and_charged_once(self):
        intro = "Graduation overview. " * 30
        text = v3_body(7614, summary=None, motivation=None).replace("## Graduation Criteria\n### Alpha", "## Graduation Criteria\n\n" + intro.strip() + "\n\n### Alpha", 1)
        fixture, root = self._run_one("alpha-prefix", 7614, text)
        self.assertEqual(fixture.prepare()["usable_inputs"], 1)
        _, rows, _ = self.load(root / "out")
        row = rows[0]
        prefixes = [b["text"] for b in row["input"]["context"] if b["text"].startswith("## Graduation Criteria")]
        self.assertEqual(len(prefixes), 1)
        self.assertGreater(len(prefixes[0]), 450)
        self.assertTrue(prefixes[0].rstrip().endswith("### Alpha"))
        for span, block in zip(row["context_spans"], row["input"]["context"]):
            self.assertEqual(block["text"], text[span["start"]:span["end"]])
        self.assertLessEqual(self._mandatory_total(row), tool.V3_MANDATORY_BUDGET)

    def test_summary_and_motivation_optional_when_only_boilerplate(self):
        text = v3_body(7603, summary=None, motivation=None)
        fixture = V3Fixture(self.root, [dict(number=7603, sig="sig-example", status="implemented", text=text)])
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, _ = self.load()
        contexts = [block["text"] for block in rows[0]["input"]["context"]]
        self.assertFalse(any("Describe the summary here." in text for text in contexts))
        self.assertFalse(any("Describe the motivation here." in text for text in contexts))
        self.assertFalse(any(text.startswith("## Summary") for text in contexts))
        self.assertFalse(any(text.startswith("## Motivation") for text in contexts))

    # -- 7. Original spans. --
    def test_context_text_equals_source_slice_and_goals_never_duplicated(self):
        number = 7700
        text = v3_body(number)
        fixture = V3Fixture(self.root, [dict(number=number, sig="sig-example", status="implemented", text=text)])
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, _ = self.load()
        row = rows[0]
        tool.validate_record(row, text)
        self.assertEqual(tool.prompt_template.context_shape_errors(row["input"]["context"]), [])
        self.assertEqual(tool.prompt_template.input_contract(row["input"]), "D-M2-10")
        for span, block in zip(row["context_spans"], row["input"]["context"]):
            self.assertEqual(block["text"], text[span["start"]:span["end"]])
        goals_text = f"Bound synthetic scope {number}."
        self.assertEqual(row["input"]["objective"], goals_text)
        self.assertFalse(any(block["text"].strip() == goals_text for block in row["input"]["context"]))

    def test_removed_template_line_gap_yields_separate_context_items(self):
        number = 7701
        sandwiched = "Real line A.\nDescribe the non-goal here.\nReal line B."
        text = v3_body(number, non_goals=sandwiched)
        fixture = V3Fixture(self.root, [dict(number=number, sig="sig-example", status="implemented", text=text)])
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, rows, _ = self.load()
        row = rows[0]
        contexts = [block["text"] for block in row["input"]["context"]]
        # Each retained run is the exact original excerpt, newline included; the removed template line between them leaves two separate spans.
        self.assertIn("Real line A.\n", contexts)
        self.assertIn("Real line B.\n", contexts)
        self.assertFalse(any("Real line A." in block and "Real line B." in block for block in contexts))
        for span, block in zip(row["context_spans"], row["input"]["context"]):
            self.assertEqual(block["text"], text[span["start"]:span["end"]])

    def test_goals_internal_gap_refuses_objective_span_not_contiguous(self):
        number = 7702
        sandwiched_goals = "Real goal A.\nDescribe the goal here.\nReal goal B."
        text = v3_body(number, goals=sandwiched_goals)
        fixture = V3Fixture(self.root, [dict(number=number, sig="sig-example", status="implemented", text=text)])
        with self.assertRaises(tool.Refusal) as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"OBJECTIVE_SPAN_NOT_CONTIGUOUS": 1})

    # -- 8. Pinned inputs. --
    def test_changed_template_hash_or_blob_refuses(self):
        for mutated_field, replacement in (("sha256", "0" * 64), ("git_blob", "0" * 40)):
            with self.subTest(field=mutated_field):
                root = self.root / mutated_field
                root.mkdir()
                local = V3Fixture(root)
                wrong_pin = dict(local.template_pin)
                wrong_pin[mutated_field] = replacement
                local.policy = v3_policy(wrong_pin)
                local.refresh()
                with patch.object(tool, "V3_TEMPLATE", wrong_pin):
                    with self.assertRaisesRegex(tool.Refusal, "TEMPLATE_PIN_MISMATCH"):
                        tool.prepare(**local.args())

    def test_changed_policy_hash_refuses(self):
        fixture = V3Fixture(self.root)
        fixture.policy_sha = "0" * 64
        with self.assertRaisesRegex(tool.Refusal, "ARTIFACT_PIN_MISMATCH"):
            tool.prepare(**fixture.args())

    def test_changed_archive_tree_census_license_refuse_under_v3(self):
        for name in ("archive", "tree", "census", "license"):
            with self.subTest(name=name):
                root = self.root / name
                root.mkdir()
                fixture = V3Fixture(root)
                fixture.pins = dict(fixture.pins, **{name: "0" * 64})
                with self.assertRaisesRegex(tool.Refusal, "ARTIFACT_PIN_MISMATCH"):
                    tool.prepare(**fixture.args())

    def test_existing_output_never_overwritten_under_v3(self):
        out = self.root / "out"
        out.mkdir()
        (out / "manifest.json").write_bytes(b"old-v2-manifest")
        fixture = V3Fixture(self.root)
        with self.assertRaisesRegex(tool.Refusal, "OUTPUT_EXISTS"):
            fixture.prepare()
        self.assertEqual((out / "manifest.json").read_bytes(), b"old-v2-manifest")

    # -- 9. Stage counts. --
    def test_stage_counts_funnel_and_absent_for_v2(self):
        text_a = v3_body(8001)
        before, after = text_a.split("## Non-Goals", 1)
        _, rest = after.split("## Notes/Constraints/Caveats (Optional)", 1)
        text_a = before + "## Notes/Constraints/Caveats (Optional)" + rest
        items = [
            dict(number=8001, sig="sig-example", status="implemented", text=text_a),
            dict(number=8002, sig="sig-example", status="implemented", text=v3_body(8002, non_goals=None)),
            dict(number=8003, sig="sig-example", status="implemented", text=v3_body(8003, non_goals="Z" * tool.V3_MANDATORY_BUDGET)),
            dict(number=8004, sig="sig-example", status="implemented", text=v3_body(8004)),
        ]
        fixture = V3Fixture(self.root, items)
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        expected = {"status_candidates": 4, "qualified_before_template_removal": 3,
                    "qualified_after_template_removal": 2, "paragraph_qualified": 1, "usable_inputs": 1}
        self.assertEqual(result["stage_counts"], expected)
        _, _, manifest = self.load()
        self.assertEqual(manifest["stage_counts"], expected)

    def test_no_usable_inputs_refusal_carries_v3_stage_counts_and_v2_summary_unchanged(self):
        fixture = V3Fixture(self.root, [dict(number=8101, sig="sig-example", status="implemented", text=v3_body(8101, non_goals=None))])
        with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["stage_counts"],
                         {"status_candidates": 1, "qualified_before_template_removal": 1, "qualified_after_template_removal": 0,
                          "paragraph_qualified": 0, "usable_inputs": 0})
        root = self.root / "v2"
        root.mkdir()
        v2text = source().replace("## Non-Goals\nDo not expand unrelated work.\n", "")
        v2fixture = Fixture(root, [dict(number=19, sig="sig-example", status="implemented", text=v2text)])
        with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as raised:
            tool.prepare(**v2fixture.args())
        self.assertNotIn("stage_counts", raised.exception.summary)

    # -- 9b. Overlapping source spans (Copilot 4190535796; the root counterexample of #299 6007550254). --
    def test_span_overlap_helper_positive_adjacent_disjoint_and_nested(self):
        span = lambda start, end: {"start": start, "end": end}
        self.assertTrue(tool._spans_overlap([span(0, 10), span(9, 12)]))        # one shared character
        self.assertTrue(tool._spans_overlap([span(5, 20), span(0, 30)]))        # nested
        self.assertTrue(tool._spans_overlap([span(3, 4), span(3, 4)]))          # identical
        self.assertFalse(tool._spans_overlap([span(0, 10), span(10, 12)]))      # adjacent: touching is allowed
        self.assertFalse(tool._spans_overlap([span(20, 25), span(0, 5), span(5, 20)]))  # disjoint, any input order
        self.assertFalse(tool._spans_overlap([span(0, 10)]))

    def test_objective_context_overlap_refuses_root_counterexample(self):
        """Non-Goals nested as ### and joined to the Goals paragraph: the objective paragraph then contains the Non-Goals heading, which is also
        emitted as Non-Goals context (root receipt: objective [200,241) and context [228,242) shared 13 characters at fe28857d)."""
        text = nest_section(v3_body(7101), "## Non-Goals", "Bound synthetic scope 7101.", attach=True)
        fixture = V3Fixture(self.root, [dict(number=7101, sig="sig-example", status="implemented", text=text)])
        with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as raised:
            fixture.prepare()
        self.assertEqual(raised.exception.summary["refused_by_reason"], {"CONTEXT_SPAN_OVERLAP": 1})
        self.assertFalse((self.root / "out").exists())

    def test_context_context_overlap_refuses_for_support_and_optional_roles(self):
        cases = {
            "support-in-support-separate": ("## Test Plan", "Do not expand unrelated work.", False),
            "support-in-support-attached": ("## Test Plan", "Do not expand unrelated work.", True),
            "support-in-optional-summary": ("## Notes/Constraints/Caveats (Optional)", "Short synthetic summary 7101.", False),
            "support-in-optional-motivation-attached": ("## Test Plan", "Motivating need 7101.", True),
        }
        for name, (heading, after, attach) in cases.items():
            with self.subTest(case=name):
                root = self.root / name
                root.mkdir()
                text = nest_section(v3_body(7101), heading, after, attach)
                fixture = V3Fixture(root, [dict(number=7101, sig="sig-example", status="implemented", text=text)])
                with self.assertRaisesRegex(tool.Refusal, "NO_USABLE_INPUTS") as raised:
                    fixture.prepare()
                self.assertEqual(raised.exception.summary["refused_by_reason"], {"CONTEXT_SPAN_OVERLAP": 1})

    def test_valid_and_disjoint_nesting_controls_still_emit_with_exact_disjoint_spans(self):
        bodies = {"ordinary": v3_body(7101),
                  "nested-in-goals-separate": nest_section(v3_body(7101), "## Non-Goals", "Bound synthetic scope 7101.", attach=False)}
        for name, text in bodies.items():
            with self.subTest(case=name):
                root = self.root / name
                root.mkdir()
                fixture = V3Fixture(root, [dict(number=7101, sig="sig-example", status="implemented", text=text)])
                result = fixture.prepare()
                self.assertEqual(result["usable_inputs"], 1)
                _, rows, _ = self.load(root / "out")
                row = rows[0]
                spans = [row["objective_span"]] + row["context_spans"]
                self.assertFalse(tool._spans_overlap(spans))
                self.assertEqual(row["input"]["objective"], text[row["objective_span"]["start"]:row["objective_span"]["end"]])
                for item, span in zip(row["input"]["context"], row["context_spans"]):
                    self.assertEqual(item["text"], text[span["start"]:span["end"]])

    # -- 10. Token-gate declaration. --
    def test_token_gate_required_recorded_and_no_tokenizer_import(self):
        fixture = V3Fixture(self.root)
        result = fixture.prepare()
        self.assertEqual(result["usable_inputs"], 1)
        _, _, manifest = self.load()
        self.assertEqual(manifest["policy"]["postconstruction_token_gate"], tool.V3_TOKEN_GATE)

        root = self.root / "missing-gate"
        root.mkdir()
        local = V3Fixture(root)
        missing = dict(local.policy)
        del missing["postconstruction_token_gate"]
        local.policy = missing
        local.refresh()
        with patch.object(tool, "V3_TEMPLATE", local.template_pin):
            with self.assertRaisesRegex(tool.Refusal, "INVALID_POLICY"):
                tool.prepare(**local.args())

        source_text = Path(tool.__file__).read_text(encoding="utf-8")
        for forbidden in ("tiktoken", "tokenizers", "sentencepiece", "transformers"):
            self.assertNotIn(forbidden, source_text)


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
