# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | BEGIN]]
"""Unit tests for build_inputs.py (synthetic fixtures only).

    python projects/homemade-llm/eval/test_build_inputs.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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
