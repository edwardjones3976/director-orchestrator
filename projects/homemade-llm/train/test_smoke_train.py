# [[Codex | GPT-6 | 2026-09-25 | 09:23 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | BEGIN]]
"""Unit tests for smoke_train.py's non-GPU logic. The ML stack is stubbed, so this runs on any Python 3.10+:
    python projects/homemade-llm/train/test_smoke_train.py
Synthetic files only: no model, no GPU, no Docker, no network.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import os
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

TRAIN, MERGE = "org/base-4bit@" + "a" * 40, "org/base@" + "b" * 40


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_snapshot(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def entry(files: dict[str, bytes]) -> dict:
    return {"files": [{"path": k, "sha256": sha(v), "size": len(v)} for k, v in sorted(files.items())], "smoke_only": True, "source": "test"}


def write_list(path: Path, data: dict) -> str:
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    return sha(raw)


SNAP = {"config.json": b"{}", "model.safetensors": b"w" * 3000, "chat_template.jinja": b"{{ template }}"}


class TrustTests(unittest.TestCase):
    def test_offline_flags_and_forced_hf_home(self):
        # The image sets its own HF_HOME; the module must override it (not setdefault), and never go online.
        self.assertEqual(os.environ["HF_HOME"], "/workspace/hf")
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")

    def test_gguf_export_uses_only_the_images_prebuilt_llama_cpp(self):
        # Set before unsloth is imported; any install/fetch fallback is switched off.
        self.assertEqual(os.environ["UNSLOTH_LLAMA_CPP_PATH"], "/opt/unsloth/llama.cpp")
        self.assertEqual((os.environ["UNSLOTH_LLAMA_CPP_OFFLINE"], os.environ["UNSLOTH_OFFLINE"], os.environ["UNSLOTH_AUTO_INSTALL"]), ("1", "1", "0"))
        src = inspect.getsource(smoke_train)
        self.assertLess(src.index('os.environ["UNSLOTH_LLAMA_CPP_PATH"]'), src.index("from unsloth import FastLanguageModel"))

    def test_sha256_file_streams_and_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "f")
            p.write_bytes(b"x" * (3 * 1024 + 7))
            self.assertEqual(smoke_train.sha256_file(p, chunk=1024), sha(p.read_bytes()))

    def test_digest_list_accepts_exact_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "list.json")
            digest = write_list(p, {TRAIN: entry(SNAP), MERGE: entry(SNAP)})
            data = smoke_train.load_digest_list(p, digest, TRAIN, MERGE)
            self.assertEqual(set(data), {TRAIN, MERGE})

    def test_digest_list_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "list.json")
            good = {TRAIN: entry(SNAP), MERGE: entry(SNAP)}
            digest = write_list(p, good)
            for case, expected in [
                ("wrong expected digest", "does not match its pinned SHA-256"),
                ("missing expected digest", "missing or not 64"),
            ]:
                with self.subTest(case):
                    with self.assertRaisesRegex(smoke_train.SmokeFailure, expected):
                        smoke_train.load_digest_list(p, "0" * 64 if case.startswith("wrong") else "", TRAIN, MERGE)
            p.write_bytes(p.read_bytes() + b" ")   # one changed byte
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "does not match its pinned SHA-256"):
                smoke_train.load_digest_list(p, digest, TRAIN, MERGE)
            for case, data, expected in [
                ("override: other training snapshot", {"org/other@" + "c" * 40: entry(SNAP), MERGE: entry(SNAP)}, "exactly the pinned"),
                ("extra snapshot", {**good, "x/y@" + "d" * 40: entry(SNAP)}, "exactly the pinned"),
                ("entry extra key", {TRAIN: {**entry(SNAP), "x": 1}, MERGE: entry(SNAP)}, "unexpected shape"),
                ("file record bool size", {TRAIN: {**entry(SNAP), "files": [{"path": "a", "sha256": "0" * 64, "size": True}]}, MERGE: entry(SNAP)}, "malformed"),
            ]:
                with self.subTest(case):
                    d = write_list(p, data)
                    with self.assertRaisesRegex(smoke_train.SmokeFailure, expected):
                        smoke_train.load_digest_list(p, d, TRAIN, MERGE)
            p.write_bytes(b'{"%s": {}, "%s": {}, "%s": {}}' % (TRAIN.encode(), MERGE.encode(), TRAIN.encode()))
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "duplicate"):
                smoke_train.load_digest_list(p, sha(p.read_bytes()), TRAIN, MERGE)

    def test_snapshot_dir_requires_full_revision_and_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HF_HOME": tmp}):
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "40-hex"):
                smoke_train.snapshot_dir("org/m", "main")
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "not a complete local snapshot"):
                smoke_train.snapshot_dir("org/m", "e" * 40)
            d = Path(tmp, "hub", "models--org--m", "snapshots", "e" * 40)
            d.mkdir(parents=True)
            self.assertEqual(smoke_train.snapshot_dir("org/m", "e" * 40), d)

    def test_verify_snapshot_accepts_exact_files_with_two_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_snapshot(Path(tmp), SNAP)
            calls = []
            real = smoke_train.sha256_file
            with mock.patch.object(smoke_train, "sha256_file", side_effect=lambda p, *a: calls.append(p) or real(p)):
                out = smoke_train.verify_snapshot(d, entry(SNAP), "t")
            self.assertEqual(out["files"], 3)
            self.assertEqual(len(calls), 6)   # every file hashed twice
            self.assertIn("read1_utc", out)
            self.assertIn("read2_utc", out)

    def test_verify_snapshot_refusals(self):
        cases = {
            "missing file": ({k: v for k, v in SNAP.items() if k != "config.json"}, "file set differs"),
            "extra file": ({**SNAP, "extra.bin": b"x"}, "file set differs"),
            "changed bytes, same size": ({**SNAP, "config.json": b"[]"}, "SHA-256 read 1"),
            "changed size": ({**SNAP, "config.json": b"{ }"}, "size differs"),
        }
        for case, (files, expected) in cases.items():
            with self.subTest(case), tempfile.TemporaryDirectory() as tmp:
                d = make_snapshot(Path(tmp), files)
                with self.assertRaisesRegex(smoke_train.SmokeFailure, expected):
                    smoke_train.verify_snapshot(d, entry(SNAP), "t")

    def test_verify_snapshot_refuses_two_read_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_snapshot(Path(tmp), SNAP)
            seen = {}
            real = smoke_train.sha256_file

            def flaky(p, *a):   # the second read of model.safetensors returns different bytes
                seen[p] = seen.get(p, 0) + 1
                return "f" * 64 if p.name == "model.safetensors" and seen[p] == 2 else real(p)
            with mock.patch.object(smoke_train, "sha256_file", side_effect=flaky):
                with self.assertRaisesRegex(smoke_train.SmokeFailure, "read 2"):
                    smoke_train.verify_snapshot(d, entry(SNAP), "t")

    def test_template_digest_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_snapshot(Path(tmp), SNAP)
            smoke_train.verify_template(d, sha(SNAP["chat_template.jinja"]))
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "template"):
                smoke_train.verify_template(d, "0" * 64)
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "missing or not 64"):
                smoke_train.verify_template(d, "")


class ArtifactTests(unittest.TestCase):
    def test_rows_are_valid_two_key_json(self):
        rows = smoke_train.rows()
        self.assertEqual(len(rows), smoke_train.ROWS)
        for r in rows:
            self.assertEqual([m["role"] for m in r["messages"]], ["system", "user", "assistant"])
            self.assertEqual(sorted(json.loads(r["messages"][2]["content"])), ["objective", "task_id"])

    def test_run_id_has_random_suffix(self):
        self.assertRegex(smoke_train.RUN, r"^smoke-\d{8}-\d{6}-[0-9a-f]{6}$")

    def test_adapter_manifest_is_sorted_complete_and_self_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_snapshot(Path(tmp), {"b.safetensors": b"bb", "a/config.json": b"{}"})
            m = smoke_train.adapter_manifest(d)
            self.assertEqual([f["path"] for f in m["files"]], ["a/config.json", "b.safetensors"])
            self.assertEqual(m["files"][1], {"path": "b.safetensors", "size": 2, "sha256": sha(b"bb")})
            canonical = json.dumps(m["files"], sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(m["manifest_sha256"], sha(canonical))
            Path(d, "b.safetensors").write_bytes(b"bc")
            self.assertNotEqual(smoke_train.adapter_manifest(d)["manifest_sha256"], m["manifest_sha256"])
            empty = Path(tmp, "empty-adapter")
            empty.mkdir()
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "empty"):
                smoke_train.adapter_manifest(empty)

    def test_actual_step_count_must_equal_request(self):
        self.assertEqual(smoke_train.check_steps(20, 20), 20)
        with self.assertRaisesRegex(smoke_train.SmokeFailure, "19 != requested 20"):
            smoke_train.check_steps(19, 20)

    def test_publish_gguf_verified_exclusive_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "m.gguf"), Path(tmp, "out", "r-m.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"g" * 4096)
            rec = smoke_train.publish_gguf(src, dest)
            self.assertEqual(rec, {"path": str(dest), "bytes": 4096, "sha256": sha(b"g" * 4096)})
            self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), ["r-m.gguf"])

    def test_publish_gguf_never_clobbers_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "m.gguf"), Path(tmp, "out", "r-m.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"g" * 4096)
            dest.write_bytes(b"someone else's file")
            with self.assertRaises(FileExistsError):
                smoke_train.publish_gguf(src, dest)
            self.assertEqual(dest.read_bytes(), b"someone else's file")
            self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), ["r-m.gguf"])

    def test_publish_gguf_race_on_the_destination_name_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "m.gguf"), Path(tmp, "out", "r-m.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"g" * 4096)
            real_open = os.open

            def racing_open(path, flags, *a):   # another writer creates the name just before the exclusive reserve
                if Path(path) == dest and not dest.exists():
                    dest.write_bytes(b"racer")
                return real_open(path, flags, *a)
            with mock.patch.object(smoke_train.os, "open", side_effect=racing_open):
                with self.assertRaises(FileExistsError):
                    smoke_train.publish_gguf(src, dest)
            self.assertEqual(dest.read_bytes(), b"racer")

    def test_publish_gguf_refuses_two_read_disagreement_before_any_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "m.gguf"), Path(tmp, "out", "r-m.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"g" * 4096)
            with mock.patch.object(smoke_train, "sha256_file", side_effect=["1" * 64, "2" * 64]):
                with self.assertRaisesRegex(smoke_train.SmokeFailure, "pre-publication"):
                    smoke_train.publish_gguf(src, dest)
            self.assertEqual(list(dest.parent.iterdir()), [])

    def test_publish_gguf_mismatched_copy_removes_only_run_owned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp, "m.gguf"), Path(tmp, "out", "r-m.gguf")
            dest.parent.mkdir()
            src.write_bytes(b"g" * 4096)
            Path(dest.parent, "other.gguf").write_bytes(b"keep me")
            with mock.patch.object(smoke_train.shutil, "copyfile", lambda s, d: Path(d).write_bytes(b"h" * 4096)):
                with self.assertRaisesRegex(smoke_train.SmokeFailure, "does not match the exported bytes"):
                    smoke_train.publish_gguf(src, dest)
            self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), ["other.gguf"])

    def test_publish_all_rolls_back_already_published_ggufs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "export"), Path(tmp, "models")
            src.mkdir()
            Path(src, "a.gguf").write_bytes(b"a" * 100)
            Path(src, "b.gguf").write_bytes(b"b" * 100)
            real = smoke_train.publish_gguf
            calls = []

            def second_fails(s, d):
                calls.append(s.name)
                if len(calls) == 2:
                    raise smoke_train.SmokeFailure("disk full")
                return real(s, d)
            with mock.patch.object(smoke_train, "publish_gguf", side_effect=second_fails):
                with self.assertRaises(smoke_train.SmokeFailure):
                    smoke_train.publish_all(src, out)
            self.assertEqual(list(out.iterdir()), [])
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "no GGUF"):
                smoke_train.publish_all(Path(tmp, "empty-export"), out)

    def test_write_report_create_refuses_existing_report_and_updates_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "reports", "r.json")
            smoke_train.write_report(path, {"status": "trained-not-exported"}, create=True)
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "already exists"):
                smoke_train.write_report(path, {"status": "other"}, create=True)
            with mock.patch.object(smoke_train.os, "replace", side_effect=OSError("rename failed")):
                with self.assertRaises(OSError):
                    smoke_train.write_report(path, {"status": "complete"})
            self.assertEqual(json.loads(path.read_text())["status"], "trained-not-exported")
            smoke_train.write_report(path, {"status": "complete"})
            self.assertEqual(json.loads(path.read_text())["status"], "complete")
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["r.json"])   # no temp file is left behind

    def test_write_report_initial_create_is_exclusive_with_no_check_then_write_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "reports", "r.json")
            real_open = os.open

            def racing_open(p, flags, *a):   # another process creates the report after our call began
                if Path(p) == path:
                    path.write_text('{"run": "other"}')
                return real_open(p, flags, *a)
            with mock.patch.object(smoke_train.os, "open", side_effect=racing_open):
                with self.assertRaisesRegex(smoke_train.SmokeFailure, "already exists"):
                    smoke_train.write_report(path, {"status": "trained-not-exported"}, create=True)
            self.assertEqual(path.read_text(), '{"run": "other"}')   # the other run's evidence is untouched
            path.unlink()
            with mock.patch.object(smoke_train.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    smoke_train.write_report(path, {"status": "trained-not-exported"}, create=True)
            self.assertFalse(path.exists())   # a failed first write removes only its own partial file
            smoke_train.write_report(path, {"status": "trained-not-exported"}, create=True)
            temps = []
            real_replace = os.replace
            with mock.patch.object(smoke_train.os, "replace", side_effect=lambda s, d: temps.append(Path(s).name) or real_replace(s, d)):
                smoke_train.write_report(path, {"status": "complete"})
                smoke_train.write_report(path, {"status": "complete"})
            self.assertEqual(len(set(temps)), 2)   # each update uses its own run-unique temp name
            self.assertTrue(all(t.startswith("r.json.") and t.endswith(".tmp") and t != "r.json.tmp" for t in temps))


class FailureReceiptTests(unittest.TestCase):
    def test_failure_receipt_is_exclusive_bounded_and_masks_foreign_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp, "reports", f"{smoke_train.RUN}.failure.json")
            with mock.patch.object(smoke_train, "FAILURE", receipt):
                exc = smoke_train.SmokeFailure("cannot read /workspace/hf/hub/token and /home/u/.ssh/key; " + "z" * 1000)
                smoke_train.write_failure("verify", exc, {"completed_stages": []})
                data = json.loads(receipt.read_text())
                self.assertEqual((data["status"], data["stage"], data["exception"]), ("failed", "verify", "SmokeFailure"))
                self.assertNotIn("/workspace/hf", data["message"])
                self.assertNotIn(".ssh", data["message"])
                self.assertLessEqual(len(data["message"]), 400)
                self.assertNotIn("sample", json.dumps(data))
                with self.assertRaises(FileExistsError):   # never overwritten
                    smoke_train.write_failure("train", exc, {})

    def test_main_writes_a_receipt_and_exits_nonzero_on_an_early_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp, f"{smoke_train.RUN}.failure.json")
            report = Path(tmp, f"{smoke_train.RUN}.json")
            with mock.patch.object(smoke_train, "FAILURE", receipt), mock.patch.object(smoke_train, "REPORT", report), \
                    mock.patch.object(smoke_train, "DIGESTS", Path(tmp, "missing-list.json")), \
                    mock.patch.object(smoke_train, "DIGESTS_SHA256", "0" * 64), \
                    mock.patch.object(smoke_train.signal, "signal"):
                with self.assertRaises(SystemExit) as cm:
                    smoke_train.main()
            self.assertNotEqual(cm.exception.code, 0)
            self.assertEqual(json.loads(receipt.read_text())["stage"], "verify")
            self.assertFalse(report.exists())   # no report claiming a stage it did not reach

    def test_training_load_is_local_files_only_and_after_verification(self):
        calls = []

        class FakeLoader:
            @staticmethod
            def from_pretrained(name, **kwargs):
                calls.append((name, kwargs))
                raise RuntimeError("fake loader stops here")
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp, f"{smoke_train.RUN}.failure.json")
            snap = Path(tmp, "snap")
            with mock.patch.object(smoke_train, "FAILURE", receipt), mock.patch.object(smoke_train, "REPORT", Path(tmp, "r.json")), \
                    mock.patch.object(smoke_train, "load_digest_list", return_value={smoke_train.BASE + "@" + smoke_train.REVISION: {},
                                                                                    smoke_train.MERGE_SOURCE + "@" + smoke_train.MERGE_REVISION: {}}), \
                    mock.patch.object(smoke_train, "snapshot_dir", return_value=snap), \
                    mock.patch.object(smoke_train, "verify_snapshot", return_value={"files": 1}), \
                    mock.patch.object(smoke_train, "verify_template"), \
                    mock.patch.object(smoke_train, "FastLanguageModel", FakeLoader), \
                    mock.patch.object(smoke_train, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(reset_peak_memory_stats=lambda: None))), \
                    mock.patch.object(smoke_train.signal, "signal"):
                with self.assertRaises(SystemExit):
                    smoke_train.main()
            self.assertEqual(calls, [(str(snap), {"max_seq_length": smoke_train.SEQ, "load_in_4bit": True, "local_files_only": True})])
            self.assertEqual(json.loads(receipt.read_text())["stage"], "load")

    def test_export_reverifies_then_reloads_local_files_only(self):
        order = []

        class FakeModel:
            config = types.SimpleNamespace(_name_or_path="")

            def save_pretrained_gguf(self, out, tok, quantization_method):
                order.append(("gguf", self.config._name_or_path, quantization_method))

        class FakeLoader:
            @staticmethod
            def from_pretrained(name, **kwargs):
                order.append(("load", name, kwargs))
                return FakeModel(), object()
        merge_key = smoke_train.MERGE_SOURCE + "@" + smoke_train.MERGE_REVISION
        llama = tempfile.TemporaryDirectory()
        self.addCleanup(llama.cleanup)
        for rel in ("llama-quantize", "convert_hf_to_gguf.py", "conversion/__init__.py", "conversion/base.py"):
            Path(llama.name, rel).parent.mkdir(parents=True, exist_ok=True)
            Path(llama.name, rel).write_text("x")
        with mock.patch.object(smoke_train, "load_digest_list", return_value={merge_key: {"files": []}}), \
                mock.patch.object(smoke_train, "adapter_manifest", return_value={"manifest_sha256": "m" * 64}), \
                mock.patch.dict(os.environ, {"SMOKE_ADAPTER_MANIFEST_SHA256": "m" * 64, "UNSLOTH_LLAMA_CPP_PATH": llama.name}), \
                mock.patch.object(smoke_train, "snapshot_dir", return_value=Path("/merge-snap")), \
                mock.patch.object(smoke_train, "verify_snapshot", side_effect=lambda *a: order.append(("verify-merge",))), \
                mock.patch.object(smoke_train, "FastLanguageModel", FakeLoader):
            smoke_train.export()
            # The valid path: re-verified immediately before the merge, then loaded locally, then exported.
            self.assertEqual([o[0] for o in order], ["verify-merge", "load", "gguf"])
            self.assertEqual(order[1][2], {"max_seq_length": smoke_train.SEQ, "load_in_4bit": True, "local_files_only": True})
            self.assertEqual(order[2][1:], (str(Path("/merge-snap")), "q4_k_m"))
            # An incomplete prebuilt refuses after the re-verification and before any load or GGUF export.
            order.clear()
            Path(llama.name, "conversion", "base.py").unlink()
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "lacks conversion/base.py"):
                smoke_train.export()
            self.assertEqual(order, [("verify-merge",)])
        with mock.patch.object(smoke_train, "load_digest_list", return_value={merge_key: {"files": []}}), \
                mock.patch.object(smoke_train, "adapter_manifest", return_value={"manifest_sha256": "x" * 64}), \
                mock.patch.dict(os.environ, {"SMOKE_ADAPTER_MANIFEST_SHA256": "m" * 64}):
            with self.assertRaisesRegex(smoke_train.SmokeFailure, "saved adapter bytes differ"):
                smoke_train.export()

    def test_main_publishes_the_gguf_unsloth_writes_beside_the_export_directory(self):
        # The pinned Unsloth writes <save_directory>_gguf/<model>.Q4_K_M.gguf, a SIBLING of save_directory (save.py
        # _gguf_output_directory). Drive main() end to end with a fake export that writes exactly there: publishing from
        # EXPORT_DIR would find nothing and fail with "no GGUF produced".
        self.assertEqual(smoke_train.GGUF_DIR, Path(f"{smoke_train.EXPORT_DIR}_gguf"))
        with tempfile.TemporaryDirectory() as tmp:
            report, models, failure = self._run_fake_main(tmp)
            final = json.loads(report.read_text())
            self.assertEqual(final["status"], "complete")
            self.assertEqual([Path(g["path"]).name for g in final["gguf"]], [f"{smoke_train.RUN}-qwen3-0.6b.Q4_K_M.gguf"])
            self.assertEqual(sorted(p.name for p in models.iterdir()), [f"{smoke_train.RUN}-qwen3-0.6b.Q4_K_M.gguf"])
            self.assertFalse(failure.exists())

    def test_main_post_commit_output_failure_writes_no_failure_receipt(self):
        # After the complete report and GGUF are committed, a broken stdout (e.g. a cancelled log consumer) must not turn
        # the success into a failure: no .failure.json, no exception, the committed evidence untouched.
        printed = []

        def broken_stdout(*args, **kwargs):
            printed.append(args)
            raise BrokenPipeError("consumer went away")
        with tempfile.TemporaryDirectory() as tmp:
            report, models, failure = self._run_fake_main(tmp, print_effect=broken_stdout)
            self.assertEqual(len(printed), 1)   # the post-commit report print was reached, and raised
            self.assertEqual(json.loads(report.read_text())["status"], "complete")
            self.assertEqual(sorted(p.name for p in models.iterdir()), [f"{smoke_train.RUN}-qwen3-0.6b.Q4_K_M.gguf"])
            self.assertFalse(failure.exists())

    def _run_fake_main(self, tmp, print_effect=None):
        """main() end to end with the ML stack faked and an export child that writes the pinned Unsloth layout."""
        class FakeModel:
            def save_pretrained(self, d):
                Path(d).mkdir(parents=True, exist_ok=True)

        class FakeTok:
            def apply_chat_template(self, messages, tokenize, enable_thinking):
                return json.dumps(messages)

            def __call__(self, text):
                return {"input_ids": [0] * 8}

            def save_pretrained(self, d):
                Path(d).mkdir(parents=True, exist_ok=True)

        class FakeLoader:
            @staticmethod
            def from_pretrained(name, **kwargs):
                return FakeModel(), FakeTok()

            @staticmethod
            def get_peft_model(model, **kwargs):
                return model

        class FakeTrainer:
            def __init__(self, **kwargs):
                self.state = types.SimpleNamespace(global_step=smoke_train.STEPS, log_history=[{"loss": 1.0}])

            def train(self):
                return types.SimpleNamespace(training_loss=1.0)
        cuda = types.SimpleNamespace(reset_peak_memory_stats=lambda: None, max_memory_allocated=lambda: 0, max_memory_reserved=lambda: 0,
                                     empty_cache=lambda: None, get_device_name=lambda i: "fake")
        export_dir = Path(tmp, "run", "gguf")

        def fake_export(cmd, check, env):   # the child's effect, at the pinned Unsloth's output location
            Path(f"{export_dir}_gguf").mkdir(parents=True)
            Path(f"{export_dir}_gguf", "qwen3-0.6b.Q4_K_M.gguf").write_bytes(b"g" * 100)
        models, report, failure = Path(tmp, "models"), Path(tmp, "report.json"), Path(tmp, "failure.json")
        stdout = mock.patch.object(smoke_train, "print", side_effect=print_effect, create=True) if print_effect else contextlib.nullcontext()
        with mock.patch.object(smoke_train, "FAILURE", failure), mock.patch.object(smoke_train, "REPORT", report), \
                mock.patch.object(smoke_train, "RUN_DIR", Path(tmp, "run-dir")), mock.patch.object(smoke_train, "MODEL_DIR", models), \
                mock.patch.object(smoke_train, "EXPORT_DIR", export_dir), mock.patch.object(smoke_train, "GGUF_DIR", Path(f"{export_dir}_gguf")), \
                mock.patch.object(smoke_train, "load_digest_list", return_value={smoke_train.BASE + "@" + smoke_train.REVISION: {},
                                                                                smoke_train.MERGE_SOURCE + "@" + smoke_train.MERGE_REVISION: {}}), \
                mock.patch.object(smoke_train, "snapshot_dir", return_value=Path(tmp, "snap")), \
                mock.patch.object(smoke_train, "verify_snapshot", return_value={"files": 1}), \
                mock.patch.object(smoke_train, "verify_template"), \
                mock.patch.object(smoke_train, "adapter_manifest", return_value={"manifest_sha256": "m" * 64}), \
                mock.patch.object(smoke_train, "FastLanguageModel", FakeLoader), \
                mock.patch.object(smoke_train, "Dataset", types.SimpleNamespace(from_dict=lambda d: d)), \
                mock.patch.object(smoke_train, "SFTConfig", lambda **kw: kw), mock.patch.object(smoke_train, "SFTTrainer", FakeTrainer), \
                mock.patch.object(smoke_train, "torch", types.SimpleNamespace(cuda=cuda, __version__="fake")), \
                mock.patch.object(smoke_train.subprocess, "run", side_effect=fake_export), \
                mock.patch.object(smoke_train.signal, "signal"), stdout:
            smoke_train.main()
        return report, models, failure

    def test_unconstrained_sample_generation_removed(self):
        src = inspect.getsource(smoke_train)
        self.assertNotIn("sample_output", src)
        self.assertNotIn(".generate(", src)
        self.assertNotIn("snapshot_download", src)   # nothing is fetched or resolved online


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:36 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Codex | GPT-6 | 2026-09-25 | 09:23 UTC | END]]
