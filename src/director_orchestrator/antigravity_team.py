# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 02:52 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
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
            or "\\" in value or ":" in value or any(ord(char)<32 or ord(char)==127 for char in value)):
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
    native_project_id: Identifier
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
        if len(self.commands)>self.max_local_commands:
            raise TeamAuthorizationError("declared checks exceed local command ceiling")
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


CAMPAIGN_LIMIT_MICROUSD = 10_000_000
DAILY_LIMIT_MICROUSD = 50_000_000
TASK_LIMIT_MICROUSD = 14_000_000


class SubscriptionPolicy(ClosedModel):
    """Owner-declared bounds for a native subscription lane.

    A subscription vendor supplies no hard per-call USD guarantee, so these
    bounds are the owner's own limit on how much of the subscription an attempt
    may consume. They are never converted into a vendor price, and they never
    replace the separate application-side micro-USD admission cap below.
    """
    schema_version: Literal["owner-bounded-subscription/v1"]
    max_calls: Annotated[int, Field(gt=0)]
    max_turns: Annotated[int, Field(gt=0)]
    max_seconds: Annotated[int, Field(gt=0)]
    max_output_bytes: Annotated[int, Field(gt=0)]


class ProviderExecutionAuthority(ClosedModel):
    """Immutable transport authority derived only from a verified reservation."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["provider-execution-authority/v1"] = "provider-execution-authority/v1"
    authority_digest: Digest
    budget_document_digest: str
    provider_id: Identifier
    task_id: Identifier
    attempt_digest: str
    execution_task_id: Identifier
    billing_mode: Literal["vendor-metered-usd", "owner-bounded-subscription"]
    max_turns: int | None = None
    max_seconds: int | None = None
    max_output_bytes: int | None = None

    @model_validator(mode="after")
    def check_bounds(self):
        for value in (self.budget_document_digest, self.attempt_digest):
            if re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value) is None:
                raise ValueError("execution authority digest is invalid")
        limits = (self.max_turns, self.max_seconds, self.max_output_bytes)
        if self.billing_mode == "owner-bounded-subscription":
            if any(value is None or value < 1 for value in limits):
                raise ValueError("subscription execution requires all positive bounds")
        elif any(value is not None for value in limits):
            raise ValueError("metered execution cannot carry subscription bounds")
        return self

    @classmethod
    def from_reserved_budget(cls, budget, *, attempt_digest, execution_task_id=None):
        policy = budget.subscription_policy
        return cls(authority_digest=budget.grant_digest,
            budget_document_digest=document_digest(budget.model_dump(mode="json")),
            provider_id=budget.provider_id, task_id=budget.task_id,
            execution_task_id=execution_task_id or budget.task_id,
            attempt_digest=attempt_digest, billing_mode=budget.billing_mode,
            max_turns=None if policy is None else policy.max_turns,
            max_seconds=None if policy is None else policy.max_seconds,
            max_output_bytes=None if policy is None else policy.max_output_bytes)

    def require_supported_execution(self):
        # No installed route currently proves hard native-turn enforcement.
        # A returned turn count, hop budget or caller capability boolean is not
        # such proof. A future enrolled supervisor must replace this gate.
        if self.billing_mode == "owner-bounded-subscription":
            raise TeamAuthorizationError("subscription hard-turn enforcement is unavailable")


class ProviderBudget(ClosedModel):
    """Owner-approved limits persisted before an execution request arrives.

    ``attempt_reservation_microusd`` is an application admission cap, not a
    claim about what a vendor will bill. It is reserved for every admitted
    attempt in either billing mode, so an attempt whose vendor cost is unknown
    is never counted as zero and never silently refunded.
    """
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
    campaign_id: Identifier | None = None
    billing_mode: Literal["vendor-metered-usd", "owner-bounded-subscription"] = "vendor-metered-usd"
    subscription_policy: SubscriptionPolicy | None = None

    @model_validator(mode="after")
    def check_billing_mode(self):
        if (self.billing_mode == "owner-bounded-subscription") != (self.subscription_policy is not None):
            raise TeamAuthorizationError("trusted subscription billing requires explicit owner bounds")
        return self

    def signing_document(self):
        return self.model_dump(mode="json", exclude={"owner_signature"})

    def verify(self, secret: bytes):
        expected = hmac.new(secret, canonical_json_bytes(self.signing_document()), hashlib.sha256).hexdigest()
        if len(secret) < 32 or not hmac.compare_digest(expected, self.owner_signature):
            raise TeamAuthorizationError("budget authority signature invalid")


class CampaignBudget(ClosedModel):
    """Immutable owner-signed monetary identity spanning validation generations.

    Membership is durable and per provider budget: once a budget joins a
    campaign, every attempt it funds counts against the one campaign ceiling,
    across days, generations, tasks, providers and processes.
    """
    schema_version: Literal["primary-provider-campaign/v1"]
    campaign_id: Identifier
    project_id: Identifier
    campaign_limit_microusd: Annotated[int, Field(gt=0)]
    owner_signature: Digest

    def signing_document(self):
        return self.model_dump(mode="json", exclude={"owner_signature"})

    @property
    def digest(self):
        return document_digest(self.signing_document())

    def verify(self, secret: bytes):
        expected = hmac.new(secret, canonical_json_bytes(self.signing_document()), hashlib.sha256).hexdigest()
        if len(secret) < 32 or not hmac.compare_digest(expected, self.owner_signature):
            raise TeamAuthorizationError("campaign authority signature invalid")


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
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_provider_campaign (
            campaign_digest text PRIMARY KEY, campaign_id text NOT NULL UNIQUE,
            project_id text NOT NULL, document jsonb NOT NULL,
            campaign_limit_microusd bigint NOT NULL CHECK(campaign_limit_microusd>0))""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_provider_campaign_member (
            authority_digest text PRIMARY KEY REFERENCES
                {schema}.director_primary_provider_budget(authority_digest),
            campaign_digest text NOT NULL REFERENCES
                {schema}.director_primary_provider_campaign(campaign_digest))""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_team_boundary (
            nonce text PRIMARY KEY, grant_digest text NOT NULL REFERENCES
                {schema}.director_primary_team_grant(grant_digest),
            stage text NOT NULL CHECK(stage IN ('disclosure','dispatch','execution','command','result')),
            attempt_digest text, evidence_digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.director_primary_team_candidate (
            attempt_digest text PRIMARY KEY REFERENCES {schema}.director_primary_provider_spend(attempt_digest),
            grant_digest text NOT NULL REFERENCES {schema}.director_primary_team_grant(grant_digest),
            capture_digest text NOT NULL CHECK(capture_digest ~ '^[0-9a-f]{{64}}$'),
            candidate_sha text NOT NULL CHECK(candidate_sha ~ '^[0-9a-f]{{40}}$'))""",
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
                "director_primary_team_grant","director_primary_provider_budget","director_primary_provider_spend","director_primary_provider_usage","director_primary_team_boundary","director_primary_team_candidate","director_primary_provider_campaign","director_primary_provider_campaign_member")),
    )


