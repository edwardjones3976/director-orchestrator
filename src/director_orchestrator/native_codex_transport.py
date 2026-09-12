# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
"""One packet-only Codex account run, with no implicit API retry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Mapping

from .packet_contracts import canonical_json_bytes, validate_packet

MAX_BYTES = 1_048_576


def codex_environment() -> dict[str, str]:
    # Saved native auth is read by Codex. Director and API credentials never cross.
    from .primary_transports import _BASE_CHILD_ENVIRONMENT
    return {k: v for k, v in os.environ.items() if k.upper() in _BASE_CHILD_ENVIRONMENT}


class NativeCodexPacketTransport:
    """Packet reasoning only; never claims filesystem execution or free billing."""

    def __init__(self, executable: str, model: str) -> None:
        path = Path(executable)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("Codex requires an explicit native executable")
        if os.name == "nt" and path.suffix.lower() != ".exe":
            raise ValueError("Codex Windows transport requires a native executable")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", model):
            raise ValueError("Codex model must be explicit")
        self.executable = str(path.resolve(strict=True))
        self.model = model

    def authenticated(self) -> bool:
        try:
            result = subprocess.run(
                (self.executable, "login", "status"), env=codex_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=10, shell=False, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.strip() == b"Logged in using ChatGPT"

    def send(self, work_packet_json: bytes, *, context: Any) -> Mapping[str, object]:
        if not work_packet_json or len(work_packet_json) > MAX_BYTES:
            raise RuntimeError("Codex packet exceeds its input bound")
        work = validate_packet("work", json.loads(work_packet_json))
        if context.fencing_generation < 1 or context.max_hops < 1:
            raise RuntimeError("Codex lacks an active bounded writer context")
        if context.allowed_external_effects or work["allowed_external_effects"]:
            raise RuntimeError("Codex packet reasoning cannot execute external effects")
        token_limit = getattr(context, "total_token_limit", None)
        if type(token_limit) is not int or token_limit < 1:
            raise RuntimeError("Codex requires a positive signed total_token_limit")
        # The verified CLI exposes token accounting and context/compaction controls,
        # not a hard execution cap over input plus output tokens. A prompt request,
        # rollout-budget reminder, or usage check after completion cannot enforce
        # this signed limit. Do not authenticate, launch, or silently use an API.
        raise RuntimeError(
            "Codex native route unavailable: signed total_token_limit cannot be "
            "enforced by the verified CLI"
        )

    @staticmethod
    def _run(argv: list[str], prompt: bytes, cwd: str, timeout: int) -> bytes:
        output = bytearray()
        overflow = threading.Event()
        with tempfile.TemporaryFile() as source:
            source.write(prompt)
            source.seek(0)
            process = subprocess.Popen(argv, cwd=cwd, env=codex_environment(), stdin=source,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       shell=False)
            def consume() -> None:
                assert process.stdout is not None
                while chunk := process.stdout.read(4096):
                    if len(output) + len(chunk) > MAX_BYTES:
                        overflow.set()
                        return
                    output.extend(chunk)
            reader = threading.Thread(target=consume, daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if overflow.is_set() or time.monotonic() >= deadline:
                        raise RuntimeError("Codex outcome is ambiguous: output or timeout bound")
                    time.sleep(0.05)
                reader.join(timeout=2)
                if reader.is_alive() or overflow.is_set() or process.returncode != 0:
                    raise RuntimeError("Codex outcome is ambiguous")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                reader.join(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()
        return bytes(output)


def normalize_codex_jsonl(work: Mapping[str, Any], stdout: bytes, *, requested_model: str) -> dict[str, Any]:
    from .primary_transports import normalize_native_provider_envelope
    if not stdout or len(stdout) > MAX_BYTES:
        raise RuntimeError("Codex returned no bounded event stream")
    thread_id = None
    started = completed = False
    message = None
    usage = None
    try:
        for line in stdout.splitlines():
            event = json.loads(line)
            kind = event["type"]
            if completed:
                raise ValueError("events after terminal")
            if kind == "thread.started" and thread_id is None:
                thread_id = event["thread_id"]
                if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", thread_id):
                    raise ValueError("invalid session")
            elif kind == "turn.started" and thread_id and not started:
                started = True
            elif kind in {"item.started", "item.updated", "item.completed"} and started:
                item = event["item"]
                if item["type"] not in {"reasoning", "agent_message"}:
                    raise ValueError("unexpected tool execution")
                if item["type"] == "agent_message" and kind == "item.completed":
                    message = item["text"]
            elif kind == "turn.completed" and started and message is not None:
                completed = True
                native_usage = event.get("usage", {})
                usage = {"model": None, "cost_usd": None}
                for name in ("input_tokens", "output_tokens"):
                    value = native_usage.get(name)
                    if type(value) is not int or value < 0:
                        raise ValueError("invalid usage")
                    usage[name] = value
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            else:
                raise ValueError("unsupported event sequence")
        if not completed:
            raise ValueError("incomplete turn")
        structured = json.loads(message)
        if not isinstance(structured, dict) or structured.get("side_effects"):
            raise ValueError("invalid structured response")
        # Only outcome/status/risks/blockers are model-authored. No synthetic checks.
        structured = {k: v for k, v in structured.items() if k in {"outcome", "status", "risks", "blockers"}}
        result = normalize_native_provider_envelope("openai", work, {
            "session_id": "codex:" + thread_id, "structured_output": structured, "usage": usage,
        })
        result["risks"].append(f"Native Codex ChatGPT authentication; requested model {requested_model}; actual model and cost are not reported by this event stream. Packet-only analysis; repository checks were not executed.")
        return validate_packet("result", result)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError("Codex terminal event evidence is invalid or ambiguous") from exc
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]

# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:29 UTC | END]]
