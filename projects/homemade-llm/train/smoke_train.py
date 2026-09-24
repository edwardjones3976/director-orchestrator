# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | BEGIN]]
"""M0.3 smoke fine-tune: prove the Unsloth container trains on the RTX 5070 and exports a GGUF.

Runs inside unsloth/unsloth with C:\\llm mounted at /workspace (see scripts/train-smoke.ps1).
20 QLoRA steps on 50 synthetic rows; this measures the pipeline, not model quality.
Writes: /workspace/runs/<run>/adapter, /workspace/models/smoke/*.gguf, /workspace/reports/<run>.json,
the Hugging Face cache at /workspace/hf (C:\\llm\\hf, the planned cache tier), and scratch under /tmp.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", "/workspace/hf")

import torch  # noqa: E402  (HF_HOME must be set before the HF stack is imported)
from unsloth import FastLanguageModel  # noqa: E402
from datasets import Dataset  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

BASE = os.environ.get("SMOKE_BASE", "Qwen/Qwen3-0.6B")
STEPS, ROWS, SEQ = 20, 50, 1024
RUN = "smoke-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
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


def main() -> None:
    t0 = time.time()
    model, tok = FastLanguageModel.from_pretrained(BASE, max_seq_length=SEQ, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=16, lora_dropout=0, bias="none", use_gradient_checkpointing="unsloth",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    # Qwen3 emits a <think> block unless the template disables it; strict JSON needs it off (M0.11 note).
    texts = [tok.apply_chat_template(r["messages"], tokenize=False, enable_thinking=False) for r in rows()]
    ds = Dataset.from_dict({"text": texts})
    tokens_per_row = sum(len(tok(t)["input_ids"]) for t in texts) / len(texts)

    trainer = SFTTrainer(
        model=model, processing_class=tok, train_dataset=ds,
        args=SFTConfig(output_dir=str(RUN_DIR / "trainer"), dataset_text_field="text", max_length=SEQ,
                       per_device_train_batch_size=2, gradient_accumulation_steps=1, max_steps=STEPS,
                       learning_rate=2e-4, logging_steps=5, save_strategy="no", report_to="none", seed=7),
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
        "run": RUN, "base": BASE, "steps": STEPS, "rows": ROWS, "batch": 2,
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
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
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")  # training evidence survives a failed export

    # Merge + GGUF export rewrite model.safetensors in place, which the Windows bind mount refuses
    # (Errno 13), so export inside the container filesystem and copy only the finished GGUF out.
    export_dir = Path("/tmp") / RUN / "gguf"
    t_gguf = time.time()
    model.save_pretrained_gguf(str(export_dir), tok, quantization_method="q4_k_m")
    report["gguf_export_seconds"] = round(time.time() - t_gguf, 1)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    report["gguf"] = []
    for f in export_dir.parent.rglob("*.gguf"):
        dest = MODEL_DIR / f"{RUN}-{f.name}"
        shutil.copy2(f, dest)
        report["gguf"].append({"path": str(dest), "bytes": dest.stat().st_size})
    report["wall_seconds"] = round(time.time() - t0, 1)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["gguf"]:
        raise SystemExit("no GGUF produced")


if __name__ == "__main__":
    main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:47 UTC | END]]
