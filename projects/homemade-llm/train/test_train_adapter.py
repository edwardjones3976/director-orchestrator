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
import os
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


def make_reparse_link(link: Path, target: Path) -> bool:
    """Create a symlink, or on Windows a directory junction when symlinks are
    unavailable. Returns False when the host permits neither."""
    try:
        link.symlink_to(target)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and target.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return os.path.isjunction(link) or link.is_symlink()
        except (OSError, NotImplementedError, ImportError):
            return False
    return False


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
                "eval/validate_rows.py",
                "eval/test_validate_rows.py",
                "train/prompt_template.py",
                "schemas/director-data-v1-row.schema.json",
            ):
                dest = hl / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / "projects" / "homemade-llm" / rel, dest)
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
                "(root / 'rows.jsonl').write_bytes(''.join(json.dumps(item) + '\\n' for item in (train, dev)).encode('utf-8'))\n"
                "code = validate_rows.validate(['--rows', str(root / 'rows.jsonl'), '--set-a', str(set_a),\n"
                "    '--set-b', str(set_b), '--out', str(root / 'validated')])\n"
                "raise SystemExit(code)\n",
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run([sys.executable, str(script), str(root)], cwd=repo, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            accepted = [json.loads(line) for line in (out / "accepted.jsonl").read_text(encoding="utf-8").splitlines()]
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["n_accepted"], 2)
            self.assertEqual(report["n_rejected"], 0)
            self.assertEqual(report["n_quarantined"], 0)
            self.assertEqual(report["rows_per_split"], {"train": 1, "dev": 1})
            self.assertEqual({item["source_group"] for item in accepted}, {"seed-001", "seed-002"})
            loaded = train_adapter.load_training_rows(out / "accepted.jsonl")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["split"], "train")
            self.assertEqual(loaded[0]["row_id"], next(item["row_id"] for item in accepted if item["split"] == "train"))
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

    def test_smoke_arguments_still_use_steps_without_epoch_saving(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertEqual(args["max_steps"], 20)
        self.assertEqual(args["save_strategy"], "no")
        self.assertNotIn("num_train_epochs", args)
        self.assertEqual(args["learning_rate"], 2e-4)

    def test_epoch_arguments_use_epochs_per_epoch_saving_and_unset_max_steps(self):
        args = train_adapter.sft_arguments("out", None, epochs=4)
        self.assertEqual(args["num_train_epochs"], 4)
        self.assertEqual(args["max_steps"], -1)
        self.assertEqual(args["save_strategy"], "epoch")
        self.assertEqual(args["learning_rate"], train_adapter.EPOCH_LEARNING_RATE)
        self.assertEqual(args["learning_rate"], 1e-4)
        self.assertEqual(args["lr_scheduler_type"], "cosine")
        self.assertEqual(args["gradient_accumulation_steps"], 8)
        self.assertNotIn("save_total_limit", args)

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

    def test_training_run_rejects_conflicting_or_missing_mode_flags_before_model_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            cases = [
                (["--steps", "20", "--epochs", "3"], "exactly one of --steps or --epochs, not both"),
                (["--epochs", "0"], "--epochs must be 1 to 6"),
                (["--epochs", "7"], "--epochs must be 1 to 6"),
                (["--epochs", "x"], None),
                ([], "needs exactly one of --steps or --epochs"),
                (["--epochs", "3", "--probe"], "--epochs cannot be combined with --probe"),
            ]
            for extra, message in cases:
                with self.subTest(extra=extra):
                    with mock.patch.object(train_adapter, "FastLanguageModel") as model:
                        with self.assertRaises(SystemExit) as raised:
                            train_adapter.main([
                                "--rows", str(path), "--report", str(Path(tmp, "out.json")),
                                "--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION,
                                *extra,
                            ])
                        if message is not None:
                            self.assertIn(message, str(raised.exception))
                    model.from_pretrained.assert_not_called()

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
        self.assertIn("m2-expected-qwen3-14b-v2.json", text)
        self.assertIn("HF_HUB_OFFLINE", text)

    def test_wrapper_serialized_docker_arguments_keep_offline_flags_before_image(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate Docker argument construction")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        # Evaluate only the argument-building block, never the wrapper or its setup.
        start = source.index("    $dockerArgs = ")
        end = source.index("    function Get-AdapterState", start)
        construction = source[start:end]
        for digests in ("", "dummy-digests.json"):
            with self.subTest(expected_digests=bool(digests)):
                command = """
$ErrorActionPreference = 'Stop'
$mounts = @('-v', 'dummy:/workspace/hf')
$name = 'dummy-container'; $code = 'dummy-code'; $Image = 'dummy-image'
$Base = 'dummy-base'; $Revision = 'dummy-revision'; $Steps = 20
$Rows = 'accepted.jsonl'; $runId = 'dummy-run'
$Winner = $true; $Probe = $true; $ProbeSeqLen = 2048; $ProbeRowsTotal = 96
$PSBoundParameters = @{ ProbeRowsTotal = 96 }
""" + f"$ExpectedDigests = '{digests}'\n$TrustedDigestRoot = '{'dummy-root.json' if digests else ''}'\n" + construction + "\nConvertTo-Json -InputObject $dockerArgs -Compress"
                completed = subprocess.run(
                    [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                args = json.loads(completed.stdout)
                image = args.index("dummy-image")
                self.assertEqual(args[image - 2:image], ["--entrypoint", "python"])
                self.assertEqual(args[image + 1], "/code/train_adapter.py")
                options, trainer = args[:image], args[image + 2:]
                self.assertNotIn("-e", trainer)
                for flag in ("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1"):
                    if digests:
                        self.assertEqual(args.count(flag), 1)
                        self.assertEqual(options[options.index(flag) - 1], "-e")
                    else:
                        self.assertNotIn(flag, args)
                if digests:
                    index = trainer.index("--expected-digests")
                    self.assertEqual(trainer[index + 1], "/workspace/expected-digests.json")
                    self.assertNotIn("--expected-digests", options)
                    index = trainer.index("--trusted-digest-root")
                    self.assertEqual(trainer[index + 1], "/workspace/trusted-digest-root.json")
                    self.assertNotIn("--trusted-digest-root", options)
                else:
                    self.assertNotIn("--expected-digests", args)
                    self.assertNotIn("--trusted-digest-root", args)
                self.assertIn("--winner", trainer)
                self.assertEqual(trainer[-5:], ["--probe", "--probe-seq-len", "2048", "--probe-rows-total", "96"])

    def test_wrapper_epoch_mode_serialized_arguments_map_output_and_skip_steps(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate Docker argument construction")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        start = source.index("    $dockerArgs = ")
        end = source.index("    function Get-AdapterState", start)
        construction = source[start:end]
        command = """
$ErrorActionPreference = 'Stop'
$mounts = @('-v', 'dummy:/workspace/models/m2')
$name = 'dummy-container'; $code = 'dummy-code'; $Image = 'dummy-image'
$Base = 'dummy-base'; $Revision = 'dummy-revision'; $Steps = 20; $Epochs = 4
$Rows = 'accepted.jsonl'; $runId = 'dummy-run'
$Winner = $false; $Probe = $false; $ProbeSeqLen = 2048; $ProbeRowsTotal = 96
$PSBoundParameters = @{}
$ExpectedDigests = ''
$TrustedDigestRoot = ''
""" + construction + "\nConvertTo-Json -InputObject $dockerArgs -Compress"
        completed = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        args = json.loads(completed.stdout)
        image = args.index("dummy-image")
        self.assertEqual(args[image - 2:image], ["--entrypoint", "python"])
        self.assertEqual(args[image + 1], "/code/train_adapter.py")
        options, trainer = args[:image], args[image + 2:]
        self.assertNotIn("-e", trainer)
        self.assertNotIn("--steps", args)
        self.assertNotIn("ADAPTER_STEPS=20", args)
        self.assertEqual(options[options.index("ADAPTER_MODELS_DIR=/workspace/models/m2") - 1], "-e")
        self.assertIn("ADAPTER_BASE=dummy-base", options)
        self.assertIn("HF_HOME=/workspace/hf", options)
        self.assertNotIn("HF_HUB_OFFLINE=1", args)
        self.assertEqual(trainer[:2], ["--rows", "/workspace/rows/accepted.jsonl"])
        self.assertIn("--epochs", trainer)
        self.assertEqual(trainer[trainer.index("--epochs") + 1], "4")
        self.assertIn("--report", trainer)
        self.assertEqual(trainer[trainer.index("--report") + 1], "/workspace/reports/dummy-run.json")
        self.assertNotIn("--winner", trainer)
        self.assertNotIn("--probe", trainer)

    def test_wrapper_guard_block_refuses_winner_without_probe_and_bad_epochs(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate wrapper argument guards")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        start = source.index("if ($Winner -and $Fallback)")
        end = source.index("if ($Steps -lt 1)", start)
        guards = source[start:end]
        cases = [
            ({"Winner": "$true", "Probe": "$false"}, "-Winner is only valid together with -Probe"),
            ({"Epochs": "0"}, "-Epochs must be between 1 and 6"),
            ({"Epochs": "7"}, "-Epochs must be between 1 and 6"),
            ({"Epochs": "4", "Steps": "20"}, "-Epochs and -Steps are mutually exclusive"),
            ({"Epochs": "4", "Probe": "$true"}, "-Epochs is a training mode"),
            ({"Epochs": "4"}, None),
            ({"Winner": "$true", "Probe": "$true"}, None),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides, message=message):
                bound = "@{ " + "; ".join(
                    f"{name} = {value}" for name, value in overrides.items() if name in ("Epochs", "Steps")
                ) + " }"
                setup = "\n".join(
                    f"${name} = {value}" for name, value in overrides.items()
                )
                command = (
                    "$ErrorActionPreference = 'Stop'\n"
                    "$Winner = $false; $Fallback = $false; $Probe = $false; "
                    "$Epochs = 0; $Steps = 20; $PSBoundParameters = @{}\n"
                    + setup + "\n"
                    + (f"$PSBoundParameters = {bound}\n" if bound != "@{  }" else "")
                    + guards + "\n'GUARDS-PASSED'"
                )
                completed = subprocess.run(
                    [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=30,
                )
                if message is None:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn("GUARDS-PASSED", completed.stdout)
                else:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(message, completed.stderr + completed.stdout)

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

    def test_warmup_boundary_discards_pending_gradients_without_a_step(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available = mock.MagicMock(return_value=False)
        fake_optimizer = fake_torch.optim.AdamW.return_value
        fake_model = mock.MagicMock()
        fake_model.parameters = mock.MagicMock(side_effect=lambda: iter([mock.MagicMock()]))
        with mock.patch.object(train_adapter, "torch", fake_torch):
            # 10 warm-up microsteps at accumulation 8: one update at 8, then the
            # 2 pending microsteps are discarded (zero_grad, no extra step).
            train_adapter.run_probe_microsteps(
                fake_model, [{"input_ids": [1], "attention_mask": [1], "labels": [-100, 2]}],
                warmup_microsteps=10, timed_microsteps=0, gradient_accumulation=8,
            )
            self.assertEqual(fake_optimizer.step.call_count, 1)
            self.assertEqual(fake_optimizer.zero_grad.call_count, 2)
            # The timed phase keeps the previous behaviour: a trailing partial
            # window still performs its optimizer update.
            fake_torch.reset_mock()
            fake_optimizer.reset_mock()
            fake_torch.cuda.is_available = mock.MagicMock(return_value=False)
            fake_torch.optim.AdamW.return_value = fake_optimizer
            train_adapter.run_probe_microsteps(
                fake_model, [{"input_ids": [1], "attention_mask": [1], "labels": [-100, 2]}],
                warmup_microsteps=0, timed_microsteps=10, gradient_accumulation=8,
            )
            self.assertEqual(fake_optimizer.step.call_count, 2)
            self.assertEqual(fake_optimizer.zero_grad.call_count, 2)
        self.assertEqual(train_adapter.PROBE_WARMUP_MICROSTEPS % train_adapter.PROBE_GRAD_ACCUM, 2)
        kwargs = self._minimal_probe_report_kwargs()
        kwargs["warmup_discarded_microsteps"] = train_adapter.PROBE_WARMUP_MICROSTEPS % train_adapter.PROBE_GRAD_ACCUM
        report = train_adapter.build_probe_report(**kwargs)
        self.assertEqual(report["warmup_pending_gradients"], "discarded_at_boundary_without_step")
        self.assertEqual(report["warmup_discarded_microsteps"], 2)

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

    def _assert_exporter_adapter_hashes(self, leaf: Path) -> None:
        """A re-implementation of the exporter's Assert-AdapterHashes gate."""
        manifest = json.loads((leaf / "adapter-hashes.json").read_text(encoding="utf-8"))
        self.assertIn("files", manifest)
        names = list(manifest["files"])
        for mandatory in ("adapter_config.json", "adapter_model.safetensors"):
            self.assertIn(mandatory, names)
        seen: set[str] = set()
        for rel in names:
            self.assertNotIn("\\", rel)
            self.assertNotIn(":", rel)
            self.assertFalse(rel.startswith("/"))
            for segment in rel.split("/"):
                self.assertNotIn(segment, ("", ".", ".."))
                self.assertFalse(segment.endswith((" ", ".")))
            self.assertRegex(manifest["files"][rel], r"^[0-9a-fA-F]{64}$")
            self.assertNotIn(rel.lower(), seen)
            seen.add(rel.lower())
            full = leaf / rel
            self.assertTrue(full.is_file(), rel)
            self.assertFalse(full.is_symlink() or os.path.isjunction(full))
            self.assertEqual(
                hashlib.sha256(full.read_bytes()).hexdigest(),
                manifest["files"][rel].lower(),
            )
        for path in sorted(leaf.rglob("*")):
            if path.name == "adapter-hashes.json" and path.parent == leaf:
                continue
            self.assertFalse(path.is_symlink() or os.path.isjunction(path), str(path))
            if path.is_file():
                self.assertIn(path.relative_to(leaf).as_posix(), names)

    def _trusted_chain(self, root: Path) -> tuple[Path, Path, str]:
        # Synthetic metadata and weights only. Tests patch the fixed code pin,
        # never add a runtime argument that can approve an arbitrary root.
        snapshot = root / "snapshot"
        snapshot.mkdir(exist_ok=True)
        entry = self._digest_entry(snapshot, "model.safetensors", b"synthetic weights")
        key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
        listing = root / "upstream.json"
        listing.write_bytes(json.dumps({key: {"files": [entry]}}).encode())
        trusted = root / "approved-root.json"
        trusted.write_bytes(json.dumps({"models": [{
            "hf_repo": train_adapter.WINNER_BASE,
            "hf_revision": train_adapter.WINNER_REVISION,
            "upstream_weight_digests_sha256": hashlib.sha256(listing.read_bytes()).hexdigest(),
        }]}).encode())
        return trusted, listing, hashlib.sha256(trusted.read_bytes()).hexdigest()

    def test_authenticated_chain_matches_before_local_weight_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=root / "snapshot") as snapshot:
                result = train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            snapshot.assert_called_once_with(train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
            self.assertEqual(result["reads"], 2)
            self.assertEqual(result["trusted_root_sha256"], pin)
            self.assertEqual(result["upstream_digest_list_sha256"], hashlib.sha256(listing.read_bytes()).hexdigest())

    def test_changed_root_refused_before_parse_or_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            trusted.write_bytes(b"not even JSON")
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                with self.assertRaisesRegex(SystemExit, "root does not match"):
                    train_adapter.digest_verification_before_load(
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                    )
            snapshot.assert_not_called()

    def test_self_consistent_changed_weights_and_list_cannot_reapprove_themselves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            entry = self._digest_entry(root / "snapshot", "model.safetensors", b"changed weights")
            listing.write_bytes(json.dumps({
                f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}": {"files": [entry]},
            }).encode())
            # The old weight-only check would accept this changed, self-consistent pair.
            train_adapter.verify_expected_digests(root / "snapshot", [entry])
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                with self.assertRaisesRegex(SystemExit, "list does not match"):
                    train_adapter.digest_verification_before_load(
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                    )
            snapshot.assert_not_called()

    def test_changed_list_is_authenticated_before_json_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            trusted, listing, pin = self._trusted_chain(Path(tmp))
            listing.write_bytes(b"not JSON")
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin):
                with self.assertRaisesRegex(SystemExit, "list does not match"):
                    train_adapter.load_authenticated_digests(
                        trusted, listing, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION,
                    )

    def test_approved_root_refuses_wrong_model_revision_and_unapproved_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            trusted, listing, pin = self._trusted_chain(Path(tmp))
            for base, revision in (
                (train_adapter.WINNER_BASE, "0" * 40),
                ("org/another-model", train_adapter.WINNER_REVISION),
                (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION),
            ):
                with self.subTest(base=base, revision=revision), \
                     mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                    with self.assertRaisesRegex(SystemExit, "no unique approved entry"):
                        train_adapter.digest_verification_before_load(base, revision, listing, trusted)
                    snapshot.assert_not_called()

    def test_direct_cli_cannot_bypass_root_by_omitting_winner_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accepted = write_accepted(root, [row()])
            _, listing, _ = self._trusted_chain(root)
            for base, revision in (
                (train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION),
                (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION),
            ):
                for mode in (["--steps", "20"], ["--probe"]):
                    with self.subTest(base=base, mode=mode), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                        with self.assertRaisesRegex(SystemExit, "trusted-digest-root is required"):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "report-out.json"),
                                "--base", base, "--revision", revision, "--expected-digests", str(listing),
                                *mode,
                            ])
                        snapshot.assert_not_called()
                        model.from_pretrained.assert_not_called()

    def test_wrapper_authenticates_root_and_list_before_gpu_ownership(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate metadata preflight")
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        start = source.index("$TrustedDigestRoot = ''")
        end = source.index("if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests))", start)
        self.assertLess(end, source.index("$gpuLock ="))
        block = source[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            block = block.replace("C:\\llm\\reports\\m2-trusted-digest-manifest-v2.json", str(trusted))
            block = block.replace(train_adapter.TRUSTED_DIGEST_ROOT_SHA256, pin)
            for mode in ("matching", "changed_list", "changed_root", "fallback"):
                trusted, listing, _ = self._trusted_chain(root)
                if mode == "changed_list":
                    listing.write_bytes(b"changed")
                if mode == "changed_root":
                    trusted.write_bytes(b"changed")
                base = train_adapter.FALLBACK_BASE if mode == "fallback" else train_adapter.WINNER_BASE
                revision = train_adapter.FALLBACK_REVISION if mode == "fallback" else train_adapter.WINNER_REVISION
                preamble = (
                    "$ErrorActionPreference = 'Stop'\n"
                    f"$WinnerBase = '{train_adapter.WINNER_BASE}'; $FallbackBase = '{train_adapter.FALLBACK_BASE}'\n"
                    f"$Base = '{base}'; $Revision = '{revision}'; $ExpectedDigests = '{listing}'\n"
                )
                result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", preamble + block],
                                        capture_output=True, text=True, timeout=30)
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode == 0, mode == "matching", result.stderr)

    def test_wrapper_root_mount_is_read_only_and_trainer_has_no_pin_override(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        self.assertIn('${TrustedDigestRoot}:/workspace/trusted-digest-root.json:ro', source)
        self.assertLess(source.index('${TrustedDigestRoot}:/workspace/trusted-digest-root.json:ro'),
                        source.index("'--entrypoint', 'python', $Image"))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                train_adapter.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("trusted-root-sha", Path(train_adapter.__file__).read_text(encoding="utf-8"))

    def test_python_and_wrapper_use_the_same_owner_approved_v2_root_pin_and_default(self):
        approved_pin = "d1a4a2d921c3f5197c2867dcdb50dbfb46d2189bbb88141ed56d3f3eb19d36af"
        self.assertEqual(train_adapter.TRUSTED_DIGEST_ROOT_SHA256, approved_pin)
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        self.assertIn(f"$TrustedDigestRootSHA256 = '{train_adapter.TRUSTED_DIGEST_ROOT_SHA256}'", source)
        self.assertIn("$TrustedDigestRoot = 'C:\\llm\\reports\\m2-trusted-digest-manifest-v2.json'", source)
        self.assertIn("$ExpectedDigests = Join-Path $HotRoot 'reports\\m2-expected-qwen3-14b-v2.json'", source)

    def _complete_trusted_snapshot(self, root: Path) -> tuple[Path, Path, str]:
        trusted, listing, _ = self._trusted_chain(root)
        snapshot = root / "snapshot"
        key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
        payload = json.loads(listing.read_bytes())
        for name, data in (
            ("config.json", b'{"model_type":"synthetic"}'),
            ("tokenizer.json", b'{"synthetic_tokenizer":true}'),
            ("model.safetensors.index.json", b'{"weight_map":{"layer":"model.safetensors"}}'),
        ):
            payload[key]["files"].append(self._digest_entry(snapshot, name, data))
        listing.write_bytes(json.dumps(payload).encode())
        approved = json.loads(trusted.read_bytes())
        approved["models"][0]["upstream_weight_digests_sha256"] = hashlib.sha256(listing.read_bytes()).hexdigest()
        trusted.write_bytes(json.dumps(approved).encode())
        return trusted, listing, hashlib.sha256(trusted.read_bytes()).hexdigest()

    def test_complete_snapshot_authenticates_metadata_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._complete_trusted_snapshot(root)
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=root / "snapshot"):
                result = train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            self.assertEqual(result["snapshot_coverage"], "all_regular_files")
            self.assertEqual(result["snapshot_files"], 4)
            self.assertEqual(result["files"], 4)
            self.assertEqual(result["weight_files"], 1)
            self.assertTrue(result["weight_map_checked"])

    def test_corrupt_snapshot_loading_metadata_refused_before_index_parse_or_model_load(self):
        for name in ("config.json", "tokenizer.json", "model.safetensors.index.json"):
            for mode in (["--steps", "20"], ["--probe"]):
                with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    accepted = write_accepted(root, [row()])
                    trusted, listing, pin = self._complete_trusted_snapshot(root)
                    snapshot = root / "snapshot"
                    path = snapshot / name
                    path.write_bytes(b"!" * path.stat().st_size)
                    self.assertEqual((snapshot / "model.safetensors").read_bytes(), b"synthetic weights")
                    with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                         mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "_index_weight_map_shards") as parse_index:
                        with self.assertRaisesRegex(SystemExit, "digest mismatch for " + name.replace(".", r"\.")):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "metrics.json"),
                                "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                                "--expected-digests", str(listing), "--trusted-digest-root", str(trusted), *mode,
                            ])
                        parse_index.assert_not_called()
                        model.from_pretrained.assert_not_called()

    def test_unlisted_snapshot_input_requires_existing_approved_digest_before_model_load(self):
        for name in ("config.json", "tokenizer.json", "model.safetensors.index.json", "future-format.data"):
            for mode in (["--steps", "20"], ["--probe"]):
                with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    accepted = write_accepted(root, [row()])
                    trusted, listing, pin = self._trusted_chain(root)
                    snapshot = root / "snapshot"
                    (snapshot / name).write_bytes(b"unapproved synthetic metadata")
                    with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                         mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "_index_weight_map_shards") as parse_index:
                        with self.assertRaisesRegex(SystemExit, "snapshot input lacks an approved digest"):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "metrics.json"),
                                "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                                "--expected-digests", str(listing), "--trusted-digest-root", str(trusted), *mode,
                            ])
                        parse_index.assert_not_called()
                        model.from_pretrained.assert_not_called()
                    self.assertEqual((snapshot / "model.safetensors").read_bytes(), b"synthetic weights")

    def test_index_parsed_only_after_all_snapshot_files_pass_both_digest_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._complete_trusted_snapshot(root)
            snapshot = root / "snapshot"
            seen = []
            real_hash = train_adapter._sha256_file
            real_parse = train_adapter._index_weight_map_shards

            def record_hash(path):
                seen.append(path.name)
                return real_hash(path)

            def parse_after_hashes(path):
                self.assertEqual(len(seen), 8)
                for name in ("model.safetensors", "config.json", "tokenizer.json", "model.safetensors.index.json"):
                    self.assertEqual(seen.count(name), 2)
                return real_parse(path)

            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                 mock.patch.object(train_adapter, "_sha256_file", side_effect=record_hash), \
                 mock.patch.object(train_adapter, "_index_weight_map_shards", side_effect=parse_after_hashes) as parse:
                train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            parse.assert_called_once_with(snapshot)

    def test_digest_paths_cannot_escape_complete_snapshot_coverage(self):
        for name in ("../escape.bin", "/absolute.bin", "C:/escape.bin", "folder\\escape.bin"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(SystemExit, "confined relative snapshot files"):
                    train_adapter.verify_expected_digests(
                        Path(tmp), [{"path": name, "sha256": "a" * 64, "size": 1}],
                    )

    def test_digest_traversal_refusal_precedes_duplicate_and_digest_checks(self):
        # A clean-looking name that resolves outside must be refused by
        # containment, not by the duplicate, missing-file or digest checks.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "outside.bin").write_bytes(b"outside the snapshot")
            if not make_reparse_link(snapshot / "linked", elsewhere):
                self.skipTest("no reparse-point creation is permitted on this host")
            entries = [
                {"path": "linked/outside.bin", "sha256": "a" * 64, "size": 29},
                {"path": "linked/outside.bin", "sha256": "b" * 64, "size": 1},
            ]
            with self.assertRaisesRegex(SystemExit, "escapes the expected directory"):
                train_adapter.verify_expected_digests(snapshot, entries)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            entry = {"path": "gone/../gone.bin", "sha256": "a" * 64, "size": 1}
            with self.assertRaisesRegex(SystemExit, "confined relative snapshot files"):
                train_adapter.verify_expected_digests(snapshot, [entry])

    def test_digest_index_entries_must_stay_inside_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            weights = self._digest_entry(snapshot, "model-00001-of-00001.safetensors", b"synthetic weights")
            index_payload = json.dumps({"weight_map": {"model.layers.0": "../../outside.bin"}})
            (snapshot / "model.safetensors.index.json").write_text(index_payload, encoding="utf-8")
            index_entry = {
                "path": "model.safetensors.index.json",
                "sha256": hashlib.sha256(index_payload.encode()).hexdigest(),
                "size": len(index_payload),
            }
            # The manifest itself is clean; only the index weight_map escapes.
            # The confinement refusal fires instead of the membership check
            # that would otherwise complain about an unlisted shard.
            entries = [weights, index_entry]
            with self.assertRaisesRegex(SystemExit, "digest index paths must be confined"):
                train_adapter.verify_expected_digests(snapshot, entries)

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
                    "snapshot_files": 2,
                    "snapshot_coverage": "all_regular_files",
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
            index = root / "model.safetensors.index.json"
            index_entry = {"path": index.name, "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
                           "size": index.stat().st_size}
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry, index_entry])
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

    def test_wrapper_winner_requires_probe_and_epoch_switch_guards(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn("-Winner is only valid together with -Probe", text)
        self.assertIn("-Rows <jsonl> -Winner -Probe", text)
        self.assertIn("-Rows <jsonl> -Epochs 4", text)
        self.assertIn("'-Epochs must be between 1 and 6'", text)
        self.assertIn("'-Epochs and -Steps are mutually exclusive; pass exactly one'", text)
        self.assertIn("ContainsKey('Epochs')", text)
        self.assertIn("ContainsKey('Steps')", text)
        # The persistent models mount and the read-only trust mounts stay.
        self.assertIn("@('models\\m2', 'models/m2')", text)
        self.assertIn(":/workspace/rows:ro", text)
        self.assertIn(":/code:ro", text)
        self.assertIn(":/workspace/trusted-digest-root.json:ro", text)
        self.assertIn(":/workspace/expected-digests.json:ro", text)
        self.assertIn("ADAPTER_MODELS_DIR=/workspace/models/m2", text)

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

    def test_epoch_train_creates_leaves_and_a_truthful_report(self):
        class FakeModel:
            def __init__(self):
                self.config = types.SimpleNamespace(_commit_hash=train_adapter.SMOKE_REVISION)
                self.saves = 0

            def save_pretrained(self, directory):
                # PEFT-style adapter save: mandatory files plus an optional README.
                Path(directory, "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
                Path(directory, "adapter_model.safetensors").write_bytes(b"adapter weights")
                Path(directory, "README.md").write_text("adapter", encoding="utf-8")
                self.saves += 1

        class FakeTrainer:
            seen_args = None

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                FakeTrainer.seen_args = kwargs["args"].__dict__

            def train(self):
                callback = self.kwargs["callbacks"][0]
                for epoch in range(1, self.kwargs["args"].num_train_epochs + 1):
                    state = types.SimpleNamespace(epoch=float(epoch), global_step=epoch * 8)
                    callback.on_epoch_end(None, state, None)
                return types.SimpleNamespace(training_loss=1.25)

        fake_model = FakeModel()
        fake_tokenizer = mock.MagicMock()
        fake_tokenizer.apply_chat_template = mock.MagicMock(
            side_effect=lambda *args, **kwargs: [1, 2, 3, 4] if kwargs.get("add_generation_prompt") else [1, 2, 3, 4, 5, 6])
        fake_flm = mock.MagicMock()
        fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
        fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
        cuda = mock.MagicMock()
        cuda.max_memory_reserved = mock.MagicMock(return_value=8.0 * 2**30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "m2-run.json"
            with mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}):
                with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                    with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                        with mock.patch.object(train_adapter, "Dataset", mock.MagicMock()):
                            with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                                with mock.patch.object(train_adapter, "SFTConfig", lambda **kwargs: types.SimpleNamespace(**kwargs)):
                                    with mock.patch.object(train_adapter, "SFTTrainer", FakeTrainer):
                                        report = train_adapter.train(
                                            [row()],
                                            train_adapter.SMOKE_BASE,
                                            train_adapter.SMOKE_REVISION,
                                            None,
                                            report_path,
                                            winner=False,
                                            epochs=3,
                                        )
            run_dir = root / "models" / "m2-run"
            self.assertEqual(report["completed_epochs"], 3)
            self.assertEqual(report["export_ready"], True)
            self.assertEqual(report["output_dir"], str(run_dir))
            self.assertEqual(FakeTrainer.seen_args["num_train_epochs"], 3)
            self.assertEqual(FakeTrainer.seen_args["max_steps"], -1)
            self.assertEqual(FakeTrainer.seen_args["save_strategy"], "epoch")
            self.assertEqual([entry["epoch"] for entry in report["checkpoints"]], [1, 2, 3])
            for entry in report["checkpoints"]:
                leaf = Path(entry["dir"])
                self.assertEqual(leaf.name, f"epoch-{entry['epoch']:02d}")
                self.assertEqual(entry["global_step"], entry["epoch"] * 8)
                self.assertRegex(entry["manifest_sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(
                    sorted(path.name for path in leaf.iterdir()),
                    ["README.md", "adapter-hashes.json", "adapter_config.json", "adapter_model.safetensors"],
                )
                self.assertEqual(entry["files"], ["README.md", "adapter_config.json", "adapter_model.safetensors"])
                manifest = json.loads((leaf / "adapter-hashes.json").read_text(encoding="utf-8"))
                self.assertEqual(set(manifest["files"]), set(entry["files"]))
                self._assert_exporter_adapter_hashes(leaf)
            final = report["final_adapter"]
            self.assertEqual(final["dir"], str(run_dir / "epoch-03"))
            self.assertEqual(final["manifest_sha256"], report["checkpoints"][-1]["manifest_sha256"])
            self.assertFalse((run_dir / "epoch-03" / "adapter_model.safetensors").samefile(run_dir / "epoch-01" / "adapter_model.safetensors"))

    def _scratch_leaf(self, root: Path, extra: dict[str, bytes] | None = None) -> Path:
        leaf = root / "epoch-01"
        leaf.mkdir(parents=True)
        files = {
            "adapter_config.json": b'{"r": 16}',
            "adapter_model.safetensors": b"adapter weights",
            "README.md": b"adapter",
            **(extra or {}),
        }
        for name, data in files.items():
            (leaf / name).write_bytes(data)
        return leaf

    def test_scratch_leaf_manifest_covers_every_file_and_passes_exporter_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = self._scratch_leaf(Path(tmp), extra={"tokenizer_config.json": b"tokenizer"})
            files, manifest_sha256 = train_adapter.write_leaf_manifest(leaf)
            self.assertEqual(
                sorted(files), ["README.md", "adapter_config.json", "adapter_model.safetensors", "tokenizer_config.json"]
            )
            self.assertRegex(manifest_sha256, r"^[0-9a-f]{64}$")
            self._assert_exporter_adapter_hashes(leaf)

    def test_scratch_leaf_fixtures_fail_the_exporter_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tampered = self._scratch_leaf(root / "tampered")
            train_adapter.write_leaf_manifest(tampered)
            (tampered / "adapter_model.safetensors").write_bytes(b"tampered")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(tampered)
            missing = self._scratch_leaf(root / "missing")
            train_adapter.write_leaf_manifest(missing)
            (missing / "adapter_config.json").unlink()
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(missing)
            unlisted = self._scratch_leaf(root / "unlisted")
            train_adapter.write_leaf_manifest(unlisted)
            (unlisted / "optimizer.pt").write_bytes(b"optimizer")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(unlisted)

    def test_manifest_writer_refuses_rewrites_reparse_and_case_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = self._scratch_leaf(root / "rewrite")
            train_adapter.write_leaf_manifest(leaf)
            with self.assertRaisesRegex(SystemExit, "already exists"):
                train_adapter.write_leaf_manifest(leaf)
            # A hand-written manifest with case-colliding keys must fail the gate.
            collision = self._scratch_leaf(root / "collision")
            (collision / "adapter-hashes.json").write_text(json.dumps(
                {"files": {
                    "adapter_config.json": "a" * 64,
                    "ADAPTER_CONFIG.json": "a" * 64,
                    "adapter_model.safetensors": "a" * 64,
                }}), encoding="utf-8")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(collision)
            linked = self._scratch_leaf(root / "linked")
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "stolen.safetensors").write_bytes(b"stolen")
            if not make_reparse_link(linked / "linked", elsewhere):
                self.skipTest("no reparse-point creation is permitted on this host")
            with self.assertRaisesRegex(SystemExit, "reparse point"):
                train_adapter.write_leaf_manifest(linked)

    def test_unsafe_leaf_relative_names_are_refused(self):
        for name in ("../escape.safetensors", "/absolute.safetensors", "C:/escape.safetensors", "dir\\escape.safetensors", "a/../b.safetensors", "trailing. /x"):
            with self.subTest(name=name):
                with self.assertRaises(SystemExit):
                    train_adapter.require_confined_relative_path(
                        name, Path("c:/safe"), "adapter leaf names must be confined")

    def test_run_output_directory_is_never_reused_or_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root)}):
                report_path = root / "reports" / "m2-run.json"
                run_dir = train_adapter.create_run_directory(report_path)
                self.assertEqual(run_dir, root / "m2-run")
                with self.assertRaisesRegex(SystemExit, "already exists"):
                    train_adapter.create_run_directory(report_path)
                (root / "m2-other").mkdir()
                with self.assertRaisesRegex(SystemExit, "already exists"):
                    train_adapter.create_run_directory(root / "reports" / "m2-other.json")

    def test_epoch_train_failure_leaves_evidence_but_no_export_ready_record(self):
        class FakeModel:
            def __init__(self):
                self.config = types.SimpleNamespace(_commit_hash=train_adapter.SMOKE_REVISION)

            def save_pretrained(self, directory):
                Path(directory, "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
                Path(directory, "adapter_model.safetensors").write_bytes(b"adapter weights")

        class FailingTrainer:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def train(self):
                callback = self.kwargs["callbacks"][0]
                state = types.SimpleNamespace(epoch=1.0, global_step=8)
                callback.on_epoch_end(None, state, None)
                raise RuntimeError("training exploded")

        fake_model = FakeModel()
        fake_tokenizer = mock.MagicMock()
        fake_tokenizer.apply_chat_template = mock.MagicMock(
            side_effect=lambda *args, **kwargs: [1, 2, 3, 4] if kwargs.get("add_generation_prompt") else [1, 2, 3, 4, 5, 6])
        fake_flm = mock.MagicMock()
        fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
        fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
        cuda = mock.MagicMock()
        cuda.max_memory_reserved = mock.MagicMock(return_value=8.0 * 2**30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "m2-fail.json"
            with mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}):
                with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                    with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                        with mock.patch.object(train_adapter, "Dataset", mock.MagicMock()):
                            with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                                with mock.patch.object(train_adapter, "SFTConfig", lambda **kwargs: types.SimpleNamespace(**kwargs)):
                                    with mock.patch.object(train_adapter, "SFTTrainer", FailingTrainer):
                                        with self.assertRaises(RuntimeError):
                                            train_adapter.train(
                                                [row()],
                                                train_adapter.SMOKE_BASE,
                                                train_adapter.SMOKE_REVISION,
                                                None,
                                                report_path,
                                                winner=False,
                                                epochs=3,
                                            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["completed_epochs"], 1)
            self.assertEqual(report["export_ready"], False)
            self.assertIsNone(report["final_adapter"])
            leaf = root / "models" / "m2-fail" / "epoch-01"
            self.assertTrue((leaf / "adapter_config.json").is_file())
            self.assertTrue((leaf / "adapter-hashes.json").is_file())

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
                                    [row() for _ in range(100)],
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
