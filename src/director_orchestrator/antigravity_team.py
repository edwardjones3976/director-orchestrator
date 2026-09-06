# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
"""Explicit owner-signed engineering authority; subordinate contracts stay closed.

This module grants no production, integration, credential, or issuer authority.
Amounts are integer micro-USD reservations from signed persisted budgets, never
provider estimates or a configuration digest. Unknown usage remains reserved.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .entitlement_contracts import canonical_json_bytes, document_digest, EntitlementActionType

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


class TeamAuthorizationError(ValueError):
    pass


def family(value: str) -> str:
    return "gemini" if value in {"gemini", "antigravity"} else value


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in {"..", ".", ".git"} for part in path.parts)
            or "\\" in value or ":" in value or "\x00" in value):
        raise TeamAuthorizationError("noncanonical or prohibited source path")
    for part in path.parts:
        if part.rstrip(" .") != part or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?",part):
            raise TeamAuthorizationError("Windows device or path alias")
    return value


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceBlob(ClosedModel):
    path: str
    sha256: Digest

    @model_validator(mode="after")
    def check_path(self):
        safe_path(self.path)
        return self


class TeamGrant(ClosedModel):
    schema_version: Literal["antigravity-team-engineering/v1"]
    grant_id: Identifier
    task_id: Identifier
    assignment_id: Identifier
    project_id: Identifier
    repository: str
    packet_digest: Digest
    commit: Commit
    tree: Commit
    review_base: Commit | None
    operation: Literal["engineering", "review"]
    source: tuple[SourceBlob, ...]
    writable_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    workspace_id: Identifier
    provider_family: Literal["gemini"]
    installation_id: Identifier
    installation_build: Digest
    executable_digest: Digest
    runtime_digest: Digest
    adapter_digest: Digest
    binding_digest: Digest
    native_account_id: Identifier
    model: str
    material_authors: tuple[str, ...]
    generation: Annotated[int, Field(ge=1)]
    expires_at: datetime
    max_tokens: Annotated[int, Field(gt=0)]
    timeout_seconds: Annotated[int, Field(gt=0, le=2700)]
    max_attempts: Literal[3]
    max_local_commands: Annotated[int, Field(ge=0, le=100)] = 20
    owner_signature: Digest

    @model_validator(mode="after")
    def boundaries(self):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise TeamAuthorizationError("repository boundary invalid")
        if not self.model.startswith("gemini-"):
            raise TeamAuthorizationError("native Gemini model required")
        if self.expires_at.tzinfo is None:
            raise TeamAuthorizationError("expiry must be timezone aware")
        paths = tuple(item.path for item in self.source)
        if not paths or len(paths) > 10000 or len(set(paths)) != len(paths):
            raise TeamAuthorizationError("complete unique source manifest required")
        if len({p.casefold() for p in paths}) != len(paths):
            raise TeamAuthorizationError("source paths collide on Windows")
        for path in (*self.writable_paths, *self.prohibited_paths):
            safe_path(path)
        for path in (*paths, *self.writable_paths):
            if any(path.casefold() == denied.casefold() or path.casefold().startswith(denied.casefold() + "/") for denied in self.prohibited_paths):
                raise TeamAuthorizationError("prohibited path in assignment")
        if any(not command or any(not arg or "\x00" in arg for arg in command) for command in self.commands):
            raise TeamAuthorizationError("commands must be explicit argument vectors")
        if self.operation == "review":
            if self.review_base is None or self.writable_paths or self.commands:
                raise TeamAuthorizationError("review environment must be immutable and command-free")
            if "gemini" in {family(author) for author in self.material_authors}:
                raise TeamAuthorizationError("material author cannot independently review")
        elif self.review_base is not None:
            raise TeamAuthorizationError("engineering cannot impersonate a review")
        return self

    def signing_document(self):
        return self.model_dump(mode="json", exclude={"owner_signature"})

    @property
    def digest(self):
        return document_digest(self.signing_document())

    def verify(self, secret: bytes, now: datetime):
        expected = hmac.new(secret, canonical_json_bytes(self.signing_document()), hashlib.sha256).hexdigest()
        if len(secret) < 32 or not hmac.compare_digest(expected, self.owner_signature):
            raise TeamAuthorizationError("task grant signature invalid")
        if self.expires_at <= now:
            raise TeamAuthorizationError("task grant expired")


class ProviderBudget(ClosedModel):
    """Owner-approved limits persisted before an execution request arrives."""
    schema_version: Literal["primary-provider-budget/v1"]
    grant_digest: Digest
    provider_id: Identifier
    project_id: Identifier
    task_id: Identifier
    day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    task_limit_microusd: Annotated[int, Field(gt=0)]
    daily_limit_microusd: Annotated[int, Field(gt=0)]
    attempt_reservation_microusd: Annotated[int, Field(gt=0)]
    token_limit: Annotated[int, Field(gt=0)]
    owner_signature: Digest

    def signing_document(self):
        return self.model_dump(mode="json", exclude={"owner_signature"})

    def verify(self, secret: bytes):
        expected = hmac.new(secret, canonical_json_bytes(self.signing_document()), hashlib.sha256).hexdigest()
        if len(secret) < 32 or not hmac.compare_digest(expected, self.owner_signature):
            raise TeamAuthorizationError("budget authority signature invalid")


class ProviderUsageReceipt(ClosedModel):
    schema_version: Literal["primary-provider-usage/v1"]
    attempt_digest: Annotated[str,Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    authority_digest: Digest
    provider_id: Identifier
    actual_microusd: Annotated[int,Field(ge=0)]
    observed_tokens: Annotated[int,Field(ge=0)]
    complete: Literal[True]
    meter_signature: Digest

    def signing_document(self):
        return self.model_dump(mode="json",exclude={"meter_signature"})


def team_ddl(schema: str) -> tuple[str, ...]:
    # Called only by the existing accepted control-plane migration.
    if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise TeamAuthorizationError("invalid control schema")
    return (
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_team_grant (
            grant_digest text PRIMARY KEY, document jsonb NOT NULL,
            budget_document jsonb NOT NULL, revoked boolean NOT NULL DEFAULT false)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_provider_budget (
            authority_digest text PRIMARY KEY, document jsonb NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_provider_spend (
            attempt_digest text PRIMARY KEY, grant_digest text NOT NULL REFERENCES
                {schema}.director_primary_provider_budget(authority_digest),
            provider_id text NOT NULL, project_id text NOT NULL, task_id text NOT NULL, budget_day date NOT NULL,
            reserved_microusd bigint NOT NULL CHECK(reserved_microusd>0),
            token_limit bigint NOT NULL CHECK(token_limit>0),
            UNIQUE(attempt_digest, grant_digest))""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_provider_usage (
            attempt_digest text PRIMARY KEY REFERENCES {schema}.director_primary_provider_spend(attempt_digest),
            document jsonb NOT NULL, actual_microusd bigint NOT NULL CHECK(actual_microusd>=0),
            observed_tokens bigint NOT NULL CHECK(observed_tokens>=0))""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_team_boundary (
            nonce text PRIMARY KEY, grant_digest text NOT NULL REFERENCES
                {schema}.director_primary_team_grant(grant_digest),
            stage text NOT NULL CHECK(stage IN ('disclosure','dispatch','execution','command','result')),
            attempt_digest text, evidence_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS director_primary_team_execution_once
            ON {schema}.director_primary_team_boundary(attempt_digest)
            WHERE stage='execution'""",
        f"""CREATE OR REPLACE FUNCTION {schema}.director_primary_team_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable team authority'; END IF;
          IF TG_TABLE_NAME='director_primary_team_grant' AND NEW.grant_digest=OLD.grant_digest
             AND NEW.document=OLD.document AND NEW.budget_document=OLD.budget_document
             AND NOT OLD.revoked AND NEW.revoked THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'immutable team authority';
        END $$""",
        *tuple(f"""DO $guard$ BEGIN
            IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='{table}_guard'
                AND tgrelid='{schema}.{table}'::regclass) THEN
              CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {schema}.{table}
              FOR EACH ROW EXECUTE FUNCTION {schema}.director_primary_team_guard();
            END IF; END $guard$""" for table in (
                "director_primary_team_grant","director_primary_provider_budget","director_primary_provider_spend","director_primary_provider_usage","director_primary_team_boundary")),
    )


