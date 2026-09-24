# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
"""M0.3 smoke fine-tune: prove the Unsloth container trains on the RTX 5070 and exports a GGUF.

Runs inside unsloth/unsloth (see scripts/train-smoke.ps1). Only these C:\\llm folders are mounted:
/workspace/hf (Hugging Face cache), /workspace/runs, /workspace/models/smoke, /workspace/reports.
20 QLoRA steps on 50 synthetic rows; this measures the pipeline, not model quality.
Writes: runs/<run>/adapter, models/smoke/*.gguf, reports/<run>.json, the HF cache, and scratch under /tmp.

The base is Unsloth's pre-quantized mirror of Qwen/Qwen3-0.6B (Unsloth loads it even when asked for the
Qwen repo), pinned by revision; the report records the commit transformers actually resolved.
"""
from __future__ import annotations

import gc
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# The image sets HF_HOME=/workspace/.cache/huggingface; override it so the cache lands in the mounted C:\llm\hf.
os.environ["HF_HOME"] = "/workspace/hf"

import torch  # noqa: E402  (HF_HOME must be set before the HF stack is imported)
from unsloth import FastLanguageModel  # noqa: E402
from datasets import Dataset  # noqa: E402
from transformers import set_seed  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

BASE = os.environ.get("SMOKE_BASE", "unsloth/qwen3-0.6b-unsloth-bnb-4bit")
REVISION = os.environ.get("SMOKE_REVISION", "e86f5289a2ca40ab7114071959dc2079cf97cb3c")
# The GGUF merge merges the adapter onto this full-precision repo, which Unsloth would otherwise fetch at "main"
# (it takes no revision). It is bound instead: the pinned commit is downloaded, and the export runs in a child
# process with HF_HUB_OFFLINE=1 that points the merge at that snapshot's local directory. Offline, nothing
# else can be fetched, so the GGUF can only be built from the pinned base and pinned merge source.
MERGE_SOURCE = os.environ.get("SMOKE_MERGE_SOURCE", "unsloth/qwen3-0.6b")
MERGE_REVISION = os.environ.get("SMOKE_MERGE_REVISION", "db4e90b8359f816f09bf5c944797ce116f26da3c")
SEED, STEPS, ROWS, SEQ = 7, 20, 50, 1024
# Timestamp for humans plus a random suffix, so two runs in the same second never share paths.
RUN = os.environ.get("SMOKE_RUN") or "smoke-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
WS = Path("/workspace")
RUN_DIR, MODEL_DIR, REPORT = WS / "runs" / RUN, WS / "models" / "smoke", WS / "reports" / f"{RUN}.json"


def rows() -> list[dict]:
    """Tiny packet-shaped task: turn a one-line request into a two-field JSON object."""
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


def hf_cache_refs() -> dict[str, str]:
    """Cache contents (repo -> refs/main) at report time. Context only, not provenance: the shared cache
    also holds earlier runs' downloads. The base's provenance is base_resolved_commit."""
    hub = Path(os.environ["HF_HOME"]) / "hub"
    return {d.name.removeprefix("models--").replace("--", "/"): (d / "refs" / "main").read_text().strip()
            for d in sorted(hub.glob("models--*")) if (d / "refs" / "main").exists()}


def resolve_commit(config_commit: str | None, repo: str, revision: str) -> str:
    """The commit the base was actually loaded from. Prefer the hash transformers stamps on the config;
    fall back to the local snapshot the pinned revision resolves to. A pinned 40-hex revision must match.
    Raises instead of returning None, so a report can never claim provenance it does not have."""
    commit = config_commit
    if not commit:
        from huggingface_hub import snapshot_download
        commit = Path(snapshot_download(repo, revision=revision, local_files_only=True)).name
    if not commit:
        raise SystemExit(f"could not resolve the commit for {repo}@{revision}")
    if len(revision) == 40 and commit != revision:
        raise SystemExit(f"{repo}: loaded commit {commit} does not match the pinned revision {revision}")
    return commit


@contextmanager
def pinned_ref(repo: str, revision: str):
    """While active, the cache's refs/main for repo points at revision, which must already be a cached snapshot;
    offline (HF_HUB_OFFLINE=1), a lookup of repo with no revision then resolves to exactly that snapshot. The
    shared cache's previous ref is restored on exit (or removed if there was none), even on failure."""
    d = Path(os.environ["HF_HOME"]) / "hub" / ("models--" + repo.replace("/", "--"))
    if not (d / "snapshots" / revision).is_dir():
        raise SystemExit(f"{repo}@{revision} is not in the cache; cannot pin it for the offline export")
    ref = d / "refs" / "main"
    previous = ref.read_text() if ref.exists() else None
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(revision)
    try:
        yield
    finally:
        if previous is None:
            ref.unlink(missing_ok=True)
        else:
            ref.write_text(previous)


