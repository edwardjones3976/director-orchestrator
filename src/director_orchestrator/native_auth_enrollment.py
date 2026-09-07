# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:44 UTC | BEGIN]]
"""Consume independently adopted native-auth evidence; never acquire or issue it.

Owner-pinned trust and an accepted installation are prerequisites. The native
stream's requested auth label is never used as an observation or trust anchor.
"""
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .native_acceptance_issuer import (IssuerError, canonical, contained_native_path,
    context_from, digest, verify_boundary, _pairs)
from .provider_acceptance_v2 import IssuerAuthority, verify_provider_acceptance

DOMAIN = b"director-native-auth-enrollment/v1\0"
VERSION = "director-native-auth-enrollment/v1"


@dataclass(frozen=True)
class VerifiedNativeAuth:
    enrollment_digest: str
    binding_digest: str


def _closed(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise IssuerError("native enrollment contract mismatch")
    return value


def _bytes(cfg, path, *, worker, limit=4*1024*1024):
    path = contained_native_path(str(path), (str(cfg.root), *cfg.runtime_roots))
    verify_boundary(cfg.root, broker_sid=cfg.broker_sid, worker_sid=cfg.worker_sid,
        worker=worker, consumed=[{"path":str(path),"kind":"protected"}], full=False)
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit:
        raise IssuerError("native enrollment artifact is not a bounded regular file")
    with path.open('rb') as handle:
        value=handle.read(limit+1)
    if len(value)>limit:
        raise IssuerError("native enrollment artifact exceeds read bound")
    return value


def installation_binding(cfg, *, worker=False):
    """Bind actual installed code/config bytes, not just an asserted source SHA."""
    manifest = json.loads(_bytes(cfg,cfg.root/"installation.json",worker=worker),object_pairs_hook=_pairs)
    for name, expected in ((cfg.executable,cfg.executable_digest),
                           (cfg.entrypoint,cfg.entrypoint_digest),
                           (cfg.native_deny_hook,cfg.native_deny_hook_digest)):
        if name and digest(_bytes(cfg,name,worker=worker,limit=256*1024*1024)) != expected:
            raise IssuerError("native enrollment runtime installation changed")
    if manifest.get("broker_sid") != cfg.broker_sid or manifest.get("worker_sid") != cfg.worker_sid:
        raise IssuerError("native enrollment installation identities differ")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files)>30000:
        raise IssuerError("native enrollment installation manifest missing")
    seen=set()
    for entry in files:
        _closed(entry,{"path","digest"})
        name=entry["path"]
        if not isinstance(name,str) or not name or name in seen or Path(name).is_absolute() or ".." in Path(name).parts:
            raise IssuerError("native enrollment installation path invalid")
        seen.add(name)
        if digest(_bytes(cfg,cfg.root/name,worker=worker)) != entry["digest"]:
            raise IssuerError("native enrollment installed file changed")
    if "issuer.json" not in seen or not any(n.replace('\\','/').startswith('code/') for n in seen):
        raise IssuerError("native enrollment manifest omits issuer code/config")
    return dict(installation_manifest_digest=digest(canonical(manifest)),
        source_commit=manifest.get("commit"), source_tree=manifest.get("tree"),
        provider=cfg.provider, deployment=cfg.deployment, repository=cfg.repository,
        broker_sid=cfg.broker_sid,worker_sid=cfg.worker_sid,
        executable_digest=cfg.executable_digest, entrypoint_digest=cfg.entrypoint_digest,
        native_home=cfg.native_home,native_project=cfg.native_project,
        native_account_id=cfg.native_auth_account_id,native_deny_hook_digest=cfg.native_deny_hook_digest)


def require_native_auth(cfg, *, worker=False, expected_digest=None, now=None):
    if cfg.provider != "gemini":
        return None
    try:
        return _require(cfg,worker=worker,expected_digest=expected_digest,
                        now=int(time.time()) if now is None else now)
    except (ValueError,TypeError,KeyError,AttributeError,OSError,InvalidSignature) as exc:
        raise IssuerError("native AGY acceptance lane unavailable: independent native authentication enrollment invalid: " + str(exc)) from None


