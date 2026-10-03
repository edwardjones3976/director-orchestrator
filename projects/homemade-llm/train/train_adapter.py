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
import hashlib
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
FALLBACK_BASE = "Qwen/Qwen3-8B"
FALLBACK_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
# Approved root bytes, not a caller-supplied digest or a weight-list self-check.
TRUSTED_DIGEST_ROOT_SHA256 = "832406c6f6675c92b2d1072ffa674c1873a1693a88d53293d8e17942961423ee"
PROBE_STEPS = 20
WINNER_VRAM_GIB = 11.0
CARD_VRAM_GIB = 12.0
PROBE_WARMUP_MICROSTEPS = 10
PROBE_TIMED_MICROSTEPS = 40
TRAIN_GRAD_ACCUM = 8
PROBE_GRAD_ACCUM = TRAIN_GRAD_ACCUM
PROBE_EPOCHS = 3
PROBE_CONTINGENCY = 0.20
PROBE_TIME_LIMIT_S = 36_000
IGNORED_LABEL = -100
SEED = 7
SEQ = 2048
TARGET_FIELDS = ("acceptance_checks", "constraints", "evidence_requirements", "non_goals")


def fail(message: str) -> None:
    raise SystemExit(message)


def is_winner_pin(base: str, revision: str) -> bool:
    return base == WINNER_BASE and revision == WINNER_REVISION


def is_smoke_pin(base: str, revision: str) -> bool:
    return base == SMOKE_BASE and revision == SMOKE_REVISION


def is_fallback_pin(base: str, revision: str) -> bool:
    return base == FALLBACK_BASE and revision == FALLBACK_REVISION


def is_allowed_probe_pin(base: str, revision: str) -> bool:
    return (
        is_winner_pin(base, revision)
        or is_fallback_pin(base, revision)
        or is_smoke_pin(base, revision)
    )


def probe_pin_name(base: str, revision: str) -> str:
    if is_winner_pin(base, revision):
        return "winner"
    if is_fallback_pin(base, revision):
        return "fallback"
    if is_smoke_pin(base, revision):
        return "smoke"
    fail(f"probe pin is not recognized for {base}@{revision}")


def length_gate_pass(p95: float, limit: int = SEQ) -> bool:
    return p95 <= limit


def probe_is_authoritative(
    base: str,
    revision: str,
    probe_seq_len: int,
    loaded_row_count: int,
    projection_row_count: int,
) -> bool:
    return (
        (is_winner_pin(base, revision) or is_fallback_pin(base, revision))
        and probe_seq_len == SEQ
        and projection_row_count == loaded_row_count
    )


def _is_weight_manifest_path(rel: str) -> bool:
    return rel.endswith(".safetensors") or rel.endswith(".bin")


def _collect_snapshot_files(snapshot_dir: Path) -> list[str]:
    """Cover every cached input, including configs/tokenizers and future formats.

    Hugging Face cache file symlinks are read and hashed through their targets.
    Directory symlinks and non-regular entries cannot hide uncovered inputs.
    """
    found: list[str] = []
    for path in snapshot_dir.rglob("*"):
        if path.is_symlink() and path.is_dir():
            fail("snapshot directory symlinks are not supported")
        if path.is_file():
            found.append(path.relative_to(snapshot_dir).as_posix())
        elif not path.is_dir():
            fail("snapshot contains a non-regular or missing input")
    return sorted(found)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _index_weight_map_shards(snapshot_dir: Path) -> tuple[set[str], bool]:
    index_path = snapshot_dir / "model.safetensors.index.json"
    if not index_path.is_file():
        return set(), False
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"model.safetensors.index.json is not usable: {exc}")
    weight_map = payload.get("weight_map")
    if not isinstance(weight_map, dict):
        fail("model.safetensors.index.json has no weight_map object")
    return {str(shard) for shard in weight_map.values()}, True


