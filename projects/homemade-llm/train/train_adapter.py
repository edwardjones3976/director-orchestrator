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
import math
import os
import re
import time
from pathlib import Path
from typing import Any

os.environ["HF_HOME"] = os.environ.get("HF_HOME", "/workspace/hf")

import torch  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402
from datasets import Dataset  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from transformers import TrainerCallback, set_seed  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

import prompt_template  # noqa: E402

SMOKE_BASE = "unsloth/qwen3-0.6b-unsloth-bnb-4bit"
SMOKE_REVISION = "e86f5289a2ca40ab7114071959dc2079cf97cb3c"
WINNER_BASE = "Qwen/Qwen3-14B"
WINNER_REVISION = "40c069824f4251a91eefaf281ebe4c544efd3e18"
FALLBACK_BASE = "Qwen/Qwen3-8B"
FALLBACK_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
# Approved root bytes, not a caller-supplied digest or a weight-list self-check.
TRUSTED_DIGEST_ROOT_SHA256 = "d1a4a2d921c3f5197c2867dcdb50dbfb46d2189bbb88141ed56d3f3eb19d36af"
PROBE_STEPS = 20
WINNER_VRAM_GIB = 11.0
CARD_VRAM_GIB = 12.0
PROBE_WARMUP_MICROSTEPS = 10
PROBE_TIMED_MICROSTEPS = 40
TRAIN_GRAD_ACCUM = 8
PROBE_GRAD_ACCUM = TRAIN_GRAD_ACCUM
EPOCH_MODE_MIN = 1
EPOCH_MODE_MAX = 6
EPOCH_LEARNING_RATE = 1e-4
# Container-side mount of the host's models\m2; the run directory lives under it.
MODELS_MOUNT = "/workspace/models/m2"
ADAPTER_HASHES_NAME = "adapter-hashes.json"
LEAF_MANDATORY_FILES = ("adapter_config.json", "adapter_model.safetensors")
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
        if _is_directory_link(path):
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


def _index_weight_map_shards(
    snapshot_dir: Path, *, blob_roots: tuple[Path, ...] = (),
) -> tuple[set[str], bool]:
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
    shards = {str(shard) for shard in weight_map.values()}
    for shard in sorted(shards):
        # The index is attacker-controlled loading metadata; each entry must
        # stay confined to the snapshot before any membership check uses it.
        require_confined_relative_path(
            shard, snapshot_dir, "digest index paths must be confined relative snapshot files",
            blob_roots=blob_roots)
    return shards, True


def _is_directory_link(path: Path) -> bool:
    return ((path.is_symlink() and path.is_dir())
            or getattr(os.path, "isjunction", lambda _p: False)(path))


def _refuse_linked_ancestors(path: Path, message: str) -> None:
    # Inspect lexical ancestors before resolving: even an in-root directory
    # link can conceal a different loading tree. Include the snapshot root.
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(ancestor):
            fail(f"{message} (directory link escapes the expected directory): {ancestor}")


def authenticated_cache_blob_roots(snapshot: Path, base: str, revision: str) -> tuple[Path, ...]:
    """Grant file-link targets only for the authenticated repo/revision layout.

    Copied snapshots still work, but an arbitrary directory cannot grant access
    to a sibling blob store merely by naming it 'blobs'.
    """
    snapshot = Path(os.path.abspath(snapshot))
    repo = snapshot.parent.parent
    if (snapshot.name != revision or snapshot.parent.name != "snapshots"
            or repo.name != "models--" + base.replace("/", "--")):
        return ()
    _refuse_linked_ancestors(snapshot, "authenticated snapshot")
    return (repo / "blobs", repo.parent / "blobs")


def require_confined_relative_path(
    rel: str, directory: Path, message: str, *, blob_roots: tuple[Path, ...] = (),
) -> str:
    """Reject unsafe names and directory links; allow authenticated file blobs.

    A snapshot file may link to the bound repo's blobs or shared hub blobs,
    through at most four file links. Every hop checks directory ancestors before
    resolution; arbitrary external targets remain refused. Adapter callers do
    not supply blob roots and retain their original confinement.
    """
    if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
            or rel.startswith("/") or any(
                part in ("", ".", "..") or part.endswith((" ", "."))
                for part in rel.split("/")
            )):
        fail(f"{message}: {rel!r}")
    directory = Path(os.path.abspath(directory))
    current = directory / rel
    allowed = (directory, *blob_roots)
    for hop in range(5):
        _refuse_linked_ancestors(current.parent, message)
        if not any(current.is_relative_to(root) and current != root for root in allowed):
            fail(f"{message} (resolved path escapes the expected directory): {rel!r}")
        if _is_directory_link(current):
            fail(f"{message} (directory link escapes the expected directory): {rel!r}")
        if not current.is_symlink():
            break
        if hop == 4:
            fail(f"{message} (more than four file-link hops): {rel!r}")
        target = current.readlink()
        current = Path(os.path.abspath(target if target.is_absolute() else current.parent / target))
    return rel


def verify_expected_digests(
    snapshot_dir: Path, expected_files: list[dict], reads: int = 2,
    *, blob_roots: tuple[Path, ...] = (),
) -> dict:
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
        # Absolute, '..'-bearing and reparse-escaping names are refused before
        # the duplicate, index and digest checks below can be distracted by them.
        require_confined_relative_path(
            rel, snapshot_dir, "digest manifest paths must be confined relative snapshot files",
            blob_roots=blob_roots)
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
    index_shards, weight_map_checked = (
        _index_weight_map_shards(snapshot_dir, blob_roots=blob_roots) if blob_roots
        else _index_weight_map_shards(snapshot_dir)
    )
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
    blob_roots = authenticated_cache_blob_roots(snapshot_dir, base, revision) if binding else ()
    return {**verify_expected_digests(snapshot_dir, expected_files, blob_roots=blob_roots), **binding}


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

def load_dev_rows(path: Path) -> list[dict[str, Any]]:
    """Read accepted dev rows strictly; malformed evidence is never skipped."""
    if path.name != "accepted.jsonl" or not path.is_file():
        fail("trainer requires the validator's accepted.jsonl")
    report_path = path.with_name("report.json")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        expected_dev = report["rows_per_split"]["dev"]
        if type(expected_dev) is not int or expected_dev < 0:
            raise ValueError("invalid dev count")
        expected_total = report["n_accepted"]
        if type(expected_total) is not int or expected_total < 0:
            raise ValueError("invalid accepted count")
        dev_rows, seen = [], 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("accepted row is not an object")
            _require_accepted_row(row, line_number)
            seen += 1
            if row["split"] == "dev":
                _validate_dev_row(row, "dev")
                dev_rows.append(row)
        if seen != expected_total or len(dev_rows) != expected_dev:
            raise ValueError("dev rows do not match the validator report")
        return dev_rows
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        fail("validator dev evidence is not usable: " + type(exc).__name__)


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


def run_output_root() -> Path:
    return Path(os.environ.get("ADAPTER_MODELS_DIR", MODELS_MOUNT))


def create_run_directory(report_path: Path) -> Path:
    """A unique run-owned directory under the persistent models mount.

    No clobber: an existing directory (or a manifest left inside one) is a
    refusal. Evidence from a failed run is never overwritten or reused.
    """
    run_dir = run_output_root() / report_path.stem
    if run_dir.exists():
        fail(f"run output directory already exists: {run_dir}")
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError:
        fail(f"run output directory already exists: {run_dir}")
    return run_dir


def write_adapter_leaf(model: Any, leaf_dir: Path) -> Path:
    """Explicit adapter-only save into a clean ``epoch-NN`` leaf directory.

    Assumption to confirm on the real host: the pinned ``peft`` writes exactly
    ``adapter_config.json`` and ``adapter_model.safetensors`` (plus an optional
    ``README.md``) via ``save_pretrained``. The Trainer checkpoint beside it
    (optimizer/RNG/trainer state) is never copied into the leaf, so leaves are
    export inputs, not resume checkpoints.
    """
    leaf_dir.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(str(leaf_dir))
    return leaf_dir


