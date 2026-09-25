<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:19 UTC | BEGIN]] -->
# M0.11 — Base-model brief for M1 (2026-09-25)

**Question:** which open-weight bases are viable for a QLoRA fine-tune on one RTX 5070 (12 GB), and what does the public record say about structured output?

**Candidates:** Qwen3-8B, Qwen3-14B, Phi-4 (14B), Mistral Nemo 12B.

**Provenance.** This is evidence for the Director, not a verdict. Every fact below comes from a primary source retrieved on 2026-09-25.
- **Perplexity (sonar-pro):** ran first, as the workstream plan assigns, at a cost of USD 0.0398. It returned mostly "not found" and nothing at all for Phi-4.
- **Fallback, per the packet:** Director research using the Hugging Face model API for license and gating, the model cards through Firecrawl, and Unsloth's documentation.

## Findings

| | Qwen3-8B | Qwen3-14B | Phi-4 | Mistral Nemo 12B |
|---|---|---|---|---|
| **License** [1] | Apache-2.0 | Apache-2.0 | MIT | Apache-2.0 (Instruct and Base) |
| **Gated on Hugging Face** [1] | no | no | no | no |
| **Size** | 8.2 B [2] | 14.8 B [3] | 14 B [4] | 12 B [5] |
| **Context** | 32,768 native; 131,072 with YaRN [2] | 32,768 native; 131,072 with YaRN [3] | 16 K [4] | 128 K [5] |
| **QLoRA 4-bit VRAM floor (Unsloth)** [6] | 6 GB (8B row) | 8.5 GB (14B row) | 8.5 GB (14B row) | no 12B row; between the two |
| **Structured output and tools** | "excels in tool calling" (vendor claim), Qwen-Agent templates [2] | same family [3] | no function-calling or JSON evaluation on the card [4] | function calling supported [5] |
| **GGUF** | Unsloth and community GGUF repos [7] | Unsloth GGUF repo [7] | not checked | many community GGUFs converted with llama.cpp [7] |
| **Caveat** | thinking mode on by default: disable with `enable_thinking=False` or `/no_think` [2]; needs transformers ≥ 4.51 [2] | same [3] | shortest context of the four (16 K) [4] | recommended temperature 0.3; "does not have any moderation mechanisms" [5] |

**Context budget on 12 GB.** Unsloth's measured QLoRA benchmark for Llama-3.1-8B reaches 21,848 tokens on a 12 GB GPU, against 932 for Hugging Face with FA2 [8]. That figure is for an 8B model: 14B models leave far less room, which the plan's §4.1 note of about 2–4 K context already reflects. This project's own measurement is the smoke run: Qwen3-0.6B peaked at 0.8 GB, but that says nothing about the 8–14B bases.

**Structured-output scores.** No BFCL, IFEval or JSON-mode score for these exact checkpoints was found on the primary pages checked. Phi-4's card lists IFEval-adjacent reasoning benchmarks, but none for function calling or JSON [4]. M1 measures this directly on the held-out sets, with schema-constrained decoding, which the published numbers could not replace anyway.

## Implications for M1

1. **No owner action for bases.** All four are ungated, so no Hugging Face token and no license clicks are needed. This corrects the earlier plan, which assumed a gating step.
2. **Qwen3 thinking mode.** The Qwen3 runs must disable it, as the smoke run already does. Otherwise `<think>` blocks break strict JSON.
3. **Phi-4's 16 K context** is enough for work packets (the real packets are far shorter), but it is the tightest of the four.
4. **VRAM at 14B.** 14B models fit QLoRA at 8.5 GB, with little headroom for context on 12 GB. The M1 shootout should record peak VRAM and the achievable context per base.

## Sources (all retrieved 2026-09-25)

1. Hugging Face model API, `https://huggingface.co/api/models/<repo>`, `cardData.license` and `gated`. Revision and last-modified per repo:
   - Qwen/Qwen3-8B `b968826d`, 2025-07-26
   - Qwen/Qwen3-14B `40c06982`, 2025-07-26
   - microsoft/phi-4 `2db69c1c`, 2026-07-14
   - mistralai/Mistral-Nemo-Instruct-2407 `04d8a905`, 2025-07-28
   - mistralai/Mistral-Nemo-Base-2407 `a4477a2f`, 2025-07-28
2. https://huggingface.co/Qwen/Qwen3-8B (model card)
3. https://huggingface.co/unsloth/Qwen3-14B-GGUF (Unsloth's card, which reproduces Qwen's specification; seen via the Perplexity result). Qwen's own 14B card was not fetched separately; its license and gating come from source 1.
4. https://huggingface.co/microsoft/phi-4 (model card; released 2024-12-12)
5. https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407 (model card)
6. https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements (VRAM table)
7. https://huggingface.co/unsloth/Qwen3-8B-GGUF, https://huggingface.co/unsloth/Qwen3-14B-GGUF, https://huggingface.co/bartowski/Mistral-Nemo-Instruct-2407-GGUF (via Perplexity)
8. https://unsloth.ai/docs/basics/unsloth-benchmarks (context-length benchmarks)
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:19 UTC | END]] -->