def write_report(path: Path, report: dict) -> None:
    """Write to a temp file and rename over the report, so an interrupted update never truncates the
    earlier valid report (the pre-export training evidence)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def publish(src: Path, dest: Path) -> None:
    """Copy under a .part name and rename only after the size matches, so no truncated .gguf is ever visible."""
    part = dest.with_name(dest.name + ".part")
    shutil.copy2(src, part)
    if part.stat().st_size != src.stat().st_size:
        part.unlink()
        raise SystemExit(f"short copy of {src.name}")
    os.replace(part, dest)


def main() -> None:
    t0 = time.time()
    # docker stop sends SIGTERM to this process (PID 1, which ignores it without a handler): exit via SystemExit
    # so finally blocks, such as the cache-ref restore, still run.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    set_seed(SEED)  # before model/PEFT construction, so the LoRA initialisation is reproducible too
    torch.cuda.reset_peak_memory_stats()
    model, tok = FastLanguageModel.from_pretrained(BASE, revision=REVISION, max_seq_length=SEQ, load_in_4bit=True)
    # This run's real provenance; fails the run (before any training) if it cannot be established.
    resolved = resolve_commit(getattr(model.config, "_commit_hash", None), BASE, REVISION)
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
    # PyTorch's own peaks: "allocated" is tensor memory, "reserved" includes the caching allocator's pool.
    # Neither counts the CUDA context or other processes, so they understate device-wide VRAM use.
    peak_alloc_gb = torch.cuda.max_memory_allocated() / 2**30
    peak_reserved_gb = torch.cuda.max_memory_reserved() / 2**30
    step_losses = [e["loss"] for e in trainer.state.log_history if "loss" in e]

    FastLanguageModel.for_inference(model)
    prompt = tok.apply_chat_template(rows()[0]["messages"][:2], tokenize=False, add_generation_prompt=True,
                                     enable_thinking=False)
    ids = tok(prompt, return_tensors="pt").to("cuda")
    sample = tok.decode(model.generate(**ids, max_new_tokens=48, do_sample=False)[0][ids["input_ids"].shape[1]:],
                        skip_special_tokens=True)

    model.save_pretrained(str(RUN_DIR / "adapter"))
    tok.save_pretrained(str(RUN_DIR / "adapter"))

    report = {
        "run": RUN, "base": BASE, "base_revision": REVISION, "base_resolved_commit": resolved,
        "seed": SEED, "steps": STEPS, "rows": ROWS, "batch": 2,
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
        "load_peak_torch_allocated_gb": round(load_alloc_gb, 2), "load_peak_torch_reserved_gb": round(load_reserved_gb, 2),
        "peak_torch_allocated_gb": round(peak_alloc_gb, 2), "peak_torch_reserved_gb": round(peak_reserved_gb, 2),
        "train_seconds": round(train_s, 1),
        "tokens_per_second_approx": round(STEPS * 2 * tokens_per_row / train_s, 1),  # steps x batch x mean row tokens
        "mean_train_loss": round(result.training_loss, 4),
        "last_logged_loss": round(step_losses[-1], 4) if step_losses else None,
        "sample_output": sample,
    }
    try:
        json.loads(sample)
        report["sample_is_json"] = True
    except ValueError:
        report["sample_is_json"] = False
    write_report(REPORT, report)  # training evidence survives a failed export

    # Stage the export's inputs, then free this process's GPU memory: fetch the merge source at its pinned commit;
    # the base's refs/main is pinned only for the export, because the offline child reloads the base by name.
    from huggingface_hub import snapshot_download
    snapshot_download(MERGE_SOURCE, revision=MERGE_REVISION)
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    t_gguf = time.time()
    with pinned_ref(BASE, resolved):
        subprocess.run([sys.executable, __file__, "export"], check=True,
                       env={**os.environ, "SMOKE_RUN": RUN, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    report["gguf_export_seconds"] = round(time.time() - t_gguf, 1)
    report["merge_source"], report["merge_revision"] = MERGE_SOURCE, MERGE_REVISION  # bound: offline, local snapshot
    export_dir = Path("/tmp") / RUN / "gguf"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    report["gguf"] = []
    for f in export_dir.parent.rglob("*.gguf"):
        dest = MODEL_DIR / f"{RUN}-{f.name}"
        publish(f, dest)
        report["gguf"].append({"path": str(dest), "bytes": dest.stat().st_size})
    report["hf_cache_refs"] = hf_cache_refs()  # context only; provenance is base_resolved_commit + merge_revision
    report["wall_seconds"] = round(time.time() - t0, 1)
    write_report(REPORT, report)
    print(json.dumps(report, indent=2))
    if not report["gguf"]:
        raise SystemExit("no GGUF produced")


def export() -> None:
    """Child process, started offline by main(): reload the adapter on its pinned base and write the GGUF.
    Merge + GGUF export rewrite model.safetensors in place, which the Windows bind mount refuses (Errno 13),
    so the export goes to the container filesystem and main() copies only the finished GGUF out."""
    from huggingface_hub import snapshot_download
    merge_dir = snapshot_download(MERGE_SOURCE, revision=MERGE_REVISION, local_files_only=True)
    model, tok = FastLanguageModel.from_pretrained(str(RUN_DIR / "adapter"), max_seq_length=SEQ, load_in_4bit=True)
    # Unsloth picks the merge source from config._name_or_path and merges a local directory's safetensors as-is.
    model.config._name_or_path = merge_dir
    model.save_pretrained_gguf(str(Path("/tmp") / RUN / "gguf"), tok, quantization_method="q4_k_m")


if __name__ == "__main__":
    export() if sys.argv[1:] == ["export"] else main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
