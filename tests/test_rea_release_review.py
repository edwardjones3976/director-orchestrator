"""Unit and regression tests for the opt-in REA release reviewer integration."""

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
LAB_DIR = SCRIPTS_DIR / "rea_release_lab"
ENTRY_POINT = SCRIPTS_DIR / "rea-release-review.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import rea_release_lab.review as review_mod  # noqa: E402


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _create_disposable_inputs(base, *, same=False):
    left = base / "left.asar"
    right = base / "right.asar"
    left_bytes = b"left-asar-payload"
    right_bytes = left_bytes if same else b"right-asar-payload"
    left.write_bytes(left_bytes)
    right.write_bytes(right_bytes)

    left_tree = base / "left_tree"
    right_tree = base / "right_tree"
    left_tree.mkdir()
    right_tree.mkdir()

    (left_tree / "main.js").write_text("const v = 1;\n", encoding="utf-8")
    (left_tree / "unchanged.json").write_text('{"ok":true}\n', encoding="utf-8")

    (right_tree / "main.js").write_text(
        "const v = 1;\n" if same else "const v = 2;\n",
        encoding="utf-8",
    )
    (right_tree / "unchanged.json").write_text('{"ok":true}\n', encoding="utf-8")
    return left, right, left_tree, right_tree


def _fake_subprocess_factory(
    left, right, left_tree, right_tree,
    *, override_left_sha=None, fail_rea_ok=False, include_scope_metadata=False,
):
    calls = []
    left_sha = override_left_sha or _sha256_bytes(left.read_bytes())
    right_sha = _sha256_bytes(right.read_bytes())

    def fake_run(command, stdout=None, stderr=None, timeout=None, **kwargs):
        calls.append(list(command))
        if command[0] == "diffoscope":
            if _sha256_bytes(left.read_bytes()) == _sha256_bytes(right.read_bytes()):
                payload = {}
                rc = 0
            else:
                payload = {
                    "source1": left_tree.resolve().as_posix(),
                    "source2": right_tree.resolve().as_posix(),
                    "details": [{
                        "source1": (left_tree.resolve() / "main.js").as_posix(),
                        "source2": (right_tree.resolve() / "main.js").as_posix(),
                        "unified_diff": "-const v = 1;\n+const v = 2;\n",
                    }],
                }
                rc = 1
            if stdout is not None:
                stdout.write(json.dumps(payload).encode("utf-8"))
            return subprocess.CompletedProcess(command, rc)

        if command[:2] == ["rea", "analyze-javascript-application"]:
            target = Path(command[2]).resolve()
            is_left = target == left.resolve()
            requested = [
                command[idx + 1]
                for idx, token in enumerate(command[:-1])
                if token == "--source-path"
            ]
            data = {
                "evidence_id": "ev-left" if is_left else "ev-right",
                "normalized_result": {
                    "root_artifact_sha256": left_sha if is_left else right_sha,
                    "graph": {
                        "nodes": [{
                            "node_id": "node-1" if is_left else "node-2",
                            "kind": "module",
                            "observations": [{
                                "label": "main entry",
                                "properties": {"module_path": "main.js"},
                                "evidence": [{"source": "static"}],
                            }],
                        }],
                    },
                },
            }
            if include_scope_metadata:
                data["source_selection"] = {
                    "requested_paths": requested,
                    "unselected_files": 85 if requested else 0,
                }
                data["graph_coverage"] = {
                    "status": "complete",
                    "truncated": False,
                    "omitted_count": None,
                    "limits": [],
                }
                data["statistics"] = {
                    "relevant_files": 87,
                    "parsed_javascript_files": len(requested) if requested else 87,
                }
                data["limits"] = [
                    "Caller-selected source analysis leaves 85 inventoried files outside analysis scope."
                    if requested
                    else "Static imports do not prove runtime execution."
                ]
            envelope = {"ok": not fail_rea_ok, "data": data}
            if stdout is not None:
                stdout.write(json.dumps(envelope).encode("utf-8"))
            return subprocess.CompletedProcess(command, 0)

        if command[:2] == ["rea", "compare-application-versions"]:
            envelope = {"ok": True, "data": {"compared": True}}
            if stdout is not None:
                stdout.write(json.dumps(envelope).encode("utf-8"))
            return subprocess.CompletedProcess(command, 0)

        raise AssertionError(f"Unexpected external command: {command}")

    return calls, fake_run


