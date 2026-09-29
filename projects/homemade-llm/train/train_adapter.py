# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 02:10 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
"""M2-T1 adapter trainer: response-only QLoRA on accepted DIRECTOR rows.

Modelled on ``smoke_train.py``. The 20-step smoke dry run and the D-M2-6 probe
on the M1 winner both go through this file. Input is ``validate_rows.py``'s
``accepted.jsonl``, which holds train and dev rows. Only train rows are
encoded. The loss labels are the response token ids; prompt positions stay
ignored, and an empty response or a prompt label stops the run before any
checkpoint is written. The PowerShell wrapper holds the GPU lock and the
storage guards; this process writes the report JSON.

Runs inside the pinned Unsloth image (see ``scripts/train-adapter.ps1``).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

os.environ["HF_HOME"] = os.environ.get("HF_HOME", "/workspace/hf")

import torch  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402
from datasets import Dataset  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from transformers import set_seed  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

import prompt_template  # noqa: E402

SMOKE_BASE = "unsloth/qwen3-0.6b-unsloth-bnb-4bit"
SMOKE_REVISION = "e86f5289a2ca40ab7114071959dc2079cf97cb3c"
WINNER_BASE = "Qwen/Qwen3-14B"
WINNER_REVISION = "40c069824f4251a91eefaf281ebe4c544efd3e18"
PROBE_STEPS = 20
WINNER_VRAM_GIB = 11.0
CARD_VRAM_GIB = 12.0
IGNORED_LABEL = -100
SEED = 7
SEQ = 2048
TARGET_FIELDS = ("acceptance_checks", "constraints", "evidence_requirements", "non_goals")


def fail(message: str) -> None:
    raise SystemExit(message)


def resolve_commit(config_commit: str | None, repo: str, revision: str) -> str:
    """The commit the base was actually loaded from. A pinned 40-hex revision must match."""
    commit = config_commit or Path(snapshot_download(repo, revision=revision, local_files_only=True)).name
    if not commit:
        fail(f"could not resolve the commit for {repo}@{revision}")
    if len(revision) == 40 and commit != revision:
        fail(f"{repo}: loaded commit {commit} does not match the pinned revision {revision}")
    return commit


def load_training_rows(path: Path) -> list[dict[str, Any]]:
    """Read the validator's ``accepted.jsonl`` and return only its ``train`` rows.

    ``validate_rows.py`` writes train and dev into that one file, plus ``report.json``
    beside it. A raw row file, a quarantine file, or a count that disagrees with the
    report never reaches the loss. Dev rows stay in the file and out of the mask.
    """
    if path.name != "accepted.jsonl":
        fail(f"trainer reads the validator's accepted.jsonl, not raw rows: {path}")
    if not path.is_file():
        fail(f"training rows not found: {path}")
    report_path = path.with_name("report.json")
    if not report_path.is_file():
        fail(f"validator report.json is missing beside {path.name}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        expected_train = int(report["rows_per_split"]["train"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        fail(f"validator report.json is not usable: {exc}")
    trainable: list[dict[str, Any]] = []
    seen = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        seen += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"line {line_number}: not JSON ({exc.msg})")
        if not isinstance(row, dict):
            fail(f"line {line_number}: row is not an object")
        _require_accepted_row(row, line_number)
        if row["split"] == "train":
            trainable.append(row)
    if seen != int(report.get("n_accepted", -1)):
        fail("accepted.jsonl row count does not match the validator report")
    if len(trainable) != expected_train:
        fail("train-row count does not match the validator report")
    if not trainable:
        fail(f"no train rows in {path}")
    return trainable


def _require_accepted_row(row: dict[str, Any], line_number: int) -> None:
    """A row the validator kept: clear leak checks, a real target, and train or dev."""
    split = row.get("split")
    if split not in ("train", "dev"):
        fail(f"line {line_number}: split {split!r} is not in the validator's accepted file")
    check = row.get("heldout_check")
    near = check.get("near_duplicate") if isinstance(check, dict) else None
    if (not isinstance(check, dict) or check.get("source_lineage") != "clear"
            or check.get("title_objective_match") is not False or check.get("overlap") != "none"
            or not isinstance(near, dict) or near.get("status") not in ("clear", "released")):
        fail(f"line {line_number}: held-out check is not an accepted clear or released row")
    target = row.get("target")
    if not isinstance(target, dict) or set(target) != set(TARGET_FIELDS):
        fail(f"line {line_number}: target must contain exactly the four scored fields")
    source = row.get("input")
    if not isinstance(source, dict):
        fail(f"line {line_number}: input is not an object")
    try:
        prompt_template.input_contract(source)
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"line {line_number}: {exc}")


def messages_for(row: dict[str, Any]) -> list[dict[str, str]]:
    """Training chat through the shared template, using the row's input block as written. Thinking stays off at the call site (D-M1-3)."""
    return prompt_template.render_messages(row["input"], row["target"])


