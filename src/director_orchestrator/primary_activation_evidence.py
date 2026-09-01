# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | BEGIN]]
"""Durable, exact-identity activation evidence state machine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class ActivationEvidenceError(RuntimeError):
    pass


class ActivationStage(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    MIGRATED = "MIGRATED"
    BACKED_UP = "BACKED_UP"
    RESTORE_PROVED = "RESTORE_PROVED"
    ACTIVATED = "ACTIVATED"


_ORDER = tuple(ActivationStage)


@dataclass(frozen=True, slots=True)
class ActivationIdentity:
    candidate_commit: str
    candidate_tree: str
    profile_id: str
    compose_project: str
    database_identity: str
    postgres_schema: str
    checkpoint_namespace: str

    def __post_init__(self) -> None:
        if any(not value for value in asdict(self).values()):
            raise ActivationEvidenceError("activation identity is incomplete")
        if len(self.candidate_commit) != 40 or len(self.candidate_tree) != 40:
            raise ActivationEvidenceError("candidate identity must use exact Git object IDs")
        if not self.database_identity.startswith("postgres:sha256:"):
            raise ActivationEvidenceError("database identity is not canonical")

    @property
    def digest(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ActivationEvidenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ActivationEvidenceError("activation evidence is unreadable") from exc
        if not isinstance(value, dict) or value.get("version") != "primary-activation-evidence/v1":
            raise ActivationEvidenceError("activation evidence contract is invalid")
        return value

    def record(
        self,
        identity: ActivationIdentity,
        stage: ActivationStage,
        *,
        evidence_digest: str,
        facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        if len(evidence_digest) != 64 or any(c not in "0123456789abcdef" for c in evidence_digest):
            raise ActivationEvidenceError("stage evidence digest must be sha256 hex")
        current = self.load()
        if current is None:
            if stage is not ActivationStage.PREFLIGHT:
                raise ActivationEvidenceError("activation evidence must start at PREFLIGHT")
            current = {
                "version": "primary-activation-evidence/v1",
                "identity": asdict(identity),
                "identity_digest": identity.digest,
                "stages": [],
            }
        elif current.get("identity_digest") != identity.digest or current.get("identity") != asdict(identity):
            raise ActivationEvidenceError("activation evidence belongs to another candidate/profile/database")
        stages = current.get("stages")
        if not isinstance(stages, list):
            raise ActivationEvidenceError("activation stage history is malformed")
        expected_index = len(stages)
        if expected_index >= len(_ORDER) or _ORDER[expected_index] is not stage:
            raise ActivationEvidenceError("activation stages are missing, duplicated, or out of order")
        facts_dict = dict(facts)
        if stage is ActivationStage.RESTORE_PROVED:
            required = int(facts_dict.get("required_control_plane_tables", 0))
            checkpoint = int(facts_dict.get("checkpoint_tables", 0))
            schema_rows = int(facts_dict.get("schema_evidence_rows", 0))
            if required < 1 or checkpoint < 1 or schema_rows < 1:
                raise ActivationEvidenceError("restore proof lacks required nonzero schema evidence")
        if stage is ActivationStage.BACKED_UP and not facts_dict.get("backup_digest"):
            raise ActivationEvidenceError("backup evidence lacks its artifact digest")
        stages.append(
            {
                "stage": stage.value,
                "evidence_digest": evidence_digest,
                "recorded_at": datetime.now(UTC).isoformat(),
                "facts": facts_dict,
            }
        )
        self._atomic_write(current)
        return current

    def require_ready(self, identity: ActivationIdentity) -> dict[str, Any]:
        current = self.load()
        if current is None or current.get("identity_digest") != identity.digest:
            raise ActivationEvidenceError("activation evidence identity is unavailable or stale")
        stages = current.get("stages", [])
        expected = [stage.value for stage in _ORDER[:-1]]
        observed = [item.get("stage") for item in stages]
        if observed != expected:
            raise ActivationEvidenceError("activation prerequisites are incomplete")
        return current

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        handle, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _identity(arguments: argparse.Namespace) -> ActivationIdentity:
    return ActivationIdentity(
        candidate_commit=arguments.candidate_commit,
        candidate_tree=arguments.candidate_tree,
        profile_id=arguments.profile_id,
        compose_project=arguments.compose_project,
        database_identity=arguments.database_identity,
        postgres_schema=arguments.postgres_schema,
        checkpoint_namespace=arguments.checkpoint_namespace,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record", "require-ready"))
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--database-identity", required=True)
    parser.add_argument("--postgres-schema", required=True)
    parser.add_argument("--checkpoint-namespace", required=True)
    parser.add_argument("--stage", choices=tuple(stage.value for stage in ActivationStage))
    parser.add_argument("--evidence-digest")
    parser.add_argument("--facts-json", default="{}")
    arguments = parser.parse_args()
    store = ActivationEvidenceStore(arguments.artifact)
    identity = _identity(arguments)
    if arguments.command == "require-ready":
        store.require_ready(identity)
        print("activation_prerequisites_ready=true")
        return
    if arguments.stage is None or arguments.evidence_digest is None:
        parser.error("record requires --stage and --evidence-digest")
    facts = json.loads(arguments.facts_json)
    if not isinstance(facts, dict):
        parser.error("--facts-json must be an object")
    store.record(
        identity,
        ActivationStage(arguments.stage),
        evidence_digest=arguments.evidence_digest,
        facts=facts,
    )
    print("activation_stage_recorded=true")


if __name__ == "__main__":
    main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | END]]