def verify_expected_digests(snapshot_dir: Path, expected_files: list[dict], reads: int = 2) -> dict:
    """Authenticate every snapshot file before parsing any model-loading input.

    A weights-only approved list is insufficient when other snapshot files are
    present. Missing trusted metadata is a refusal, never a locally minted hash.
    """
    if reads < 1:
        fail("reads must be positive")
    seen_paths: set[str] = set()
    manifest_weight_paths: list[str] = []
    for entry in expected_files:
        rel = entry["path"]
        if (not isinstance(rel, str) or "\\" in rel or ":" in rel
                or any(part in ("", ".", "..") for part in rel.split("/"))):
            fail("digest manifest paths must be confined relative snapshot files")
        if rel in seen_paths:
            fail(f"duplicate path in digest manifest: {rel}")
        seen_paths.add(rel)
        if _is_weight_manifest_path(rel):
            manifest_weight_paths.append(rel)
    if not manifest_weight_paths:
        fail("digest manifest lists no weight file")
    on_disk_files = _collect_snapshot_files(snapshot_dir)
    for rel in on_disk_files:
        if rel not in seen_paths:
            if _is_weight_manifest_path(rel):
                fail(f"weight file not covered by the digest manifest: {rel}")
            fail(f"snapshot input lacks an approved digest: {rel}")
    total_bytes = 0
    for entry in expected_files:
        rel = entry["path"]
        expected_sha = entry["sha256"]
        expected_size = entry["size"]
        path = snapshot_dir / rel
        if not path.is_file():
            fail(f"missing expected file: {rel}")
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            fail(f"size mismatch for {rel}")
        for read_index in range(1, reads + 1):
            actual_sha = _sha256_file(path)
            if actual_sha != expected_sha:
                fail(f"digest mismatch for {rel} on read {read_index}")
        total_bytes += expected_size
    # The index is executable loading metadata too: do not parse it before its
    # own bytes (and every other cached file) have passed the approved hashes.
    index_shards, weight_map_checked = _index_weight_map_shards(snapshot_dir)
    manifest_weight_set = set(manifest_weight_paths)
    if weight_map_checked:
        for shard in sorted(index_shards):
            if shard not in manifest_weight_set:
                fail(f"digest manifest does not list index shard: {shard}")
            shard_path = snapshot_dir / shard
            if not shard_path.is_file():
                fail(f"index shard missing from snapshot: {shard}")
    return {
        "files": len(expected_files),
        "reads": reads,
        "bytes": total_bytes,
        "weight_files": len(manifest_weight_paths),
        "weight_map_checked": weight_map_checked,
        "snapshot_files": len(on_disk_files),
        "snapshot_coverage": "all_regular_files",
    }


def _expected_digest_files(data: bytes, base: str, revision: str) -> list[dict]:
    key = f"{base}@{revision}"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"expected digests file is not usable: {exc}")
    if not isinstance(payload, dict):
        fail("expected digests file must be a JSON object")
    entry = payload.get(key)
    if entry is None:
        fail(f"expected digests file has no entry for {key}")
    files = entry.get("files") if isinstance(entry, dict) else None
    if not isinstance(files, list) or not files:
        fail(f"expected digests entry for {key} has no files")
    return files


def load_expected_digests(path: Path, base: str, revision: str) -> list[dict]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        fail(f"expected digests file is not usable: {exc}")
    return _expected_digest_files(data, base, revision)