def write_leaf_manifest(leaf_dir: Path) -> tuple[dict[str, str], str]:
    """Write ``adapter-hashes.json`` once the leaf's files are final.

    Mirrors the exporter's gate: every regular file in the leaf is listed
    (except the manifest itself), reparse points, unsafe or non-normalized
    names and case-insensitive duplicates are refused, and the two mandatory
    adapter files must be present. Returns the files map and the manifest's
    own SHA-256 for the report.
    """
    files: dict[str, str] = {}
    names: set[str] = set()
    for path in sorted(leaf_dir.rglob("*")):
        if path.name == ADAPTER_HASHES_NAME and path.parent == leaf_dir:
            fail("leaf manifest already exists; refusing to rewrite final files")
        if path.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(path):
            fail(f"leaf contains a reparse point: {path}")
        rel = path.relative_to(leaf_dir).as_posix()
        require_confined_relative_path(rel, leaf_dir, "adapter leaf")
        folded = rel.lower()
        if folded in names:
            fail(f"case-insensitive duplicate file in adapter leaf: {rel}")
        names.add(folded)
        if not path.is_file():
            fail(f"adapter leaf contains a non-regular entry: {rel}")
        files[rel] = _sha256_file(path)
    for mandatory in LEAF_MANDATORY_FILES:
        if mandatory not in files:
            fail(f"adapter leaf is missing the mandatory file: {mandatory}")
    manifest_path = leaf_dir / ADAPTER_HASHES_NAME
    payload = json.dumps({"files": dict(sorted(files.items()))}, indent=2) + "\n"
    manifest_path.write_text(payload, encoding="utf-8")
    return files, _sha256_file(manifest_path)


def leaf_report_entry(leaf_dir: Path, *, epoch: int, global_step: int) -> dict[str, Any]:
    files, manifest_sha256 = write_leaf_manifest(leaf_dir)
    return {
        "epoch": epoch,
        "global_step": global_step,
        "dir": str(leaf_dir),
        "manifest_sha256": manifest_sha256,
        "files": sorted(files),
    }


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
    warmup_discarded_microsteps: int | None = None,
    probe_executed: bool = True,
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
        "probe_executed": probe_executed,
        # The warm-up phase boundary discards its pending partial accumulation
        # window (zero_grad without an optimizer step) so warm-up microsteps
        # never contribute to an optimizer update at this accumulation.
        "warmup_pending_gradients": "discarded_at_boundary_without_step" if probe_executed else "not_run",
        "warmup_discarded_microsteps": warmup_discarded_microsteps,
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