def labels_for_token_ids(prompt_ids: list[int], full_ids: list[int]) -> list[int]:
    """Loss labels for one rendered chat. The response must be a non-empty suffix of the full token ids."""
    prompt = list(prompt_ids)
    full = list(full_ids)
    if full[:len(prompt)] != prompt:
        fail("rendered response is not a suffix of the prompt")
    response = full[len(prompt):]
    if not response:
        fail("response mask is empty; the loss would cover nothing")
    if any(token == IGNORED_LABEL for token in response):
        fail("response token uses the ignored label")
    labels = [IGNORED_LABEL] * len(prompt) + response
    require_response_only(labels, len(prompt))
    return labels


def require_response_only(labels: list[int], prompt_tokens: int) -> float:
    """Return assistant-token coverage. Fail closed if the mask is empty or covers the prompt."""
    if prompt_tokens < 0 or prompt_tokens > len(labels):
        fail("response mask is not aligned to the prompt")
    if any(label != IGNORED_LABEL for label in labels[:prompt_tokens]):
        fail("response mask covers prompt tokens")
    supervised = [label for label in labels[prompt_tokens:] if label != IGNORED_LABEL]
    if not supervised:
        fail("response mask is empty; the loss would cover nothing")
    return len(supervised) / len(labels)


def require_vram(peak_reserved_gib: float, *, winner: bool) -> None:
    """The card is 12 GiB. The winner probe also stops at the D-M1-F 11.0 GiB ceiling."""
    if peak_reserved_gib > CARD_VRAM_GIB:
        fail(f"peak reserved VRAM {peak_reserved_gib:.2f} GiB exceeds the 12 GiB card")
    if winner and peak_reserved_gib > WINNER_VRAM_GIB:
        fail(f"winner probe peak reserved VRAM {peak_reserved_gib:.2f} GiB exceeds {WINNER_VRAM_GIB:.1f} GiB")


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def encode_rows(rows: list[dict[str, Any]], tokenize) -> list[dict[str, list[int]]]:
    """tokenize(messages, add_generation_prompt) -> token ids. Dev rows are not in ``rows``."""
    encoded: list[dict[str, list[int]]] = []
    for row in rows:
        messages = messages_for(row)
        prompt = list(tokenize(messages[:-1], add_generation_prompt=True))
        full = list(tokenize(messages, add_generation_prompt=False))
        labels = labels_for_token_ids(prompt, full)
        encoded.append({
            "input_ids": full,
            "attention_mask": [1] * len(full),
            "labels": labels,
        })
    return encoded


def coverage_for_rows(rows: list[dict[str, Any]], tokenize) -> float:
    encoded = encode_rows(rows, tokenize)
    coverages = [
        require_response_only(example["labels"], example["labels"].count(IGNORED_LABEL))
        for example in encoded
    ]
    return sum(coverages) / len(coverages)


def build_report(
    *,
    run: str,
    base: str,
    revision: str,
    rows: int,
    steps: int,
    mask_coverage: float,
    peak_reserved_gib: float | None,
    loss: float | None,
    wall_seconds: float,
    winner: bool,
    resolved_commit: str,
) -> dict[str, Any]:
    return {
        "run": run,
        "base": base,
        "base_revision": revision,
        "base_resolved_commit": resolved_commit,
        "rows": rows,
        "steps": steps,
        "mask_coverage": round(mask_coverage, 6),
        "peak_reserved_vram_gib": None if peak_reserved_gib is None else round(peak_reserved_gib, 3),
        "loss": loss,
        "wall_seconds": round(wall_seconds, 1),
        "gguf_sha256": None,
        "winner_probe": winner,
        "thinking": "disabled",
    }


