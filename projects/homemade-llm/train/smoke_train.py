# [[Codex | GPT-6 | 2026-09-25 | 09:23 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
"""P7 smoke fine-tune: prove the pinned Unsloth container trains, saves, merges and exports a GGUF on the RTX 5070.

Runs inside unsloth/unsloth (see scripts/train-smoke.ps1), which mounts only:
  /workspace/hf                    the Hugging Face cache, READ-ONLY (snapshots are loaded by explicit local path)
  /workspace/smoke-digests.json    the root-pinned smoke digest list, READ-ONLY (its SHA-256 arrives in SMOKE_DIGESTS_SHA256)
  /workspace/runs/<run>            this run's own directory (adapter, trainer scratch)
  /workspace/models/smoke          GGUF publication (exclusive, no clobber)
  /workspace/reports               this run's report and failure receipt
20 QLoRA steps on 50 synthetic rows (seed 7, sequence length 1024); this measures the pipeline, not model quality.

Both complete snapshots (the pre-quantized training source and the full-precision merge source) are authenticated against
the digest list (exact file set, size, and SHA-256 read twice) before anything is loaded, everything runs offline, and the
merge source is re-verified immediately before the merge. Any failure writes reports/<run>.failure.json instead of a
report claiming a stage it did not reach.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# The image sets HF_HOME=/workspace/.cache/huggingface; override it to the mounted (read-only) cache, and never go online.
os.environ["HF_HOME"] = "/workspace/hf"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# GGUF export uses ONLY the pinned image's own prebuilt llama.cpp (quantizer, converter and its co-versioned conversion/
# package); Unsloth reads this path at import. Without it, the export looks in ~/.unsloth/llama.cpp, finds nothing and
# downloads or clones an unpinned llama.cpp from GitHub. The offline and no-auto-install switches make any such fallback
# fail instead of fetching.
os.environ["UNSLOTH_LLAMA_CPP_PATH"] = "/opt/unsloth/llama.cpp"
os.environ["UNSLOTH_LLAMA_CPP_OFFLINE"] = "1"
os.environ["UNSLOTH_OFFLINE"] = "1"
os.environ["UNSLOTH_AUTO_INSTALL"] = "0"

import torch  # noqa: E402  (HF_HOME must be set before the HF stack is imported)
from unsloth import FastLanguageModel  # noqa: E402
from datasets import Dataset  # noqa: E402
from transformers import set_seed  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

BASE = os.environ.get("SMOKE_BASE", "unsloth/qwen3-0.6b-unsloth-bnb-4bit")
REVISION = os.environ.get("SMOKE_REVISION", "e86f5289a2ca40ab7114071959dc2079cf97cb3c")
MERGE_SOURCE = os.environ.get("SMOKE_MERGE_SOURCE", "unsloth/qwen3-0.6b")
MERGE_REVISION = os.environ.get("SMOKE_MERGE_REVISION", "db4e90b8359f816f09bf5c944797ce116f26da3c")
DIGESTS = Path(os.environ.get("SMOKE_DIGESTS", "/workspace/smoke-digests.json"))
DIGESTS_SHA256 = os.environ.get("SMOKE_DIGESTS_SHA256", "")
TEMPLATE_SHA256 = os.environ.get("SMOKE_TEMPLATE_SHA256", "")
SEED, STEPS, ROWS, SEQ = 7, 20, 50, 1024
# Timestamp for humans plus a random suffix, so two runs in the same second never share paths. The host passes its own.
RUN = os.environ.get("SMOKE_RUN") or "smoke-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
WS = Path("/workspace")
RUN_DIR, MODEL_DIR = WS / "runs" / RUN, WS / "models" / "smoke"
REPORT, FAILURE = WS / "reports" / f"{RUN}.json", WS / "reports" / f"{RUN}.failure.json"
EXPORT_DIR = Path("/tmp") / RUN / "gguf"
LIST_ENTRY_KEYS = {"files", "smoke_only", "source"}
FILE_KEYS = {"path", "sha256", "size"}
HEX64 = re.compile(r"[0-9a-f]{64}")


class SmokeFailure(Exception):
    """A refusal with a stage-specific reason; main() turns it (and any other exception) into a failure receipt."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows() -> list[dict]:
    """Tiny packet-shaped task: turn a one-line request into a two-field JSON object (source-defined, no data file)."""
    verbs = ["Add", "Fix", "Document", "Test", "Remove"]
    things = ["the mirror log", "the preflight floor", "a spend report", "the LM Studio probe", "a dataset scrub"]
    out = []
    for i in range(ROWS):
        req = f"{verbs[i % 5]} {things[(i // 5) % 5]} (item {i})"
        answer = json.dumps({"task_id": f"smoke-{i:03d}", "objective": req}, separators=(",", ":"))
        out.append({"messages": [
            {"role": "system", "content": "Reply with one JSON object with keys task_id and objective. No prose."},
            {"role": "user", "content": req},
            {"role": "assistant", "content": answer},
        ]})
    return out


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 with a fixed buffer: a 0.6B safetensors file is never held in memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def strict_json(raw: bytes):
    """UTF-8 JSON that refuses duplicate member names (a later duplicate must never silently win)."""
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise SmokeFailure("duplicate JSON member name")
        return dict(items)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SmokeFailure(f"invalid JSON: {type(exc).__name__}") from None