def sft_arguments(output_dir: str, steps: int | None, *, epochs: int | None = None) -> dict[str, Any]:
    """Trainer settings. ``remove_unused_columns`` stays off so the attention mask is not dropped before collation."""
    arguments: dict[str, Any] = {
        "output_dir": output_dir,
        "max_length": SEQ,
        "packing": False,
        "dataset_kwargs": {"skip_prepare_dataset": True},
        "remove_unused_columns": False,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": TRAIN_GRAD_ACCUM,
        "report_to": "none",
        "seed": SEED,
    }
    if epochs is not None:
        # Epoch mode: cosine decay at 1e-4, one save per epoch, every epoch
        # kept. A positive max_steps overrides num_train_epochs in HF Trainer,
        # so it must stay -1 here.
        arguments.update({
            "num_train_epochs": epochs,
            "max_steps": -1,
            "learning_rate": EPOCH_LEARNING_RATE,
            "lr_scheduler_type": "cosine",
            "logging_steps": 1,
            "save_strategy": "epoch",
        })
        return arguments
    arguments.update({
        "max_steps": steps,
        "learning_rate": 2e-4,
        "logging_steps": max(1, steps // 4),
        "save_strategy": "no",
    })
    return arguments


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

    def run_phase(count: int, *, timed: bool, discard_pending: bool = False) -> float:
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
            if discard_pending:
                # The warm-up boundary drops the partial accumulation window
                # (zero_grad, no step) so no optimizer update ever mixes
                # warm-up and timed microsteps.
                optimizer.zero_grad(set_to_none=True)
            else:
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        if timed:
            return synchronized_now() - start
        return 0.0

    run_phase(warmup_microsteps, timed=False, discard_pending=True)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    elapsed = run_phase(timed_microsteps, timed=True)
    peak_gib = torch.cuda.max_memory_reserved() / 2**30 if torch.cuda.is_available() else 0.0
    seconds_per_microstep = elapsed / timed_microsteps if timed_microsteps else 0.0
    return seconds_per_microstep, peak_gib


def epoch_leaf_name(epoch: int) -> str:
    return f"epoch-{epoch:02d}"


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _atomic_state_write(run_dir: Path, payload: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    destination = run_dir / "best-state.json"
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _initial_failure_state(run_dir: Path, reason: str) -> None:
    sequence = 1
    existing = run_dir / "best-state.json"
    if existing.exists() or existing.is_symlink():
        if existing.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(existing) or not existing.is_file():
            raise ValueError("existing best state is not a regular run-local file")

        def unique_pairs(pairs: Any) -> dict[str, Any]:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key in existing best state")
                result[key] = value
            return result

        def reject_constant(value: str) -> Any:
            raise ValueError("non-finite constant in existing best state")

        payload = json.loads(existing.read_text(encoding="utf-8"),
                             object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        if (not isinstance(payload, dict) or payload.get("format") != "route-r-dev-state/v1"
                or not isinstance(payload.get("state"), str) or payload["state"] not in {"running", "stopped", "failed"}
                or type(payload.get("sequence")) is not int or payload["sequence"] < 1):
            raise ValueError("existing best state is malformed")
        # The callback constructor may already have recorded a more precise
        # failure before train() could assign its returned object. Retain those
        # exact run-owned bytes and the sequence instead of resetting to one.
        if payload["state"] == "failed":
            return
        sequence = payload["sequence"] + 1
    _atomic_state_write(run_dir, {
        "format": "route-r-dev-state/v1", "dev_roster_sha256": None,
        "expected_packets_sha256": None, "evaluations": [], "best_epoch": None,
        "best_primary": None, "best_per_field": {}, "epochs_without_improvement": 0,
        "stop_reason": reason, "state": "failed", "sequence": sequence,
    })


def _require_hex(value: Any, length: int, label: str) -> None:
    if not isinstance(value, str) or len(value) != length or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"invalid {label}")


def _validate_dev_row(row: Any, split: str) -> None:
    if not isinstance(row, dict) or row.get("split") != split:
        raise ValueError(f"invalid {split} row")
    for field in ("row_id", "source_group"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"invalid {split} {field}")
    target = row.get("target")
    if not isinstance(target, dict) or set(target) != set(TARGET_FIELDS):
        raise ValueError("target must contain exactly the four fields")
    for items in target.values():
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError("invalid target items")
    source = row.get("input")
    if not isinstance(source, dict):
        raise ValueError("invalid row input")
    prompt_template.input_contract(source)
    for key in ("objective", "repository", "title"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise ValueError("invalid input field")
    if "context" in source and prompt_template.context_shape_errors(source["context"]):
        raise ValueError("invalid cited context")


def prepare_dev_context(train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]],
                        identity: dict[str, str]) -> dict[str, Any]:
    import copy
    if not isinstance(train_rows, list) or not train_rows or not isinstance(dev_rows, list) or not dev_rows:
        raise ValueError("train and dev rows must be non-empty lists")
    train_ids, train_groups, dev_ids, packets = set(), set(), set(), {}
    for row in train_rows:
        _validate_dev_row(row, "train")
        if row["row_id"] in train_ids:
            raise ValueError("duplicate training row id")
        train_ids.add(row["row_id"])
        train_groups.add(row["source_group"])
    for row in dev_rows:
        _validate_dev_row(row, "dev")
        if row["row_id"] in dev_ids:
            raise ValueError("duplicate dev row id")
        if row["row_id"] in train_ids or row["source_group"] in train_groups:
            raise ValueError("train/dev row or source_group overlap")
        dev_ids.add(row["row_id"])
        packets.setdefault(row["source_group"], []).append(row["row_id"])
    roster = sorted(packets)
    if len(roster) < 20:
        raise ValueError("epoch mode requires at least 20 distinct dev groups")
    packets = {group: sorted(packets[group]) for group in roster}
    if not isinstance(identity, dict) or set(identity) != {"base_revision", "weights_manifest_sha256", "template_sha256"}:
        raise ValueError("invalid frozen dev identity")
    for key, length in (("base_revision", 40), ("weights_manifest_sha256", 64), ("template_sha256", 64)):
        _require_hex(identity[key], length, key)
    return copy.deepcopy({"dev_rows": dev_rows, "train_rows": train_rows, "train_ids": train_ids,
                          "roster": roster, "expected_packets": packets, "identity": identity})


class DevEvaluationCallback(TrainerCallback):
    STOP_REASONS = frozenset({"max_epochs", "requested_epochs_completed", "two_epochs_without_improvement",
                              "primary_drop_gt_5pp", "field_drop_gt_5pp"})

    def __init__(self, model: Any, run_dir: Path, checkpoints: list[dict[str, Any]],
                 frozen_dev_context: dict[str, Any], max_epochs: int,
                 predict_fn: Any = None, score_fn: Any = None,
                 requested_epochs: int | None = None, *,
                 use_gradient_checkpointing: str | bool) -> None:
        self.model, self.run_dir, self.checkpoints = model, run_dir, checkpoints
        self.use_gradient_checkpointing = use_gradient_checkpointing
        self.best_primary = None
        self.best_per_field = {}
        self.best_epoch_end_primary = None
        self.best_epoch = None
        self.epochs_without_improvement = 0
        self.evaluations = []
        self.stop_reason = None
        self.state = "running"
        self.sequence = 0
        self.midpoints_done, self.epoch_ends_done = set(), set()
        self.dev_roster_sha256 = self.expected_packets_sha256 = None
        try:
            if type(max_epochs) is not int or not EPOCH_MODE_MIN <= max_epochs <= EPOCH_MODE_MAX:
                raise ValueError("invalid maximum epochs")
            self.max_epochs = max_epochs
            self.requested_epochs = max_epochs if requested_epochs is None else requested_epochs
            if type(self.requested_epochs) is not int or not 1 <= self.requested_epochs <= max_epochs:
                raise ValueError("invalid requested epochs")
            context = prepare_dev_context(frozen_dev_context["train_rows"], frozen_dev_context["dev_rows"], frozen_dev_context["identity"])
            if (frozen_dev_context["roster"] != context["roster"]
                    or frozen_dev_context["expected_packets"] != context["expected_packets"]
                    or set(frozen_dev_context.get("train_ids", context["train_ids"])) != context["train_ids"]):
                raise ValueError("frozen context roster mismatch")
            self.dev_rows, self.frozen_roster = context["dev_rows"], context["roster"]
            self.expected_packets, self.identity = context["expected_packets"], context["identity"]
            self.dev_roster_sha256 = _canonical_sha(self.frozen_roster)
            self.expected_packets_sha256 = _canonical_sha(self.expected_packets)
            import sys
            eval_dir = str(Path(__file__).parent.parent / "eval")
            if eval_dir not in sys.path:
                sys.path.insert(0, eval_dir)
            import predict_dev
            import dev_selection
            template_bytes = predict_dev._default_template_source()
            if not isinstance(template_bytes, bytes) or hashlib.sha256(template_bytes).hexdigest() != ADMITTED_TEMPLATE_SHA256:
                raise ValueError("template digest mismatch")
            if self.identity["template_sha256"] != ADMITTED_TEMPLATE_SHA256:
                raise ValueError("frozen template identity mismatch")
            self.predict_fn = predict_dev.run_predictions if predict_fn is None else predict_fn
            self.score_fn = dev_selection.evaluate_primary_score if score_fn is None else score_fn
            self.primary_fraction_fn = dev_selection.primary_fraction
            self.exact_score_fn = dev_selection.exact_score
            self.select_checkpoint_fn = dev_selection.select_checkpoint
            if self.checkpoints:
                raise ValueError("callback requires a fresh checkpoint list")
            self._write_state()
        except BaseException as exc:
            self._mark_failed("initialization_" + type(exc).__name__)
            raise

    def _mark_failed(self, reason: str) -> None:
        self.state, self.stop_reason = "failed", reason
        self._write_state()

    def _fail_run(self, reason: str, exc: Exception | None = None) -> None:
        self._mark_failed(reason)
        if exc is not None:
            raise exc
        raise ValueError(reason)

    def _write_state(self, failure_reason: str | None = None) -> None:
        if failure_reason is not None:
            self.stop_reason = failure_reason
        def pair(value: Any) -> Any:
            return None if value is None else {"numerator": value.numerator, "denominator": value.denominator}
        next_sequence = self.sequence + 1
        _atomic_state_write(self.run_dir, {
            "format": "route-r-dev-state/v1", "dev_roster_sha256": self.dev_roster_sha256,
            "expected_packets_sha256": self.expected_packets_sha256, "evaluations": self.evaluations,
            "best_epoch": self.best_epoch, "best_primary": pair(self.best_primary),
            "best_epoch_end_primary": pair(self.best_epoch_end_primary),
            "best_per_field": {field: pair(value) for field, value in sorted(self.best_per_field.items())},
            "epochs_without_improvement": self.epochs_without_improvement, "stop_reason": self.stop_reason,
            "state": self.state, "sequence": next_sequence,
        })
        self.sequence = next_sequence

    def _evaluate(self, epoch: int, kind: str, manifest_sha256: str, control: Any, tokenizer: Any) -> None:
        from fractions import Fraction
        try:
            if tokenizer is None:
                raise ValueError("missing tokenizer")
            _require_hex(manifest_sha256, 64, "leaf manifest digest")
            identity = {**self.identity, "adapter_sha256": manifest_sha256}
            expected_binding = {
                "source_label": "checkpoint", "seed": 1234, "max_new_tokens": SEQ, "thinking": False,
                "roster": self.frozen_roster, "row_ids": sorted(row["row_id"] for row in self.dev_rows),
                "identity": identity, "expected_packets_sha256": self.expected_packets_sha256,
                "input_sha256": {row["row_id"]: _canonical_sha(row["input"]) for row in self.dev_rows},
            }
            # Pass isolated copies: an injected hook cannot mutate the frozen cohort.
            import copy
            try:
                FastLanguageModel.for_inference(self.model)
                preds = self.predict_fn(copy.deepcopy(self.dev_rows), self.model, tokenizer,
                    frozen_roster=list(self.frozen_roster), expected_packets=copy.deepcopy(self.expected_packets),
                    max_new_tokens=SEQ, source_label="checkpoint", identity=dict(identity))
            finally:
                FastLanguageModel.for_training(self.model, use_gradient_checkpointing=self.use_gradient_checkpointing)
            if (not isinstance(preds, dict) or preds.get("complete") is not True
                    or preds.get("binding") != expected_binding
                    or type(preds["binding"].get("seed")) is not int
                    or type(preds["binding"].get("max_new_tokens")) is not int
                    or preds["binding"].get("thinking") is not False
                    or preds.get("schema_invalid_row_ids") != []
                    or not isinstance(preds.get("predictions"), dict)
                    or set(preds["predictions"]) != set(expected_binding["row_ids"])):
                raise ValueError("prediction completeness or binding mismatch")
            for prediction in preds["predictions"].values():
                if (not isinstance(prediction, dict) or set(prediction) != set(TARGET_FIELDS)
                        or any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
                               for items in prediction.values())):
                    raise ValueError("malformed prediction")
            scores = self.score_fn(copy.deepcopy(self.dev_rows), copy.deepcopy(preds["predictions"]),
                frozen_roster=list(self.frozen_roster), schema_invalid_row_ids=[],
                expected_packets=copy.deepcopy(self.expected_packets))
            if (not isinstance(scores, dict) or "group_results" in scores
                    or scores.get("roster_bound") is not True or scores.get("diagnostic_only") is not False
                    or scores.get("incomplete") is not False
                    or type(scores.get("groups")) is not int or scores["groups"] != len(self.frozen_roster)
                    or scores.get("incomplete_groups") != []):
                raise ValueError("non-selectable primary result")
            primary_frac = self.primary_fraction_fn(scores)
            exact, summaries = scores.get("per_field_exact"), scores.get("per_field")
            if not isinstance(exact, dict) or set(exact) != set(TARGET_FIELDS) or not isinstance(summaries, dict) or set(summaries) != set(TARGET_FIELDS):
                raise ValueError("malformed per_field_exact")
            fields = {field: self.exact_score_fn(exact[field], summaries[field], field) for field in TARGET_FIELDS}
            self.evaluations.append({"epoch": epoch, "kind": kind,
                "primary": {"numerator": primary_frac.numerator, "denominator": primary_frac.denominator},
                "per_field": {field: {"numerator": value.numerator, "denominator": value.denominator} for field, value in fields.items()},
                "leaf_manifest_sha256": manifest_sha256})
            if self.best_primary is not None and self.best_primary - primary_frac > Fraction(1, 20):
                self.stop_reason = "primary_drop_gt_5pp"
            if any(self.best_per_field.get(field, value) - value > Fraction(1, 20) for field, value in fields.items()):
                self.stop_reason = self.stop_reason or "field_drop_gt_5pp"
            if kind == "epoch_end":
                if self.best_epoch_end_primary is None or primary_frac > self.best_epoch_end_primary:
                    self.best_epoch_end_primary, self.best_epoch = primary_frac, epoch
                    self.epochs_without_improvement = 0
                else:
                    self.epochs_without_improvement += 1
                if self.epochs_without_improvement >= 2:
                    self.stop_reason = self.stop_reason or "two_epochs_without_improvement"
                if epoch >= self.max_epochs:
                    self.stop_reason = self.stop_reason or "max_epochs"
                elif epoch >= self.requested_epochs:
                    self.stop_reason = self.stop_reason or "requested_epochs_completed"
            self.best_primary = primary_frac if self.best_primary is None else max(self.best_primary, primary_frac)
            for field, value in fields.items():
                self.best_per_field[field] = max(self.best_per_field.get(field, value), value)
            if self.stop_reason is not None:
                self.state = "stopped"
                control.should_training_stop = True
            self._write_state()
        except BaseException as exc:
            self._mark_failed("evaluation_" + type(exc).__name__)
            raise

    def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        if self.state != "running":
            return control
        try:
            progress = actual_trainer_epoch(state)
            if progress is None:
                raise ValueError("missing epoch progress")
            if 0.5 <= progress < 1 and 1 not in self.midpoints_done:
                leaf_dir = write_adapter_leaf(self.model, self.run_dir / "midpoint-epoch-01")
                _, digest = write_leaf_manifest(leaf_dir)
                self._evaluate(1, "midpoint", digest, control, kwargs.get("processing_class") or kwargs.get("tokenizer"))
                self.midpoints_done.add(1)
            return control
        except BaseException as exc:
            if self.state != "failed":
                self._mark_failed("midpoint_" + type(exc).__name__)
            raise

    def on_epoch_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        try:
            progress = actual_trainer_epoch(state)
            if progress is None or progress < 1 or not progress.is_integer():
                raise ValueError("unfinished epoch end")
            epoch = int(progress)
            if epoch in self.epoch_ends_done:
                return control
            if self.state != "running" or epoch != len(self.epoch_ends_done) + 1 or epoch > self.requested_epochs:
                raise ValueError("out of order epoch end")
            if 1 not in self.midpoints_done:
                raise ValueError("midpoint_missing")
            step = getattr(state, "global_step", None)
            if type(step) is not int or step < 0:
                raise ValueError("invalid global step")
            leaf_dir = write_adapter_leaf(self.model, self.run_dir / epoch_leaf_name(epoch))
            entry = leaf_report_entry(leaf_dir, epoch=epoch, global_step=step)
            _require_hex(entry.get("manifest_sha256"), 64, "leaf manifest digest")
            self.checkpoints.append(entry)
            self._evaluate(epoch, "epoch_end", entry["manifest_sha256"], control, kwargs.get("processing_class") or kwargs.get("tokenizer"))
            self.epoch_ends_done.add(epoch)
            return control
        except BaseException as exc:
            if self.state != "failed":
                self._mark_failed("midpoint_missing" if str(exc) == "midpoint_missing" else "epoch_end_" + type(exc).__name__)
            raise

    def on_train_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        if self.state == "failed":
            raise ValueError("callback failed")
        if self.state == "running":
            self._fail_run("training_ended_without_completed_epochs")
        return control

    def completed_selection(self) -> dict[str, str] | None:
        from fractions import Fraction
        if self.state != "stopped" or self.stop_reason not in self.STOP_REASONS:
            raise ValueError("callback is not a valid terminal stop")
        end_evals = [record for record in self.evaluations if record["kind"] == "epoch_end"]
        count = len(end_evals)
        if count == 0:
            return None
        expected_ids = list(range(1, count + 1))
        if ([entry["epoch"] for entry in self.checkpoints] != expected_ids
                or [record["epoch"] for record in end_evals] != expected_ids
                or self.epoch_ends_done != set(expected_ids)
                or len([record for record in self.evaluations if record["kind"] == "midpoint"]) != 1
                or self.midpoints_done != {1}):
            raise ValueError("incomplete cadence or checkpoint roster")
        if self.stop_reason in {"requested_epochs_completed", "max_epochs"} and count != self.requested_epochs:
            raise ValueError("natural completion epoch mismatch")
        candidates = []
        for entry, record in zip(self.checkpoints, end_evals):
            digest = entry.get("manifest_sha256")
            _require_hex(digest, 64, "completed leaf digest")
            if record["leaf_manifest_sha256"] != digest or entry["dir"] != str(self.run_dir / epoch_leaf_name(entry["epoch"])):
                raise ValueError("checkpoint/evaluation leaf mismatch")
            # Evidence is re-read, not re-written, before publishing selection.
            manifest_path = Path(entry["dir"]) / ADAPTER_HASHES_NAME
            require_confined_relative_path(ADAPTER_HASHES_NAME, Path(entry["dir"]), "completed leaf manifest")
            if (manifest_path.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(manifest_path)
                    or not manifest_path.is_file() or _sha256_file(manifest_path) != digest):
                raise ValueError("selected leaf manifest changed or missing")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(files, dict) or sorted(files) != entry.get("files"):
                raise ValueError("selected leaf inventory mismatch")
            actual_files = []
            for path in Path(entry["dir"]).rglob("*"):
                if path.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(path):
                    raise ValueError("completed leaf contains a non-regular or linked entry")
                relative = path.relative_to(Path(entry["dir"])).as_posix()
                require_confined_relative_path(relative, Path(entry["dir"]), "completed leaf")
                if not path.is_file():
                    raise ValueError("completed leaf contains a non-regular entry")
                if path != manifest_path:
                    actual_files.append(relative)
            if sorted(actual_files) != sorted(files) or any(name not in files for name in LEAF_MANDATORY_FILES):
                raise ValueError("completed leaf coverage mismatch")
            for relative, expected in files.items():
                require_confined_relative_path(relative, Path(entry["dir"]), "completed leaf")
                _require_hex(expected, 64, "completed leaf file digest")
                if _sha256_file(Path(entry["dir"]) / relative) != expected:
                    raise ValueError("completed leaf file changed")
            score = Fraction(record["primary"]["numerator"], record["primary"]["denominator"])
            candidates.append((entry["dir"], entry["epoch"], score))
        selected_dir = self.select_checkpoint_fn(candidates)
        entry = next(item for item in self.checkpoints if item["dir"] == selected_dir)
        return {"dir": entry["dir"], "manifest_sha256": entry["manifest_sha256"]}