def _require(cfg, *, worker, expected_digest, now):
    if not cfg.native_auth_account_id or len(cfg.native_auth_registry_sha256)!=64:
        raise IssuerError("owner-pinned native enrollment trust is missing")
    registry=json.loads(_bytes(cfg,cfg.root/"native-auth"/"registry.json",worker=worker),object_pairs_hook=_pairs)
    if digest(canonical(registry)) != cfg.native_auth_registry_sha256:
        raise IssuerError("native enrollment registry pin changed")
    _closed(registry,{"version","authorities","revoked_deployments","revoked_enrollments",
                      "adoption_context","adoption_package","adopted_at"})
    if registry["version"] != "director-native-auth-trust/v1":
        raise IssuerError("native enrollment registry version invalid")
    authorities={}
    for role, fields in _closed(registry["authorities"],{"security","independent_review","ci"}).items():
        value=dict(fields)
        value["public_key"]=base64.b64decode(value["public_key"],validate=True)
        value["required_ci_checks"]=frozenset(value["required_ci_checks"])
        authorities[role]=IssuerAuthority(**value)
    for name in ("revoked_deployments","revoked_enrollments"):
        if not isinstance(registry[name],list) or any(not isinstance(v,str) for v in registry[name]):
            raise IssuerError("native enrollment revocation set invalid")
    from .acceptance_journal import REQUIRED_ACTIVATION_CHECKS
    if not REQUIRED_ACTIVATION_CHECKS <= authorities['ci'].required_ci_checks:
        raise IssuerError("native enrollment adoption omits required CI checks")
    revoked=registry["revoked_deployments"]
    if cfg.deployment in revoked or any(a.deployment in revoked for a in authorities.values()):
        raise IssuerError("native enrollment authority or installation revoked")
    adoption=context_from(registry["adoption_context"])
    binding=installation_binding(cfg,worker=worker)
    if (adoption.activation.candidate_commit != binding["source_commit"]
        or adoption.activation.candidate_tree != binding["source_tree"]
        or adoption.repository != cfg.repository or type(registry["adopted_at"]) is not int
        or registry["adopted_at"]>now):
        raise IssuerError("native enrollment adoption belongs to another installation")
    # Initial adoption must use earlier independent families, not the new issuer.
    if any(a.provider==cfg.provider or a.principal in {cfg.broker_sid,cfg.worker_sid}
           or a.deployment==cfg.deployment for a in authorities.values()):
        raise IssuerError("native enrollment cannot self-adopt")
    verify_provider_acceptance(canonical(registry["adoption_package"]),expected=adoption,
        authorities=authorities,now=registry["adopted_at"])
    envelope=json.loads(_bytes(cfg,cfg.root/"native-auth"/"enrollment.json",worker=worker),object_pairs_hook=_pairs)
    _closed(envelope,{"evidence","signature"})
    evidence=_closed(envelope["evidence"],{"version","enrollment_id","authority_role","binding",
        "issued_at","expires_at","authentication_mode","observation_source","readback_digest","credential_files"})
    role=evidence["authority_role"]
    if role not in {"security","independent_review"}:
        raise IssuerError("native enrollment signer is not an independent native authority")
    signature=base64.b64decode(envelope["signature"],validate=True)
    Ed25519PublicKey.from_public_bytes(authorities[role].public_key).verify(signature,DOMAIN+canonical(evidence))
    if (evidence["version"]!=VERSION or evidence["binding"]!=binding
        or evidence["enrollment_id"] in registry["revoked_enrollments"]
        or not isinstance(evidence["enrollment_id"],str) or not evidence["enrollment_id"]
        or type(evidence["issued_at"]) is not int or type(evidence["expires_at"]) is not int
        or not evidence["issued_at"]<=now<evidence["expires_at"]
        or not 0<evidence["expires_at"]-evidence["issued_at"]<=3600
        or evidence["authentication_mode"]!="native-account"
        or evidence["observation_source"]!="native-authenticated-profile-and-project-readback"):
        raise IssuerError("native enrollment binding, mode, freshness or revocation failed")
    artifact=_bytes(cfg,cfg.root/"native-auth"/"readback.bin",worker=worker)
    if not artifact or digest(artifact)!=evidence["readback_digest"]:
        raise IssuerError("native enrollment observed readback artifact missing or changed")
    credentials=evidence["credential_files"]
    if not isinstance(credentials,dict) or not credentials or len(credentials)>128:
        raise IssuerError("native enrollment credential manifest missing")
    for relative,expected in credentials.items():
        if not isinstance(relative,str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise IssuerError("native credential path invalid")
        path=contained_native_path(str(Path(cfg.native_home)/relative),(cfg.native_home,))
        if digest(_bytes(cfg,path,worker=worker))!=expected:
            raise IssuerError("native authenticated credential state changed")
    enrollment_digest=digest(canonical(envelope))
    if expected_digest is not None and expected_digest != enrollment_digest:
        raise IssuerError("native enrollment changed during this attempt")
    return VerifiedNativeAuth(enrollment_digest,digest(canonical(binding)))
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:44 UTC | END]]
