# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 05:51 UTC | BEGIN]]
"""Canonical external-effect intents and exact Slack/GitHub gateways."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx

from .langgraph_postgres import EffectReservation
from .oracle_merge import OracleGitHubGateway
from .packet_contracts import document_digest


EFFECT_INTENT_SCHEMA_VERSION = "director-primary-effect-intent/v1"
SLACK_POST_MESSAGE = "chat.postMessage"
GITHUB_MARK_READY = "pulls.mark_ready"
SUPPORTED_EFFECT_ACTIONS = frozenset(
    {("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}
)
_SAFE_SYSTEM = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SAFE_ACTION = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SAFE_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{7,199}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SLACK_ID = re.compile(r"^[CGD][A-Z0-9]{2,63}$")
_SECRET_KEY = re.compile(
    r"(?i)(authorization|cookie|password|private[_-]?key|secret|token)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|xox[baprs]-[A-Za-z0-9-]+)"
)


class EffectIntentError(RuntimeError):
    """The intent or its configured transport failed a deterministic gate."""


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} must be a sha256 digest")


def _sanitize(value: Any, *, path: str = "payload") -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 20_000:
            raise ValueError(f"{path} string exceeds the canonical limit")
        if _SECRET_VALUE.search(value):
            raise ValueError(f"{path} contains credential-like material")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 128:
                raise ValueError(f"{path} has a malformed field name")
            if _SECRET_KEY.search(raw_key):
                raise ValueError(f"{path}.{raw_key} is a prohibited secret field")
            result[raw_key] = _sanitize(item, path=f"{path}.{raw_key}")
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 256:
            raise ValueError(f"{path} has too many items")
        return [_sanitize(item, path=f"{path}[]") for item in value]
    raise ValueError(f"{path} is not canonical JSON")


@dataclass(frozen=True, slots=True)
class TypedEffectIntent:
    """One fully bound, secret-free intent persisted before remote execution."""

    system: str
    action: str
    target: Mapping[str, Any]
    payload: Mapping[str, Any]
    idempotency_key: str
    authority_binding_digest: str
    approval_binding_digest: str
    writer_owner_id: str
    writer_fencing_generation: int
    budget_units: int
    schema_version: str = EFFECT_INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EFFECT_INTENT_SCHEMA_VERSION:
            raise ValueError("effect intent schema version is unsupported")
        if _SAFE_SYSTEM.fullmatch(self.system) is None:
            raise ValueError("effect system is malformed")
        if _SAFE_ACTION.fullmatch(self.action) is None:
            raise ValueError("effect action is malformed")
        if (self.system, self.action) not in SUPPORTED_EFFECT_ACTIONS:
            raise ValueError("effect action is unsupported")
        if _SAFE_IDEMPOTENCY.fullmatch(self.idempotency_key) is None:
            raise ValueError("effect idempotency key is malformed")
        _require_digest(self.authority_binding_digest, "authority_binding_digest")
        _require_digest(self.approval_binding_digest, "approval_binding_digest")
        if not self.writer_owner_id.strip():
            raise ValueError("writer_owner_id is required")
        if type(self.writer_fencing_generation) is not int or self.writer_fencing_generation < 1:
            raise ValueError("writer_fencing_generation must be positive")
        if type(self.budget_units) is not int or self.budget_units < 0:
            raise ValueError("budget_units must be non-negative")
        target = _sanitize(self.target, path="target")
        payload = _sanitize(self.payload)
        if not isinstance(target, dict) or not isinstance(payload, dict):
            raise ValueError("target and payload must be mappings")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "payload", payload)
        self._validate_action()

    def _validate_action(self) -> None:
        if self.system == "slack":
            if set(self.target) - {"channel", "thread_ts"}:
                raise ValueError("Slack target contains unsupported fields")
            if _SLACK_ID.fullmatch(str(self.target.get("channel", ""))) is None:
                raise ValueError("Slack channel is malformed")
            thread_ts = self.target.get("thread_ts")
            if thread_ts is not None and not re.fullmatch(r"\d{10,}\.\d{1,6}", str(thread_ts)):
                raise ValueError("Slack thread timestamp is malformed")
            if set(self.payload) != {"text"} or not str(self.payload.get("text", "")).strip():
                raise ValueError("Slack post requires only a non-empty text payload")
        elif self.system == "github":
            required = {"repository", "pull_number", "head_sha", "base_sha"}
            if set(self.target) != required or self.payload:
                raise ValueError("GitHub mark-ready target or payload is malformed")
            if _REPOSITORY.fullmatch(str(self.target["repository"])) is None:
                raise ValueError("GitHub repository is malformed")
            if type(self.target["pull_number"]) is not int or self.target["pull_number"] < 1:
                raise ValueError("GitHub pull number is malformed")
            for field in ("head_sha", "base_sha"):
                if _SHA40.fullmatch(str(self.target[field])) is None:
                    raise ValueError(f"GitHub {field} is malformed")

    @property
    def payload_digest(self) -> str:
        return document_digest(dict(self.payload))

    @property
    def intent_digest(self) -> str:
        return document_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "system": self.system,
            "action": self.action,
            "target": dict(self.target),
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "idempotency_key": self.idempotency_key,
            "authority_binding_digest": self.authority_binding_digest,
            "approval_binding_digest": self.approval_binding_digest,
            "writer_owner_id": self.writer_owner_id,
            "writer_fencing_generation": self.writer_fencing_generation,
            "budget_units": self.budget_units,
        }


class TypedEffectGateway(Protocol):
    system: str
    action: str

    def mutate(self, intent: TypedEffectIntent, timeout_seconds: float) -> str: ...

    def readback(
        self, intent: TypedEffectIntent, remote_identifier: str, timeout_seconds: float
    ) -> Mapping[str, Any]: ...


class ExactEffectGatewayRegistry:
    """Fail-closed registry: readiness means every allowed action has one gateway."""

    def __init__(
        self,
        gateways: tuple[TypedEffectGateway, ...],
        *,
        allowed_actions: frozenset[tuple[str, str]],
    ) -> None:
        if not allowed_actions or not allowed_actions.issubset(SUPPORTED_EFFECT_ACTIONS):
            raise ValueError("allowed effect actions must be a non-empty supported set")
        routes: dict[tuple[str, str], TypedEffectGateway] = {}
        for gateway in gateways:
            route = (gateway.system, gateway.action)
            if route in routes:
                raise ValueError("duplicate effect gateway route")
            routes[route] = gateway
        if set(routes) != set(allowed_actions):
            raise ValueError("configured gateways do not exactly match allowed actions")
        self._routes = routes
        self.allowed_actions = allowed_actions

    @property
    def ready(self) -> bool:
        return bool(self._routes)

    def require(self, intent: TypedEffectIntent) -> TypedEffectGateway:
        gateway = self._routes.get((intent.system, intent.action))
        if gateway is None:
            raise EffectIntentError("effect action transport is not configured")
        return gateway


class PostgresTypedEffectIntentStore:
    """Append-only typed intent ledger bound to the active reservation and fence."""

    def __init__(self, *, pool: Any, schema: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
            raise ValueError("effect intent schema is malformed")
        self._pool = pool
        self._schema = schema

    def record_approval_binding(
        self,
        *,
        task_id: str,
        effect_key_digest: str,
        approval_request_digest: str,
        approval_receipt_digest: str,
        command_id: str,
        command_digest: str,
        authorization_nonce_digest: str,
        actor_id: str,
        writer_owner_id: str,
        writer_fencing_generation: int,
    ) -> None:
        """Bind one signed owner effect approval to its durable command receipt."""

        for value, field in (
            (effect_key_digest, "effect_key_digest"),
            (approval_request_digest, "approval_request_digest"),
            (approval_receipt_digest, "approval_receipt_digest"),
            (command_digest, "command_digest"),
            (authorization_nonce_digest, "authorization_nonce_digest"),
        ):
            _require_digest(value, field)
        values = locals().copy()
        values.pop("self")
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    f"""
                    INSERT INTO {schema}.director_primary_effect_approval_binding (
                        task_id, effect_key_digest, approval_request_digest,
                        approval_receipt_digest, command_digest,
                        authorization_nonce_digest, actor_id, writer_owner_id,
                        writer_fencing_generation
                    )
                    SELECT writer.task_id, %(effect_key_digest)s,
                           %(approval_request_digest)s, %(approval_receipt_digest)s,
                           authz.command_digest, authz.nonce_digest,
                           authz.actor_id, %(writer_owner_id)s,
                           writer.fencing_generation
                    FROM {schema}.director_primary_task_writer writer
                    JOIN {schema}.director_primary_command_authorization authz
                      ON authz.task_id = writer.task_id
                     AND authz.operation = 'approval_resume'
                     AND authz.command_id = %(command_id)s
                     AND authz.command_digest = %(command_digest)s
                     AND authz.nonce_digest = %(authorization_nonce_digest)s
                     AND authz.actor_id = %(actor_id)s
                     AND authz.writer_fencing_generation = writer.fencing_generation
                    WHERE writer.task_id = %(task_id)s
                      AND writer.engine_owner = 'langgraph'
                      AND writer.owner_token <> ''
                      AND writer.fencing_generation = %(writer_fencing_generation)s
                      AND writer.lease_expires_at > now()
                    ON CONFLICT (task_id) DO NOTHING
                    RETURNING task_id
                    """,
                    values,
                ).fetchone()
                if row is None:
                    existing = connection.execute(
                        f"""
                        SELECT effect_key_digest, approval_request_digest,
                               approval_receipt_digest, command_digest,
                               authorization_nonce_digest, actor_id,
                               writer_owner_id, writer_fencing_generation
                        FROM {schema}.director_primary_effect_approval_binding
                        WHERE task_id = %(task_id)s
                        """,
                        values,
                    ).fetchone()
                    expected = {
                        key: values[key]
                        for key in (
                            "effect_key_digest", "approval_request_digest",
                            "approval_receipt_digest", "command_digest",
                            "authorization_nonce_digest", "actor_id",
                            "writer_owner_id", "writer_fencing_generation",
                        )
                    }
                    if existing is None or any(
                        str(existing[key]) != str(value)
                        for key, value in expected.items()
                    ):
                        raise EffectIntentError(
                            "effect approval binding lacks exact signed owner authority"
                        )

    def persist(
        self, *, reservation: EffectReservation, owner_token: str, intent: TypedEffectIntent
    ) -> str:
        canonical = _canonical(intent.as_dict())
        intent_digest = intent.intent_digest
        idempotency_digest = "sha256:" + hashlib.sha256(
            intent.idempotency_key.encode("utf-8")
        ).hexdigest()
        values = {
            "reservation_key": reservation.reservation_key,
            "owner_token": owner_token,
            "intent_digest": intent_digest,
            "system": intent.system,
            "action": intent.action,
            "target": json.dumps(intent.target, sort_keys=True, separators=(",", ":")),
            "payload": json.dumps(intent.payload, sort_keys=True, separators=(",", ":")),
            "payload_digest": intent.payload_digest,
            "idempotency_digest": idempotency_digest,
            "authority": intent.authority_binding_digest,
            "approval": intent.approval_binding_digest,
            "writer_owner_id": intent.writer_owner_id,
            "generation": intent.writer_fencing_generation,
            "budget": intent.budget_units,
            "canonical": canonical,
        }
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    f"""
                    INSERT INTO {schema}.director_primary_typed_effect_intent (
                        reservation_key, task_id, intent_digest, system, action,
                        target, sanitized_payload, payload_digest,
                        idempotency_key_digest, authority_binding_digest,
                        approval_binding_digest, writer_owner_id,
                        writer_fencing_generation, budget_units, canonical_intent
                    )
                    SELECT reserved.reservation_key, reserved.task_id,
                           %(intent_digest)s, %(system)s, %(action)s,
                           %(target)s::jsonb, %(payload)s::jsonb,
                           %(payload_digest)s, %(idempotency_digest)s,
                           %(authority)s, %(approval)s, %(writer_owner_id)s,
                           %(generation)s, %(budget)s, %(canonical)s
                    FROM {schema}.director_primary_effect_reservation reserved
                    JOIN {schema}.director_primary_claim claim
                      ON claim.claim_key = reserved.claim_key
                    JOIN {schema}.director_primary_task_writer writer
                      ON writer.task_id = claim.task_id
                     AND writer.owner_token = claim.owner_token
                     AND writer.fencing_generation = claim.fencing_generation
                     AND writer.lease_expires_at > now()
                    JOIN {schema}.director_primary_effect_approval_binding approval
                      ON approval.task_id = reserved.task_id
                     AND approval.effect_key_digest = reserved.effect_key_digest
                     AND approval.approval_receipt_digest = %(approval)s
                     AND approval.authorization_nonce_digest = %(authority)s
                     AND approval.writer_owner_id = reserved.writer_owner_id
                     AND approval.writer_fencing_generation = reserved.fencing_generation
                    JOIN {schema}.director_primary_command_authorization authz
                      ON authz.task_id = approval.task_id
                     AND authz.operation = 'approval_resume'
                     AND authz.command_digest = approval.command_digest
                     AND authz.nonce_digest = approval.authorization_nonce_digest
                     AND authz.actor_id = approval.actor_id
                     AND authz.writer_fencing_generation = approval.writer_fencing_generation
                    WHERE reserved.reservation_key = %(reservation_key)s
                      AND reserved.owner_token = %(owner_token)s
                      AND reserved.writer_owner_id = %(writer_owner_id)s
                      AND reserved.fencing_generation = %(generation)s
                      AND reserved.reserved_units = %(budget)s
                    ON CONFLICT (reservation_key) DO NOTHING
                    RETURNING intent_digest
                    """,
                    values,
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        f"""
                        SELECT intent_digest, canonical_intent
                        FROM {schema}.director_primary_typed_effect_intent
                        WHERE reservation_key = %(reservation_key)s
                        """,
                        values,
                    ).fetchone()
                if row is None:
                    raise EffectIntentError(
                        "typed effect intent lacks current writer-fenced reservation authority"
                    )
                if not hmac.compare_digest(str(row["intent_digest"]), intent_digest):
                    raise EffectIntentError("typed effect intent replay changed")
                if "canonical_intent" in row and not hmac.compare_digest(
                    str(row["canonical_intent"]), canonical
                ):
                    raise EffectIntentError("typed effect intent canonical document changed")
        return intent_digest

    def read(self, reservation_key: str) -> Mapping[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT canonical_intent FROM {self._schema}.director_primary_typed_effect_intent
                WHERE reservation_key = %s
                """,
                (reservation_key,),
            ).fetchone()
        return None if row is None else json.loads(str(row["canonical_intent"]))

    def approval_binding(
        self, *, task_id: str, effect_key_digest: str
    ) -> Mapping[str, Any]:
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT approval_receipt_digest, authorization_nonce_digest,
                       writer_owner_id, writer_fencing_generation
                FROM {self._schema}.director_primary_effect_approval_binding
                WHERE task_id = %s AND effect_key_digest = %s
                """,
                (task_id, effect_key_digest),
            ).fetchone()
        if row is None:
            raise EffectIntentError("signed effect approval binding is unavailable")
        return dict(row)


class SlackPostMessageGateway:
    system = "slack"
    action = SLACK_POST_MESSAGE

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not token:
            raise ValueError("Slack bot token is required")
        self._token = token
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url="https://slack.com/api",
            timeout=timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    @staticmethod
    def _payload(response: httpx.Response) -> Mapping[str, Any]:
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, Mapping) or value.get("ok") is not True:
            raise EffectIntentError("Slack API returned a non-success response")
        return value

    def mutate(self, intent: TypedEffectIntent, timeout_seconds: float) -> str:
        body = {
            "channel": intent.target["channel"],
            "text": intent.payload["text"],
            "client_msg_id": intent.idempotency_key,
        }
        if intent.target.get("thread_ts") is not None:
            body["thread_ts"] = intent.target["thread_ts"]
        with self._client(timeout_seconds) as client:
            payload = self._payload(client.post("/chat.postMessage", json=body))
        timestamp = payload.get("ts")
        if not isinstance(timestamp, str) or not re.fullmatch(r"\d{10,}\.\d{1,6}", timestamp):
            raise EffectIntentError("Slack mutation returned no canonical timestamp")
        return timestamp

    def readback(
        self, intent: TypedEffectIntent, remote_identifier: str, timeout_seconds: float
    ) -> Mapping[str, Any]:
        params = {
            "channel": intent.target["channel"],
            "oldest": remote_identifier,
            "latest": remote_identifier,
            "inclusive": "true",
            "limit": "1",
        }
        endpoint = "/conversations.history"
        if intent.target.get("thread_ts") is not None:
            endpoint = "/conversations.replies"
            params["ts"] = str(intent.target["thread_ts"])
        with self._client(timeout_seconds) as client:
            payload = self._payload(client.get(endpoint, params=params))
        messages = payload.get("messages")
        matching = [
            item for item in messages or []
            if isinstance(item, Mapping) and item.get("ts") == remote_identifier
        ]
        if len(matching) != 1 or matching[0].get("text") != intent.payload["text"]:
            raise EffectIntentError("Slack terminal readback did not match the intent")
        return {
            "remote_system": self.system,
            "remote_identifier": remote_identifier,
            "terminal_status": "COMPLETED",
            "intent_digest": intent.intent_digest,
        }


class GitHubMarkReadyGateway:
    system = "github"
    action = GITHUB_MARK_READY

    def __init__(self, gateway: OracleGitHubGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _remote_identifier(intent: TypedEffectIntent) -> str:
        repository = str(intent.target["repository"]).replace("/", ".")
        return f"github-pr:{repository}:{intent.target['pull_number']}"

    def mutate(self, intent: TypedEffectIntent, timeout_seconds: float) -> str:
        target = intent.target
        if not self._gateway.mark_ready(
            str(target["repository"]),
            int(target["pull_number"]),
            str(target["head_sha"]),
            str(target["base_sha"]),
        ):
            raise EffectIntentError("GitHub did not confirm ready-for-review")
        return self._remote_identifier(intent)

    def readback(
        self, intent: TypedEffectIntent, remote_identifier: str, timeout_seconds: float
    ) -> Mapping[str, Any]:
        target = intent.target
        expected_identifier = self._remote_identifier(intent)
        if not hmac.compare_digest(remote_identifier, expected_identifier):
            raise EffectIntentError("GitHub remote identity changed")
        snapshot = self._gateway.snapshot(
            str(target["repository"]), int(target["pull_number"])
        )
        if (
            snapshot.draft
            or snapshot.head_sha != target["head_sha"]
            or snapshot.base_sha != target["base_sha"]
        ):
            raise EffectIntentError("GitHub terminal readback did not match the intent")
        return {
            "remote_system": self.system,
            "remote_identifier": remote_identifier,
            "terminal_status": "COMPLETED",
            "intent_digest": intent.intent_digest,
        }


def build_configured_effect_gateway_registry(
    settings: Any,
) -> ExactEffectGatewayRegistry:
    """Build only transports backed by an exact configured credential."""

    gateways: list[TypedEffectGateway] = []
    if settings.slack_bot_token:
        gateways.append(SlackPostMessageGateway(settings.slack_bot_token))
    if settings.oracle_github_credential_configured():
        gateways.append(GitHubMarkReadyGateway(OracleGitHubGateway.from_settings(settings)))
    allowed = frozenset((item.system, item.action) for item in gateways)
    if not allowed:
        raise ValueError("no authenticated Slack or GitHub effect gateway is configured")
    return ExactEffectGatewayRegistry(tuple(gateways), allowed_actions=allowed)


# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 05:51 UTC | END]]
