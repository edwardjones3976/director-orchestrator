# [[OpenAI Codex | GPT-6 | 2026-10-02 | 10:48 UTC | BEGIN]]
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
from unittest.mock import MagicMock, Mock, patch

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


    def coverage_answer(self):
        # Artificial offline ledger: tests structural binding, never semantic grading.
        return {"target": json.loads(json.dumps(TARGET)), "coverage": [
            {"quote_index": 0, "classification": "mandatory", "links": [
                {"field": field, "index": 0} for field in generate_rows.TARGET_FIELDS if field != "non_goals"]},
            {"quote_index": 1, "classification": "exclusion", "links": [{"field": "non_goals", "index": 0}]},
            {"quote_index": 2, "classification": "advisory", "links": []},
            {"quote_index": 3, "classification": "context", "links": []}]}

    def quote_options(self, bad_stage=None, coverage_answer=None):
        options = self.local_options()
        scenario = self.default_scenario()
        context = scenario["input"]["context"][0]
        self.quote_calls = []
        def respond(method, url, body, timeout, *, capture):
            index = len(self.quote_calls)
            if index == 1:
                receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text())
                self.assertEqual(receipt["stages"][0]["status"], "completed")
                self.assertNotIn("target", receipt)
            self.quote_calls.append(body)
            quotes = [context["text"]] if coverage_answer is None else [
                "Both secrets", "are accepted", "for one hour", "after rotation."]
            output = ({"quotes": [{"source_ref": context["source_ref"], "quote": quote} for quote in quotes]}
                      if index == 0 else TARGET if coverage_answer is None else coverage_answer)
            if bad_stage == index:
                output = {"quotes": [{"source_ref": context["source_ref"], "quote": "unsupported quote"}]} if index == 0 else {}
            response = {"id": f"quote-stage-{index}", "model": "qwen-local",
                "usage": {"completion_tokens": 40}, "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(output)}}]}
            capture.update(request_bytes=json.dumps(body, allow_nan=False).encode(),
                           response_bytes=json.dumps(response, indent=2).encode(), http_status=200)
            return response
        self.local_http.side_effect = respond
        return options, scenario

    def test_quote_teacher_has_two_raw_bound_stages_and_original_student_input(self):
        options, scenario = self.quote_options()
        self.assertEqual(self.generate_local(options, scenario, quote_first=True)["rows"], 1)
        self.assertEqual(len(self.quote_calls), 2)
        self.assertEqual(set(self.quote_calls[1]["response_format"]["json_schema"]["schema"]["properties"]), set(TARGET))
        row = json.loads(self.out.read_text())
        self.assertEqual(row["input"], scenario["input"])
        receipt = json.loads((self.root / row["receipt_ref"]).read_text())
        self.assertEqual(receipt["protocol"], "source-quotes/v1")
        self.assertEqual([stage["prompt_sha256"] for stage in receipt["stages"]], [
            "2e9759e6909c8db0e07624936a7de2f74784b0e6a09319695ce47e1c88a082bc",
            "f18620e0f3b29d655246a1824f6d7a2bfc2a8e62c6664385940428c95d35edd7"])
        self.assertEqual([s["name"] for s in receipt["stages"]], ["extract", "map"])
        for body, stage in zip(self.quote_calls, receipt["stages"]):
            self.assertEqual(bytes.fromhex(stage["request_bytes_hex"]), json.dumps(body).encode())
            self.assertEqual(json.loads(bytes.fromhex(stage["response_bytes_hex"])), stage["response"])
        self.assertNotEqual(receipt["prompt_sha256"], receipt["student_prompt_sha256"])
        self.assertEqual(receipt["student_prompt_sha256"], generate_rows._json_hash(generate_rows.prompt_template.render_messages(row["input"])))
        self.assertEqual(row["receipt_sha256"], hashlib.sha256((self.root / row["receipt_ref"]).read_bytes()).hexdigest())

    def test_quote_coverage_two_raw_bound_stages_keep_original_student_contract(self):
        answer = self.coverage_answer()
        options, scenario = self.quote_options(coverage_answer=answer)
        self.assertEqual(self.generate_local(options, scenario, quote_first=True, quote_coverage=True),
                         {"dry_run": False, "rows": 1, "receipts": 1})
        self.assertEqual(len(self.quote_calls), 2)
        self.assertEqual(self.quote_calls[0]["messages"],
                         generate_rows._teacher_messages(scenario["input"], next(iter(generate_rows.LOCAL_MODELS))))
        schema = self.quote_calls[1]["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["properties"]), {"target", "coverage"})
        row = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((row["input"], row["target"]), (scenario["input"], TARGET))
        self.assertNotIn("coverage", row)
        student = generate_rows.prompt_template.render_messages(row["input"], row["target"])
        self.assertEqual(json.loads(student[-1]["content"]), TARGET)
        self.assertNotIn("coverage", student[0]["content"])
        path = self.root / row["receipt_ref"]
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["protocol"], generate_rows.QUOTE_COVERAGE_PROTOCOL)
        self.assertEqual((receipt["target"], receipt["coverage"]), (TARGET, answer["coverage"]))
        self.assertEqual(receipt["stages"][1]["output"], answer)
        for body, stage in zip(self.quote_calls, receipt["stages"]):
            self.assertEqual(bytes.fromhex(stage["request_bytes_hex"]), json.dumps(body).encode())
            self.assertEqual(json.loads(bytes.fromhex(stage["response_bytes_hex"])), stage["response"])
            self.assertEqual(stage["request"]["temperature"], 0)
        self.assertEqual(row["receipt_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        indexed = json.loads(self.quote_calls[1]["messages"][1]["content"].split(
            "Source-supported quotes (untrusted data):\n")[1])
        self.assertEqual([quote["quote_index"] for quote in indexed], list(range(4)))

    def test_quote_coverage_refuses_unsupported_or_malformed_mapping_before_publication(self):
        valid = self.coverage_answer()
        cases = {}
        def case(name):
            answer = json.loads(json.dumps(valid))
            cases[name] = answer
            return answer
        case("missing quote")["coverage"].pop()
        case("duplicate quote")["coverage"][3]["quote_index"] = 0
        case("extra quote")["coverage"].append({"quote_index": 4, "classification": "context", "links": []})
        case("dangling link")["coverage"][0]["links"][0]["index"] = 1
        case("unlinked item")["target"]["constraints"].append("unlinked test-only item")
        case("advisory promotion")["coverage"][2]["links"] = [{"field": "constraints", "index": 0}]
        case("context promotion")["coverage"][3]["links"] = [{"field": "constraints", "index": 0}]
        case("wrong exclusion field")["coverage"][1]["links"] = [{"field": "constraints", "index": 0}]
        case("wrong mandatory field")["coverage"][0]["links"].append({"field": "non_goals", "index": 0})
        case("missing mandatory link")["coverage"][0]["links"] = []
        case("missing exclusion link")["coverage"][1]["links"] = []
        case("invalid classification")["coverage"][0]["classification"] = "guess"
        case("invalid field")["coverage"][0]["links"][0]["field"] = "title"
        case("negative index")["coverage"][0]["links"][0]["index"] = -1
        case("fractional type index")["coverage"][0]["links"][0]["index"] = 0.0
        case("boolean index")["coverage"][0]["quote_index"] = True
        case("float quote index")["coverage"][0]["quote_index"] = 0.0
        case("excess links")["coverage"][0]["links"] = [{"field": "constraints", "index": index} for index in range(65)]
        case("additional envelope field")["repair"] = True
        case("additional target field")["target"]["notes"] = []
        options, scenario = self.quote_options(coverage_answer=valid)
        for name, answer in cases.items():
            self.quote_calls.clear()
            original = self.local_http.side_effect
            def invalid_mapping(method, url, body, timeout, *, capture, answer=answer):
                response = original(method, url, body, timeout, capture=capture)
                if len(self.quote_calls) == 2:
                    response["choices"][0]["message"]["content"] = json.dumps(answer)
                    capture["response_bytes"] = json.dumps(response).encode()
                return response
            self.local_http.side_effect = invalid_mapping
            with self.subTest(case=name), self.assertRaises(SystemExit):
                self.generate_local(options, scenario, quote_first=True, quote_coverage=True)
            self.local_http.side_effect = original
            self.assertEqual(len(self.quote_calls), 2)
            self.assertFalse(self.out.exists())
        records = [json.loads(path.read_text(encoding="utf-8")) for path in options["receipts_dir"].glob("*.json")]
        self.assertEqual(len(records), len(cases))
        self.assertTrue(all([stage["status"] for stage in receipt["stages"]] == ["completed", "failed"]
                            and receipt["status"] == "failed" for receipt in records))

    def test_quote_coverage_receipt_readback_rejects_ledger_and_wire_target_tampering(self):
        options, scenario = self.quote_options(coverage_answer=self.coverage_answer())
        self.generate_local(options, scenario, quote_first=True, quote_coverage=True)
        row = json.loads(self.out.read_text(encoding="utf-8"))
        path = self.root / row["receipt_ref"]
        original = path.read_bytes()
        def ledger(r): r["coverage"][2]["classification"] = "context"
        def stage_ledger(r): r["stages"][1]["output"]["coverage"][2]["classification"] = "context"
        def stage_target(r): r["stages"][1]["output"]["target"]["constraints"] = ["changed"]
        def aggregate_target(r): r["target"]["constraints"] = ["changed"]
        def schema(r): r["stages"][1]["schema"] = generate_rows.QUOTE_SCHEMA
        def wire(r): r["stages"][1]["response_bytes_hex"] = b'{}'.hex()
        def both_ledgers(r):
            r["coverage"][2]["classification"] = "context"
            r["stages"][1]["output"]["coverage"][2]["classification"] = "context"
        def wire_target(r):
            stage = r["stages"][1]
            answer = json.loads(stage["response"]["choices"][0]["message"]["content"])
            answer["target"]["constraints"] = ["changed"]
            stage["response"]["choices"][0]["message"]["content"] = json.dumps(answer)
            raw = json.dumps(stage["response"]).encode()
            stage["response_bytes_hex"] = raw.hex()
            stage["response_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
            stage["response_sha256"] = generate_rows._json_hash(stage["response"])
        def protocol(r): r["protocol"] = "source-quotes-coverage/v99"
        for mutate in (ledger, stage_ledger, both_ledgers, stage_target, aggregate_target, schema, wire, wire_target, protocol):
            record = json.loads(original)
            mutate(record)
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.subTest(mutation=mutate.__name__), self.assertRaises(SystemExit):
                generate_rows.local_receipt_hash(generate_rows.DispatchResult(json.dumps(row["target"]), path,
                                                  row["source_model"]), scenario, row["target"], UTC)
        path.write_bytes(original)

    def test_quote_coverage_flag_guards_and_dry_run_have_no_effects(self):
        options = self.local_options()
        self.assertEqual(self.generate_local(options, quote_first=True, quote_coverage=True, dry_run=True),
                         {"dry_run": True, "rows": 0, "receipts": 0})
        with patch.object(generate_rows, "validate_scenarios_for_dispatch") as validation:
            for flags in ({"quote_coverage": True},
                          {"quote_first": True, "quote_coverage": True, "provider": "openai"}):
                with self.subTest(flags=flags), self.assertRaises(SystemExit):
                    generate_rows.generate([], provider=flags.pop("provider", "local_model"),
                        set_a=self.set_a, set_b=self.set_b, out=self.out, terms_verified_at=UTC,
                        generated_at=UTC, source_root=self.root, dry_run=True, **flags)
            validation.assert_not_called()
        with self.assertRaises(SystemExit):
            self.generate_local(options, quote_first=True, quote_coverage=True, dispatch=self.dispatch(), dry_run=True)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertFalse(options["receipts_dir"].exists())
        self.assertFalse(self.out.exists())
        with patch.object(generate_rows, "load_scenarios") as load, self.assertRaises(SystemExit):
            generate_rows.main(["--scenarios", str(self.root / "absent.jsonl"), "--provider", "local_model",
                "--set-a", str(self.set_a), "--set-b", str(self.set_b), "--out", str(self.out),
                "--terms-verified-at", UTC, "--quote-coverage", "--dry-run"])
        load.assert_not_called()

    def segment_options(self):
        options = self.local_options()
        scenario = self.default_scenario()
        text = ASSIGNMENT_BODY.replace("\n", "\r\n") + "\r\nRepeated α\r\n\r\nRepeated α\r\n"
        context = scenario["input"]["context"][0]
        context["text"] = text
        (self.root / context["source_ref"]).write_bytes(text.encode())
        context["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        self.github_record["issue"]["body"] = text
        spans = generate_rows._source_segments(scenario["input"])
        # Fixture classifications test mechanical support only, not source faithfulness.
        answer = {"target": json.loads(json.dumps(TARGET)), "coverage": [
            {"quote_index": 0, "classification": "context", "links": []},
            {"quote_index": 1, "classification": "mandatory", "links": [
                {"field": field, "index": 0} for field in generate_rows.TARGET_FIELDS if field != "non_goals"]},
            {"quote_index": 2, "classification": "exclusion", "links": [{"field": "non_goals", "index": 0}]},
            {"quote_index": 3, "classification": "context", "links": []},
            {"quote_index": 4, "classification": "context", "links": []}]}
        answer = {f"segment_{entry['quote_index']}": {"classification": entry["classification"], "items": [
            {"field": link["field"], "text": TARGET[link["field"]][link["index"]]} for link in entry["links"]]}
            for entry in answer["coverage"]}
        self.segment_calls = []
        def respond(method, url, body, timeout, *, capture):
            self.segment_calls.append(body)
            receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(receipt["located_quotes"], spans)
            self.assertEqual([stage["name"] for stage in receipt["stages"]], ["map"])
            response = {"id": "segment-map-0", "model": "qwen-local", "usage": {"completion_tokens": 40},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}
            capture.update(request_bytes=json.dumps(body, allow_nan=False).encode(),
                           response_bytes=json.dumps(response, indent=2).encode(), http_status=200)
            return response
        self.local_http.side_effect = respond
        return options, scenario, answer

    def test_source_segments_preserve_all_original_nonwhitespace_crlf_unicode_and_repetitions(self):
        texts = (" \r\n# Heading α\r\n\r\n  repeated α  \r\n\r\n  repeated α  \r\n\r\nlast β\r\n ",
                 "prefix\nline\n\t\nnext\n", "α\r\rβ\r", "paragraph without newline")
        for text in texts:
            item = {"context": [{"source_ref": "test.md", "text": text}]}
            spans = generate_rows._source_segments(item)
            covered = set()
            for span in spans:
                start, end = span["start"], span["end"]
                self.assertEqual(span["quote"], text[start:end])
                self.assertEqual(span["context_sha256"], hashlib.sha256(text.encode()).hexdigest())
                covered.update(range(start, end))
            self.assertTrue(all(index in covered for index, char in enumerate(text) if not char.isspace()))
        spans = generate_rows._source_segments({"context": [{"source_ref": "test.md", "text": texts[0]}]})
        self.assertEqual(spans[1]["quote"], spans[2]["quote"])
        self.assertNotEqual(spans[1]["start"], spans[2]["start"])
        self.assertEqual(spans[0]["quote"], "# Heading α\r\n")
        self.assertEqual(spans[3]["quote"], "last β\r\n")

    def test_source_segments_bounds_empty_and_duplicate_refs_are_refused_before_probe(self):
        def item(text): return {"context": [{"source_ref": "test.md", "text": text}]}
        self.assertEqual(len(generate_rows._source_segments(item("x" * 2048))), 1)
        self.assertEqual(len(generate_rows._source_segments(item("\n\n".join(["x"] * 64)))), 64)
        for source in (item("x" * 2049), item("\n\n".join(["x"] * 65)), item(" \r\n\t"),
                       {"context": [{"source_ref": "same.md", "text": "one"},
                                    {"source_ref": "same.md", "text": "two"}]}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                generate_rows._source_segments(source)
        options = self.local_options()
        with patch.object(generate_rows, "_source_segments", side_effect=ValueError("source bounds")), self.assertRaises(SystemExit):
            self.generate_local(options, source_segments=True)
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()
        self.assertFalse(options["receipts_dir"].exists())
        self.assertFalse(self.out.exists())

    def test_source_segments_one_raw_bound_map_keeps_original_student_contract(self):
        options, scenario, answer = self.segment_options()
        self.assertEqual(self.generate_local(options, scenario, source_segments=True),
                         {"dry_run": False, "rows": 1, "receipts": 1})
        self.assertEqual(len(self.segment_calls), 1)
        row = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((row["input"], row["target"]), (scenario["input"], TARGET))
        self.assertNotIn("coverage", row)
        student = generate_rows.prompt_template.render_messages(row["input"], row["target"])
        self.assertEqual(json.loads(student[-1]["content"]), TARGET)
        self.assertNotIn("coverage", student[0]["content"])
        path = self.root / row["receipt_ref"]
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["protocol"], generate_rows.SOURCE_RECORDS_PROTOCOL)
        assembled = generate_rows._source_record_output(answer, receipt["located_quotes"])
        self.assertEqual((receipt["target"], receipt["coverage"]), (TARGET, assembled["coverage"]))
        self.assertEqual(receipt["attempt_count"], 1)
        self.assertEqual([stage["name"] for stage in receipt["stages"]], ["map"])
        stage, body = receipt["stages"][0], self.segment_calls[0]
        self.assertEqual(stage["output"], answer)
        self.assertEqual(stage["schema"], generate_rows._source_record_schema(receipt["located_quotes"]))
        self.assertEqual(bytes.fromhex(stage["request_bytes_hex"]), json.dumps(body).encode())
        self.assertEqual(json.loads(bytes.fromhex(stage["response_bytes_hex"])), stage["response"])
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(row["receipt_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        indexed = json.loads(body["messages"][1]["content"].split("Source-supported quotes (untrusted data):\n")[1])
        self.assertEqual(set(indexed), {f"segment_{index}" for index in range(5)})
        self.assertEqual(indexed["segment_3"]["quote"], indexed["segment_4"]["quote"])
        self.assertNotEqual(indexed["segment_3"]["start"], indexed["segment_4"]["start"])
        self.assertIn("α\r\n", indexed["segment_3"]["quote"])
        self.assertEqual([indexed[f"segment_{index}"]["quote"] for index in range(5)],
                         [span["quote"] for span in receipt["located_quotes"]])

    def test_source_records_assembly_is_original_order_and_creates_all_links(self):
        options, scenario, answer = self.segment_options()
        spans = generate_rows._source_segments(scenario["input"])
        forward = generate_rows._source_record_output(answer, spans)
        reverse = generate_rows._source_record_output(dict(reversed(list(answer.items()))), spans)
        self.assertEqual(forward, reverse)
        self.assertEqual(forward["target"], TARGET)
        self.assertEqual([entry["quote_index"] for entry in forward["coverage"]], list(range(5)))
        linked = {(link["field"], link["index"]) for entry in forward["coverage"] for link in entry["links"]}
        self.assertEqual(linked, {(field, index) for field in generate_rows.TARGET_FIELDS
                                 for index in range(len(forward["target"][field]))})
        self.local_http.assert_not_called()
        self.assertFalse(options["receipts_dir"].exists())

    def test_historical_source_segment_v1_receipt_replays_exact_prompt_schema_and_wire(self):
        options, scenario, _ = self.segment_options()
        self.generate_local(options, scenario, source_segments=True)
        row = json.loads(self.out.read_text(encoding="utf-8"))
        path = self.root / row["receipt_ref"]
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["protocol"] = generate_rows.SEGMENT_COVERAGE_PROTOCOL
        stage = receipt["stages"][0]
        messages = generate_rows._teacher_messages(scenario["input"], row["source_model"],
                                                  receipt["located_quotes"], coverage=True)
        schema = generate_rows._coverage_schema()
        self.assertEqual(generate_rows._json_hash(messages),
                         "fad98c8abbbfb18387b8719745565b3ae9a17e75626e07bed994850353f51b81")
        self.assertEqual(generate_rows._json_hash(schema),
                         "80b604b384d081f8e22a17b0e520fc535aecbc6ee7f962284bef4d3253b53656")
        body = generate_rows._teacher_body(receipt, messages, schema, stage["request"]["max_tokens"], "map")
        envelope = {"target": row["target"], "coverage": receipt["coverage"]}
        response = stage["response"]
        response["choices"][0]["message"]["content"] = json.dumps(envelope)
        request_bytes, response_bytes = json.dumps(body, allow_nan=False).encode(), json.dumps(response).encode()
        stage.update(request=body, response=response, schema=schema, output=envelope,
                     prompt_sha256=generate_rows._json_hash(messages), schema_sha256=generate_rows._json_hash(schema),
                     request_sha256=generate_rows._json_hash(body), response_sha256=generate_rows._json_hash(response),
                     request_bytes_hex=request_bytes.hex(), request_bytes_sha256=hashlib.sha256(request_bytes).hexdigest(),
                     response_bytes_hex=response_bytes.hex(), response_bytes_sha256=hashlib.sha256(response_bytes).hexdigest())
        for key in ("prompt_sha256", "schema_sha256", "request_sha256", "response_sha256"):
            receipt[key] = stage[key]
        path.write_text(json.dumps(receipt), encoding="utf-8")
        digest = generate_rows.local_receipt_hash(generate_rows.DispatchResult(json.dumps(row["target"]), path,
                                                 row["source_model"]), scenario, row["target"], UTC)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(self.segment_calls), 1)

    def test_source_records_complete_packet_bounds_include_required_key_schema(self):
        quotes = [{"quote": "test-only source"}] * 64
        schema = generate_rows._source_record_schema(quotes)
        prepared = {"alias": "offline", "model": "microsoft/phi-4"}
        messages = [{"role": "user", "content": "short"}]
        self.assertLess(len(json.dumps(messages).encode()), generate_rows.MAX_PACKET_BYTES)
        with self.assertRaises(ValueError):
            generate_rows._teacher_body(prepared, messages, schema, 1024, "map", full_packet=True)

    def test_source_segments_packet_bound_fails_before_http_and_preserves_failed_receipt(self):
        options, scenario, _ = self.segment_options()
        with patch.object(generate_rows, "_teacher_messages", return_value=[{
                "role": "system", "content": "x" * (generate_rows.MAX_PACKET_BYTES + 1)}]), self.assertRaises(SystemExit):
            self.generate_local(options, scenario, source_segments=True)
        self.local_http.assert_not_called()
        self.assertFalse(self.out.exists())
        receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual([stage["name"] for stage in receipt["stages"]], ["map"])
        self.assertNotIn("request", receipt["stages"][0])

    def test_source_segments_malformed_records_fail_after_one_call(self):
        options, scenario, answer = self.segment_options()
        cases = {}
        def case(name):
            bad = json.loads(json.dumps(answer))
            cases[name] = bad
            return bad
        case("missing key").pop("segment_4")
        case("extra key")["segment_5"] = {"classification": "context", "items": []}
        case("empty mandatory")["segment_1"]["items"] = []
        case("advisory promotion")["segment_0"]["items"] = answer["segment_1"]["items"]
        case("wrong exclusion field")["segment_2"]["items"][0]["field"] = "constraints"
        case("unknown class")["segment_0"]["classification"] = "guess"
        case("invalid field")["segment_1"]["items"][0]["field"] = "title"
        case("oversized text")["segment_1"]["items"][0]["text"] = "x" * 2049
        case("oversized items")["segment_1"]["items"] = [answer["segment_1"]["items"][0]] * 65
        case("extra item field")["segment_1"]["items"][0]["index"] = 0
        case("secret text")["segment_1"]["items"][0]["text"] = "PASSWORD=not-a-real-secret"
        cases["duplicate JSON key"] = '{"segment_0":' + json.dumps(answer["segment_0"]) + ',' + json.dumps(answer)[1:]
        original = self.local_http.side_effect
        for name, bad in cases.items():
            def bad_map(method, url, body, timeout, *, capture, bad=bad):
                response = original(method, url, body, timeout, capture=capture)
                response["choices"][0]["message"]["content"] = json.dumps(bad) if isinstance(bad, dict) else bad
                capture["response_bytes"] = json.dumps(response).encode()
                return response
            self.local_http.side_effect = bad_map
            self.segment_calls.clear()
            with self.subTest(case=name), self.assertRaises(SystemExit):
                self.generate_local(options, scenario, source_segments=True)
            self.assertEqual(len(self.segment_calls), 1)
            self.assertFalse(self.out.exists())
        records = [json.loads(path.read_text(encoding="utf-8")) for path in options["receipts_dir"].glob("*.json")]
        self.assertEqual(len(records), len(cases))
        self.assertTrue(all(record["status"] == "failed" and len(record["stages"]) == 1 for record in records))

    def test_source_segments_readback_reconstructs_spans_and_refuses_raw_ledger_protocol_tamper(self):
        options, scenario, answer = self.segment_options()
        self.generate_local(options, scenario, source_segments=True)
        row = json.loads(self.out.read_text(encoding="utf-8"))
        path = self.root / row["receipt_ref"]
        original = path.read_bytes()
        def span(r): r["located_quotes"][0]["start"] += 1
        def omit_span(r): r["located_quotes"].pop()
        def reorder(r): r["located_quotes"].reverse()
        def ledger(r): r["coverage"][0]["classification"] = "advisory"
        def output(r): r["stages"][0]["output"]["segment_1"]["items"][0]["text"] = "changed"
        def assembled(r): r["target"]["constraints"] = ["changed"]
        def raw_request(r): r["stages"][0]["request_bytes_hex"] = b'{}'.hex()
        def raw_response(r): r["stages"][0]["response_bytes_hex"] = b'{}'.hex()
        def time(r): r["stages"][0]["end_utc"] = "2026-09-01T00:00:00Z"
        def protocol(r): r["protocol"] = generate_rows.QUOTE_COVERAGE_PROTOCOL
        def downgrade(r): r["protocol"] = generate_rows.SEGMENT_COVERAGE_PROTOCOL
        for mutate in (span, omit_span, reorder, ledger, output, assembled, raw_request, raw_response, time, protocol, downgrade):
            record = json.loads(original)
            mutate(record)
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.subTest(mutation=mutate.__name__), self.assertRaises(SystemExit):
                generate_rows.local_receipt_hash(generate_rows.DispatchResult(json.dumps(row["target"]), path,
                                                  row["source_model"]), scenario, row["target"], UTC)
        path.write_bytes(original)

    def test_source_segments_flags_and_cli_dry_run_make_no_effects(self):
        options = self.local_options()
        self.assertEqual(self.generate_local(options, source_segments=True, dry_run=True),
                         {"dry_run": True, "rows": 0, "receipts": 0})
        for flags in ({"quote_first": True}, {"quote_coverage": True}, {"provider": "openai"},
                      {"dispatch": self.dispatch()}):
            with patch.object(generate_rows, "validate_scenarios_for_dispatch") as validate, self.assertRaises(SystemExit):
                generate_rows.generate([], provider=flags.pop("provider", "local_model"), set_a=self.set_a,
                    set_b=self.set_b, out=self.out, terms_verified_at=UTC, generated_at=UTC, dry_run=True,
                    source_segments=True, **flags)
            validate.assert_not_called()
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertFalse(options["receipts_dir"].exists())
        self.assertFalse(self.out.exists())
        cli = ["--scenarios", str(self.root / "absent.jsonl"), "--provider", "local_model",
               "--set-a", str(self.set_a), "--set-b", str(self.set_b), "--out", str(self.out),
               "--terms-verified-at", UTC, "--source-segments", "--dry-run"]
        for conflict in ("--quote-first", "--quote-coverage"):
            with patch.object(generate_rows, "load_scenarios") as load, self.assertRaises(SystemExit):
                generate_rows.main(cli + [conflict])
            load.assert_not_called()
        with patch.object(generate_rows, "load_scenarios", return_value=[self.default_scenario()]), patch("builtins.print") as output:
            self.assertEqual(generate_rows.main(cli), 0)
        self.assertEqual(json.loads(output.call_args.args[0]), {"dry_run": True, "rows": 0, "receipts": 0})
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()

    def test_quote_missing_or_ambiguous_source_spans_are_refused(self):
        item = {"context": [{"source_ref": "wo.md", "text": "one obligation. one obligation."}]}
        for entry in ({"source_ref": "wo.md", "quote": "missing"},
                      {"source_ref": "unknown.md", "quote": "one obligation."},
                      {"source_ref": "wo.md", "quote": "one obligation."},
                      {"source_ref": "wo.md", "quote": "one obligation.", "start": 0}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                generate_rows._located_quotes(item, {"quotes": [entry]})
        unique = {"context": [{"source_ref": "wo.md", "text": "prefix α obligation suffix"}]}
        located = generate_rows._located_quotes(unique, {"quotes": [{"source_ref": "wo.md", "quote": "α obligation"}]})
        self.assertEqual((located[0]["start"], located[0]["end"]), (7, 19))

    def test_quote_stage_failures_preserve_first_stage_and_do_not_retry_or_emit(self):
        options, scenario = self.quote_options(bad_stage=0)
        with self.assertRaises(SystemExit):
            self.generate_local(options, scenario, quote_first=True)
        self.assertEqual(len(self.quote_calls), 1)
        first = json.loads(next(options["receipts_dir"].glob("*.json")).read_text())
        self.assertEqual((first["status"], first["stages"][0]["status"]), ("failed", "failed"))
        self.assertFalse(self.out.exists())
        # Same configured mock changes only the failure phase; this is a new offline attempt.
        self.quote_calls.clear()
        def bad_mapping(method, url, body, timeout, *, capture):
            index = len(self.quote_calls)
            self.quote_calls.append(body)
            context = scenario["input"]["context"][0]
            answer = {"quotes": [{"source_ref": context["source_ref"], "quote": context["text"]}]} if index == 0 else {}
            response = {"id": str(index), "model": "qwen-local", "usage": {},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}
            capture.update(request_bytes=json.dumps(body).encode(), response_bytes=json.dumps(response).encode(), http_status=200)
            return response
        self.local_http.side_effect = bad_mapping
        with self.assertRaises(SystemExit):
            self.generate_local(options, scenario, quote_first=True)
        records = [json.loads(p.read_text()) for p in options["receipts_dir"].glob("*.json")]
        second = next(r for r in records if len(r["stages"]) == 2)
        self.assertEqual([s["status"] for s in second["stages"]], ["completed", "failed"])
        self.assertEqual(second["status"], "failed")
        self.assertEqual(len(self.quote_calls), 2)
        self.assertFalse(self.out.exists())

    def test_quote_failure_receipts_distinguish_causes_without_retry(self):
        options, scenario = self.quote_options()
        details = ("quote is missing or not unique in source", "teacher request exceeds packet/token bounds")
        for detail in details:
            with self.subTest(detail=detail), patch.object(generate_rows, "_teacher_body", side_effect=ValueError(detail)), self.assertRaises(SystemExit):
                self.generate_local(options, scenario, quote_first=True)
        records = [json.loads(p.read_text(encoding="utf-8")) for p in options["receipts_dir"].glob("*.json")]
        self.assertEqual(len(records), 2)
        self.assertEqual({r["error_detail"] for r in records}, set(details))
        for record in records:
            self.assertEqual((record["status"], record["error"]), ("failed", "ValueError"))
            self.assertEqual(record["stages"][0]["error_detail"], record["error_detail"])
            self.assertEqual(len(record["stages"]), 1)
        self.local_http.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_quote_mapping_preparation_failure_is_recorded_before_second_contact(self):
        options, scenario = self.quote_options()
        original = generate_rows._teacher_messages
        def oversized(item, model, quotes=None):
            messages = original(item, model, quotes)
            if quotes is not None:
                messages[1]["content"] = "x" * (generate_rows.MAX_PACKET_BYTES + 1)
            return messages
        with patch.object(generate_rows, "_teacher_messages", side_effect=oversized), self.assertRaises(SystemExit):
            self.generate_local(options, scenario, quote_first=True)
        self.assertEqual(len(self.quote_calls), 1)
        receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text())
        self.assertEqual([s["status"] for s in receipt["stages"]], ["completed", "failed"])
        self.assertNotIn("request", receipt["stages"][1])
        self.assertFalse(self.out.exists())

    def test_quote_chain_readback_rejects_mutated_offsets_bytes_order_or_target(self):
        options, scenario = self.quote_options()
        self.generate_local(options, scenario, quote_first=True)
        row = json.loads(self.out.read_text())
        path = self.root / row["receipt_ref"]
        original = path.read_bytes()
        def changed_quote(r): r["located_quotes"][0]["start"] += 1
        def changed_bytes(r): r["stages"][0]["response_bytes_hex"] = b'{}'.hex()
        def changed_order(r): r["stages"].reverse()
        def changed_target(r): r["stages"][1]["output"]["constraints"] = ["invented"]
        def changed_time(r): r["stages"][1]["start_utc"] = "2026-09-01T00:00:00Z"
        def changed_usage(r): r["usage"]["completion_tokens"] = 999
        for mutate in (changed_quote, changed_bytes, changed_order, changed_target, changed_time, changed_usage):
            record = json.loads(original)
            mutate(record)
            path.write_text(json.dumps(record))
            with self.subTest(mutation=mutate.__name__), self.assertRaises(SystemExit):
                generate_rows.local_receipt_hash(generate_rows.DispatchResult(json.dumps(row["target"]), path, row["source_model"]),
                                                  scenario, row["target"], UTC)
        path.write_bytes(original)

    def test_quote_dry_run_and_cloud_refusal_make_no_request_or_receipt(self):
        options = self.local_options()
        self.assertEqual(self.generate_local(options, quote_first=True, dry_run=True), {"dry_run": True, "rows": 0, "receipts": 0})
        self.local_probe.assert_not_called()
        self.local_http.assert_not_called()
        self.assertFalse(options["receipts_dir"].exists())
        with self.assertRaises(SystemExit):
            self.generate([self.default_scenario()], quote_first=True, dry_run=True)
        self.assertFalse(self.out.exists())

    def test_http_optional_capture_preserves_wire_bytes_without_network(self):
        raw = b'{ "ok": true }\n'
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = raw
        capture = {}
        with patch.object(generate_rows.local_transport.http.client, "HTTPConnection", return_value=connection):
            self.assertEqual(generate_rows.local_transport.http_json("POST", "http://127.0.0.1:1234/v1", {"probe": True}, 30, capture=capture), {"ok": True})
        self.assertEqual(capture, {"request_bytes": b'{"probe": true}', "response_bytes": raw, "http_status": 200})
        self.assertEqual(connection.request.call_args.kwargs["body"], capture["request_bytes"])
        response.status = 503
        failed_capture = {}
        with patch.object(generate_rows.local_transport.http.client, "HTTPConnection", return_value=connection), self.assertRaises(ValueError):
            generate_rows.local_transport.http_json("POST", "http://127.0.0.1:1234/v1", {"probe": True}, 30, capture=failed_capture)
        self.assertEqual(failed_capture["response_bytes"], raw)
        self.assertEqual(failed_capture["http_status"], 503)


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
    """Offline, test-only captures stand in for the closed reviewed source batches."""

    blob = staticmethod(LicensedSpecificationTests.blob)
    unused_sources = frozenset({"hello_world", "my_first_io", "http_client"})
    server_sources = frozenset({"time_server", "http_file_server", "http_json_api_server"})

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
        self.allowed_slugs = generate_rows.LEARNYOUNODE_SOURCES
        for slug in sorted(self.allowed_slugs | self.unused_sources | self.server_sources):
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
            "suitable": sorted(self.allowed_slugs), "targets_generated": 0, "targets_accepted": 0,
            "admitted_at": "2026-09-27T11:20:00Z", "response_id": "independent-response", "review_receipt_ref": "xai/review.json"}
        self.receipt = {"provider": "xai", "model": "grok-4.6", "http_status": 200,
            "response_status": "completed", "response_id": "independent-response"}
        self.pins = patch.dict(generate_rows.LEARNYOUNODE_ADMISSIONS, {}, clear=True)
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.server_pin = patch.object(generate_rows, "LEARNYOUNODE_SERVER_ADMISSION", "fixture-not-admitted")
        self.server_pin.start()
        self.addCleanup(self.server_pin.stop)
        self.write_evidence()

    def write_evidence(self, *, plain=False, answer=None, answer_hash=None, bind_plain=True):
        manifest = "".join(json.dumps(entry) + "\n" for entry in self.manifest).encode()
        commit = json.dumps(self.commit_record).encode()
        tree = json.dumps(self.tree_record).encode()
        self.admission["manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
        if plain:
            self.admission.update(source_tree="b" * 40, share_record_sha256="c" * 64)
            answer = answer if answer is not None else (
                "TERMINAL: ACCEPT_FOR_SOURCE_FACTS\nPACKET_ID QA-M2-LNY-SERVERS-SOURCE-1\n"
                f"source_pin {self.commit}\nsource_tree {self.admission['source_tree']}\n"
                f"manifest_sha256 {self.admission['manifest_sha256']}\n"
                f"share_record_sha256 {self.admission['share_record_sha256']}\n\nSource facts only.")
            self.admission["review_answer_exact_utf8_sha256"] = (
                answer_hash if answer_hash is not None else hashlib.sha256(answer.encode()).hexdigest())
        else:
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
            hashlib.sha256(tree).hexdigest(), self.admission["review_receipt_sha256"], self.allowed_slugs)
        if plain and bind_plain:
            generate_rows.LEARNYOUNODE_SERVER_ADMISSION = self.digest

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

    def test_new_batch_dry_run_has_no_probe_transport_receipts_or_rows(self):
        self.allowed_slugs = self.unused_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        self.write_evidence()
        rows = [self.scenario(slug) for slug in sorted(self.allowed_slugs)]
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(rows), {"dry_run": True, "rows": 0, "receipts": 0})
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_linked_siblings_cannot_cross_splits_before_probe_or_any_output(self):
        asynchronous = self.scenario("my_first_async_io")
        (self.root / "old-admission.json").write_bytes((self.root / "admission.json").read_bytes())
        asynchronous["source_provenance"]["admission_ref"] = "old-admission.json"
        self.allowed_slugs = self.unused_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        self.write_evidence()
        sync = self.scenario("my_first_io")
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            for train, dev in ((sync, asynchronous), (asynchronous, sync)):
                rows = json.loads(json.dumps([train, dev]))
                rows[1]["split"] = "dev"
                original = json.loads(json.dumps(rows))
                with self.subTest(train=train["source_group"]), patch.object(
                        generate_rows, "fail", wraps=generate_rows.fail) as failure, self.assertRaises(SystemExit):
                    self.generate(rows, dry_run=False)
                self.assertIn("one verified source group cannot straddle train and dev", failure.call_args.args[0])
                self.assertEqual(rows, original)
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_linked_siblings_same_side_keep_raw_groups_and_allow_unrelated_opposite_side(self):
        asynchronous = self.scenario("my_first_async_io")
        (self.root / "old-admission.json").write_bytes((self.root / "admission.json").read_bytes())
        asynchronous["source_provenance"]["admission_ref"] = "old-admission.json"
        self.allowed_slugs = self.unused_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        self.write_evidence()
        rows = [self.scenario("my_first_io"), asynchronous, self.scenario("http_client")]
        rows[2]["split"] = "dev"
        original = json.loads(json.dumps(rows))
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(rows), {"dry_run": True, "rows": 0, "receipts": 0})
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertEqual(rows, original)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_cross_batch_sources_refused_before_probe_even_if_evidence_lists_them(self):
        self.admission["suitable"] = sorted(generate_rows.LEARNYOUNODE_SOURCES | self.unused_sources | self.server_sources)
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            for allowed, refused in ((generate_rows.LEARNYOUNODE_SOURCES, "hello_world"),
                                     (self.unused_sources, "baby_steps"),
                                     (generate_rows.LEARNYOUNODE_SOURCES, "time_server"),
                                     (self.unused_sources, "http_file_server")):
                self.allowed_slugs = allowed
                self.write_evidence()
                self.assertEqual(self.generate([self.scenario(sorted(allowed)[0])]),
                                 {"dry_run": True, "rows": 0, "receipts": 0})
                with self.subTest(refused=refused), patch.object(
                        generate_rows, "fail", wraps=generate_rows.fail) as failure, self.assertRaises(SystemExit):
                    self.generate([self.scenario(refused)], dry_run=False)
                self.assertIn("outside the admitted synthetic pilot", failure.call_args.args[0])
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

    def test_server_batch_plain_review_dry_run_has_no_effects(self):
        self.allowed_slugs = self.server_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        self.write_evidence(plain=True)
        rows = [self.scenario(slug) for slug in sorted(self.allowed_slugs)]
        original = json.loads(json.dumps(rows))
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(rows), {"dry_run": True, "rows": 0, "receipts": 0})
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertEqual(rows, original)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_plain_review_is_not_enabled_for_old_batch(self):
        self.write_evidence(plain=True, bind_plain=False)
        with patch.object(generate_rows, "prepare_local_model") as probe, self.assertRaises(SystemExit):
            self.generate(dry_run=False)
        probe.assert_not_called()

    def test_server_plain_verdict_and_exact_bindings_fail_closed(self):
        self.allowed_slugs = self.server_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        self.write_evidence(plain=True)
        valid = self.receipt["answer"]
        invalid = [valid.replace("TERMINAL: ACCEPT_FOR_SOURCE_FACTS", "TERMINAL: CHANGES_REQUIRED", 1),
                   "Quoted verdict:\n" + valid, valid + "\nTERMINAL: CHANGES_REQUIRED",
                   valid.replace("SOURCE-1", "SOURCE-2"), valid.replace("c" * 64, "d" * 64),
                   valid.replace("b" * 40, "a" * 40), valid.replace(self.commit, "e" * 40),
                   valid.replace(self.admission["manifest_sha256"], "f" * 64)]
        with patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            for answer in invalid:
                self.write_evidence(plain=True, answer=answer)
                with self.subTest(answer=answer.splitlines()[0]), self.assertRaises(SystemExit):
                    self.generate([self.scenario("time_server")], dry_run=False)
            self.write_evidence(plain=True, answer_hash="0" * 64)
            with self.assertRaises(SystemExit):
                self.generate([self.scenario("time_server")], dry_run=False)
            for call in (probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_server_plain_review_keeps_authenticated_status_and_identity_checks(self):
        self.allowed_slugs = self.server_sources
        self.admission["suitable"] = sorted(self.allowed_slugs)
        with patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            for key, value in (("provider", "openai"), ("model", "wrong-model"), ("http_status", 500),
                               ("response_status", "incomplete"), ("response_id", ""),
                               ("response_id", "unbound-response")):
                original = self.receipt[key]
                self.receipt[key] = value
                self.write_evidence(plain=True)
                with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                    self.generate([self.scenario("time_server")], dry_run=False)
                self.receipt[key] = original
            for call in (probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_server_batch_tampered_evidence_and_extra_slugs_fail_before_contact(self):
        self.allowed_slugs = self.server_sources
        self.admission["suitable"] = sorted(self.allowed_slugs | self.unused_sources)
        self.write_evidence(plain=True)
        row = self.scenario("time_server")
        with patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate([row]), {"dry_run": True, "rows": 0, "receipts": 0})
            for ref in ("admission.json", "manifest.jsonl", "commit.json", "tree.json", "LICENSE.md",
                        "README.md", "time_server.problem.md", "http_file_server.problem.md", "http_json_api_server.problem.md"):
                path = self.root / ref
                raw = path.read_bytes()
                path.write_bytes(raw + b"tampered")
                slug = ref.removesuffix(".problem.md") if ref.endswith(".problem.md") else "time_server"
                with self.subTest(ref=ref), self.assertRaises(SystemExit):
                    self.generate([self.scenario(slug)], dry_run=False)
                path.write_bytes(raw)
            path = self.review_root / "xai/review.json"
            raw = path.read_bytes()
            path.write_bytes(raw + b"tampered")
            with self.assertRaises(SystemExit):
                self.generate([row], dry_run=False)
            path.write_bytes(raw)
            for slug in self.unused_sources | generate_rows.LEARNYOUNODE_SOURCES:
                with self.subTest(slug=slug), self.assertRaises(SystemExit):
                    self.generate([self.scenario(slug)], dry_run=False)
            for call in (probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())


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


class MarkdownAdmissionTests(unittest.TestCase):
    """Offline fixture for one pinned learner challenge, never a live approval."""

    blob = staticmethod(LicensedSpecificationTests.blob)
    generate = LearnyounodeAdmissionTests.generate

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.review_root = self.root / "reviews"
        (self.review_root / "xai").mkdir(parents=True)
        self.repository, self.commit = generate_rows.MARKDOWN_REPOSITORY, generate_rows.MARKDOWN_COMMIT
        self.description = "Tutorial example: # Heading 1.\nPlease create a first-level heading and verify your solution.\n"
        self.source_bytes = {"headings.md": self.description.encode(), "LICENSE": b"MIT test-only source license",
                             "footer.md": b"- To verify your program, run: `{appname} verify file.md`\n"}
        paths = {"headings.md": "exercises/headings/en.md", "LICENSE": "LICENSE", "footer.md": "i18n/footer/en.md"}
        self.manifest, self.tree = [], []
        for ref, raw in self.source_bytes.items():
            (self.root / ref).write_bytes(raw)
            path = paths[ref]
            self.manifest.append({"source_id": f"{self.repository}/{self.commit}/{path}",
                "source_repository": self.repository, "source_commit": self.commit, "source_tree": "b" * 40,
                "path": path, "source_ref": ref, "source_url": f"https://github.com/{self.repository}/blob/{self.commit}/{path}",
                "source_sha256": hashlib.sha256(raw).hexdigest(), "source_git_blob": self.blob(raw),
                "license": "MIT", "license_sha256": hashlib.sha256(self.source_bytes["LICENSE"]).hexdigest(),
                "upstream_commit_time": "2017-12-06T12:29:12Z", "captured_at": "2026-09-27T11:00:00.123456+00:00",
                "source_kind": "independently_licensed_specification", "destination": "training_candidates_only"})
            self.tree.append({"path": path, "mode": "100644", "type": "blob", "sha": self.blob(raw), "size": len(raw)})
        api = f"https://api.github.com/repos/{self.repository}"
        self.commit_record = {"sha": self.commit, "url": f"{api}/commits/{self.commit}", "commit": {
            "committer": {"date": "2017-12-06T12:29:12Z"}, "tree": {"sha": "b" * 40, "url": f"{api}/git/trees/{'b' * 40}"}}}
        self.tree_record = {"sha": self.commit, "url": f"{api}/git/trees/{self.commit}", "truncated": False, "tree": self.tree}
        self.input = {"objective": "Please create a first-level heading and verify your solution.",
            "repository": self.repository, "title": "headings", "context": [
                {"kind": "assignment_packet", "source_ref": ref, "source_sha256": hashlib.sha256(self.source_bytes[ref]).hexdigest(),
                 "text": self.source_bytes[ref].decode()} for ref in ("headings.md", "footer.md")]}
        self.admission = {"source_facts_verdict": "ACCEPT_FOR_SOURCE_FACTS", "pinned_commit": self.commit,
            "suitable": ["headings"], "targets_generated": 0, "targets_accepted": 0,
            "admitted_at": "2026-09-27T11:20:00Z", "response_id": "independent-response", "review_receipt_ref": "xai/review.json"}
        self.receipt = {"provider": "xai", "model": "grok-4.6", "http_status": 200,
            "response_status": "completed", "response_id": "independent-response"}
        pins = patch.dict(generate_rows.MARKDOWN_ADMISSIONS, {}, clear=True)
        pins.start()
        self.addCleanup(pins.stop)
        self.write_evidence()

    def write_evidence(self):
        raws = {"manifest.jsonl": "".join(json.dumps(e) + "\n" for e in self.manifest).encode(),
                "commit.json": json.dumps(self.commit_record).encode(), "tree.json": json.dumps(self.tree_record).encode(),
                "input.preparation.json": json.dumps(self.input).encode()}
        self.admission["manifest_sha256"] = hashlib.sha256(raws["manifest.jsonl"]).hexdigest()
        self.admission["input_preparation_sha256"] = hashlib.sha256(raws["input.preparation.json"]).hexdigest()
        old = self.admission.get("review_answer")
        self.admission["review_answer"] = "**TERMINAL: ACCEPT_FOR_SOURCE_FACTS** " + " ".join(
            (self.admission["manifest_sha256"], self.admission["input_preparation_sha256"], self.commit))
        if self.receipt.get("answer") in (None, old):
            self.receipt["answer"] = self.admission["review_answer"]
        receipt = json.dumps(self.receipt).encode()
        self.admission["review_receipt_sha256"] = hashlib.sha256(receipt).hexdigest()
        raws["admission.json"] = json.dumps(self.admission).encode()
        for ref, raw in raws.items():
            (self.root / ref).write_bytes(raw)
        (self.review_root / "xai/review.json").write_bytes(receipt)
        self.digest = hashlib.sha256(raws["admission.json"]).hexdigest()
        generate_rows.MARKDOWN_ADMISSIONS[self.digest] = tuple(hashlib.sha256(raws[ref]).hexdigest()
            for ref in ("manifest.jsonl", "commit.json", "tree.json")) + (
                self.admission["review_receipt_sha256"], self.admission["input_preparation_sha256"])

    def scenario(self):
        return {"source_group": f"licensed/{self.repository}/headings", "split": "train",
            "source_provenance": {"kind": "licensed_specification", "source_id": self.manifest[0]["source_id"],
                "admission_ref": "admission.json", "admission_sha256": self.digest, "manifest_ref": "manifest.jsonl",
                "official_capture_ref": "commit.json", "official_tree_ref": "tree.json", "license_ref": "LICENSE",
                "admitted_at": self.admission["admitted_at"]}, "input": json.loads(json.dumps(self.input))}

    def test_valid_dry_run_makes_no_effects(self):
        with patch.object(generate_rows, "github_chronology") as github, \
                patch.object(generate_rows, "prepare_local_model") as probe, \
                patch.object(generate_rows.local_transport, "http_json") as transport:
            self.assertEqual(self.generate(), {"dry_run": True, "rows": 0, "receipts": 0})
            for call in (github, probe, transport):
                call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())

    def test_changed_artifacts_receipt_and_prepared_input_fail_before_probe(self):
        paths = [self.root / ref for ref in (*self.source_bytes, "manifest.jsonl", "admission.json", "commit.json",
                                           "tree.json", "input.preparation.json")]
        paths.append(self.review_root / "xai/review.json")
        with patch.object(generate_rows, "prepare_local_model") as probe:
            for path in paths:
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.subTest(path=path.name), self.assertRaises(SystemExit):
                    self.generate(dry_run=False)
                path.write_bytes(original)
            probe.assert_not_called()

    def test_context_identity_paths_and_split_remain_closed(self):
        for change in ("unknown", "second-task", "escape", "title", "objective", "footer", "placeholder", "extra", "group", "split"):
            row = self.scenario()
            if change == "unknown": row["source_provenance"]["admission_sha256"] = "0" * 64
            elif change == "second-task": row["source_provenance"]["source_id"] = f"{self.repository}/{self.commit}/exercises/hello_world/en.md"
            elif change == "escape": row["source_provenance"]["official_tree_ref"] = "../tree.json"
            elif change == "title": row["input"]["title"] = "hello-world"
            elif change == "objective": row["input"]["objective"] = "Invent a new deliverable."
            elif change == "footer": row["input"]["context"].pop()
            elif change == "placeholder": row["input"]["context"][1]["text"] = row["input"]["context"][1]["text"].replace("{appname}", "how-to-markdown")
            elif change == "extra": row["input"]["context"].append(row["input"]["context"][0])
            elif change == "group": row["source_group"] += "/variant"
            else: row["split"] = "evaluation"
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.generate([row])
        with self.assertRaises(SystemExit):
            self.generate(provider="openai")
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(SystemExit):
            self.generate()
        train, dev = self.scenario(), self.scenario()
        dev["split"] = "dev"
        with self.assertRaises(SystemExit):
            self.generate([train, dev])

    def test_forged_review_capture_and_closed_manifest_fail_with_test_pins(self):
        for record, key, value in ((self.receipt, "response_id", "forged"), (self.receipt, "answer", "ACCEPT invented"),
                (self.receipt, "http_status", 0), (self.manifest[0], "source_repository", "other/repo"),
                (self.manifest[0], "captured_at", "2026-09-27T11:30:00Z"),
                (self.manifest[0], "captured_at", "2026-09-27T11:00:00+01:00"),
                (self.tree[0], "sha", "0" * 40), (self.admission, "suitable", ["headings", "extra"])):
            original = record[key]
            record[key] = value
            self.write_evidence()
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                self.generate()
            record[key] = original
        self.manifest.append(dict(self.manifest[0]))
        self.write_evidence()
        with self.assertRaises(SystemExit):
            self.generate()

    def test_heldout_source_digest_still_fails_before_probe(self):
        path = self.set_a / "metadata/ew-alpha.meta.json"
        record = json.loads(path.read_text())
        record["source_sha256"] = self.manifest[0]["source_sha256"]
        path.write_text(json.dumps(record), encoding="utf-8")
        with patch.object(generate_rows, "prepare_local_model") as probe, self.assertRaises(SystemExit):
            self.generate(dry_run=False)
        probe.assert_not_called()

