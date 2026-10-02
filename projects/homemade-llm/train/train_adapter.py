# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | BEGIN]]
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
PROBE_WARMUP_MICROSTEPS = 10
PROBE_TIMED_MICROSTEPS = 40
PROBE_GRAD_ACCUM = 8
PROBE_EPOCHS = 3
PROBE_CONTINGENCY = 0.20
PROBE_TIME_LIMIT_S = 36_000
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


def percentile(values: list[int], p: float) -> float:
    """Linear-interpolation percentile on a non-empty list (``p`` is 0–100)."""
    if not values:
        fail("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def length_statistics(lengths: list[int]) -> dict[str, float | int]:
    """Token-length p50, p95 and max for the D-M1-F row-length gate."""
    return {
        "p50": round(percentile(lengths, 50.0), 3),
        "p95": round(percentile(lengths, 95.0), 3),
        "max": max(lengths),
    }


def timed_window_optimizer_updates(timed_microsteps: int, gradient_accumulation: int) -> int:
    """Optimizer steps inside a timed microstep window."""
    if timed_microsteps < 0 or gradient_accumulation < 1:
        fail("timed window counts must be non-negative with positive accumulation")
    return timed_microsteps // gradient_accumulation


def extrapolate_wall_seconds(rows_total: int, seconds_per_microstep: float, *, epochs: int = PROBE_EPOCHS) -> float:
    """Three-epoch wall estimate: one microstep per row per epoch."""
    if rows_total < 0 or seconds_per_microstep < 0 or epochs < 0:
        fail("extrapolation inputs must be non-negative")
    return rows_total * epochs * seconds_per_microstep


def wall_seconds_with_contingency(wall_seconds: float, fraction: float = PROBE_CONTINGENCY) -> float:
    if wall_seconds < 0 or fraction < 0:
        fail("contingency inputs must be non-negative")
    return wall_seconds * (1.0 + fraction)


def probe_memory_pass(peak_reserved_gib: float, *, limit_gib: float = WINNER_VRAM_GIB) -> bool:
    return peak_reserved_gib < limit_gib


def probe_time_pass(contingency_wall_seconds: float, *, limit_seconds: float = PROBE_TIME_LIMIT_S) -> bool:
    return contingency_wall_seconds < limit_seconds


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


def token_lengths(encoded: list[dict[str, list[int]]]) -> list[int]:
    return [len(example["input_ids"]) for example in encoded]


def prompt_token_count(labels: list[int]) -> int:
    for index, label in enumerate(labels):
        if label != IGNORED_LABEL:
            return index
    return len(labels)


def pad_example_to_seq_len(example: dict[str, list[int]], seq_len: int, pad_token_id: int) -> dict[str, list[int]]:
    """Right-pad or left-truncate one encoded row to exactly ``seq_len`` for the VRAM exercise."""
    input_ids = list(example["input_ids"])
    labels = list(example["labels"])
    attention = list(example.get("attention_mask", [1] * len(input_ids)))
    if len(input_ids) > seq_len:
        input_ids = input_ids[-seq_len:]
        labels = labels[-seq_len:]
        attention = attention[-seq_len:]
    if len(input_ids) < seq_len:
        pad_count = seq_len - len(input_ids)
        input_ids.extend([pad_token_id] * pad_count)
        labels.extend([IGNORED_LABEL] * pad_count)
        attention.extend([0] * pad_count)
    prompt_tokens = prompt_token_count(labels)
    require_response_only(labels, prompt_tokens)
    return {"input_ids": input_ids, "attention_mask": attention, "labels": labels}


def build_synthetic_row_from_context(row: dict[str, Any], seq_len: int, tokenize) -> dict[str, list[int]]:
    """Lengthen context text by repetition until tokenization reaches ``seq_len``."""
    context_items = row["input"].get("context")
    if not isinstance(context_items, list) or not context_items:
        fail("context repetition requires at least one context item")
    base_text = context_items[0].get("text")
    if not isinstance(base_text, str) or not base_text:
        fail("context repetition requires non-empty context text")
    repeated = dict(row)
    repeated_input = dict(row["input"])
    repeated_context = [dict(context_items[0])]
    text = base_text
    encoded = encode_rows([repeated], tokenize)[0]
    while len(encoded["input_ids"]) < seq_len:
        text = text + "\n" + base_text
        repeated_context[0] = dict(repeated_context[0], text=text)
        repeated_input["context"] = repeated_context
        repeated["input"] = repeated_input
        encoded = encode_rows([repeated], tokenize)[0]
        if len(text) > seq_len * 200:
            fail("context repetition did not reach the probe sequence length")
    return encoded


def prepare_probe_examples(
    rows: list[dict[str, Any]],
    encoded: list[dict[str, list[int]]],
    seq_len: int,
    tokenize,
    pad_token_id: int,
) -> tuple[list[dict[str, list[int]]], str]:
    """Ensure exercise rows reach ``seq_len``; report ``pad`` or ``context_repetition``."""
    if any(len(example["input_ids"]) < seq_len for example in encoded):
        try:
            return [pad_example_to_seq_len(example, seq_len, pad_token_id) for example in encoded], "pad"
        except SystemExit:
            synthetic = build_synthetic_row_from_context(rows[0], seq_len, tokenize)
            return [pad_example_to_seq_len(synthetic, seq_len, pad_token_id)], "context_repetition"
    return [pad_example_to_seq_len(example, seq_len, pad_token_id) for example in encoded], "pad"


def build_probe_report(
    *,
    run: str,
    base: str,
    revision: str,
    rows: int,
    mask_coverage: float,
    resolved_commit: str,
    length_stats: dict[str, float | int],
    sequence_length: int,
    sequence_length_method: str,
    warmup_microsteps: int,
    timed_microsteps: int,
    gradient_accumulation: int,
    timed_optimizer_updates: int,
    seconds_per_microstep: float,
    peak_reserved_gib: float,
    rows_total: int,
    wall_seconds_three_epochs: float,
    wall_seconds_three_epochs_contingency: float,
    memory_pass: bool,
    time_pass: bool,
) -> dict[str, Any]:
    return {
        "run": run,
        "mode": "d_m1_f_probe",
        "base": base,
        "base_revision": revision,
        "base_resolved_commit": resolved_commit,
        "rows": rows,
        "probe_rows_total": rows_total,
        "mask_coverage": round(mask_coverage, 6),
        "token_lengths": length_stats,
        "sequence_length": sequence_length,
        "sequence_length_method": sequence_length_method,
        "warmup_microsteps": warmup_microsteps,
        "timed_microsteps": timed_microsteps,
        "gradient_accumulation": gradient_accumulation,
        "timed_optimizer_updates": timed_optimizer_updates,
        "seconds_per_microstep": round(seconds_per_microstep, 6),
        "peak_reserved_vram_gib": round(peak_reserved_gib, 3),
        "wall_seconds_three_epochs": round(wall_seconds_three_epochs, 1),
        "wall_seconds_three_epochs_contingency": round(wall_seconds_three_epochs_contingency, 1),
        "memory_pass": memory_pass,
        "time_pass": time_pass,
        "gguf_sha256": None,
        "thinking": "disabled",
    }


def sft_arguments(output_dir: str, steps: int) -> dict[str, Any]:
    """Trainer settings. ``remove_unused_columns`` stays off so the attention mask is not dropped before collation."""
    return {
        "output_dir": output_dir,
        "max_length": SEQ,
        "packing": False,
        "dataset_kwargs": {"skip_prepare_dataset": True},
        "remove_unused_columns": False,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": 1,
        "max_steps": steps,
        "learning_rate": 2e-4,
        "logging_steps": max(1, steps // 4),
        "save_strategy": "no",
        "report_to": "none",
        "seed": SEED,
    }


def collate_features(features: list[dict[str, Any]]) -> dict[str, Any]:
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


def run_probe_microsteps(
    model: Any,
    examples: list[dict[str, list[int]]],
    *,
    warmup_microsteps: int,
    timed_microsteps: int,
    gradient_accumulation: int,
    learning_rate: float = 2e-4,
) -> tuple[float, float]:
    """Warm up, then time ``timed_microsteps`` microsteps at ``gradient_accumulation``."""
    device = next(model.parameters()).device
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    model.train()
    cursor = 0

    def synchronized_now() -> float:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        return time.time()

    def run_phase(count: int, *, timed: bool) -> float:
        nonlocal cursor
        pending = 0
        start = synchronized_now() if timed else 0.0
        for _ in range(count):
            example = examples[cursor % len(examples)]
            cursor += 1
            batch = collate_features([example])
            batch = {key: value.to(device) for key, value in batch.items()}
            loss = model(**batch).loss / gradient_accumulation
            loss.backward()
            pending += 1
            if pending == gradient_accumulation:
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
                pending = 0
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
        if pending:
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        if timed:
            return synchronized_now() - start
        return 0.0

    run_phase(warmup_microsteps, timed=False)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    elapsed = run_phase(timed_microsteps, timed=True)
    peak_gib = torch.cuda.max_memory_reserved() / 2**30 if torch.cuda.is_available() else 0.0
    seconds_per_microstep = elapsed / timed_microsteps if timed_microsteps else 0.0
    return seconds_per_microstep, peak_gib


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

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=Dataset.from_list(encoded),
        data_collator=collate_features,
        args=SFTConfig(**sft_arguments(str(report_path.with_suffix("")) + "-trainer", steps)),
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


def run_probe(
    rows: list[dict[str, Any]],
    base: str,
    revision: str,
    report_path: Path,
    *,
    probe_rows_total: int,
    probe_seq_len: int,
) -> dict[str, Any]:
    """D-M1-F feasibility probe: measure row lengths, VRAM and a three-epoch time estimate."""
    set_seed(SEED)
    model, tokenizer = FastLanguageModel.from_pretrained(
        base,
        revision=revision,
        max_seq_length=probe_seq_len,
        load_in_4bit=True,
        use_exact_model_name=True,
    )
    resolved = resolve_commit(getattr(model.config, "_commit_hash", None), base, revision)
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        random_state=SEED,
    )

    def tokenize(messages: list[dict[str, str]], add_generation_prompt: bool) -> list[int]:
        return list(
            tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=add_generation_prompt,
                enable_thinking=False,
            )
        )

    encoded = encode_rows(rows, tokenize)
    length_stats = length_statistics(token_lengths(encoded))
    mask_coverage = sum(
        require_response_only(example["labels"], prompt_token_count(example["labels"]))
        for example in encoded
    ) / len(encoded)
    pad_token_id = tokenizer.pad_token_id
    if pad_token_id is None:
        pad_token_id = tokenizer.eos_token_id
    if pad_token_id is None:
        fail("tokenizer has no pad or eos token for probe padding")
    exercise_rows, sequence_length_method = prepare_probe_examples(
        rows, encoded, probe_seq_len, tokenize, int(pad_token_id),
    )
    seconds_per_microstep, peak_gib = run_probe_microsteps(
        model,
        exercise_rows,
        warmup_microsteps=PROBE_WARMUP_MICROSTEPS,
        timed_microsteps=PROBE_TIMED_MICROSTEPS,
        gradient_accumulation=PROBE_GRAD_ACCUM,
    )
    timed_updates = timed_window_optimizer_updates(PROBE_TIMED_MICROSTEPS, PROBE_GRAD_ACCUM)
    wall_three_epochs = extrapolate_wall_seconds(probe_rows_total, seconds_per_microstep)
    wall_contingency = wall_seconds_with_contingency(wall_three_epochs)
    report = build_probe_report(
        run=report_path.stem,
        base=base,
        revision=revision,
        rows=len(rows),
        mask_coverage=mask_coverage,
        resolved_commit=resolved,
        length_stats=length_stats,
        sequence_length=probe_seq_len,
        sequence_length_method=sequence_length_method,
        warmup_microsteps=PROBE_WARMUP_MICROSTEPS,
        timed_microsteps=PROBE_TIMED_MICROSTEPS,
        gradient_accumulation=PROBE_GRAD_ACCUM,
        timed_optimizer_updates=timed_updates,
        seconds_per_microstep=seconds_per_microstep,
        peak_reserved_gib=peak_gib,
        rows_total=probe_rows_total,
        wall_seconds_three_epochs=wall_three_epochs,
        wall_seconds_three_epochs_contingency=wall_contingency,
        memory_pass=probe_memory_pass(peak_gib),
        time_pass=probe_time_pass(wall_contingency),
    )
    write_report(report_path, report)
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
    parser.add_argument("--probe", action="store_true", help="D-M1-F feasibility probe; writes metrics only.")
    parser.add_argument("--probe-rows-total", type=int, default=96)
    parser.add_argument("--probe-seq-len", type=int, default=SEQ)
    args = parser.parse_args(argv)
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        fail(f"revision {args.revision!r} is not a full 40-hex commit")
    if args.steps < 1:
        fail("steps must be positive")
    if args.probe_rows_total < 1:
        fail("probe-rows-total must be positive")
    if args.probe_seq_len < 1:
        fail("probe-seq-len must be positive")
    if args.probe and args.check_only:
        fail("--probe cannot be combined with --check-only")
    if args.probe and (args.base != WINNER_BASE or args.revision != WINNER_REVISION):
        fail(f"D-M1-F probe is pinned to {WINNER_BASE}@{WINNER_REVISION}")
    if args.winner and (args.base != WINNER_BASE or args.revision != WINNER_REVISION):
        fail(f"winner probe is pinned to {WINNER_BASE}@{WINNER_REVISION}")
    rows = load_training_rows(args.rows)
    if args.check_only:
        for row in rows:
            messages_for(row)
        print(json.dumps({"train_rows": len(rows), "check_only": True}))
        return
    if args.probe:
        report = run_probe(
            rows,
            args.base,
            args.revision,
            args.report,
            probe_rows_total=args.probe_rows_total,
            probe_seq_len=args.probe_seq_len,
        )
    else:
        report = train(rows, args.base, args.revision, args.steps, args.report, winner=args.winner)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 02:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