class ReaReleaseReviewTest(unittest.TestCase):
    def test_entrypoint_cli_diffoscope_only_via_runpy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            output = base / "out"
            calls, fake_run = _fake_subprocess_factory(left, right, left_tree, right_tree)
            stdout_buf = io.StringIO()

            argv = [
                str(ENTRY_POINT),
                str(left), str(right), str(left_tree), str(right_tree), str(output),
            ]
            with patch.object(sys, "argv", argv):
                with patch.object(review_mod.subprocess, "run", side_effect=fake_run):
                    with redirect_stdout(stdout_buf):
                        with self.assertRaises(SystemExit) as ctx:
                            runpy.run_path(str(ENTRY_POINT), run_name="__main__")

            self.assertEqual(ctx.exception.code, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "diffoscope")

            summary = json.loads(stdout_buf.getvalue())
            self.assertEqual(summary["pipeline"], "diffoscope")
            self.assertEqual(summary["changes"], ["main.js"])

            report = json.loads((output / "review.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "rea-release-review/v1")
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["incremental_model_cost_usd"], 0)
            self.assertIn(
                "Caller supplies independently verified extracted trees; this function does not authenticate arbitrary trees against ASAR contents.",
                report["limitations"],
            )
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "diffoscope.receipt.json").is_file())

    def test_shared_main_with_rea_evidence_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            output = base / "out-rea"
            calls, fake_run = _fake_subprocess_factory(left, right, left_tree, right_tree)
            stdout_buf = io.StringIO()

            with patch.object(review_mod.subprocess, "run", side_effect=fake_run):
                with redirect_stdout(stdout_buf):
                    rc = review_mod.main([
                        str(left), str(right), str(left_tree), str(right_tree), str(output), "--rea",
                    ])

            self.assertEqual(rc, 0)
            self.assertEqual(
                [cmd[:2] if cmd[0] == "rea" else [cmd[0]] for cmd in calls],
                [
                    ["diffoscope"],
                    ["rea", "analyze-javascript-application"],
                    ["rea", "analyze-javascript-application"],
                    ["rea", "compare-application-versions"],
                ],
            )
            report = json.loads((output / "review.json").read_text(encoding="utf-8"))
            self.assertEqual(report["pipeline"], "diffoscope+rea")
            self.assertFalse(report["same_artifact"])
            self.assertEqual(report["changed_paths"], ["main.js"])
            self.assertEqual(len(report["findings"]), 1)
            finding = report["findings"][0]
            self.assertEqual(finding["path"], "main.js")
            self.assertEqual(finding["before_evidence_id"], "ev-left")
            self.assertEqual(finding["after_evidence_id"], "ev-right")
            self.assertEqual(finding["before_observations"][0]["node_id"], "node-1")
            self.assertEqual(finding["after_observations"][0]["node_id"], "node-2")

    def test_unchanged_control_skips_rea_subprocesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base, same=True)
            output = base / "out-control"
            calls, fake_run = _fake_subprocess_factory(left, right, left_tree, right_tree)
            stdout_buf = io.StringIO()

            with patch.object(review_mod.subprocess, "run", side_effect=fake_run):
                with redirect_stdout(stdout_buf):
                    rc = review_mod.main([
                        str(left), str(right), str(left_tree), str(right_tree), str(output), "--rea",
                    ])

            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "diffoscope")
            report = json.loads((output / "review.json").read_text(encoding="utf-8"))
            self.assertTrue(report["same_artifact"])
            self.assertEqual(report["changed_paths"], [])
            self.assertEqual(report["findings"], [])

    def test_rea_digest_mismatch_and_ok_false_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            _, fake_mismatch = _fake_subprocess_factory(
                left, right, left_tree, right_tree, override_left_sha="0" * 64,
            )
            with patch.object(review_mod.subprocess, "run", side_effect=fake_mismatch):
                with self.assertRaises(ValueError):
                    review_mod.review(left, right, left_tree, right_tree, base / "out-mismatch", enhanced=True)

            _, fake_not_ok = _fake_subprocess_factory(
                left, right, left_tree, right_tree, fail_rea_ok=True,
            )
            with patch.object(review_mod.subprocess, "run", side_effect=fake_not_ok):
                with self.assertRaises(RuntimeError):
                    review_mod.review(left, right, left_tree, right_tree, base / "out-not-ok", enhanced=True)

    def test_refuses_missing_inputs_before_creating_output_or_running_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            cases = [
                (base / "missing-left.asar", right, left_tree, right_tree),
                (left, base / "missing-right.asar", left_tree, right_tree),
                (left, right, base / "missing-left-tree", right_tree),
                (left, right, left_tree, base / "missing-right-tree"),
            ]
            for idx, (l_art, r_art, l_dir, r_dir) in enumerate(cases):
                output = base / f"out-{idx}"
                with patch.object(review_mod.subprocess, "run") as mocked_run:
                    with self.assertRaises(ValueError):
                        review_mod.review(l_art, r_art, l_dir, r_dir, output, enhanced=True)
                mocked_run.assert_not_called()
                self.assertFalse(output.exists())

    def test_refuses_preexisting_output_and_output_inside_input_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            existing_out = base / "existing"
            existing_out.mkdir()
            (existing_out / "keep.txt").write_text("keep", encoding="utf-8")

            bad_outputs = [
                existing_out,
                left_tree,
                left_tree / "nested-out",
                right_tree / "nested-out",
            ]
            for output in bad_outputs:
                with patch.object(review_mod.subprocess, "run") as mocked_run:
                    with self.assertRaises(ValueError):
                        review_mod.review(left, right, left_tree, right_tree, output, enhanced=True)
                mocked_run.assert_not_called()

            self.assertFalse((left_tree / "nested-out").exists())
            self.assertFalse((right_tree / "nested-out").exists())
            self.assertEqual((existing_out / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_refuses_junctions_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            output = base / "out-junction"
            target_member = left_tree / "main.js"
            with patch.object(
                review_mod.os.path,
                "isjunction",
                create=True,
                side_effect=lambda p: Path(p) == target_member,
            ):
                with patch.object(review_mod.subprocess, "run") as mocked_run:
                    with self.assertRaises(ValueError):
                        review_mod.review(left, right, left_tree, right_tree, output)
                mocked_run.assert_not_called()
            self.assertFalse(output.exists())

    def test_refuses_root_and_member_symlinks_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            secret = base / "outside.txt"
            secret.write_text("outside", encoding="utf-8")

            symlink_artifact = base / "link.asar"
            try:
                os.symlink(left, symlink_artifact)
            except OSError as exc:
                self.skipTest(f"Symbolic links require elevated privilege on this host: {exc}")

            with patch.object(review_mod.subprocess, "run") as mocked_run:
                with self.assertRaises(ValueError):
                    review_mod.review(symlink_artifact, right, left_tree, right_tree, base / "out-sym-art")
            mocked_run.assert_not_called()
            self.assertFalse((base / "out-sym-art").exists())

            member_link = left_tree / "leak.txt"
            os.symlink(secret, member_link)
            with patch.object(review_mod.subprocess, "run") as mocked_run:
                with self.assertRaises(ValueError):
                    review_mod.review(left, right, left_tree, right_tree, base / "out-sym-member")
            mocked_run.assert_not_called()
            self.assertFalse((base / "out-sym-member").exists())

    def test_lab_regressions_in_isolated_subprocess(self):
        had_mcp_before = "mcp" in sys.modules
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(LAB_DIR), "-p", "test_*.py"],
            cwd=str(LAB_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Lab unittest discovery failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        if not had_mcp_before:
            self.assertNotIn("mcp", sys.modules)

    def test_repeatable_source_path_passthrough_and_partial_scope_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            output = base / "out-scoped"
            calls, fake_run = _fake_subprocess_factory(
                left, right, left_tree, right_tree, include_scope_metadata=True,
            )
            stdout_buf = io.StringIO()
            selected = [".webpack/main/index.js", ".webpack/renderer/main_window/preload.js"]

            with patch.object(review_mod.subprocess, "run", side_effect=fake_run):
                with redirect_stdout(stdout_buf):
                    rc = review_mod.main([
                        str(left), str(right), str(left_tree), str(right_tree), str(output),
                        "--rea",
                        "--source-path", selected[0],
                        "--source-path", selected[1],
                    ])

            self.assertEqual(rc, 0)
            rea_calls = [c for c in calls if c[:2] == ["rea", "analyze-javascript-application"]]
            self.assertEqual(len(rea_calls), 2)
            for cmd in rea_calls:
                self.assertIn("--source-path", cmd)
                forwarded = [
                    cmd[idx + 1]
                    for idx, tok in enumerate(cmd[:-1])
                    if tok == "--source-path"
                ]
                self.assertEqual(forwarded, selected)

            summary = json.loads(stdout_buf.getvalue())
            self.assertEqual(summary["requested_source_paths"], selected)
            self.assertEqual(summary["semantic_relationship_coverage"], "partial")

            report = json.loads((output / "review.json").read_text(encoding="utf-8"))
            self.assertEqual(report["left_sha256"], _sha256_bytes(left.read_bytes()))
            self.assertEqual(report["right_sha256"], _sha256_bytes(right.read_bytes()))
            self.assertEqual(report["requested_source_paths"], selected)
            scope = report["analysis_scope"]
            self.assertEqual(scope["mode"], "selected_sources")
            self.assertEqual(scope["file_change_coverage"], "all_files")
            self.assertEqual(scope["semantic_relationship_coverage"], "partial")
            for side_name in ("left", "right"):
                side = scope["sides"][side_name]
                self.assertEqual(side["analysis_file"], f"{side_name}-analysis.json")
                self.assertEqual(side["source_selection"]["requested_paths"], selected)
                self.assertEqual(side["source_selection"]["unselected_files"], 85)
                self.assertEqual(side["graph_coverage"]["status"], "complete")
                self.assertNotIn("graph", side)
            self.assertIn("graph", json.loads((output / "left-analysis.json").read_text(encoding="utf-8"))["data"]["normalized_result"])
            index_html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("left-analysis.json", index_html)
            self.assertIn("right-analysis.json", index_html)
            self.assertIn("partial", index_html)

    def test_missing_optional_metadata_uses_no_fake_values_and_control_skips_rea(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            output = base / "out-minimal-meta"
            _, fake_run = _fake_subprocess_factory(
                left, right, left_tree, right_tree, include_scope_metadata=False,
            )
            with patch.object(review_mod.subprocess, "run", side_effect=fake_run):
                report = review_mod.review(
                    left, right, left_tree, right_tree, output, enhanced=True,
                )
            scope = report["analysis_scope"]
            self.assertEqual(scope["mode"], "full_artifact")
            self.assertEqual(scope["semantic_relationship_coverage"], "unknown")
            for side_name in ("left", "right"):
                side = scope["sides"][side_name]
                self.assertIsNone(side["source_selection"])
                self.assertIsNone(side["graph_coverage"])
                self.assertIsNone(side["statistics"])
                self.assertIsNone(side["limits"])

        with tempfile.TemporaryDirectory() as tmp_same:
            base_same = Path(tmp_same)
            l_art, r_art, l_dir, r_dir = _create_disposable_inputs(base_same, same=True)
            out_same = base_same / "out-same"
            calls, fake_same = _fake_subprocess_factory(l_art, r_art, l_dir, r_dir)
            with patch.object(review_mod.subprocess, "run", side_effect=fake_same):
                same_report = review_mod.review(
                    l_art, r_art, l_dir, r_dir, out_same,
                    enhanced=True, source_paths=["main.js"],
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(same_report["analysis_scope"]["mode"], "skipped_identical_artifact")
            self.assertEqual(same_report["analysis_scope"]["semantic_relationship_coverage"], "none")
            self.assertEqual(same_report["analysis_scope"]["sides"], {})
            self.assertEqual(same_report["requested_source_paths"], ["main.js"])

    def test_rejects_meaningless_or_unsafe_source_paths_before_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            cases = [
                (False, ["main.js"]),
                (True, ["../outside.js"]),
                (True, ["/etc/passwd"]),
                (True, ["C:\\Windows\\system.js"]),
                (True, ["./main.js"]),
                (True, [""]),
            ]
            for idx, (enhanced, bad_paths) in enumerate(cases):
                output = base / f"bad-out-{idx}"
                with patch.object(review_mod.subprocess, "run") as mocked_run:
                    with self.assertRaises(ValueError):
                        review_mod.review(
                            left, right, left_tree, right_tree, output,
                            enhanced=enhanced, source_paths=bad_paths,
                        )
                mocked_run.assert_not_called()
                self.assertFalse(output.exists())

    def test_diffoscope_file_list_addition_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            left, right, left_tree, right_tree = _create_disposable_inputs(base)
            (left_tree / ".webpack" / "assets").mkdir(parents=True)
            (left_tree / ".webpack" / "assets" / "keep.js").write_text("k", encoding="utf-8")
            (right_tree / ".webpack" / "assets").mkdir(parents=True)
            (right_tree / ".webpack" / "assets" / "keep.js").write_text("k", encoding="utf-8")
            (right_tree / ".webpack" / "sfw").mkdir(parents=True)
            (right_tree / ".webpack" / "sfw" / "a.js").write_text("a", encoding="utf-8")
            (right_tree / ".webpack" / "sfw" / "b.js").write_text("b", encoding="utf-8")

            payload = {
                "source1": left_tree.resolve().as_posix(),
                "source2": right_tree.resolve().as_posix(),
                "details": [{
                    "source1": (left_tree.resolve() / ".webpack").as_posix(),
                    "source2": (right_tree.resolve() / ".webpack").as_posix(),
                    "details": [{
                        "source1": "file list",
                        "source2": "file list",
                        "unified_diff": "@@ -1,2 +1,3 @@\n assets\n+sfw\n",
                    }],
                }],
            }

            def fake_diffoscope(command, stdout=None, stderr=None, timeout=None, **kwargs):
                if stdout is not None:
                    stdout.write(json.dumps(payload).encode("utf-8"))
                return subprocess.CompletedProcess(command, 1)

            output = base / "out-filelist"
            with patch.object(review_mod.subprocess, "run", side_effect=fake_diffoscope):
                report = review_mod.review(left, right, left_tree, right_tree, output)
            self.assertEqual(report["changed_paths"], [".webpack/sfw/a.js", ".webpack/sfw/b.js"])


if __name__ == "__main__":
    unittest.main()
