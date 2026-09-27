# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
"""Z.ai (GLM) production native lane: assignment-scoped, tool-enabled, fail closed.

Z.ai is reached by running the Claude Code executable against Z.ai's
Anthropic-compatible Coding Plan endpoint. The executable never makes the output
Claude's: this transport's lane is `zai`, its family is Z.ai and every result
must prove it was served by the configured GLM model.

What this adds to the shared `NativeCommandPacketTransport` path:

* Credential isolation. The child inherits no Anthropic, Claude or other lane
  credential; it receives only the configured ZAI_API_KEY (as the CLI's
  ANTHROPIC_AUTH_TOKEN), Z.ai's official endpoint and the GLM model names, in a
  throwaway home and config directory per attempt so no host settings, hooks,
  sessions or MCP servers are loaded. The host bridge route also pins the key's
  account by the SHA-256 of its key ID.
* Assignment-scoped tools. Packet tool grants map to a fixed, narrow set of
  Claude Code tools and permission rules; anything unrecognised is refused
  before launch. These are the CLI's own permission rules, not an
  operating-system sandbox: the CLI runs as an ordinary host process.
  Prohibited paths and secret files are denied; writes are confined to the
  packet's owned paths and exist only when the deployment enables them. The
  permission mode is never a bypass. Only client-side coding tools that the
  Coding Plan's documented Claude Code route executes locally are offered; a
  generic Claude tool name is not evidence that Z.ai supports it, so web search
  (an Anthropic server tool on this CLI) is not advertised.
* Finite execution. Print mode with a finite stdin file: the CLI can never wait
  on an interactive permission prompt. Turns are capped, the shared absolute
  cutoff and contained process tree apply, and a nonzero exit or late return
  stays ambiguous.
* Terminal and model verification. A missing, errored, truncated or over-turn
  envelope is rejected, and every model in `modelUsage` must be the configured
  GLM model; a mismatch is rejected rather than relabelled.
* Accounting honesty. Token counts come from the native envelope only; missing
  counts mean the result is never SUCCEEDED. Three quantities stay distinct and
  none is invented: incremental cash expense (`usage.cost_usd`, null because no
  charge is measured by this call), Coding Plan credits (not reported by the
  CLI; read from the account) and the CLI's model-equivalent dollar telemetry
  (Anthropic tariffs, not a charge), reported as a risk line only.

External effects are never performed here: branch and pull-request effects stay
with the Director's effect gateway, so a packet granting one is refused.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import Any, Mapping, Sequence

from .host_provider_bridge import _enforce_native_result_attempt_limits
from .langgraph_runtime import ProviderAdapterContext
from .native_acceptance_capture import child_environment, pin_native_home
from .packet_contracts import PacketContractError, validate_packet
from .primary_transports import (
    NativeCommandPacketTransport,
    _command_cutoff,
    _run_bounded_command,
    normalize_native_provider_envelope,
)
from .workload_policy import WorkloadPolicyError, _canonical_repository_relative_path
from .zai_trial import ENDPOINT, redact_bound_key

GLM_MODEL = re.compile(r"^glm-[0-9a-z][0-9a-z.\-]{0,62}$")
KEY_ID_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_TURNS_LIMIT = 100
MAX_TIMEOUT_SECONDS = 3_600
# Client-side Claude Code tools the Coding Plan route runs locally. Web search
# is deliberately absent: it is not qualified on Z.ai's endpoint.
TOOL_GRANTS = frozenset({"READ_REPOSITORY", "RUN_TESTS", "WRITE_ISOLATED_BRANCH"})
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
# The host bridge command for the lane: [executable, model, max turns, write
# mode, key-ID digest]. It is reported, signed, on the bridge challenge.
HOST_WRITE_MODES = {"read-only": False, "owned-path-writes": True}


def zai_key_id_digest(api_key: str) -> str | None:
    """SHA-256 of a Z.ai key's ID part (`<id>.<secret>`); the secret is never hashed."""

    key_id, separator, secret = api_key.partition(".")
    if (not separator or not key_id or not secret
            or any(character.isspace() for character in api_key)):
        return None
    return "sha256:" + hashlib.sha256(key_id.encode("utf-8")).hexdigest()


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
    # When set, the key must belong to this account's key ID.
    key_id_digest: str | None = None

    def validate(self) -> None:
        if self.endpoint != ENDPOINT:
            raise ValueError("the zai lane requires Z.ai's official endpoint")
        if not GLM_MODEL.fullmatch(self.model):
            raise ValueError("the zai lane requires an explicit GLM model")
        if not self.api_key or any(character.isspace() for character in self.api_key):
            raise ValueError("the zai lane requires one clean ZAI_API_KEY")
        if self.key_id_digest is not None and zai_key_id_digest(self.api_key) != self.key_id_digest:
            raise ValueError("the zai key does not belong to the pinned key ID")
        path = Path(self.executable)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("the zai lane requires an absolute CLI executable")
        if isinstance(self.max_turns, bool) or not 1 <= self.max_turns <= MAX_TURNS_LIMIT:
            raise ValueError("the zai lane requires a finite turn limit")
        if isinstance(self.timeout_seconds, bool) or not 30 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("the zai lane requires a finite timeout")