class AccessibilityAdmissionTests(unittest.TestCase):
    """Offline evidence uses the existing Microsoft admission field names."""

    blob = staticmethod(LicensedSpecificationTests.blob)
    generate = LearnyounodeAdmissionTests.generate
    test_valid_dry_run_makes_no_effects = MarkdownAdmissionTests.test_valid_dry_run_makes_no_effects
    test_heldout_source_digest_still_fails_before_probe = MarkdownAdmissionTests.test_heldout_source_digest_still_fails_before_probe

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.set_a, self.set_b = write_sets(self.root)
        self.out = self.root / "rows.jsonl"
        self.review_root = self.root / "reviews"
        (self.review_root / "xai").mkdir(parents=True)
        self.repository, self.commit = generate_rows.ACCESSIBILITY_REPOSITORY, generate_rows.ACCESSIBILITY_COMMIT
        self.description = f"# {generate_rows.ACCESSIBILITY_TITLE}\n\n{generate_rows.ACCESSIBILITY_OBJECTIVE}\n"
        self.source_bytes = {"assignment.md": self.description.encode(), "LICENSE": b"MIT test-only source license"}
        self.manifest, self.tree = [], []
        for ref, raw in self.source_bytes.items():
            (self.root / ref).write_bytes(raw)
            path = generate_rows.ACCESSIBILITY_PATH if ref == "assignment.md" else ref
            self.manifest.append({"source_id": f"{self.repository}/{self.commit}/{path}",
                "source_repository": self.repository, "source_commit": self.commit, "source_tree": "b" * 40,
                "path": path, "source_ref": ref, "source_url": f"https://github.com/{self.repository}/blob/{self.commit}/{path}",
                "source_sha256": hashlib.sha256(raw).hexdigest(), "source_git_blob": self.blob(raw),
                "license": "MIT", "license_sha256": hashlib.sha256(self.source_bytes["LICENSE"]).hexdigest(),
                "upstream_commit_time": "2021-12-28T15:05:08Z", "captured_at": "2026-09-27T11:00:00.123456+00:00",
                "source_kind": "independently_licensed_specification", "destination": "training_candidates_only",
                "targets_generated": 0})
            self.tree.append({"path": path, "mode": "100644", "type": "blob", "sha": self.blob(raw), "size": len(raw)})
        api = f"https://api.github.com/repos/{self.repository}"
        self.commit_record = {"sha": self.commit, "url": f"{api}/commits/{self.commit}", "commit": {
            "committer": {"date": "2021-12-28T15:05:08Z"}, "tree": {"sha": "b" * 40, "url": f"{api}/git/trees/{'b' * 40}"}}}
        self.tree_record = {"sha": "b" * 40, "url": f"{api}/git/trees/{'b' * 40}", "truncated": False, "tree": self.tree}
        self.admission = {"disposition": "ACCEPT_FOR_SOURCE_FACTS", "source_pin": self.commit,
            "generated_targets": 0, "accepted_training_rows": 0, "recorded_at": "2026-09-27T11:20:00+00:00",
            "response_id": "independent-response", "receipt": r"C:\fixture\receipts\xai\review.json",
            "share_record_sha256": "a" * 64, "source_files": {}, "code_admission": "PENDING"}
        self.receipt = {"provider": "xai", "model": "grok-4.6", "http_status": 200,
            "response_status": "completed", "response_id": "independent-response"}
        pins = patch.dict(generate_rows.ACCESSIBILITY_ADMISSIONS, {}, clear=True)
        pins.start()
        self.addCleanup(pins.stop)
        self.write_evidence()

    def write_evidence(self):
        raws = {"manifest.jsonl": "".join(json.dumps(e) + "\n" for e in self.manifest).encode(),
                "commit.json": json.dumps(self.commit_record).encode(), "tree.json": json.dumps(self.tree_record).encode()}
        hashes = {ref: hashlib.sha256(raw).hexdigest() for ref, raw in raws.items()}
        self.admission["manifest_sha256"] = hashes["manifest.jsonl"]
        self.admission["source_files"].update({"commit-evidence.json": hashes["commit.json"],
                                             "tree-evidence.json": hashes["tree.json"]})
        answer = "**Verdict:** `ACCEPT_FOR_SOURCE_FACTS` " + " ".join(
            (self.admission["manifest_sha256"], self.commit, self.admission["share_record_sha256"]))
        if self.receipt.get("answer") in (None, getattr(self, "review_answer", None)):
            self.receipt["answer"] = answer
        self.review_answer = answer
        self.admission["verdict_sha256"] = hashlib.sha256((self.receipt["answer"] + "\n").encode()).hexdigest()
        receipt = json.dumps(self.receipt).encode()
        self.admission["receipt_sha256"] = hashlib.sha256(receipt).hexdigest()
        raws["admission.json"] = json.dumps(self.admission).encode()
        for ref, raw in raws.items():
            (self.root / ref).write_bytes(raw)
        (self.review_root / "xai/review.json").write_bytes(receipt)
        self.digest = hashlib.sha256(raws["admission.json"]).hexdigest()
        generate_rows.ACCESSIBILITY_ADMISSIONS[self.digest] = tuple(hashes[ref]
            for ref in ("manifest.jsonl", "commit.json", "tree.json")) + (self.admission["receipt_sha256"],)

    def scenario(self):
        entry = self.manifest[0]
        return {"source_group": f"licensed/{self.repository}/accessibility", "split": "train",
            "source_provenance": {"kind": "licensed_specification", "source_id": entry["source_id"],
                "admission_ref": "admission.json", "admission_sha256": self.digest, "manifest_ref": "manifest.jsonl",
                "official_capture_ref": "commit.json", "official_tree_ref": "tree.json", "license_ref": "LICENSE",
                "admitted_at": self.admission["recorded_at"].replace("+00:00", "Z")},
            "input": {"repository": self.repository, "title": generate_rows.ACCESSIBILITY_TITLE,
                "objective": generate_rows.ACCESSIBILITY_OBJECTIVE, "context": [{"kind": "assignment_packet",
                    "source_ref": entry["source_ref"], "source_sha256": entry["source_sha256"], "text": self.description}]}}

    def test_changed_artifacts_fail_before_probe(self):
        paths = [self.root / ref for ref in (*self.source_bytes, "manifest.jsonl", "admission.json", "commit.json", "tree.json")]
        paths.append(self.review_root / "xai/review.json")
        with patch.object(generate_rows, "prepare_local_model") as probe:
            for path in paths:
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.subTest(path=path.name), self.assertRaises(SystemExit):
                    self.generate(dry_run=False)
                path.write_bytes(original)
            probe.assert_not_called()

    def test_input_identity_paths_and_local_only_remain_closed(self):
        for change in ("unknown", "task", "escape", "title", "objective", "context", "extra", "group", "split"):
            row = self.scenario()
            if change == "unknown": row["source_provenance"]["admission_sha256"] = "0" * 64
            elif change == "task": row["source_provenance"]["source_id"] = self.manifest[1]["source_id"]
            elif change == "escape": row["source_provenance"]["official_tree_ref"] = "../tree.json"
            elif change == "title": row["input"]["title"] = "accessibility"
            elif change == "objective": row["input"]["objective"] = "Identify a website"
            elif change == "context": row["input"]["context"][0]["text"] = generate_rows.ACCESSIBILITY_OBJECTIVE
            elif change == "extra": row["input"]["context"].append(row["input"]["context"][0])
            elif change == "group": row["source_group"] += "/variant"
            else: row["split"] = "evaluation"
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.generate([row])
        for provider in generate_rows.COMPETING_PROVIDERS:
            with self.subTest(provider=provider), self.assertRaises(SystemExit):
                self.generate(provider=provider)
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(SystemExit):
            self.generate()
        dev = self.scenario()
        dev["split"] = "dev"
        self.assertEqual(self.generate([dev]), {"dry_run": True, "rows": 0, "receipts": 0})
        with self.assertRaises(SystemExit):
            self.generate([self.scenario(), dev])

    def test_forged_receipt_capture_and_chronology_fail_even_with_test_pins(self):
        for record, key, value in ((self.receipt, "response_id", "forged"), (self.receipt, "answer", "ACCEPT invented"),
                (self.receipt, "http_status", 0), (self.receipt, "response_status", "incomplete"),
                (self.receipt, "model", "other"), (self.manifest[0], "source_commit", "0" * 40),
                (self.manifest[0], "captured_at", "2026-09-27T11:30:00Z"),
                (self.manifest[0], "captured_at", "2026-09-27T11:00:00+01:00"),
                (self.tree[0], "sha", "0" * 40), (self.tree_record, "truncated", True),
                (self.tree_record, "sha", self.commit),
                (self.tree_record, "url", f"https://api.github.com/repos/{self.repository}/git/trees/{self.commit}"),
                (self.admission, "disposition", "PENDING"), (self.admission, "generated_targets", 1)):
            original = record[key]
            record[key] = value
            self.write_evidence()
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                self.generate()
            record[key] = original
        self.write_evidence()
        with patch.object(generate_rows.local_transport, "utc_now", return_value="2026-09-27T11:10:00Z"), self.assertRaises(SystemExit):
            self.generate()



