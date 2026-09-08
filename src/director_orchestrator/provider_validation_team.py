# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-07 | 09:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Dedicated synthetic canary through the actual pinned SSH engineering worker."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

from .antigravity_team import TeamGrant, TeamAuthorizationError
from .antigravity_team_transport import verify_team_prepared, verify_team_capture
from .antigravity_team_worker import PinnedSshTeamWorker, authenticate
from .packet_contracts import canonical_json_bytes
from .provider_validation import ValidationError, digest, make_capture

SYNTHETIC_SOURCE={"validation-canary.txt":b"Director isolated native provider validation canary v1.\n"}


class TeamValidationRoute:
    def __init__(self, *, grant, owner_secret, worker_secret, worker_factory, configuration_digest):
        grant.verify(owner_secret,datetime.now(timezone.utc))
        if len(worker_secret)<32 or worker_secret==owner_secret:
            raise ValidationError("distinct worker capture authority required")
        expected={p:hashlib.sha256(b).hexdigest() for p,b in SYNTHETIC_SOURCE.items()}
        if (grant.operation!="review" or grant.commands or grant.writable_paths
            or {b.path:b.sha256 for b in grant.source}!=expected
            or not grant.task_id.startswith("VALIDATION-") or not grant.assignment_id.startswith("VALIDATION-")):
            raise ValidationError("only fixed synthetic immutable team canary is permitted")
        self.grant,self.owner_secret,self.worker_secret=grant,owner_secret,worker_secret
        self.worker_factory,self.configuration_digest=worker_factory,configuration_digest

    def check_work(self,work):
        g=self.grant
        g.verify(self.owner_secret,datetime.now(timezone.utc))
        if (digest(canonical_json_bytes(work))!=g.packet_digest
            or work["task_id"]!=g.task_id or work["assignment_id"]!=g.assignment_id
            or work["starting_ref"]!=g.commit or work["repository"]!=g.repository
            or work["project"]!=g.project_id or work["allowed_tools"] or work["allowed_external_effects"]):
            raise ValidationError("registered team validation packet differs from signed grant")

    def normalize(self, work, raw):
        self.check_work(work)
        if not raw or len(raw)>12*1024*1024:
            raise ValidationError("team validation capture exceeds bound")
        from .packet_contracts import _decode_json
        bundle=_decode_json(raw,source="team validation capture")
        if not isinstance(bundle,dict) or set(bundle)!={"prepared","capture","challenge","attempt_digest"}:
            raise ValidationError("complete native team capture bundle required")
        prepared=authenticate(self.worker_secret,bundle["prepared"])
        verify_team_prepared(prepared,grant=self.grant,attempt_digest=bundle["attempt_digest"],challenge=bundle["challenge"])
        return verify_team_capture(bundle["capture"],worker_secret=self.worker_secret,
            grant=self.grant,work=work,attempt_digest=bundle["attempt_digest"],challenge=bundle["challenge"],
            prepared=prepared,authorized_commands=[])

    def run(self,service,request,owner_token):
        admission=service.store.admit(request,owner_token,monetary_admission=service.monetary_admission,
                                      offline_fixture=service.offline_fixture)
        worker=None
        try:
            if admission.get("execution_authority") is not None:
                admission["execution_authority"].require_supported_execution()
            work=json.loads(admission["packet"])
            self.check_work(work)
            g=self.grant
            if (g.commit!=admission["candidate"] or g.tree!=admission["tree"]
                or g.binding_digest!=digest(canonical_json_bytes(admission["binding"]))
                or self.configuration_digest()!=admission["configuration_digest"]
                or int(g.expires_at.timestamp())<admission["expires_at"]):
                raise ValidationError("team validation binding or generation mismatch")
            service.store.claim_execution(admission)
            # Same durable validation fence used by the native host bridge, with
            # a separate reservation identity from the signed fixed canary task.
            fence=SimpleNamespace(task_id="VALIDATION-"+admission["reservation"],
                assignment_id="VALIDATION-"+admission["reservation"],writer_generation=admission["fence"],
                nonce=admission["nonce"],packet_digest=digest(admission["packet"]),
                writer_owner_token_digest="sha256:"+admission["owner_digest"],provider="antigravity")
            service.store.verify_fence(fence)
            challenge=secrets.token_hex(32)
            attempt="sha256:"+admission["reservation"]
            worker=self.worker_factory(g)
            prepared=worker.prepare(grant_digest=g.digest,attempt_digest=attempt,challenge=challenge)
            verified=authenticate(self.worker_secret,prepared)
            verify_team_prepared(verified,grant=g,attempt_digest=attempt,challenge=challenge)
            service.store.verify_fence(fence)
            calls=[]
            def before_execute():
                if calls:
                    raise ValidationError("duplicate native validation execution")
                service.store.verify_fence(fence)
                verify_team_prepared(verified,grant=g,attempt_digest=attempt,challenge=challenge)
                calls.append(True)
            def no_command(argv):
                raise ValidationError("validation never permits local commands")
            capture=worker.exchange(grant=g,work_packet=work,source=dict(SYNTHETIC_SOURCE),
                challenge=challenge,before_execute=before_execute,before_local_command=no_command)
            if len(calls)!=1:
                raise ValidationError("worker did not obtain immediate execution admission")
            service.store.verify_fence(fence)
            if self.configuration_digest()!=admission["configuration_digest"]:
                raise ValidationError("team configuration changed during execution")
            raw=canonical_json_bytes(dict(prepared=prepared,capture=capture,challenge=challenge,attempt_digest=attempt))
            signed=make_capture(admission,raw,capture_key=service.capture_key,team_route=self)
            evidence=service.store.settle(admission,raw,signed)
            return {"evidence_digest":evidence,"evidence_id":admission["reservation"],
                "expires_at":signed.expires_at,"surfaces":["app","runtime_adapter"]}
        except Exception:
            service.store.fail(admission["reservation"])
            raise
        finally:
            if worker is not None:
                worker.close()


