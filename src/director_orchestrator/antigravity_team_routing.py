# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | BEGIN]]
"""Explicit primary factory composition for registered engineering assignments."""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

from .antigravity_team import (
    TeamAuthorizationError, TeamAttemptAdmission, ProviderBudgetAdmission,
    PrimaryProviderBudgetAuthority, ProviderAttemptBudget,
)
from .antigravity_team_transport import team_packet_adapter
from .entitlement_contracts import document_digest

TEAM_FORMAT = ("antigravity-team-engineering-v1", "qualified-native-worker-v1")


class PrimaryProviderBudgetRouting:
    """Resolve existing signed daily budgets independently of team enrollment."""

    def __init__(self, *, authority: PrimaryProviderBudgetAuthority, pool):
        if not isinstance(authority, PrimaryProviderBudgetAuthority):
            raise TeamAuthorizationError("typed enrolled provider budget authority required")
        self.authority, self.pool = authority, pool

    def admission(self, provider, packet, *, deadline=None):
        from .langgraph_postgres import bind_transaction_deadline, bounded_connection
        day = self.authority.entitlements._clock().astimezone(timezone.utc).date().isoformat()
        with bounded_connection(self.pool, deadline) as connection, connection.transaction():
            bind_transaction_deadline(connection, deadline)
            rows = connection.execute(f"""SELECT authority_digest FROM {self.authority.schema}.director_primary_provider_budget
                WHERE document->>'task_id'=%s AND document->>'provider_id'=%s
                AND document->>'project_id'=%s AND document->>'day'=%s""",
                (packet["task_id"], provider, packet["project"], day)).fetchall()
        if len(rows) != 1:
            raise TeamAuthorizationError("selected provider lacks one authoritative daily task budget")
        # Lookup is not admission: signature, actual amount and UTC expiration
        # are rechecked under the project lock inside the attempt transaction.
        return ProviderBudgetAdmission(self.authority, rows[0]["authority_digest"])


def configured_provider_budget_routing(settings, handle):
    """Open only already-enrolled authority on the primary pool; never bootstrap."""
    from psycopg.conninfo import conninfo_to_dict
    from .entitlement_control_plane import (
        EntitlementControlPlaneStore, _bootstrap_schema, _read_secret_file,
        _require_schema, _storage_identity,
    )
    from .langgraph_postgres import _parse_connection_target, _storage_identity as primary_storage_identity

    if not settings.entitlement_control_plane_enabled:
        raise TeamAuthorizationError("live primary requires enrolled daily provider budget authority")
    schema = _require_schema(settings.entitlement_postgres_schema)
    dsn_path = Path(settings.entitlement_postgres_dsn_file)
    if not dsn_path.is_file() or dsn_path.stat().st_size > 65_536:
        raise TeamAuthorizationError("existing entitlement DSN file is required for live provider budgets")
    parsed = _parse_connection_target(dsn_path.read_text(encoding="utf-8").strip(),
        conninfo_to_dict=conninfo_to_dict,
        allow_insecure_local_transport=settings.entitlement_allow_insecure_local_postgres)
    primary = _parse_connection_target(settings.langgraph_primary_postgres_dsn,
        conninfo_to_dict=conninfo_to_dict,
        allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres)
    identity = _storage_identity(parsed, schema=schema)
    if (identity != _storage_identity(primary, schema=schema)
            or primary_storage_identity(parsed, schema=handle.schema) != handle.storage_identity):
        raise TeamAuthorizationError("daily budget entitlement and primary stores must share database and role")
    store = EntitlementControlPlaneStore(handle.pool, schema=schema, storage_identity=identity,
        trusted_verifier_secret=_read_secret_file(settings.entitlement_trusted_verifier_secret_file),
        route_verifier_secret=_read_secret_file(settings.entitlement_route_verifier_secret_file),
        owner_authority_secret=_read_secret_file(settings.entitlement_owner_authority_secret_file))
    with handle.pool.connection() as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        _bootstrap_schema(connection, schema=schema, storage_identity=identity, allow_create=False)
    return PrimaryProviderBudgetRouting(
        authority=PrimaryProviderBudgetAuthority(store, primary_schema=handle.schema), pool=handle.pool)