def actual_trainer_epoch(state: Any) -> float | None:
    value = getattr(state, "epoch", None)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    progress = float(value)
    return progress if math.isfinite(progress) and progress >= 0 else None


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
    epochs: int | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    final_adapter: dict[str, Any] | None = None,
    export_ready: bool | None = None,
    output_dir: str | None = None,
    trainer_epoch: float | None = None,
    checkpoint_artifacts_ready: bool = False,
    dev_cadence: dict[str, Any] | None = None,
    dev_roster_sha256: str | None = None,
    expected_packets_sha256: str | None = None,
    selected_adapter: dict[str, Any] | None = None,
    dev_selection_status: str | None = None,
    operational_completion: bool | None = None,
    qualified: bool = False,
    state: str | None = None,
    selected_primary: dict[str, int] | None = None,
    completed_epoch_ids: list[int] | None = None,
    publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
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
    if epochs is not None:
        report["publication"] = publication
        report["requested_epochs"] = epochs
        completed_ids = [entry["epoch"] for entry in checkpoints or []] if completed_epoch_ids is None else completed_epoch_ids
        report["completed_epochs"] = len(completed_ids)
        report["completed_epoch_ids"] = completed_ids
        report["trainer_epoch"] = trainer_epoch
        report["checkpoints"] = checkpoints or []
        report["checkpoint_artifacts_ready"] = checkpoint_artifacts_ready
        report["artifacts_complete"] = checkpoint_artifacts_ready
        report["selected_adapter"] = selected_adapter
        report["final_adapter"] = None
        report["export_ready"] = False
        report["dev_selection_status"] = dev_selection_status or "pending_bound_dev_selection"
        report["production_protocol_status"] = "blocked_pending_dev_cadence_reconciliation"
        report["output_dir"] = output_dir

        if dev_cadence:
            report["dev_cadence"] = dev_cadence
        if dev_roster_sha256:
            report["dev_roster_sha256"] = dev_roster_sha256
        if expected_packets_sha256:
            report["expected_packets_sha256"] = expected_packets_sha256
        if operational_completion is not None:
            report["operational_completion"] = operational_completion
        report["qualified"] = False
        report["selected_primary"] = selected_primary
        if state:
            report["state"] = state

        mount_root = run_output_root()
        report["adapter_mount"] = {
            "container_root": str(mount_root),
            "host_root": os.environ.get("ADAPTER_MODELS_HOST_DIR"),
        }
        report["output_mount_relative_dir"] = Path(output_dir).relative_to(mount_root).as_posix() if output_dir else None
        report["checkpoints"] = [
            {**entry, "mount_relative_dir": Path(entry["dir"]).relative_to(mount_root).as_posix()}
            for entry in checkpoints or []
        ]
        report["last_epoch_adapter"] = report["checkpoints"][-1] if report["checkpoints"] else None
    return report


