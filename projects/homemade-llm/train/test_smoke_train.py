# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | BEGIN]]
"""Unit tests for smoke_train.py's non-GPU logic. The ML stack is stubbed, so this runs on any Python 3.10+:
    python projects/homemade-llm/train/test_smoke_train.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Stub the heavy imports so the module loads without torch, CUDA, or Unsloth.
for name, attrs in {
    "torch": {},
    "unsloth": {"FastLanguageModel": object},
    "datasets": {"Dataset": object},
    "transformers": {"set_seed": lambda *_: None},
    "trl": {"SFTConfig": object, "SFTTrainer": object},
}.items():
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules.setdefault(name, mod)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_train  # noqa: E402


class SmokeTrainTests(unittest.TestCase):
    def test_rows_are_valid_two_key_json(self):
        rows = smoke_train.rows()
        self.assertEqual(len(rows), smoke_train.ROWS)
        for r in rows:
            roles = [m["role"] for m in r["messages"]]
            self.assertEqual(roles, ["system", "user", "assistant"])
            answer = json.loads(r["messages"][2]["content"])
            self.assertEqual(sorted(answer), ["objective", "task_id"])

    def test_run_id_has_random_suffix(self):
        self.assertRegex(smoke_train.RUN, r"^smoke-\d{8}-\d{6}-[0-9a-f]{6}$")

    def test_publish_renames_only_after_full_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "a.gguf"), Path(tmp, "out", "b.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"x" * 4096)
            smoke_train.publish(src, dest)
            self.assertEqual(dest.read_bytes(), src.read_bytes())
            self.assertFalse(dest.with_name("b.gguf.part").exists())

    def test_publish_short_copy_leaves_no_gguf(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "a.gguf"), Path(tmp, "out", "b.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"x" * 4096)
            truncate = lambda s, d: Path(d).write_bytes(b"x" * 10)  # simulate an interrupted copy
            with mock.patch.object(smoke_train.shutil, "copy2", truncate):
                with self.assertRaises(SystemExit):
                    smoke_train.publish(src, dest)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name("b.gguf.part").exists())

    def test_publish_copy_error_leaves_no_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "a.gguf"), Path(tmp, "out", "b.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"x" * 4096)
            def partial_then_fail(s, d):
                Path(d).write_bytes(b"x" * 100)
                raise OSError("No space left on device")
            with mock.patch.object(smoke_train.shutil, "copy2", partial_then_fail):
                with self.assertRaises(OSError):
                    smoke_train.publish(src, dest)
            self.assertEqual(list(dest.parent.iterdir()), [])

    def test_publish_all_removes_ggufs_when_the_final_report_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out, rep = Path(tmp, "export"), Path(tmp, "models"), Path(tmp, "reports", "r.json")
            src.mkdir()
            Path(src, "m.Q4_K_M.gguf").write_bytes(b"g" * 4096)
            with mock.patch.object(smoke_train, "write_report", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    smoke_train.publish_all(src, out, {}, rep)
            self.assertEqual(list(out.iterdir()), [])  # no orphan GGUF without its report
            report = {}
            smoke_train.publish_all(src, out, report, rep)
            self.assertEqual(len(list(out.glob("*.gguf"))), 1)
            self.assertEqual(json.loads(rep.read_text())["gguf"][0]["bytes"], 4096)

    def test_hf_cache_refs_reads_refs_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp, "hub", "models--org--name", "refs")
            repo.mkdir(parents=True)
            (repo / "main").write_text("abc123\n")
            Path(tmp, "hub", "models--org--noref").mkdir()
            with mock.patch.dict(smoke_train.os.environ, {"HF_HOME": tmp}):
                self.assertEqual(smoke_train.hf_cache_refs(), {"org/name": "abc123"})

    def test_resolve_commit_prefers_config_and_enforces_pin(self):
        pin = "a" * 40
        self.assertEqual(smoke_train.resolve_commit(pin, "org/m", pin), pin)
        with self.assertRaises(SystemExit):
            smoke_train.resolve_commit("b" * 40, "org/m", pin)  # loaded something other than the pin

    def test_resolve_commit_falls_back_to_snapshot_and_never_returns_none(self):
        pin = "c" * 40
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = lambda repo, revision, local_files_only: f"/cache/snapshots/{pin}"
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}):
            self.assertEqual(smoke_train.resolve_commit(None, "org/m", pin), pin)
        hub.snapshot_download = lambda repo, revision, local_files_only: "/cache/snapshots/"
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}):
            with self.assertRaises(SystemExit):
                smoke_train.resolve_commit(None, "org/m", pin)

    def test_pinned_ref_pins_only_cached_snapshots_and_restores_the_shared_ref(self):
        pin = "d" * 40
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp, "hub", "models--org--src")
            ref = repo_dir / "refs" / "main"
            with mock.patch.dict(smoke_train.os.environ, {"HF_HOME": tmp}):
                with self.assertRaises(SystemExit):  # snapshot not cached: refuse rather than pin a missing commit
                    with smoke_train.pinned_ref("org/src", pin):
                        pass
                self.assertFalse(ref.exists())
                (repo_dir / "snapshots" / pin).mkdir(parents=True)
                with smoke_train.pinned_ref("org/src", pin):  # no previous ref: removed again afterwards
                    self.assertEqual(ref.read_text(), pin)
                self.assertFalse(ref.exists())
                ref.write_text("e" * 40)  # another tool's ref in the shared cache
                with self.assertRaises(RuntimeError):
                    with smoke_train.pinned_ref("org/src", pin):
                        self.assertEqual(ref.read_text(), pin)
                        raise RuntimeError("export failed")
                self.assertEqual(ref.read_text(), "e" * 40)  # restored even though the export failed
                with smoke_train.pinned_ref("org/src", pin):
                    ref.write_text("f" * 40)  # another cache user updates the ref during the export
                self.assertEqual(ref.read_text(), "f" * 40)  # their newer value is not overwritten

    def test_write_report_failure_keeps_previous_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "reports", "r.json")
            smoke_train.write_report(path, {"stage": "pre-export"})
            def boom(self, *a, **k):
                raise OSError("disk full")
            with mock.patch.object(Path, "write_text", boom):
                with self.assertRaises(OSError):
                    smoke_train.write_report(path, {"stage": "final"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "pre-export"})
            smoke_train.write_report(path, {"stage": "final"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "final"})
            self.assertFalse(path.with_name("r.json.tmp").exists())

    def test_write_report_publishes_only_through_replace(self):
        # If the rename is what fails, the old report must be untouched and the new content only in .tmp.
        # A direct write to the report path (no tmp + os.replace) would overwrite the original and fail here.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "r.json")
            smoke_train.write_report(path, {"stage": "pre-export"})
            with mock.patch.object(smoke_train.os, "replace", side_effect=OSError("rename failed")):
                with self.assertRaises(OSError):
                    smoke_train.write_report(path, {"stage": "final"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "pre-export"})
            self.assertEqual(json.loads(path.with_name("r.json.tmp").read_text()), {"stage": "final"})

    def test_hf_home_is_forced_not_defaulted(self):
        # The image sets its own HF_HOME; the module must override it, not setdefault.
        self.assertEqual(smoke_train.os.environ["HF_HOME"], "/workspace/hf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