def live_provider_admissions(*, settings, budget_routing, team_routing, provider, packet, attempt_usd,
                             deadline=None):
    """Every live attempt supplies monetary authority, including team-off lanes.

    `deadline` is the caller's absolute bound: resolving authority is real
    database work that runs before the ledger's own transaction opens, so it
    belongs inside the same bound rather than ahead of it.
    """
    if not isinstance(budget_routing, PrimaryProviderBudgetRouting):
        raise TeamAuthorizationError("live primary requires typed daily provider budget routing")
    limits = ProviderAttemptBudget.from_usd(project_id=packet["project"],
        attempt_usd=attempt_usd, task_usd=settings.max_task_budget_usd,
        daily_usd=settings.max_daily_provider_budget_usd)
    if team_routing is not None and provider == "antigravity":
        if not settings.langgraph_primary_antigravity_team_enabled or not isinstance(team_routing, PrimaryTeamRouting):
            raise TeamAuthorizationError("primary team mode lacks typed enrolled routing services")
        admissions = team_routing.admissions(provider, packet, deadline=deadline)
    else:
        admissions = dict(budget_admission=budget_routing.admission(provider, packet, deadline=deadline))
    return dict(admissions, budget_limits=limits)


class ParallelOnlyTeamTransport:
    def send(self, *args, **kwargs):
        raise TeamAuthorizationError("team transport requires a funded parallel attempt and exact grant")


class PrimaryTeamRouting:
    """Trusted service composition; the signed database grant selects the route.

    query_factory is the enrolled runtime proof client, never provider output or
    a local signature generator. Worker and artifact dependencies are separately
    protected. Construction does not enroll them or grant any issuer authority.
    """
    def __init__(self, *, settings, authority, pool, query_factory, worker_factory,
                 worker_verifier_secret, source_root, artifact_store, integrator,
                 writer_owner_token):
        if not settings.langgraph_primary_antigravity_team_enabled or not authority.enabled:
            raise TeamAuthorizationError("explicit primary team mode is disabled")
        if not callable(query_factory) or not callable(worker_factory):
            raise TeamAuthorizationError("enrolled runtime proof and worker clients are required")
        self.settings,self.authority,self.pool=settings,authority,pool
        self.budget_routing = PrimaryProviderBudgetRouting(authority=authority.budgets, pool=pool)
        self.query_factory,self.worker_factory=query_factory,worker_factory
        self.transport_arguments=dict(authority=authority,pool=pool,worker_verifier_secret=worker_verifier_secret,
            source_root=source_root,artifact_store=artifact_store,integrator=integrator,writer_owner_token=writer_owner_token)

    def grant_for(self, packet, *, deadline=None):
        from .langgraph_postgres import bind_transaction_deadline, bounded_connection
        with bounded_connection(self.pool, deadline) as connection,connection.transaction():
            bind_transaction_deadline(connection, deadline)
            rows=connection.execute(f"""SELECT grant_digest FROM {self.authority.schema}.director_primary_team_grant
                WHERE document->>'task_id'=%s AND document->>'assignment_id'=%s AND NOT revoked""",
                (packet["task_id"],packet["assignment_id"])).fetchall()
            if len(rows)!=1:
                raise TeamAuthorizationError("exactly one active registered team assignment is required")
            grant,_=self.authority._load(connection,rows[0]["grant_digest"])
            if grant.packet_digest!=document_digest(packet):
                raise TeamAuthorizationError("registered team packet changed")
            return grant

    def admissions(self, provider, packet, *, deadline=None):
        if provider=="antigravity":
            grant=self.grant_for(packet, deadline=deadline)
            return dict(team_admission=TeamAttemptAdmission(self.authority,grant.digest,
                self.query_factory(grant,"dispatch",None)))
        return dict(budget_admission=self.budget_routing.admission(provider, packet, deadline=deadline))

    def adapter(self, packet, attempt):
        if (attempt.format_id,attempt.transport_id)!=TEAM_FORMAT:
            raise TeamAuthorizationError("team assignment selected an unqualified format")
        grant=self.grant_for(packet)
        return team_packet_adapter(self.settings,**self.transport_arguments,grant_digest=grant.digest,
            attempt_digest=attempt.attempt_digest,query_factory=lambda stage,command:self.query_factory(grant,stage,command),
            worker=self.worker_factory(grant))

    def candidate(self, task_id, assignment_id):
        with self.pool.connection() as connection,connection.transaction():
            rows=connection.execute(f"""SELECT c.candidate_sha FROM {self.authority.schema}.director_primary_team_candidate c
                JOIN {self.authority.schema}.director_primary_team_grant g USING(grant_digest)
                WHERE g.document->>'task_id'=%s AND g.document->>'assignment_id'=%s AND NOT g.revoked""",
                (task_id,assignment_id)).fetchall()
            if len(rows)!=1:
                raise TeamAuthorizationError("one writer-admitted team candidate is required")
            return rows[0]["candidate_sha"]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