ADMITTED_TEMPLATE_SHA256 = "6ee4833fbce2b0f38f37b5cd51f5424cd154a78cbfc9d9ae3643359a709908ea"

# WO-R5 slice 4: authenticate the staged publication binding (rows, trainer,
# predictor, scorer, renderer) before any GPU, model or tokenizer resource is
# touched in epoch mode.
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HEX40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PUBLICATION_BATCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

PUBLICATION_BINDING_FORMAT = "adapter-publication-binding/v1"
PUBLICATION_RESOLUTION_FORMAT = "route-r-resolution/v1"
PUBLICATION_PROFILE_FORMAT = "route-r-publication-profile/v1"

# (dir, name) -> the candidate.* key that must agree with this file's digest, or
# None when the file carries no candidate identity of its own (the row files).
PUBLICATION_PAYLOAD_FILES: dict[tuple[str, str], str | None] = {
    ("rows", "accepted.jsonl"): None,
    ("rows", "report.json"): None,
    ("rows", "profile.json"): None,
    ("train", "train_adapter.py"): "trainer_sha256",
    ("train", "prompt_template.py"): "template_sha256",
    ("eval", "predict_dev.py"): "predictor_sha256",
    ("eval", "dev_selection.py"): "scorer_sha256",
}
PUBLICATION_CANDIDATE_KEYS = frozenset(
    {"commit", "trainer_sha256", "predictor_sha256", "scorer_sha256", "template_sha256"})
PUBLICATION_RESOLUTION_KEYS = frozenset({
    "format", "batch", "verified", "accepted_jsonl_sha256", "profile_sha256",
    "publication_sha256", "trust_root_sha256", "validation_report_sha256", "bridge_sha256",
})
PUBLICATION_PROFILE_KEYS = frozenset({
    "format", "batch", "built_by", "accepted_jsonl_sha256", "candidate_row_sha256",
    "bundle_manifest_sha256", "recount_sha256", "validation_report_sha256", "row_schema_sha256",
    "trust_root_sha256", "verified_pins_sha256", "source_groups_and_splits", "input_lineage_sha256",
    "split_table_sha256", "constructor_inputs_sha256", "constructor_manifest_sha256",
    "constructor_receipt_sha256", "constructor_admission_sha256", "constructor_recount_sha256",
})
PUBLICATION_PROFILE_SCALAR_SHA_FIELDS = (
    "accepted_jsonl_sha256", "bundle_manifest_sha256", "recount_sha256", "validation_report_sha256",
    "row_schema_sha256", "trust_root_sha256", "verified_pins_sha256", "split_table_sha256",
    "constructor_inputs_sha256", "constructor_manifest_sha256", "constructor_receipt_sha256",
    "constructor_admission_sha256", "constructor_recount_sha256",
)
PUBLICATION_REPORT_KEYS = frozenset(
    {"format", "batch", "rows_in", "rows_admitted", "schema_valid", "splits", "accepted_jsonl_sha256"})


def _publication_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant is not allowed: {token}")


def _load_strict_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_publication_object_pairs, parse_constant=_reject_json_constant)


