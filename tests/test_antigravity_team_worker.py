# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:42 UTC | BEGIN]]
"""Transport/candidate tests; no test result represents native host enrollment."""
import base64
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from director_orchestrator.antigravity_team import TeamAuthority, TeamAuthorizationError, SourceBlob, team_ddl, verify_team_schema
from director_orchestrator.antigravity_team_artifacts import TeamArtifactStore, GitTeamCandidateIntegrator, decode_returned_files
from director_orchestrator.antigravity_team_worker import FrameChannel, LinuxNativeTeamWorker, sign
from director_orchestrator.antigravity_team_transport import NativeTeamPacketTransport
from director_orchestrator.entitlement_contracts import RepositoryScope, EntitlementActionType, document_digest
from tests.test_antigravity_team import grant, budget
from tests import test_entitlement_control_plane as fixtures


class WorkerProtocolTests(unittest.TestCase):
    def test_ordered_frames_reject_replay_tamper_and_reflection(self):
        wire=io.BytesIO()
        sender=FrameChannel(io.BytesIO(),wire,b"w"*32,"a"*64,sender="director")
        sender.send("prepare",{"public":"metadata"})
        raw=wire.getvalue()
        receiver=FrameChannel(io.BytesIO(raw+raw),io.BytesIO(),b"w"*32,"a"*64,sender="worker")
        self.assertEqual(receiver.receive("prepare"),{"public":"metadata"})
        with self.assertRaisesRegex(TeamAuthorizationError,"replay"):
            receiver.receive("prepare")
        reflected=FrameChannel(io.BytesIO(raw),io.BytesIO(),b"w"*32,"a"*64,sender="director")
        with self.assertRaises(TeamAuthorizationError):reflected.receive("prepare")
        corrupted=FrameChannel(io.BytesIO(raw.replace(b"metadata",b"modified")),io.BytesIO(),b"w"*32,"a"*64,sender="worker")
        with self.assertRaisesRegex(TeamAuthorizationError,"authentication"):
            corrupted.receive("prepare")

    def test_wrong_challenge_and_truncated_frame_fail_closed(self):
        wire=io.BytesIO()
        FrameChannel(io.BytesIO(),wire,b"w"*32,"a"*64,sender="director").send("capture",{})
        for raw,challenge in ((wire.getvalue(),"b"*64),(wire.getvalue()[:-1],"a"*64)):
            with self.assertRaises(TeamAuthorizationError):
                FrameChannel(io.BytesIO(raw),io.BytesIO(),b"w"*32,challenge,sender="worker").receive("capture")

    def test_unqualified_platform_never_starts_process(self):
        worker=LinuxNativeTeamWorker({},owner_secret=fixtures.OWNER_SECRET,issuer_secret=b"i"*32,worker_secret=b"w"*32)
        g=grant()
        with patch("director_orchestrator.antigravity_team_worker.sys.platform","unqualified"),patch("subprocess.Popen") as launch:
            with self.assertRaisesRegex(TeamAuthorizationError,"enrolled Linux"):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest="sha256:"+"1"*64,challenge="a"*64)
            launch.assert_not_called()

    def test_returned_bytes_reject_ungranted_path_hash_alias_and_review_write(self):
        g=grant()
        entry={"path":"src/allowed.py","sha256":hashlib.sha256(b"new").hexdigest(),"content_base64":base64.b64encode(b"new").decode()}
        self.assertEqual(decode_returned_files(g,[entry]),{"src/allowed.py":b"new"})
        for changes in ({"path":".git/config"},{"path":"src/allowed.py\nother"},{"sha256":"0"*64},{"content_base64":"!!!"}):
            with self.assertRaises((ValueError,TeamAuthorizationError)):
                decode_returned_files(g,[dict(entry,**changes)])
        with self.assertRaises(TeamAuthorizationError):decode_returned_files(g,[entry,entry])

    def test_durable_store_rejects_changed_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=TeamArtifactStore(Path(temporary))
            digest=store.put({"capture":"one"})
            self.assertEqual(digest,store.put({"capture":"one"}))
            (Path(temporary)/(digest+".json")).write_text("{}")
            with self.assertRaisesRegex(TeamAuthorizationError,"changed"):store.get(digest)

    def test_worker_claim_precedes_execution_and_revoked_command_never_runs(self):
        # Local fixture substitutes only host qualification and native process;
        # it tests actual supervisor ordering, durable claims and returned bytes.
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"claims").mkdir()
            (root/"work").mkdir()
            (root/"nativehome").mkdir()
            work={"task_id":"task-one"}
            g=grant(packet_digest=document_digest(work),source=(SourceBlob(path="src/allowed.py",sha256=hashlib.sha256(b"old").hexdigest()),))
            worker=LinuxNativeTeamWorker({"claims_root":str(root/"claims"),"workspace_root":str(root/"work"),
                "native_uid":1001,"native_gid":1001,"check_uid":1002,"check_gid":1002,
                "native_home":str(root/"nativehome"),"executable":"/native/agy","command_binaries":{"python":"/usr/bin/python"}},
                owner_secret=fixtures.OWNER_SECRET,issuer_secret=b"i"*32,worker_secret=b"w"*32)
            qualification={"grant_digest":g.digest,"native_state_manifest":{},"protected_files":{"/usr/bin/python":"d"*64},"expires_at":(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
            calls=[]
            write_session=[False]
            def native(argv,**kwargs):
                calls.append(argv)
                if argv[0]=="/native/agy":
                    self.assertEqual(argv[-2:],["--project",g.native_project_id])
                    if write_session[0]:(root/"nativehome/session.json").write_text('{"new_session":true}')
                packet={"checks":[]}
                proposal={"packet":packet,"returned_files":[]}
                events=[{"event":"init","conversation_id":"session-one","init":{"cwd":str(kwargs["cwd"]),"model":g.model}},
                    {"event":"result","result":{"conversation_id":"session-one","status":"SUCCESS","structured_output":proposal,"usage":{"total_tokens":1}}}]
                return 0,b"\n".join(json.dumps(e).encode() for e in events),b""
            def denied(argv):raise TeamAuthorizationError("revoked before command")
            with patch.object(worker,"qualify",return_value=qualification),patch.object(worker,"account_lock",return_value=nullcontext()),patch("director_orchestrator.antigravity_team_worker.os.chown",create=True),patch("director_orchestrator.antigravity_team_worker.run_unprivileged",side_effect=native):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest="sha256:"+"1"*64,challenge="a"*64)
                with self.assertRaisesRegex(TeamAuthorizationError,"revoked before command"):
                    worker.exchange(grant=g,work_packet=work,source={"src/allowed.py":b"old"},challenge="a"*64,
                        before_execute=lambda:self.assertTrue((root/"claims"/("1"*64)).exists()),before_local_command=denied)
                self.assertEqual(len(calls),1)
                with self.assertRaises(FileExistsError):
                    worker.prepare(grant=g,grant_digest=g.digest,attempt_digest="sha256:"+"1"*64,challenge="b"*64)
                self.assertEqual(len(calls),1)
                # A fresh task-qualified state can legitimately acquire its own
                # session file. Later command authorization verifies immutable
                # policy again but does not require the original empty home.
                newer=grant(grant_id="grant-two",packet_digest=g.packet_digest,source=g.source)
                qualification["grant_digest"]=newer.digest
                worker.prepare(grant=newer,grant_digest=newer.digest,attempt_digest="sha256:"+"2"*64,challenge="c"*64)
                write_session[0]=True
                capture=worker.exchange(grant=newer,work_packet=work,source={"src/allowed.py":b"old"},challenge="c"*64,
                    before_execute=lambda:None,before_local_command=lambda argv:None)
                self.assertIn("session.json",capture["evidence"]["native_state_manifest_after"])

    def test_general_host_qualification_cannot_authorize_another_grants_source(self):
        g=grant()
        worker=LinuxNativeTeamWorker({},owner_secret=fixtures.OWNER_SECRET,issuer_secret=b"i"*32,worker_secret=b"w"*32)
        with patch.object(worker,"qualify",return_value={"grant_digest":"f"*64}):
            with self.assertRaisesRegex(TeamAuthorizationError,"exact task grant"):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest="sha256:"+"1"*64,challenge="a"*64)


class TeamCandidatePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.EntitlementControlPlanePostgresTests.setUpClass()
    @classmethod
    def tearDownClass(cls):fixtures.EntitlementControlPlanePostgresTests.tearDownClass()

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.repository=self.root/"repository"
        self.repository.mkdir()
        self.git=Path(shutil.which("git")).resolve()
        def git(*args):
            return subprocess.check_output([str(self.git),"-C",str(self.repository),*args]).decode().strip()
        self.git_command=git
        git("init","-q")
        git("config","user.name","Fixture")
        git("config","user.email","fixture@invalid")
        (self.repository/"src").mkdir()
        (self.repository/"src/allowed.py").write_bytes(b"original\n")
        git("add","src/allowed.py")
        git("commit","-qm","Fixture base")
        commit,tree=git("rev-parse","HEAD"),git("rev-parse","HEAD^{tree}")
        registration=fixtures._registration
        def scoped(*args,**kwargs):
            value=registration(*args,**kwargs)
            value=replace(value,repository_scopes=(RepositoryScope(repository=value.repository_scopes[0].repository,refs=(commit,"a"*40),paths=("src/**",)),))
            return replace(value,owner_signature_digest=fixtures._owner_signature(value.signing_document()))
        with patch.object(fixtures,"PROFILE_TEMPLATE","antigravity-team-engineering/v1"),patch.object(fixtures,"_registration",scoped):
            fixtures.EntitlementControlPlanePostgresTests.setUp(self)
            attestation=fixtures._attestation(installation_id="install-one")
        self.store.grant_from_attestation(attestation,idempotency_key_digest=document_digest({"candidate":"one"}))
        self.authority=TeamAuthority(self.store,primary_schema=self.schema,enabled=True)
        self.work=json.loads((Path(__file__).parents[1]/"docs/schemas/model-work-packet-v1.example.json").read_text())
        self.work.update(task_id="task-one",assignment_id="assignment-one",repository="edwardjones3976/director-orchestrator",
            starting_ref=commit,project="uppercut",allowed_external_effects=[])
        self.g=grant(commit=commit,tree=tree,commands=(),packet_digest=document_digest(self.work),source=(SourceBlob(path="src/allowed.py",sha256=hashlib.sha256(b"original\n").hexdigest()),))
        self.attempt="sha256:"+"1"*64
        with self.store._pool.connection() as c,c.transaction():
            for statement in team_ddl(self.schema):c.execute(statement)
            c.execute(f"CREATE TABLE {self.schema}.director_primary_provider_attempt(attempt_digest text PRIMARY KEY)")
            c.execute(f"CREATE TABLE {self.schema}.director_primary_task_writer(task_id text PRIMARY KEY,engine_owner text,owner_token text,fencing_generation bigint,lease_expires_at timestamptz)")
            c.execute(f"INSERT INTO {self.schema}.director_primary_task_writer VALUES(%s,'langgraph','writer-one',1,now()+interval '10 minutes')",(self.g.task_id,))
            self.authority.register(c,self.g,budget(self.g))
            self.authority._reserve(c,self.g,budget(self.g),self.attempt)
        self.artifacts=TeamArtifactStore(self.root)
        self.integrator=GitTeamCandidateIntegrator(authority=self.authority,pool=self.store._pool,
            repository=self.repository,git_executable=self.git,git_sha256=hashlib.sha256(self.git.read_bytes()).hexdigest(),
            artifact_store=self.artifacts,worker_verifier_secret=b"w"*32)
        entry={"path":"src/allowed.py","sha256":hashlib.sha256(b"changed\n").hexdigest(),"content_base64":base64.b64encode(b"changed\n").decode()}
        self.capture=self.artifacts.put(sign(b"w"*32,{"grant_digest":self.g.digest,"attempt_digest":self.attempt,"returned_files":[entry],"completed_at":datetime.now(timezone.utc).isoformat()}))

    def tearDown(self):
        fixtures.EntitlementControlPlanePostgresTests.tearDown(self)
        self.temporary.cleanup()

    def integrate(self,**changes):
        query=fixtures._boundary_query(installation_id="install-one",ref=self.g.commit,path="src/allowed.py",action_type=EntitlementActionType.REPOSITORY_WRITE,effect_digest=self.g.digest)
        arguments=dict(grant=self.g,attempt_digest=self.attempt,capture_digest=self.capture,query=query,owner_token="writer-one",fencing_generation=1)
        arguments.update(changes)
        return self.integrator.integrate(**arguments)

    def test_exact_returned_bytes_become_durable_candidate_without_checkout_or_ref_change(self):
        candidate=self.integrate()
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)
        self.assertEqual((self.repository/"src/allowed.py").read_bytes(),b"original\n")
        self.assertEqual(self.git_command("show",candidate+":src/allowed.py"),"changed")
        self.assertEqual(self.git_command("rev-parse",candidate+"^"),self.g.commit)
        self.assertEqual(self.integrate(),candidate)
        with self.store._pool.connection() as c:
            self.assertEqual(c.execute(f"SELECT candidate_sha FROM {self.schema}.director_primary_team_candidate").fetchone()["candidate_sha"],candidate)

    def test_stale_writer_and_revoked_grant_cannot_create_candidate(self):
        with self.assertRaisesRegex(TeamAuthorizationError,"current writer"):self.integrate(fencing_generation=2)
        with self.store._pool.connection() as c,c.transaction():c.execute(f"UPDATE {self.schema}.director_primary_team_grant SET revoked=true")
        with self.assertRaisesRegex(TeamAuthorizationError,"revoked"):self.integrate()
        with self.store._pool.connection() as c:
            self.assertEqual(c.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_team_candidate").fetchone()["n"],0)

    def test_startup_inventory_detects_disabled_guard_and_changed_function(self):
        with self.store._pool.connection() as c,c.transaction():verify_team_schema(c,self.schema)
        with self.store._pool.connection() as c,c.transaction():c.execute(f"ALTER TABLE {self.schema}.director_primary_team_candidate DISABLE TRIGGER director_primary_team_candidate_guard")
        with self.assertRaisesRegex(TeamAuthorizationError,"guard changed"):
            with self.store._pool.connection() as c,c.transaction():verify_team_schema(c,self.schema)

    def test_authenticated_transport_reauthorizes_and_records_real_candidate_bytes(self):
        with self.store._pool.connection() as c,c.transaction():
            c.execute(f"ALTER TABLE {self.schema}.director_primary_provider_attempt ADD COLUMN task_id text, ADD COLUMN assignment_id text, ADD COLUMN status text, ADD COLUMN event_sequence bigint")
            c.execute(f"INSERT INTO {self.schema}.director_primary_provider_attempt VALUES(%s,%s,%s,'STARTED',1)",(self.attempt,self.g.task_id,self.g.assignment_id))
        entry={"path":"src/allowed.py","sha256":hashlib.sha256(b"transport changed\n").hexdigest(),"content_base64":base64.b64encode(b"transport changed\n").decode()}
        packet=json.loads((Path(__file__).parents[1]/"docs/schemas/model-result-packet-v1.example.json").read_text())
        packet.update({key:self.work[key] for key in ("task_id","assignment_id","idempotency_key","authority_snapshot_digest","correlation")})
        packet.update(provider="antigravity",checks=[],artifacts=[],side_effects=[])
        test=self
        class Worker:
            def prepare(self,**kwargs):
                self.challenge=kwargs["challenge"]
                return sign(b"w"*32,dict(kwargs,binding_digest=test.g.binding_digest,workspace_id=test.g.workspace_id,
                    containment_evidence_digest="f"*64,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
                    denied_access=["unassigned_source","git_history","coordinator_credentials","broker_keys","launcher_mutation","production_network"],authentication_mode="native-account"))
            def exchange(self,**kwargs):
                test.assertEqual(kwargs["source"],{"src/allowed.py":b"original\n"})
                kwargs["before_execute"]()
                proposal={"packet":packet,"returned_files":[entry]}
                stream="\n".join(json.dumps(e) for e in [{"event":"init","conversation_id":"native-fixture","init":{"cwd":"/isolated","model":test.g.model}},
                    {"event":"result","result":{"conversation_id":"native-fixture","status":"SUCCESS","structured_output":proposal,"usage":{"total_tokens":3,"input_tokens":2,"output_tokens":1}}}])
                evidence={key:getattr(test.g,key) for key in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","workspace_id")}
                evidence.update(grant_digest=test.g.digest,attempt_digest=test.attempt,packet_digest=test.g.packet_digest,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    native_project_id=test.g.native_project_id,native_identity_observations={"requested_project_id":test.g.native_project_id,"observed_project_id":None,"observed_account_id":None},
                    native_state_manifest_after={},
                    authentication_mode="native-account",cwd="/isolated",challenge=self.challenge,
                    source_manifest_digest=document_digest({"source":[b.model_dump() for b in test.g.source]}),
                    containment_evidence_digest="f"*64,native_stream=stream,exit_code=0,
                    changed_files=[{"path":entry["path"],"sha256":entry["sha256"]}],returned_files=[entry],executed_commands=[],checks=[])
                return sign(b"w"*32,evidence)
        def query(stage,command):
            action={"disclosure":EntitlementActionType.SOURCE_READ,"execution":EntitlementActionType.PROVIDER_DISPATCH,"result":EntitlementActionType.REPOSITORY_WRITE}[stage]
            arguments=dict(installation_id="install-one",ref=self.g.commit,path="src/allowed.py",action_type=action,effect_digest=self.g.digest)
            if stage=="execution":arguments.update(destination_id="uppercut",reserved_spend_usd=0.00006)
            return fixtures._boundary_query(**arguments)
        transport=NativeTeamPacketTransport(authority=self.authority,pool=self.store._pool,grant_digest=self.g.digest,
            attempt_digest=self.attempt,query_factory=query,worker=Worker(),worker_verifier_secret=b"w"*32,
            source_root=self.repository,artifact_store=self.artifacts,integrator=self.integrator,writer_owner_token="writer-one")
        result=transport.send(json.dumps(self.work).encode(),context=SimpleNamespace(timeout_seconds=60,fencing_generation=1))
        candidate=result["artifacts"][-1]["location"].split("@")[-1]
        self.assertEqual(self.git_command("show",candidate+":src/allowed.py"),"transport changed")
        self.assertEqual(result["usage"]["model"],"unknown")
        self.assertEqual(result["run_reference"],"native-fixture")
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)
        with self.store._pool.connection() as c:
            stages=[r["stage"] for r in c.execute(f"SELECT stage FROM {self.schema}.director_primary_team_boundary ORDER BY created_at").fetchall()]
        self.assertEqual(stages,["disclosure","execution","result","result"])
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]
