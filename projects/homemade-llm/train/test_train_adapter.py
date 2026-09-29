# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
"""Unit tests for the M2-T1 trainer's non-GPU contract.

    python projects/homemade-llm/train/test_train_adapter.py
"""
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