def team_route_from_environment():
    """Explicit protected operator files; no arbitrary import or command request."""
    import os
    location=os.environ.get("DIRECTOR_PROVIDER_VALIDATION_TEAM_CONFIG")
    if not location:
        return None
    config_path=Path(location)
    config=json.loads(config_path.read_bytes())
    expected={"grant_file","owner_key_file","worker_key_file","ssh","ssh_sha256","host","user",
        "identity_file","known_hosts","worker_command"}
    if set(config)!=expected:
        raise ValidationError("closed team validation configuration required")
    def file_bytes(name):
        p=Path(config[name])
        if not p.is_absolute() or p.is_symlink() or not p.is_file():
            raise ValidationError("absolute regular pinned configuration file required")
        return p.read_bytes()
    grant=TeamGrant.model_validate_json(file_bytes("grant_file"))
    owner=file_bytes("owner_key_file")
    worker_key=file_bytes("worker_key_file")
    def fingerprint():
        files={name:digest(file_bytes(name)) for name in (
            "grant_file","owner_key_file","worker_key_file","ssh","identity_file","known_hosts")}
        files.update({name:digest(Path(__file__).with_name(name).read_bytes()) for name in (
            "provider_validation_team.py","antigravity_team_worker.py","antigravity_team_transport.py")})
        return digest(canonical_json_bytes({"config":config,"config_file":digest(config_path.read_bytes()),"files":files}))
    def factory(g):
        return PinnedSshTeamWorker(ssh=Path(config["ssh"]),ssh_sha256=config["ssh_sha256"],
            host=config["host"],user=config["user"],identity_file=Path(config["identity_file"]),
            known_hosts=Path(config["known_hosts"]),worker_command=config["worker_command"],worker_secret=worker_key,grant=g)
    return TeamValidationRoute(grant=grant,owner_secret=owner,worker_secret=worker_key,
        worker_factory=factory,configuration_digest=fingerprint)
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-07 | 09:14 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | END]]