def _require_hex_field(value: Any, pattern: "re.Pattern[str]", label: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail(f"publication binding {label} is invalid")


def _read_publication_rows(
    binding_path: Path, rows_dir: Path, code_dir: Path, eval_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Authenticate the staged publication binding before any model, tokenizer or callback is touched.

    Re-hashes every payload file named in ``files`` against the binding's own
    record, refuses any missing, duplicate or extra entry and refuses any
    unexpected entry in the three staged directories (while never touching
    anything outside them), cross-checks every declared identity (candidate
    hashes, resolution digests, the admitted renderer digest), and re-derives
    the per-source-group split and unique-group counts from the accepted rows,
    the authenticated profile and the validation report. Reads accepted.jsonl
    twice against its bound digest before parsing any rows, then returns the
    publication metadata and those authenticated rows without rereading files.
    Any defect calls ``fail`` (raising ``SystemExit``) before returning.
    """
    binding_path = Path(binding_path)
    if not binding_path.is_file():
        fail(f"publication binding not found: {binding_path}")
    try:
        binding = _load_strict_json(binding_path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        fail(f"publication binding is not usable: {exc}")
    if not isinstance(binding, dict) or set(binding) != {
            "format", "batch", "candidate", "resolution", "root_sha256", "files"}:
        fail("publication binding must contain exactly format, batch, candidate, resolution, root_sha256, files")
    if binding["format"] != PUBLICATION_BINDING_FORMAT:
        fail(f"publication binding format must be {PUBLICATION_BINDING_FORMAT!r}")
    batch = binding["batch"]
    if not isinstance(batch, str) or not _PUBLICATION_BATCH_PATTERN.fullmatch(batch):
        fail("publication binding batch is invalid")

    candidate = binding["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != PUBLICATION_CANDIDATE_KEYS:
        fail("publication binding candidate must contain exactly commit, trainer_sha256, "
             "predictor_sha256, scorer_sha256, template_sha256")
    _require_hex_field(candidate["commit"], _HEX40_PATTERN, "candidate.commit")
    for key in ("trainer_sha256", "predictor_sha256", "scorer_sha256", "template_sha256"):
        _require_hex_field(candidate[key], _HEX64_PATTERN, f"candidate.{key}")
    if candidate["template_sha256"] != ADMITTED_TEMPLATE_SHA256:
        fail("publication binding candidate template_sha256 is not the admitted renderer digest")

    resolution = binding["resolution"]
    if not isinstance(resolution, dict) or set(resolution) != PUBLICATION_RESOLUTION_KEYS:
        fail("publication binding resolution has an unexpected key set")
    if resolution["format"] != PUBLICATION_RESOLUTION_FORMAT:
        fail(f"publication binding resolution format must be {PUBLICATION_RESOLUTION_FORMAT!r}")
    if resolution["batch"] != batch:
        fail("publication binding resolution batch does not match the binding batch")
    if resolution["verified"] is not True:
        fail("publication binding resolution is not verified")
    for key in ("accepted_jsonl_sha256", "profile_sha256", "publication_sha256",
                "trust_root_sha256", "validation_report_sha256", "bridge_sha256"):
        _require_hex_field(resolution[key], _HEX64_PATTERN, f"resolution.{key}")

    root_sha256 = binding["root_sha256"]
    _require_hex_field(root_sha256, _HEX64_PATTERN, "root_sha256")
    if resolution["trust_root_sha256"] != root_sha256:
        fail("publication binding root_sha256 does not match the resolution's trust_root_sha256")

    files = binding["files"]
    if not isinstance(files, list):
        fail("publication binding files must be a list")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in files:
        if (not isinstance(entry, dict) or set(entry) != {"dir", "name", "sha256", "bytes"}
                or not isinstance(entry.get("dir"), str) or not isinstance(entry.get("name"), str)
                or (entry["dir"], entry["name"]) not in PUBLICATION_PAYLOAD_FILES
                or not isinstance(entry.get("sha256"), str) or not _HEX64_PATTERN.fullmatch(entry["sha256"])
                or type(entry.get("bytes")) is not int or entry["bytes"] < 0):
            fail(f"publication binding files entry is invalid: {entry!r}")
        key = (entry["dir"], entry["name"])
        if key in by_key:
            fail(f"publication binding files has a duplicate entry: {key[0]}/{key[1]}")
        by_key[key] = entry
    missing = set(PUBLICATION_PAYLOAD_FILES) - set(by_key)
    extra = set(by_key) - set(PUBLICATION_PAYLOAD_FILES)
    if missing or extra:
        fail("publication binding files must list exactly the seven payload files "
             f"(missing={sorted(missing)}, extra={sorted(extra)})")

    dir_paths = {"rows": Path(rows_dir), "train": Path(code_dir), "eval": Path(eval_dir)}
    payloads: dict[tuple[str, str], bytes] = {}
    for (directory, name), entry in by_key.items():
        path = dir_paths[directory] / name
        if not path.is_file():
            fail(f"publication binding payload file is missing: {directory}/{name}")
        # Authenticate the exact bytes consumed below. Accepted rows need two
        # independent reads; neither row JSON nor caller-provided objects are
        # consumed until both reads match the admitted digest.
        for _ in range(2 if (directory, name) == ("rows", "accepted.jsonl") else 1):
            try:
                data = path.read_bytes()
            except OSError as exc:
                fail(f"publication payload cannot be read: {directory}/{name}: {exc}")
            if len(data) != entry["bytes"]:
                fail(f"publication binding payload file has an unexpected byte count: {directory}/{name}")
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                fail(f"publication binding payload file does not match its recorded digest: {directory}/{name}")
        payloads[(directory, name)] = data

    for directory, path in dir_paths.items():
        allowed_names = {name for (d, name) in PUBLICATION_PAYLOAD_FILES if d == directory}
        unexpected = {child.name for child in path.iterdir()} - allowed_names
        if unexpected:
            fail(f"unexpected entries in the staged {directory} directory: {sorted(unexpected)}")

    for (directory, name), identity_key in PUBLICATION_PAYLOAD_FILES.items():
        if identity_key is not None and candidate[identity_key] != by_key[(directory, name)]["sha256"]:
            fail(f"publication binding candidate.{identity_key} does not match the staged {directory}/{name}")

    for resolution_key, (directory, name) in (
        ("accepted_jsonl_sha256", ("rows", "accepted.jsonl")),
        ("validation_report_sha256", ("rows", "report.json")),
        ("profile_sha256", ("rows", "profile.json")),
    ):
        if resolution[resolution_key] != by_key[(directory, name)]["sha256"]:
            fail(f"publication binding resolution.{resolution_key} does not match the staged {directory}/{name}")

    try:
        profile = _load_strict_json(payloads[("rows", "profile.json")].decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"profile.json is not usable: {exc}")
    if not isinstance(profile, dict) or set(profile) != PUBLICATION_PROFILE_KEYS:
        fail("profile.json has an unexpected key set")
    if profile["format"] != PUBLICATION_PROFILE_FORMAT:
        fail(f"profile.json format must be {PUBLICATION_PROFILE_FORMAT!r}")
    if profile["batch"] != batch:
        fail("profile.json batch does not match the publication binding")
    if not isinstance(profile["built_by"], str) or not profile["built_by"].strip():
        fail("profile.json built_by is invalid")
    for field in PUBLICATION_PROFILE_SCALAR_SHA_FIELDS:
        _require_hex_field(profile[field], _HEX64_PATTERN, f"profile.{field}")
    if profile["accepted_jsonl_sha256"] != resolution["accepted_jsonl_sha256"]:
        fail("profile.json accepted_jsonl_sha256 does not match the resolution")
    if profile["validation_report_sha256"] != resolution["validation_report_sha256"]:
        fail("profile.json validation_report_sha256 does not match the resolution")
    if profile["trust_root_sha256"] != resolution["trust_root_sha256"]:
        fail("profile.json trust_root_sha256 does not match the resolution")

    groups_and_splits = profile["source_groups_and_splits"]
    if (not isinstance(groups_and_splits, dict) or not groups_and_splits
            or any(not isinstance(group, str) or not group.strip() or split not in ("train", "dev")
                   for group, split in groups_and_splits.items())):
        fail("profile.json source_groups_and_splits must map each source group to 'train' or 'dev'")

    lineage = profile["input_lineage_sha256"]
    if (not isinstance(lineage, dict) or set(lineage) != set(groups_and_splits)
            or any(not isinstance(v, str) or not _HEX64_PATTERN.fullmatch(v) for v in lineage.values())):
        fail("profile.json input_lineage_sha256 must be an object keyed exactly like source_groups_and_splits")

    row_digests = profile["candidate_row_sha256"]
    if (not isinstance(row_digests, list)
            or not row_digests
            or any(not isinstance(d, str) or not _HEX64_PATTERN.fullmatch(d) for d in row_digests)
            or len(set(row_digests)) != len(row_digests)):
        fail("profile.json candidate_row_sha256 must be a non-empty, duplicate-free ordered list of row digests")

    try:
        validation_report = _load_strict_json(payloads[("rows", "report.json")].decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"report.json is not usable: {exc}")
    if not isinstance(validation_report, dict) or set(validation_report) != PUBLICATION_REPORT_KEYS:
        fail("report.json has an unexpected key set")
    if not isinstance(validation_report["format"], str) or not validation_report["format"].strip():
        fail("report.json format is invalid")
    if validation_report["batch"] != batch:
        fail("report.json batch does not match the publication binding")
    for key in ("rows_in", "rows_admitted", "schema_valid"):
        if type(validation_report[key]) is not int or validation_report[key] < 0:
            fail(f"report.json {key} is invalid")
    splits = validation_report["splits"]
    if (not isinstance(splits, dict) or set(splits) != {"train", "dev"}
            or any(type(v) is not int or v < 0 for v in splits.values())):
        fail("report.json splits must contain exactly non-negative train and dev counts")
    _require_hex_field(validation_report["accepted_jsonl_sha256"], _HEX64_PATTERN, "report.accepted_jsonl_sha256")
    if validation_report["accepted_jsonl_sha256"] != resolution["accepted_jsonl_sha256"]:
        fail("report.json accepted_jsonl_sha256 does not match the resolution")

    try:
        lines = [line for line in payloads[("rows", "accepted.jsonl")].decode("utf-8").splitlines() if line.strip()]
    except UnicodeError as exc:
        fail(f"accepted.jsonl is not UTF-8: {exc}")
    if len(lines) != len(row_digests):
        fail("accepted.jsonl row count does not match the profile's candidate_row_sha256")
    parsed_rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            parsed = _load_strict_json(line)
        except (ValueError, json.JSONDecodeError) as exc:
            fail(f"accepted.jsonl line {line_number} is not usable: {exc}")
        if not isinstance(parsed, dict):
            fail(f"accepted.jsonl line {line_number} is not an object")
        group, split = parsed.get("source_group"), parsed.get("split")
        if not isinstance(group, str) or not group.strip() or split not in ("train", "dev"):
            fail(f"accepted.jsonl line {line_number} has an invalid source_group or split")
        parsed_rows.append(parsed)

    if len(parsed_rows) != validation_report["rows_admitted"] or len(parsed_rows) != validation_report["schema_valid"]:
        fail("accepted row count does not match report.json rows_admitted and schema_valid")

    groups_by_split: dict[str, set[str]] = {"train": set(), "dev": set()}
    for parsed in parsed_rows:
        group, split = parsed["source_group"], parsed["split"]
        expected_split = groups_and_splits.get(group)
        if expected_split is None:
            fail(f"accepted row source_group {group!r} is not in the profile's source_groups_and_splits")
        if expected_split != split:
            fail(f"accepted row source_group {group!r} split {split!r} does not match the profile's {expected_split!r}")
        groups_by_split[split].add(group)

    if set(groups_and_splits) - (groups_by_split["train"] | groups_by_split["dev"]):
        fail("not every profile source_group appears among the accepted rows")
    for split in ("train", "dev"):
        if len(groups_by_split[split]) != splits[split]:
            fail(f"unique source_group count for split {split!r} does not match report.json splits")

    return {"batch": batch, "profile_sha256": resolution["profile_sha256"], "root_sha256": root_sha256}, parsed_rows


def verify_publication_binding(
    binding_path: Path, rows_dir: Path, code_dir: Path, eval_dir: Path,
) -> dict[str, Any]:
    """Verify the publication without exposing row content in its receipt."""
    publication, _ = _read_publication_rows(binding_path, rows_dir, code_dir, eval_dir)
    return publication


def load_epoch_rows(
    binding_path: Path, rows_dir: Path, code_dir: Path, eval_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Consume the admitted producer schema; legacy loaders remain unchanged."""
    publication, accepted = _read_publication_rows(binding_path, rows_dir, code_dir, eval_dir)
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "dev": []}
    for line_number, row in enumerate(accepted, 1):
        _require_accepted_row(row, line_number)
        _validate_dev_row(row, row["split"])
        splits[row["split"]].append(row)
    return publication, splits["train"], splits["dev"]


def train(
    rows: list[dict[str, Any]] | None,
    dev_rows: list[dict[str, Any]] | None,
    base: str,
    revision: str,
    steps: int | None,
    report_path: Path,
    *,
    winner: bool,
    expected_digests_path: Path | None = None,
    trusted_digest_root_path: Path | None = None,
    epochs: int | None = None,
    use_gradient_checkpointing: str | bool = "unsloth",
    publication_binding_path: Path | None = None,
    publication_rows_dir: Path | None = None,
    publication_code_dir: Path | None = None,
    publication_eval_dir: Path | None = None,
) -> dict[str, Any]:
    """QLoRA on admitted rows, with synchronous bound dev gates in epoch mode."""
    started = time.time()
    supplied_rows, supplied_dev_rows = rows, dev_rows
    if epochs is not None:
        rows, dev_rows = [], []
    run_dir = create_run_directory(report_path) if epochs is not None else None
    checkpoints: list[dict[str, Any]] = []
    digest_verification, callback, trainer, peak, result = None, None, None, None, None
    memory_tracking_initialized = False
    resolved, mask_coverage = revision, 0.0
    publication: dict[str, Any] | None = None

    def retained_report(*, state: str, selected: Any = None, completed: bool = False) -> dict[str, Any]:
        cadence = None if callback is None else {
            "evaluations": callback.evaluations, "stop_reason": callback.stop_reason,
            "best_epoch": callback.best_epoch,
            "best_primary": None if callback.best_primary is None else {
                "numerator": callback.best_primary.numerator, "denominator": callback.best_primary.denominator},
            "sequence": callback.sequence,
            "best_epoch_end_primary": None if callback.best_epoch_end_primary is None else {
                "numerator": callback.best_epoch_end_primary.numerator, "denominator": callback.best_epoch_end_primary.denominator},
        }
        report = build_report(
            run=report_path.stem, base=base, revision=revision, rows=len(rows), steps=steps,
            mask_coverage=mask_coverage, peak_reserved_gib=peak,
            loss=None if state == "failed" or result is None else round(result.training_loss, 4),
            wall_seconds=time.time() - started, winner=winner, resolved_commit=resolved,
            digest_verification=digest_verification, epochs=epochs, checkpoints=checkpoints,
            publication=publication,
            final_adapter=None, export_ready=False, output_dir=str(run_dir) if run_dir is not None else None,
            trainer_epoch=actual_trainer_epoch(getattr(trainer, "state", None)),
            checkpoint_artifacts_ready=completed, dev_cadence=cadence,
            dev_roster_sha256=None if callback is None else callback.dev_roster_sha256,
            expected_packets_sha256=None if callback is None else callback.expected_packets_sha256,
            selected_adapter=selected, dev_selection_status="selected_on_frozen_dev" if selected else "not_selected",
            operational_completion=completed, qualified=False, state=state,
            selected_primary=None if selected is None else {
                "numerator": callback.best_epoch_end_primary.numerator, "denominator": callback.best_epoch_end_primary.denominator},
            completed_epoch_ids=None if callback is None else sorted(callback.epoch_ends_done),
        )
        write_report(report_path, report)
        return report

    try:
        if epochs is not None:
            if publication_binding_path is None:
                raise ValueError("epoch mode requires a publication binding")
            if publication_rows_dir is None or publication_code_dir is None or publication_eval_dir is None:
                raise ValueError("epoch mode requires the publication rows, code and eval directories")
            # Authenticate the staged publication binding before anything else in
            # epoch mode: no model, tokenizer, GPU or callback resource is touched
            # above this line.
            publication, rows, dev_rows = load_epoch_rows(
                publication_binding_path, publication_rows_dir, publication_code_dir, publication_eval_dir)
            # Direct callers cannot substitute rows after authenticating a
            # different publication. Train only the decoded admitted bytes.
            if ((supplied_rows is not None and supplied_rows != rows)
                    or (supplied_dev_rows is not None and supplied_dev_rows != dev_rows)):
                raise ValueError("epoch caller rows differ from the admitted publication")
            if type(epochs) is not int or not 1 <= epochs <= EPOCH_MODE_MAX:
                raise ValueError("invalid requested epochs")
            if not is_winner_pin(base, revision):
                raise ValueError("epoch mode requires the explicit winner pin")
            # Validate the cohort before any model or GPU operation. The approved
            # digest verification below reads local files, never loads a model.
            for row in rows:
                _validate_dev_row(row, "train")
            for row in dev_rows:
                _validate_dev_row(row, "dev")
            # Batch size is fixed at one. With <= 8 rows the first optimizer
            # update is already epoch end, so no interior midpoint can exist.
            if len(rows) <= TRAIN_GRAD_ACCUM:
                raise ValueError("epoch mode needs more than 8 training rows for an interior first-epoch update")
        digest_verification = digest_verification_before_load(
            base, revision, expected_digests_path, trusted_digest_root_path)
        callbacks = []
        if epochs is not None:
            if not isinstance(digest_verification, dict):
                raise ValueError("epoch mode needs authenticated digest verification")
            context = prepare_dev_context(rows, dev_rows, {
                "base_revision": revision,
                "weights_manifest_sha256": digest_verification.get("upstream_digest_list_sha256"),
                "template_sha256": ADMITTED_TEMPLATE_SHA256,
            })
            callback = DevEvaluationCallback(None, run_dir, checkpoints, context,
                max_epochs=EPOCH_MODE_MAX, requested_epochs=epochs,
                use_gradient_checkpointing=use_gradient_checkpointing)
            callbacks.append(callback)
        set_seed(SEED)
        torch.cuda.reset_peak_memory_stats()
        memory_tracking_initialized = True
        load_kwargs: dict[str, Any] = {}
        if digest_verification is not None:
            load_kwargs["local_files_only"] = True
        model, tokenizer = FastLanguageModel.from_pretrained(
            base, revision=revision, max_seq_length=SEQ, load_in_4bit=True,
            use_exact_model_name=True, **load_kwargs)
        resolved = resolve_commit(getattr(model.config, "_commit_hash", None), base, revision)
        if callback is not None and callback.identity["base_revision"] != resolved:
            raise ValueError("loaded model differs from frozen dev identity")
        model = FastLanguageModel.get_peft_model(
            model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
            use_gradient_checkpointing=use_gradient_checkpointing,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            random_state=SEED)
        if callback is not None:
            callback.model = model

        def tokenize(messages: list[dict[str, str]], add_generation_prompt: bool) -> list[int]:
            return list(tokenizer.apply_chat_template(messages, tokenize=True,
                add_generation_prompt=add_generation_prompt, enable_thinking=False))

        encoded = encode_rows(rows, tokenize)
        lengths = token_lengths(encoded)
        overlength = [length for length in lengths if length > SEQ]
        if overlength:
            fail(f"{len(overlength)} training row(s) exceed SEQ={SEQ}; maximum length {max(overlength)}")
        mask_coverage = sum(require_response_only(example["labels"], example["labels"].count(IGNORED_LABEL))
                            for example in encoded) / len(encoded)
        trainer_output = str(run_dir / "trainer") if run_dir is not None else str(report_path.with_suffix("")) + "-trainer"
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=Dataset.from_list(encoded),
            data_collator=collate_features, args=SFTConfig(**sft_arguments(trainer_output, steps, epochs=epochs)),
            callbacks=callbacks)
        torch.cuda.reset_peak_memory_stats()
        result = trainer.train()
        peak = torch.cuda.max_memory_reserved() / 2**30
        if epochs is not None:
            require_vram(peak, winner=winner)
            # Refuse absent callback termination; do not synthesize completion
            # from a Trainer result or from files alone.
            selected = callback.completed_selection()
            if selected is None:
                retained_report(state="stopped", completed=False)
                fail("no completed selectable epoch-end adapter")
            progress = actual_trainer_epoch(getattr(trainer, "state", None))
            if progress is None or not progress.is_integer() or int(progress) != len(callback.epoch_ends_done):
                raise ValueError("terminal trainer progress differs from completed cadence")
            return retained_report(state="stopped", selected=selected, completed=True)
        report = retained_report(state="stopped")
        require_vram(peak, winner=winner)
        return report
    except BaseException as exc:
        if epochs is not None:
            if memory_tracking_initialized:
                try:
                    sampled_peak = torch.cuda.max_memory_reserved() / 2**30
                    peak = sampled_peak if peak is None else max(peak, sampled_peak)
                except BaseException:
                    # Best-effort telemetry must not replace the original
                    # failure or erase an already observed peak.
                    pass
            if callback is None:
                _initial_failure_state(run_dir, "initialization_" + type(exc).__name__)
            elif callback.state != "failed":
                callback._mark_failed("training_" + type(exc).__name__)
            retained_report(state="failed", completed=False)
        raise


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
            warmup_discarded_microsteps=0,
            probe_executed=False,
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
        warmup_discarded_microsteps=PROBE_WARMUP_MICROSTEPS % PROBE_GRAD_ACCUM,
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
    parser.add_argument("--base", default=None)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--steps", type=int, default=None,
                        help="Smoke/dry-run length in optimizer steps; exactly one of --steps or --epochs.")
    parser.add_argument("--epochs", type=int, default=None,
                        help="Epoch mode: N epochs (1-6) with per-epoch adapter leaves; no default.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--winner", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Validate rows and exit. No GPU and no report.")
    parser.add_argument("--probe", action="store_true", help="D-M1-F feasibility probe; writes metrics only.")
    parser.add_argument("--probe-rows-total", type=int, default=None)
    parser.add_argument("--probe-seq-len", type=int, default=SEQ)
    parser.add_argument("--expected-digests", type=Path, default=None)
    parser.add_argument("--trusted-digest-root", type=Path, default=None,
                        help="Approved root file; its SHA-256 is pinned in the trainer.")
    parser.add_argument("--publication-binding", type=Path, default=None,
                        help="Epoch mode only: staged publication binding authenticating rows, "
                             "trainer, predictor, scorer and renderer before GPU training proceeds.")
    args = parser.parse_args(argv)
    explicit_pin = args.base is not None and args.revision is not None
    args.base = args.base if args.base is not None else os.environ.get("ADAPTER_BASE", SMOKE_BASE)
    args.revision = args.revision if args.revision is not None else os.environ.get("ADAPTER_REVISION", SMOKE_REVISION)
    if args.winner and not args.probe:
        fail("--winner is only valid together with --probe")
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        fail(f"revision {args.revision!r} is not a full 40-hex commit")
    if args.steps is not None and args.steps < 1:
        fail("steps must be positive")
    if args.epochs is not None:
        if args.steps is not None:
            fail("provide exactly one of --steps or --epochs, not both")
        if not EPOCH_MODE_MIN <= args.epochs <= EPOCH_MODE_MAX:
            fail(f"--epochs must be {EPOCH_MODE_MIN} to {EPOCH_MODE_MAX}")
        if args.probe:
            fail("--epochs cannot be combined with --probe")
        if not explicit_pin or not is_winner_pin(args.base, args.revision):
            fail(f"epoch mode requires explicit --base {WINNER_BASE} --revision {WINNER_REVISION}")
        if args.publication_binding is None:
            fail("--publication-binding is required in epoch mode")
    else:
        if args.publication_binding is not None:
            fail("--publication-binding is only valid in epoch mode")
        if not args.probe and not args.check_only and args.steps is None:
            fail("a training run needs exactly one of --steps or --epochs")
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
    try:
        if args.epochs is not None:
            if args.rows.name != "accepted.jsonl":
                fail("epoch mode requires the admitted accepted.jsonl")
            # The normal epoch path loads once inside train(), before CUDA or
            # model access. Check-only uses the same authenticated loader.
            rows, dev_rows = None, None
            if args.check_only:
                _, rows, dev_rows = load_epoch_rows(
                    args.publication_binding, args.rows.parent,
                    Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent / "eval")
                if not rows:
                    fail(f"no train rows in {args.rows}")
        else:
            rows, dev_rows = load_training_rows(args.rows), []
    except BaseException as exc:
        if args.epochs is not None:
            run_dir = create_run_directory(args.report)
            _initial_failure_state(run_dir, "input_" + type(exc).__name__)
            report = build_report(run=args.report.stem, base=args.base, revision=args.revision,
                rows=0, steps=args.steps, mask_coverage=0.0, peak_reserved_gib=None,
                loss=None, wall_seconds=0.0, winner=args.winner, resolved_commit=args.revision,
                epochs=args.epochs, checkpoints=[], output_dir=str(run_dir),
                state="failed", operational_completion=False, qualified=False,
                selected_adapter=None, dev_selection_status="not_selected")
            write_report(args.report, report)
        raise
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
            dev_rows,
            args.base,
            args.revision,
            args.steps,
            args.report,
            winner=args.winner,
            expected_digests_path=args.expected_digests,
            trusted_digest_root_path=args.trusted_digest_root,
            epochs=args.epochs,
            publication_binding_path=args.publication_binding,
            publication_rows_dir=args.rows.parent,
            publication_code_dir=Path(__file__).resolve().parent,
            publication_eval_dir=Path(__file__).resolve().parent.parent / "eval",
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 02:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