class NativeSdkTransportTests(unittest.TestCase):
    """Mocked model lifecycle; actual harmless contained child tests. No SDK client."""
    setUp = GenerateRowsTests.setUp
    default_scenario = GenerateRowsTests.default_scenario
    local_options = GenerateRowsTests.local_options
    generate_local = GenerateRowsTests.generate_local

    def native_fixture(self):
        options = self.local_options()
        options["model"] = "m2-native-offline"
        runtime_path = self.root / "runtime.json"
        runtime_path.write_text('{"offline_fixture":true}', encoding="utf-8")
        runtime = {"node": sys.executable, "sdk_entry": str(self.root / "dist" / "index.mjs"),
                   "model_key": "fixture/catalog", "lms_models_dir": str(options["lms_models_dir"])}
        self.operator_pin = (self.root / "OFFLINE-MOCK-not-a-live-release", "f" * 64)
        release = {"attempt_ordinal": 3, "release_sha256": "f" * 64,
                   "runtime_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest()}
        for name, value in (("NATIVE_GPU_MUTEX", "Global\\homemade-llm-gpu.TEST-" + self.root.name),
                            ("NATIVE_ATTEMPT_ROOT", self.root / "attempts")):
            patched = patch.object(generate_rows, name, value); patched.start(); self.addCleanup(patched.stop)
        marker = patch.object(generate_rows, "_native_gpu_marker", return_value=self.root / "owned-marker.json")
        marker.start(); self.addCleanup(marker.stop)
        for name, value in (("native_execution_release", release), ("_native_runtime", runtime),
                            ("_native_resource_checks", {"offline_mock": True})):
            patched = patch.object(generate_rows, name, return_value=value)
            patched.start(); self.addCleanup(patched.stop)
        def checked_resources():
            self.assertTrue((self.root / "owned-marker.json").exists())
            return {"offline_mock": True}
        generate_rows._native_resource_checks.side_effect = checked_resources
        original = generate_rows.native_execution_release
        def checked_release(*args, operator_pin=None):
            self.assertIs(operator_pin, self.operator_pin)
            return release
        original.side_effect = checked_release
        process = patch.object(generate_rows, "_native_process", side_effect=self.child_response)
        self.native_child = process.start(); self.addCleanup(process.stop)
        self.runtime_path = runtime_path
        return options

    @staticmethod
    def response(request):
        # Deliberately mocked result carrying the production-shaped provenance contract.
        # This fixture does not authenticate a live SDK/backend.
        info = {"identifier": request["alias"], "instanceReference": "offline-owned-instance",
                **{key: request["expectedModel"][key] for key in ("modelKey", "path", "sizeBytes")}, "deviceIdentifier": None}
        predicted = {"llm.prediction.temperature": 0,
                     "llm.prediction.maxPredictedTokens": {"checked": True, "value": request["maxOutput"]},
                     "llm.prediction.reasoning.enableThinking": False,
                     "llm.prediction.contextOverflowPolicy": "stopAtLimit",
                     "llm.prediction.structured": {"type": "json", "jsonSchema": request["schema"]},
                     "future.opaque": {"checked": False, "value": 42}}
        loaded = {"llm.load.contextLength": 8192, "llm.load.llama.acceleration.offloadRatio": "max",
                  "llm.load.llama.autoFit": False}
        return {"protocol": generate_rows.NATIVE_PROTOCOL, "status": "NATIVE_COMPLETED", "inferenceCalls": 1,
                "requestSha256": generate_rows.local_transport.sha256(generate_rows._sdk_json(request)),
                "messagesSha256": generate_rows.local_transport.sha256(generate_rows._sdk_json(request["messages"])),
                "schemaSha256": generate_rows.local_transport.sha256(generate_rows._sdk_json(request["schema"])),
                "server": request["expectedServer"], "sdkProvenance": "PINNED_PUBLIC_PACKAGE",
                "returnedModel": info, "roundIndex": 0, "stats": {"stopReason": "eosFound"},
                "content": json.dumps(TARGET), "reasoningContent": "", "nonReasoningContent": json.dumps(TARGET),
                "requestedPredictionConfig": {"temperature": 0, "maxTokens": request["maxOutput"],
                    "enableThinking": False, "contextOverflowPolicy": "stopAtLimit",
                    "structured": {"type": "json", "jsonSchema": request["schema"]}},
                "predictionConfig": {"state": "RETURNED_RAW_UNINTERPRETED",
                                     "value": {"fields": [{"key": k, "value": v} for k, v in predicted.items()]}},
                "loadConfig": {"state": "RETURNED_RAW_UNINTERPRETED",
                               "value": {"fields": [{"key": k, "value": v} for k, v in loaded.items()]}},
                "lifecycle": {"ownedInstance": info, "loadCalls": 1, "unloadCalls": 1,
                    "requestedLoadConfig": {"contextLength": 8192, "gpu": {"ratio": "max"}},
                    "ownedAliasUnloaded": "ABSENCE_OBSERVED", "clientDisposal": "CONNECTIONS_CLOSED",
                    "backendStopped": "UNKNOWN"}}

    def child_response(self, argv, payload, deadline):
        self.assertTrue((self.root / "owned-marker.json").exists())
        attempts = list(generate_rows.NATIVE_ATTEMPT_ROOT.glob("*.json"))
        self.assertEqual(len(attempts), 1)
        request = generate_rows.local_transport.strict_json(payload)
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["enableThinking"], False)
        self.assertIn("generationDeadlineUnixMs", request)
        response = self.response(request)
        if hasattr(self, "mutation"):
            self.mutation(response)
        return generate_rows._sdk_json(response), {"pid": 0, "exit_code": 0, "child_tree_closed": True,
                                                   "evidence": "OFFLINE_MOCK_ONLY"}

    def test_operator_bootstrap_hash_graph_is_non_circular_and_cli_cannot_supply_pin(self):
        # Graph proof only: no production release A or launch B is created/configured.
        # Complete candidate bytes remain frozen while downstream evidence is built.
        candidate = Path(generate_rows.__file__).read_bytes()
        candidate_hash = hashlib.sha256(candidate).hexdigest()
        runtime = {"proof_scope": "OFFLINE_GRAPH_ONLY_NOT_A_RUNTIME",
                   "code_sha256": {"eval/generate_rows.py": candidate_hash}}
        runtime_hash = generate_rows._canonical_hash(runtime)
        release = {"proof_scope": "OFFLINE_GRAPH_ONLY_NOT_A_RELEASE",
                   "runtime_sha256": runtime_hash, "attempt_ordinal": 3,
                   "preserved_history_sha256": "a" * 64}
        release_hash = generate_rows._canonical_hash(release)
        launch = {"proof_scope": "OFFLINE_GRAPH_ONLY_NOT_A_LAUNCH",
                  "candidate_sha256": candidate_hash, "runtime_sha256": runtime_hash,
                  "release_sha256": release_hash}
        self.assertEqual(launch["candidate_sha256"], hashlib.sha256(Path(generate_rows.__file__).read_bytes()).hexdigest())
        self.assertEqual(launch["release_sha256"], generate_rows._canonical_hash(release))
        self.assertNotIn("NATIVE_RELEASE_PIN", candidate.decode("utf-8"))
        with patch.object(generate_rows, "_native_process") as process:
            with self.assertRaises(SystemExit):
                generate_rows.native_execution_release(self.default_scenario(), "not-a-release", self.root / "not-runtime")
            with self.assertRaises(SystemExit):
                generate_rows.main(["--scenarios", "unused", "--provider", "local_model", "--set-a", "unused", "--set-b", "unused",
                                    "--out", "unused", "--terms-verified-at", UTC, "--native-release-pin", "caller-GO"])
            process.assert_not_called()
        self.assertEqual(candidate, Path(generate_rows.__file__).read_bytes())

    def test_native_dry_run_has_zero_effects_and_preserves_refs(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in (self.set_a, self.set_b) for p in root.glob("*.json")}
        with patch.object(generate_rows, "prepare_local_model") as prepare, patch.object(generate_rows, "_native_process") as child, patch.object(generate_rows, "native_execution_release") as release, patch.object(generate_rows, "_native_resources") as resources, patch.object(generate_rows, "_native_gpu_marker") as marker:
            report = self.generate_local({}, native_sdk=True, dry_run=True)
            self.assertEqual(report, {"dry_run": True, "rows": 0, "receipts": 0})
            prepare.assert_not_called(); child.assert_not_called(); release.assert_not_called()
            resources.assert_not_called(); marker.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.root / "receipts").exists())
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in (self.set_a, self.set_b) for p in root.glob("*.json")}
        self.assertEqual(before, after)

    def test_native_held_release_and_incompatible_modes_have_no_probes(self):
        options = self.local_options()
        with patch.object(generate_rows, "prepare_local_model") as prepare, patch.object(generate_rows, "_native_process") as child:
            with self.assertRaises(SystemExit):
                self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.root / "missing"})
            with self.assertRaises(SystemExit):
                self.generate_local(options, native_sdk=True, quote_first=True, dry_run=True)
            prepare.assert_not_called(); child.assert_not_called()
        self.local_probe.assert_not_called(); self.local_http.assert_not_called()

    def test_native_success_exact_contract_receipt_and_collision(self):
        options = self.native_fixture()
        report = self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path}, native_release_pin=self.operator_pin)
        self.assertEqual(report["rows"], 1); self.native_child.assert_called_once()
        self.local_probe.assert_not_called(); self.local_http.assert_not_called()
        receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
        expected = generate_rows.prompt_template.render_messages(self.default_scenario()["input"])
        expected[0]["content"] += generate_rows.local_transport.QWEN3_PROMPT
        self.assertEqual(receipt["request"]["messages"], expected)
        self.assertEqual(receipt["request"]["schema"], generate_rows._schema_validator().schema["properties"]["target"])
        self.assertNotEqual(receipt["request_sha256"], receipt["sdk_request_utf8_sha256"])
        self.assertEqual(receipt["response"]["lifecycle"]["backendStopped"], "UNKNOWN")
        self.assertEqual(receipt["cleanup"], "OWNED_ABSENCE_AND_CHILD_DEATH_OBSERVED")
        # Mechanical completion does not release a GPU whose backend stop is UNKNOWN.
        self.assertTrue((self.root / "owned-marker.json").exists())
        self.assertEqual(len(list(generate_rows.NATIVE_ATTEMPT_ROOT.glob("*.json"))), 1)
        self.out = self.root / "second.jsonl"
        with self.assertRaises(FileExistsError):
            self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path}, native_release_pin=self.operator_pin)
        self.native_child.assert_called_once()

    def test_native_config_refusal_preserves_failure_without_replay(self):
        options = self.native_fixture()
        self.mutation = lambda r: r["predictionConfig"]["value"]["fields"].append(r["predictionConfig"]["value"]["fields"][0])
        with self.assertRaises(SystemExit):
            self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path}, native_release_pin=self.operator_pin)
        self.native_child.assert_called_once(); self.assertFalse(self.out.exists())
        receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["response"]["predictionConfig"]["value"]["fields"][-2]["value"], {"checked": False, "value": 42})
        self.assertTrue((self.root / "owned-marker.json").exists())

    def test_native_resource_refusal_has_durable_poison_and_zero_model_calls(self):
        options = self.native_fixture()
        def refuse_resources():
            self.assertTrue((self.root / "owned-marker.json").exists())
            raise ValueError("offline resource refusal")
        generate_rows._native_resource_checks.side_effect = refuse_resources
        with self.assertRaises(SystemExit):
            self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path},
                                native_release_pin=self.operator_pin)
        self.native_child.assert_not_called(); self.local_probe.assert_not_called(); self.local_http.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertTrue((self.root / "owned-marker.json").exists())
        self.assertEqual(len(list(generate_rows.NATIVE_ATTEMPT_ROOT.glob("*.json"))), 1)
        receipt = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["cleanup"], "UNKNOWN")
        self.assertNotIn("response", receipt)

    def test_native_independent_result_refusals(self):
        options = self.native_fixture()
        self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path}, native_release_pin=self.operator_pin)
        record = json.loads(next(options["receipts_dir"].glob("*.json")).read_text(encoding="utf-8"))
        changes = [lambda r: r.update(roundIndex=1), lambda r: r.update(reasoningContent="hidden"),
                   lambda r: r["stats"].update(stopReason="contextLengthReached"),
                   lambda r: r["returnedModel"].update(instanceReference="different"),
                   lambda r: r["lifecycle"].update(ownedAliasUnloaded="UNKNOWN"),
                   lambda r: r["predictionConfig"].update(state="UNKNOWN"),
                   lambda r: r["predictionConfig"]["value"]["fields"].pop(0),
                   lambda r: r["predictionConfig"]["value"]["fields"][1].update(value={"checked": False, "value": 2048}),
                   lambda r: r["loadConfig"]["value"]["fields"].pop(0)]
        for change in changes:
            response = json.loads(json.dumps(record["response"]))
            change(response)
            with self.assertRaises(ValueError):
                generate_rows._native_answer(response, record["request"])

    def test_native_cleanup_unknown_retains_owned_marker(self):
        options = self.native_fixture()
        self.mutation = lambda r: r["lifecycle"].update(ownedAliasUnloaded="UNKNOWN")
        with self.assertRaises(SystemExit):
            self.generate_local(options, native_sdk=True, native_options={"runtime_manifest": self.runtime_path}, native_release_pin=self.operator_pin)
        self.assertTrue((self.root / "owned-marker.json").exists())
        self.assertFalse(self.out.exists()); self.native_child.assert_called_once()

    def test_actual_contained_child_success_timeout_overflow_and_no_threads(self):
        import time
        import threading
        before = {thread.ident for thread in threading.enumerate()}
        raw, proof = generate_rows._native_process([sys.executable, "-c", "print('{}',end='')"], b"", time.monotonic() + 3)
        self.assertEqual(raw, b"{}"); self.assertTrue(proof["child_tree_closed"])
        for code, delay in (("import time;time.sleep(10)", .3), ("print('x'*1100000)", 3),
                            ("import sys;sys.stderr.write('private text');sys.exit(2)", 3)):
            with self.assertRaises(ValueError) as caught:
                generate_rows._native_process([sys.executable, "-c", code], b"", time.monotonic() + delay)
            self.assertNotIn("private text", str(caught.exception))
            self.assertTrue(caught.exception.child["child_tree_closed"])
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)

    def test_actual_contained_child_descendant_is_dead(self):
        import time
        code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid,flush=True)"
        raw, proof = generate_rows._native_process([sys.executable, "-c", code], b"", time.monotonic() + 3)
        self.assertTrue(proof["child_tree_closed"])
        pid = int(raw.strip())
        if generate_rows.os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
                finally:
                    kernel.CloseHandle(handle)
        else:
            # POSIX killed child may remain a zombie until its OS parent reaps it.
            proc = Path(f"/proc/{pid}/stat")
            self.assertTrue(not proc.exists() or proc.read_text().split()[2] == "Z")


