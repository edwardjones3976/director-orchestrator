# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
"""Team authority contract and real PostgreSQL transaction tests."""
import hashlib
import hmac
import json
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import Mock
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from dataclasses import replace

from director_orchestrator.antigravity_team import (
    TeamGrant,SourceBlob,ProviderBudget,ProviderUsageReceipt,TeamAuthority,TeamAuthorizationError,team_ddl,
)
from director_orchestrator.antigravity_team_transport import parse_native_stream,verified_source_bytes,NativeTeamPacketTransport
from director_orchestrator.entitlement_contracts import canonical_json_bytes,document_digest,EntitlementActionType,effective_capabilities,RepositoryScope
from tests import test_entitlement_control_plane as fixtures


def signed(model,secret):
    signature=hmac.new(secret,canonical_json_bytes(model.signing_document()),hashlib.sha256).hexdigest()
    return model.model_copy(update={"owner_signature":signature})


def grant(**changes):
    values=dict(schema_version="antigravity-team-engineering/v1",grant_id="grant-one",task_id="task-one",assignment_id="assignment-one",project_id="uppercut",repository="edwardjones3976/director-orchestrator",packet_digest="a"*64,commit="a"*40,tree="d"*40,review_base=None,operation="engineering",source=(SourceBlob(path="src/allowed.py",sha256="e"*64),),writable_paths=("src/allowed.py",),prohibited_paths=("secrets",),commands=(("python","-m","unittest"),),workspace_id="isolated-one",provider_family="gemini",installation_id="install-one",installation_build="a"*64,executable_digest="a"*64,runtime_digest="a"*64,adapter_digest="a"*64,binding_digest="a"*64,native_account_id="worker-one",model="gemini-approved",material_authors=("claude",),generation=1,expires_at=datetime.now(timezone.utc)+timedelta(hours=1),max_tokens=1000,timeout_seconds=60,max_attempts=3,owner_signature="0"*64)
    values.update(changes)
    values.setdefault("native_project_id","native-project-one")
    return signed(TeamGrant(**values),fixtures.OWNER_SECRET)


def budget(g,**changes):
    values=dict(schema_version="primary-provider-budget/v1",grant_digest=g.digest,provider_id="antigravity",project_id=g.project_id,task_id=g.task_id,day=datetime.now(timezone.utc).date().isoformat(),task_limit_microusd=100,daily_limit_microusd=100,attempt_reservation_microusd=60,token_limit=1000,owner_signature="0"*64)
    values.update(changes)
    return signed(ProviderBudget(**values),fixtures.OWNER_SECRET)


