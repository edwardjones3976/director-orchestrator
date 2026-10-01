# [[OpenAI Codex | GPT-6 | 2026-09-30 | 23:54 UTC | BEGIN]]
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
from test_generate_candidates import gguf_fixture  # noqa: E402

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
                    {"source": {"__typename": "Issue", "number": 66,
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
        earlier["__typename"] = "PullRequest"
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
        earlier["__typename"] = "PullRequest"
        earlier["closingIssuesReferences"]["nodes"] = [{
            "number": 77, "repository": {"nameWithOwner": "edwardjones3976/director-orchestrator"}}]
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def test_url_and_unbound_closing_claims_cannot_hide_earlier_work(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        earlier["__typename"] = "PullRequest"
        claims = ("Fixes https://github.com/edwardjones3976/director-orchestrator/issues/77",
                  "Issue: https://github.com/edwardjones3976/director-orchestrator/issues/77",
                  "Fixes GH-77", "Fixes [assignment](https://github.com/edwardjones3976/director-orchestrator/issues/77)",
                  "Issue: [assignment](https://github.com/edwardjones3976/director-orchestrator/issues/77)",
                  "Implements #77", "Handles #77", "Discussion of #77; not a work claim")
        for index, claim in enumerate(claims):
            with self.subTest(claim=claim):
                self.calls.clear()
                self.out = self.root / f"claim-{index}.jsonl"
                earlier["body"] = claim
                with self.assertRaises(SystemExit):
                    self.generate([self.default_scenario()])
                self.assertEqual(self.calls, [])
                self.assertFalse(self.out.exists())

    def test_documented_reference_forms_bind_the_first_work(self):
        work = self.github_record["issue"]["timelineItems"]["nodes"][1]["source"]
        for ref in ("#77", "edwardjones3976/director-orchestrator#77", "GH-77",
                    "https://github.com/edwardjones3976/director-orchestrator/issues/77"):
            with self.subTest(ref=ref):
                work["body"] = f"FIXES: {ref}"
                self.assertTrue(self.generate([self.default_scenario()], dry_run=True)["dry_run"])
                self.assertEqual(self.calls, [])

    def test_explicit_github_issue_kind_preserves_the_original_route(self):
        row = self.default_scenario()
        row["source_provenance"]["kind"] = "github_issue"
        self.assertTrue(self.generate([row], dry_run=True)["dry_run"])
        self.github_record["issue"]["lastEditedAt"] = "2026-09-28T00:00:00Z"
        with self.assertRaises(SystemExit):
            self.generate([row], dry_run=True)
        self.assertEqual(self.calls, [])

    def test_edited_out_work_claim_is_ambiguous_and_refused(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        earlier["__typename"] = "PullRequest"
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

    def test_earlier_foreign_work_reference_is_not_silently_ignored(self):
        earlier = self.github_record["issue"]["timelineItems"]["nodes"][0]["source"]
        earlier.update(__typename="PullRequest", body="Implements edwardjones3976/director-orchestrator#77",
                       repository={"nameWithOwner": "another/repository"})
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_renamed_issue_title_is_not_immutable_input(self):
        self.github_record["issue"]["timelineItems"]["nodes"].append({
            "__typename": "RenamedTitleEvent"})
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()])
        self.assertEqual(self.calls, [])

    def local_options(self, response: dict | None = None) -> dict:
        model = next(iter(generate_rows.LOCAL_MODELS))
        model_dir = self.root / "models"
        model_dir.mkdir()
        gguf = model_dir / "qwen.gguf"
        gguf.write_bytes(gguf_fixture())
        license_file = self.root / "LICENSE"
        license_file.write_text("Test-only license bytes", encoding="utf-8")
        gguf_hash = hashlib.sha256(gguf.read_bytes()).hexdigest()
        license_hash = hashlib.sha256(license_file.read_bytes()).hexdigest()
        repo, revision = model.split("@")
        conversion = self.root / "conversion.json"
        conversion.write_text(json.dumps({"status": "complete", "repo": repo, "revision": revision,
            "gguf": [{"path": str(gguf), "quant": "Q4_K_M", "bytes": gguf.stat().st_size,
                      "sha256": gguf_hash}]}), encoding="utf-8")
        models = patch.dict(generate_rows.LOCAL_MODELS, {model: (gguf_hash, license_hash)})
        models.start()
        self.addCleanup(models.stop)
        binding = patch.object(generate_rows.local_transport, "loaded_model_binding", return_value={
            "identifier": "qwen-local", "model_key": "qwen-catalog", "sha256": gguf_hash,
            "catalog_path": str(gguf)})
        self.local_probe = binding.start()
        self.addCleanup(binding.stop)
        http = patch.object(generate_rows.local_transport, "http_json", return_value=response or {
            "id": "local-response-1", "model": "qwen-local", "usage": {"completion_tokens": 40},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(TARGET)}}]})
        self.local_http = http.start()
        self.addCleanup(http.stop)
        return {"model": "qwen-local", "model_gguf": gguf, "conversion_receipt": conversion,
                "license_file": license_file, "base_url": "http://127.0.0.1:1234/v1",
                "lms_models_dir": model_dir, "receipts_dir": self.root / "receipts" / "local-model"}

    def generate_local(self, options: dict, row: dict | None = None, **kwargs):
        return generate_rows.generate([row or self.default_scenario()], provider="local_model",
            set_a=self.set_a, set_b=self.set_b, out=self.out, terms_verified_at=UTC,
            generated_at=UTC, source_root=self.root, dry_run=kwargs.pop("dry_run", False),
            scrub=lambda folder: "reports/test-scan.json", local_options=options, **kwargs)

    def test_local_row_receipt_model_and_request_bindings(self):
        options = self.local_options()
        report = self.generate_local(options)
        self.assertEqual(report, {"dry_run": False, "rows": 1, "receipts": 1})
        self.local_http.assert_called_once()
        body = self.local_http.call_args.args[2]
        self.assertEqual((body["temperature"], body["n"]), (0, 1))
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        schema = body["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["properties"]), set(TARGET))
        self.assertEqual(schema["properties"]["acceptance_checks"]["minItems"], 1)
        row = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(row["source_model"], next(iter(generate_rows.LOCAL_MODELS)))
        self.assertEqual(row["terms_tier"], "open_weight_license")
        receipt_path = self.root / row["receipt_ref"]
        self.assertEqual(row["receipt_sha256"], hashlib.sha256(receipt_path.read_bytes()).hexdigest())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual((receipt["model"], receipt["input"], receipt["target"]),
                         (row["source_model"], row["input"], row["target"]))
        self.assertEqual(receipt["api_cost_usd"], 0)
        self.assertEqual(receipt["response_id"], "local-response-1")
        training = generate_rows.prompt_template.render_messages(row["input"], row["target"])
        sent = body["messages"]
        self.assertEqual(sent[1], training[1])
        self.assertEqual(sent[0]["content"],
                         training[0]["content"] + generate_rows.local_transport.QWEN3_PROMPT)
        self.assertEqual(receipt["prompt_sha256"],
                         hashlib.sha256(json.dumps(sent, sort_keys=True).encode()).hexdigest())

    def test_local_dry_run_never_probes_or_writes(self):
        options = self.local_options()
        options["base_url"] = "https://remote.invalid/v1"
        self.assertTrue(self.generate_local(options, dry_run=True)["dry_run"])
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()
        self.assertFalse(options["receipts_dir"].exists())
        self.assertFalse(self.out.exists())

    def test_local_source_gate_and_remote_endpoint_precede_inference(self):
        options = self.local_options()
        row = self.default_scenario()
        row.pop("source_provenance")
        with self.assertRaises(SystemExit):
            self.generate_local(options, row)
        for url in ("https://remote.invalid/v1", "http://127.0.0.1:1235/v1",
                    "http://user:secret@127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1?q=1",
                    "http://127.0.0.1:bad/v1"):
            options["base_url"] = url
            with self.subTest(url=url), self.assertRaises(SystemExit):
                self.generate_local(options)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()

    def test_local_missing_options_fail_without_a_probe(self):
        options = self.local_options()
        for invalid in ({}, dict(options, receipts_dir=None), dict(options, model=None),
                        {"receipts_dir": options["receipts_dir"]}):
            with self.subTest(options=invalid), self.assertRaises(SystemExit):
                self.generate_local(invalid)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()

    def test_local_wrong_conversion_license_and_gguf_send_no_request(self):
        options = self.local_options()
        original = options["conversion_receipt"].read_bytes()
        for key, value in (("status", "partial"), ("revision", "0" * 40), ("repo", "wrong/repo")):
            conversion = json.loads(original)
            conversion[key] = value
            options["conversion_receipt"].write_text(json.dumps(conversion), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(SystemExit):
                self.generate_local(options)
        options["conversion_receipt"].write_bytes(original)
        license_bytes = options["license_file"].read_bytes()
        options["license_file"].write_text("Wrong license", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        options["license_file"].write_bytes(license_bytes)
        with options["model_gguf"].open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()

    def test_local_bad_completions_leave_failure_receipt_and_never_retry(self):
        options = self.local_options()
        for index, message in enumerate((
                {"content": json.dumps(TARGET), "reasoning_content": "hidden"},
                {"content": json.dumps(dict(TARGET, extra=[]))},
                {"content": '{"constraints": [], "constraints": []}'},
                {"content": json.dumps(dict(TARGET, acceptance_checks=[]))},
                {"content": "<think>hidden</think>" + json.dumps(TARGET)})):
            response = {"model": "qwen-local", "choices": [{"finish_reason": "stop", "message": message}]}
            self.local_http.return_value = response
            self.local_http.reset_mock()
            with self.subTest(index=index), self.assertRaises(SystemExit):
                self.generate_local(options)
            self.local_http.assert_called_once()
            self.assertFalse(self.out.exists())
        self.local_http.return_value = {"model": "qwen-local", "choices": [{"finish_reason": "length", "message": {"content": json.dumps(TARGET)}}]}
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        self.local_http.side_effect = TimeoutError("one request")
        self.local_http.reset_mock()
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        self.local_http.assert_called_once()
        receipts = list(options["receipts_dir"].glob("*.json"))
        self.assertEqual(len(receipts), 7)
        self.assertTrue(all(json.loads(path.read_text())["status"] == "failed" for path in receipts))

    def test_local_missing_receipt_is_not_a_valid_row(self):
        row = self.default_scenario()
        caller = lambda *args: generate_rows.DispatchResult(json.dumps(TARGET), self.root / "receipts/local/absent.json",
                                                           next(iter(generate_rows.LOCAL_MODELS)))
        with self.assertRaises(SystemExit):
            self.generate_local({}, row, dispatch=caller)
        self.assertFalse(self.out.exists())

    def test_local_receipt_collision_preserves_bytes_and_sends_nothing(self):
        options = self.local_options()
        options["receipts_dir"].mkdir(parents=True)
        collision = options["receipts_dir"] / "local-20260928T200000Z-existing.json"
        collision.write_bytes(b"original receipt")
        with patch.object(generate_rows.local_transport, "utc_now", return_value=UTC), \
                patch.object(generate_rows.secrets, "token_hex", return_value="existing"):
            with self.assertRaises(SystemExit):
                self.generate_local(options)
        self.assertEqual(collision.read_bytes(), b"original receipt")
        self.local_http.assert_not_called()

    def test_local_nonzero_reasoning_tokens_are_refused(self):
        options = self.local_options({"model": "qwen-local", "usage": {"completion_tokens_details": {"reasoning_tokens": 1}},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(TARGET)}}]})
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        self.local_http.assert_called_once()
        self.assertFalse(self.out.exists())

    def test_local_response_model_must_match_verified_binding(self):
        options = self.local_options({"model": "different-local-model", "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(TARGET)}}]})
        with self.assertRaises(SystemExit):
            self.generate_local(options)
        self.local_http.assert_called_once()
        self.assertFalse(self.out.exists())

    def test_local_oversized_packet_stops_before_model_probe(self):
        options = self.local_options()
        row = self.default_scenario()
        row["input"]["context"][0]["text"] = "unique_context " * 2000
        body = ASSIGNMENT_BODY + row["input"]["context"][0]["text"]
        source = self.root / row["input"]["context"][0]["source_ref"]
        source.write_text(body, encoding="utf-8", newline="\n")
        row["input"]["context"][0]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.github_record["issue"]["body"] = body
        with self.assertRaises(SystemExit):
            self.generate_local(options, row)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()

    def test_cloud_live_cli_route_stays_held(self):
        with patch.object(generate_rows, "live_dispatch") as call:
            with self.assertRaises(SystemExit):
                generate_rows.generate([self.default_scenario()], provider="openai", set_a=self.set_a,
                    set_b=self.set_b, out=self.out, terms_verified_at=UTC, generated_at=UTC,
                    source_root=self.root, dry_run=False)
            call.assert_not_called()
        self.assertFalse(self.out.exists())


class LicensedSpecificationTests(unittest.TestCase):
    """A synthetic fixture stands in for the externally reviewed, pinned capture."""

    local_options = GenerateRowsTests.local_options

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.specs = self.root / "specs"
        self.specs.mkdir()
        self.review_root = self.root / "review-receipts"
        (self.review_root / "xai").mkdir(parents=True)
        self.commit = "a" * 40
        self.description = ("Simulate a bank account supporting opening/closing, withdrawals, and deposits.\n"
                            "Watch out for concurrent transactions!\n"
                            "Operations against a closed account must fail.\nRun the test file.\n")
        (self.specs / "bank-account.md").write_bytes(self.description.encode())
        (self.specs / "LICENSE").write_bytes(b"MIT test-only source license")
        self.entry = {
            "source_id": f"exercism/{self.commit}/bank-account",
            "source_repository": "exercism/problem-specifications", "source_commit": self.commit,
            "upstream_commit_time": "2019-12-31T12:28:34Z", "captured_at": "2026-09-27T11:00:00Z",
            "path": "exercises/bank-account/description.md", "source_ref": "specs/bank-account.md",
            "source_url": f"https://github.com/exercism/problem-specifications/blob/{self.commit}/exercises/bank-account/description.md",
            "source_sha256": hashlib.sha256(self.description.encode()).hexdigest(),
            "source_git_blob": self.blob(self.description.encode()), "license": "MIT",
            "license_sha256": hashlib.sha256((self.specs / "LICENSE").read_bytes()).hexdigest(),
            "destination": "training_candidates_only", "source_kind": "independently_licensed_specification",
            "targets_generated": 0,
        }
        api = "https://api.github.com/repos/exercism/problem-specifications"
        self.official = {
            "official_commit": {"sha": self.commit, "url": f"{api}/commits/{self.commit}", "commit": {
                "committer": {"date": self.entry["upstream_commit_time"]},
                "tree": {"sha": "b" * 40, "url": f"{api}/git/trees/{'b' * 40}"}}},
            "official_tree": {"sha": self.commit, "url": f"{api}/git/trees/{self.commit}",
                "truncated": False, "tree": [
                    {"path": self.entry["path"], "mode": "100644", "type": "blob",
                     "sha": self.entry["source_git_blob"], "size": len(self.description.encode())},
                    {"path": "LICENSE", "mode": "100644", "type": "blob",
                     "sha": self.blob((self.specs / "LICENSE").read_bytes()),
                     "size": (self.specs / "LICENSE").stat().st_size}]},
            "observed_at": "2026-09-27T11:10:00Z",
        }
        self.admission = {"source_admission": "ACCEPT_FOR_SOURCE_FACTS", "response_id": "independent-response",
            "review_receipt_ref": "xai/review.json", "pilot_suitable": ["bank-account"], "pilot_hold": [],
            "targets_accepted": 0, "admitted_at": "2026-09-27T11:20:00Z"}
        self.receipt = {"provider": "xai", "model": "grok-4.6", "http_status": 200,
                        "response_status": "completed", "response_id": "independent-response"}
        self.row = {"source_group": "licensed/exercism/problem-specifications/bank-account", "split": "train",
            "source_provenance": {"kind": "licensed_specification", "source_id": self.entry["source_id"],
                "manifest_ref": "specs/manifest.jsonl", "admission_ref": "specs/admission.json",
                "official_capture_ref": "specs/official.json", "license_ref": "specs/LICENSE",
                "admitted_at": self.admission["admitted_at"]},
            "input": {"title": "bank-account", "repository": "exercism/problem-specifications",
                "objective": "Simulate a bank account supporting opening/closing, withdrawals, and deposits.",
                "context": [{"kind": "assignment_packet", "text": self.description,
                    "source_ref": self.entry["source_ref"], "source_sha256": self.entry["source_sha256"]}]}}
        self.pins = patch.dict(generate_rows.LICENSED_SPECIFICATION_ADMISSIONS, {}, clear=True)
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.write_evidence()
        self.github = patch.object(generate_rows, "github_chronology", side_effect=AssertionError("not an issue"))
        self.github.start()
        self.addCleanup(self.github.stop)

    @staticmethod
    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    def write_evidence(self):
        """Pin fixture bytes as an independent admission, never a live approval."""
        manifest = (json.dumps(self.entry) + "\n").encode()
        official = json.dumps(self.official).encode()
        self.admission.update(manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                              official_capture_sha256=hashlib.sha256(official).hexdigest())
        prior_answer = self.admission.get("review_answer")
        self.admission["review_answer"] = "**ACCEPT_FOR_SOURCE_FACTS** " + self.admission["manifest_sha256"] + " " + self.commit
        if self.receipt.get("answer") in (None, prior_answer):
            self.receipt["answer"] = self.admission["review_answer"]
        receipt = json.dumps(self.receipt).encode()
        self.admission["review_receipt_sha256"] = hashlib.sha256(receipt).hexdigest()
        admission = json.dumps(self.admission).encode()
        for name, raw in (("manifest.jsonl", manifest), ("official.json", official), ("admission.json", admission)):
            (self.specs / name).write_bytes(raw)
        (self.review_root / "xai/review.json").write_bytes(receipt)
        digest = hashlib.sha256(admission).hexdigest()
        self.row["source_provenance"]["admission_sha256"] = digest
        generate_rows.LICENSED_SPECIFICATION_ADMISSIONS[digest] = (
            self.admission["manifest_sha256"], self.admission["official_capture_sha256"],
            self.admission["review_receipt_sha256"], frozenset({"bank-account"}))

    def generate(self, rows=None, **kwargs):
        return generate_rows.generate(rows or [self.row], provider=kwargs.pop("provider", "local_model"),
            set_a=self.set_a, set_b=self.set_b, out=self.out, source_root=self.root,
            source_review_receipts_root=self.review_root, terms_verified_at=UTC,
            generated_at=kwargs.pop("generated_at", UTC), dry_run=kwargs.pop("dry_run", True), **kwargs)

    def test_valid_spec_dry_run_needs_no_issue_pr_probe_or_transport(self):
        with patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(), {"dry_run": True, "rows": 0, "receipts": 0})
            probe.assert_not_called()
            transport.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_valid_spec_local_receipt_preserves_source_binding_and_row_kind(self):
        target = {"non_goals": ["Do not allow operations against closed accounts"],
                  "constraints": ["Watch out for concurrent transactions"],
                  "acceptance_checks": ["Operations against a closed account must fail"],
                  "evidence_requirements": ["Run the test file"]}
        options = self.local_options({"model": "qwen-local", "usage": {}, "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(target)}}]})
        self.generate(dry_run=False, local_options=options, scrub=lambda folder: "reports/scan.json")
        row = json.loads(self.out.read_bytes())
        receipt = json.loads((self.root / row["receipt_ref"]).read_bytes())
        self.assertEqual(row["source_kind"], "provider_exemplar")
        self.assertEqual(row["source_provider"], "local_model")
        self.assertEqual(receipt["source_provenance"], self.row["source_provenance"])
        self.assertEqual(receipt["input"]["context"][0]["text"], self.description)

    def test_unknown_unadmitted_or_cloud_source_is_refused(self):
        for key, value in (("kind", "arbitrary_spec"), ("admission_sha256", "0" * 64),
                           ("admission_sha256", [])):
            original = self.row["source_provenance"][key]
            self.row["source_provenance"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                self.generate()
            self.row["source_provenance"][key] = original
        with self.assertRaises(SystemExit):
            self.generate(provider="openai")

    def test_changed_bytes_are_refused_before_any_transport(self):
        paths = [self.specs / name for name in ("admission.json", "manifest.jsonl", "official.json",
                                               "LICENSE", "bank-account.md")]
        paths.append(self.review_root / "xai/review.json")
        with patch.object(generate_rows, "prepare_local_model") as probe:
            for path in paths:
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.subTest(path=path.name), self.assertRaises(SystemExit):
                    self.generate(dry_run=False)
                path.write_bytes(original)
            probe.assert_not_called()

    def test_invalid_chronology_and_generation_metadata_are_refused(self):
        for field, value in (("upstream_commit_time", "2020-01-01T00:00:00Z"),
                             ("captured_at", "2019-12-30T00:00:00Z"),
                             ("captured_at", "2026-09-27T12:00:00Z"),
                             ("captured_at", "2026-02-30T00:00:00Z")):
            original = self.entry[field]
            self.entry[field] = value
            self.official["official_commit"]["commit"]["committer"]["date"] = self.entry["upstream_commit_time"]
            self.write_evidence()
            with self.subTest(field=field, value=value), self.assertRaises(SystemExit):
                self.generate()
            self.entry[field] = original
        self.official["official_commit"]["commit"]["committer"]["date"] = self.entry["upstream_commit_time"]
        for value in ("2026-09-27T10:00:00Z", "2099-01-01T00:00:00Z"):
            self.admission["admitted_at"] = value
            self.row["source_provenance"]["admitted_at"] = value
            self.write_evidence()
            with self.subTest(admitted=value), self.assertRaises(SystemExit):
                self.generate()
        self.admission["admitted_at"] = "2026-09-27T11:20:00Z"
        self.row["source_provenance"]["admitted_at"] = self.admission["admitted_at"]
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate(generated_at="2026-09-27T11:19:59Z")

    def test_unsupported_repository_solution_path_or_held_slug_is_refused(self):
        for field, value in (("source_repository", "someone/unreviewed"),
                             ("path", "exercises/bank-account/canonical-data.json"),
                             ("destination", "evaluation"), ("source_kind", "owner_seed")):
            original = self.entry[field]
            self.entry[field] = value
            self.write_evidence()
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.generate()
            self.entry[field] = original
        self.admission["pilot_hold"] = ["bank-account"]
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate()

    def test_unauthenticated_verdict_and_wrong_official_blob_are_refused(self):
        for field, value in (("response_id", "different-response"), ("provider", "openai"),
                             ("http_status", 0), ("answer", "ACCEPT invented")):
            original = self.receipt[field]
            self.receipt[field] = value
            self.write_evidence()
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.generate()
            self.receipt[field] = original
        self.official["official_tree"]["tree"][0]["sha"] = "0" * 40
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate()

    def test_path_escape_and_ungrounded_title_objective_context_are_refused(self):
        for key, value in (("manifest_ref", "../manifest.jsonl"), ("admission_ref", "C:/admission.json"),
                           ("official_capture_ref", "specs/missing.json")):
            original = self.row["source_provenance"][key]
            self.row["source_provenance"][key] = value
            with self.subTest(key=key), self.assertRaises(SystemExit):
                self.generate()
            self.row["source_provenance"][key] = original
        for key, value in (("title", "invented title"), ("objective", "Deploy an unmentioned database")):
            original = self.row["input"][key]
            self.row["input"][key] = value
            with self.subTest(key=key), self.assertRaises(SystemExit):
                self.generate()
            self.row["input"][key] = original
        self.row["input"]["context"][0]["text"] = "Run the test file."
        with self.assertRaises(SystemExit):
            self.generate()

    def test_heldout_identity_and_stable_group_split_remain_gates(self):
        original = self.row["source_group"]
        self.row["source_group"] = "ew-alpha"
        with self.assertRaises(SystemExit):
            self.generate()
        self.row["source_group"] = original
        dev = json.loads(json.dumps(self.row))
        dev["split"] = "dev"
        with self.assertRaises(SystemExit):
            self.generate([self.row, dev])
        self.row["source_group"] += "/" + self.commit
        with self.assertRaises(SystemExit):
            self.generate()

    def test_heldout_source_digest_remains_a_gate(self):
        metadata = self.set_a / "metadata/ew-alpha.meta.json"
        record = json.loads(metadata.read_text())
        record["source_sha256"] = self.entry["source_sha256"]
        metadata.write_text(json.dumps(record), encoding="utf-8")
        with patch.object(generate_rows, "prepare_local_model") as probe:
            with self.assertRaises(SystemExit):
                self.generate(dry_run=False)
            probe.assert_not_called()

    def test_source_artifact_symlink_and_size_guard(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(SystemExit):
                generate_rows._source_artifact(self.root, "specs/LICENSE", "test")
        (self.specs / "large.json").write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaises(SystemExit):
            generate_rows._source_artifact(self.root, "specs/large.json", "test")

    def test_actual_inference_clock_cannot_precede_admission(self):
        options = self.local_options()
        with patch.object(generate_rows.local_transport, "utc_now", side_effect=[UTC, "2026-09-27T11:00:00Z"]):
            with self.assertRaises(SystemExit):
                self.generate(dry_run=False, local_options=options)
        self.local_http.assert_not_called()
        self.assertFalse(options["receipts_dir"].exists())


class LearnyounodeAdmissionTests(unittest.TestCase):
    """Offline, test-only captures stand in for the closed reviewed source batch."""

    blob = staticmethod(LicensedSpecificationTests.blob)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.review_root = self.root / "reviews"
        (self.review_root / "xai").mkdir(parents=True)
        self.commit = generate_rows.LEARNYOUNODE_COMMIT
        self.repository = generate_rows.LEARNYOUNODE_REPOSITORY
        self.verification = "4. **Verify that your solution to the lesson is correct** by running `learnyounode verify program.js`."
        self.source_bytes = {
            "README.md": ("Generic workshop instructions.\n" + self.verification + "\nOther material not admitted as context.\n").encode(),
            "LICENSE.md": b"MIT test-only source license",
        }
        for slug in sorted(generate_rows.LEARNYOUNODE_SOURCES):
            self.source_bytes[slug + ".problem.md"] = f"Complete the {slug} exercise.\nCheck with {{appname}} verify {slug}.js.\n".encode()
        self.manifest = []
        self.tree = []
        for ref, raw in self.source_bytes.items():
            path = ref if ref in ("README.md", "LICENSE.md") else f"exercises/{ref.removesuffix('.problem.md')}/problem.md"
            (self.root / ref).write_bytes(raw)
            self.manifest.append({"source_id": f"{self.repository}/{self.commit}/{path}",
                "source_repository": self.repository, "source_commit": self.commit,
                "source_tree": "b" * 40, "path": path, "source_ref": ref,
                "source_url": f"https://github.com/{self.repository}/blob/{self.commit}/{path}",
                "source_sha256": hashlib.sha256(raw).hexdigest(), "source_git_blob": self.blob(raw),
                "license": "MIT", "license_sha256": hashlib.sha256(self.source_bytes["LICENSE.md"]).hexdigest(),
                "upstream_commit_time": "2020-06-19T15:49:14Z", "captured_at": "2026-09-27T11:00:00.123456+00:00",
                "source_kind": "independently_licensed_specification", "destination": "training_candidates_only",
                "targets_generated": 0})
            self.tree.append({"path": path, "sha": self.blob(raw), "mode": "100644", "type": "blob", "size": len(raw)})
        api = f"https://api.github.com/repos/{self.repository}"
        self.commit_record = {"sha": self.commit, "url": f"{api}/commits/{self.commit}", "commit": {
            "committer": {"date": "2020-06-19T15:49:14Z"}, "tree": {"sha": "b" * 40, "url": f"{api}/git/trees/{'b' * 40}"}}}
        self.tree_record = {"sha": self.commit, "url": f"{api}/git/trees/{self.commit}", "truncated": False, "tree": self.tree}
        self.admission = {"source_facts_verdict": "ACCEPT_FOR_SOURCE_FACTS", "pinned_commit": self.commit,
            "suitable": sorted(generate_rows.LEARNYOUNODE_SOURCES), "targets_generated": 0, "targets_accepted": 0,
            "admitted_at": "2026-09-27T11:20:00Z", "response_id": "independent-response", "review_receipt_ref": "xai/review.json"}
        self.receipt = {"provider": "xai", "model": "grok-4.6", "http_status": 200,
            "response_status": "completed", "response_id": "independent-response"}
        self.pins = patch.dict(generate_rows.LEARNYOUNODE_ADMISSIONS, {}, clear=True)
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.write_evidence()

    def write_evidence(self):
        manifest = "".join(json.dumps(entry) + "\n" for entry in self.manifest).encode()
        commit = json.dumps(self.commit_record).encode()
        tree = json.dumps(self.tree_record).encode()
        self.admission["manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
        answer = "**TERMINAL: ACCEPT_FOR_SOURCE_FACTS** " + self.admission["manifest_sha256"] + " " + self.commit
        self.admission["review_answer"] = answer
        self.receipt["answer"] = answer
        receipt = json.dumps(self.receipt).encode()
        self.admission["review_receipt_sha256"] = hashlib.sha256(receipt).hexdigest()
        admission = json.dumps(self.admission).encode()
        for name, raw in (("manifest.jsonl", manifest), ("commit.json", commit), ("tree.json", tree), ("admission.json", admission)):
            (self.root / name).write_bytes(raw)
        (self.review_root / "xai/review.json").write_bytes(receipt)
        self.digest = hashlib.sha256(admission).hexdigest()
        generate_rows.LEARNYOUNODE_ADMISSIONS[self.digest] = (
            self.admission["manifest_sha256"], hashlib.sha256(commit).hexdigest(),
            hashlib.sha256(tree).hexdigest(), self.admission["review_receipt_sha256"])

    def scenario(self, slug="baby_steps"):
        entry = next(item for item in self.manifest if item["path"] == f"exercises/{slug}/problem.md")
        readme = next(item for item in self.manifest if item["path"] == "README.md")
        return {"source_group": f"licensed/{self.repository}/{slug}", "split": "train",
            "source_provenance": {"kind": "licensed_specification", "source_id": entry["source_id"],
                "admission_ref": "admission.json", "admission_sha256": self.digest,
                "manifest_ref": "manifest.jsonl", "official_capture_ref": "commit.json", "official_tree_ref": "tree.json",
                "license_ref": "LICENSE.md", "admitted_at": self.admission["admitted_at"]},
            "input": {"repository": self.repository, "title": slug, "objective": f"Complete the {slug} exercise.",
                "context": [{"kind": "assignment_packet", "source_ref": entry["source_ref"],
                    "source_sha256": entry["source_sha256"], "text": self.source_bytes[entry["source_ref"]].decode()},
                    {"kind": "assignment_packet", "source_ref": "README.md", "source_sha256": readme["source_sha256"],
                     "text": self.verification}]}}

    def generate(self, rows=None, **kwargs):
        return generate_rows.generate(rows or [self.scenario()], provider=kwargs.pop("provider", "local_model"),
            set_a=self.set_a, set_b=self.set_b, out=self.out, source_root=self.root,
            source_review_receipts_root=self.review_root, terms_verified_at=UTC, generated_at=UTC,
            dry_run=kwargs.pop("dry_run", True), **kwargs)

    def test_all_three_sources_validate_without_probe_transport_receipts_or_rows(self):
        rows = [self.scenario(slug) for slug in sorted(generate_rows.LEARNYOUNODE_SOURCES)]
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(rows), {"dry_run": True, "rows": 0, "receipts": 0})
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_tampered_evidence_fails_before_probe(self):
        files = [self.root / name for name in ("manifest.jsonl", "admission.json", "commit.json", "tree.json",
            "README.md", "LICENSE.md", "baby_steps.problem.md")]
        files.append(self.review_root / "xai/review.json")
        with patch.object(generate_rows, "prepare_local_model") as probe:
            for path in files:
                raw = path.read_bytes()
                path.write_bytes(raw + b"tampered")
                with self.subTest(path=path.name), self.assertRaises(SystemExit):
                    self.generate(dry_run=False)
                path.write_bytes(raw)
            probe.assert_not_called()

    def test_no_fabricated_admission_or_cloud_target_route(self):
        row = self.scenario()
        row["source_provenance"]["admission_sha256"] = "0" * 64
        with self.assertRaises(SystemExit):
            self.generate([row])
        with self.assertRaises(SystemExit):
            self.generate(provider="openai")

    def test_unsupported_sources_context_and_groups_fail_closed(self):
        for key, value in (("source_id", f"{self.repository}/{self.commit}/README.md"),
                           ("official_tree_ref", "../tree.json"), ("admitted_at", "2099-01-01T00:00:00Z")):
            row = self.scenario()
            row["source_provenance"][key] = value
            with self.subTest(key=key), self.assertRaises(SystemExit):
                self.generate([row])
        for change in ("README", "placeholder", "version_group", "heldout", "split"):
            row = self.scenario()
            if change == "README":
                row["input"]["context"][1]["text"] = self.source_bytes["README.md"].decode()
            elif change == "placeholder":
                row["input"]["context"][0]["text"] = row["input"]["context"][0]["text"].replace("{appname}", "learnyounode")
            elif change == "version_group":
                row["source_group"] += "/" + self.commit
            elif change == "heldout":
                row["source_group"] = "ew-alpha"
            else:
                row["split"] = "evaluation"
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.generate([row])
        train, dev = self.scenario(), self.scenario()
        dev["split"] = "dev"
        with self.assertRaises(SystemExit):
            self.generate([train, dev])

    def test_bad_chronology_review_identity_and_tree_fail_even_with_fixture_pins(self):
        original_time = self.manifest[0]["captured_at"]
        for value in ("2019-01-01T00:00:00Z", "2026-09-27T11:30:00Z", "2026-09-27T11:00:00+01:00"):
            for entry in self.manifest:
                entry["captured_at"] = value
            self.write_evidence()
            with self.subTest(capture=value), self.assertRaises(SystemExit):
                self.generate()
        for entry in self.manifest:
            entry["captured_at"] = original_time
        self.receipt["response_id"] = "unbound-response"
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate()
        self.receipt["response_id"] = self.admission["response_id"]
        self.tree[0]["sha"] = "0" * 40
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate()


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
# [[OpenAI Codex | GPT-6 | 2026-09-30 | 23:54 UTC | END]]