def verify_team_schema(connection, schema):
    """Fail on missing/disabled authority guards or a changed storage contract."""
    statements = team_ddl(schema)
    columns = {
        "director_primary_team_grant": {"grant_digest", "document", "budget_document", "revoked"},
        "director_primary_provider_budget": {"authority_digest", "document"},
        "director_primary_provider_spend": {"attempt_digest", "grant_digest", "provider_id", "project_id", "task_id", "budget_day", "reserved_microusd", "token_limit"},
        "director_primary_provider_usage": {"attempt_digest", "document", "actual_microusd", "observed_tokens"},
        "director_primary_team_boundary": {"nonce", "grant_digest", "stage", "attempt_digest", "evidence_digest", "created_at"},
        "director_primary_team_candidate": {"attempt_digest", "grant_digest", "capture_digest", "candidate_sha"},
    }
    json_columns={"document","budget_document"}
    integer_columns={"reserved_microusd","token_limit","actual_microusd","observed_tokens"}
    for table, expected in columns.items():
        actual = connection.execute("SELECT column_name,is_nullable,data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s", (schema,table)).fetchall()
        if {r["column_name"] for r in actual} != expected or any(r["is_nullable"] != "NO" and (table,r["column_name"]) != ("director_primary_team_boundary","attempt_digest") for r in actual):
            raise TeamAuthorizationError("team schema column inventory changed: " + table)
        for column in actual:
            name=column["column_name"]
            expected_type=("jsonb" if name in json_columns else "bigint" if name in integer_columns else
                "boolean" if name=="revoked" else "date" if name=="budget_day" else
                "timestamp with time zone" if name=="created_at" else "text")
            if column["data_type"]!=expected_type:
                raise TeamAuthorizationError("team schema column type changed")
        row = connection.execute("""SELECT t.tgtype,t.tgenabled,p.proname,n.nspname FROM pg_trigger t
            JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE t.tgrelid=%s::regclass AND t.tgname=%s AND NOT t.tgisinternal""",
            (schema+"."+table,table+"_guard")).fetchone()
        if row is None or (row["tgtype"],row["tgenabled"],row["proname"],row["nspname"]) != (27,"O","director_primary_team_guard",schema):
            raise TeamAuthorizationError("team immutable guard changed: " + table)
        primary = connection.execute("SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=%s::regclass AND contype='p' AND convalidated",(schema+"."+table,)).fetchall()
        key="grant_digest" if table=="director_primary_team_grant" else "authority_digest" if table=="director_primary_provider_budget" else "nonce" if table=="director_primary_team_boundary" else "attempt_digest"
        if len(primary)!=1 or primary[0]["definition"]!="PRIMARY KEY ("+key+")":
            raise TeamAuthorizationError("team primary key changed")
        checks=connection.execute("SELECT pg_get_constraintdef(oid) AS definition,convalidated FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",(schema+"."+table,)).fetchall()
        expected_checks={
            "director_primary_provider_spend":{"CHECK ((reserved_microusd > 0))","CHECK ((token_limit > 0))"},
            "director_primary_provider_usage":{"CHECK ((actual_microusd >= 0))","CHECK ((observed_tokens >= 0))"},
            "director_primary_team_boundary":{"CHECK ((stage = ANY (ARRAY['disclosure'::text, 'dispatch'::text, 'execution'::text, 'command'::text, 'result'::text])))"},
            "director_primary_team_candidate":{"CHECK ((capture_digest ~ '^[0-9a-f]{64}$'::text))","CHECK ((candidate_sha ~ '^[0-9a-f]{40}$'::text))"},
        }.get(table,set())
        if {r["definition"] for r in checks}!=expected_checks or any(not r["convalidated"] for r in checks):
            raise TeamAuthorizationError("team check constraints changed")
    function = connection.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure",(schema+".director_primary_team_guard()",)).fetchone()
    expected_body = next(s for s in statements if "CREATE OR REPLACE FUNCTION" in s).split("AS $$",1)[1].rsplit("$$",1)[0].strip()
    if function is None or function["prosrc"].strip()!=expected_body:
        raise TeamAuthorizationError("team immutable guard implementation changed")
    index = connection.execute("""SELECT i.indisunique,i.indisvalid,i.indisready,pg_get_expr(i.indpred,i.indrelid) AS predicate,
        pg_get_indexdef(i.indexrelid,1,true) AS key_column
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relname='director_primary_team_execution_once'""",(schema,)).fetchone()
    if index is None or not all(index[k] for k in ("indisunique","indisvalid","indisready")) or index["predicate"]!="(stage = 'execution'::text)" or index["key_column"]!="attempt_digest":
        raise TeamAuthorizationError("team execution uniqueness guard changed")


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
        if (budget.task_limit_microusd > TASK_LIMIT_MICROUSD
                or budget.daily_limit_microusd > DAILY_LIMIT_MICROUSD):
            raise TeamAuthorizationError("budget exceeds the deterministic launch ceiling")
        rows=connection.execute(f"SELECT document FROM {self.schema}.director_primary_provider_budget WHERE document->>'project_id'=%s",(budget.project_id,)).fetchall()
        for row in rows:
            old=ProviderBudget.model_validate_json(json.dumps(row["document"]))
            if ((old.task_id==budget.task_id and old.task_limit_microusd!=budget.task_limit_microusd)
                    or (old.day==budget.day and old.daily_limit_microusd!=budget.daily_limit_microusd)):
                raise TeamAuthorizationError("persisted budget ceiling cannot change")
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) VALUES(%s,%s::jsonb)",(budget.grant_digest,budget.model_dump_json()))
        if budget.campaign_id is not None:
            self._join_campaign(connection,budget)

    def register_campaign(self, connection, campaign: CampaignBudget):
        """Persist one immutable campaign identity; re-registration cannot change it."""
        campaign.verify(self.entitlements._owner_authority_secret)
        if campaign.campaign_limit_microusd > CAMPAIGN_LIMIT_MICROUSD:
            raise TeamAuthorizationError("campaign exceeds the deterministic launch ceiling")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("primary-provider-budget:"+campaign.project_id,))
        row=connection.execute(f"SELECT campaign_digest,project_id,campaign_limit_microusd FROM {self.schema}.director_primary_provider_campaign WHERE campaign_id=%s",(campaign.campaign_id,)).fetchone()
        if row is not None:
            if (row["campaign_digest"],row["project_id"],row["campaign_limit_microusd"])!=(
                    campaign.digest,campaign.project_id,campaign.campaign_limit_microusd):
                raise TeamAuthorizationError("persisted campaign identity cannot change")
            return campaign
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_campaign(campaign_digest,campaign_id,project_id,document,campaign_limit_microusd) VALUES(%s,%s,%s,%s::jsonb,%s)",(campaign.digest,campaign.campaign_id,campaign.project_id,campaign.model_dump_json(),campaign.campaign_limit_microusd))
        return campaign

    def _join_campaign(self, connection, budget: ProviderBudget):
        row=connection.execute(f"SELECT campaign_digest,project_id FROM {self.schema}.director_primary_provider_campaign WHERE campaign_id=%s",(budget.campaign_id,)).fetchone()
        if row is None or row["project_id"]!=budget.project_id:
            raise TeamAuthorizationError("registered campaign identity required before its budgets")
        held=connection.execute(f"SELECT campaign_digest FROM {self.schema}.director_primary_provider_campaign_member WHERE authority_digest=%s",(budget.grant_digest,)).fetchone()
        if held is not None:
            if held["campaign_digest"]!=row["campaign_digest"]:
                raise TeamAuthorizationError("campaign membership is immutable")
            return
        connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_campaign_member(authority_digest,campaign_digest) VALUES(%s,%s)",(budget.grant_digest,row["campaign_digest"]))

    def _campaign_admission(self, connection, budget: ProviderBudget, amount: int):
        """Charge one cross-generation campaign ceiling inside the caller's transaction."""
        row=connection.execute(f"""SELECT c.campaign_digest,c.campaign_id,c.campaign_limit_microusd
            FROM {self.schema}.director_primary_provider_campaign_member m
            JOIN {self.schema}.director_primary_provider_campaign c USING(campaign_digest)
            WHERE m.authority_digest=%s""",(budget.grant_digest,)).fetchone()
        if row is None or row["campaign_id"]!=budget.campaign_id:
            raise TeamAuthorizationError("campaign membership missing for this provider budget")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("primary-provider-campaign:"+row["campaign_id"],))
        # Every reservation ever made under this campaign counts, including the
        # failed and ambiguous ones, on any day, task, generation or process.
        spent=connection.execute(f"""SELECT COALESCE(sum(GREATEST(s.reserved_microusd,COALESCE(u.actual_microusd,0))),0) AS total
            FROM {self.schema}.director_primary_provider_spend s
            JOIN {self.schema}.director_primary_provider_campaign_member m ON m.authority_digest=s.grant_digest
            LEFT JOIN {self.schema}.director_primary_provider_usage u ON u.attempt_digest=s.attempt_digest
            WHERE m.campaign_digest=%s""",(row["campaign_digest"],)).fetchone()
        if int(spent["total"])+amount>int(row["campaign_limit_microusd"]):
            raise TeamAuthorizationError("campaign monetary authority exhausted")

    def _subscription_admission(self, connection, budget: ProviderBudget):
        """Owner-bounded subscription lanes are admitted by call count, not price."""
        policy=budget.subscription_policy
        used=connection.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_spend WHERE grant_digest=%s",(budget.grant_digest,)).fetchone()["n"]
        if int(used)>=policy.max_calls:
            raise TeamAuthorizationError("owner-bounded subscription call budget exhausted")

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
        if budget.billing_mode=="owner-bounded-subscription":
            self._subscription_admission(connection,budget)
        if budget.campaign_id is not None:
            self._campaign_admission(connection,budget,amount)
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