def load_digest_list(path: Path, expected_sha256: str, training: str, merge: str) -> dict:
    """The root-pinned list: its bytes must equal the expected digest, and it must name exactly the two pinned snapshots."""
    if not HEX64.fullmatch(expected_sha256 or ""):
        raise SmokeFailure("SMOKE_DIGESTS_SHA256 is missing or not 64 lowercase hex")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SmokeFailure("digest list does not match its pinned SHA-256")
    data = strict_json(raw)
    if not isinstance(data, dict) or set(data) != {training, merge}:
        raise SmokeFailure("digest list must name exactly the pinned training and merge snapshots")
    for key, entry in data.items():
        if not isinstance(entry, dict) or set(entry) != LIST_ENTRY_KEYS or not isinstance(entry["files"], list) or not entry["files"]:
            raise SmokeFailure(f"digest list entry {key} has an unexpected shape")
        for f in entry["files"]:
            if (not isinstance(f, dict) or set(f) != FILE_KEYS or not isinstance(f["path"], str) or not f["path"]
                    or not isinstance(f["size"], int) or isinstance(f["size"], bool) or not HEX64.fullmatch(str(f["sha256"]))):
                raise SmokeFailure(f"digest list entry {key} has a malformed file record")
    return data


def snapshot_dir(repo: str, revision: str) -> Path:
    """The cached snapshot directory for an exact 40-hex revision; never a ref, never a download."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SmokeFailure(f"{repo}: revision {revision!r} is not a full 40-hex commit")
    d = Path(os.environ["HF_HOME"]) / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
    if not d.is_dir():
        raise SmokeFailure(f"{repo}@{revision} is not a complete local snapshot")
    return d


def snapshot_files(d: Path) -> set[str]:
    return {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}


def verify_snapshot(d: Path, entry: dict, label: str) -> dict:
    """Exact file set, size, and two independent SHA-256 reads of every file, all equal to the list."""
    expected = {f["path"]: f for f in entry["files"]}
    actual = snapshot_files(d)
    if actual != set(expected):
        missing, extra = sorted(set(expected) - actual), sorted(actual - set(expected))
        raise SmokeFailure(f"{label}: snapshot file set differs from the digest list (missing {missing[:3]}, extra {extra[:3]})")
    reads = {}
    for n in (1, 2):
        at = utc()
        for rel, f in sorted(expected.items()):
            p = d / rel
            if p.stat().st_size != f["size"]:
                raise SmokeFailure(f"{label}: {rel} size differs from the digest list")
            if sha256_file(p) != f["sha256"]:
                raise SmokeFailure(f"{label}: {rel} SHA-256 read {n} differs from the digest list")
        reads[f"read{n}_utc"] = at
    return {"files": len(expected), **reads}


def verify_template(d: Path, expected_sha256: str) -> None:
    if not HEX64.fullmatch(expected_sha256 or ""):
        raise SmokeFailure("SMOKE_TEMPLATE_SHA256 is missing or not 64 lowercase hex")
    if sha256_file(d / "chat_template.jinja") != expected_sha256:
        raise SmokeFailure("training snapshot chat_template.jinja does not match the root-pinned template digest")


def adapter_manifest(d: Path) -> dict:
    """Every file in the saved adapter directory, sorted by relative path, with size and SHA-256, plus the manifest's own digest."""
    files = [{"path": p.relative_to(d).as_posix(), "size": p.stat().st_size, "sha256": sha256_file(p)}
             for p in sorted(d.rglob("*"), key=lambda q: q.relative_to(d).as_posix()) if p.is_file()]
    if not files:
        raise SmokeFailure("saved adapter directory is empty")
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"files": files, "manifest_sha256": hashlib.sha256(canonical).hexdigest()}


