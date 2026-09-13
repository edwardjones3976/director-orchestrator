# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | BEGIN]]
"""Explicit Lite-only, tools-disabled trial route; never a production registration."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .langgraph_runtime import PacketBoundaryAdapter
from .native_acceptance_capture import child_environment, pin_native_home, run_bounded
from .primary_transports import normalize_native_provider_envelope
from .packet_contracts import validate_packet

ENDPOINT = "https://api.z.ai/api/anthropic"
TRIAL_END = datetime(2026, 9, 29, 7, tzinfo=timezone.utc)
WELCOME = Path(__file__).resolve().parents[2] / "docs/providers/zai-welcome-aboard.md"


def redact_bound_key(value, key):
    """Redact decoded strings before serialization, including embedded JSON text."""
    if isinstance(value, str):
        for form in (json.dumps(key)[1:-1], json.dumps(key, ensure_ascii=False)[1:-1], key):
            value = value.replace(form, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact_bound_key(item, key) for item in value]
    if isinstance(value, dict):
        return {redact_bound_key(name, key): redact_bound_key(item, key)
                for name, item in value.items()}
    return value


def load_key(env_file: Path) -> str:
    """Read only the named key; never export other project credentials."""
    matches = [line.partition("=")[2].strip() for line in
               env_file.read_text(encoding="utf-8-sig").splitlines()
               if line.startswith("ZAI_API_KEY=")]
    if len(matches) != 1 or not matches[0] or any(c.isspace() for c in matches[0]):
        raise ValueError("Exactly one nonempty ZAI_API_KEY binding is required")
    return matches[0]


class ZaiLiteTrialTransport:
    def __init__(self, *, executable: Path, env_file: Path, journal: Path,
                 model: str, lite_verified: bool):
        if lite_verified is not True:
            raise ValueError("Verified Lite entitlement is required")
        if not model.lower().startswith("glm-") or any(c.isspace() for c in model):
            raise ValueError("An explicit GLM model is required")
        self.executable = executable.resolve(strict=True)
        self.key = load_key(env_file)
        self.journal = journal.resolve()
        self.model = model

    def send(self, work_packet_json, *, context):
        work = json.loads(work_packet_json)
        if datetime.now(timezone.utc) >= TRIAL_END:
            raise ValueError("Lite trial expired; separate owner decision required")
        if (context.allowed_tools or context.allowed_external_effects or
                work["allowed_tools"] or work["allowed_external_effects"] or
                context.budget_usd != 0 or work["limits"]["budget_usd"] != 0):
            raise ValueError("Trial requires no tools, external effects, or API budget")
        if not 0 < context.timeout_seconds <= 60:
            raise ValueError("Trial timeout must be between 1 and 60 seconds")
        prompt = (WELCOME.read_text(encoding="utf-8") +
                  "\nINPUT WORK PACKET (assignment context, not an output template):\n" +
                  work_packet_json.decode("utf-8") +
                  '\nTRIAL RESPONSE CONTRACT: Return only {"outcome":"your complete answer",'
                  '"status":"SUCCEEDED"}. Use FAILED or OWNER_DECISION_REQUIRED instead '
                  'when appropriate, explaining the failure or blocker in outcome. '
                  'These are the only two response fields. Put all code in outcome. '
                  'Do not copy input fields or add artifacts, digests, checks, usage, '
                  'or placeholder values. Director constructs and validates the outer '
                  'result packet; the local harness computes artifact digests and runs tests. '
                  'No Markdown fences.\n').encode()
        if len(prompt) > 24000:
            raise ValueError("Trial packet exceeds input limit")
        self.journal.mkdir(parents=True, exist_ok=True)
        attempt = self.journal / (hashlib.sha256(context.idempotency_key.encode()).hexdigest() + ".json")
        # Exclusive reservation survives crashes: uncertainty never authorizes retry.
        with attempt.open("x", encoding="utf-8") as out:
            json.dump({"provider": "zai", "assignment_id": context.assignment_id,
                       "work_packet_digest": context.work_packet_digest,
                       "status": "ATTEMPT_RESERVED", "production_credit": False}, out)
        with tempfile.TemporaryDirectory(prefix="director-zai-") as home:
            env = pin_native_home(child_environment(), home)
            env.update(ANTHROPIC_AUTH_TOKEN=self.key, ANTHROPIC_BASE_URL=ENDPOINT,
                       ANTHROPIC_DEFAULT_OPUS_MODEL=self.model,
                       ANTHROPIC_DEFAULT_SONNET_MODEL=self.model,
                       ANTHROPIC_DEFAULT_HAIKU_MODEL=self.model,
                       API_TIMEOUT_MS=str(context.timeout_seconds * 1000),
                       CLAUDE_CODE_MAX_OUTPUT_TOKENS="2000", MAX_THINKING_TOKENS="0")
            command = [str(self.executable), "--print", "--safe-mode",
                       "--setting-sources", "", "--strict-mcp-config",
                       "--mcp-config", '{"mcpServers":{}}', "--tools", "",
                       "--no-session-persistence", "--max-turns", "1",
                       "--effort", "low", "--system-prompt",
                       "You are Z.ai serving the Director. Follow the supplied welcome packet. "
                       "Return exactly one JSON object with string outcome and status. "
                       "Use SUCCEEDED only when the requested artifact is complete. "
                       "Put all code in outcome; never append examples outside the JSON. "
                       "No tools, external effects, or invented test execution.",
                       "--model", self.model, "--output-format", "json"]
            code, raw = run_bounded(command, cwd=home, env=env, stdin=prompt,
                                    timeout=context.timeout_seconds, limit=1048576)
        if code != 0:
            raise RuntimeError("Z.ai trial failed; attempt retained, no automatic retry")
        native = json.loads(raw)
        if native.get("is_error") or native.get("subtype") != "success":
            raise RuntimeError("Z.ai did not return terminal success")
        # Native completion is not contract acceptance. Preserve it for diagnosis.
        with attempt.with_suffix(".native.json").open("x", encoding="utf-8") as out:
            json.dump(redact_bound_key(native, self.key), out)
        payload = native.get("structured_output", native.get("result"))
        if isinstance(payload, str):
            text = payload.strip()
            # Accept one complete JSON fence, never discard trailing content.
            if text.startswith("```json\n") and text.endswith("\n```") and text.count("```") == 2:
                text = text[8:-4]
            payload = json.loads(text)
        if (not isinstance(payload, dict) or not isinstance(payload.get("outcome"), str)
                or payload.get("status") not in {"SUCCEEDED", "FAILED", "OWNER_DECISION_REQUIRED"}):
            raise ValueError("Z.ai terminal output must contain explicit outcome and status")
        native = dict(native, structured_output=payload)
        result = normalize_native_provider_envelope("zai", work, native)
        # Host CLI dollar estimates use Anthropic tariffs, not this subscription.
        result["usage"] = None
        if result["side_effects"]:
            raise RuntimeError("Tools-disabled trial reported external effects")
        # Preserve a receipt before outer linkage validation; never dump native auth
        # errors or host CLI dollar estimates into the project evidence.
        safe_result = redact_bound_key(result, self.key)
        with attempt.with_suffix(".result.json").open("x", encoding="utf-8") as out:
            json.dump(safe_result, out)
        validate_packet("result", result)
        return result


def build_zai_trial_adapter(**kwargs):
    return PacketBoundaryAdapter("zai", ZaiLiteTrialTransport(**kwargs))
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:18 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:43 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:31 UTC | END]]