@unittest.skipUnless(generate_rows.os.name == "nt", "actual harmless Windows GPU guard interoperation")
class NativeSharedGpuGuardTests(unittest.TestCase):
    """Unique TEST mutex/temp poison only; no production lock, SDK or GPU work."""
    def setUp(self):
        import uuid
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.marker = self.root / "test-owned-poison.json"
        self.name = "Global\\homemade-llm-gpu.TEST-" + uuid.uuid4().hex
        self.owner = {"owner_token": uuid.uuid4().hex, "fixture": "OFFLINE_GUARD_ONLY"}
        self.retained = []
        self.children = []
        self.patches = [patch.object(generate_rows, "NATIVE_GPU_MUTEX", self.name),
                        patch.object(generate_rows, "_native_gpu_marker", return_value=self.marker),
                        patch.object(generate_rows, "_NATIVE_GPU_RETAINED", self.retained)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        # Only this test's children/handles/temp directory; production state is untouched.
        for child, stop in self.children:
            stop.touch()
            try:
                child.communicate(timeout=10)
            except generate_rows.subprocess.TimeoutExpired:
                child.kill(); child.communicate(timeout=5)
        for kernel, handle in self.retained:
            self.assertTrue(kernel.ReleaseMutex(handle))  # Same test-owning thread.
            self.assertTrue(kernel.CloseHandle(handle))
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def ps(self, body):
        import subprocess
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", body],
                              cwd=self.root, capture_output=True, text=True, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def hold_in_ps(self, *, abandoned=False):
        import subprocess
        import time
        ready = self.root / "ready"
        stop = self.root / "stop"
        release = "" if abandoned else "$m.ReleaseMutex();"
        script = ("$ErrorActionPreference='Stop'; $m=[Threading.Mutex]::new($false,'" + self.name + "'); "
                  "if(-not $m.WaitOne(0)){exit 2}; [IO.File]::WriteAllText('" + str(ready) + "','ready'); "
                  "$end=[DateTime]::UtcNow.AddSeconds(8); "
                  "while(-not (Test-Path -LiteralPath '" + str(stop) + "') -and [DateTime]::UtcNow -lt $end){Start-Sleep -Milliseconds 10}; "
                  + release + "$m.Dispose();")
        child = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                 cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.children.append((child, stop))
        end = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(ready.exists(), "test PS mutex holder did not become ready")
        return child, stop

    def probe_in_ps(self):
        result = self.ps("$ErrorActionPreference='Stop'; $m=[Threading.Mutex]::new($false,'" + self.name + "'); "
                         "$held=$false; try { $held=$m.WaitOne(0); if(-not $held){'BUSY'} "
                         "elseif(Test-Path -LiteralPath '" + str(self.marker) + "'){'POISON'} else {'FREE'} } "
                         "finally {if($held){$m.ReleaseMutex()};$m.Dispose()}")
        self.assertEqual(result.returncode, 0, "harmless PS probe failed")
        return result.stdout.strip()

    def test_python_and_powershell_exclude_each_other(self):
        child, stop = self.hold_in_ps()
        with self.assertRaisesRegex(ValueError, "mutex occupied"):
            with generate_rows._native_resources(self.owner):
                self.fail("busy shared mutex allowed entry")
        self.assertFalse(self.marker.exists())
        stop.touch(); child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0)
        with generate_rows._native_resources(self.owner):
            self.assertEqual(json.loads(self.marker.read_bytes())["owner_token"], self.owner["owner_token"])
            self.assertEqual(self.probe_in_ps(), "BUSY")
            self.owner["cleanup_confirmed"] = True
            self.owner["backend_stopped"] = True  # Test-only declared termination, not SDK proof.
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.probe_in_ps(), "FREE")

    def test_poison_refuses_and_foreign_bytes_remain(self):
        foreign = b"foreign test-owned poison\n"
        self.marker.write_bytes(foreign)
        with self.assertRaisesRegex(ValueError, "GPU poisoned"):
            with generate_rows._native_resources(self.owner):
                self.fail("foreign poison allowed entry")
        self.assertEqual(self.marker.read_bytes(), foreign)
        self.assertEqual(self.probe_in_ps(), "POISON")

    def test_unknown_cleanup_retains_poison_for_powershell(self):
        with generate_rows._native_resources(self.owner):
            pass  # No successful cleanup declaration; no model is called.
        before = self.marker.read_bytes()
        self.assertEqual(self.probe_in_ps(), "POISON")
        with self.assertRaisesRegex(ValueError, "GPU poisoned"):
            with generate_rows._native_resources(self.owner):
                self.fail("unknown cleanup allowed replay")
        self.assertEqual(self.marker.read_bytes(), before)

    def test_backend_unknown_retains_poison_despite_other_cleanup_observations(self):
        self.owner["backend_stopped"] = "UNKNOWN"
        with generate_rows._native_resources(self.owner):
            self.owner["cleanup_confirmed"] = True
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.probe_in_ps(), "POISON")

    def test_abandoned_mutex_refuses_and_publishes_poison(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        # Keep a reference alive, otherwise last-handle close destroys the test object.
        reference = kernel.CreateMutexW(None, False, self.name)
        self.assertTrue(reference)
        try:
            child, stop = self.hold_in_ps(abandoned=True)
            stop.touch(); child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0)
            with self.assertRaisesRegex(ValueError, "mutex abandoned"):
                with generate_rows._native_resources(self.owner):
                    self.fail("abandonment allowed entry")
            self.assertTrue(self.marker.exists())
            self.assertEqual(self.probe_in_ps(), "POISON")
        finally:
            self.assertTrue(kernel.CloseHandle(reference))

    def test_marker_write_failure_prevents_entry(self):
        with patch.object(Path, "open", side_effect=OSError("test marker write refusal")):
            with self.assertRaises(OSError):
                with generate_rows._native_resources(self.owner):
                    self.fail("write failure allowed entry")
        self.assertEqual(self.probe_in_ps(), "FREE")  # No operation was started.

    def test_foreign_replacement_is_never_removed(self):
        foreign = b"replacement owned by another test actor\n"
        with self.assertRaisesRegex(ValueError, "marker ownership changed"):
            with generate_rows._native_resources(self.owner):
                self.marker.write_bytes(foreign)
                self.owner["cleanup_confirmed"] = True
                self.owner["backend_stopped"] = True  # Test-only known-stop branch.
        self.assertEqual(self.marker.read_bytes(), foreign)
        self.assertEqual(self.probe_in_ps(), "BUSY")
        self.assertEqual(len(self.retained), 1)

    def test_missing_uncertain_marker_is_repaired_or_mutex_retained(self):
        with generate_rows._native_resources(self.owner):
            self.marker.unlink()  # Test-owned temporary marker, never production.
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.probe_in_ps(), "POISON")
        self.marker.unlink()
        failure = patch.object(Path, "open", side_effect=OSError("test repair refusal"))
        try:
            with self.assertRaises(OSError):
                with generate_rows._native_resources(self.owner):
                    self.marker.unlink()
                    failure.start()  # Remains installed through the guard's finalization.
        finally:
            failure.stop()
        self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.retained), 1)
        self.assertEqual(self.probe_in_ps(), "BUSY")
        # Handle retention is local only; it cannot guarantee safety after process exit.

    def test_cross_thread_exit_cannot_clear_poison_or_release_mutex(self):
        import threading
        context = generate_rows._native_resources(self.owner)
        context.__enter__()
        self.owner["cleanup_confirmed"] = True
        before = self.marker.read_bytes()
        failures = []
        def exit_elsewhere():
            try:
                context.__exit__(None, None, None)
            except ValueError as exc:
                failures.append(str(exc))
        worker = threading.Thread(target=exit_elsewhere)
        worker.start(); worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIn("ownership thread changed", failures[0])
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertEqual(len(self.retained), 1)
        self.assertEqual(self.probe_in_ps(), "BUSY")

    def test_system_marker_resolution_matches_existing_powershell_protocol(self):
        # Read-only location comparison; no production marker/mutex is touched.
        original = self.patches[1]
        original.stop()
        try:
            actual = generate_rows._native_gpu_marker()
        finally:
            original.start()
        expected = self.ps("Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'homemade-llm\\gpu-poisoned.txt'")
        self.assertEqual(expected.returncode, 0)
        self.assertEqual(actual, Path(expected.stdout.strip()))