def check_steps(actual: int, requested: int) -> int:
    if actual != requested:
        raise SmokeFailure(f"completed optimizer steps {actual} != requested {requested}")
    return actual


def write_report(path: Path, report: dict, *, create: bool = False) -> None:
    """Write to a temp file and rename over the report, so an interrupted update never truncates the earlier valid report.
    The first write (create=True) refuses an existing report: a run never adopts or overwrites someone else's evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if create and path.exists():
        raise SmokeFailure(f"report {path.name} already exists")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def publish_gguf(src: Path, dest: Path) -> dict:
    """Exclusive, verified publication. The GGUF is hashed twice before the copy; the destination name is reserved with an
    exclusive create (an existing file, including one created concurrently, refuses and is never touched); the copy goes
    to a run-owned .part, is hashed again, and only then replaces the run's own placeholder. On any failure only files
    this call created are removed."""
    h1, h2 = sha256_file(src), sha256_file(src)
    if h1 != h2:
        raise SmokeFailure(f"{src.name}: the two pre-publication SHA-256 reads differ")
    part = dest.with_name(dest.name + ".part")
    created = []
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # reserve the name; FileExistsError if anything is there
        os.close(fd)
        created.append(dest)
        fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        created.append(part)
        shutil.copyfile(src, part)
        if part.stat().st_size != src.stat().st_size or sha256_file(part) != h1:
            raise SmokeFailure(f"{src.name}: published copy does not match the exported bytes")
        os.replace(part, dest)   # replaces only the placeholder this call created
        created.remove(part)
        if sha256_file(dest) != h1:
            raise SmokeFailure(f"{dest.name}: post-publication SHA-256 differs")
        return {"path": str(dest), "bytes": dest.stat().st_size, "sha256": h1}
    except BaseException:
        for p in created:
            p.unlink(missing_ok=True)
        raise


def publish_all(src_dir: Path, model_dir: Path) -> list[dict]:
    """Publish every exported GGUF; if any one fails, remove the ones this run already published."""
    model_dir.mkdir(parents=True, exist_ok=True)
    published = []
    try:
        for f in sorted(src_dir.rglob("*.gguf")):
            published.append(publish_gguf(f, model_dir / f"{RUN}-{f.name}"))
        if not published:
            raise SmokeFailure("no GGUF produced")
        return published
    except BaseException:
        for g in published:
            Path(g["path"]).unlink(missing_ok=True)
        raise


_ALLOWED_PATH_PREFIXES = (f"/workspace/runs/{RUN}", "/workspace/reports", "/workspace/models/smoke", f"/tmp/{RUN}")


def bounded_message(exc: BaseException, limit: int = 400) -> str:
    """A short message for the failure receipt: absolute paths outside this run's own outputs are masked, and it is
    truncated, so no cache layout, credential location or host path leaks into a published receipt."""
    text = str(exc).replace("\n", " ")
    text = re.sub(r"/[^\s'\":,]+", lambda m: m.group(0) if m.group(0).startswith(_ALLOWED_PATH_PREFIXES) else "<path>", text)
    return text[:limit]


def write_failure(stage: str, exc: BaseException, evidence: dict) -> None:
    """Exclusive create: a receipt is never overwritten, and a crash while writing never leaves a success-looking file."""
    receipt = {"run": RUN, "status": "failed", "stage": stage, "exception": type(exc).__name__, "message": bounded_message(exc),
               "utc": utc(), "evidence": evidence}
    FAILURE.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE, "x", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)


def main() -> None:
    t0 = time.time()
    # docker stop sends SIGTERM to this process (PID 1, which ignores it without a handler): exit via SystemExit
    # so finally/except blocks, such as the failure receipt, still run.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    training, merge = f"{BASE}@{REVISION}", f"{MERGE_SOURCE}@{MERGE_REVISION}"
    stage, evidence = "verify", {"completed_stages": []}
    try:
        digests = load_digest_list(DIGESTS, DIGESTS_SHA256, training, merge)
        train_snap, merge_snap = snapshot_dir(BASE, REVISION), snapshot_dir(MERGE_SOURCE, MERGE_REVISION)
        auth = {"training": verify_snapshot(train_snap, digests[training], training),
                "merge": verify_snapshot(merge_snap, digests[merge], merge)}
        verify_template(train_snap, TEMPLATE_SHA256)
        evidence.update(digest_list_sha256=DIGESTS_SHA256, snapshot_authentication=auth)
        evidence["completed_stages"].append("verify")

        stage = "load"
        set_seed(SEED)  # before model/PEFT construction, so the LoRA initialisation is reproducible too
        torch.cuda.reset_peak_memory_stats()
        model, tok = FastLanguageModel.from_pretrained(str(train_snap), max_seq_length=SEQ, load_in_4bit=True, local_files_only=True)
        model = FastLanguageModel.get_peft_model(
            model, r=16, lora_alpha=16, lora_dropout=0, bias="none", use_gradient_checkpointing="unsloth",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            random_state=SEED,
        )
        load_alloc_gb = torch.cuda.max_memory_allocated() / 2**30
        load_reserved_gb = torch.cuda.max_memory_reserved() / 2**30
        # Qwen3 emits a <think> block unless the template disables it; strict JSON needs it off (M0.11 note).
        texts = [tok.apply_chat_template(r["messages"], tokenize=False, enable_thinking=False) for r in rows()]
        ds = Dataset.from_dict({"text": texts})
        tokens_per_row = sum(len(tok(t)["input_ids"]) for t in texts) / len(texts)
        evidence["completed_stages"].append("load")

        stage = "train"
        trainer = SFTTrainer(
            model=model, processing_class=tok, train_dataset=ds,
            args=SFTConfig(output_dir=str(RUN_DIR / "trainer"), dataset_text_field="text", max_length=SEQ,
                           per_device_train_batch_size=2, gradient_accumulation_steps=1, max_steps=STEPS,
                           learning_rate=2e-4, logging_steps=5, save_strategy="no", report_to="none", seed=SEED),
        )
        torch.cuda.reset_peak_memory_stats()
        t_train = time.time()
        result = trainer.train()
        train_s = time.time() - t_train
        steps = check_steps(int(trainer.state.global_step), STEPS)
        evidence["optimizer_steps"] = steps
        # PyTorch's own peaks: "allocated" is tensor memory, "reserved" includes the caching allocator's pool.
        # Neither counts the CUDA context or other processes, so they understate device-wide VRAM use.
        peak_alloc_gb = torch.cuda.max_memory_allocated() / 2**30
        peak_reserved_gb = torch.cuda.max_memory_reserved() / 2**30
        step_losses = [e["loss"] for e in trainer.state.log_history if "loss" in e]
        evidence["completed_stages"].append("train")

        stage = "save"
        adapter_dir = RUN_DIR / "adapter"
        model.save_pretrained(str(adapter_dir))
        tok.save_pretrained(str(adapter_dir))
        manifest = adapter_manifest(adapter_dir)
        evidence["adapter_manifest_sha256"] = manifest["manifest_sha256"]
        report = {
            "run": RUN, "status": "trained-not-exported", "base": BASE, "base_revision": REVISION, "base_resolved_commit": REVISION,
            "merge_source": MERGE_SOURCE, "merge_revision": MERGE_REVISION,
            "digest_list_sha256": DIGESTS_SHA256, "chat_template_sha256": TEMPLATE_SHA256, "snapshot_authentication": auth,
            "row_generator": {"function": "smoke_train.rows", "rows": ROWS, "seed": SEED, "sequence_length": SEQ},
            "seed": SEED, "steps_requested": STEPS, "optimizer_steps": steps, "rows": ROWS, "batch": 2,
            "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
            "load_peak_torch_allocated_gb": round(load_alloc_gb, 2), "load_peak_torch_reserved_gb": round(load_reserved_gb, 2),
            "peak_torch_allocated_gb": round(peak_alloc_gb, 2), "peak_torch_reserved_gb": round(peak_reserved_gb, 2),
            "train_seconds": round(train_s, 1),
            "tokens_per_second_approx": round(STEPS * 2 * tokens_per_row / train_s, 1),  # steps x batch x mean row tokens
            "mean_train_loss": round(result.training_loss, 4),
            "last_logged_loss": round(step_losses[-1], 4) if step_losses else None,
            "adapter_manifest": manifest,
        }
        write_report(REPORT, report, create=True)  # training evidence survives a failed export
        evidence["completed_stages"].append("save")

        stage = "export"
        del trainer, model
        gc.collect()
        torch.cuda.empty_cache()
        t_gguf = time.time()
        subprocess.run([sys.executable, __file__, "export"], check=True,
                       env={**os.environ, "SMOKE_RUN": RUN, "SMOKE_ADAPTER_MANIFEST_SHA256": manifest["manifest_sha256"]})
        report["gguf_export_seconds"] = round(time.time() - t_gguf, 1)
        evidence["completed_stages"].append("export")

        stage = "publish"
        report["gguf"] = publish_all(EXPORT_DIR, MODEL_DIR)
        report["status"] = "complete"
        report["wall_seconds"] = round(time.time() - t0, 1)
        try:
            write_report(REPORT, report)
        except BaseException:
            for g in report["gguf"]:
                Path(g["path"]).unlink(missing_ok=True)   # no GGUF without its final report
            raise
        evidence["completed_stages"].append("publish")
        print(json.dumps(report, indent=2))
    except BaseException as exc:
        try:
            write_failure(stage, exc, evidence)
        except BaseException as receipt_exc:   # never let a receipt problem hide the original failure
            print(f"failure receipt not written: {type(receipt_exc).__name__}", file=sys.stderr)
        if isinstance(exc, SystemExit) and exc.code not in (None, 0):
            raise
        raise SystemExit(1) from exc


def export() -> None:
    """Child process, started offline by main(): re-verify the merge snapshot and the saved adapter, reload the adapter on
    its verified local base, merge onto the verified local merge snapshot, and write the GGUF to the container filesystem
    (merge + export rewrite model.safetensors in place, which the Windows bind mount refuses with Errno 13)."""
    training, merge = f"{BASE}@{REVISION}", f"{MERGE_SOURCE}@{MERGE_REVISION}"
    digests = load_digest_list(DIGESTS, DIGESTS_SHA256, training, merge)
    adapter_dir = RUN_DIR / "adapter"
    if adapter_manifest(adapter_dir)["manifest_sha256"] != os.environ.get("SMOKE_ADAPTER_MANIFEST_SHA256"):
        raise SmokeFailure("saved adapter bytes differ from the manifest recorded after the save")
    merge_dir = snapshot_dir(MERGE_SOURCE, MERGE_REVISION)
    verify_snapshot(merge_dir, digests[merge], merge)   # immediately before the merge
    llama = Path(os.environ["UNSLOTH_LLAMA_CPP_PATH"])
    for rel in ("llama-quantize", "convert_hf_to_gguf.py", "conversion/__init__.py", "conversion/base.py"):
        if not (llama / rel).is_file():
            raise SmokeFailure(f"the image's prebuilt llama.cpp lacks {rel}; refusing to let the export install or fetch one")
    model, tok = FastLanguageModel.from_pretrained(str(adapter_dir), max_seq_length=SEQ, load_in_4bit=True, local_files_only=True)
    # Unsloth picks the merge source from config._name_or_path and merges a local directory's safetensors as-is.
    model.config._name_or_path = str(merge_dir)
    model.save_pretrained_gguf(str(EXPORT_DIR), tok, quantization_method="q4_k_m")


if __name__ == "__main__":
    export() if sys.argv[1:] == ["export"] else main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
# [[Cursor | cursor-agent | 2026-09-25 | 09:14 UTC | END]]
# [[Codex | GPT-6 | 2026-09-25 | 09:23 UTC | END]]