class TeamContractTests(unittest.TestCase):
    def test_unqualified_worker_receives_no_private_packet_or_source(self):
        work=json.loads((Path(__file__).parents[1]/"docs/schemas/model-work-packet-v1.example.json").read_text())
        g=grant()
        work.update(task_id=g.task_id,assignment_id=g.assignment_id,repository=g.repository,starting_ref=g.commit,project=g.project_id,allowed_external_effects=[])
        g=grant(packet_digest=document_digest(work))
        @contextmanager
        def scope():yield connection
        connection=SimpleNamespace(transaction=scope)
        pool=SimpleNamespace(connection=scope)
        authority=SimpleNamespace(entitlements=SimpleNamespace(_owner_authority_secret=fixtures.OWNER_SECRET),_load=lambda c,d:(g,budget(g)))
        worker=Mock()
        worker.prepare.return_value={"evidence":{},"signature":"0"*64}
        transport=NativeTeamPacketTransport(authority=authority,pool=pool,grant_digest=g.digest,attempt_digest="sha256:"+"1"*64,query_factory=Mock(),worker=worker,worker_verifier_secret=b"w"*32,source_root=Path("does-not-exist"))
        with self.assertRaisesRegex(TeamAuthorizationError,"signature invalid"):
            transport.send(canonical_json_bytes(work),context=SimpleNamespace(timeout_seconds=60))
        worker.exchange.assert_not_called()
        self.assertEqual(set(worker.prepare.call_args.kwargs),{"grant_digest","attempt_digest","challenge"})
    def test_source_export_reads_only_exact_manifest_and_refuses_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"src").mkdir()
            (root/"src/allowed.py").write_bytes(b"safe")
            (root/"secret.txt").write_bytes(b"never disclose")
            g=grant(source=(SourceBlob(path="src/allowed.py",sha256=hashlib.sha256(b"safe").hexdigest()),))
            self.assertEqual(verified_source_bytes(g,root),{"src/allowed.py":b"safe"})
            (root/"src/allowed.py").write_bytes(b"changed")
            with self.assertRaises(TeamAuthorizationError):verified_source_bytes(g,root)
    def test_signature_binds_every_manifest_blob(self):
        g=grant()
        changed=g.model_copy(update={"source":(SourceBlob(path="src/allowed.py",sha256="f"*64),)})
        with self.assertRaises(TeamAuthorizationError):changed.verify(fixtures.OWNER_SECRET,datetime.now(timezone.utc))

    def test_reviewer_is_not_author_under_either_family_alias(self):
        for author in ("gemini","antigravity"):
            with self.subTest(author=author),self.assertRaises(ValueError):
                grant(operation="review",review_base="b"*40,writable_paths=(),commands=(),material_authors=(author,))

    def test_manifest_rejects_history_escape_and_windows_collision(self):
        for path in ("../secret",".git/config","C:/secret","src\\secret","/secret"):
            with self.subTest(path=path),self.assertRaises(ValueError):SourceBlob(path=path,sha256="a"*64)
        with self.assertRaises(ValueError):grant(source=(SourceBlob(path="src/A.py",sha256="a"*64),SourceBlob(path="src/a.py",sha256="a"*64)))

    def test_team_template_has_no_shared_effect_capabilities(self):
        self.assertFalse(any(c.startswith("integration.") for c in effective_capabilities("antigravity-team-engineering/v1")))

    def test_stream_unique_completion_and_tool_free_review(self):
        g=grant(operation="review",review_base="b"*40,writable_paths=(),commands=())
        events=[{"event":"init","conversation_id":"session-one","init":{"cwd":"/isolated","model":g.model}}, {"event":"result","result":{"conversation_id":"session-one","status":"SUCCESS","structured_output":{"status":"SUCCEEDED"}}}]
        def raw():return b"\n".join(json.dumps(e).encode() for e in events)
        result,session,_=parse_native_stream(raw(),grant=g,expected_cwd="/isolated")
        self.assertEqual(session,"session-one")
        events.append(events[-1])
        with self.assertRaises(TeamAuthorizationError):parse_native_stream(raw(),grant=g,expected_cwd="/isolated")
        events.pop()
        events.insert(1,{"event":"step_update","step_update":{"conversation_id":"session-one","step_type":"tool"}})
        with self.assertRaises(TeamAuthorizationError):parse_native_stream(raw(),grant=g,expected_cwd="/isolated")


class TeamPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.EntitlementControlPlanePostgresTests.setUpClass()
    @classmethod
    def tearDownClass(cls):fixtures.EntitlementControlPlanePostgresTests.tearDownClass()

    def setUp(self):
        registration=fixtures._registration
        def registered_targets(*args,**kwargs):
            value=registration(*args,**kwargs)
            value=replace(value,repository_scopes=(RepositoryScope(repository=value.repository_scopes[0].repository,refs=("a"*40,"c"*40),paths=("src/**",)),))
            return replace(value,owner_signature_digest=fixtures._owner_signature(value.signing_document()))
        with patch.object(fixtures,"PROFILE_TEMPLATE","antigravity-team-engineering/v1"),patch.object(fixtures,"_registration",registered_targets):
            fixtures.EntitlementControlPlanePostgresTests.setUp(self)
            attestation=fixtures._attestation(installation_id="install-one")
        self.store.grant_from_attestation(attestation,idempotency_key_digest=document_digest({"grant":"one"}))
        self.authority=TeamAuthority(self.store,primary_schema=self.schema,enabled=True)
        with self.store._pool.connection() as c,c.transaction():
            for sql in team_ddl(self.schema):c.execute(sql)
            # This focused entitlement fixture needs only the existing attempt
            # ledger identity column; the integration test uses the full primary DDL.
            c.execute(f"CREATE TABLE {self.schema}.director_primary_provider_attempt(attempt_digest text PRIMARY KEY)")
        self.g=grant()
        with self.store._pool.connection() as c,c.transaction():self.authority.register(c,self.g,budget(self.g))

    def tearDown(self):fixtures.EntitlementControlPlanePostgresTests.tearDown(self)

    def query(self,stage="disclosure",**changes):
        action={"disclosure":EntitlementActionType.SOURCE_READ,"dispatch":EntitlementActionType.PROVIDER_DISPATCH,"result":EntitlementActionType.REPOSITORY_WRITE}[stage]
        values=dict(ref=self.g.commit,path=self.g.source[0].path,action_type=action,effect_digest=self.g.digest)
        if stage=="dispatch":values.update(destination_id="uppercut",reserved_spend_usd=0.00006)
        values.update(changes)
        return fixtures._boundary_query(installation_id="install-one",**values)

    def test_full_manifest_authorization_and_nonce_replay(self):
        q=self.query()
        with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=q,stage="disclosure")
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=q,stage="disclosure")

    def test_review_target_is_distinct_from_installed_reviewer_build(self):
        g=grant(commit="c"*40,operation="review",review_base="a"*40,writable_paths=(),commands=())
        with self.store._pool.connection() as c,c.transaction():
            self.authority.register(c,g,budget(g))
            self.authority._reserve(c,g,budget(g),"sha256:"+"1"*64)
        q=self.query("result",ref=g.commit,effect_digest=g.digest,action_type=EntitlementActionType.REVIEW_EXACT_SHA)
        self.assertNotEqual(q.ref,q.runtime_proof.source_commit_sha)
        with self.store._pool.connection() as c,c.transaction():
            result=self.authority.authorize(c,grant_digest=g.digest,query=q,stage="result",attempt_digest="sha256:"+"1"*64)
        self.assertEqual(result.commit,"c"*40)

    def test_durable_grant_and_budget_rows_cannot_be_rewritten(self):
        with self.assertRaises(Exception):
            with self.store._pool.connection() as c,c.transaction():
                c.execute(f"UPDATE {self.schema}.director_primary_team_grant SET revoked=false")

    def test_one_denied_manifest_file_rolls_back_every_claim(self):
        bad=grant(source=(*self.g.source,SourceBlob(path="outside/denied.txt",sha256="a"*64)))
        with self.store._pool.connection() as c,c.transaction():self.authority.register(c,bad,budget(bad))
        q=self.query(effect_digest=bad.digest)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=bad.digest,query=q,stage="disclosure")
        with self.store._pool.connection() as c:
            self.assertEqual(c.execute(f"SELECT count(*) AS n FROM {self.schema}.entitlement_runtime_authorization_claim").fetchone()["n"],0)

    def test_budget_exhaustion_and_unknown_usage_remain_reserved(self):
        with self.store._pool.connection() as c,c.transaction():self.authority._reserve(c,self.g,budget(self.g),"sha256:"+"1"*64)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as c,c.transaction():self.authority._reserve(c,self.g,budget(self.g),"sha256:"+"2"*64)

    def test_concurrent_dispatch_claims_share_authoritative_daily_budget(self):
        def claim(index):
            try:
                with self.store._pool.connection() as c,c.transaction():
                    self.authority.authorize(c,grant_digest=self.g.digest,query=self.query("dispatch"),stage="dispatch",attempt_digest="sha256:"+str(index)*64)
                return True
            except TeamAuthorizationError:return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(claim,(1,2))),[False,True])

    def test_other_provider_spend_uses_the_same_project_daily_ceiling(self):
        other=signed(budget(self.g).model_copy(update={"grant_digest":"f"*64,"provider_id":"openai","task_id":"other-task"}),fixtures.OWNER_SECRET)
        with self.store._pool.connection() as c,c.transaction():
            self.authority.budgets.register(c,other)
            self.authority.budgets.reserve(c,authority_digest=other.grant_digest,provider="openai",task_id="other-task",attempt_digest="sha256:"+"1"*64)
        with self.assertRaisesRegex(TeamAuthorizationError,"budget exhausted"):
            with self.store._pool.connection() as c,c.transaction():
                self.authority.authorize(c,grant_digest=self.g.digest,query=self.query("dispatch"),stage="dispatch",attempt_digest="sha256:"+"2"*64)

    def test_unknown_prior_provider_usage_cannot_be_replaced_by_budget_hash(self):
        with self.store._pool.connection() as c,c.transaction():
            c.execute(f"INSERT INTO {self.schema}.director_primary_provider_attempt VALUES(%s)",("sha256:"+"f"*64,))
        with self.assertRaisesRegex(TeamAuthorizationError,"lacks authoritative"):
            with self.store._pool.connection() as c,c.transaction():
                self.authority._reserve(c,self.g,budget(self.g),"sha256:"+"1"*64)

    def test_usage_requires_meter_signature_and_cannot_refund_reserved_budget(self):
        attempt="sha256:"+"1"*64
        with self.store._pool.connection() as c,c.transaction():self.authority._reserve(c,self.g,budget(self.g),attempt)
        receipt=ProviderUsageReceipt(schema_version="primary-provider-usage/v1",attempt_digest=attempt,authority_digest=self.g.digest,provider_id="antigravity",actual_microusd=0,observed_tokens=1,complete=True,meter_signature="0"*64)
        with self.assertRaisesRegex(TeamAuthorizationError,"authenticated usage"):
            with self.store._pool.connection() as c,c.transaction():self.authority.budgets.record_usage(c,receipt)
        signature=hmac.new(fixtures.ROUTE_SECRET,canonical_json_bytes(receipt.signing_document()),hashlib.sha256).hexdigest()
        receipt=receipt.model_copy(update={"meter_signature":signature})
        with self.store._pool.connection() as c,c.transaction():self.authority.budgets.record_usage(c,receipt)
        with self.assertRaisesRegex(TeamAuthorizationError,"budget exhausted"):
            with self.store._pool.connection() as c,c.transaction():self.authority._reserve(c,self.g,budget(self.g),"sha256:"+"2"*64)

    def test_native_execution_claim_is_single_use_across_new_nonces(self):
        attempt="sha256:"+"1"*64
        with self.store._pool.connection() as c,c.transaction():self.authority._reserve(c,self.g,budget(self.g),attempt)
        with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=self.query("dispatch"),stage="execution",attempt_digest=attempt)
        with self.assertRaises(Exception):
            with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=self.query("dispatch"),stage="execution",attempt_digest=attempt)

    def test_primary_attempt_insert_failure_rolls_back_all_admissions(self):
        from tests.test_primary_workload import PostgresAdmissionTests,DIGEST
        from director_orchestrator.antigravity_team import TeamAttemptAdmission
        fixture=PostgresAdmissionTests()
        fixture.dsn=fixtures.TEST_DSN
        fixture.setUp()
        try:
            task,writer=fixture.task("antigravity")
            reservation=fixture.ready((task,writer))
            authority=TeamAuthority(self.store,primary_schema=fixture.schema,enabled=True)
            g=grant(task_id=task,assignment_id="assignment")
            with fixture.handle.pool.connection() as c,c.transaction():
                authority.register(c,g,budget(g))
                c.execute(f"CREATE FUNCTION {fixture.schema}.fail_team_attempt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected attempt insert failure'; END $$")
                c.execute(f"CREATE TRIGGER fail_team_attempt BEFORE INSERT ON {fixture.schema}.director_primary_provider_attempt FOR EACH ROW EXECUTE FUNCTION {fixture.schema}.fail_team_attempt()")
            admission=TeamAttemptAdmission(authority,g.digest,self.query("dispatch",effect_digest=g.digest))
            with self.assertRaisesRegex(Exception,"injected attempt insert failure"):
                fixture.parallel.reserve_provider_attempt(reservation=reservation,writer=writer,attempt_ordinal=1,
                    format_roster=(("antigravity-team-engineering-v1","native-cli"),),idempotency_key_digest=DIGEST,
                    budget_evidence_digest=DIGEST,team_admission=admission)
            for table in ("director_primary_provider_attempt","director_primary_workload_assignment","director_primary_provider_spend","director_primary_team_boundary"):
                self.assertEqual(fixture.count(table),0)
            with self.store._pool.connection() as c:
                self.assertEqual(c.execute(f"SELECT count(*) AS n FROM {self.schema}.entitlement_runtime_authorization_claim").fetchone()["n"],0)
        finally:fixture.tearDown()

    def test_attempt_failure_rolls_back_spend_and_nonce(self):
        q=self.query()
        with self.assertRaisesRegex(RuntimeError,"attempt failed"):
            with self.store._pool.connection() as c,c.transaction():
                self.authority._reserve(c,self.g,budget(self.g),"sha256:"+"1"*64)
                self.authority.authorize(c,grant_digest=self.g.digest,query=q,stage="disclosure")
                raise RuntimeError("attempt failed")
        with self.store._pool.connection() as c:
            for table in ("director_primary_provider_spend","entitlement_runtime_authorization_claim","director_primary_team_boundary"):
                self.assertEqual(c.execute(f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"],0)

    def test_revocation_between_disclosure_and_result(self):
        with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=self.query(),stage="disclosure")
        payload=canonical_json_bytes({"operation":"revoke-team-grant","grant_digest":self.g.digest})
        signature=hmac.new(fixtures.OWNER_SECRET,payload,hashlib.sha256).hexdigest()
        with self.store._pool.connection() as c,c.transaction():self.authority.revoke(c,grant_digest=self.g.digest,owner_signature=signature)
        with self.assertRaisesRegex(TeamAuthorizationError,"revoked"):
            with self.store._pool.connection() as c,c.transaction():self.authority.authorize(c,grant_digest=self.g.digest,query=self.query("result"),stage="result",attempt_digest="sha256:"+"1"*64)
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]