class NativeHotRootAccountingTests(unittest.TestCase):
    """Offline metadata accounting only; no real cache, mutex or model operation."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "regular").write_bytes(b"123")
        self.blob = self.root / "hf" / "hub" / "models--fixture" / "blobs" / ("a" * 64)
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(b"pointer fixture")

    def metadata(self, *, tag=0xA000001D, directory=False, size=0):
        return Mock(st_mode=generate_rows.stat.S_IFDIR if directory else generate_rows.stat.S_IFREG,
                    st_size=size, st_file_attributes=0x400 | (0x10 if directory else 0),
                    st_reparse_tag=tag)

    def changed_metadata(self, path, metadata):
        original = Path.lstat
        return patch.object(Path, "lstat", lambda item: metadata if str(item).removeprefix("\\\\?\\") == str(path) else original(item))

    def test_regular_files_and_recognized_cache_pointer_count_own_bytes_once(self):
        with self.changed_metadata(self.blob, self.metadata(size=7)):
            self.assertEqual(generate_rows._native_hot_bytes(self.root), 10)
        with self.changed_metadata(self.blob, self.metadata(size=0)):
            self.assertEqual(generate_rows._native_hot_bytes(self.root), 3)

    def test_unknown_tag_and_pointer_outside_cache_refuse(self):
        for path, tag in ((self.blob, 0xA000000C), (self.root / "regular", 0xA000001D)):
            with self.subTest(path=path, tag=tag), self.changed_metadata(path, self.metadata(tag=tag)):
                with self.assertRaisesRegex(ValueError, "reparse requires reviewed accounting"):
                    generate_rows._native_hot_bytes(self.root)

    def test_directory_reparses_refuse_before_traversal(self):
        original = generate_rows.os.scandir
        for path in (self.root, self.blob.parent):
            with self.subTest(path=path), self.changed_metadata(path, self.metadata(directory=True)), \
                    patch.object(generate_rows.os, "scandir", wraps=original) as scan:
                with self.assertRaisesRegex(ValueError, "reparse requires reviewed accounting"):
                    generate_rows._native_hot_bytes(self.root)
                self.assertNotIn(str(path), [str(call.args[0]).removeprefix("\\\\?\\") for call in scan.call_args_list])

    @unittest.skipUnless(generate_rows.os.name == "nt", "Windows extended-length path accounting")
    def test_long_regular_path_counts_without_cache_mutation(self):
        long_path = self.root / ("d" * 120) / ("e" * 120) / "regular"
        extended = Path("\\\\?\\" + str(long_path))
        extended.parent.mkdir(parents=True)
        self.addCleanup(extended.parent.rmdir)
        self.addCleanup(extended.unlink)
        extended.write_bytes(b"12345")
        self.assertGreater(len(str(long_path)), 260)
        self.assertEqual(generate_rows._native_hot_bytes(self.root), 3 + self.blob.stat().st_size + 5)

    def test_inaccessible_metadata_and_directory_scan_propagate(self):
        with patch.object(Path, "lstat", side_effect=PermissionError("offline inaccessible")):
            with self.assertRaises(PermissionError):
                generate_rows._native_hot_bytes(self.root)
        with patch.object(generate_rows.os, "scandir", side_effect=PermissionError("offline inaccessible")):
            with self.assertRaises(PermissionError):
                generate_rows._native_hot_bytes(self.root)

    def test_unclassifiable_entries_and_file_root_refuse(self):
        unknown = Mock(st_mode=generate_rows.stat.S_IFIFO, st_size=0,
                       st_file_attributes=0, st_reparse_tag=0)
        with self.changed_metadata(self.blob, unknown):
            with self.assertRaisesRegex(ValueError, "entry unclassifiable"):
                generate_rows._native_hot_bytes(self.root)
        with self.assertRaisesRegex(ValueError, "not a directory"):
            generate_rows._native_hot_bytes(self.root / "regular")

    def test_resource_floors_cap_and_competing_producer_remain_refusals(self):
        for free, hot, producers, refusal in (
                (149 * 1024**3, 0, b"0", "C free floor"),
                (150 * 1024**3, 120 * 1024**3, b"0", "Cllm cap"),
                (150 * 1024**3, 3, b"1", "competing producer")):
            with self.subTest(refusal=refusal), patch.object(generate_rows.os, "name", "nt"), \
                    patch.object(generate_rows.shutil, "disk_usage", return_value=Mock(free=free)), \
                    patch.object(generate_rows, "_native_hot_bytes", return_value=hot), \
                    patch.object(generate_rows, "_native_process", return_value=(producers, {})):
                with self.assertRaisesRegex(ValueError, refusal):
                    generate_rows._native_resource_checks()


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-30 | 23:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-10-02 | 10:48 UTC | END]]
