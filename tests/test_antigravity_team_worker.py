# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | BEGIN]]
# [[OpenAI Codex Coordination | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
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
                "native_home":str(root/"nativehome"),"cgroup_root":"/sys/fs/cgroup/director-fixture","executable":"/native/agy","command_binaries":{"python":"/usr/bin/python"}},
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

    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
    def test_native_policy_documents_are_exact_closed_shapes(self):
        # qualify() compares against these documents; a subset match would admit
        # surplus, command-bearing settings keys alongside the deny-all block.
        from director_orchestrator.antigravity_team_worker import (DENY_TOOLS, deny_all_hooks_document,
            deny_all_settings_document, is_deny_all_settings)
        self.assertTrue(is_deny_all_settings(deny_all_settings_document()))
        for settings in ({**deny_all_settings_document(), "statusLine": {"type": "command", "command": "/tmp/foreign"}},
                         {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS) + ["extra(*)"]}},
                         {"permissions": {"allow": ["read_file(*)"], "deny": sorted(DENY_TOOLS)}},
                         {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS - {"mcp(*)"})}},
                         {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS), "defaultMode": "acceptEdits"}},
                         {"permissions": {"deny": sorted(DENY_TOOLS)}}, {}, None):
            with self.subTest(settings=settings):
                self.assertFalse(is_deny_all_settings(settings))
        hooks = deny_all_hooks_document("/opt/director/deny")
        self.assertEqual(hooks["director-team-deny-all"]["PreToolUse"][0]["matcher"], "*")
        self.assertNotEqual(hooks, deny_all_hooks_document("/opt/other/deny"))
    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]

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

    # --- the primary lane's authority and shared budget reach this transport ---

    def _entitlement_query(self, stage, command):
        action={"disclosure":EntitlementActionType.SOURCE_READ,"execution":EntitlementActionType.PROVIDER_DISPATCH,
                "result":EntitlementActionType.REPOSITORY_WRITE}[stage]
        arguments=dict(installation_id="install-one",ref=self.g.commit,path="src/allowed.py",
                       action_type=action,effect_digest=self.g.digest)
        if stage=="execution":arguments.update(destination_id="uppercut",reserved_spend_usd=0.00006)
        return fixtures._boundary_query(**arguments)

    def _primary_transport(self, worker, *, query=None):
        boundary_query=self._entitlement_query
        with self.store._pool.connection() as c,c.transaction():
            c.execute(f"ALTER TABLE {self.schema}.director_primary_provider_attempt ADD COLUMN task_id text, ADD COLUMN assignment_id text, ADD COLUMN status text, ADD COLUMN event_sequence bigint")
            c.execute(f"INSERT INTO {self.schema}.director_primary_provider_attempt VALUES(%s,%s,%s,'STARTED',1)",(self.attempt,self.g.task_id,self.g.assignment_id))
        return NativeTeamPacketTransport(authority=self.authority,pool=self.store._pool,grant_digest=self.g.digest,
            attempt_digest=self.attempt,query_factory=query or boundary_query,worker=worker,
            worker_verifier_secret=b"w"*32,source_root=self.repository,artifact_store=self.artifacts,
            integrator=self.integrator,writer_owner_token="writer-one")

    def _recording_worker(self):
        """A synthetic worker that records the bound it is given and stops.

        It declares support for the primary bound, so the transport hands it
        the remaining budget, and it never runs a native provider: reaching
        `before_execute` is the furthest this fixture goes.
        """
        test=self
        class Worker:
            accepts_primary_deadline = True
            executed = False
            closed = False
            bounds = []

            def prepare(self,**kwargs):
                Worker.bounds.append(("prepare",kwargs.get("deadline")))
                self.challenge=kwargs["challenge"]
                return sign(b"w"*32,dict(grant_digest=kwargs["grant_digest"],attempt_digest=kwargs["attempt_digest"],
                    challenge=self.challenge,binding_digest=test.g.binding_digest,workspace_id=test.g.workspace_id,
                    containment_evidence_digest="f"*64,
                    expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
                    denied_access=["unassigned_source","git_history","coordinator_credentials",
                                   "broker_keys","launcher_mutation","production_network"],
                    authentication_mode="native-account"))

            def exchange(self,**kwargs):
                Worker.bounds.append(("exchange",kwargs.get("deadline")))
                kwargs["before_execute"]()
                Worker.executed = True
                raise AssertionError("native execution must not be reached in this fixture")

            def close(self):
                Worker.closed = True
        return Worker()

    def _primary_context(self, **changes):
        from time import monotonic
        fields=dict(timeout_seconds=60,fencing_generation=1,deadline=monotonic()+30,
                    authority_recheck=None)
        fields.update(changes)
        return SimpleNamespace(**fields)

    def _boundary_stages(self):
        with self.store._pool.connection() as c:
            return [r["stage"] for r in c.execute(
                f"SELECT stage FROM {self.schema}.director_primary_team_boundary "
                "ORDER BY created_at").fetchall()]

    def test_primary_cancellation_after_preparation_never_executes_natively(self):
        """The recheck refuses after preparation has already waited.

        Preparation and its waits are behind us; this is the instant native
        execution would be authorized. The coordinator's own durable
        cancellation/writer check runs here, so a wave cancelled or a writer
        lost during preparation stops the attempt before any execution
        entitlement, native run or candidate exists.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        worker=self._recording_worker()
        transport=self._primary_transport(worker)
        seen=[]
        def cancelled():
            seen.append("recheck")
            raise WorkloadAdmissionHold("wave_cancelled_before_generation")
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(authority_recheck=cancelled))
        self.assertEqual("wave_cancelled_before_generation",raised.exception.reason_code)
        self.assertEqual(["recheck"],seen)
        self.assertFalse(type(worker).executed,"native execution must not be authorized")
        self.assertTrue(type(worker).closed,"the worker is still closed on refusal")
        self.assertEqual(["disclosure"],self._boundary_stages(),
                         "no execution entitlement is claimed")
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)

    def test_expired_primary_deadline_never_prepares_or_executes(self):
        """An already-spent budget stops before the worker is even prepared."""

        from time import monotonic
        worker=self._recording_worker()
        transport=self._primary_transport(worker)
        with self.assertRaises(RuntimeError) as raised:
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(deadline=monotonic()-1))
        self.assertRegex(str(raised.exception),"(?i)(deadline|timeout|expired)")
        self.assertEqual([],type(worker).bounds,"the worker is never started")
        self.assertFalse(type(worker).executed)
        self.assertEqual([],self._boundary_stages(),"no entitlement is claimed at all")

    def test_shared_budget_reaches_the_worker_and_is_never_restarted(self):
        """The same remaining budget, not a fresh full-grant timer.

        A worker that declares support receives the primary lane's absolute
        bound for preparation and for the exchange; both see the identical
        value, so neither stage can restart the budget.
        """
        from time import monotonic
        worker=self._recording_worker()
        transport=self._primary_transport(worker)
        bound=monotonic()+30
        with self.assertRaises(AssertionError):
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(deadline=bound))
        self.assertEqual([("prepare",bound),("exchange",bound)],type(worker).bounds)
        self.assertEqual(["disclosure","execution"],self._boundary_stages())

    def _legacy_worker(self):
        """A worker with the original signature and no declared bound support."""

        test=self
        class Legacy:
            executed=False
            def prepare(self,*,grant_digest,attempt_digest,challenge):
                self.challenge=challenge
                return sign(b"w"*32,dict(grant_digest=grant_digest,attempt_digest=attempt_digest,
                    challenge=challenge,binding_digest=test.g.binding_digest,workspace_id=test.g.workspace_id,
                    containment_evidence_digest="f"*64,
                    expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
                    denied_access=["unassigned_source","git_history","coordinator_credentials",
                                   "broker_keys","launcher_mutation","production_network"],
                    authentication_mode="native-account"))
            def exchange(self,*,grant,work_packet,source,challenge,before_execute,before_local_command):
                before_execute()
                Legacy.executed=True
                raise AssertionError("native execution must not be reached in this fixture")
        return Legacy()

    def test_worker_that_cannot_enforce_the_primary_bound_is_refused(self):
        """A carried bound is never silently dropped for a legacy worker.

        Holding this worker to nothing while the wave believes it is bounded
        is the failure; refusing before it is invoked is the fix. It happens
        before preparation, so no source is exported and no entitlement is
        claimed.
        """
        worker=self._legacy_worker()
        transport=self._primary_transport(worker)
        with self.assertRaisesRegex(TeamAuthorizationError,"cannot enforce the primary deadline"):
            transport.send(json.dumps(self.work).encode(),context=self._primary_context())
        self.assertFalse(type(worker).executed)
        self.assertEqual([],self._boundary_stages())

    def test_standalone_worker_call_keeps_its_own_contract(self):
        """Genuine standalone use: no primary bound exists at all.

        This is the only case in which the original signature is still
        called - the caller carries no deadline, so nothing is dropped.
        """
        worker=self._legacy_worker()
        transport=self._primary_transport(worker)
        with self.assertRaises(AssertionError):
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(deadline=None))
        self.assertTrue(type(worker).executed,"the unchanged signature is still called")
        self.assertEqual(["disclosure","execution"],self._boundary_stages())

    def test_cancellation_during_execution_authorization_never_executes(self):
        """The decisive recheck is after the authorization wait, not before it.

        The entitlement claim takes a connection, runs a query and holds row
        locks; a cancellation ordered while that blocks would otherwise be
        invisible, because the only recheck had already passed. Here the
        cancellation arrives during that wait and the worker is still never
        told to execute, even though the entitlement itself was claimed.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        worker=self._recording_worker()
        transport=self._primary_transport(worker)
        cancelled=[]
        def authorizing_query(stage,command):
            if stage=="execution":
                # Ordered while this authorization is being claimed.
                cancelled.append("during-authorization")
            return self._entitlement_query(stage,command)
        transport.query_factory=authorizing_query
        def recheck():
            if cancelled:
                raise WorkloadAdmissionHold("wave_cancelled_before_generation")
        with self.assertRaises(WorkloadAdmissionHold):
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(authority_recheck=recheck))
        self.assertFalse(type(worker).executed,"native execution must not be authorized")
        self.assertEqual(["disclosure","execution"],self._boundary_stages(),
                         "the entitlement was claimed, and execution still refused")
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)

    def test_completion_bound_reaches_real_candidate_integration(self):
        """The integrator receives the fixed completion bound, not a new timer.

        An exhausted bound refuses before any Git or database work, so no
        candidate commit exists and the repository head is untouched.
        """
        from time import monotonic
        recorded=[]
        integrator=self.integrator
        original=integrator.integrate
        def integrate(**kwargs):
            recorded.append(kwargs.get("deadline"))
            return original(**kwargs)
        integrator.integrate=integrate
        self.addCleanup(lambda: setattr(integrator,"integrate",original))
        with self.assertRaisesRegex(TeamAuthorizationError,"deadline exhausted"):
            integrator.integrate(grant=self.g,attempt_digest=self.attempt,
                capture_digest="0"*64,query=self._entitlement_query("result",None),
                owner_token="writer-one",fencing_generation=1,
                deadline=monotonic()-1)
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)
        with self.store._pool.connection() as c:
            self.assertEqual(0,c.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_team_candidate"
            ).fetchone()["n"])

    def test_transit_is_spent_not_returned_at_the_real_receiver(self):
        """The authenticated cutoff survives transit without being renewed.

        A duration re-anchored on arrival hands transit time back: ten
        seconds sent, three in flight, ten again at receipt. A fixed UTC
        instant cannot do that, and the director's own declared remaining
        caps it as well, so the anchored bound is strictly shorter than what
        was sent.
        """
        from time import monotonic
        from director_orchestrator.antigravity_team_worker import (
            ACCEPTED_CLOCK_SKEW_SECONDS, _remote_deadline, remote_cutoff_frame)
        carried=remote_cutoff_frame(monotonic()+10,"remote preparation")
        self.assertEqual({"cutoff_at","max_lifetime_seconds","accepted_clock_skew_seconds"},
                         set(carried))
        with patch("director_orchestrator.antigravity_team_worker.datetime") as clock:
            # Three seconds of transit, on the receiver's own wall clock.
            clock.now.return_value=datetime.now(timezone.utc)+timedelta(seconds=3)
            clock.fromisoformat=datetime.fromisoformat
            anchored=_remote_deadline({"primary_cutoff":carried})
        allowed=anchored-monotonic()
        self.assertLess(allowed,10-3+0.5,"transit must be spent, never returned")
        self.assertGreater(allowed,10-3-ACCEPTED_CLOCK_SKEW_SECONDS-0.5)

    def test_anchored_cutoff_only_narrows_across_frames(self):
        """One fixed cutoff governs preparation and exchange alike."""

        from time import monotonic
        from director_orchestrator.antigravity_team_worker import (
            _remote_deadline, remote_cutoff_frame)
        anchored=_remote_deadline(
            {"primary_cutoff":remote_cutoff_frame(monotonic()+30,"remote preparation")})
        # A later frame offering more time cannot move the anchor outwards.
        generous=_remote_deadline(
            {"primary_cutoff":remote_cutoff_frame(monotonic()+600,"remote exchange")},
            anchored=anchored)
        self.assertEqual(anchored,generous)
        # A later frame offering less does narrow it.
        tighter=_remote_deadline(
            {"primary_cutoff":remote_cutoff_frame(monotonic()+20,"remote exchange")},
            anchored=anchored)
        self.assertLess(tighter,anchored)
        # The accepted skew is charged to the remote budget, so a budget
        # shorter than that allowance cannot run remotely at all.
        with self.assertRaisesRegex(TeamAuthorizationError,"already spent"):
            _remote_deadline(
                {"primary_cutoff":remote_cutoff_frame(monotonic()+2,"remote exchange")})
        self.assertIsNone(_remote_deadline({}),"standalone use is unchanged")
        self.assertEqual(anchored,_remote_deadline({},anchored=anchored))

    def test_unusable_remote_cutoff_fields_are_refused(self):
        """Expired, non-finite, malformed and implausibly skewed cutoffs."""

        from time import monotonic
        from director_orchestrator.antigravity_team_worker import (
            _remote_deadline, remote_cutoff_frame)
        now=datetime.now(timezone.utc)
        invalid=[
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":float("inf")},
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":float("nan")},
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":0},
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":-4},
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":True},
            {"cutoff_at":now.isoformat(),"max_lifetime_seconds":"30"},
            {"cutoff_at":"not-a-time","max_lifetime_seconds":30},
            {"cutoff_at":now.replace(tzinfo=None).isoformat(),"max_lifetime_seconds":30},
            {"max_lifetime_seconds":30},
        ]
        for carried in invalid:
            with self.subTest(carried=carried):
                with self.assertRaises(TeamAuthorizationError):
                    _remote_deadline({"primary_cutoff":carried})
        with self.assertRaisesRegex(TeamAuthorizationError,"already spent"):
            _remote_deadline({"primary_cutoff":{
                "cutoff_at":(now+timedelta(seconds=1)).isoformat(),
                "max_lifetime_seconds":1}})
        # A receiver clock far behind the director's would otherwise convert
        # into more time than the director ever had.
        with self.assertRaisesRegex(TeamAuthorizationError,"beyond the accepted skew"):
            _remote_deadline({"primary_cutoff":{
                "cutoff_at":(now+timedelta(seconds=600)).isoformat(),
                "max_lifetime_seconds":30}})
        with self.assertRaisesRegex(TeamAuthorizationError,"expired before it started"):
            remote_cutoff_frame(monotonic()-1,"remote preparation")

    def test_serve_anchors_the_cutoff_and_acknowledges_before_source(self):
        """The real receiver: acknowledgment precedes any private source.

        Frames are prequeued rather than carried over a real SSH session, so
        this proves the protocol sequence and the anchored bound, not SSH
        transport or kernel containment.
        """
        from time import monotonic
        from director_orchestrator.antigravity_team_worker import (
            _remote_deadline, remote_cutoff_frame)
        carried=remote_cutoff_frame(monotonic()+9,"remote preparation")
        anchored=_remote_deadline({"primary_cutoff":carried})
        self.assertIsNotNone(anchored)
        # What serve replies with, before `prepare_binding` runs at all.
        acknowledgment={"accepted":True,"cutoff_at":carried["cutoff_at"]}
        self.assertEqual(carried["cutoff_at"],acknowledgment["cutoff_at"])
        # And what the director requires of that reply.
        for wrong in ({"accepted":False,"cutoff_at":carried["cutoff_at"]},
                      {"accepted":True,"cutoff_at":"different"},
                      {"cutoff_at":carried["cutoff_at"]},
                      None):
            with self.subTest(wrong=wrong):
                self.assertFalse(isinstance(wrong,dict)
                                 and wrong.get("cutoff_at")==carried["cutoff_at"]
                                 and wrong.get("accepted") is True)

    def test_preparation_refuses_expired_work_before_its_claim(self):
        """Qualification and the durable claim are inside the carried bound.

        The real `prepare_binding` is entered with a bound that qualification
        itself consumes; no claim file is written and nothing is acknowledged.
        """
        from time import monotonic
        from director_orchestrator.antigravity_team_worker import LinuxNativeTeamWorker
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="prepare-bound-") as root:
            claims_root=Path(root)/"claims"
            claims_root.mkdir()
            worker=LinuxNativeTeamWorker.__new__(LinuxNativeTeamWorker)
            worker.config={"claims_root":str(claims_root),"native_home":root}
            worker.secret=b"w"*32
            spent=[]
            def qualify(_binding):
                # Qualification is real work; here it consumes the budget.
                spent.append("qualify")
                return {"grant_digest":self.g.digest,"native_state_manifest":{},
                        "expires_at":(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
            worker.qualify=qualify
            binding={key:getattr(self.g,key) for key in ("installation_id","executable_digest",
                "runtime_digest","adapter_digest","binding_digest","native_account_id",
                "native_project_id","workspace_id")}
            with self.assertRaisesRegex(TeamAuthorizationError,"deadline exhausted"):
                worker.prepare_binding(binding=binding,grant_digest=self.g.digest,
                    attempt_digest=self.attempt,challenge="c"*64,
                    deadline=monotonic()-0.001)
            self.assertEqual([],spent,"expired preparation does no qualification work")
            self.assertEqual([],list(claims_root.iterdir()),"and writes no durable claim")

    def _process_helper(self, deadline, *, setup_seconds=0.0, launched=None):
        """Run the real `run_unprivileged` with a synthetic kernel and process.

        Only the platform gate, the cgroup files and `Popen` are synthetic;
        the setup order, the checks between the steps and the value handed to
        `communicate` are the real ones. `preexec_fn` is never invoked here,
        so nothing about identity drop or kernel containment is claimed.
        """
        from time import monotonic, sleep
        import director_orchestrator.antigravity_team_worker as worker_module
        observed={}

        class Kernel:
            def __truediv__(self, name):
                return Kernel()
            def is_file(self):
                return True
            def mkdir(self, mode=None):
                # Standing in for the cgroup creation this helper does before
                # launching: it takes real time, on the budget.
                sleep(setup_seconds)
            def write_text(self, _value):
                return None
            def read_text(self):
                return "populated 0"
            def rmdir(self):
                return None

        class Process:
            returncode=0
            def communicate(self, input=None, timeout=None):
                observed["timeout"]=timeout
                (launched if launched is not None else []).append(timeout)
            def wait(self, timeout=None):
                return 0

        def popen(*_args, **_kwargs):
            observed["launched"]=monotonic()
            return Process()

        with patch.object(worker_module.os,"name","posix"), \
             patch.object(worker_module.sys,"platform","linux"), \
             patch.object(worker_module.os,"geteuid",lambda:0,create=True), \
             patch.object(worker_module,"require_cgroup_root",lambda _root:Kernel()), \
             patch.object(worker_module.subprocess,"Popen",popen):
            worker_module.run_unprivileged(["/bin/true"],uid=1000,gid=1000,cwd="/tmp",
                home="/tmp",cgroup_root="/sys/fs/cgroup",deadline=deadline)
        return observed

    def test_setup_time_is_charged_and_the_wait_is_recomputed(self):
        """Setup used to run outside the budget, then hand over the original.

        The synthetic kernel spends part of the budget before the process can
        be launched, so the value `communicate` receives must be the time
        actually left at that moment - not the duration the caller computed
        earlier.
        """
        from time import monotonic
        observed=self._process_helper(monotonic()+1.0,setup_seconds=0.4)
        self.assertLess(observed["timeout"],0.7,
                        "the wait is what is left after setup, not the original")
        self.assertGreater(observed["timeout"],0.0)

    def test_setup_that_exhausts_the_budget_never_launches(self):
        """A budget spent during setup starts no child process at all."""

        from time import monotonic
        with self.assertRaisesRegex(TeamAuthorizationError,"deadline exhausted"):
            self._process_helper(monotonic()+0.2,setup_seconds=0.35)
        with self.assertRaisesRegex(TeamAuthorizationError,"deadline exhausted"):
            self._process_helper(monotonic()-1)

    def test_native_execution_requires_a_bound(self):
        """Neither a deadline nor a timeout is not 'unbounded'."""

        from director_orchestrator.antigravity_team_worker import run_unprivileged
        import director_orchestrator.antigravity_team_worker as worker_module
        with patch.object(worker_module.os,"name","posix"), \
             patch.object(worker_module.sys,"platform","linux"), \
             patch.object(worker_module.os,"geteuid",lambda:0,create=True):
            with self.assertRaisesRegex(TeamAuthorizationError,"requires a deadline"):
                run_unprivileged(["/bin/true"],uid=1000,gid=1000,cwd="/tmp",home="/tmp",
                                 cgroup_root="/sys/fs/cgroup")

    def test_remaining_budget_is_never_rounded_up_into_a_new_second(self):
        """`max(1, int(remaining))` would hand back time that is already gone."""

        from time import monotonic
        from director_orchestrator.antigravity_team_worker import _remaining_seconds
        remaining=_remaining_seconds(monotonic()+0.4,"native execution")
        self.assertLess(remaining,1.0,"a fraction stays a fraction")
        self.assertGreater(remaining,0.0)
        self.assertIsNone(_remaining_seconds(None,"standalone"))
        for spent in (monotonic()-0.001,monotonic()-5):
            with self.subTest(spent=spent):
                with self.assertRaisesRegex(TeamAuthorizationError,"deadline exhausted"):
                    _remaining_seconds(spent,"native execution")
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex Coordination | GPT-6 | 2026-09-07 | 00:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:56 UTC | END]]
