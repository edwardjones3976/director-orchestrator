# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
"""Unit tests for the M2-T1 trainer's non-GPU contract.

    python projects/homemade-llm/train/test_train_adapter.py
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import io
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

for name, attrs in {
    "torch": {},
    "unsloth": {"FastLanguageModel": object},
    "datasets": {"Dataset": object},
    "transformers": {"set_seed": lambda *_a, **_k: None},
    "huggingface_hub": {"snapshot_download": lambda *_a, **_k: ""},
    "trl": {"SFTConfig": object, "SFTTrainer": object},
}.items():
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules.setdefault(name, module)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_adapter  # noqa: E402


def row(split: str = "train") -> dict:
    return {
        "split": split,
        "heldout_check": {
            "set_a_manifest": "a" * 64,
            "set_b_manifest": "b" * 64,
            "source_lineage": "clear",
            "near_duplicate": {
                "status": "clear", "max_overlap": 0.0, "threshold": 0.5, "quarantine_band": 0.2,
            },
            "title_objective_match": False,
            "overlap": "none",
        },
        "input": {
            "id": "row-1",
            "objective": "Add a bounded retry.",
            "repository": "example/repo",
            "title": "Retry the client",
            "context": [{
                "kind": "assignment_packet",
                "text": "Retry three times.",
                "source_ref": "packet/row-1",
                "source_sha256": "ab" * 32,
            }],
        },
        "target": {
            "acceptance_checks": ["the client stops at three attempts"],
            "constraints": ["no unbounded loop"],
            "evidence_requirements": ["the test names the cap"],
            "non_goals": ["no new provider"],
        },
    }


def write_accepted(directory: Path, rows: list[dict]) -> Path:
    path = directory / "accepted.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    counts = {"train": sum(item["split"] == "train" for item in rows), "dev": sum(item["split"] == "dev" for item in rows)}
    (directory / "report.json").write_text(json.dumps({
        "n_accepted": len(rows), "rows_per_split": counts,
    }), encoding="utf-8")
    return path


class TrainAdapterTests(unittest.TestCase):
    def test_response_mask_ignores_the_prompt_and_covers_the_answer(self):
        labels = train_adapter.labels_for_token_ids([4, 5, 6, 7], [4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(labels, [-100, -100, -100, -100, 8, 9, 10])
        self.assertAlmostEqual(train_adapter.require_response_only(labels, 4), 3 / 7)

    def test_empty_response_and_prompt_labels_fail_closed(self):
        with self.assertRaises(SystemExit):
            train_adapter.labels_for_token_ids([4, 5], [4, 5])
        with self.assertRaises(SystemExit):
            train_adapter.labels_for_token_ids([4, 5], [9, 5, 6])
        with self.assertRaises(SystemExit):
            train_adapter.require_response_only([-100, 1, 1], 2)

    def test_encode_rows_labels_are_the_response_ids(self):
        def tokenize(messages, add_generation_prompt):
            if messages and messages[-1]["role"] == "assistant" and not add_generation_prompt:
                return tokenize(messages[:-1], True) + [60, 61, 62]
            if add_generation_prompt:
                return tokenize(messages, False) + [50, 51]
            return [10 + len(messages)]

        encoded = train_adapter.encode_rows([row()], tokenize)
        self.assertEqual(encoded[0]["labels"][-3:], [60, 61, 62])
        self.assertTrue(all(label == -100 for label in encoded[0]["labels"][:-3]))
        self.assertEqual(len(encoded[0]["attention_mask"]), len(encoded[0]["input_ids"]))
        self.assertEqual(encoded[0]["input_ids"][-3:], [60, 61, 62])

    def test_only_the_validator_accepted_file_reaches_the_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "rows.jsonl"
            raw.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                train_adapter.load_training_rows(raw)
            quarantined = row("quarantine")
            write_accepted(root, [quarantined])
            with self.assertRaises(SystemExit):
                train_adapter.load_training_rows(root / "accepted.jsonl")
            dev = row("dev")
            dev["input"] = dict(dev["input"], id="dev-1")
            accepted = write_accepted(root, [row(), dev])
            loaded = train_adapter.load_training_rows(accepted)
            self.assertEqual([item["input"]["id"] for item in loaded], ["row-1"])

    def test_a_train_row_renders_through_the_shared_template(self):
        messages = train_adapter.messages_for(row())
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant"])
        self.assertIn("context", messages[1]["content"])
        self.assertIn("acceptance_checks", messages[2]["content"])

    def test_a_validator_row_without_an_input_id_still_renders(self):
        item = row()
        del item["input"]["id"]
        item["input"]["project"] = "director"
        item["row_id"] = "cafebabe" * 2
        messages = train_adapter.messages_for(item)
        self.assertNotIn(item["row_id"], messages[1]["content"])
        self.assertNotIn("director", messages[1]["content"])
        self.assertIn("Add a bounded retry.", messages[1]["content"])

    def test_real_validator_accepted_file_trains_only_the_train_row(self):
        repo = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hl = root / "hl"
            for rel in (
                "eval/build_inputs.py",
                "eval/test_build_inputs.py",
                "train/prompt_template.py",
                "schemas/director-data-v1-row.schema.json",
            ):
                dest = hl / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / "projects" / "homemade-llm" / rel, dest)
            for name in ("validate_rows.py", "test_validate_rows.py"):
                blob = subprocess.check_output(
                    ["git", "show", f"origin/pr-349:projects/homemade-llm/eval/{name}"],
                    cwd=repo,
                )
                (hl / "eval" / name).write_bytes(blob)
            out = root / "validated"
            script = root / "run_validator.py"
            script.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                "root = Path(sys.argv[1])\n"
                "sys.path.insert(0, str(root / 'hl' / 'eval'))\n"
                "import test_validate_rows as fixture\n"
                "import validate_rows\n"
                "set_a, set_b = fixture.write_sets(root / 'sets')\n"
                "train = fixture.make_row(set_a, set_b)\n"
                "dev = fixture.make_row(set_a, set_b, source_group='seed-002', split='dev',\n"
                "    input={'objective': 'Add a retry budget to the n8n intake webhook.'},\n"
                "    target={'non_goals': ['no change to the Slack copy']})\n"
                "(root / 'rows.jsonl').write_text(''.join(json.dumps(item) + '\\n' for item in (train, dev)), encoding='utf-8')\n"
                "code = validate_rows.validate(['--rows', str(root / 'rows.jsonl'), '--set-a', str(set_a),\n"
                "    '--set-b', str(set_b), '--out', str(root / 'validated')])\n"
                "raise SystemExit(code)\n",
                encoding="utf-8",
            )
            completed = subprocess.run([sys.executable, str(script), str(root)], cwd=repo, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            loaded = train_adapter.load_training_rows(out / "accepted.jsonl")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["split"], "train")
            self.assertNotIn("id", loaded[0]["input"])
            messages = train_adapter.messages_for(loaded[0])
            self.assertIn("Webhook secret rotation", messages[1]["content"])
            self.assertNotIn("n8n intake webhook", messages[1]["content"])
            self.assertIn("acceptance_checks", messages[2]["content"])

    def test_the_trainer_keeps_the_attention_mask_column(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertIs(args["remove_unused_columns"], False)
        self.assertEqual(args["dataset_kwargs"], {"skip_prepare_dataset": True})
        self.assertIs(args["packing"], False)

    def test_sft_arguments_matches_probe_gradient_accumulation(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertEqual(args["gradient_accumulation_steps"], train_adapter.PROBE_GRAD_ACCUM)
        self.assertEqual(train_adapter.PROBE_GRAD_ACCUM, 8)
        self.assertEqual(train_adapter.TRAIN_GRAD_ACCUM, 8)

    def test_pinned_revision_must_match_the_loaded_commit(self):
        pin = "a" * 40
        self.assertEqual(train_adapter.resolve_commit(pin, "org/model", pin), pin)
        with self.assertRaises(SystemExit):
            train_adapter.resolve_commit("b" * 40, "org/model", pin)

    def test_winner_probe_stops_above_the_card_and_the_eleven_gib_ceiling(self):
        train_adapter.require_vram(10.9, winner=True)
        with self.assertRaises(SystemExit):
            train_adapter.require_vram(11.1, winner=True)
        with self.assertRaises(SystemExit):
            train_adapter.require_vram(12.1, winner=False)

    def test_report_records_the_probe_fields_and_keeps_the_previous_file(self):
        report = train_adapter.build_report(
            run="probe", base=train_adapter.WINNER_BASE, revision=train_adapter.WINNER_REVISION,
            rows=2, steps=20, mask_coverage=0.25, peak_reserved_gib=9.5, loss=1.2,
            wall_seconds=3, winner=True, resolved_commit=train_adapter.SMOKE_REVISION,
        )
        self.assertEqual(report["gguf_sha256"], None)
        self.assertEqual(report["thinking"], "disabled")
        self.assertEqual(report["base_resolved_commit"], train_adapter.SMOKE_REVISION)
        self.assertEqual(report["peak_reserved_vram_gib"], 9.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "probe.json")
            train_adapter.write_report(path, {"stage": "first"})
            with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    train_adapter.write_report(path, {"stage": "second"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "first"})

    def test_check_only_validates_rows_without_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            train_adapter.main(["--rows", str(path), "--report", str(Path(tmp, "missing.json")), "--check-only"])
            self.assertFalse(Path(tmp, "missing.json").exists())

    def test_winner_flag_rejects_a_different_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path), "--report", str(Path(tmp, "out.json")),
                    "--winner", "--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION,
                    "--check-only",
                ])

    def test_wrapper_carries_the_same_winner_pin(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn(train_adapter.WINNER_REVISION, text)
        self.assertIn(train_adapter.WINNER_BASE, text)
        self.assertIn("Global\\homemade-llm-gpu", text)
        self.assertIn("--probe", text)
        self.assertNotIn("-Probe requires -Winner", text)
        self.assertIn("--expected-digests", text)
        self.assertIn("ExpectedDigests", text)
        self.assertIn("m1-expected-qwen3-14b.json", text)
        self.assertIn("HF_HUB_OFFLINE", text)

    def test_length_statistics_on_a_known_list(self):
        lengths = [10, 20, 30, 40, 50]
        stats = train_adapter.length_statistics(lengths)
        self.assertEqual(stats["max"], 50)
        self.assertAlmostEqual(stats["p50"], train_adapter.percentile(lengths, 50.0))
        self.assertAlmostEqual(stats["p95"], train_adapter.percentile(lengths, 95.0))

    def test_probe_timed_window_bookkeeping(self):
        self.assertEqual(
            train_adapter.timed_window_optimizer_updates(
                train_adapter.PROBE_TIMED_MICROSTEPS,
                train_adapter.PROBE_GRAD_ACCUM,
            ),
            5,
        )
        self.assertEqual(train_adapter.PROBE_WARMUP_MICROSTEPS, 10)
        self.assertEqual(train_adapter.PROBE_TIMED_MICROSTEPS, 40)

    def test_probe_extrapolation_and_contingency(self):
        self.assertAlmostEqual(train_adapter.extrapolate_wall_seconds(96, 2.0), 576.0)
        self.assertAlmostEqual(train_adapter.wall_seconds_with_contingency(100.0), 120.0)

    def test_probe_pass_booleans_at_boundaries(self):
        self.assertTrue(train_adapter.probe_memory_pass(10.999))
        self.assertFalse(train_adapter.probe_memory_pass(11.0))
        self.assertTrue(train_adapter.probe_time_pass(35999.9))
        self.assertFalse(train_adapter.probe_time_pass(36000.0))

    def test_help_lists_probe_flags(self):
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            with self.assertRaises(SystemExit) as raised:
                train_adapter.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = buffer.getvalue()
        self.assertIn("--probe", help_text)
        self.assertIn("--probe-rows-total", help_text)
        self.assertIn("--probe-seq-len", help_text)
        self.assertIn("--expected-digests", help_text)

    def _digest_entry(self, root: Path, name: str, data: bytes) -> dict:
        path = root / name
        path.write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def test_verify_expected_digests_passes_with_two_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [
                self._digest_entry(root, "a.bin", b"alpha"),
                self._digest_entry(root, "b.bin", b"beta"),
            ]
            result = train_adapter.verify_expected_digests(root, entries, reads=2)
            self.assertEqual(
                result,
                {
                    "files": 2,
                    "reads": 2,
                    "bytes": len(b"alpha") + len(b"beta"),
                    "weight_files": 2,
                    "weight_map_checked": False,
                },
            )

    def test_verify_expected_digests_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(root, [{"path": "nope.bin", "sha256": "a" * 64, "size": 1}])

    def test_verify_expected_digests_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.bin").write_bytes(b"xy")
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(
                    root, [{"path": "x.bin", "sha256": hashlib.sha256(b"xy").hexdigest(), "size": 99}],
                )

    def test_verify_expected_digests_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.bin").write_bytes(b"xy")
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(
                    root, [{"path": "x.bin", "sha256": "b" * 64, "size": 2}],
                )

    def test_verify_expected_digests_index_shard_missing_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = "model-00001-of-00002.safetensors"
            shard_b = "model-00002-of-00002.safetensors"
            (root / shard_a).write_bytes(b"only-a")
            (root / "model.safetensors.index.json").write_text(
                json.dumps({"weight_map": {"layer.0": shard_a, "layer.1": shard_b}}),
                encoding="utf-8",
            )
            entry = self._digest_entry(root, shard_a, b"only-a")
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry])
            self.assertIn(shard_b, str(raised.exception))

    def test_verify_expected_digests_uncovered_weight_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "extra.safetensors").write_bytes(b"shard")
            entry = self._digest_entry(root, "listed.bin", b"listed")
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry])
            self.assertIn("weight file not covered", str(raised.exception))

    def test_verify_expected_digests_duplicate_manifest_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._digest_entry(root, "w.bin", b"w")
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry, dict(entry)])
            self.assertIn("duplicate path", str(raised.exception))

    def test_verify_expected_digests_requires_manifest_weight_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("{}", encoding="utf-8")
            entry = {
                "path": "config.json",
                "sha256": hashlib.sha256(b"{}").hexdigest(),
                "size": 2,
            }
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry])
            self.assertIn("no weight file", str(raised.exception))

    def test_verify_expected_digests_second_read_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"stable"
            entry = self._digest_entry(root, "stable.bin", data)
            calls = 0
            real = train_adapter._sha256_file

            def flaky(path: Path) -> str:
                nonlocal calls
                calls += 1
                if calls == 2:
                    return "c" * 64
                return real(path)

            with mock.patch.object(train_adapter, "_sha256_file", side_effect=flaky):
                with self.assertRaises(SystemExit) as raised:
                    train_adapter.verify_expected_digests(root, [entry], reads=2)
                self.assertIn("read 2", str(raised.exception))

    def test_load_expected_digests_returns_matching_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            files = [{"path": "config.json", "sha256": "a" * 64, "size": 3}]
            path.write_text(json.dumps({key: {"files": files}}), encoding="utf-8")
            loaded = train_adapter.load_expected_digests(path, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
            self.assertEqual(loaded, files)

    def test_load_expected_digests_absent_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digests.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                train_adapter.load_expected_digests(path, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION)

    def test_main_probe_smoke_pin_calls_run_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with mock.patch.object(train_adapter, "run_probe", return_value={"mode": "d_m1_f_probe"}) as mocked:
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.SMOKE_BASE,
                    "--revision", train_adapter.SMOKE_REVISION,
                ])
            mocked.assert_called_once()

    def test_main_probe_arbitrary_base_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", "org/other-model",
                    "--revision", "a" * 40,
                ])

    def test_main_winner_without_expected_digests_refuses_before_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock()
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with self.assertRaises(SystemExit):
                    train_adapter.main([
                        "--rows", str(path),
                        "--report", str(Path(tmp, "out.json")),
                        "--winner",
                        "--base", train_adapter.WINNER_BASE,
                        "--revision", train_adapter.WINNER_REVISION,
                    ])
            fake_flm.from_pretrained.assert_not_called()

    def test_probe_pins_accepted_by_guard(self):
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION))
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION))
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION))
        self.assertFalse(train_adapter.is_allowed_probe_pin("org/other-model", "a" * 40))

    def test_length_gate_pass_boundary(self):
        self.assertTrue(train_adapter.length_gate_pass(2048.0))
        self.assertFalse(train_adapter.length_gate_pass(2048.001))

    def test_fallback_pin_without_expected_digests_refused_before_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock()
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with self.assertRaises(SystemExit):
                    train_adapter.main([
                        "--rows", str(path),
                        "--report", str(Path(tmp, "probe.json")),
                        "--probe",
                        "--base", train_adapter.FALLBACK_BASE,
                        "--revision", train_adapter.FALLBACK_REVISION,
                    ])
            fake_flm.from_pretrained.assert_not_called()

    def test_main_probe_fallback_pin_calls_run_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.FALLBACK_BASE}@{train_adapter.FALLBACK_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            with mock.patch.object(train_adapter, "run_probe", return_value={"pin": "fallback"}) as mocked:
                with mock.patch.object(train_adapter, "load_expected_digests", return_value=[]):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                        train_adapter.main([
                            "--rows", str(path),
                            "--report", str(Path(tmp, "probe.json")),
                            "--probe",
                            "--base", train_adapter.FALLBACK_BASE,
                            "--revision", train_adapter.FALLBACK_REVISION,
                            "--expected-digests", str(digest_path),
                        ])
            mocked.assert_called_once()

    def test_probe_length_gate_failure_skips_microsteps(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            fake_tokenizer = mock.MagicMock()
            fake_tokenizer.pad_token_id = 0
            fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=[1, 2, 3])
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.WINNER_REVISION
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            bad_stats = {"p50": 100.0, "p95": 2048.001, "max": 3000}
            encoded = [{
                "input_ids": [1, 2, 3],
                "attention_mask": [1, 1, 1],
                "labels": [-100, -100, 3],
            }]
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                    with mock.patch.object(train_adapter, "encode_rows", return_value=encoded):
                        with mock.patch.object(train_adapter, "length_statistics", return_value=bad_stats):
                            with mock.patch.object(train_adapter, "run_probe_microsteps") as micro_mock:
                                with self.assertRaises(SystemExit) as raised:
                                    train_adapter.main([
                                        "--rows", str(rows_path),
                                        "--report", str(report_path),
                                        "--probe",
                                        "--base", train_adapter.WINNER_BASE,
                                        "--revision", train_adapter.WINNER_REVISION,
                                        "--expected-digests", str(digest_path),
                                    ])
            self.assertIn("length gate failed", str(raised.exception))
            micro_mock.assert_not_called()
            self.assertTrue(report_path.is_file())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["length_gate_pass"])
            self.assertIsNone(payload["memory_pass"])
            self.assertIsNone(payload["seconds_per_microstep"])
            self.assertIsNone(payload["peak_reserved_vram_gib"])

    def test_probe_mode_and_authoritative_matrix(self):
        def report_for(base: str, revision: str, seq_len: int) -> dict:
            pin = train_adapter.probe_pin_name(base, revision)
            authoritative = train_adapter.probe_is_authoritative(base, revision, seq_len, 1, 1)
            mode = "d_m1_f_probe" if authoritative else "diagnostic_probe"
            kwargs = self._minimal_probe_report_kwargs()
            kwargs.update({
                "pin": pin,
                "mode": mode,
                "base": base,
                "revision": revision,
                "sequence_length": seq_len,
                "authoritative": authoritative,
            })
            return train_adapter.build_probe_report(**kwargs)

        winner_2048 = report_for(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048)
        self.assertEqual(winner_2048["mode"], "d_m1_f_probe")
        self.assertTrue(winner_2048["authoritative"])
        self.assertEqual(winner_2048["pin"], "winner")

        winner_1024 = report_for(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 1024)
        self.assertEqual(winner_1024["mode"], "diagnostic_probe")
        self.assertFalse(winner_1024["authoritative"])

        smoke_2048 = report_for(
            train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION, 2048)
        self.assertEqual(smoke_2048["mode"], "diagnostic_probe")
        self.assertFalse(smoke_2048["authoritative"])
        self.assertEqual(smoke_2048["pin"], "smoke")

        fallback_2048 = report_for(
            train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION, 2048)
        self.assertEqual(fallback_2048["mode"], "d_m1_f_probe")
        self.assertTrue(fallback_2048["authoritative"])
        self.assertEqual(fallback_2048["pin"], "fallback")

    def test_main_probe_seq_len_above_seq_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.SMOKE_BASE,
                    "--revision", train_adapter.SMOKE_REVISION,
                    "--probe-seq-len", "4096",
                ])

    def test_wrapper_fallback_switch_and_probe_timeout(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn("-Fallback", text)
        self.assertIn("mutually exclusive", text)
        self.assertIn("-Fallback is only valid together with -Probe", text)
        self.assertIn("m1-expected-qwen3-8b.json", text)
        self.assertIn("ContainsKey('TimeoutMinutes')", text)
        self.assertIn("$TimeoutMinutes = 150", text)

    def _minimal_probe_report_kwargs(self) -> dict:
        return {
            "run": "probe",
            "pin": "smoke",
            "mode": "diagnostic_probe",
            "base": train_adapter.SMOKE_BASE,
            "revision": train_adapter.SMOKE_REVISION,
            "rows": 1,
            "mask_coverage": 0.5,
            "resolved_commit": train_adapter.SMOKE_REVISION,
            "length_stats": {"p50": 1.0, "p95": 2.0, "max": 3},
            "length_gate_passed": True,
            "sequence_length": 2048,
            "sequence_length_method": "attended_prompt_repeat",
            "warmup_microsteps": 1,
            "timed_microsteps": 1,
            "gradient_accumulation": 1,
            "timed_optimizer_updates": 1,
            "seconds_per_microstep": 1.0,
            "peak_reserved_gib": 1.0,
            "rows_total": 96,
            "wall_seconds_three_epochs": 1.0,
            "wall_seconds_three_epochs_contingency": 1.2,
            "memory_pass": True,
            "time_pass": True,
            "authoritative": False,
            "overlength_rows": 0,
            "max_tokens": 3,
        }

    def test_train_refuses_overlength_row_before_trainer(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "out.json"
            fake_tokenizer = mock.MagicMock()
            fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=list(range(10)))
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.SMOKE_REVISION
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            long_encoded = [{
                "input_ids": list(range(train_adapter.SEQ + 5)),
                "attention_mask": [1] * (train_adapter.SEQ + 5),
                "labels": [-100] * train_adapter.SEQ + list(range(5)),
            }]
            cuda = mock.MagicMock()
            cuda.reset_peak_memory_stats = mock.MagicMock()
            with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                        with mock.patch.object(train_adapter, "encode_rows", return_value=long_encoded):
                            with mock.patch.object(train_adapter, "SFTTrainer") as trainer_mock:
                                with self.assertRaises(SystemExit) as raised:
                                    train_adapter.train(
                                        [row()],
                                        train_adapter.SMOKE_BASE,
                                        train_adapter.SMOKE_REVISION,
                                        20,
                                        report_path,
                                        winner=False,
                                    )
            self.assertIn("exceed SEQ", str(raised.exception))
            trainer_mock.assert_not_called()

    def test_from_pretrained_uses_local_files_only_when_digests_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "out.json"
            digest_path = Path(tmp) / "digests.json"
            digest_info = {"files": 1, "reads": 2, "bytes": 1, "weight_files": 1, "weight_map_checked": False}
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(side_effect=SystemExit("stop after load"))
            cuda = mock.MagicMock()
            cuda.reset_peak_memory_stats = mock.MagicMock()
            with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=digest_info):
                        with self.assertRaises(SystemExit):
                            train_adapter.train(
                                [row()],
                                train_adapter.WINNER_BASE,
                                train_adapter.WINNER_REVISION,
                                20,
                                report_path,
                                winner=True,
                                expected_digests_path=digest_path,
                            )
            _, kwargs = fake_flm.from_pretrained.call_args
            self.assertTrue(kwargs.get("local_files_only"))

    def test_probe_overlength_row_is_diagnostic_on_winner_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            seq = train_adapter.SEQ
            at_limit = {
                "input_ids": list(range(seq)),
                "attention_mask": [1] * seq,
                "labels": [-100] * (seq - 2) + [4, 5],
            }
            over = {
                "input_ids": list(range(seq + 1)),
                "attention_mask": [1] * (seq + 1),
                "labels": [-100] * (seq - 1) + [4, 5],
            }
            encoded = [at_limit] * 99 + [over]
            fake_tokenizer = mock.MagicMock()
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.WINNER_REVISION
            fake_model.parameters = mock.MagicMock(return_value=iter([mock.MagicMock(device="cpu")]))
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            exercise = [at_limit]
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                    with mock.patch.object(train_adapter, "encode_rows", return_value=encoded):
                        with mock.patch.object(
                            train_adapter, "prepare_probe_examples", return_value=(exercise, "attended_prompt_repeat"),
                        ):
                            with mock.patch.object(train_adapter, "run_probe_microsteps", return_value=(1.0, 9.0)):
                                train_adapter.run_probe(
                                    [row()],
                                    train_adapter.WINNER_BASE,
                                    train_adapter.WINNER_REVISION,
                                    report_path,
                                    probe_rows_total=100,
                                    probe_seq_len=seq,
                                    expected_digests_path=digest_path,
                                )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "diagnostic_probe")
            self.assertFalse(payload["authoritative"])
            self.assertEqual(payload["overlength_rows"], 1)
            self.assertEqual(payload["max_tokens"], seq + 1)

    def test_probe_exercise_rows_use_attended_prompt_repeat(self):
        seq_len = 12
        example = {
            "input_ids": [10, 11, 12, 20, 21],
            "attention_mask": [1, 1, 1, 1, 1],
            "labels": [-100, -100, -100, 20, 21],
        }
        original_response_labels = example["labels"][-2:]
        exercise = train_adapter.extend_probe_example_to_seq_len(example, seq_len)
        self.assertEqual(len(exercise["input_ids"]), seq_len)
        self.assertEqual(exercise["attention_mask"], [1] * seq_len)
        self.assertEqual(exercise["labels"][-2:], original_response_labels)
        self.assertEqual(exercise["input_ids"][-2:], [20, 21])
        fill_labels = exercise["labels"][3:-2]
        self.assertTrue(fill_labels)
        self.assertTrue(all(label == train_adapter.IGNORED_LABEL for label in fill_labels))
        train_adapter.require_response_only(exercise["labels"], 3)

    def test_probe_authoritative_fails_memory_gate_after_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=False, time_pass=True, authoritative=True,
                expect_exit=True, message="memory gate",
            )

    def test_probe_authoritative_fails_time_gate_after_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=True, time_pass=False, authoritative=True,
                expect_exit=True, message="time gate",
            )

    def test_probe_diagnostic_failed_gate_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=False, time_pass=False, authoritative=False,
                expect_exit=False,
            )

    def test_probe_is_authoritative_only_when_projection_matches_loaded_rows(self):
        self.assertTrue(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048, 96, 96,
        ))
        self.assertFalse(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048, 1, 96,
        ))
        self.assertFalse(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 1024, 1, 1,
        ))

    def test_main_probe_default_rows_total_is_authoritative_on_winner_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            captured: dict = {}

            def fake_run_probe(rows, base, revision, report_path, **kwargs):
                loaded = len(rows)
                projection = kwargs["probe_rows_total"]
                if projection is None:
                    projection = loaded
                captured["projection"] = projection
                captured["authoritative"] = train_adapter.probe_is_authoritative(
                    base, revision, kwargs["probe_seq_len"], loaded, projection,
                )
                return {"authoritative": captured["authoritative"], "mode": "d_m1_f_probe"}

            with mock.patch.object(train_adapter, "run_probe", side_effect=fake_run_probe):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.WINNER_BASE,
                    "--revision", train_adapter.WINNER_REVISION,
                    "--expected-digests", str(digest_path),
                ])
            self.assertEqual(captured["projection"], 1)
            self.assertTrue(captured["authoritative"])

    def test_main_probe_explicit_rows_total_mismatch_is_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            captured: dict = {}

            def fake_run_probe(rows, base, revision, report_path, **kwargs):
                loaded = len(rows)
                projection = kwargs["probe_rows_total"] or loaded
                captured["authoritative"] = train_adapter.probe_is_authoritative(
                    base, revision, kwargs["probe_seq_len"], loaded, projection,
                )
                return {"authoritative": captured["authoritative"], "mode": "diagnostic_probe"}

            with mock.patch.object(train_adapter, "run_probe", side_effect=fake_run_probe):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.WINNER_BASE,
                    "--revision", train_adapter.WINNER_REVISION,
                    "--expected-digests", str(digest_path),
                    "--probe-rows-total", "96",
                ])
            self.assertFalse(captured["authoritative"])

    def test_wrapper_passes_probe_rows_total_only_when_bound(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\$ProbeRowsTotal\s*=\s*96")
        self.assertIn("ContainsKey('ProbeRowsTotal')", text)
        self.assertIn("'--probe-rows-total', \"$ProbeRowsTotal\"", text)

    def _run_probe_with_gate_outcome(
        self,
        rows_path: Path,
        report_path: Path,
        *,
        memory_pass: bool,
        time_pass: bool,
        authoritative: bool,
        expect_exit: bool,
        message: str = "",
    ) -> None:
        digest_path = rows_path.parent / "digests.json"
        key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
        digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
        fake_tokenizer = mock.MagicMock()
        fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=[1, 2, 3, 4, 5])
        base = train_adapter.WINNER_BASE if authoritative else train_adapter.SMOKE_BASE
        revision = train_adapter.WINNER_REVISION if authoritative else train_adapter.SMOKE_REVISION
        fake_model = mock.MagicMock()
        fake_model.config._commit_hash = revision
        fake_model.parameters = mock.MagicMock(return_value=iter([mock.MagicMock(device="cpu")]))
        fake_flm = mock.MagicMock()
        fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
        fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
        encoded = [{
            "input_ids": [1, 2, 3, 4, 5],
            "attention_mask": [1, 1, 1, 1, 1],
            "labels": [-100, -100, -100, 4, 5],
        }]
        exercise = [{
            "input_ids": list(range(train_adapter.SEQ)),
            "attention_mask": [1] * train_adapter.SEQ,
            "labels": [-100] * (train_adapter.SEQ - 2) + [4, 5],
        }]
        peak = 10.0 if memory_pass else 12.0
        seconds = 1.0 if time_pass else 12_000.0
        argv = [
            "--rows", str(rows_path),
            "--report", str(report_path),
            "--probe",
            "--base", base,
            "--revision", revision,
        ]
        if authoritative:
            argv.extend(["--expected-digests", str(digest_path)])
        patches = [
            mock.patch.object(train_adapter, "FastLanguageModel", fake_flm),
            mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None),
            mock.patch.object(train_adapter, "encode_rows", return_value=encoded),
            mock.patch.object(train_adapter, "prepare_probe_examples", return_value=(exercise, "attended_prompt_repeat")),
            mock.patch.object(train_adapter, "run_probe_microsteps", return_value=(seconds, peak)),
        ]
        if expect_exit:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                with self.assertRaises(SystemExit) as raised:
                    train_adapter.main(argv)
                if message:
                    self.assertIn(message, str(raised.exception))
            self.assertTrue(report_path.is_file())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["memory_pass"], memory_pass)
            self.assertEqual(payload["time_pass"], time_pass)
            self.assertEqual(payload["authoritative"], authoritative)
        else:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                train_adapter.main(argv)
            self.assertTrue(report_path.is_file())

    def test_probe_report_authoritative_for_winner_and_smoke_pins(self):
        kwargs = self._minimal_probe_report_kwargs()
        kwargs.update({"pin": "winner", "mode": "d_m1_f_probe", "authoritative": True})
        winner = train_adapter.build_probe_report(**kwargs)
        smoke_kwargs = self._minimal_probe_report_kwargs()
        smoke_kwargs.update({"pin": "smoke", "mode": "diagnostic_probe", "authoritative": False})
        smoke = train_adapter.build_probe_report(**smoke_kwargs)
        self.assertTrue(winner["authoritative"])
        self.assertFalse(smoke["authoritative"])
        self.assertTrue(winner["memory_pass"])
        self.assertTrue(smoke["time_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