class PrimaryProviderBudgetAuthority:
    """One monetary authority for every provider using the primary attempt log.

    Existing legacy stage-unit counters remain distinct from monetary authority;
    they cannot be used as evidence that a provider attempt has been funded.
    """
    def __init__(self, entitlement_store, *, primary_schema):
        team_ddl(primary_schema)
        self.entitlements=entitlement_store
        self.schema=primary_schema

    def register(self, connection, budget: ProviderBudget):
        budget.verify(self.entitlements._owner_authority_secret)
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("primary-provider-budget:"+budget.project_id,))
        registration=connection.execute(f"SELECT document FROM {self.entitlements.schema}.entitlement_project_registration WHERE project_profile_id=%s AND superseded_at IS NULL ORDER BY registration_generation DESC LIMIT 1",(budget.project_id,)).fetchone()
        if registration is None:
            raise TeamAuthorizationError("registered project budget required")
        from decimal import Decimal
        limits=registration["document"]
        if (budget.task_limit_microusd > int(Decimal(str(limits["budget_task_usd"]))*1000000)
                or budget.daily_limit_microusd > int(Decimal(str(limits["budget_daily_usd"]))*1000000)):
            raise TeamAuthorizationError("budget exceeds signed project registration")
        rows=connection.execute(f"SELECT document FROM {self.schema}.director_primary_provider_budget WHERE document->>'project_id'=%s",(budget.project_id,)).fetchall()
        for row in rows:
            old=ProviderBudget.model_validate_json(json.dumps(row["document"]))
            if ((old.task_id==budget.task_id and old.task_limit_microusd!=budget.task_limit_microusd)
                    or (old.day==budget.day and old.daily_limit_microusd!=budget.daily_limit_microusd)):
                raise TeamAuthorizationError("persisted budget ceiling cannot change")
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) VALUES(%s,%s::jsonb)",(budget.grant_digest,budget.model_dump_json()))

    def reserve(self, connection, *, authority_digest, provider, task_id, attempt_digest):
        row=connection.execute(f"SELECT document FROM {self.schema}.director_primary_provider_budget WHERE authority_digest=%s",(authority_digest,)).fetchone()
        if row is None:
            raise TeamAuthorizationError("authoritative provider budget missing")
        budget=ProviderBudget.model_validate_json(json.dumps(row["document"]))
        budget.verify(self.entitlements._owner_authority_secret)
        if (budget.grant_digest!=authority_digest or budget.provider_id!=provider or budget.task_id!=task_id
                or budget.day!=self.entitlements._clock().astimezone(timezone.utc).date().isoformat()):
            raise TeamAuthorizationError("provider budget binding expired or changed")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("primary-provider-budget:"+budget.project_id,))
        unknown=connection.execute(f"""SELECT 1 FROM {self.schema}.director_primary_provider_attempt a
            LEFT JOIN {self.schema}.director_primary_provider_spend s USING(attempt_digest)
            WHERE s.attempt_digest IS NULL LIMIT 1""").fetchone()
        if unknown is not None:
            raise TeamAuthorizationError("prior provider attempt lacks authoritative monetary accounting")
        if connection.execute(f"SELECT 1 FROM {self.schema}.director_primary_provider_spend s JOIN {self.schema}.director_primary_provider_usage u USING(attempt_digest) WHERE s.project_id=%s AND u.observed_tokens>s.token_limit LIMIT 1",(budget.project_id,)).fetchone():
            raise TeamAuthorizationError("observed provider token limit violation requires reconciliation")
        totals=connection.execute(f"""SELECT
            COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.task_id=%s),0) AS task_spend,
            COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))) FILTER(WHERE s.budget_day=%s::date),0) AS daily_spend
            FROM {self.schema}.director_primary_provider_spend s LEFT JOIN {self.schema}.director_primary_provider_usage u USING(attempt_digest)
            WHERE s.project_id=%s""",(task_id,budget.day,budget.project_id)).fetchone()
        amount=budget.attempt_reservation_microusd
        if totals["task_spend"]+amount>budget.task_limit_microusd or totals["daily_spend"]+amount>budget.daily_limit_microusd:
            raise TeamAuthorizationError("authoritative provider budget exhausted")
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest,grant_digest,provider_id,project_id,task_id,budget_day,reserved_microusd,token_limit) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(attempt_digest,authority_digest,provider,budget.project_id,task_id,budget.day,amount,budget.token_limit))
        return budget

    def record_usage(self,connection,receipt:ProviderUsageReceipt):
        expected=hmac.new(self.entitlements._route_verifier_secret,canonical_json_bytes(receipt.signing_document()),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,receipt.meter_signature):
            raise TeamAuthorizationError("independently authenticated usage required")
        row=connection.execute(f"SELECT grant_digest,provider_id,project_id FROM {self.schema}.director_primary_provider_spend WHERE attempt_digest=%s",(receipt.attempt_digest,)).fetchone()
        if row is None or (row["grant_digest"],row["provider_id"])!=(receipt.authority_digest,receipt.provider_id):
            raise TeamAuthorizationError("usage receipt reservation mismatch")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("primary-provider-budget:"+row["project_id"],))
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest,document,actual_microusd,observed_tokens) VALUES(%s,%s::jsonb,%s,%s)",(receipt.attempt_digest,receipt.model_dump_json(),receipt.actual_microusd,receipt.observed_tokens))


