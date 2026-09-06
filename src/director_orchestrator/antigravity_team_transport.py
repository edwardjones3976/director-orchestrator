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
                 source_root: Path):
        if len(worker_verifier_secret)<32 or worker_verifier_secret==authority.entitlements._owner_authority_secret:
            raise TeamAuthorizationError("distinct protected worker verifier required")
        self.authority,self.pool,self.grant_digest=authority,pool,grant_digest
        self.attempt_digest,self.query_factory,self.worker=attempt_digest,query_factory,worker
        self.secret=worker_verifier_secret
        self.source_root=source_root

    def _boundary(self, stage, command=None):
        query=self.query_factory(stage,command)
        with self.pool.connection() as connection, connection.transaction():
            return self.authority.authorize(connection,grant_digest=self.grant_digest,
                query=query,stage=stage,attempt_digest=self.attempt_digest,command=command)

    def _authenticated_evidence(self, response):
        if not isinstance(response,dict) or set(response)!={"evidence","signature"}:
            raise TeamAuthorizationError("authenticated worker evidence required")
        expected=hmac.new(self.secret,canonical_json_bytes(response["evidence"]),hashlib.sha256).hexdigest()
        if not isinstance(response["signature"],str) or not hmac.compare_digest(expected,response["signature"]):
            raise TeamAuthorizationError("worker evidence signature invalid")
        return response["evidence"]

    def send(self, work_packet_json: bytes, *, context) -> Mapping[str,object]:
        work=validate_packet("work",json.loads(work_packet_json,object_pairs_hook=_unique))
        with self.pool.connection() as connection,connection.transaction():
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
        prepared=self._authenticated_evidence(self.worker.prepare(grant_digest=grant.digest,
            attempt_digest=self.attempt_digest,challenge=challenge))
        if set(prepared)!={"grant_digest","attempt_digest","challenge","binding_digest","workspace_id",
                "containment_evidence_digest","expires_at","denied_access","authentication_mode"}:
            raise TeamAuthorizationError("worker qualification contract mismatch")
        if (prepared["grant_digest"]!=grant.digest or prepared["attempt_digest"]!=self.attempt_digest
                or prepared["challenge"]!=challenge or prepared["binding_digest"]!=grant.binding_digest
                or prepared["workspace_id"]!=grant.workspace_id or prepared["authentication_mode"]!="native-account"
                or not isinstance(prepared["containment_evidence_digest"],str)
                or not re.fullmatch(r"[0-9a-f]{64}",prepared["containment_evidence_digest"])
                or set(prepared["denied_access"])!={"unassigned_source","git_history","coordinator_credentials",
                    "broker_keys","launcher_mutation","production_network"}
                or datetime.fromisoformat(prepared["expires_at"])<=datetime.now(timezone.utc)):
            raise TeamAuthorizationError("worker containment qualification invalid")
        # Do not disclose even the packet until its durable STARTED attempt exists.
        with self.pool.connection() as connection, connection.transaction():
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
        self._boundary("disclosure")
        execution_callbacks=[]
        authorized_commands=[]
        def before_execute():
            if execution_callbacks:
                raise TeamAuthorizationError("native attempt cannot execute twice")
            if datetime.fromisoformat(prepared["expires_at"])<=datetime.now(timezone.utc):
                raise TeamAuthorizationError("worker qualification expired before execution")
            execution_callbacks.append(self._boundary("execution"))
        def before_command(argv):
            if not execution_callbacks:
                raise TeamAuthorizationError("local command before native execution admission")
            self._boundary("command",tuple(argv))
            authorized_commands.append(list(argv))
        capture=self.worker.exchange(grant=grant,work_packet=work,source=source,challenge=challenge,
            before_execute=before_execute,
            before_local_command=before_command)
        if len(execution_callbacks)!=1:
            raise TeamAuthorizationError("worker did not authorize immediate execution")
        if not isinstance(capture,dict) or set(capture)!={"evidence","signature"}:
            raise TeamAuthorizationError("authenticated native capture required")
        evidence=capture["evidence"]
        expected=hmac.new(self.secret,canonical_json_bytes(evidence),hashlib.sha256).hexdigest()
        if not isinstance(capture["signature"],str) or not hmac.compare_digest(expected,capture["signature"]):
            raise TeamAuthorizationError("native capture authentication failed")
        required={"grant_digest","attempt_digest","packet_digest","installation_id","executable_digest",
            "runtime_digest","adapter_digest","binding_digest","native_account_id","authentication_mode",
            "workspace_id","cwd","source_manifest_digest","containment_evidence_digest","native_stream","exit_code","challenge",
            "changed_files","executed_commands","checks"}
        if set(evidence)!=required:
            raise TeamAuthorizationError("native capture contract mismatch")
        for key in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","workspace_id"):
            if evidence[key]!=getattr(grant,key):
                raise TeamAuthorizationError("native installation binding mismatch")
        if (evidence["grant_digest"]!=grant.digest or evidence["attempt_digest"]!=self.attempt_digest
                or evidence["packet_digest"]!=grant.packet_digest or evidence["authentication_mode"]!="native-account"
                or evidence["challenge"]!=challenge or evidence["containment_evidence_digest"]!=prepared["containment_evidence_digest"]
                or evidence["exit_code"]!=0 or not evidence["containment_evidence_digest"]
                or evidence["source_manifest_digest"]!=document_digest({"source":[item.model_dump() for item in grant.source]})):
            raise TeamAuthorizationError("native capture authority mismatch")
        result,_,_=parse_native_stream(evidence["native_stream"].encode(),grant=grant,expected_cwd=evidence["cwd"])
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
        if result.get("checks")!=evidence["checks"] or result.get("side_effects"):
            raise TeamAuthorizationError("provider checks/effects lack independent worker evidence")
        self._boundary("result")
        return validate_packet("result",result)


def team_packet_adapter(settings, **transport_arguments):
    """Explicit per-assignment route; never replace the closed subordinate lane."""
    from .langgraph_runtime import PacketBoundaryAdapter
    if settings.langgraph_primary_antigravity_team_enabled is not True:
        raise TeamAuthorizationError("primary Antigravity team mode is disabled")
    return PacketBoundaryAdapter("antigravity",NativeTeamPacketTransport(**transport_arguments))
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
