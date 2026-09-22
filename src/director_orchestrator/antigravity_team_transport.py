# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
"""Team packet boundary for an independently qualified isolated native worker.

The worker is a protected broker integration, not an arbitrary command callback.
No local fallback exists: absent authenticated worker evidence fails closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Mapping
from pathlib import Path
import os
import stat
import secrets
import re
from datetime import datetime,timezone

from .antigravity_team import TeamAuthorizationError, TeamAuthority, safe_path
from .entitlement_contracts import canonical_json_bytes, document_digest
from .packet_contracts import validate_packet


def verified_source_bytes(grant, root: Path):
    """Read only the manifest, rejecting aliases; disclose nothing on a bad blob."""
    root=root.resolve(strict=True)
    content={}
    total=0
    for blob in grant.source:
        path=root / blob.path
        for item in (path,*path.parents):
            if item==root:
                break
            metadata=item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata,"st_file_attributes",0)&1024:
                raise TeamAuthorizationError("source contains a reparse/symlink alias")
        metadata=path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1:
            raise TeamAuthorizationError("source must be a regular single-link file")
        total+=metadata.st_size
        if total>16*1024*1024:
            raise TeamAuthorizationError("source export exceeds bound")
        with path.open("rb") as handle:
            actual=os.fstat(handle.fileno())
            if (actual.st_dev,actual.st_ino)!=(metadata.st_dev,metadata.st_ino):
                raise TeamAuthorizationError("source changed during read")
            data=handle.read(16*1024*1024+1)
        if hashlib.sha256(data).hexdigest()!=blob.sha256:
            raise TeamAuthorizationError("source manifest blob mismatch")
        content[blob.path]=data
    return content


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TeamAuthorizationError("duplicate native stream key")
        result[key] = value
    return result


def parse_native_stream(raw: bytes, *, grant, expected_cwd: str):
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise TeamAuthorizationError("native stream size invalid")
    events = [json.loads(line,object_pairs_hook=_unique) for line in raw.splitlines() if line.strip()]
    if len(events) < 2 or events[0].get("event") != "init" or events[-1].get("event") != "result":
        raise TeamAuthorizationError("native stream incomplete")
    if sum(e.get("event")=="init" for e in events)!=1 or sum(e.get("event")=="result" for e in events)!=1:
        raise TeamAuthorizationError("duplicate native completion")
    init=events[0]
    session=init.get("conversation_id")
    config=init.get("init",{})
    if not isinstance(session,str) or not session or config.get("cwd") != expected_cwd:
        raise TeamAuthorizationError("native session/workspace mismatch")
    # CLI model in init is a requested configuration observation, not proof of
    # the backend actually serving that model.
    if config.get("model") != grant.model:
        raise TeamAuthorizationError("native requested model mismatch")
    for event in events[1:-1]:
        step=event.get("step_update",{})
        if event.get("event")!="step_update" or step.get("conversation_id")!=session:
            raise TeamAuthorizationError("foreign native stream event")
        if step.get("subagent_info"):
            raise TeamAuthorizationError("ungranted nested dispatch")
        if grant.operation=="review" and (step.get("step_type")=="tool" or step.get("tool_info")):
            raise TeamAuthorizationError("tool-bearing review stream")
    terminal=events[-1].get("result",{})
    if terminal.get("conversation_id")!=session or terminal.get("status")!="SUCCESS":
        raise TeamAuthorizationError("native terminal identity/status mismatch")
    result=terminal.get("structured_output")
    if not isinstance(result,dict):
        result=json.loads(terminal.get("response",""),object_pairs_hook=_unique)
    if not isinstance(result,dict):
        raise TeamAuthorizationError("native result must be an object")
    return result, session, terminal.get("usage")


class NativeTeamPacketTransport:
    """Native-only, grant-bound exchange with a protected isolated worker.

    ``worker.exchange`` is an authenticated broker transport. Its signed capture
    must attest the complete installation and actual containment evidence. It
    cannot use a model-supplied payload as its capture signature source.
    """
    def __init__(self, *, authority: TeamAuthority, pool, grant_digest: str,
                 attempt_digest: str, query_factory, worker, worker_verifier_secret: bytes,
                 source_root: Path, artifact_store=None, integrator=None, writer_owner_token=None):
        if len(worker_verifier_secret)<32 or worker_verifier_secret==authority.entitlements._owner_authority_secret:
            raise TeamAuthorizationError("distinct protected worker verifier required")
        self.authority,self.pool,self.grant_digest=authority,pool,grant_digest
        self.attempt_digest,self.query_factory,self.worker=attempt_digest,query_factory,worker
        self.secret=worker_verifier_secret
        self.source_root=source_root
        self.artifacts, self.integrator = artifact_store, integrator
        self.writer_owner_token=writer_owner_token

    def _boundary(self, stage, command=None, *, deadline=None):
        from .langgraph_postgres import bounded_transaction
        query=self.query_factory(stage,command)
        with bounded_transaction(self.pool, deadline) as connection:
            return self.authority.authorize(connection,grant_digest=self.grant_digest,
                query=query,stage=stage,attempt_digest=self.attempt_digest,command=command)

    def _worker_bound(self, deadline):
        """The remaining primary budget, for a worker that honours one.

        A worker that does not declare support keeps its own grant timer and
        its existing call signature - genuine standalone use, where no
        primary bound exists. But a primary bound that *does* exist cannot be
        silently dropped: this transport would then hold a worker to nothing
        while claiming the wave was bounded, so an unsupported worker is
        refused before it is invoked at all.
        """

        if deadline is None:
            return {}
        if not getattr(self.worker, "accepts_primary_deadline", False):
            raise TeamAuthorizationError(
                "native team worker cannot enforce the primary deadline")
        return {"deadline": deadline}

    def _authenticated_evidence(self, response):
        if not isinstance(response,dict) or set(response)!={"evidence","signature"}:
            raise TeamAuthorizationError("authenticated worker evidence required")
        expected=hmac.new(self.secret,canonical_json_bytes(response["evidence"]),hashlib.sha256).hexdigest()
        if not isinstance(response["signature"],str) or not hmac.compare_digest(expected,response["signature"]):
            raise TeamAuthorizationError("worker evidence signature invalid")
        return response["evidence"]

    def send(self, work_packet_json: bytes, *, context) -> Mapping[str,object]:
        try:
            return self._send(work_packet_json,context=context)
        finally:
            close=getattr(self.worker,"close",None)
            if callable(close):close()

    def _send(self, work_packet_json: bytes, *, context) -> Mapping[str,object]:
        from .langgraph_postgres import bounded_transaction, require_deadline_remaining
        from .primary_parallel import completion_deadline
        # The primary lane's own absolute budget and authority recheck, carried
        # in the adapter context. This transport never mints, extends or
        # restarts either: it spends the remaining budget and re-reads the
        # same durable authority the coordinator owns. A standalone caller
        # carries neither, and every check below is then a no-op.
        deadline = getattr(context, "deadline", None)
        recheck = getattr(context, "authority_recheck", None)

        def remaining(what):
            return require_deadline_remaining(deadline, what)

        remaining("native team dispatch")
        work=validate_packet("work",json.loads(work_packet_json,object_pairs_hook=_unique))
        with bounded_transaction(self.pool, deadline) as connection:
            grant,_=self.authority._load(connection,self.grant_digest)
        if (document_digest(work).removeprefix("sha256:")!=grant.packet_digest
                or work["task_id"]!=grant.task_id or work["assignment_id"]!=grant.assignment_id
                or work["repository"]!=grant.repository or work["starting_ref"]!=grant.commit
                or work["project"]!=grant.project_id
                or work["allowed_external_effects"] or context.timeout_seconds>grant.timeout_seconds):
            raise TeamAuthorizationError("team packet boundary mismatch")
        challenge=secrets.token_hex(32)
        # A fresh protected-worker attestation is required BEFORE any source is
        # exported. This is not a user-configured boolean or provider self-report.
        remaining("worker preparation")
        prepared=self._authenticated_evidence(self.worker.prepare(grant_digest=grant.digest,
            attempt_digest=self.attempt_digest,challenge=challenge,**self._worker_bound(deadline)))
        # Preparation can wait on the worker; what it waited with is the same
        # budget, and what is left of it still has to cover everything after.
        remaining("worker preparation")
        verify_team_prepared(prepared,grant=grant,attempt_digest=self.attempt_digest,challenge=challenge)
        if self.artifacts is None or self.integrator is None or not self.writer_owner_token:
            raise TeamAuthorizationError("protected durable candidate integration is unbound")
        # Do not disclose even the packet until its durable STARTED attempt exists.
        with bounded_transaction(self.pool, deadline) as connection:
            row=connection.execute(f"""SELECT 1 FROM {self.authority.schema}.director_primary_provider_attempt a
                JOIN {self.authority.schema}.director_primary_provider_spend s USING(attempt_digest)
                WHERE a.attempt_digest=%s AND s.grant_digest=%s AND a.status='STARTED'
                AND a.task_id=%s AND a.assignment_id=%s
                AND NOT EXISTS(SELECT 1 FROM {self.authority.schema}.director_primary_provider_attempt later
                    WHERE later.attempt_digest=a.attempt_digest AND later.event_sequence>a.event_sequence)""",
                (self.attempt_digest,grant.digest,grant.task_id,grant.assignment_id)).fetchone()
            if row is None:
                raise TeamAuthorizationError("native team dispatch lacks authoritative active attempt")
        source=verified_source_bytes(grant,self.source_root)
        self._boundary("disclosure",deadline=deadline)
        execution_callbacks=[]
        authorized_commands=[]
        def before_execute():
            if execution_callbacks:
                raise TeamAuthorizationError("native attempt cannot execute twice")
            # Immediately before native execution is authorized, and after
            # preparation has already waited: the primary cancellation, writer
            # lease and plan binding are re-read from durable state, and the
            # carried budget must still hold. Nothing here is a post-hoc
            # elapsed check - refusing at this point means the worker is never
            # told to execute.
            if recheck is not None:
                recheck()
            remaining("native execution")
            if datetime.fromisoformat(prepared["expires_at"])<=datetime.now(timezone.utc):
                raise TeamAuthorizationError("worker qualification expired before execution")
            admitted=self._boundary("execution",deadline=deadline)
            # Claiming the execution entitlement takes a pooled connection, a
            # query and its row locks, and those wait. The decisive recheck is
            # therefore *after* that wait, with nothing left between it and the
            # worker being told to execute.
            if recheck is not None:
                recheck()
            remaining("native execution")
            execution_callbacks.append(admitted)
        def before_command(argv):
            if not execution_callbacks:
                raise TeamAuthorizationError("local command before native execution admission")
            remaining("local command")
            if recheck is not None:
                recheck()
            self._boundary("command",tuple(argv),deadline=deadline)
            # Same boundary, same reason: the authorization above blocks, and
            # this command runs immediately after it.
            if recheck is not None:
                recheck()
            remaining("local command")
            authorized_commands.append(list(argv))
        capture=self.worker.exchange(grant=grant,work_packet=work,source=source,challenge=challenge,
            before_execute=before_execute,
            before_local_command=before_command,**self._worker_bound(deadline))
        if len(execution_callbacks)!=1:
            raise TeamAuthorizationError("worker did not authorize immediate execution")
        # Execution happened. What follows records and integrates what it
        # produced, so it spends the same operation's one completion
        # allowance - never a fresh budget - and a failure here is refused
        # conservatively rather than retried: the capture and the durable
        # attempt remain for reconciliation.
        settlement = completion_deadline(deadline)
        require_deadline_remaining(settlement, "team completion")
        result=verify_team_capture(capture,worker_secret=self.secret,grant=grant,work=work,
            attempt_digest=self.attempt_digest,challenge=challenge,prepared=prepared,
            authorized_commands=authorized_commands)
        self._boundary("result",deadline=settlement)
        validated=validate_packet("result",result)
        if self.artifacts is None or self.integrator is None:
            raise TeamAuthorizationError("durable team artifact integration is unbound")
        observer=getattr(context,"execution_observer",None)
        if observer is not None:
            observer("antigravity",canonical_json_bytes(capture),validated)
        capture_digest=self.artifacts.put(capture)
        # The candidate is a durable effect, so the same authority is re-read
        # immediately before it: a wave cancelled, or a writer lost, while the
        # worker was running must not gain a candidate commit.
        if recheck is not None:
            recheck()
        require_deadline_remaining(settlement, "candidate integration")
        candidate=self.integrator.integrate(grant=grant,attempt_digest=self.attempt_digest,
            capture_digest=capture_digest,query=self.query_factory("result",None),
            owner_token=self.writer_owner_token,
            fencing_generation=context.fencing_generation,
            # The same fixed completion bound the rest of this settlement uses,
            # carried through the integrator's Git and database work rather
            # than replaced by a timer of its own.
            **({} if settlement is None else {"deadline": settlement}),
            # The read just above ran in its own transaction. A same-owner
            # cancellation completing between it and the integrator's writer
            # lock would still be seen only by a read *inside* that lock, so
            # the integrator runs this same recheck once more on its own
            # connection before any Git object is written. A standalone caller
            # carries no recheck and the integrator keeps its previous contract.
            **({} if recheck is None else {"authority_recheck": recheck}))
        validated["artifacts"]=[a for a in validated["artifacts"] if a["kind"]!="git_commit"]+[
            {"kind":"git_commit","location":grant.repository+"@"+candidate,"digest":"sha256:"+capture_digest}]
        return validate_packet("result",validated)




def verify_team_prepared(prepared, *, grant, attempt_digest, challenge):
    if set(prepared)!={"grant_digest","attempt_digest","challenge","binding_digest","workspace_id",
            "containment_evidence_digest","expires_at","denied_access","authentication_mode"}:
        raise TeamAuthorizationError("worker qualification contract mismatch")
    if (prepared["grant_digest"]!=grant.digest or prepared["attempt_digest"]!=attempt_digest
            or prepared["challenge"]!=challenge or prepared["binding_digest"]!=grant.binding_digest
            or prepared["workspace_id"]!=grant.workspace_id or prepared["authentication_mode"]!="native-account"
            or not isinstance(prepared["containment_evidence_digest"],str)
            or not re.fullmatch(r"[0-9a-f]{64}",prepared["containment_evidence_digest"])
            or set(prepared["denied_access"])!={"unassigned_source","git_history","coordinator_credentials",
                "broker_keys","launcher_mutation","production_network"}
            or datetime.fromisoformat(prepared["expires_at"])<=datetime.now(timezone.utc)):
        raise TeamAuthorizationError("worker containment qualification invalid")
    return prepared

def verify_team_capture(capture, *, worker_secret, grant, work, attempt_digest, challenge, prepared, authorized_commands):
    """Pure authenticated capture verification shared by production and canaries."""
    if not isinstance(capture,dict) or set(capture)!={"evidence","signature"}:
        raise TeamAuthorizationError("authenticated native capture required")
    evidence=capture["evidence"]
    expected=hmac.new(worker_secret,canonical_json_bytes(evidence),hashlib.sha256).hexdigest()
    if not isinstance(capture["signature"],str) or not hmac.compare_digest(expected,capture["signature"]):
        raise TeamAuthorizationError("native capture authentication failed")
    required={"grant_digest","attempt_digest","packet_digest","installation_id","executable_digest",
        "runtime_digest","adapter_digest","binding_digest","native_account_id","authentication_mode",
        "workspace_id","cwd","source_manifest_digest","containment_evidence_digest","native_stream","exit_code","challenge",
        "changed_files","returned_files","executed_commands","checks","completed_at","native_project_id","native_identity_observations","native_state_manifest_after"}
    if set(evidence)!=required:
        raise TeamAuthorizationError("native capture contract mismatch")
    if not isinstance(evidence["native_state_manifest_after"],dict) or len(evidence["native_state_manifest_after"])>10000:
        raise TeamAuthorizationError("native state capture invalid")
    for path,digest in evidence["native_state_manifest_after"].items():
        safe_path(path)
        if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
            raise TeamAuthorizationError("native state capture digest invalid")
    if evidence["native_project_id"]!=grant.native_project_id or evidence["native_identity_observations"]!={
        "requested_project_id":grant.native_project_id,"observed_project_id":None,"observed_account_id":None}:
        raise TeamAuthorizationError("native project invocation binding or observation provenance changed")
    for key in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","workspace_id"):
        if evidence[key]!=getattr(grant,key):
            raise TeamAuthorizationError("native installation binding mismatch")
    if (evidence["grant_digest"]!=grant.digest or evidence["attempt_digest"]!=attempt_digest
            or evidence["packet_digest"]!=grant.packet_digest or evidence["authentication_mode"]!="native-account"
            or evidence["challenge"]!=challenge or evidence["containment_evidence_digest"]!=prepared["containment_evidence_digest"]
            or evidence["exit_code"]!=0 or not evidence["containment_evidence_digest"]
            or evidence["source_manifest_digest"]!=document_digest({"source":[item.model_dump() for item in grant.source]})):
        raise TeamAuthorizationError("native capture authority mismatch")
    proposal,session,native_usage=parse_native_stream(evidence["native_stream"].encode(),grant=grant,expected_cwd=evidence["cwd"])
    if set(proposal)!={"packet","returned_files"} or proposal["returned_files"]!=evidence["returned_files"]:
        raise TeamAuthorizationError("native proposal differs from captured returned bytes")
    result=dict(proposal["packet"])
    if any(result.get(key)!=work.get(key) for key in ("task_id","assignment_id","idempotency_key","authority_snapshot_digest","correlation")) or result.get("provider")!="antigravity":
        raise TeamAuthorizationError("native result identity differs from assignment")
    if not isinstance(native_usage,dict) or type(native_usage.get("total_tokens")) is not int or not 0<=native_usage["total_tokens"]<=grant.max_tokens:
        raise TeamAuthorizationError("native usage missing or exceeds authorized ceiling")
    result["run_reference"]=session
    result["usage"]={"model":"unknown","input_tokens":native_usage.get("input_tokens"),
        "output_tokens":native_usage.get("output_tokens"),"total_tokens":native_usage["total_tokens"],"cost_usd":None}
    if result.get("checks"):
        raise TeamAuthorizationError("native packet cannot impersonate independently executed checks")
    result["checks"]=evidence["checks"]
    from .antigravity_team_artifacts import decode_returned_files
    files=decode_returned_files(grant,evidence["returned_files"])
    if evidence["changed_files"] != [{"path":p,"sha256":hashlib.sha256(data).hexdigest()} for p,data in files.items()]:
        raise TeamAuthorizationError("changed-file inventory differs from returned bytes")
    if evidence["executed_commands"]!=authorized_commands:
        raise TeamAuthorizationError("worker command log differs from authorized commands")
    for entry in evidence["changed_files"]:
        if (not isinstance(entry,dict) or set(entry)!={"path","sha256"}
                or safe_path(entry["path"]) not in grant.writable_paths
                or not isinstance(entry["sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",entry["sha256"])):
            raise TeamAuthorizationError("worker changed an ungranted path")
    if grant.operation=="review" and (evidence["changed_files"] or authorized_commands):
        raise TeamAuthorizationError("review worker changed immutable state")
    if datetime.fromisoformat(prepared["expires_at"])<=datetime.now(timezone.utc):
        raise TeamAuthorizationError("worker qualification expired before result admission")
    if any(check.get("exit_code")!=0 for check in evidence["checks"]) or result.get("side_effects"):
        raise TeamAuthorizationError("provider checks/effects lack independent worker evidence")
    return validate_packet("result",result)

def team_packet_adapter(settings, **transport_arguments):
    """Explicit per-assignment route; never replace the closed subordinate lane."""
    from .langgraph_runtime import PacketBoundaryAdapter
    if settings.langgraph_primary_antigravity_team_enabled is not True:
        raise TeamAuthorizationError("primary Antigravity team mode is disabled")
    return PacketBoundaryAdapter("antigravity",NativeTeamPacketTransport(**transport_arguments))
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]]