def train(rows: list[dict[str, Any]], base: str, revision: str, steps: int, report_path: Path, *, winner: bool) -> dict[str, Any]:
    """QLoRA for ``steps`` on ``base@revision``. Thinking stays off. The mask is checked before ``trainer.train``."""
    started = time.time()
    set_seed(SEED)
    torch.cuda.reset_peak_memory_stats()
    model, tokenizer = FastLanguageModel.from_pretrained(
        base, revision=revision, max_seq_length=SEQ, load_in_4bit=True, use_exact_model_name=True)
    resolved = resolve_commit(getattr(model.config, "_commit_hash", None), base, revision)
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
        use_gradient_checkpointing="unsloth",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        random_state=SEED,
    )

    def tokenize(messages: list[dict[str, str]], add_generation_prompt: bool) -> list[int]:
        return list(tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=add_generation_prompt, enable_thinking=False))

    encoded = encode_rows(rows, tokenize)
    mask_coverage = sum(
        require_response_only(example["labels"], example["labels"].count(IGNORED_LABEL))
        for example in encoded
    ) / len(encoded)

    def collate(features: list[dict[str, Any]]) -> dict[str, Any]:
        if not features or "input_ids" not in features[0] or "labels" not in features[0]:
            seen = sorted(features[0]) if features else []
            fail(f"trainer batch is missing the response mask: {seen}")

        def as_list(value: Any) -> list[int]:
            if hasattr(value, "tolist"):
                return [int(item) for item in value.tolist()]
            return [int(item) for item in value]

        input_ids = [as_list(feature["input_ids"]) for feature in features]
        labels = [as_list(feature["labels"]) for feature in features]
        if any(IGNORED_LABEL not in row for row in labels):
            fail("trainer batch lost the prompt mask")
        attention_mask = [
            as_list(feature["attention_mask"]) if "attention_mask" in feature else [1] * len(row_ids)
            for feature, row_ids in zip(features, input_ids)
        ]
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=Dataset.from_list(encoded),
        data_collator=collate,
        args=SFTConfig(
            output_dir=str(report_path.with_suffix("")) + "-trainer",
            max_length=SEQ,
            packing=False,
            dataset_kwargs={"skip_prepare_dataset": True},
            per_device_train_batch_size=1,
            gradient_accumulation_steps=1,
            max_steps=steps,
            learning_rate=2e-4,
            logging_steps=max(1, steps // 4),
            save_strategy="no",
            report_to="none",
            seed=SEED,
        ),
    )
    torch.cuda.reset_peak_memory_stats()
    result = trainer.train()
    peak = torch.cuda.max_memory_reserved() / 2**30
    report = build_report(
        run=report_path.stem, base=base, revision=revision, rows=len(rows), steps=steps,
        mask_coverage=mask_coverage, peak_reserved_gib=peak, loss=round(result.training_loss, 4),
        wall_seconds=time.time() - started, winner=winner, resolved_commit=resolved,
    )
    write_report(report_path, report)
    require_vram(peak, winner=winner)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train a DIRECTOR QLoRA adapter (M2-T1).")
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--base", default=os.environ.get("ADAPTER_BASE", SMOKE_BASE))
    parser.add_argument("--revision", default=os.environ.get("ADAPTER_REVISION", SMOKE_REVISION))
    parser.add_argument("--steps", type=int, default=int(os.environ.get("ADAPTER_STEPS", str(PROBE_STEPS))))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--winner", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Validate rows and exit. No GPU and no report.")
    args = parser.parse_args(argv)
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        fail(f"revision {args.revision!r} is not a full 40-hex commit")
    if args.steps < 1:
        fail("steps must be positive")
    if args.winner and (args.base != WINNER_BASE or args.revision != WINNER_REVISION):
        fail(f"winner probe is pinned to {WINNER_BASE}@{WINNER_REVISION}")
    rows = load_training_rows(args.rows)
    if args.check_only:
        for row in rows:
            messages_for(row)
        print(json.dumps({"train_rows": len(rows), "check_only": True}))
        return
    report = train(rows, args.base, args.revision, args.steps, args.report, winner=args.winner)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 02:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
