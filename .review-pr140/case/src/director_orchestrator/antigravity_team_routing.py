# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | BEGIN]]
"""Explicit primary factory composition for registered engineering assignments."""
from __future__ import annotations

from .antigravity_team import TeamAuthorizationError, TeamAttemptAdmission, ProviderBudgetAdmission
from .antigravity_team_transport import team_packet_adapter
from .entitlement_contracts import document_digest

TEAM_FORMAT = ("antigravity-team-engineering-v1", "qualified-native-worker-v1")


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
        self.query_factory,self.worker_factory=query_factory,worker_factory
        self.transport_arguments=dict(authority=authority,pool=pool,worker_verifier_secret=worker_verifier_secret,
            source_root=source_root,artifact_store=artifact_store,integrator=integrator,writer_owner_token=writer_owner_token)

    def grant_for(self, packet):
        with self.pool.connection() as connection,connection.transaction():
            rows=connection.execute(f"""SELECT grant_digest FROM {self.authority.schema}.director_primary_team_grant
                WHERE document->>'task_id'=%s AND document->>'assignment_id'=%s AND NOT revoked""",
                (packet["task_id"],packet["assignment_id"])).fetchall()
            if len(rows)!=1:
                raise TeamAuthorizationError("exactly one active registered team assignment is required")
            grant,_=self.authority._load(connection,rows[0]["grant_digest"])
            if grant.packet_digest!=document_digest(packet):
                raise TeamAuthorizationError("registered team packet changed")
            return grant

    def admissions(self, provider, packet):
        if provider=="antigravity":
            grant=self.grant_for(packet)
            return dict(team_admission=TeamAttemptAdmission(self.authority,grant.digest,
                self.query_factory(grant,"dispatch",None)))
        with self.pool.connection() as connection,connection.transaction():
            rows=connection.execute(f"""SELECT authority_digest FROM {self.authority.schema}.director_primary_provider_budget
                WHERE document->>'task_id'=%s AND document->>'provider_id'=%s
                AND document->>'project_id'=%s AND document->>'day'=CURRENT_DATE::text""",
                (packet["task_id"],provider,packet["project"])).fetchall()
            if len(rows)!=1:
                raise TeamAuthorizationError("selected provider lacks one authoritative daily task budget")
            return dict(budget_admission=ProviderBudgetAdmission(self.authority.budgets,rows[0]["authority_digest"]))

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
