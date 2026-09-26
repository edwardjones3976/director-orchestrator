# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:51 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | BEGIN]]
"""Unit tests for build_inputs.py (synthetic fixtures only).

    python projects/homemade-llm/eval/test_build_inputs.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_inputs  # noqa: E402


def _minimal_ref(
    packet_id: str = "p1",
    objective: str = "Deliver the feature with clean tests.",
    repository: str = "org/repo",
    *,
    non_goals: list[str] | None = None,
    constraints: list[str] | None = None,
    acceptance_checks: list[str] | None = None,
    evidence_requirements: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": packet_id,
        "assignment_id": "a1",
        "project": "demo",
        "repository": repository,
        "starting_ref": "main",
        "objective": objective,
        "non_goals": non_goals if non_goals is not None else ["skip unrelated work"],
        "authority_snapshot": [
            {"path": "AGENTS.md", "commit_sha": "a" * 40, "blob_sha": "b" * 40}
        ],
        "authority_snapshot_digest": "sha256:" + "c" * 64,
        "dependencies": [],
        "constraints": constraints if constraints is not None else ["stdlib only"],
        "prohibited_actions": [],
        "allowed_tools": [],
        "allowed_external_effects": [],
        "acceptance_checks": acceptance_checks if acceptance_checks is not None else ["tests pass"],
        "evidence_requirements": evidence_requirements
        if evidence_requirements is not None
        else ["command output"],
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


class BuildInputsTests(unittest.TestCase):
    def _layout(self, tmp: Path) -> tuple[Path, Path, Path]:
        root = tmp / "dataset"
        refs = root / "refs"
        out = root / "inputs"
        refs.mkdir(parents=True)
        (refs / "metadata").mkdir()
        return root, refs, out

    def test_source_file_rejects_absolute_and_parent_escape(self):
        for source_kind in ("absolute_inside", "absolute_outside", "parent_escape"):
            with self.subTest(source_kind=source_kind), tempfile.TemporaryDirectory() as tmp:
                root, refs, out = self._layout(Path(tmp))
                source_file = {
                    "absolute_inside": str(root / "packet.md"),
                    "absolute_outside": str(Path(tmp) / "packet.md"),
                    "parent_escape": "../packet.md",
                }[source_kind]
                (refs / "p1.json").write_text(json.dumps(_minimal_ref()), encoding="utf-8")
                (refs / "metadata" / "p1.meta.json").write_text(
                    json.dumps({"source_file": source_file}), encoding="utf-8"
                )
                errors = io.StringIO()
                with patch.object(build_inputs, "_title_from_heading") as read_heading, redirect_stderr(errors):
                    self.assertEqual(build_inputs.build(
                        ["--set", "a", "--refs", str(refs), "--out", str(out)]
                    ), 1)
                read_heading.assert_not_called()
                self.assertIn("p1", errors.getvalue())
                self.assertFalse(out.exists())
                self.assertFalse(out.with_name("inputs.build.json").exists())

    def test_stale_run_record_refused_before_validation(self):
        for empty_out in (False, True):
            with self.subTest(empty_out=empty_out), tempfile.TemporaryDirectory() as tmp:
                root, refs, out = self._layout(Path(tmp))
                if empty_out:
                    out.mkdir()
                run_path = root / "inputs.build.json"
                run_path.write_text("stale record", encoding="utf-8")
                errors = io.StringIO()
                with patch.object(build_inputs, "_reference_ids") as validate, redirect_stderr(errors):
                    self.assertEqual(build_inputs.build(
                        ["--set", "b", "--refs", str(refs), "--out", str(out)]
                    ), 1)
                validate.assert_not_called()
                self.assertIn(str(run_path), errors.getvalue())
                self.assertEqual(run_path.read_text(encoding="utf-8"), "stale record")
                self.assertEqual(out.exists(), empty_out)
                if empty_out:
                    self.assertEqual(list(out.iterdir()), [])

    def test_publish_failure_removes_output_and_partial_record(self):
        for stage in ("rename", "record_write", "record_replace"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root, refs, out = self._layout(Path(tmp))
                (refs / "p1.json").write_text(json.dumps(_minimal_ref()), encoding="utf-8")
                (refs / "metadata" / "p1.meta.json").write_text(
                    json.dumps({"source_title": "Safe Title"}), encoding="utf-8"
                )
                write_text = Path.write_text

                def fail_write(path, text, **kwargs):
                    if path.name.startswith("inputs.build.json."):
                        self.assertTrue(out.is_dir())
                        write_text(path, "partial", **kwargs)
                        raise OSError("record write failed")
                    return write_text(path, text, **kwargs)

                def fail_replace(source, destination):
                    self.assertTrue(out.is_dir())
                    self.assertFalse(Path(destination).exists())
                    record = json.loads(Path(source).read_text(encoding="utf-8"))
                    self.assertEqual(record["input_manifest"], build_inputs.manifest(out))
                    raise OSError("record replace failed")

                failure = {
                    "rename": patch.object(Path, "rename", side_effect=OSError("rename failed")),
                    "record_write": patch.object(Path, "write_text", new=fail_write),
                    "record_replace": patch.object(build_inputs.os, "replace", side_effect=fail_replace),
                }[stage]
                with failure, self.assertRaises(OSError):
                    build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
                self.assertEqual(list(root.iterdir()), [refs])

    def _assert_leak_rejected(self, reference):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            (refs / "p1.json").write_text(json.dumps(reference), encoding="utf-8")
            (refs / "metadata" / "p1.meta.json").write_text(
                json.dumps({"source_title": "Safe Title"}), encoding="utf-8"
            )
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(build_inputs.build(
                    ["--set", "b", "--refs", str(refs), "--out", str(out)]
                ), 1)
            self.assertIn("leak p1:", errors.getvalue())
            self.assertEqual(list(root.iterdir()), [refs])

    def test_leak_in_repository(self):
        self._assert_leak_rejected(_minimal_ref(
            repository="org/keep-scope-narrow-always", constraints=["keep scope narrow always"]
        ))

    def test_leak_nfd_matches_nfc(self):
        self._assert_leak_rejected(_minimal_ref(
            objective="Keep the cafe\u0301 service running today.",
            constraints=["caf\u00e9 service running today"],
        ))

    def test_normalization_nfkc_and_scalar_flattening(self):
        self.assertEqual(build_inputs._normalize_text("\uff21\uff22\uff23"), "abc")
        self.assertEqual(
            build_inputs._flatten_strings({"nested": [42, True, None, {"value": 3.5}]}),
            ["42", "True", "3.5"],
        )

    def test_leak_eight_word_shingle_without_whole_string(self):
        scored = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        objective = "Plan gamma delta epsilon zeta eta theta iota kappa safely."
        self.assertNotIn(scored, objective)
        self._assert_leak_rejected(_minimal_ref(objective=objective, constraints=[scored]))

    def test_set_a_title_from_markdown_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "ew-one"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            md_path = root / "packets" / "ew-one.md"
            md_path.parent.mkdir()
            md_path.write_text("# Heading From Markdown\n\nBody text.\n", encoding="utf-8")
            (refs / "metadata" / f"{item_id}.meta.json").write_text(
                json.dumps({"source_file": "packets/ew-one.md"}),
                encoding="utf-8",
            )
            code = build_inputs.build(["--set", "a", "--refs", str(refs), "--out", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads((out / f"{item_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "Heading From Markdown")

    def test_set_b_title_from_source_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "bw-one"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            (refs / "metadata" / f"{item_id}.meta.json").write_text(
                json.dumps({"source_title": "Title From Meta"}),
                encoding="utf-8",
            )
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads((out / f"{item_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "Title From Meta")

    def test_output_has_exactly_four_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "four-keys"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            (refs / "metadata" / f"{item_id}.meta.json").write_text(
                json.dumps({"source_title": "Stable Title"}),
                encoding="utf-8",
            )
            self.assertEqual(
                build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)]), 0
            )
            raw = (out / f"{item_id}.json").read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            payload = json.loads(raw)
            self.assertEqual(set(payload.keys()), {"id", "objective", "repository", "title"})
            self.assertEqual(
                raw,
                json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            )

    def test_missing_title_fails_without_output_override_fixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "needs-title"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            (refs / "metadata" / f"{item_id}.meta.json").write_text(json.dumps({}), encoding="utf-8")
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertNotEqual(code, 0)
            self.assertFalse(out.exists())

            titles_path = root / "titles.json"
            titles_path.write_text(json.dumps({item_id: "Override Title"}), encoding="utf-8")
            code = build_inputs.build(
                [
                    "--set",
                    "b",
                    "--refs",
                    str(refs),
                    "--out",
                    str(out),
                    "--titles",
                    str(titles_path),
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads((out / f"{item_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "Override Title")
            run = json.loads((out.parent / f"{out.name}.build.json").read_text(encoding="utf-8"))
            self.assertEqual(run["override_ids"], [item_id])

    def test_leak_eight_word_shingle_and_whole_string(self):
        leaked = "alpha beta gamma delta epsilon zeta eta theta"
        ref = _minimal_ref(
            "leak-item",
            objective=f"Plan work where {leaked} must never appear.",
            non_goals=[leaked],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            (refs / "leak-item.json").write_text(json.dumps(ref), encoding="utf-8")
            (refs / "metadata" / "leak-item.meta.json").write_text(
                json.dumps({"source_title": "Safe Title"}),
                encoding="utf-8",
            )
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

        short_four = "keep scope narrow always"
        ref2 = _minimal_ref(
            "leak-four",
            objective=f"Objective mentions {short_four} in the plan.",
            constraints=[short_four],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            (refs / "leak-four.json").write_text(json.dumps(ref2), encoding="utf-8")
            (refs / "metadata" / "leak-four.meta.json").write_text(
                json.dumps({"source_title": "Another Title"}),
                encoding="utf-8",
            )
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

    def test_allow_leak_records_and_writes(self):
        leaked = "one two three four five six seven eight"
        ref = _minimal_ref(
            "allowed",
            objective=f"Contains {leaked} accidentally.",
            evidence_requirements=[leaked],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            (refs / "allowed.json").write_text(json.dumps(ref), encoding="utf-8")
            (refs / "metadata" / "allowed.meta.json").write_text(
                json.dumps({"source_title": "Allowed Title"}),
                encoding="utf-8",
            )
            code = build_inputs.build(
                [
                    "--set",
                    "b",
                    "--refs",
                    str(refs),
                    "--out",
                    str(out),
                    "--allow-leak",
                    "allowed",
                ]
            )
            self.assertEqual(code, 0)
            run = json.loads((out.parent / f"{out.name}.build.json").read_text(encoding="utf-8"))
            self.assertEqual(run["allow_leak_ids"], ["allowed"])

    def test_exclusion_records_counts_and_preserves_reference_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            for item_id in ("p1", "p2", "p3"):
                (refs / f"{item_id}.json").write_text(
                    json.dumps(_minimal_ref(item_id)), encoding="utf-8"
                )
                (refs / "metadata" / f"{item_id}.meta.json").write_text(
                    json.dumps({"source_title": "Safe Title"}), encoding="utf-8"
                )
            args = ["--set", "b", "--refs", str(refs)]
            self.assertEqual(build_inputs.build(args + ["--out", str(root / "full")]), 0)
            full = json.loads((root / "full.build.json").read_text(encoding="utf-8"))
            self.assertEqual((full["n_references"], full["n_excluded"], full["n_inputs"]), (3, 0, 3))
            self.assertEqual(full["excluded"], [])
            self.assertEqual(build_inputs.build(args + [
                "--out", str(out), "--exclude", "p2=answer = revealed",
                "--exclude", "p3=another substantive answer",
            ]), 0)
            self.assertEqual([p.name for p in out.iterdir()], ["p1.json"])
            run = json.loads((root / "inputs.build.json").read_text(encoding="utf-8"))
            self.assertEqual(run["excluded"], [
                {"id": "p2", "reason": "answer = revealed"},
                {"id": "p3", "reason": "another substantive answer"},
            ])
            self.assertEqual((run["n_references"], run["n_excluded"], run["n_inputs"]), (3, 2, 1))
            self.assertEqual(run["item_count"], run["n_inputs"])
            self.assertEqual(run["input_manifest"], build_inputs.manifest(out))
            self.assertEqual(len(run["input_manifest"].splitlines()), 1)
            self.assertEqual(run["reference_manifest"], full["reference_manifest"])
            self.assertEqual(run["reference_manifest"], build_inputs.manifest(refs))

    def test_excluded_item_skips_leak_and_missing_title_checks(self):
        leaked = "keep scope narrow always"
        for missing_title in (False, True):
            with self.subTest(missing_title=missing_title), tempfile.TemporaryDirectory() as tmp:
                root, refs, out = self._layout(Path(tmp))
                for item_id in ("safe", "leaking"):
                    reference = _minimal_ref(item_id)
                    if item_id == "leaking":
                        reference = _minimal_ref(item_id, objective=leaked, constraints=[leaked])
                    (refs / f"{item_id}.json").write_text(json.dumps(reference), encoding="utf-8")
                    if item_id == "safe" or not missing_title:
                        (refs / "metadata" / f"{item_id}.meta.json").write_text(
                            json.dumps({"source_title": "Safe Title"}), encoding="utf-8"
                        )
                self.assertEqual(build_inputs.build([
                    "--set", "b", "--refs", str(refs), "--out", str(out),
                    "--exclude", "leaking=substantive answer in objective",
                ]), 0)
                self.assertEqual([p.name for p in out.iterdir()], ["safe.json"])

    def test_invalid_exclusions_fail_without_output(self):
        cases = (
            (["unknown=reason"], "unknown --exclude id"),
            (["p1=reason", "p2=reason"], "at least one input"),
            (["p1"], "non-empty reason"),
            (["p1="], "non-empty reason"),
            (["p1=   "], "non-empty reason"),
            (["=reason"], "non-empty reason"),
            (["p1=reason", "p1=different reason"], "duplicate --exclude id"),
        )
        for entries, expected in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp:
                root, refs, out = self._layout(Path(tmp))
                for item_id in ("p1", "p2"):
                    (refs / f"{item_id}.json").write_text(
                        json.dumps(_minimal_ref(item_id)), encoding="utf-8"
                    )
                args = ["--set", "b", "--refs", str(refs), "--out", str(out)]
                for entry in entries:
                    args.extend(["--exclude", entry])
                errors = io.StringIO()
                with redirect_stderr(errors):
                    self.assertEqual(build_inputs.build(args), 1)
                self.assertIn(expected, errors.getvalue())
                self.assertEqual(list(root.iterdir()), [refs])

    def test_refuses_non_empty_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "solo"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            (refs / "metadata" / f"{item_id}.meta.json").write_text(
                json.dumps({"source_title": "T"}),
                encoding="utf-8",
            )
            out.mkdir()
            (out / "stray.txt").write_text("x", encoding="utf-8")
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertNotEqual(code, 0)

    def test_manifests_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            for item_id in ("m1", "m2"):
                (refs / f"{item_id}.json").write_text(
                    json.dumps(_minimal_ref(item_id)), encoding="utf-8"
                )
                (refs / "metadata" / f"{item_id}.meta.json").write_text(
                    json.dumps({"source_title": f"Title {item_id}"}),
                    encoding="utf-8",
                )
            self.assertEqual(
                build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)]), 0
            )
            run1 = json.loads((out.parent / f"{out.name}.build.json").read_text(encoding="utf-8"))
            self.assertEqual(
                run1["input_manifest"],
                build_inputs.manifest(out),
            )
            shutil.rmtree(out)
            (out.parent / f"{out.name}.build.json").unlink(missing_ok=True)
            self.assertEqual(
                build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)]),
                0,
            )
            run2 = json.loads((out.parent / f"{out.name}.build.json").read_text(encoding="utf-8"))
            self.assertEqual(run1["reference_manifest"], run2["reference_manifest"])
            self.assertEqual(run1["input_manifest"], run2["input_manifest"])

    def test_metadata_json_not_treated_as_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, refs, out = self._layout(Path(tmp))
            item_id = "real-ref"
            (refs / f"{item_id}.json").write_text(
                json.dumps(_minimal_ref(item_id)), encoding="utf-8"
            )
            (refs / "metadata" / "ghost.json").write_text(
                json.dumps(_minimal_ref("ghost")), encoding="utf-8"
            )
            (refs / "metadata" / f"{item_id}.meta.json").write_text(
                json.dumps({"source_title": "Only Real Item"}),
                encoding="utf-8",
            )
            code = build_inputs.build(["--set", "b", "--refs", str(refs), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(p.name for p in out.glob("*.json")), [f"{item_id}.json"])
            run = json.loads((out.parent / f"{out.name}.build.json").read_text(encoding="utf-8"))
            self.assertEqual(run["item_count"], 1)


if __name__ == "__main__":
    unittest.main()
# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:16 UTC | END]]