def load_authenticated_digests(
    root_path: Path | None, path: Path, base: str, revision: str,
) -> tuple[list[dict], dict[str, str]]:
    """Authenticate root -> exact model/revision -> list bytes before parsing the list."""
    if root_path is None:
        fail("--trusted-digest-root is required for pinned M2 models")
    try:
        root_bytes = root_path.read_bytes()
    except OSError as exc:
        fail(f"trusted digest root is not usable: {exc}")
    root_sha = hashlib.sha256(root_bytes).hexdigest()
    if root_sha != TRUSTED_DIGEST_ROOT_SHA256:
        fail("trusted digest root does not match the approved SHA-256")
    try:
        root = json.loads(root_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"trusted digest root is not usable: {exc}")
    models = root.get("models") if isinstance(root, dict) else None
    if not isinstance(models, list):
        fail("trusted digest root has no models list")
    matching = [item for item in models if isinstance(item, dict)
                and item.get("hf_repo") == base and item.get("hf_revision") == revision]
    if len(matching) != 1:
        fail(f"trusted digest root has no unique approved entry for {base}@{revision}")
    upstream_sha = matching[0].get("upstream_weight_digests_sha256")
    if (not isinstance(upstream_sha, str) or len(upstream_sha) != 64
            or any(char not in "0123456789abcdef" for char in upstream_sha)):
        fail("approved model entry has no valid upstream digest-list SHA-256")
    try:
        data = path.read_bytes()
    except OSError as exc:
        fail(f"expected digests file is not usable: {exc}")
    if hashlib.sha256(data).hexdigest() != upstream_sha:
        fail("upstream digest list does not match the approved root")
    return _expected_digest_files(data, base, revision), {
        "trusted_root_sha256": root_sha, "upstream_digest_list_sha256": upstream_sha,
        "base": base, "revision": revision,
    }


def local_snapshot_dir(base: str, revision: str) -> Path:
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=base, revision=revision, local_files_only=True))


def digest_verification_before_load(
    base: str,
    revision: str,
    expected_digests_path: Path | None,
    trusted_digest_root_path: Path | None = None,
) -> dict | None:
    if is_winner_pin(base, revision) and expected_digests_path is None:
        fail("--expected-digests is required for the winner pin")
    if is_fallback_pin(base, revision) and expected_digests_path is None:
        fail("--expected-digests is required for the fallback pin")
    requires_root = base in (WINNER_BASE, FALLBACK_BASE)
    if requires_root and expected_digests_path is None:
        fail("--expected-digests is required for pinned M2 models")
    if expected_digests_path is None:
        return None
    binding = {}
    if requires_root or trusted_digest_root_path is not None:
        expected_files, binding = load_authenticated_digests(
            trusted_digest_root_path, expected_digests_path, base, revision,
        )
    else:
        expected_files = load_expected_digests(expected_digests_path, base, revision)
    snapshot_dir = local_snapshot_dir(base, revision)
    return {**verify_expected_digests(snapshot_dir, expected_files), **binding}


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
    digest_verification: dict | None = None,
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
        "digest_verification": digest_verification,
        "thinking": "disabled",
    }


def token_lengths(encoded: list[dict[str, list[int]]]) -> list[int]:
    return [len(example["input_ids"]) for example in encoded]


def prompt_token_count(labels: list[int]) -> int:
    for index, label in enumerate(labels):
        if label != IGNORED_LABEL:
            return index
    return len(labels)


def extend_probe_example_to_seq_len(example: dict[str, list[int]], seq_len: int) -> dict[str, list[int]]:
    """Lengthen or truncate one probe row to ``seq_len`` with full attention on every position."""
    input_ids = list(example["input_ids"])
    labels = list(example["labels"])
    if len(input_ids) > seq_len:
        input_ids = input_ids[-seq_len:]
        labels = labels[-seq_len:]
    if len(input_ids) < seq_len:
        prompt_tokens = prompt_token_count(labels)
        prompt_ids = input_ids[:prompt_tokens]
        response_ids = input_ids[prompt_tokens:]
        response_labels = labels[prompt_tokens:]
        if not prompt_ids:
            fail("probe sequence extension requires prompt token ids to repeat")
        deficit = seq_len - len(input_ids)
        fill_ids = [prompt_ids[index % len(prompt_ids)] for index in range(deficit)]
        input_ids = prompt_ids + fill_ids + response_ids
        labels = [IGNORED_LABEL] * prompt_tokens + [IGNORED_LABEL] * deficit + response_labels
    prompt_tokens = prompt_token_count(labels)
    require_response_only(labels, prompt_tokens)
    attention = [1] * len(input_ids)
    if len(input_ids) != seq_len:
        fail("probe exercise row length does not match the target sequence length")
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
) -> tuple[list[dict[str, list[int]]], str]:
    """Ensure exercise rows reach ``seq_len`` with attended prompt-token fill or context repetition."""
    method = "attended_prompt_repeat"
    if any(len(example["input_ids"]) < seq_len for example in encoded):
        try:
            return [extend_probe_example_to_seq_len(example, seq_len) for example in encoded], method
        except SystemExit:
            synthetic = build_synthetic_row_from_context(rows[0], seq_len, tokenize)
            return [extend_probe_example_to_seq_len(synthetic, seq_len)], "context_repetition"
    return [extend_probe_example_to_seq_len(example, seq_len) for example in encoded], method