@dataclass(frozen=True)
class ZaiHostCommand:
    """The bridge command-map entry for the lane, parsed without any secret."""

    executable: str
    model: str
    max_turns: int
    allow_repository_write: bool
    key_id_digest: str

    @classmethod
    def parse(cls, argv: Sequence[str]) -> "ZaiHostCommand":
        if len(argv) != 5 or not all(isinstance(item, str) for item in argv):
            raise ValueError("Z.ai host command must be [executable, GLM model, max turns, write mode, key-ID digest]")
        executable, model, turns, write_mode, key_digest = argv
        if not (PureWindowsPath(executable).is_absolute() or PurePosixPath(executable).is_absolute()):
            raise ValueError("Z.ai host executable must be absolute")
        if not GLM_MODEL.fullmatch(model):
            raise ValueError("Z.ai host model must be a GLM model")
        if not re.fullmatch(r"[1-9][0-9]{0,2}", turns) or not 1 <= int(turns) <= MAX_TURNS_LIMIT:
            raise ValueError("Z.ai host turn limit is invalid")
        if write_mode not in HOST_WRITE_MODES:
            raise ValueError("Z.ai host write mode is invalid")
        if not KEY_ID_DIGEST.fullmatch(key_digest):
            raise ValueError("Z.ai host key-ID digest is invalid")
        return cls(executable, model, int(turns), HOST_WRITE_MODES[write_mode], key_digest)

    def config(self, api_key: str, *, timeout_seconds: int) -> ZaiNativeConfig | None:
        """The validated native configuration, or None when the host cannot serve it."""
        config = ZaiNativeConfig(
            executable=Path(self.executable), api_key=api_key, model=self.model,
            max_turns=self.max_turns, timeout_seconds=max(30, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS)),
            allow_repository_write=self.allow_repository_write, key_id_digest=self.key_id_digest)
        try:
            config.validate()
        except ValueError:
            return None
        return config


@dataclass(frozen=True)
class ZaiBridgeExpectation:
    """What the Linux Director requires of the bridged lane; no key, no CLI path."""

    model: str
    allow_repository_write: bool = False

    def matches(self, host_argv: Sequence[str] | None) -> bool:
        """Whether the signed host command serves exactly this GLM model."""
        if host_argv is None:
            return False
        try:
            command = ZaiHostCommand.parse(tuple(host_argv))
        except ValueError:
            return False
        return command.model == self.model


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
    # Plan mode executes nothing, so an approved test run (including in a review
    # assignment) needs "default": it runs exactly the allowed rules and, with
    # no terminal to ask in print mode, refuses every other action. Pure reads
    # stay in plan mode. Never a bypass mode.
    executes = bool(grants & {"RUN_TESTS", "WRITE_ISOLATED_BRANCH"})
    arguments += ["--permission-mode", "default" if executes else "plan"]
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
    """Token counts from the native envelope; cash expense is not measured (null)."""

    usage = native.get("usage")
    if not isinstance(usage, Mapping):
        return None
    counts = [usage.get(name, 0 if name.startswith("cache_") else None) for name in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    return {"model": model, "input_tokens": counts[0], "output_tokens": counts[1],
            "total_tokens": sum(counts), "cost_usd": None}


def accounting_notes(native: Mapping[str, Any]) -> list[str]:
    """Keep cash, plan credits and model-equivalent telemetry apart; invent none."""

    telemetry = native.get("total_cost_usd")
    if (isinstance(telemetry, (int, float)) and not isinstance(telemetry, bool)
            and math.isfinite(telemetry) and telemetry >= 0):
        equivalent = f"{telemetry} USD at Anthropic tariffs, reported by the CLI; not a charge"
    else:
        equivalent = "not reported"
    return [
        "Z.ai cash expense: not measured by this call (usage.cost_usd is null); "
        "confirm against the account after the call",
        "Z.ai Coding Plan credits: not reported by the CLI; read them from the account",
        "Z.ai model-equivalent telemetry: " + equivalent,
    ]


def zai_bridge_guard(work: Mapping[str, Any], context: ProviderAdapterContext, *,
                     allow_repository_write: bool) -> None:
    """Director-side refusals before anything is signed or sent to the bridge."""

    if getattr(context, "fencing_generation", 0) < 1:
        raise RuntimeError("Z.ai bridge dispatch lacks primary writer fencing")
    tool_arguments(work, context, allow_repository_write=allow_repository_write)


def normalize_zai_bridge_result(model: str, work: Mapping[str, Any],
                                returned: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only the host's already-verified Z.ai result for this exact packet."""

    try:
        result = validate_packet("result", dict(returned))
    except PacketContractError as exc:
        raise RuntimeError("Z.ai bridge result violates the result contract") from exc
    if result.get("provider") != "zai":
        raise RuntimeError("Z.ai bridge result names another provider identity")
    for field in ("task_id", "assignment_id", "idempotency_key", "authority_snapshot_digest", "correlation"):
        if result.get(field) != work.get(field):
            raise RuntimeError("Z.ai bridge result is not bound to this work packet")
    if result.get("side_effects"):
        raise RuntimeError("Z.ai bridge result reports external effects the lane may not perform")
    usage = result.get("usage")
    if usage is None:
        if result.get("status") == "SUCCEEDED":
            raise RuntimeError("Z.ai bridge result claims success with unknown usage")
    elif usage.get("model") != model:
        raise RuntimeError("Z.ai bridge result was served by an unexpected model")
    elif usage.get("cost_usd") is not None:
        raise RuntimeError("Z.ai bridge result carries a cash cost the route cannot measure")
    return dict(result)


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
        result["risks"] = [*result["risks"], *accounting_notes(native)]
        result = _enforce_native_result_attempt_limits(result, context=context)
        observer = getattr(context, "execution_observer", None)
        if observer is not None:
            observer(self.provider_id,
                     completed.stdout.replace(self.config.api_key.encode("utf-8"), b"[REDACTED]"), result)
        return result
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | END]]
