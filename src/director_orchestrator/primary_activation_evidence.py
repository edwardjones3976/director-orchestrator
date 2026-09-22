# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | BEGIN]]
"""Durable, exact-identity activation evidence state machine."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


ACCEPTANCE_ARTIFACT_VERSION = "director-primary-acceptance-artifact/v1"
ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION = (
    "director-primary-acceptance-artifact/owner-local-evidence-v1"
)
OWNER_LOCAL_EVIDENCE_ACCEPTANCE_MODE = "owner-local-evidence-v1"
OWNER_LOCAL_ADOPTION_VERSION = "director-primary-local-evidence-adoption/v1"
OWNER_LOCAL_ADOPTION_DECISION = "ADOPT_SCOPED_LOCAL_EVIDENCE"
OWNER_LOCAL_CI_ISSUER = "owner-local-evidence-adoption"
HOSTED_CI_NOT_EXECUTED = "HOSTED_NOT_EXECUTED"
_OWNER_LOCAL_ADOPTION_DOMAIN = (
    b"director-primary-local-evidence-adoption/v1\0"
)
_OWNER_LOCAL_EXTRA_FACT_NAMES = frozenset(
    {
        "hosted_ci_failure_cause",
        "local_qa_evidence_digest",
        "reviewed_source_commit",
        "reviewed_source_tree",
        "owner_local_evidence_adoption_digest",
        "unchanged_source_reconciliation_digest",
    }
)
_ACCEPTANCE_SECRET_ENVIRONMENTS = {
    "ci": "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
    "independent_review": "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
    "security": "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
}
_ACCEPTANCE_FACT_NAMES = {
    "candidate_commit",
    "candidate_tree",
    "ci_conclusion",
    "ci_evidence_digest",
    "ci_required_check_count",
    "ci_run_ids",
    "independent_review_evidence_digest",
    "independent_review_verdict",
    "independent_reviewed_commit",
    "independent_reviewer",
    "pull_request_author",
    "remote_head_commit",
    "security_evidence_digest",
    "security_reviewed_commit",
    "security_reviewer",
    "security_verdict",
}

_OWNER_LOCAL_FACT_NAMES = _ACCEPTANCE_FACT_NAMES | _OWNER_LOCAL_EXTRA_FACT_NAMES


class ActivationEvidenceError(RuntimeError):
    pass


class ActivationStage(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    ACCEPTED = "ACCEPTED"
    MIGRATED = "MIGRATED"
    BACKED_UP = "BACKED_UP"
    RESTORE_PROVED = "RESTORE_PROVED"
    ACTIVATED = "ACTIVATED"


_ORDER = tuple(ActivationStage)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActivationEvidenceError("activation facts are not canonical JSON") from exc


def canonical_facts_digest(facts: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(facts))).hexdigest()


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise ActivationEvidenceError("canonical artifact contains duplicate fields")
        value[name] = item
    return value


def unchanged_source_reconciliation_digest(
    *,
    reviewed_source_commit: str,
    reviewed_source_tree: str,
    activation_candidate_commit: str,
    activation_candidate_tree: str,
    pinned_evidence: object,
) -> str:
    payload = {
        "activation_candidate_commit": activation_candidate_commit,
        "activation_candidate_tree": activation_candidate_tree,
        "pinned_evidence": pinned_evidence,
        "reviewed_source_commit": reviewed_source_commit,
        "reviewed_source_tree": reviewed_source_tree,
    }
    return canonical_facts_digest(payload)


def hosted_ci_absence_digest(*, failure_cause: str, required_check_count: int) -> str:
    return canonical_facts_digest(
        {
            "failure_cause": failure_cause,
            "hosted_ci_conclusion": HOSTED_CI_NOT_EXECUTED,
            "required_check_count": required_check_count,
        }
    )


def _owner_local_adoption_signature(owner_token: str, document: Mapping[str, Any]) -> str:
    return hmac.new(
        owner_token.encode("utf-8"),
        _OWNER_LOCAL_ADOPTION_DOMAIN + _canonical_json(dict(document)),
        hashlib.sha256,
    ).hexdigest()


def load_pinned_owner_local_adoption(
    path: str | Path,
    *,
    expected_sha256: str,
    owner_token: str,
) -> dict[str, Any]:
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ActivationEvidenceError("owner-local evidence adoption pin is invalid")
    if len(owner_token) < 32:
        raise ActivationEvidenceError("owner-local evidence adoption authority is unavailable")
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ActivationEvidenceError("owner-local evidence adoption is unreadable") from exc
    if len(raw) > 256 * 1024:
        raise ActivationEvidenceError("owner-local evidence adoption is oversized")
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(observed_sha256, expected_sha256.lower()):
        raise ActivationEvidenceError("owner-local evidence adoption pin mismatch")
    try:
        envelope = json.loads(raw, object_pairs_hook=_closed_json_object)
    except json.JSONDecodeError as exc:
        raise ActivationEvidenceError("owner-local evidence adoption is invalid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"document", "signature"}:
        raise ActivationEvidenceError("owner-local evidence adoption contract is invalid")
    document = envelope.get("document")
    signature = envelope.get("signature")
    if not isinstance(document, dict) or not isinstance(signature, str):
        raise ActivationEvidenceError("owner-local evidence adoption contract is invalid")
    expected_document_names = {
        "decision",
        "hosted_ci_failure_cause",
        "independent_review_evidence_digest",
        "independent_reviewer",
        "local_qa_evidence_digest",
        "pinned_evidence",
        "pull_request_author",
        "pull_request_number",
        "repository",
        "reviewed_source_commit",
        "reviewed_source_tree",
        "security_evidence_digest",
        "security_reviewer",
        "version",
    }
    if set(document) != expected_document_names:
        raise ActivationEvidenceError("owner-local evidence adoption document is invalid")
    if document.get("version") != OWNER_LOCAL_ADOPTION_VERSION:
        raise ActivationEvidenceError("owner-local evidence adoption version is invalid")
    if document.get("decision") != OWNER_LOCAL_ADOPTION_DECISION:
        raise ActivationEvidenceError("owner-local evidence adoption decision is invalid")
    for field in ("reviewed_source_commit", "reviewed_source_tree"):
        value = document.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ActivationEvidenceError("owner-local evidence adoption source identity is invalid")
    repository = document.get("repository")
    if (
        not isinstance(repository, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
    ):
        raise ActivationEvidenceError("owner-local evidence adoption repository is invalid")
    pull_request_number = document.get("pull_request_number")
    if (
        isinstance(pull_request_number, bool)
        or not isinstance(pull_request_number, int)
        or pull_request_number < 1
    ):
        raise ActivationEvidenceError("owner-local evidence adoption pull request is invalid")
    failure_cause = document.get("hosted_ci_failure_cause")
    if not isinstance(failure_cause, str) or not failure_cause.strip():
        raise ActivationEvidenceError("hosted CI failure cause is required")
    pinned_evidence = document.get("pinned_evidence")
    if not isinstance(pinned_evidence, list) or not pinned_evidence:
        raise ActivationEvidenceError("owner-local evidence adoption lacks pinned artifacts")
    for item in pinned_evidence:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ActivationEvidenceError("owner-local evidence adoption pins are invalid")
        digest = item.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ActivationEvidenceError("owner-local evidence adoption pins are invalid")
        if not isinstance(item.get("path"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item["path"]):
            raise ActivationEvidenceError("owner-local evidence adoption pins are invalid")
        evidence = source.parent / item["path"]
        try:
            if evidence.resolve().parent != source.parent.resolve() or evidence.stat().st_size > 8 * 1024 * 1024:
                raise ActivationEvidenceError("adopted evidence is outside its bundle or oversized")
            with evidence.open("rb") as stream:
                observed = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise ActivationEvidenceError("adopted evidence is unavailable") from exc
        if not hmac.compare_digest(observed, digest):
            raise ActivationEvidenceError("adopted evidence bytes changed")
    for name in (
        "security_evidence_digest",
        "independent_review_evidence_digest",
        "local_qa_evidence_digest",
    ):
        value = document.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ActivationEvidenceError("owner-local evidence adoption digests are invalid")
        if value not in {item["sha256"] for item in pinned_evidence}:
            raise ActivationEvidenceError("required review or QA evidence is not pinned")
    for name in ("security_reviewer", "independent_reviewer", "pull_request_author"):
        value = document.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ActivationEvidenceError("owner-local evidence adoption actors are invalid")
    security_actor = str(document["security_reviewer"])
    independent_actor = str(document["independent_reviewer"])
    pull_request_author = str(document["pull_request_author"])
    if (
        hmac.compare_digest(security_actor.casefold(), independent_actor.casefold())
        or hmac.compare_digest(security_actor.casefold(), pull_request_author.casefold())
        or hmac.compare_digest(independent_actor.casefold(), pull_request_author.casefold())
    ):
        raise ActivationEvidenceError(
            "owner-local evidence adoption requires distinct attributable identities"
        )
    expected_signature = _owner_local_adoption_signature(owner_token, document)
    if not hmac.compare_digest(signature, expected_signature):
        raise ActivationEvidenceError("owner-local evidence adoption signature is invalid")
    return {
        **document,
        "adoption_digest": observed_sha256,
        "canonical_document": dict(document),
    }


def owner_local_acceptance_facts(
    identity: ActivationIdentity,
    adoption: Mapping[str, Any],
    *,
    repository: str,
    pull_request_number: int,
    required_check_count: int = 6,
) -> dict[str, Any]:
    if repository != adoption["repository"] or pull_request_number != adoption["pull_request_number"] or required_check_count != 6:
        raise ActivationEvidenceError("owner-local acceptance target or baseline checks differ")
    failure_cause = str(adoption["hosted_ci_failure_cause"]).strip()
    reviewed_source_commit = str(adoption["reviewed_source_commit"])
    reviewed_source_tree = str(adoption["reviewed_source_tree"])
    reconciliation = unchanged_source_reconciliation_digest(
        reviewed_source_commit=reviewed_source_commit,
        reviewed_source_tree=reviewed_source_tree,
        activation_candidate_commit=identity.candidate_commit,
        activation_candidate_tree=identity.candidate_tree,
        pinned_evidence=adoption["pinned_evidence"],
    )
    return {
        "candidate_commit": identity.candidate_commit,
        "candidate_tree": identity.candidate_tree,
        "ci_conclusion": HOSTED_CI_NOT_EXECUTED,
        "ci_evidence_digest": hosted_ci_absence_digest(
            failure_cause=failure_cause,
            required_check_count=required_check_count,
        ),
        "ci_required_check_count": required_check_count,
        "ci_run_ids": [],
        "hosted_ci_failure_cause": failure_cause,
        "local_qa_evidence_digest": adoption["local_qa_evidence_digest"],
        "independent_review_evidence_digest": adoption["independent_review_evidence_digest"],
        "independent_review_verdict": "ACCEPT",
        "independent_reviewed_commit": identity.candidate_commit,
        "independent_reviewer": adoption["independent_reviewer"],
        "owner_local_evidence_adoption_digest": adoption["adoption_digest"],
        "pull_request_author": adoption["pull_request_author"],
        "remote_head_commit": identity.candidate_commit,
        "reviewed_source_commit": reviewed_source_commit,
        "reviewed_source_tree": reviewed_source_tree,
        "security_evidence_digest": adoption["security_evidence_digest"],
        "security_reviewed_commit": identity.candidate_commit,
        "security_reviewer": adoption["security_reviewer"],
        "security_verdict": "ACCEPT",
        "unchanged_source_reconciliation_digest": reconciliation,
    }


def _acceptance_issuer(kind: str, facts: Mapping[str, Any]) -> str:
    if kind == "ci":
        return "github-actions"
    field = "security_reviewer" if kind == "security" else "independent_reviewer"
    issuer = facts.get(field)
    if not isinstance(issuer, str) or not issuer:
        raise ActivationEvidenceError("acceptance artifact issuer is invalid")
    return issuer


def _acceptance_signature(
    kind: str, issuer: str, secret: str, payload: bytes
) -> str:
    domain = f"director-primary-acceptance/{kind}/{issuer}/v1\0".encode("ascii")
    return hmac.new(secret.encode("utf-8"), domain + payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class ActivationIdentity:
    candidate_commit: str
    candidate_tree: str
    profile_id: str
    compose_project: str
    database_identity: str
    database_instance_id: str
    postgres_schema: str
    checkpoint_namespace: str

    def __post_init__(self) -> None:
        if any(not value for value in asdict(self).values()):
            raise ActivationEvidenceError("activation identity is incomplete")
        if len(self.candidate_commit) != 40 or len(self.candidate_tree) != 40:
            raise ActivationEvidenceError("candidate identity must use exact Git object IDs")
        if not self.database_identity.startswith("postgres:sha256:"):
            raise ActivationEvidenceError("database identity is not canonical")
        try:
            uuid.UUID(self.database_instance_id)
        except ValueError as exc:
            raise ActivationEvidenceError("database instance identity is invalid") from exc

    @property
    def digest(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ActivationEvidenceStore:
    def __init__(
        self,
        path: str | Path,
        *,
        acceptance_secrets: Mapping[str, str] | None = None,
        read_only: bool = False,
    ) -> None:
        from .acceptance_journal import journal_from_environment
        self._read_only = read_only
        self._journal = journal_from_environment(read_only=read_only)
        self._revision = None
        if os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE") == OWNER_LOCAL_EVIDENCE_ACCEPTANCE_MODE:
            generation_journal = os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH", "")
            if not generation_journal or not Path(generation_journal).is_absolute() or Path(generation_journal).suffix != ".sqlite3":
                raise ActivationEvidenceError("owner-local acceptance requires a selected generation journal")
            path = Path(generation_journal).with_suffix(".evidence.json")
        self.path = Path(path).resolve()
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acceptance_secrets = (
            dict(acceptance_secrets) if acceptance_secrets is not None else None
        )
        self._owner_local_adoption = owner_local_adoption_from_environment()

    def load(self) -> dict[str, Any] | None:
        if self._journal is not None:
            self._revision, value = self._journal.load()
            if value is not None:
                self._validate_history(value)
            return value
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ActivationEvidenceError("activation evidence is unreadable") from exc
        if not isinstance(value, dict) or value.get("version") != "primary-activation-evidence/v1":
            raise ActivationEvidenceError("activation evidence contract is invalid")
        self._validate_history(value)
        return value

    def record(
        self,
        identity: ActivationIdentity,
        stage: ActivationStage,
        *,
        evidence_digest: str,
        facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        if stage is ActivationStage.ACCEPTED:
            raise ActivationEvidenceError(
                "ACCEPTED requires a verified canonical acceptance artifact"
            )
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
        expected_evidence_digest = canonical_facts_digest(facts_dict)
        if not hmac.compare_digest(evidence_digest, expected_evidence_digest):
            raise ActivationEvidenceError(
                "stage evidence digest does not match canonical facts"
            )
        backup_digest = facts_dict.get("backup_digest")
        if stage in (ActivationStage.BACKED_UP, ActivationStage.RESTORE_PROVED):
            self._require_artifact_digest(backup_digest)
        if stage is ActivationStage.RESTORE_PROVED:
            expected_backup_digest = self._backed_up_digest(stages)
            if not hmac.compare_digest(str(backup_digest), expected_backup_digest):
                raise ActivationEvidenceError(
                    "restore dump does not match the durable backup artifact"
                )
            required = int(facts_dict.get("required_control_plane_tables", 0))
            checkpoint = int(facts_dict.get("checkpoint_tables", 0))
            schema_rows = int(facts_dict.get("schema_evidence_rows", 0))
            if required < 17 or checkpoint != 4 or schema_rows < 1:
                raise ActivationEvidenceError(
                    "restore proof requires at least 17 control-plane tables, exactly 4 "
                    "checkpoint tables, and nonzero schema evidence"
                )
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

    def record_acceptance_artifact(
        self,
        identity: ActivationIdentity,
        artifact_path: str | Path,
    ) -> dict[str, Any]:
        if self._journal is not None:
            current = self.load()
            if current is None or current.get("identity") != asdict(identity):
                raise ActivationEvidenceError("v2 acceptance requires matching preflight")
            return self._journal.accept(self._revision, current, Path(artifact_path).read_bytes())
        artifact = self._load_acceptance_artifact(artifact_path)
        facts_digest = self._validate_acceptance_artifact(identity, artifact)
        facts = artifact["facts"]

        current = self.load()
        if current is None:
            raise ActivationEvidenceError("activation evidence must start at PREFLIGHT")
        if current.get("identity_digest") != identity.digest or current.get("identity") != asdict(identity):
            raise ActivationEvidenceError("activation evidence belongs to another candidate/profile/database")
        stages = current.get("stages")
        if not isinstance(stages, list) or len(stages) != 1 or stages[0].get("stage") != ActivationStage.PREFLIGHT.value:
            raise ActivationEvidenceError("activation stages are missing, duplicated, or out of order")
        stages.append(
            {
                "stage": ActivationStage.ACCEPTED.value,
                "evidence_digest": facts_digest,
                "recorded_at": datetime.now(UTC).isoformat(),
                "facts": facts,
                "source_artifact": artifact,
                "source_artifact_digest": hashlib.sha256(
                    _canonical_json(artifact)
                ).hexdigest(),
            }
        )
        self._atomic_write(current)
        return current

    @staticmethod
    def _load_acceptance_artifact(path: str | Path) -> dict[str, Any]:
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise ActivationEvidenceError("canonical acceptance artifact is unreadable") from exc
        if len(raw) > 256 * 1024:
            raise ActivationEvidenceError("canonical acceptance artifact is oversized")
        try:
            artifact = json.loads(raw, object_pairs_hook=_closed_json_object)
        except json.JSONDecodeError as exc:
            raise ActivationEvidenceError("canonical acceptance artifact is invalid JSON") from exc
        ActivationEvidenceStore._validate_acceptance_artifact_contract(artifact)
        return artifact

    @staticmethod
    def _validate_acceptance_artifact_contract(artifact: object) -> None:
        expected_names = {
            "attestations", "facts", "facts_digest", "pull_request_number",
            "repository", "version",
        }
        if not isinstance(artifact, dict) or set(artifact) != expected_names:
            raise ActivationEvidenceError("canonical acceptance artifact contract is invalid")
        version = artifact.get("version")
        if version not in {ACCEPTANCE_ARTIFACT_VERSION, ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION}:
            raise ActivationEvidenceError("canonical acceptance artifact version is invalid")
        if not isinstance(artifact.get("facts"), dict):
            raise ActivationEvidenceError("canonical acceptance artifact facts are invalid")
        if (
            not isinstance(artifact.get("repository"), str)
            or re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", artifact["repository"]
            )
            is None
        ):
            raise ActivationEvidenceError("acceptance artifact repository is invalid")
        pull_request_number = artifact.get("pull_request_number")
        if isinstance(pull_request_number, bool) or not isinstance(pull_request_number, int) or pull_request_number < 1:
            raise ActivationEvidenceError("acceptance artifact pull request is invalid")
        if not isinstance(artifact.get("facts_digest"), str):
            raise ActivationEvidenceError("acceptance artifact facts digest is invalid")
        attestations = artifact.get("attestations")
        expected_attestations = {"owner_adoption"} if version == ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION else set(_ACCEPTANCE_SECRET_ENVIRONMENTS)
        if not isinstance(attestations, dict) or set(attestations) != expected_attestations:
            raise ActivationEvidenceError("acceptance artifact attestations are invalid")
        for kind, value in attestations.items():
            if not isinstance(value, dict) or set(value) != {"issuer", "signature"}:
                raise ActivationEvidenceError("acceptance artifact attestations are invalid")
            issuer = value.get("issuer")
            signature = value.get("signature")
            if (
                not isinstance(issuer, str)
                or not issuer
                or not isinstance(signature, str)
                or len(signature) != 64
                or any(character not in "0123456789abcdef" for character in signature)
            ):
                raise ActivationEvidenceError("acceptance artifact attestations are invalid")

    def _validate_history(self, value: Mapping[str, Any]) -> None:
        identity_value = value.get("identity")
        if not isinstance(identity_value, dict):
            raise ActivationEvidenceError("activation evidence identity is malformed")
        try:
            identity = ActivationIdentity(**identity_value)
        except (TypeError, ActivationEvidenceError) as exc:
            raise ActivationEvidenceError("activation evidence identity is malformed") from exc
        observed_identity_digest = value.get("identity_digest")
        if (
            not isinstance(observed_identity_digest, str)
            or not hmac.compare_digest(observed_identity_digest, identity.digest)
        ):
            raise ActivationEvidenceError("activation evidence identity digest is invalid")
        stages = value.get("stages")
        if not isinstance(stages, list):
            raise ActivationEvidenceError("activation stage history is malformed")
        for item in stages:
            if not isinstance(item, dict) or not isinstance(item.get("facts"), dict):
                raise ActivationEvidenceError("activation stage history is malformed")
            observed = item.get("evidence_digest")
            expected = canonical_facts_digest(item["facts"])
            if not isinstance(observed, str) or not hmac.compare_digest(observed, expected):
                raise ActivationEvidenceError(
                    "activation stage evidence digest does not match canonical facts"
                )
            if item.get("stage") == ActivationStage.ACCEPTED.value:
                if self._journal is not None:
                    self._journal.verify_record(identity, item)
                    continue
                artifact = item.get("source_artifact")
                if not isinstance(artifact, dict) or artifact.get("facts") != item["facts"]:
                    raise ActivationEvidenceError(
                        "acceptance artifact was substituted or removed"
                    )
                if artifact.get("facts_digest") != observed:
                    raise ActivationEvidenceError(
                        "acceptance artifact digest does not match recorded facts"
                    )
                artifact_digest = item.get("source_artifact_digest")
                expected_artifact_digest = hashlib.sha256(
                    _canonical_json(artifact)
                ).hexdigest()
                if (
                    not isinstance(artifact_digest, str)
                    or not hmac.compare_digest(
                        artifact_digest, expected_artifact_digest
                    )
                ):
                    raise ActivationEvidenceError(
                        "acceptance artifact was mutated or substituted"
                    )
                verified_facts_digest = self._validate_acceptance_artifact(
                    identity, artifact
                )
                if not hmac.compare_digest(verified_facts_digest, observed):
                    raise ActivationEvidenceError(
                        "acceptance artifact does not match durable stage evidence"
                    )

    def _validate_acceptance_artifact(
        self, identity: ActivationIdentity, artifact: Mapping[str, Any]
    ) -> str:
        self._validate_acceptance_artifact_contract(artifact)
        facts = artifact.get("facts")
        if not isinstance(facts, dict):
            raise ActivationEvidenceError("canonical acceptance artifact facts are invalid")
        artifact_version = str(artifact.get("version"))
        if artifact_version == ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION:
            self._validate_owner_local_acceptance(identity, facts)
        else:
            self._validate_acceptance(identity, facts)
        unsigned = {
            name: artifact.get(name)
            for name in (
                "facts",
                "facts_digest",
                "pull_request_number",
                "repository",
                "version",
            )
        }
        facts_digest = canonical_facts_digest(facts)
        artifact_facts_digest = artifact.get("facts_digest")
        if (
            not isinstance(artifact_facts_digest, str)
            or not hmac.compare_digest(artifact_facts_digest, facts_digest)
        ):
            raise ActivationEvidenceError(
                "acceptance artifact facts digest does not match canonical facts"
            )
        supplied_secrets = self._acceptance_secrets
        if supplied_secrets is None:
            raise ActivationEvidenceError(
                "accepted evidence requires durable attestation verifier secrets"
            )
        local = artifact_version == ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION
        roles = ("owner_adoption",) if local else tuple(_ACCEPTANCE_SECRET_ENVIRONMENTS)
        if local and (artifact["repository"] != self._owner_local_adoption["repository"] or artifact["pull_request_number"] != self._owner_local_adoption["pull_request_number"]):
            raise ActivationEvidenceError("acceptance artifact differs from the owner-adopted target")
        relevant_secrets = {kind: supplied_secrets.get(kind) for kind in roles}
        if any(
            not isinstance(secret, str) or len(secret) < 32
            for secret in relevant_secrets.values()
        ):
            raise ActivationEvidenceError(
                "acceptance artifact verification authority is unavailable"
            )
        if len(set(relevant_secrets.values())) != len(roles):
            raise ActivationEvidenceError(
                "acceptance artifact verification authorities must be distinct"
            )
        attestations = artifact.get("attestations")
        if not isinstance(attestations, dict):
            raise ActivationEvidenceError("acceptance artifact attestations are invalid")
        payload = _canonical_json(unsigned)
        for kind, secret in relevant_secrets.items():
            attestation = attestations.get(kind)
            if not isinstance(attestation, dict):
                raise ActivationEvidenceError(
                    f"acceptance artifact {kind} attestation is invalid"
                )
            issuer = OWNER_LOCAL_CI_ISSUER if local else _acceptance_issuer(kind, facts)
            if attestation.get("issuer") != issuer:
                raise ActivationEvidenceError(
                    f"acceptance artifact {kind} issuer is invalid"
                )
            observed = attestation.get("signature")
            expected = _acceptance_signature(kind, issuer, str(secret), payload)
            if not isinstance(observed, str) or not hmac.compare_digest(
                observed, expected
            ):
                raise ActivationEvidenceError(
                    f"acceptance artifact {kind} attestation is invalid"
                )
        return facts_digest

    def _validate_owner_local_acceptance(
        self, identity: ActivationIdentity, facts: Mapping[str, Any]
    ) -> None:
        if os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1") != OWNER_LOCAL_EVIDENCE_ACCEPTANCE_MODE:
            raise ActivationEvidenceError("owner-local evidence requires explicit mode selection")
        if set(facts) != _OWNER_LOCAL_FACT_NAMES:
            raise ActivationEvidenceError("acceptance artifact facts contract is invalid")
        if facts.get("ci_conclusion") != HOSTED_CI_NOT_EXECUTED:
            raise ActivationEvidenceError("hosted CI failure was not preserved explicitly")
        run_ids = facts.get("ci_run_ids")
        if not isinstance(run_ids, list) or run_ids:
            raise ActivationEvidenceError("hosted CI run identity must remain absent")
        check_count = facts.get("ci_required_check_count")
        if isinstance(check_count, bool) or not isinstance(check_count, int) or check_count != 6:
            raise ActivationEvidenceError("hosted CI required check count is invalid")
        failure_cause = facts.get("hosted_ci_failure_cause")
        if not isinstance(failure_cause, str) or not failure_cause.strip():
            raise ActivationEvidenceError("hosted CI failure cause is required")
        expected_ci_digest = hosted_ci_absence_digest(
            failure_cause=failure_cause.strip(),
            required_check_count=check_count,
        )
        ci_digest = facts.get("ci_evidence_digest")
        if not isinstance(ci_digest, str) or not hmac.compare_digest(ci_digest, expected_ci_digest):
            raise ActivationEvidenceError("hosted CI absence digest is invalid")
        exact_fields = {
            "candidate_commit": identity.candidate_commit,
            "candidate_tree": identity.candidate_tree,
            "remote_head_commit": identity.candidate_commit,
            "security_reviewed_commit": identity.candidate_commit,
            "independent_reviewed_commit": identity.candidate_commit,
        }
        if any(facts.get(name) != expected for name, expected in exact_fields.items()):
            raise ActivationEvidenceError(
                "acceptance evidence is not bound to the exact candidate"
            )
        if facts.get("security_verdict") != "ACCEPT":
            raise ActivationEvidenceError("security review has not accepted the exact head")
        if facts.get("independent_review_verdict") != "ACCEPT":
            raise ActivationEvidenceError(
                "independent review has not accepted the exact head"
            )
        security_actor = facts.get("security_reviewer")
        independent_actor = facts.get("independent_reviewer")
        pull_request_author = facts.get("pull_request_author")
        if (
            not isinstance(security_actor, str)
            or not security_actor.strip()
            or not isinstance(independent_actor, str)
            or not independent_actor.strip()
            or hmac.compare_digest(security_actor.casefold(), independent_actor.casefold())
            or not isinstance(pull_request_author, str)
            or not pull_request_author.strip()
            or hmac.compare_digest(security_actor.casefold(), pull_request_author.casefold())
            or hmac.compare_digest(independent_actor.casefold(), pull_request_author.casefold())
        ):
            raise ActivationEvidenceError(
                "producer and acceptance actors require distinct attributable identities"
            )
        for name in (
            "security_evidence_digest",
            "independent_review_evidence_digest",
            "owner_local_evidence_adoption_digest",
            "unchanged_source_reconciliation_digest",
        ):
            value = facts.get(name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ActivationEvidenceError(
                    "acceptance evidence lacks exact artifact digests"
                )
        reviewed_source_commit = facts.get("reviewed_source_commit")
        reviewed_source_tree = facts.get("reviewed_source_tree")
        if (
            not isinstance(reviewed_source_commit, str)
            or len(reviewed_source_commit) != 40
            or not isinstance(reviewed_source_tree, str)
            or len(reviewed_source_tree) != 40
        ):
            raise ActivationEvidenceError("reviewed source identity is invalid")
        # Recheck retained evidence on replay, including within an existing store.
        adoption = owner_local_adoption_from_environment()
        self._owner_local_adoption = adoption
        if adoption is None:
            raise ActivationEvidenceError("owner-local evidence adoption is unavailable")
        adoption_digest = facts.get("owner_local_evidence_adoption_digest")
        if not hmac.compare_digest(str(adoption_digest), str(adoption["adoption_digest"])):
            raise ActivationEvidenceError("owner-local evidence adoption digest mismatch")
        for field in (
            "hosted_ci_failure_cause",
            "local_qa_evidence_digest",
            "independent_review_evidence_digest",
            "independent_reviewer",
            "pull_request_author",
            "security_evidence_digest",
            "security_reviewer",
        ):
            if facts.get(field) != adoption.get(field):
                raise ActivationEvidenceError("owner-local evidence adoption facts mismatch")
        if facts.get("reviewed_source_commit") != adoption.get("reviewed_source_commit"):
            raise ActivationEvidenceError("reviewed source commit mismatch")
        if facts.get("reviewed_source_tree") != adoption.get("reviewed_source_tree"):
            raise ActivationEvidenceError("reviewed source tree mismatch")
        reconciliation = unchanged_source_reconciliation_digest(
            reviewed_source_commit=str(reviewed_source_commit),
            reviewed_source_tree=str(reviewed_source_tree),
            activation_candidate_commit=identity.candidate_commit,
            activation_candidate_tree=identity.candidate_tree,
            pinned_evidence=adoption.get("pinned_evidence"),
        )
        if not hmac.compare_digest(
            str(facts.get("unchanged_source_reconciliation_digest")), reconciliation
        ):
            raise ActivationEvidenceError("unchanged source reconciliation digest mismatch")
        if identity.candidate_commit != reviewed_source_commit:
            if identity.candidate_tree != reviewed_source_tree:
                raise ActivationEvidenceError(
                    "changed activation candidate requires unchanged source tree reconciliation"
                )

    @staticmethod
    def _validate_acceptance(
        identity: ActivationIdentity, facts: Mapping[str, Any]
    ) -> None:
        if set(facts) != _ACCEPTANCE_FACT_NAMES:
            raise ActivationEvidenceError("acceptance artifact facts contract is invalid")
        exact_fields = {
            "candidate_commit": identity.candidate_commit,
            "candidate_tree": identity.candidate_tree,
            "remote_head_commit": identity.candidate_commit,
            "security_reviewed_commit": identity.candidate_commit,
            "independent_reviewed_commit": identity.candidate_commit,
        }
        if any(facts.get(name) != expected for name, expected in exact_fields.items()):
            raise ActivationEvidenceError(
                "acceptance evidence is not bound to the exact candidate"
            )
        if facts.get("ci_conclusion") != "SUCCESS":
            raise ActivationEvidenceError("exact-head CI has not succeeded")
        check_count = facts.get("ci_required_check_count")
        if isinstance(check_count, bool) or not isinstance(check_count, int) or check_count < 1:
            raise ActivationEvidenceError("exact-head CI lacks required checks")
        run_ids = facts.get("ci_run_ids")
        if (
            not isinstance(run_ids, list)
            or not run_ids
            or any(not isinstance(item, int) or item < 1 for item in run_ids)
        ):
            raise ActivationEvidenceError("exact-head CI run identity is invalid")
        if facts.get("security_verdict") != "ACCEPT":
            raise ActivationEvidenceError("security review has not accepted the exact head")
        if facts.get("independent_review_verdict") != "ACCEPT":
            raise ActivationEvidenceError(
                "independent review has not accepted the exact head"
            )
        security_actor = facts.get("security_reviewer")
        independent_actor = facts.get("independent_reviewer")
        pull_request_author = facts.get("pull_request_author")
        if (
            not isinstance(security_actor, str)
            or not security_actor.strip()
            or not isinstance(independent_actor, str)
            or not independent_actor.strip()
            or hmac.compare_digest(security_actor.casefold(), independent_actor.casefold())
            or not isinstance(pull_request_author, str)
            or not pull_request_author.strip()
            or hmac.compare_digest(security_actor.casefold(), pull_request_author.casefold())
            or hmac.compare_digest(independent_actor.casefold(), pull_request_author.casefold())
        ):
            raise ActivationEvidenceError(
                "producer and acceptance actors require distinct attributable identities"
            )
        for name in (
            "ci_evidence_digest",
            "security_evidence_digest",
            "independent_review_evidence_digest",
        ):
            value = facts.get(name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ActivationEvidenceError(
                    "acceptance evidence lacks exact artifact digests"
                )

    def require_backup_digest(
        self, identity: ActivationIdentity, observed_digest: str
    ) -> str:
        """Prove an observed dump is the exact artifact durably backed up."""

        self._require_artifact_digest(observed_digest)
        current = self.load()
        if (
            current is None
            or current.get("identity_digest") != identity.digest
            or current.get("identity") != asdict(identity)
        ):
            raise ActivationEvidenceError(
                "backup evidence identity is unavailable or stale"
            )
        expected = self._backed_up_digest(current.get("stages"))
        if not hmac.compare_digest(observed_digest, expected):
            raise ActivationEvidenceError(
                "restore dump does not match the durable backup artifact"
            )
        return expected

    def require_backup_file(
        self, identity: ActivationIdentity, path: str | Path
    ) -> str:
        """Hash the exact supplied file, then apply durable continuity proof."""

        source = Path(path)
        try:
            with source.open("rb") as stream:
                digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise ActivationEvidenceError("restore dump is missing or unreadable") from exc
        return self.require_backup_digest(identity, digest)

    @staticmethod
    def _require_artifact_digest(value: object) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in value[7:])
        ):
            raise ActivationEvidenceError(
                "backup evidence lacks an exact sha256 artifact digest"
            )

    @classmethod
    def _backed_up_digest(cls, stages: object) -> str:
        if not isinstance(stages, list):
            raise ActivationEvidenceError("activation stage history is malformed")
        backed_up = next(
            (
                item
                for item in stages
                if isinstance(item, dict)
                and item.get("stage") == ActivationStage.BACKED_UP.value
            ),
            None,
        )
        if not isinstance(backed_up, dict) or not isinstance(backed_up.get("facts"), dict):
            raise ActivationEvidenceError("durable backup evidence is unavailable")
        digest = backed_up["facts"].get("backup_digest")
        cls._require_artifact_digest(digest)
        return str(digest)

    def require_accepted(self, identity: ActivationIdentity) -> dict[str, Any]:
        current = self.load()
        if current is None or current.get("identity") != asdict(identity) or current.get("identity_digest") != identity.digest:
            raise ActivationEvidenceError("accepted identity is unavailable or stale")
        if [item.get("stage") for item in current.get("stages", [])] != ["PREFLIGHT", "ACCEPTED"]:
            raise ActivationEvidenceError("migration requires exact accepted pre-migration state")
        return current

    def require_staged(self, identity: ActivationIdentity) -> dict[str, Any]:
        """Validate authenticated acceptance and restore without adopting intake."""
        current = self.load()
        if current is None or current.get("identity_digest") != identity.digest or current.get("identity") != asdict(identity):
            raise ActivationEvidenceError("activation evidence identity is unavailable or stale")
        stages = current.get("stages", [])
        expected = [stage.value for stage in _ORDER[:-1]]
        observed = [item.get("stage") for item in stages]
        if observed != expected:
            raise ActivationEvidenceError("activation prerequisites are incomplete")
        return current

    def require_ready(self, identity: ActivationIdentity) -> dict[str, Any]:
        current = self.require_staged(identity)
        inventory = current.get("legacy_inventory")
        if not isinstance(inventory, dict):
            raise ActivationEvidenceError("pre-cutover legacy ownership inventory is unavailable")
        return current

    def bind_legacy_inventory(
        self, identity: ActivationIdentity, *, inventory_digest: str, task_count: int
    ) -> dict[str, Any]:
        if (
            len(inventory_digest) != 64
            or any(character not in "0123456789abcdef" for character in inventory_digest)
            or isinstance(task_count, bool)
            or task_count < 0
        ):
            raise ActivationEvidenceError("legacy ownership inventory is invalid")
        current = self.load()
        if (
            current is None
            or current.get("identity_digest") != identity.digest
            or current.get("identity") != asdict(identity)
        ):
            raise ActivationEvidenceError("legacy ownership inventory identity is stale")
        expected_stages = [stage.value for stage in _ORDER[:-1]]
        if [item.get("stage") for item in current.get("stages", [])] != expected_stages:
            raise ActivationEvidenceError("legacy ownership inventory requires restore proof")
        inventory = {"digest": inventory_digest, "task_count": task_count, "owner": "legacy"}
        held = current.get("legacy_inventory")
        if held is not None and held != inventory:
            raise ActivationEvidenceError("legacy ownership inventory is immutable")
        current["legacy_inventory"] = inventory
        self._atomic_write(current)
        return current

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        if getattr(self, "_read_only", False):
            raise ActivationEvidenceError("observational evidence store is read-only")
        if self._journal is not None:
            self._journal.write(self._revision, value)
            return
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
        database_instance_id=arguments.database_instance_id,
        postgres_schema=arguments.postgres_schema,
        checkpoint_namespace=arguments.checkpoint_namespace,
    )


def _acceptance_secrets_from_environment() -> dict[str, str]:
    values = {
        kind: os.environ.get(environment, "")
        for kind, environment in _ACCEPTANCE_SECRET_ENVIRONMENTS.items()
    }
    values["owner_adoption"] = os.environ.get("LANGGRAPH_PRIMARY_OWNER_TOKEN", "")
    return values


def owner_local_adoption_from_environment() -> dict[str, Any] | None:
    mode = os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")
    if mode != OWNER_LOCAL_EVIDENCE_ACCEPTANCE_MODE:
        return None
    path = os.environ.get("LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_PATH", "").strip()
    pin = os.environ.get("LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256", "").strip().lower()
    owner_token = os.environ.get("LANGGRAPH_PRIMARY_OWNER_TOKEN", "")
    if not path or not pin:
        raise ActivationEvidenceError("owner-local evidence adoption pin is unavailable")
    return load_pinned_owner_local_adoption(
        path,
        expected_sha256=pin,
        owner_token=owner_token,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record", "require-ready", "require-staged", "require-accepted", "verify-backup", "bind-legacy-inventory", "challenge"))
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--database-identity", required=True)
    parser.add_argument("--database-instance-id", required=True)
    parser.add_argument("--postgres-schema", required=True)
    parser.add_argument("--checkpoint-namespace", required=True)
    parser.add_argument("--stage", choices=tuple(stage.value for stage in ActivationStage))
    parser.add_argument("--evidence-digest")
    parser.add_argument("--facts-json")
    parser.add_argument("--acceptance-artifact")
    parser.add_argument("--backup-digest")
    parser.add_argument("--legacy-inventory-digest")
    parser.add_argument("--legacy-task-count", type=int)
    arguments = parser.parse_args()
    store = ActivationEvidenceStore(
        arguments.artifact,
        acceptance_secrets=_acceptance_secrets_from_environment(),
        read_only=arguments.command in {"require-ready", "require-staged", "require-accepted", "verify-backup"},
    )
    identity = _identity(arguments)
    if os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE") == OWNER_LOCAL_EVIDENCE_ACCEPTANCE_MODE:
        from .config import Settings
        from .primary_generation import require_generation
        require_generation(Settings(), identity)
    if arguments.command in {"require-staged", "require-accepted"}:
        getattr(store, arguments.command.replace("-", "_"))(identity)
        print("prerequisites_verified=true")
        return
    if arguments.command == "challenge":
        if store._journal is None:
            parser.error("challenge requires owner-configured signed provider mode")
        store.load()
        print(json.dumps(store._journal.challenge(identity), sort_keys=True, separators=(",", ":")))
        return
    if arguments.command == "require-ready":
        store.require_ready(identity)
        print("activation_prerequisites_ready=true")
        return
    if arguments.command == "verify-backup":
        if arguments.backup_digest is None:
            parser.error("verify-backup requires --backup-digest")
        store.require_backup_digest(identity, arguments.backup_digest)
        print("backup_continuity_verified=true")
        return
    if arguments.command == "bind-legacy-inventory":
        if arguments.legacy_inventory_digest is None or arguments.legacy_task_count is None:
            parser.error("bind-legacy-inventory requires digest and task count")
        store.bind_legacy_inventory(
            identity,
            inventory_digest=arguments.legacy_inventory_digest,
            task_count=arguments.legacy_task_count,
        )
        print("legacy_ownership_inventory_bound=true")
        return
    if arguments.stage is None:
        parser.error("record requires --stage")
    stage = ActivationStage(arguments.stage)
    if stage is ActivationStage.ACCEPTED:
        if arguments.facts_json is not None or arguments.evidence_digest is not None:
            parser.error("ACCEPTED refuses --facts-json and --evidence-digest")
        if arguments.acceptance_artifact is None:
            parser.error("ACCEPTED requires --acceptance-artifact")
        store.record_acceptance_artifact(
            identity,
            arguments.acceptance_artifact,
        )
        print("activation_stage_recorded=true")
        return
    if arguments.acceptance_artifact is not None:
        parser.error("--acceptance-artifact is only valid for ACCEPTED")
    if arguments.evidence_digest is None:
        parser.error("record requires --evidence-digest")
    facts = json.loads(arguments.facts_json or "{}")
    if not isinstance(facts, dict):
        parser.error("--facts-json must be an object")
    store.record(
        identity,
        stage,
        evidence_digest=arguments.evidence_digest,
        facts=facts,
    )
    print("activation_stage_recorded=true")


if __name__ == "__main__":
    main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:30 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:30 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:11 UTC | END]]
