# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:02 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 17:31 UTC | BEGIN]]
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
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from director_orchestrator.antigravity_team import TeamAuthority, TeamAuthorizationError, SourceBlob, team_ddl, verify_team_schema
from director_orchestrator.antigravity_team_artifacts import TeamArtifactStore, GitTeamCandidateIntegrator, decode_returned_files
import director_orchestrator.antigravity_team_worker as worker_module
from director_orchestrator.antigravity_team_worker import (FrameChannel, LinuxNativeTeamWorker, PinnedSshTeamWorker,
    MAX_REMAINING_SECONDS, PRIMARY_BOUND_MODE, authenticate, run_unprivileged, serve, sign)
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


SECRET=b"w"*32
CHALLENGE="a"*64
ATTEMPT="sha256:"+"1"*64
# What the synthetic `os.urandom` makes the real `serve` issue as its anchor
# token in the prerecorded-frame tests, so a director frame can name it - or
# deliberately fail to.
ISSUED=bytes(range(16)).hex()


class _Transit:
    """A pipe end whose reads charge the receiving side's clock the transit.

    Each read spends the next stated delay and the last one repeats. This is
    the only way time passes between the two synthetic clocks - while a frame
    is in flight - and never by comparing one clock with the other.
    """

    def __init__(self, raw, clock, delays):
        self.raw, self.clock, self.delays = raw, clock, list(delays)

    def readline(self, limit=-1):
        line=self.raw.readline(limit)
        if line:
            self.clock[0]+=self.delays.pop(0) if len(self.delays)>1 else self.delays[0]
        return line

    def close(self):
        self.raw.close()


class _RemoteSession:
    """What `PinnedSshTeamWorker` is handed instead of an SSH process.

    Two OS pipes carry the real frames and a thread on the far end runs the
    server - the real `serve` unless a test supplies an older one. `kill`
    closes the far end's input, which is what killing SSH does to it.
    """

    def __init__(self, server, *, clocks, to_server, to_client):
        client_to_server,server_to_client=os.pipe(),os.pipe()
        self.stdin=os.fdopen(client_to_server[1],"wb")
        self.stdout=_Transit(os.fdopen(server_to_client[0],"rb"),clocks["client"],to_client)
        self.remote_stdin=_Transit(os.fdopen(client_to_server[0],"rb"),clocks["server"],to_server)
        self.remote_stdout=os.fdopen(server_to_client[1],"wb")
        self.error=self.returncode=None
        self.thread=threading.Thread(target=self._run,args=(server,),daemon=True)
        self.thread.side="server"

    def _run(self, server):
        try:
            server(self.remote_stdin,self.remote_stdout)
            self.returncode=0
        except BaseException as exc:
            self.error,self.returncode=exc,1
        finally:
            for end in (self.remote_stdout,self.remote_stdin):
                try:end.close()
                except OSError:pass

    def poll(self):
        return None if self.thread.is_alive() else self.returncode

    def kill(self):
        try:self.stdin.close()
        except OSError:pass

    def wait(self, timeout=None):
        self.thread.join(timeout)
        return self.poll()