class CampaignAttemptAdmission:
    """Monetary admission for one campaign attempt, charged in the caller's transaction.

    The task binding comes from the signed persisted budget, never from the
    caller, so a validation lane cannot spend another task's authority.
    """
    def __init__(self, authority: PrimaryProviderBudgetAuthority, campaign_id: str):
        self.authority,self.campaign_id=authority,campaign_id

    def admit(self, connection, *, authority_digest, provider, attempt_digest):
        row=connection.execute(f"SELECT document FROM {self.authority.schema}.director_primary_provider_budget WHERE authority_digest=%s",(authority_digest,)).fetchone()
        if row is None:
            raise TeamAuthorizationError("authoritative provider budget missing")
        budget=ProviderBudget.model_validate_json(json.dumps(row["document"]))
        if budget.campaign_id != self.campaign_id:
            raise TeamAuthorizationError("provider budget belongs to another campaign")
        return self.authority.reserve(connection,authority_digest=authority_digest,provider=provider,
            task_id=budget.task_id,attempt_digest=attempt_digest)


class PrimaryMonetaryAdmissions:
    """Team-independent resolver: every paid primary lane needs one signed budget."""
    def __init__(self, authority: PrimaryProviderBudgetAuthority, *, pool):
        self.authority,self.pool=authority,pool

    def admissions(self, provider, packet):
        with self.pool.connection() as connection,connection.transaction():
            day = self.authority.entitlements._clock().astimezone(timezone.utc).date().isoformat()
            rows=connection.execute(f"""SELECT authority_digest FROM {self.authority.schema}.director_primary_provider_budget
                WHERE document->>'task_id'=%s AND document->>'provider_id'=%s
                AND document->>'project_id'=%s AND document->>'day'=%s""",
                (packet["task_id"],provider,packet["project"],day)).fetchall()
            if len(rows)!=1:
                raise TeamAuthorizationError("selected provider lacks one authoritative daily task budget")
            return dict(budget_admission=ProviderBudgetAdmission(self.authority,rows[0]["authority_digest"]))


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
        if stage in {"dispatch", "execution", "command"} and budget.subscription_policy is not None:
            raise TeamAuthorizationError("subscription hard-turn enforcement is unavailable for the team worker")
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
        reserved = self.budgets.reserve(connection,authority_digest=grant.digest,provider="antigravity",
            task_id=grant.task_id,attempt_digest=attempt_digest)
        ProviderExecutionAuthority.from_reserved_budget(
            reserved, attempt_digest=attempt_digest).require_supported_execution()

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
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 02:52 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | END]]