def build_probe_report(
    *,
    run: str,
    pin: str,
    mode: str,
    base: str,
    revision: str,
    rows: int,
    mask_coverage: float,
    resolved_commit: str,
    length_stats: dict[str, float | int],
    length_gate_passed: bool,
    sequence_length: int,
    sequence_length_method: str | None,
    warmup_microsteps: int,
    timed_microsteps: int,
    gradient_accumulation: int,
    timed_optimizer_updates: int | None,
    seconds_per_microstep: float | None,
    peak_reserved_gib: float | None,
    rows_total: int,
    wall_seconds_three_epochs: float | None,
    wall_seconds_three_epochs_contingency: float | None,
    memory_pass: bool | None,
    time_pass: bool | None,
    authoritative: bool,
    overlength_rows: int,
    max_tokens: int,
    digest_verification: dict | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "run": run,
        "pin": pin,
        "mode": mode,
        "base": base,
        "base_revision": revision,
        "base_resolved_commit": resolved_commit,
        "rows": rows,
        "probe_rows_total": rows_total,
        "mask_coverage": round(mask_coverage, 6),
        "token_lengths": length_stats,
        "length_gate_pass": length_gate_passed,
        "sequence_length": sequence_length,
        "warmup_microsteps": warmup_microsteps,
        "timed_microsteps": timed_microsteps,
        "gradient_accumulation": gradient_accumulation,
        "seconds_per_microstep": None if seconds_per_microstep is None else round(seconds_per_microstep, 6),
        "peak_reserved_vram_gib": None if peak_reserved_gib is None else round(peak_reserved_gib, 3),
        "wall_seconds_three_epochs": None if wall_seconds_three_epochs is None else round(wall_seconds_three_epochs, 1),
        "wall_seconds_three_epochs_contingency": None
        if wall_seconds_three_epochs_contingency is None
        else round(wall_seconds_three_epochs_contingency, 1),
        "memory_pass": memory_pass,
        "time_pass": time_pass,
        "authoritative": authoritative,
        "overlength_rows": overlength_rows,
        "max_tokens": max_tokens,
        "digest_verification": digest_verification,
        "gguf_sha256": None,
        "thinking": "disabled",
    }
    if sequence_length_method is not None:
        report["sequence_length_method"] = sequence_length_method
    if timed_optimizer_updates is not None:
        report["timed_optimizer_updates"] = timed_optimizer_updates
    return report