class WorkerBoundTests(unittest.TestCase):
    """The real frames, `serve`, `PinnedSshTeamWorker`, preparation and process helper.

    Two OS pipes and a thread stand in for the SSH session. Each side has its
    own monotonic clock with a distinct epoch, advanced only by the stated
    transit of every frame it reads. Inside the real `serve` a synthetic
    worker records the bound it is handed and drives both boundaries. What
    is synthetic, and therefore not proven here: the SSH process itself,
    host qualification, the native provider, the kernel cgroup and identity
    drop (`preexec_fn` is never invoked) and remote cleanup after a spent
    bound. No database is used.
    """

    def _enter(self, manager):
        value=manager.__enter__()
        self.addCleanup(manager.__exit__,None,None,None)
        return value

    def _synthetic_worker(self, seen):
        class Worker:
            def __init__(self, config, **secrets):
                self.secret=SECRET
            def prepare_binding(self, **kwargs):
                seen["prepare"]=kwargs
                return {"prepared":kwargs["attempt_digest"]}
            def exchange(self, **kwargs):
                seen["exchange"]=kwargs
                kwargs["before_execute"]()
                for argv in kwargs["grant"].commands:
                    kwargs["before_local_command"](argv)
                return {"capture":"synthetic"}
        return Worker

    def _serve_patches(self, worker):
        """The real `serve` with only its host-bound pieces replaced."""
        self._enter(patch.object(worker_module,"LinuxNativeTeamWorker",worker))
        self._enter(patch.object(worker_module,"protected_bytes",return_value=json.dumps({"secret_files":{}}).encode()))

    def _clocks(self, clocks):
        def monotonic():
            return clocks[getattr(threading.current_thread(),"side","client")][0]
        self._enter(patch.object(worker_module,"time",SimpleNamespace(monotonic=monotonic,sleep=time.sleep)))

    def _ssh(self, g, *, to_server=(5.0,), to_client=(5.0,), server=None):
        """A real `PinnedSshTeamWorker` whose SSH process is a thread running `server`."""
        clocks={"client":[100.0],"server":[5000.0]}
        seen={}
        self._clocks(clocks)
        self._serve_patches(self._synthetic_worker(seen))
        remote=_RemoteSession(server or (lambda stdin,stdout:serve("synthetic-config")),
                              clocks=clocks,to_server=to_server,to_client=to_client)
        self._enter(patch.object(worker_module,"sys",SimpleNamespace(
            stdin=SimpleNamespace(buffer=remote.remote_stdin),stdout=SimpleNamespace(buffer=remote.remote_stdout))))
        def launch(*_argv, **_options):
            remote.thread.start()
            return remote
        self._enter(patch.object(worker_module.subprocess,"Popen",launch))
        def settle():
            # Joined before the patches above are undone, every pipe end closed.
            remote.kill()
            if remote.thread.is_alive():
                remote.thread.join(5)
            for end in (remote.stdout,remote.remote_stdin,remote.remote_stdout):
                try:end.close()
                except OSError:pass
        self.addCleanup(settle)
        root=Path(self._enter(tempfile.TemporaryDirectory(prefix="pinned-ssh-")))
        ssh=root/"ssh"
        ssh.write_bytes(b"synthetic ssh client; never executed")
        (root/"identity").write_bytes(b"synthetic identity")
        (root/"known_hosts").write_bytes(b"synthetic pin")
        client=PinnedSshTeamWorker(ssh=ssh,ssh_sha256=hashlib.sha256(ssh.read_bytes()).hexdigest(),
            host="worker.invalid",user="director",identity_file=root/"identity",known_hosts=root/"known_hosts",
            worker_command="/opt/director/team-worker",worker_secret=SECRET,grant=g)
        self.addCleanup(client.close)
        return client,seen,remote

    def test_pinned_ssh_and_serve_bound_the_remote_by_its_anchor_not_by_receipt(self):
        """Acknowledged success over two clocks with five seconds of transit each way.

        The director's bound is fixed at 140 on its clock (epoch 100). The
        server's clock (epoch 5000) reads 5005 when it anchors, after the
        challenge line arrived; the anchor reaches the director at 105, so it
        sends 35 remaining, and the server bounds preparation and the
        exchange alike at 5005 + 35 = 5040 - not the 5010 + 35 that anchoring
        at receipt would give. Both transits are spent. The exchange frame
        names the same anchor and the bound is unchanged.
        """
        g=grant()
        client,seen,remote=self._ssh(g)
        prepared=client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=140.0)
        self.assertEqual({"prepared":ATTEMPT},prepared)
        self.assertEqual(140.0,client.deadline)
        self.assertEqual(5040.0,seen["prepare"]["deadline"])
        executed,commands=[],[]
        result=client.exchange(grant=g,work_packet={"packet":"synthetic"},source={"src/allowed.py":b"private"},
            challenge=CHALLENGE,before_execute=lambda:executed.append(True),before_local_command=commands.append,
            deadline=140.0)
        self.assertEqual({"capture":"synthetic"},result)
        self.assertEqual(5040.0,seen["exchange"]["deadline"],"the exchange names the anchor; it is not re-anchored")
        self.assertEqual([True],executed)
        self.assertEqual([("python","-m","unittest")],commands)
        self.assertEqual({"src/allowed.py":b"private"},seen["exchange"]["source"],
                         "source is disclosed only after the acknowledged preparation")
        self.assertEqual((0,None),(remote.returncode,remote.error))

    def test_receiver_pause_before_the_prepare_frame_does_not_extend_the_bound(self):
        """Twenty-five seconds pass on the server between its anchor and the prepare frame.

        Anchoring at receipt would give 5030 + 35; the retained anchor gives
        5005 + 35, leaving exactly the ten seconds the director actually has.
        """
        g=grant()
        client,seen,remote=self._ssh(g,to_server=(5.0,25.0,5.0))
        client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=140.0)
        self.assertEqual(5040.0,seen["prepare"]["deadline"])

    def test_director_pause_before_its_sample_shrinks_what_it_sends(self):
        """The director samples once, after the anchor: a pause before that sample is spent.

        The anchor takes twenty-five seconds to be acted on, so fifteen
        remain of the forty, and the server bounds at 5005 + 15 - the pause
        cannot be handed back by sampling earlier or sending later.
        """
        g=grant()
        client,seen,remote=self._ssh(g,to_client=(25.0,5.0))
        client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=140.0)
        self.assertEqual(5020.0,seen["prepare"]["deadline"])

    def test_a_bound_spent_in_transit_never_prepares(self):
        """The prepare frame arrives after the remaining budget it carries has elapsed."""

        g=grant()
        client,seen,remote=self._ssh(g,to_server=(5.0,40.0))
        with self.assertRaisesRegex(TeamAuthorizationError,"truncated"):
            client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=140.0)
        self.assertNotIn("prepare",seen,"preparation never starts")
        self.assertIsInstance(remote.error,TeamAuthorizationError)
        self.assertRegex(str(remote.error),"primary bound deadline expired before it started")

    def test_a_server_that_waits_to_be_spoken_to_first_receives_nothing_and_is_ended(self):
        """Every earlier server read the challenge and then waited for `prepare`.

        The director now waits for the anchor instead, so nothing but the
        challenge ever reaches such a server; the local timer ends the
        session at the bound and the director fails closed at the frame.
        """
        received=[]
        def old_server(stdin, stdout):
            challenge=stdin.readline(66).decode().strip()
            received.append(FrameChannel(stdin,stdout,SECRET,challenge,sender="worker").receive("prepare"))
        g=grant()
        client,seen,remote=self._ssh(g,server=old_server)
        # A quarter of a second of real time: the local timer is the only
        # thing that ends this session, so it is the one real wait here.
        with self.assertRaisesRegex(TeamAuthorizationError,"truncated"):
            client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=100.25)
        self.assertEqual([],received,"no preparation, and no source, was ever sent")
        self.assertIsInstance(remote.error,TeamAuthorizationError)
        self.assertNotIn("prepare",seen)

    def test_a_server_answering_with_anything_but_an_anchor_is_refused_before_preparation(self):
        """A first frame of the wrong kind or shape is unsupported, not standalone."""

        for kind,payload in (("prepared",{}),("anchor",{"mode":"director-team-cutoff/v0","anchor":"0"*32}),
                             ("anchor",{"mode":PRIMARY_BOUND_MODE,"anchor":"short"}),
                             ("anchor",{"mode":PRIMARY_BOUND_MODE,"anchor":"0"*32,"remaining_seconds":30})):
            with self.subTest(kind=kind,payload=payload):
                received=[]
                def foreign_server(stdin, stdout, kind=kind, payload=payload):
                    challenge=stdin.readline(66).decode().strip()
                    channel=FrameChannel(stdin,stdout,SECRET,challenge,sender="worker")
                    channel.send(kind,payload)
                    received.append(channel.receive("prepare"))
                g=grant()
                client,seen,remote=self._ssh(g,server=foreign_server)
                with self.assertRaisesRegex(TeamAuthorizationError,"does not support|mismatch"):
                    client.prepare(grant_digest=g.digest,attempt_digest=ATTEMPT,challenge=CHALLENGE,deadline=140.0)
                self.assertEqual([],received)
                self.doCleanups()

    def _serve(self, frames, *, worker, clock=5000.0):
        """The real `serve` on prerecorded director frames, with a fixed clock.

        Returns what it raised and every authenticated frame it emitted, in
        order. The anchor token it issues is `ISSUED`, so a frame can name it.
        """
        sent=io.BytesIO()
        director=FrameChannel(io.BytesIO(),sent,SECRET,CHALLENGE,sender="director")
        for kind,payload in frames:
            director.send(kind,payload)
        outgoing=io.BytesIO()
        stdio=SimpleNamespace(stdin=SimpleNamespace(buffer=io.BytesIO(CHALLENGE.encode()+b"\n"+sent.getvalue())),
                              stdout=SimpleNamespace(buffer=outgoing))
        error=None
        with patch.object(worker_module,"LinuxNativeTeamWorker",worker), \
             patch.object(worker_module,"protected_bytes",return_value=json.dumps({"secret_files":{}}).encode()), \
             patch.object(worker_module,"sys",stdio), \
             patch.object(worker_module,"os",SimpleNamespace(urandom=lambda n:bytes(range(n)))), \
             patch.object(worker_module,"time",SimpleNamespace(monotonic=lambda:clock,sleep=time.sleep)):
            try:
                serve("synthetic-config")
            except TeamAuthorizationError as exc:
                error=exc
        emitted=[]
        for raw in outgoing.getvalue().splitlines():
            frame=authenticate(SECRET,json.loads(raw))
            emitted.append((frame["kind"],frame["payload"]))
        return error,emitted

    def _prepare(self, bound):
        return ("prepare",{"binding":{},"grant_digest":grant().digest,"attempt_digest":ATTEMPT,"primary_bound":bound})

    def _exchange(self, bound):
        return ("exchange",{"grant":grant().model_dump(mode="json"),"work_packet":{},"source":{},"primary_bound":bound})

    def test_serve_treats_an_explicit_none_as_deliberate_standalone_use(self):
        """Genuine standalone use, stated rather than inferred from an absent key."""

        seen={}
        error,emitted=self._serve([self._prepare(None),self._exchange(None),
            ("authorized",{"stage":"execution"}),("authorized",{"stage":"command"})],worker=self._synthetic_worker(seen))
        self.assertIsNone(error)
        self.assertEqual(["anchor","prepared","before_execute","before_command","capture"],[kind for kind,_ in emitted])
        self.assertEqual({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED},emitted[0][1])
        self.assertIsNone(seen["prepare"]["deadline"])
        self.assertIsNone(seen["exchange"]["deadline"])

    def test_serve_refuses_legacy_and_unanchored_preparation_instead_of_downgrading(self):
        """An older client's bound is never dropped into standalone use.

        The revision that sent `lifetime_seconds`, the one that sent
        `primary_cutoff`, a frame with no bound key at all, and a bound the
        server did not anchor - a value a client could only have produced
        without waiting for the anchor - are each refused before preparation,
        with nothing but the anchor frame ever emitted.
        """
        cases={"lifetime_seconds":({"binding":{},"grant_digest":grant().digest,"attempt_digest":ATTEMPT,"lifetime_seconds":30.0},"unsupported worker preparation protocol"),
               "primary_cutoff":({"binding":{},"grant_digest":grant().digest,"attempt_digest":ATTEMPT,"primary_cutoff":{"cutoff_at":datetime.now(timezone.utc).isoformat(),"max_lifetime_seconds":30.0,"accepted_clock_skew_seconds":5.0}},"unsupported worker preparation protocol"),
               "no bound key":({"binding":{},"grant_digest":grant().digest,"attempt_digest":ATTEMPT},"unsupported worker preparation protocol"),
               "foreign anchor":(self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":"0"*32,"remaining_seconds":30.0})[1],"not anchored"),
               "foreign mode":(self._prepare({"mode":"director-team-cutoff/v0","anchor":ISSUED,"remaining_seconds":30.0})[1],"not anchored"),
               "bare value":(self._prepare(30.0)[1],"not anchored")}
        for name,remaining,message in (("infinite",float("inf"),"remaining budget is invalid"),
                                       ("nan",float("nan"),"remaining budget is invalid"),
                                       ("zero",0,"remaining budget is invalid"),
                                       ("negative",-4,"remaining budget is invalid"),
                                       ("boolean",True,"remaining budget is invalid"),
                                       ("text","30","remaining budget is invalid"),
                                       # Finite and positive, yet more than any grant can hold:
                                       # refused as malformed, never clamped to the ceiling.
                                       ("just above the grant ceiling",MAX_REMAINING_SECONDS+0.001,"exceeds the grant ceiling"),
                                       ("absurd",1e12,"exceeds the grant ceiling")):
            cases[name]=(self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,"remaining_seconds":remaining})[1],message)
        for name,(payload,message) in cases.items():
            with self.subTest(case=name):
                seen={}
                error,emitted=self._serve([("prepare",payload)],worker=self._synthetic_worker(seen))
                self.assertRegex(str(error),message)
                self.assertEqual(["anchor"],[kind for kind,_ in emitted],"nothing is acknowledged")
                self.assertNotIn("prepare",seen,"preparation never starts")

    def test_serve_binds_the_grant_ceiling_exactly_and_never_clamps_above_it(self):
        """The ceiling is the task grant's own, and it is a boundary, not a cap.

        A director's bound is at most its grant timeout, so exactly the
        ceiling is admitted as it is; a hair more is refused rather than
        reduced to it. The ceiling is read from `TeamGrant`, which is proved
        here to accept it and reject the next second.
        """
        self.assertEqual(2700.0,MAX_REMAINING_SECONDS)
        self.assertEqual(2700,grant(timeout_seconds=2700).timeout_seconds)
        with self.assertRaises(ValueError):
            grant(timeout_seconds=2701)
        seen={}
        error,emitted=self._serve([self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,
            "remaining_seconds":MAX_REMAINING_SECONDS})],worker=self._synthetic_worker(seen))
        self.assertEqual(["anchor","prepared"],[kind for kind,_ in emitted])
        self.assertEqual(5000.0+MAX_REMAINING_SECONDS,seen["prepare"]["deadline"],"bound exactly, not clamped")
        self.assertRegex(str(error),"truncated","the prerecorded input ends after preparation")
        seen={}
        error,emitted=self._serve([self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,
            "remaining_seconds":MAX_REMAINING_SECONDS+0.001})],worker=self._synthetic_worker(seen))
        self.assertRegex(str(error),"exceeds the grant ceiling")
        self.assertEqual(["anchor"],[kind for kind,_ in emitted])
        self.assertNotIn("prepare",seen)

    def test_serve_refuses_a_non_finite_anchor_instead_of_binding_to_it(self):
        """An anchor that is not a finite instant cannot bound anything.

        The server's own clock reading is validated where it is used: with an
        infinite or undefined anchor a well-formed, otherwise admissible
        `prepare` frame is refused before preparation, with only the anchor
        frame emitted, rather than producing a bound that never expires.
        """
        for clock in (float("inf"),float("nan")):
            with self.subTest(clock=clock):
                seen={}
                error,emitted=self._serve([self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,
                    "remaining_seconds":35.0})],worker=self._synthetic_worker(seen),clock=clock)
                self.assertRegex(str(error),"anchor is not a finite instant")
                self.assertEqual(["anchor"],[kind for kind,_ in emitted])
                self.assertNotIn("prepare",seen)
        # Standalone use needs no anchor at all, so the same clock admits it.
        seen={}
        error,emitted=self._serve([self._prepare(None)],worker=self._synthetic_worker(seen),clock=float("nan"))
        self.assertEqual(["anchor","prepared"],[kind for kind,_ in emitted])
        self.assertIsNone(seen["prepare"]["deadline"])

    def test_serve_keeps_the_prepared_bound_and_refuses_an_exchange_that_does_not_name_it(self):
        """The exchange frame can only name the anchor; it carries no time value.

        A frame that claims standalone use after a bound was prepared, names
        another anchor or mode, smuggles a remaining value, or has an older
        shape is refused after `prepared` and before execution; and a bound
        cannot appear at exchange after standalone preparation either.
        """
        prepared=self._prepare({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,"remaining_seconds":35.0})
        cases={"standalone after a bound":(prepared,self._exchange(None),"did not have|does not name"),
               "foreign anchor":(prepared,self._exchange({"mode":PRIMARY_BOUND_MODE,"anchor":"0"*32}),"does not name"),
               "foreign mode":(prepared,self._exchange({"mode":"director-team-cutoff/v0","anchor":ISSUED}),"does not name"),
               "renewal":(prepared,self._exchange({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED,"remaining_seconds":600.0}),"does not name"),
               "legacy shape":(prepared,("exchange",{"grant":grant().model_dump(mode="json"),"work_packet":{},"source":{},"primary_cutoff":None}),"unsupported worker exchange protocol"),
               "bound after standalone":(self._prepare(None),self._exchange({"mode":PRIMARY_BOUND_MODE,"anchor":ISSUED}),"did not have")}
        for name,(first,second,message) in cases.items():
            with self.subTest(case=name):
                seen={}
                error,emitted=self._serve([first,second],worker=self._synthetic_worker(seen))
                self.assertRegex(str(error),message)
                self.assertEqual(["anchor","prepared"],[kind for kind,_ in emitted],"refused before execution")
                self.assertEqual(5035.0 if first is prepared else None,seen["prepare"]["deadline"])
                self.assertNotIn("exchange",seen)

    def _native_worker(self, root, **config):
        (root/"claims").mkdir()
        (root/"native").mkdir()
        return LinuxNativeTeamWorker({"claims_root":str(root/"claims"),"native_home":str(root/"native"),**config},
            owner_secret=fixtures.OWNER_SECRET,issuer_secret=b"i"*32,worker_secret=SECRET)

    def _qualification(self, g, **extra):
        return {"grant_digest":g.digest,"native_state_manifest":{},
                "expires_at":(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),**extra}

    def test_preparation_that_runs_out_during_qualification_writes_no_claim(self):
        """The real `prepare_binding`, with qualification itself consuming the budget."""

        clock=[100.0]
        root=Path(self._enter(tempfile.TemporaryDirectory(prefix="prepare-qualify-")))
        g=grant()
        worker=self._native_worker(root)
        qualified=[]
        def qualify(_binding):
            qualified.append(True)
            clock[0]=103.0
            return self._qualification(g)
        self._enter(patch.object(worker_module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=time.sleep)))
        with patch.object(worker,"qualify",side_effect=qualify):
            with self.assertRaisesRegex(TeamAuthorizationError,"worker preparation deadline exhausted"):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest=ATTEMPT,challenge="b"*64,deadline=102.0)
        self.assertEqual([True],qualified,"the refusal comes after qualification, not before it")
        self.assertEqual([],list((root/"claims").iterdir()),"no durable claim is written")
        self.assertIsNone(worker.prepared)

    def test_preparation_that_runs_out_while_persisting_retains_the_claim_and_refuses(self):
        """The budget ends during `fsync`: the claim is durable, readiness is not.

        The consumed receipt stays on disk and is never treated as
        acknowledged: the exchange finds no preparation, and preparing the
        same qualification again fails on the retained claim rather than
        spending the receipt twice.
        """
        clock=[100.0]
        root=Path(self._enter(tempfile.TemporaryDirectory(prefix="prepare-persist-")))
        g=grant()
        worker=self._native_worker(root)
        fsync=os.fsync
        def persist(descriptor):
            fsync(descriptor)
            clock[0]=103.0
        self._enter(patch.object(worker_module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=time.sleep)))
        with patch.object(worker,"qualify",return_value=self._qualification(g)), \
             patch.object(worker,"account_lock",return_value=nullcontext()), \
             patch.object(worker_module.os,"fsync",side_effect=persist):
            with self.assertRaisesRegex(TeamAuthorizationError,"worker preparation deadline exhausted"):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest=ATTEMPT,challenge="b"*64,deadline=102.0)
            claims=list((root/"claims").iterdir())
            self.assertEqual(1,len(claims),"the consumed claim is retained, not rolled back")
            self.assertEqual(ATTEMPT,json.loads(claims[0].read_bytes())["attempt_digest"])
            self.assertIsNone(worker.prepared)
            with self.assertRaisesRegex(TeamAuthorizationError,"has no preparation"):
                worker.exchange(grant=g,work_packet={},source={},challenge="b"*64,before_execute=lambda:None,
                                before_local_command=lambda argv:None,deadline=102.0)
            with self.assertRaises(FileExistsError):
                worker.prepare(grant=g,grant_digest=g.digest,attempt_digest="sha256:"+"2"*64,challenge="c"*64,deadline=200.0)
        self.assertEqual(claims,list((root/"claims").iterdir()))
        self.assertIsNone(worker.prepared)

    def test_preparation_inside_the_bound_acknowledges_and_the_exchange_cannot_renew_it(self):
        """The bound preparation was acknowledged under reaches native execution unchanged.

        A caller carrying a later bound at exchange - or none - gets the
        prepared one: `run_unprivileged` is handed 102 on a clock at 100,
        never the 200 offered or the grant's own 160.
        """
        clock=[100.0]
        root=Path(self._enter(tempfile.TemporaryDirectory(prefix="prepare-exchange-")))
        (root/"work").mkdir()
        work={"task_id":"task-one"}
        g=grant(commands=(),packet_digest=document_digest(work),
                source=(SourceBlob(path="src/allowed.py",sha256=hashlib.sha256(b"old").hexdigest()),))
        worker=self._native_worker(root,workspace_root=str(root/"work"),native_uid=1001,native_gid=1001,
            check_uid=1002,check_gid=1002,cgroup_root="/sys/fs/cgroup/director-fixture",executable="/native/agy",
            command_binaries={})
        bounds=[]
        def native(argv, **kwargs):
            bounds.append(kwargs["deadline"])
            proposal={"packet":{"checks":[]},"returned_files":[]}
            events=[{"event":"init","conversation_id":"session-one","init":{"cwd":str(kwargs["cwd"]),"model":g.model}},
                    {"event":"result","result":{"conversation_id":"session-one","status":"SUCCESS","structured_output":proposal,"usage":{"total_tokens":1}}}]
            return 0,b"\n".join(json.dumps(e).encode() for e in events),b""
        self._enter(patch.object(worker_module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=time.sleep)))
        with patch.object(worker,"qualify",return_value=self._qualification(g,protected_files={})), \
             patch.object(worker,"account_lock",return_value=nullcontext()), \
             patch("director_orchestrator.antigravity_team_worker.os.chown",create=True), \
             patch.object(worker_module,"run_unprivileged",side_effect=native):
            prepared=authenticate(SECRET,worker.prepare(grant=g,grant_digest=g.digest,attempt_digest=ATTEMPT,
                                                        challenge="b"*64,deadline=102.0))
            self.assertEqual((ATTEMPT,"b"*64),(prepared["attempt_digest"],prepared["challenge"]))
            self.assertEqual((g.digest,ATTEMPT,"b"*64),worker.prepared)
            self.assertEqual(102.0,worker.prepared_deadline)
            capture=worker.exchange(grant=g,work_packet=work,source={"src/allowed.py":b"old"},challenge="b"*64,
                before_execute=lambda:None,before_local_command=lambda argv:None,deadline=200.0)
        self.assertEqual([102.0],bounds)
        self.assertEqual(ATTEMPT,authenticate(SECRET,capture)["attempt_digest"])
        self.assertIsNone(worker.prepared)
        self.assertIsNone(worker.prepared_deadline)

    def _process(self, seen, *, setup=0.0, launch=0.0, **bound):
        """The real `run_unprivileged` over a synthetic kernel, clock and process.

        The cgroup is a real directory whose control files the synthetic
        kernel writes; creating it spends `setup` seconds of the clock and
        `Popen` spends `launch`. What happened is recorded in `seen`, which
        the caller keeps when the helper refuses. `preexec_fn` is never
        invoked, so nothing about the identity drop or the kernel group
        itself is claimed.
        """
        clock=[100.0]
        root=Path(self._enter(tempfile.TemporaryDirectory(prefix="native-process-")))

        class Group:
            def __init__(self, path):
                self.path=path
            def __truediv__(self, name):
                return self.path/name
            def mkdir(self, **options):
                self.path.mkdir()
                for name,value in (("cgroup.kill",""),("cgroup.events","populated 0")):
                    (self.path/name).write_text(value)
                clock[0]+=setup
            def rmdir(self):
                seen["killed"]=(self.path/"cgroup.kill").read_text()
                for file in self.path.iterdir():
                    file.unlink()
                self.path.rmdir()
                seen["removed"]=not self.path.exists()

        class Root:
            def __truediv__(self, name):
                return Group(root/name)

        class Process:
            returncode=0
            def communicate(self, *, input, timeout):
                seen["communicate"]=timeout
            def wait(self, *, timeout):
                seen["wait"]=timeout
                return 0

        def launch_process(*_argv, **_options):
            seen["launched"]=True
            clock[0]+=launch
            return Process()

        with patch.object(worker_module,"os",SimpleNamespace(name="posix",geteuid=lambda:0,urandom=os.urandom)), \
             patch.object(worker_module,"sys",SimpleNamespace(platform="linux")), \
             patch.object(worker_module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=time.sleep)), \
             patch.object(worker_module,"require_cgroup_root",lambda _path:Root()), \
             patch.object(worker_module.subprocess,"Popen",launch_process):
            return run_unprivileged(["synthetic-no-exec"],uid=1001,gid=1001,cwd=root,home=root,
                                    cgroup_root="synthetic",**bound)

    def test_process_setup_and_launch_are_charged_to_the_wait_exactly(self):
        """What `communicate` waits for is what is left when it is called, fractions included."""

        for setup,launch,expected in ((0.0,0.0,10.0),(3.0,0.0,7.0),(2.5,0.0,7.5),(1.0,1.5,7.5)):
            with self.subTest(setup=setup,launch=launch):
                seen={}
                self.assertEqual((0,b"",b""),self._process(seen,setup=setup,launch=launch,timeout=10.0))
                self.assertEqual(expected,seen["communicate"])
                self.assertEqual(("1",True),(seen["killed"],seen["removed"]),
                                 "the group is killed and emptied on success too")
                self.doCleanups()
        # An absolute bound and a plain timeout narrow each other.
        seen={}
        self._process(seen,timeout=10.0,deadline=104.0)
        self.assertEqual(4.0,seen["communicate"])

    def test_a_budget_consumed_before_the_wait_launches_nothing_more_and_still_cleans_up(self):
        """Setup, or `Popen` itself, ending after the bound: nothing waits.

        A `Popen` that returns after the budget has elapsed gets no
        `communicate` at all; the group is still killed and drained and the
        refusal propagates. A budget spent during setup launches nothing, and
        one spent before setup, or absent altogether, creates no group.
        """
        cases=(({"launch":10.0,"timeout":10.0},"native execution deadline exhausted",True),
               ({"setup":10.0,"timeout":10.0},"native execution deadline exhausted",False),
               ({"deadline":99.0},"native process setup deadline exhausted",None),
               ({},"requires a deadline",None))
        for options,message,launched in cases:
            with self.subTest(options=options):
                seen={}
                with self.assertRaisesRegex(TeamAuthorizationError,message):
                    self._process(seen,**options)
                self.assertNotIn("communicate",seen,"nothing waits on a spent budget")
                if launched is None:
                    self.assertEqual({},seen,"refused before any group exists")
                else:
                    self.assertEqual(launched,"launched" in seen)
                    self.assertEqual(launched,"wait" in seen)
                    self.assertEqual(("1",True),(seen["killed"],seen["removed"]),
                                     "the group is killed and emptied on refusal")
                self.doCleanups()


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
        self.assertEqual(
            self.git_command("show", "-s", "--format=%(trailers:key=Contributed-by)", candidate),
            "Contributed-by: Antigravity | " + self.g.model,
        )
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

    # --- candidate integration under current primary authority ---

    def _capturing_worker(self, content=b"integrated under authority\n"):
        """A synthetic worker whose capture returns one granted file.

        It declares the primary bound, so the real transport hands it the
        carried deadline; host qualification and the native provider are not
        run here, only the authenticated capture contract.
        """
        entry={"path":"src/allowed.py","sha256":hashlib.sha256(content).hexdigest(),"content_base64":base64.b64encode(content).decode()}
        packet=json.loads((Path(__file__).parents[1]/"docs/schemas/model-result-packet-v1.example.json").read_text())
        packet.update({key:self.work[key] for key in ("task_id","assignment_id","idempotency_key","authority_snapshot_digest","correlation")})
        packet.update(provider="antigravity",checks=[],artifacts=[],side_effects=[])
        test=self
        class Worker:
            accepts_primary_deadline=True
            def prepare(self,**kwargs):
                self.challenge=kwargs["challenge"]
                return sign(b"w"*32,dict(grant_digest=kwargs["grant_digest"],attempt_digest=kwargs["attempt_digest"],
                    challenge=self.challenge,binding_digest=test.g.binding_digest,workspace_id=test.g.workspace_id,
                    containment_evidence_digest="f"*64,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
                    denied_access=["unassigned_source","git_history","coordinator_credentials","broker_keys","launcher_mutation","production_network"],
                    authentication_mode="native-account"))
            def exchange(self,**kwargs):
                kwargs["before_execute"]()
                proposal={"packet":packet,"returned_files":[entry]}
                stream="\n".join(json.dumps(e) for e in [{"event":"init","conversation_id":"native-fixture","init":{"cwd":"/isolated","model":test.g.model}},
                    {"event":"result","result":{"conversation_id":"native-fixture","status":"SUCCESS","structured_output":proposal,"usage":{"total_tokens":3,"input_tokens":2,"output_tokens":1}}}])
                evidence={key:getattr(test.g,key) for key in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","workspace_id")}
                evidence.update(grant_digest=test.g.digest,attempt_digest=test.attempt,packet_digest=test.g.packet_digest,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    native_project_id=test.g.native_project_id,native_identity_observations={"requested_project_id":test.g.native_project_id,"observed_project_id":None,"observed_account_id":None},
                    native_state_manifest_after={},authentication_mode="native-account",cwd="/isolated",challenge=self.challenge,
                    source_manifest_digest=document_digest({"source":[b.model_dump() for b in test.g.source]}),
                    containment_evidence_digest="f"*64,native_stream=stream,exit_code=0,
                    changed_files=[{"path":entry["path"],"sha256":entry["sha256"]}],returned_files=[entry],executed_commands=[],checks=[])
                return sign(b"w"*32,evidence)
            def close(self):
                pass
        return Worker()

    def _wave_rows(self):
        """This task's batch and ACTIVE assignment: the rows the live recheck reads besides the writer and attempt."""
        with self.store._pool.connection() as c,c.transaction():
            c.execute(f"CREATE TABLE {self.schema}.director_primary_parallel_batch(task_id text PRIMARY KEY,kill_switch boolean NOT NULL DEFAULT false)")
            c.execute(f"CREATE TABLE {self.schema}.director_primary_parallel_assignment(task_id text,assignment_id text,status text,provider_id text,reservation_digest text)")
            c.execute(f"INSERT INTO {self.schema}.director_primary_parallel_batch(task_id) VALUES(%s)",(self.g.task_id,))
            c.execute(f"INSERT INTO {self.schema}.director_primary_parallel_assignment VALUES(%s,%s,'ACTIVE','antigravity',%s)",
                      (self.g.task_id,self.g.assignment_id,"r"*64))

    def _live_recheck(self):
        """The live coordinator's own durable recheck, bound to this fixture's rows and writer."""
        from director_orchestrator.primary_default import _require_current_dispatch_authority
        handle=SimpleNamespace(schema=self.schema,pool=self.store._pool)
        def authority_recheck(*,connection=None,stage="generation"):
            _require_current_dispatch_authority(handle,task_id=self.g.task_id,assignment_id=self.g.assignment_id,
                reservation_digest="r"*64,provider="antigravity",owner_token="writer-one",fencing_generation=1,
                attempt_digest=self.attempt,deadline=None,connection=connection,stage=stage)
        return authority_recheck

    def _cancel_wave(self):
        """Exactly what `engage_kill_switch` does: hold the writer row, set the switch, commit."""
        with self.store._pool.connection() as c,c.transaction():
            c.execute(f"SELECT task_id FROM {self.schema}.director_primary_task_writer WHERE task_id=%s FOR UPDATE",(self.g.task_id,))
            c.execute(f"UPDATE {self.schema}.director_primary_parallel_batch SET kill_switch=true WHERE task_id=%s",(self.g.task_id,))

    def _retention_refs(self):
        return self.git_command("for-each-ref","--format=%(refname)","refs/director/team/").splitlines()

    def test_candidate_integration_under_current_authority_records_the_candidate(self):
        """Positive control: the live recheck passes at every boundary, including inside the integrator."""

        self._wave_rows()
        transport=self._primary_transport(self._capturing_worker())
        result=transport.send(json.dumps(self.work).encode(),
                              context=self._primary_context(authority_recheck=self._live_recheck()))
        candidate=result["artifacts"][-1]["location"].split("@")[-1]
        self.assertEqual("integrated under authority",self.git_command("show",candidate+":src/allowed.py"))
        self.assertEqual(["refs/director/team/"+self.attempt.removeprefix("sha256:")],self._retention_refs())
        self.assertEqual(["disclosure","execution","result","result"],self._boundary_stages())
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)

    def test_cancellation_completed_before_the_integrator_lock_gains_no_candidate(self):
        """Same-owner cancellation between the transport's recheck and the integrator's writer lock.

        The transport's own recheck ran in its separate transaction and
        passed. The cancellation then holds the writer row, commits the kill
        switch and releases, and the integrator takes the writer row next.
        Its in-transaction recheck sees the engaged switch: no Git object,
        retention ref or candidate row is created, while the capture the
        transport already stored and the STARTED attempt remain for
        reconciliation. Previously the integrator checked grant, proof and
        reservation but not the current switch, and created the candidate.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        self._wave_rows()
        transport=self._primary_transport(self._capturing_worker())
        original=self.integrator.integrate
        carried=[]
        def cancel_then_integrate(**kwargs):
            carried.append(kwargs.get("authority_recheck") is not None)
            self._cancel_wave()
            return original(**kwargs)
        self.integrator.integrate=cancel_then_integrate
        self.addCleanup(lambda:setattr(self.integrator,"integrate",original))
        artifacts_before=sorted(p.name for p in self.artifacts.root.glob("*.json"))
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            transport.send(json.dumps(self.work).encode(),
                           context=self._primary_context(authority_recheck=self._live_recheck()))
        self.assertEqual("wave_cancelled_before_candidate",raised.exception.reason_code)
        self.assertEqual([True],carried,"the transport carried its recheck into the integrator")
        with self.store._pool.connection() as c:
            self.assertEqual(0,c.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_team_candidate").fetchone()["n"])
            self.assertEqual("STARTED",c.execute(
                f"SELECT status FROM {self.schema}.director_primary_provider_attempt WHERE attempt_digest=%s",
                (self.attempt,)).fetchone()["status"])
        self.assertEqual([],self._retention_refs(),"no retention ref, so no candidate object is anchored")
        self.assertEqual(self.git_command("rev-parse","HEAD"),self.g.commit)
        artifacts_after=sorted(p.name for p in self.artifacts.root.glob("*.json"))
        self.assertEqual(len(artifacts_before)+1,len(artifacts_after),"the captured raw evidence is retained")
        self.assertEqual(["disclosure","execution","result"],self._boundary_stages(),
                         "refused before the integrator claims its own result boundary")
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex Coordination | GPT-6 | 2026-09-07 | 00:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 17:31 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:02 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]]
