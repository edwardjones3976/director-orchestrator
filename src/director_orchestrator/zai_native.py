# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
"""Z.ai (GLM) production native lane: assignment-scoped, tool-enabled, fail closed.

Z.ai is reached by running the Claude Code executable against Z.ai's
Anthropic-compatible endpoint. The executable never makes the output Claude's:
this transport's lane is `zai`, its family is Z.ai and every result must prove
it was served by the configured GLM model.

What this adds to the shared `NativeCommandPacketTransport` path:

* Credential isolation. The child inherits no Anthropic, Claude or other lane
  credential; it receives only the configured ZAI_API_KEY (as the CLI's
  ANTHROPIC_AUTH_TOKEN), Z.ai's official endpoint and the GLM model names, in a
  throwaway home and config directory per attempt so no host settings, hooks,
  sessions or MCP servers are loaded.
* Assignment-scoped tools. Packet tool grants map to a fixed, narrow set of CLI
  tools and permission rules; anything unrecognised is refused before launch.
  Prohibited paths and secret files are denied; writes are confined to the
  packet's owned paths and exist only when the deployment enables them. The
  permission mode is never a bypass.
* Finite execution. Turns are capped, the shared absolute cutoff and contained
  process tree apply, and a nonzero exit or late return stays ambiguous.
* Terminal and model verification. A missing, errored, truncated or over-turn
  envelope is rejected, and every model in `modelUsage` must be the configured
  GLM model; a mismatch is rejected rather than relabelled.
* Usage honesty. Token counts come from the native envelope only; missing counts
  mean the result is never SUCCEEDED. The CLI prices with Anthropic tariffs, so
  cost is recorded as null (UNKNOWN), never as zero.

External effects are never performed here: branch and pull-request effects stay
with the Director's effect gateway, so a packet granting one is refused.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .host_provider_bridge import _enforce_native_result_attempt_limits
from .langgraph_runtime import ProviderAdapterContext
from .native_acceptance_capture import child_environment, pin_native_home
from .primary_transports import (
    NativeCommandPacketTransport,
    _command_cutoff,
    _run_bounded_command,
    normalize_native_provider_envelope,
)
from .workload_policy import WorkloadPolicyError, _canonical_repository_relative_path
from .zai_trial import ENDPOINT, redact_bound_key

GLM_MODEL = re.compile(r"^glm-[0-9a-z][0-9a-z.\-]{0,62}$")
MAX_TURNS_LIMIT = 100
MAX_TIMEOUT_SECONDS = 3_600
TOOL_GRANTS = frozenset({"READ_REPOSITORY", "RUN_TESTS", "PUBLIC_WEB_SEARCH", "WRITE_ISOLATED_BRANCH"})
# Fixed test-runner prefixes; a packet cannot name its own shell commands.
TEST_COMMAND_PREFIXES = ("python -m unittest", "python -m pytest", "pytest")
# Always denied, whatever the packet says.
SECRET_PATH_RULES = (".env", ".env.*", "**/.env", "**/.env.*", ".git/**")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/\-]+$")
IDENTITY_PROMPT = (
    "You are Z.ai (GLM) serving the Director Orchestrator as the zai lane. You are not "
    "Claude. Work only inside the current directory with the tools you were given. Never "
    "claim to have run a command or test you did not run. Report no external effects."
)


@dataclass(frozen=True)
class ZaiNativeConfig:
    """Trusted deployment facts for the lane; never taken from a work packet."""

    executable: Path
    api_key: str
    model: str
    max_turns: int
    timeout_seconds: int
    allow_repository_write: bool = False
    endpoint: str = ENDPOINT

    def validate(self) -> None:
        if self.endpoint != ENDPOINT:
            raise ValueError("the zai lane requires Z.ai's official endpoint")
        if not GLM_MODEL.fullmatch(self.model):
            raise ValueError("the zai lane requires an explicit GLM model")
        if not self.api_key or any(character.isspace() for character in self.api_key):
            raise ValueError("the zai lane requires one clean ZAI_API_KEY")
        path = Path(self.executable)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("the zai lane requires an absolute CLI executable")
        if isinstance(self.max_turns, bool) or not 1 <= self.max_turns <= MAX_TURNS_LIMIT:
            raise ValueError("the zai lane requires a finite turn limit")
        if isinstance(self.timeout_seconds, bool) or not 30 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("the zai lane requires a finite timeout")


def _path_rule(tool: str, path: str) -> tuple[str, ...]:
    return (f"{tool}(./{path})", f"{tool}(./{path}/**)")


def _scoped_paths(values: Any, field: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise RuntimeError(f"zai packet {field} are malformed")
    scoped = []
    for value in values:
        try:
            path = _canonical_repository_relative_path(value, field_name=field)
        except WorkloadPolicyError as exc:
            raise RuntimeError(f"zai packet {field} are not repository-relative") from exc
        if not _SAFE_PATH.fullmatch(path):
            # Permission rules are argv words; a space, comma or bracket could
            # widen or split a rule, so such a path is refused, not quoted.
            raise RuntimeError(f"zai packet {field} contain an unsupported character")
        scoped.append(path)
    return tuple(scoped)


def tool_arguments(work: Mapping[str, Any], context: ProviderAdapterContext, *,
                   allow_repository_write: bool) -> tuple[str, ...]:
    """CLI tool and permission arguments for one packet; refuses before launch."""

    grants = set(context.allowed_tools)
    if grants != set(work.get("allowed_tools") or ()):
        raise RuntimeError("zai tool grants differ between packet and dispatch context")
    if context.allowed_external_effects or work.get("allowed_external_effects"):
        raise RuntimeError("the zai lane performs no external effects; the Director's gateway owns them")
    unknown = grants - TOOL_GRANTS
    if unknown:
        raise RuntimeError("zai packet grants an unsupported tool: " + ",".join(sorted(unknown)))
    owned = _scoped_paths(work.get("owned_paths"), "owned_paths")
    prohibited = _scoped_paths(work.get("prohibited_paths"), "prohibited_paths")
    tools: list[str] = []
    allowed: list[str] = []
    if "READ_REPOSITORY" in grants:
        tools += ["Read", "Grep", "Glob"]
        allowed += ["Read", "Grep", "Glob"]
    if "RUN_TESTS" in grants:
        tools.append("Bash")
        allowed += [f"Bash({prefix}:*)" for prefix in TEST_COMMAND_PREFIXES]
    if "PUBLIC_WEB_SEARCH" in grants:
        tools += ["WebSearch", "WebFetch"]
        allowed += ["WebSearch", "WebFetch"]
    if "WRITE_ISOLATED_BRANCH" in grants:
        if not allow_repository_write:
            raise RuntimeError("zai repository writes are disabled for this deployment")
        if not owned:
            raise RuntimeError("zai repository writes require owned paths")
        tools += ["Edit", "Write"]
        allowed += [rule for path in owned for tool in ("Edit", "Write") for rule in _path_rule(tool, path)]
    denied = [f"{tool}(./{rule})" for rule in SECRET_PATH_RULES for tool in ("Read", "Edit", "Write")]
    denied += [rule for path in prohibited for tool in ("Read", "Edit", "Write") for rule in _path_rule(tool, path)]
    arguments = ["--tools", ",".join(tools)]
    if allowed:
        arguments += ["--allowedTools", *allowed]
    arguments += ["--disallowedTools", *denied]
    # "default" asks for anything not allowed above, and print mode cannot ask,
    # so every unlisted action is refused. Read-only work is planned, not edited.
    arguments += ["--permission-mode", "default" if "WRITE_ISOLATED_BRANCH" in grants else "plan"]
    return tuple(arguments)


def verify_terminal_envelope(native: Mapping[str, Any], *, model: str, max_turns: int) -> None:
    """Reject anything but one complete, successful run served by `model`."""

    if (native.get("type") != "result" or native.get("subtype") != "success"
            or native.get("is_error") is not False):
        raise RuntimeError("Z.ai envelope is not a successful terminal result")
    stop = native.get("stop_reason")
    if stop is not None and stop != "end_turn":
        raise RuntimeError("Z.ai envelope is truncated or incomplete")
    turns = native.get("num_turns")
    if turns is not None and (type(turns) is not int or not 1 <= turns <= max_turns):
        raise RuntimeError("Z.ai envelope exceeds its turn limit")
    models = native.get("modelUsage")
    if not isinstance(models, Mapping) or not models:
        raise RuntimeError("Z.ai envelope does not identify its serving model")
    if set(models) != {model}:
        raise RuntimeError("Z.ai envelope was served by an unexpected model")


def zai_usage(native: Mapping[str, Any], *, model: str) -> dict[str, Any] | None:
    """Token counts from the native envelope; cost is always UNKNOWN (null)."""

    usage = native.get("usage")
    if not isinstance(usage, Mapping):
        return None
    counts = [usage.get(name, 0 if name.startswith("cache_") else None) for name in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    return {"model": model, "input_tokens": counts[0], "output_tokens": counts[1],
            "total_tokens": sum(counts), "cost_usd": None}


class ZaiNativePacketTransport(NativeCommandPacketTransport):
    """The only transport that may serve the zai lane."""

    _zai_native = True

    def __init__(self, config: ZaiNativeConfig, *, cwd: str | Path) -> None:
        config.validate()
        executable = Path(config.executable).resolve(strict=True)
        super().__init__(
            "zai",
            (str(executable), "--print", "--output-format", "json", "--setting-sources", "",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
             "--max-turns", str(config.max_turns), "--model", config.model,
             "--append-system-prompt", IDENTITY_PROMPT),
            cwd=cwd,
            maximum_timeout_seconds=config.timeout_seconds,
        )
        self.config = config

    def _attempt_environment(self, home: str, context: ProviderAdapterContext) -> dict[str, str]:
        # The lane-minimal environment (no credentials), then a home and config
        # directory that exist only for this attempt.
        environment = child_environment()
        environment.update(self.environment)
        pin_native_home(environment, home)
        for name in [key for key in environment if key.upper().startswith(("ANTHROPIC_", "CLAUDE_"))
                     or key.upper() in {"HOMEDRIVE", "HOMEPATH"}]:
            environment.pop(name)
        model = self.config.model
        environment.update(
            ANTHROPIC_AUTH_TOKEN=self.config.api_key, ANTHROPIC_BASE_URL=self.config.endpoint,
            ANTHROPIC_MODEL=model, ANTHROPIC_DEFAULT_OPUS_MODEL=model,
            ANTHROPIC_DEFAULT_SONNET_MODEL=model, ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
            ANTHROPIC_SMALL_FAST_MODEL=model, CLAUDE_CONFIG_DIR=str(Path(home) / ".claude"),
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", CLAUDE_CODE_MAX_RETRIES="0",
            API_TIMEOUT_MS=str(int(min(context.timeout_seconds, self.config.timeout_seconds)) * 1000),
        )
        for name, field in (("DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD", "budget_usd"),
                            ("DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT", "total_token_limit")):
            value = getattr(context, field, None)
            environment.pop(name, None)
            if value is not None:
                environment[name] = str(value)
        return environment

    def send(self, work_packet_json: bytes, *, context: ProviderAdapterContext) -> Mapping[str, object]:
        if context.fencing_generation < 1:
            raise RuntimeError("native transport lacks primary writer fencing")
        cutoff = _command_cutoff(context, self.maximum_timeout_seconds, "Z.ai transport")
        work = json.loads(work_packet_json)
        # Every refusal below happens before launch: nothing reaches Z.ai.
        argv = [*self.command, *tool_arguments(
            work, context, allow_repository_write=self.config.allow_repository_write)]
        prompt = self._prompt(work_packet_json)
        with tempfile.TemporaryDirectory(prefix="director-zai-") as home:
            completed = _run_bounded_command(
                argv, cwd=self.cwd, environment=self._attempt_environment(home, context),
                input_bytes=prompt, cutoff=cutoff, what="Z.ai transport",
            )
        if completed.returncode != 0:
            raise RuntimeError("Z.ai command outcome is ambiguous")
        native = redact_bound_key(self._native_payload(completed.stdout), self.config.api_key)
        verify_terminal_envelope(native, model=self.config.model, max_turns=self.config.max_turns)
        result = normalize_native_provider_envelope("zai", work, native)
        if result["side_effects"]:
            raise RuntimeError("the zai lane reported external effects it may not perform")
        result["usage"] = zai_usage(native, model=self.config.model)
        if result["usage"] is None and result["status"] == "SUCCEEDED":
            result["status"] = "FAILED"
            result["blockers"] = [*result["blockers"], "Z.ai usage is UNKNOWN; not accepted as success"]
        result["risks"] = [*result["risks"], "Z.ai cost is UNKNOWN: the CLI prices with Anthropic tariffs"]
        result = _enforce_native_result_attempt_limits(result, context=context)
        observer = getattr(context, "execution_observer", None)
        if observer is not None:
            observer(self.provider_id,
                     completed.stdout.replace(self.config.api_key.encode("utf-8"), b"[REDACTED]"), result)
        return result
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