def sft_arguments(output_dir: str, steps: int) -> dict[str, Any]:
    """Trainer settings. ``remove_unused_columns`` stays off so the attention mask is not dropped before collation."""
    return {
        "output_dir": output_dir,
        "max_length": SEQ,
        "packing": False,
        "dataset_kwargs": {"skip_prepare_dataset": True},
        "remove_unused_columns": False,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": TRAIN_GRAD_ACCUM,
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
        return time.perf_counter()

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


def train(
    rows: list[dict[str, Any]],
    base: str,
    revision: str,
    steps: int,
    report_path: Path,
    *,
    winner: bool,
    expected_digests_path: Path | None = None,
    trusted_digest_root_path: Path | None = None,
) -> dict[str, Any]:
    """QLoRA for ``steps`` on ``base@revision``. Thinking stays off. The mask is checked before ``trainer.train``."""
    digest_verification = digest_verification_before_load(
        base, revision, expected_digests_path, trusted_digest_root_path,
    )
    started = time.time()
    set_seed(SEED)
    torch.cuda.reset_peak_memory_stats()
    load_kwargs: dict[str, Any] = {}
    if digest_verification is not None:
        load_kwargs["local_files_only"] = True
    model, tokenizer = FastLanguageModel.from_pretrained(
        base,
        revision=revision,
        max_seq_length=SEQ,
        load_in_4bit=True,
        use_exact_model_name=True,
        **load_kwargs,
    )
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
    lengths = token_lengths(encoded)
    overlength = [length for length in lengths if length > SEQ]
    if overlength:
        fail(
            f"{len(overlength)} training row(s) exceed SEQ={SEQ}; "
            f"maximum length {max(overlength)}"
        )
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
        digest_verification=digest_verification,
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
    probe_rows_total: int | None,
    probe_seq_len: int,
    expected_digests_path: Path | None = None,
    trusted_digest_root_path: Path | None = None,
) -> dict[str, Any]:
    """D-M1-F feasibility probe: measure row lengths, VRAM and a three-epoch time estimate."""
    digest_verification = digest_verification_before_load(
        base, revision, expected_digests_path, trusted_digest_root_path,
    )
    set_seed(SEED)
    load_kwargs: dict[str, Any] = {}
    if digest_verification is not None:
        load_kwargs["local_files_only"] = True
    model, tokenizer = FastLanguageModel.from_pretrained(
        base,
        revision=revision,
        max_seq_length=probe_seq_len,
        load_in_4bit=True,
        use_exact_model_name=True,
        **load_kwargs,
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
    raw_lengths = token_lengths(encoded)
    length_stats = length_statistics(raw_lengths)
    max_tokens = int(length_stats["max"])
    overlength_rows = sum(1 for length in raw_lengths if length > probe_seq_len)
    mask_coverage = sum(
        require_response_only(example["labels"], prompt_token_count(example["labels"]))
        for example in encoded
    ) / len(encoded)
    pin = probe_pin_name(base, revision)
    loaded_rows = len(rows)
    projection_rows = probe_rows_total if probe_rows_total is not None else loaded_rows
    authoritative = probe_is_authoritative(
        base, revision, probe_seq_len, loaded_rows, projection_rows,
    )
    if pin in ("winner", "fallback") and overlength_rows:
        authoritative = False
    if authoritative:
        overlength_rows = 0
    mode = "d_m1_f_probe" if authoritative else "diagnostic_probe"
    gate_passed = length_gate_pass(float(length_stats["p95"]))
    if pin in ("winner", "fallback") and not gate_passed:
        report = build_probe_report(
            run=report_path.stem,
            pin=pin,
            mode=mode,
            base=base,
            revision=revision,
            rows=loaded_rows,
            mask_coverage=mask_coverage,
            resolved_commit=resolved,
            length_stats=length_stats,
            length_gate_passed=False,
            sequence_length=probe_seq_len,
            sequence_length_method=None,
            warmup_microsteps=PROBE_WARMUP_MICROSTEPS,
            timed_microsteps=PROBE_TIMED_MICROSTEPS,
            gradient_accumulation=PROBE_GRAD_ACCUM,
            timed_optimizer_updates=None,
            seconds_per_microstep=None,
            peak_reserved_gib=None,
            rows_total=projection_rows,
            wall_seconds_three_epochs=None,
            wall_seconds_three_epochs_contingency=None,
            memory_pass=None,
            time_pass=None,
            authoritative=authoritative,
            overlength_rows=overlength_rows,
            max_tokens=max_tokens,
            digest_verification=digest_verification,
        )
        write_report(report_path, report)
        fail("row-length p95 exceeds 2048; D-M1-F length gate failed")
    exercise_rows, sequence_length_method = prepare_probe_examples(
        rows, encoded, probe_seq_len, tokenize,
    )
    seconds_per_microstep, peak_gib = run_probe_microsteps(
        model,
        exercise_rows,
        warmup_microsteps=PROBE_WARMUP_MICROSTEPS,
        timed_microsteps=PROBE_TIMED_MICROSTEPS,
        gradient_accumulation=PROBE_GRAD_ACCUM,
    )
    timed_updates = timed_window_optimizer_updates(PROBE_TIMED_MICROSTEPS, PROBE_GRAD_ACCUM)
    wall_three_epochs = extrapolate_wall_seconds(projection_rows, seconds_per_microstep)
    wall_contingency = wall_seconds_with_contingency(wall_three_epochs)
    report = build_probe_report(
        run=report_path.stem,
        pin=pin,
        mode=mode,
        base=base,
        revision=revision,
        rows=loaded_rows,
        mask_coverage=mask_coverage,
        resolved_commit=resolved,
        length_stats=length_stats,
        length_gate_passed=gate_passed,
        sequence_length=probe_seq_len,
        sequence_length_method=sequence_length_method,
        warmup_microsteps=PROBE_WARMUP_MICROSTEPS,
        timed_microsteps=PROBE_TIMED_MICROSTEPS,
        gradient_accumulation=PROBE_GRAD_ACCUM,
        timed_optimizer_updates=timed_updates,
        seconds_per_microstep=seconds_per_microstep,
        peak_reserved_gib=peak_gib,
        rows_total=projection_rows,
        wall_seconds_three_epochs=wall_three_epochs,
        wall_seconds_three_epochs_contingency=wall_contingency,
        memory_pass=probe_memory_pass(peak_gib),
        time_pass=probe_time_pass(wall_contingency),
        authoritative=authoritative,
        overlength_rows=overlength_rows,
        max_tokens=max_tokens,
        digest_verification=digest_verification,
    )
    write_report(report_path, report)
    if authoritative:
        if not report["memory_pass"]:
            fail("authoritative probe failed memory gate")
        if not report["time_pass"]:
            fail("authoritative probe failed time gate")
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
    parser.add_argument("--probe-rows-total", type=int, default=None)
    parser.add_argument("--probe-seq-len", type=int, default=SEQ)
    parser.add_argument("--expected-digests", type=Path, default=None)
    parser.add_argument("--trusted-digest-root", type=Path, default=None,
                        help="Approved root file; its SHA-256 is pinned in the trainer.")
    args = parser.parse_args(argv)
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        fail(f"revision {args.revision!r} is not a full 40-hex commit")
    if args.steps < 1:
        fail("steps must be positive")
    if args.probe_rows_total is not None and args.probe_rows_total < 1:
        fail("probe-rows-total must be positive")
    if args.probe_seq_len < 1:
        fail("probe-seq-len must be positive")
    if args.probe and args.probe_seq_len > SEQ:
        fail(f"probe-seq-len must not exceed {SEQ}")
    if args.probe and args.check_only:
        fail("--probe cannot be combined with --check-only")
    if args.probe and not is_allowed_probe_pin(args.base, args.revision):
        fail(
            f"D-M1-F probe is pinned to {WINNER_BASE}@{WINNER_REVISION}, "
            f"{FALLBACK_BASE}@{FALLBACK_REVISION}, or {SMOKE_BASE}@{SMOKE_REVISION}"
        )
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
            expected_digests_path=args.expected_digests,
            trusted_digest_root_path=args.trusted_digest_root,
        )
    else:
        report = train(
            rows,
            args.base,
            args.revision,
            args.steps,
            args.report,
            winner=args.winner,
            expected_digests_path=args.expected_digests,
            trusted_digest_root_path=args.trusted_digest_root,
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 02:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