class ProviderBudgetAdmission:
    def __init__(self, authority: PrimaryProviderBudgetAuthority, authority_digest: str):
        self.authority,self.authority_digest=authority,authority_digest

    def admit(self, connection, *, task_id, provider, attempt_digest):
        return self.authority.reserve(connection,authority_digest=self.authority_digest,
            task_id=task_id,provider=provider,attempt_digest=attempt_digest)


class TeamAuthority:
    """Uses the existing primary store's connection and existing entitlement locks.

    No method implicitly migrates, mounts source, or runs a provider.
    """
    def __init__(self, entitlement_store, *, primary_schema: str, enabled: bool = False):
        team_ddl(primary_schema)  # identifier validation only; no DDL
        self.entitlements = entitlement_store
        self.schema = primary_schema
        self.enabled = enabled
        self.budgets = PrimaryProviderBudgetAuthority(entitlement_store,primary_schema=primary_schema)

    def _require_enabled(self):
        if self.enabled is not True:
            raise TeamAuthorizationError("primary Antigravity team mode is disabled")

    def register(self, connection, grant: TeamGrant, budget: ProviderBudget):
        self._require_enabled()
        grant.verify(self.entitlements._owner_authority_secret, self.entitlements._clock())
        budget.verify(self.entitlements._owner_authority_secret)
        if (budget.grant_digest != grant.digest or budget.project_id != grant.project_id
                or budget.task_id != grant.task_id or budget.token_limit > grant.max_tokens
                or budget.provider_id != "antigravity"):
            raise TeamAuthorizationError("budget does not bind the complete task grant")
        self.budgets.register(connection,budget)
        connection.execute(f"INSERT INTO {self.schema}.director_primary_team_grant(grant_digest,document,budget_document) VALUES (%s,%s::jsonb,%s::jsonb)", (grant.digest, grant.model_dump_json(), budget.model_dump_json()))

    def _load(self, connection, digest: str):
        self._require_enabled()
        row = connection.execute(f"SELECT document,budget_document,revoked FROM {self.schema}.director_primary_team_grant WHERE grant_digest=%s FOR UPDATE", (digest,)).fetchone()
        if row is None or row["revoked"]:
            raise TeamAuthorizationError("task grant missing or revoked")
        grant = TeamGrant.model_validate_json(json.dumps(row["document"]))
        grant.verify(self.entitlements._owner_authority_secret, self.entitlements._clock())
        if grant.digest != digest:
            raise TeamAuthorizationError("persisted task grant changed")
        budget = ProviderBudget.model_validate_json(json.dumps(row["budget_document"]))
        budget.verify(self.entitlements._owner_authority_secret)
        if (budget.grant_digest != digest or budget.project_id != grant.project_id
                or budget.task_id != grant.task_id or budget.token_limit > grant.max_tokens
                or budget.provider_id != "antigravity"):
            raise TeamAuthorizationError("persisted budget/grant binding changed")
        return grant, budget

    def authorize(self, connection, *, grant_digest: str, query, stage: str,
                  attempt_digest: str | None = None, command: tuple[str, ...] | None = None):
        """Caller owns one transaction encompassing authorization and its action claim."""
        grant, budget = self._load(connection, grant_digest)
        if (query.project_profile_id != grant.project_id or query.repository != grant.repository
                or query.ref != grant.commit or query.installation_id != grant.installation_id
                or query.current_build_digest != grant.installation_build
                or query.effect_digest != grant.digest or query.provider_id != "antigravity"):
            raise TeamAuthorizationError("task grant/query binding mismatch")
        if stage not in {"disclosure", "dispatch", "execution", "command", "result"}:
            raise TeamAuthorizationError("unknown team boundary")
        expected_action = {
            "disclosure": EntitlementActionType.SOURCE_READ,
            "dispatch": EntitlementActionType.PROVIDER_DISPATCH,
            "execution": EntitlementActionType.PROVIDER_DISPATCH,
            "command": EntitlementActionType.REPOSITORY_WRITE,
            "result": EntitlementActionType.REVIEW_EXACT_SHA if grant.operation == "review" else EntitlementActionType.REPOSITORY_WRITE,
        }[stage]
        if query.action_type != expected_action:
            raise TeamAuthorizationError("wrong entitlement action for team boundary")
        if stage in {"dispatch","execution"}:
            from decimal import Decimal
            if Decimal(str(query.reserved_spend_usd))*1000000 != budget.attempt_reservation_microusd:
                raise TeamAuthorizationError("query amount differs from authoritative reservation")
        if command is not None and (stage != "command" or command not in grant.commands):
            raise TeamAuthorizationError("local command not granted")
        if stage == "command" and command is None:
            raise TeamAuthorizationError("local command required")
        if stage == "command":
            used=connection.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_team_boundary WHERE attempt_digest=%s AND stage='command'",(attempt_digest,)).fetchone()["n"]
            if used>=grant.max_local_commands:
                raise TeamAuthorizationError("local command budget exhausted")
        if stage == "dispatch":
            if attempt_digest is None:
                raise TeamAuthorizationError("durable attempt required")
            self._reserve(connection, grant, budget, attempt_digest)
        elif stage in {"execution", "command", "result"}:
            held = connection.execute(f"SELECT 1 FROM {self.schema}.director_primary_provider_spend WHERE attempt_digest=%s AND grant_digest=%s", (attempt_digest, grant.digest)).fetchone()
            if held is None:
                raise TeamAuthorizationError("team attempt reservation missing")
        result = self.entitlements._query_action_boundary_in_transaction(
            connection, query, manifest_paths=tuple(dict.fromkeys((*tuple(x.path for x in grant.source),*grant.writable_paths))),
            team_target=grant.commit, spend_reserved=(stage in {"dispatch","execution"}),
        )
        if not result.permitted or result.entitlement_generation != grant.generation:
            raise TeamAuthorizationError(result.denial_reason or "entitlement generation changed")
        connection.execute(f"INSERT INTO {self.schema}.director_primary_team_boundary(nonce,grant_digest,stage,attempt_digest,evidence_digest) VALUES(%s,%s,%s,%s,%s)", (query.runtime_proof.nonce_digest,grant.digest,stage,attempt_digest,document_digest({"command":command,"authorization":result.authorization_binding_digest})))
        return grant

    def _reserve(self, connection, grant, budget, attempt_digest):
        self.budgets.reserve(connection,authority_digest=grant.digest,provider="antigravity",
            task_id=grant.task_id,attempt_digest=attempt_digest)

    def revoke(self, connection, *, grant_digest: str, owner_signature: str):
        payload = canonical_json_bytes({"operation":"revoke-team-grant","grant_digest":grant_digest})
        expected = hmac.new(self.entitlements._owner_authority_secret,payload,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,owner_signature):
            raise TeamAuthorizationError("revocation owner signature invalid")
        connection.execute(f"UPDATE {self.schema}.director_primary_team_grant SET revoked=true WHERE grant_digest=%s",(grant_digest,))


class TeamAttemptAdmission:
    """Typed handoff to the existing writer-fenced primary attempt transaction."""
    def __init__(self, authority: TeamAuthority, grant_digest: str, query):
        self.authority, self.grant_digest, self.query = authority, grant_digest, query

    def admit(self, connection, *, task_id, assignment_id, provider, attempt_digest):
        grant = self.authority.authorize(connection, grant_digest=self.grant_digest,
            query=self.query, stage="dispatch", attempt_digest=attempt_digest)
        if (grant.task_id,grant.assignment_id,provider) != (task_id,assignment_id,"antigravity"):
            raise TeamAuthorizationError("parallel attempt/task grant mismatch")
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
