# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:09 UTC | BEGIN]]
"""Fail-closed production construction for Completion Gate worker tiers.

The orchestrator never interprets a command supplied by a request.  This module
registers a small fixed Python validation catalog and binds it either to a
separately installed audited low-privilege host broker or to an immutable,
network-disabled disposable container.  Construction grants evidence execution
only; it does not grant completion, merge, deployment, or external-write authority.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .completion_gates import ParameterProfile, RunnerDefinition, RunnerRegistry
from .completion_worker_backends import (
    AuditedHostWorkerBackend,
    DisposableContainerWorkerBackend,
    ExecutableBinding,
    SanitizedWorkerOutput,
)
from .completion_workers import (
    AuditedHostProfile,
    CompletionWorkerExecutor,
    CompletionWorkerRegistry,
    CompletionWorkerStore,
    DisposableContainerSpec,
    WorkerLimits,
    WorkerSpec,
    WorkerTier,
)
from .config import Settings


class ClosedVocabularyWorkerSanitizer:
    """Retain no subprocess prose and disclose only bounded structural evidence.

    Withholding is the privacy boundary: raw stdout/stderr are discarded before
    persistence while their length-delimited bytes enter a one-way digest. A
    small secret-pattern scan is defense in depth and never substitutes for
    withholding. The decisive output is a fixed closed-vocabulary token.
    """

    _secret_patterns = (
        re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(rb"(?i)\b(?:api[_ -]?key|access[_ -]?token|password)\s*[:=]\s*\S+"),
    )

    def sanitize(self, stdout: bytes, stderr: bytes) -> SanitizedWorkerOutput:
        digest = hashlib.sha256()
        digest.update(b"director-completion-worker-withheld-output/v1\0")
        digest.update(len(stdout).to_bytes(8, "big"))
        digest.update(stdout)
        digest.update(len(stderr).to_bytes(8, "big"))
        digest.update(stderr)
        secret_scan_passed = not any(
            pattern.search(stdout) or pattern.search(stderr)
            for pattern in self._secret_patterns
        )
        return SanitizedWorkerOutput(
            decisive_output="WORKER_OUTPUT_WITHHELD_BY_POLICY",
            output_digest="sha256:" + digest.hexdigest(),
            redaction_passed=True,
            secret_scan_passed=secret_scan_passed,
        )


@dataclass(frozen=True, slots=True)
class CompletionWorkerRuntime:
    runner_registry: RunnerRegistry
    worker_registry: CompletionWorkerRegistry
    executor: CompletionWorkerExecutor


def _runner_registry() -> RunnerRegistry:
    python = str(Path(sys.executable).resolve(strict=True))
    definitions = (
        RunnerDefinition.build(
            "python_compileall",
            python,
            (ParameterProfile.build("src-tests", ("-m", "compileall", "-q", "src", "tests")),),
            maximum_timeout_seconds=900,
            executable_version=f"python-{sys.version_info.major}.{sys.version_info.minor}",
        ),
        RunnerDefinition.build(
            "python_unittest",
            python,
            (
                ParameterProfile.build(
                    "discover-tests",
                    ("-m", "unittest", "discover", "-s", "tests", "-v"),
                ),
            ),
            maximum_timeout_seconds=900,
            executable_version=f"python-{sys.version_info.major}.{sys.version_info.minor}",
        ),
        RunnerDefinition.build(
            "pip_check",
            python,
            (ParameterProfile.build("environment", ("-m", "pip", "check")),),
            maximum_timeout_seconds=300,
            executable_version=f"python-{sys.version_info.major}.{sys.version_info.minor}",
        ),
    )
    return RunnerRegistry(definitions)


def _worker_specs(
    settings: Settings, runner_registry: RunnerRegistry
) -> tuple[WorkerSpec, ...]:
    limits = WorkerLimits(timeout_seconds=900)
    specs: list[WorkerSpec] = []
    for runner_id, profile_id in (
        ("python_compileall", "src-tests"),
        ("python_unittest", "discover-tests"),
        ("pip_check", "environment"),
    ):
        definition = runner_registry.definition(runner_id)
        profile = definition.profiles[profile_id]
        if "host" in settings.completion_gate_execution_tiers:
            specs.append(
                AuditedHostProfile.build(
                    spec_id=f"host.{runner_id}.{profile_id}",
                    runner_id=runner_id,
                    registry_digest=runner_registry.registry_digest,
                    runner_definition_digest=definition.definition_digest,
                    parameter_profile_id=profile_id,
                    parameter_profile_digest=profile.digest,
                    launcher_profile_id="director-completion-low-privilege",
                    fixed_argv=(definition.executable, *profile.argv),
                    limits=limits,
                )
            )
        # The shipped digest-pinned base image intentionally contains only
        # Python. Dependency-requiring unittest and pip checks stay on the
        # audited host until a separately reviewed worker image is configured.
        if (
            "container" in settings.completion_gate_execution_tiers
            and runner_id == "python_compileall"
        ):
            specs.append(
                DisposableContainerSpec.build(
                    spec_id=f"container.{runner_id}.{profile_id}",
                    runner_id=runner_id,
                    registry_digest=runner_registry.registry_digest,
                    runner_definition_digest=definition.definition_digest,
                    parameter_profile_id=profile_id,
                    parameter_profile_digest=profile.digest,
                    image=settings.completion_gate_container_image,
                    fixed_argv=("python", *profile.argv),
                    limits=limits,
                )
            )
    return tuple(specs)


def build_completion_worker_runtime(settings: Settings) -> CompletionWorkerRuntime:
    """Construct configured tiers only after Settings has failed closed."""

    readiness = settings.completion_gate_readiness()
    if not readiness["worker_execution_active"]:
        raise ValueError("Completion Gate worker execution is not active")
    runner_registry = _runner_registry()
    worker_registry = CompletionWorkerRegistry(_worker_specs(settings, runner_registry))
    sanitizer = ClosedVocabularyWorkerSanitizer()
    backends = {}
    if "host" in settings.completion_gate_execution_tiers:
        backends[WorkerTier.AUDITED_HOST] = AuditedHostWorkerBackend(
            ExecutableBinding.create(settings.completion_gate_host_broker_path),
            sanitizer,
        )
    if "container" in settings.completion_gate_execution_tiers:
        backends[WorkerTier.DISPOSABLE_CONTAINER] = DisposableContainerWorkerBackend(
            ExecutableBinding.create(settings.completion_gate_container_runtime_path),
            sanitizer,
        )
    executor = CompletionWorkerExecutor(
        worker_registry,
        CompletionWorkerStore(settings.completion_gate_worker_store_path),
        backends,
        enabled=True,
        reserved_database_paths=(
            settings.sqlite_path,
            settings.langgraph_checkpoint_path,
            settings.langgraph_checkpoint_path + ".resume-claims.sqlite3",
            settings.langgraph_runtime_checkpoint_path,
            settings.langgraph_runtime_ledger_path,
            settings.completion_gate_store_path,
        ),
    )
    if not executor.readiness()["ready"]:
        raise ValueError("Completion Gate worker backends are not ready")
    return CompletionWorkerRuntime(runner_registry, worker_registry, executor)
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:09 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
