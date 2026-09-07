# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
"""Fixed-role native acceptance issuer. Installed broker and worker SIDs differ.

No activation, account installation, remote fetch, arbitrary signing or imported
session API. The CLI operates only on administrator-provisioned authorizations.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .primary_activation_evidence import ActivationIdentity
from .provider_acceptance_v2 import DOMAIN, VERSION, ReviewContext, canonical

MAX_PACKET = 2_097_152  # full packet; never truncate a review
MAX_CAPTURE = 1_048_576
# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
# MAX_CAPTURE bounds the raw native process output. The canonical capture
# document wraps that output in an envelope and re-escapes non-ASCII text, so a
# raw stream at exactly MAX_CAPTURE serializes larger. Separate bounds keep the
# rejection honest instead of discarding complete, in-bound native evidence.
MAX_DOCUMENT = 4 * MAX_CAPTURE          # serialized capture document
MAX_RESPONSE = 2 * MAX_DOCUMENT         # base64 document plus worker provenance
# The AGY stream carries no independent observation of the native account or
# authentication mode; only a producer-controlled request label. Until protected
# host enrollment supplies trusted readback evidence, this lane cannot issue an
# acceptance receipt and must fail closed before any native attempt.
NATIVE_AUTH_UNENROLLED = frozenset({"gemini"})
# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]
ROLE_PROVIDER = {"security": "claude", "independent_review": "cursor"}
ROLE_PROVIDERS = {"security": frozenset({"claude", "openai", "gemini"}),
                  "independent_review": frozenset({"cursor", "gemini"})}
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class IssuerError(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise IssuerError("duplicate JSON field")
        out[k] = v
    return out


def read_json(path: Path, limit=131072):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise IssuerError("invalid bounded input")
        return json.loads(path.read_bytes(), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise IssuerError("invalid bounded input") from None


def write_new(path: Path, value):
    # A crash may leave a partial file: it must never be reinterpreted as ready.
    with path.open("xb") as f:
        f.write(canonical(value))
        f.flush()
        os.fsync(f.fileno())


# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
def _is_reparse(path: Path) -> bool:
    """Symlink, junction, mount point or any other reparse tag at this component."""
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", None)
    except OSError:
        raise IssuerError("native containment cannot be evaluated") from None
    if attributes is None:
        if os.name == "nt":
            raise IssuerError("native containment cannot be evaluated")
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def canonical_native_path(value) -> Path:
    """One exact absolute location: no traversal, no link/junction/alias escape.

    Lexical containment alone accepts protected\\..\\elsewhere and any ancestor
    reparse point, so the declared path must already be its own canonical
    location. Anything that cannot be evaluated here fails closed.
    """
    if not isinstance(value, str) or not value:
        raise IssuerError("native path must be configured text")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts) or str(path) != os.path.normpath(str(path)):
        raise IssuerError("native path must be an exact absolute location")
    if not path.exists():
        raise IssuerError("configured native path does not exist")
    for item in (path, *path.parents):
        if _is_reparse(item):
            raise IssuerError("native path or ancestor is a link, junction or reparse point")
    try:
        resolved = path.resolve()
    except OSError:
        raise IssuerError("native containment cannot be evaluated") from None
    if os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
        raise IssuerError("native path is not its own canonical location")
    return path


def contained_native_path(value, roots) -> Path:
    """Canonical containment inside a protected runtime root; roots are canonical too."""
    canonical_roots = [canonical_native_path(root) for root in roots]
    if not canonical_roots:
        raise IssuerError("protected runtime roots required for native containment")
    path = canonical_native_path(value)
    if not any(path == root or root in path.parents for root in canonical_roots):
        raise IssuerError("native path outside protected runtime roots")
    return path
# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]


def context_from(value: dict) -> ReviewContext:
    if set(value) != {"activation", "repository", "pull_request_number", "packet_digest",
                      "challenge", "issued_at", "expires_at", "material_author_providers"}:
        raise IssuerError("unknown review context fields")
    return ReviewContext(**{**value, "activation": ActivationIdentity(**value["activation"]),
                           "material_author_providers": frozenset(value["material_author_providers"])})


def _git(executable: str, repository: Path, *args: str) -> bytes:
    from .native_acceptance_capture import run_bounded
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    code, raw = run_bounded([executable, "--no-pager", "-c", "core.hooksPath=" + os.devnull,
                             "-C", str(repository), *args], cwd=repository, env=env, timeout=30, limit=MAX_PACKET)
    if code:
        raise IssuerError("Git read failed or packet exceeds complete-review bound")
    return raw


def prepare_packet(*, git: str, mirror: Path, repository: str, base: str, head: str) -> bytes:
    """Read immutable objects only; no checkout, hooks, textconv or source execution."""
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise IssuerError("exact Git revisions required")
    def run(*args):
        return _git(git, mirror, *args)
    run("merge-base", "--is-ancestor", base, head)
    tree = run("rev-parse", head + "^{tree}").decode().strip()
    paths = run("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", base, head).split(b"\0")
    files = []
    for encoded in sorted(p for p in paths if p):
        path = encoded.decode("utf-8")
        if path.startswith("/") or "\\" in path or any(p in {".", ".."} for p in path.split("/")):
            raise IssuerError("unsafe repository path")
        row = {"path": path}
        for label, revision in (("base", base), ("head", head)):
            entry = run("ls-tree", "-z", revision, "--", path)
            if not entry:
                row.update({label + "_blob": None, label + "_text": None})
                continue
            meta, actual = entry.rstrip(b"\0").split(b"\t", 1)
            mode, kind, oid = meta.split()
            if mode not in {b"100644", b"100755"} or kind != b"blob" or actual != encoded:
                raise IssuerError("nonregular source cannot be reviewed by this transport")
            raw = run("cat-file", "blob", oid.decode())
            if b"\0" in raw:
                raise IssuerError("binary source cannot be reviewed by this transport")
            row.update({label + "_blob": oid.decode(), label + "_text": raw.decode("utf-8")})
        files.append(row)
    if not files:
        raise IssuerError("empty review delta")
    packet = canonical(dict(version="director-native-review-packet/v1", repository=repository,
                            base=base, head=head, tree=tree, files=files,
                            diff=run("diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index", "--no-renames", base, head).decode("utf-8")))
    if len(packet) > MAX_PACKET:
        raise IssuerError("complete packet exceeds 2 MiB; no truncation permitted")
    return packet


def current_sid() -> str:
    if os.name != "nt":
        raise IssuerError("installed issuer requires Windows identity")
    exe = str(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe")
    raw = subprocess.check_output([exe, "/user", "/fo", "csv", "/nh"], timeout=10).decode()
    match = re.search(r"S-1-5-[0-9-]+", raw)
    if not match:
        raise IssuerError("Windows token SID unavailable")
    return match.group()


def validate_acl_rows(rows, *, broker_sid, worker_sid=None):
    """Check actual ACL grants, not a configuration label claiming isolation."""
    trusted = {"S-1-5-18", "S-1-5-32-544"}
    servicing = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    # NT access masks: read data/list, write data/add, append/add child, EA,
    # delete child, write attrs, delete, WRITE_DAC, WRITE_OWNER.
    write_mask = 2 | 4 | 16 | 64 | 256 | 65536 | 262144 | 524288
    replace_mask = 65536 | 64 | 262144 | 524288
    if not rows:
        raise IssuerError("Windows ACL evidence unavailable")
    for row in rows:
        row_trusted = trusted | ({servicing} if row["kind"] in {"ancestor", "runtime"} else set())
        private = row["kind"] == "private"
        owners = row_trusted | ({broker_sid} if private or row["kind"] in {"requests", "public"} else {worker_sid} if row["kind"] == "responses" else set())
        if row["owner"] not in owners or row["reparse"]:
            raise IssuerError("untrusted owner or reparse point in issuer boundary")
        for rule in row["rules"]:
            if not rule["allow"] or rule["inherit_only"]:
                continue
            if private:
                if rule["sid"] not in owners and rule["rights"]:
                    raise IssuerError("signer state accessible outside broker identity")
            elif rule["sid"] not in (row_trusted | ({broker_sid} if row["kind"] in {"requests", "public"} else {worker_sid} if row["kind"] == "responses" else set())) and rule["rights"] & (replace_mask if row["kind"] == "ancestor" else write_mask):
                raise IssuerError("issuer program or ancestor is replaceable")


def verify_boundary(root: Path, *, broker_sid: str, worker_sid: str, worker: bool, consumed=(), runtime_roots=(), full=True):
    if os.name != "nt":
        raise IssuerError("Windows ACL verification required")
    # Worker cannot inspect the private directory: that denial is the boundary.
    # Broker checks key/state grants before initialization and every signature.
    script = r'''
$ErrorActionPreference='Stop'
$root=Get-Item -LiteralPath $env:DIRECTOR_ISSUER_ROOT
$items=@(@{item=$root;kind='protected'},@{item=(Get-Item -LiteralPath (Join-Path $root.FullName 'code'));kind='protected'},@{item=(Get-Item -LiteralPath (Join-Path $root.FullName 'issuer.json'));kind='protected'})
foreach($name in @('requests','responses','cursor-config')){$items+=@{item=(Get-Item -LiteralPath (Join-Path $root.FullName $name));kind=$name}}
if($env:DIRECTOR_FULL_AUDIT -eq '1'){
$trees=@((Join-Path $root.FullName 'code'),(Join-Path $root.FullName 'cursor-config'))
$runtimeRoots=$env:DIRECTOR_RUNTIME_ROOTS | ConvertFrom-Json
foreach($runtimeRoot in $runtimeRoots){$trees+=[string]$runtimeRoot}
foreach($tree in $trees) {
 if($tree -isnot [string] -or -not $tree){continue}
 $children=@(Get-ChildItem -LiteralPath $tree -Recurse -Force)
 if($children.Count -gt 30000){throw 'Runtime inspection bound exceeded'}
 $kind=if($tree -in $runtimeRoots){'runtime'}else{'protected'}
 $items+=@{item=(Get-Item -LiteralPath $tree);kind=$kind}
 foreach($child in $children){$items+=@{item=$child;kind=$kind}}
 $ancestor=(Get-Item -LiteralPath $tree).Parent
 while($null -ne $ancestor){$items+=@{item=$ancestor;kind='ancestor'};$ancestor=$ancestor.Parent}
}
}
$consumed=$env:DIRECTOR_CONSUMED | ConvertFrom-Json
foreach($file in $consumed){
 if(-not $file.path){continue}
 $items+=@{item=(Get-Item -LiteralPath $file.path);kind=$file.kind}
}
$parent=$root.Parent
while($null -ne $parent){$items+=@{item=$parent;kind='ancestor'};$parent=$parent.Parent}
if($env:DIRECTOR_ISSUER_WORKER -ne '1'){
 $items+=@{item=(Get-Item -LiteralPath (Join-Path $root.FullName 'authorizations'));kind='protected'}
 $items+=@{item=(Get-Item -LiteralPath (Join-Path $root.FullName 'public'));kind='public'}
 $items+=@{item=(Get-Item -LiteralPath (Join-Path $root.FullName 'broker'));kind='private'}
 $key=Join-Path $root.FullName 'broker\private.key'
 if(Test-Path -LiteralPath $key){$items+=@{item=(Get-Item -LiteralPath $key);kind='private'}}
}
$rows=@(foreach($entry in $items){
 $a=Get-Acl -LiteralPath $entry.item.FullName
 $rules=@(foreach($r in $a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
  @{sid=$r.IdentityReference.Value;rights=[int]$r.FileSystemRights;allow=($r.AccessControlType -eq 'Allow');inherit_only=([bool]($r.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly))}
 })
 @{kind=$entry.kind;owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value;reparse=([bool]($entry.item.Attributes -band [IO.FileAttributes]::ReparsePoint));rules=$rules}
})
ConvertTo-Json -InputObject $rows -Depth 6 -Compress
'''
    exe = str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    env = {"SystemRoot": os.environ["SystemRoot"], "DIRECTOR_ISSUER_ROOT": str(root),
           "DIRECTOR_ISSUER_WORKER": "1" if worker else "0",
           "PSModulePath": str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
           "DIRECTOR_FULL_AUDIT": "1" if full else "0",
           "DIRECTOR_CONSUMED": json.dumps(list(consumed)), "DIRECTOR_RUNTIME_ROOTS": json.dumps(list(runtime_roots))}
    result = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                            capture_output=True, timeout=60)
    if result.returncode or len(result.stdout) > 32_000_000:
        raise IssuerError("Windows ACL inspection failed")
    validate_acl_rows(json.loads(result.stdout), broker_sid=broker_sid, worker_sid=worker_sid)


@dataclass(frozen=True)
class Config:
    root: Path
    role: str
    provider: str
    broker_sid: str
    worker_sid: str
    deployment: str
    mirror: Path
    repository: str
    git: str
    executable: str
    executable_digest: str
    entrypoint: str | None
    entrypoint_digest: str | None
    model: str
    runtime_roots: tuple = ()
    native_home: str = ""
    native_project: str = ""
    native_deny_hook: str = ""
    native_deny_hook_digest: str = ""

    @classmethod
    def load(cls, path: Path, expected_digest: str):
        if not DIGEST.fullmatch(expected_digest) or digest(path.read_bytes()) != expected_digest:
            raise IssuerError("installed configuration digest mismatch")
        value = read_json(path)
        cfg = cls(**{**value, "root": Path(value["root"]), "mirror": Path(value["mirror"])})
        if cfg.provider not in ROLE_PROVIDERS.get(cfg.role, ()) or cfg.worker_sid == cfg.broker_sid:
            raise IssuerError("fixed role or separate native worker identity missing")
        if not cfg.runtime_roots or any(not Path(p).is_absolute() for p in cfg.runtime_roots):
            raise IssuerError("complete protected runtime roots required")
        if cfg.provider == "gemini":
            # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
            if not cfg.native_project or not DIGEST.fullmatch(cfg.native_deny_hook_digest):
                raise IssuerError("AGY requires protected native home, project and deny-all hook")
            for native_path in (cfg.native_home, cfg.native_deny_hook):
                contained_native_path(native_path, cfg.runtime_roots)
            # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]
        for sid in (cfg.worker_sid, cfg.broker_sid):
            if re.fullmatch(r"S-1-5-21-(?:\d+-){3}\d+", sid) is None:
                raise IssuerError("dedicated local principal required")
        return cfg

    def identity(self, worker=False):
        if current_sid() != (self.worker_sid if worker else self.broker_sid):
            raise IssuerError("actual Windows principal does not match installed role")
        verify_boundary(self.root, broker_sid=self.broker_sid, worker_sid=self.worker_sid, worker=worker, runtime_roots=self.runtime_roots)

    def read(self, directory, name, limit=131072, *, worker=False):
        path = self.root / directory / name
        verify_boundary(self.root, broker_sid=self.broker_sid, worker_sid=self.worker_sid,
                        worker=worker, consumed=[{"path": str(path), "kind": directory}],
                        full=False)
        return read_json(path, limit)

    def command(self):
        paths = [(self.executable, self.executable_digest)]
        if self.entrypoint:
            paths.append((self.entrypoint, self.entrypoint_digest))
        if self.provider == "gemini":
            paths.append((self.native_deny_hook, self.native_deny_hook_digest))
        for name, expected in paths:
            p = Path(name)
            if not p.is_absolute() or p.is_symlink() or digest(p.read_bytes()) != expected:
                raise IssuerError("native executable or entrypoint pin mismatch")
        if Path(self.executable).suffix.lower() != ".exe":
            raise IssuerError("native Windows executable required")
        return [self.executable] + ([self.entrypoint] if self.entrypoint else [])


def claim(path: Path, identity: str):
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS claims (identity TEXT PRIMARY KEY, status TEXT NOT NULL)")
        try:
            db.execute("INSERT INTO claims VALUES (?, 'CLAIMED')", (identity,))
            db.commit()
        except sqlite3.IntegrityError as exc:
            raise IssuerError("already claimed; ambiguous work is never repeated") from exc


def _job(cfg: Config, job_id: str):
    if not DIGEST.fullmatch(job_id):
        raise IssuerError("invalid challenge ID")
    value = cfg.read("authorizations", job_id + ".json")
    if set(value) != {"base", "context"}:
        raise IssuerError("job can contain only protected base and context")
    context = context_from(value["context"])
    if context.challenge != job_id or context.repository != cfg.repository or cfg.provider in context.material_author_providers:
        raise IssuerError("challenge, repository or independent authorship mismatch")
    if not context.issued_at <= int(time.time()) < context.expires_at:
        raise IssuerError("challenge expired or not yet valid")
    return value, context


def issue(cfg: Config, job_id: str):
    """Broker starts one fixed worker request; a later collect never redispatches."""
    cfg.identity()
    job, context = _job(cfg, job_id)
    packet = prepare_packet(git=cfg.git, mirror=cfg.mirror, repository=cfg.repository,
                            base=job["base"], head=context.activation.candidate_commit)
    if digest(packet) != context.packet_digest or json.loads(packet)["tree"] != context.activation.candidate_tree:
        raise IssuerError("complete source packet does not match approved challenge")
    claim(cfg.root / "broker" / "claims.sqlite3", job_id)
    write_new(cfg.root / "requests" / (job_id + ".json"),
              dict(context=context.document(), packet=packet.decode(), run_nonce=uuid.uuid4().hex))


def initialize(cfg: Config):
    cfg.identity()
    path = cfg.root / "broker" / "private.key"
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        with path.open("xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return dict(role=cfg.role, provider=cfg.provider, principal=cfg.broker_sid,
                deployment=cfg.deployment, public_key=base64.b64encode(public).decode(),
                required_ci_checks=[], ci_workflow_ref="", ci_workflow_commit="")


def collect(cfg: Config, job_id: str):
    cfg.identity()
    _, context = _job(cfg, job_id)
    request = cfg.read("requests", job_id + ".json", MAX_PACKET * 2)
    result = cfg.read("responses", job_id + ".json", MAX_RESPONSE)
    # responses is writable only by the fixed worker SID and administrator;
    # unlike an imported session endpoint, the coordinator cannot write here.
    if set(result) != {"request_digest", "worker_sid", "capture", "exit_code"} or result["request_digest"] != digest(canonical(request)) or result["worker_sid"] != cfg.worker_sid:
        raise IssuerError("worker response provenance mismatch")
    if canonical(request["context"]) != canonical(context.document()):
        raise IssuerError("protected context changed")
    from .native_acceptance_capture import parse_capture
    raw = base64.b64decode(result["capture"], validate=True)
    session = parse_capture(raw, provider=cfg.provider, model=cfg.model,
                            request=request, exit_code=result["exit_code"])
    unsigned = dict(role=cfg.role, provider=cfg.provider, principal=cfg.broker_sid,
                    deployment=cfg.deployment, session_id=session,
                    transport_evidence_digest=digest(canonical(result)), verdict="ACCEPT",
                    ci_checks=[], ci_workflow_ref="", ci_workflow_commit="")
    key = Ed25519PrivateKey.from_private_bytes((cfg.root / "broker" / "private.key").read_bytes())
    signature = key.sign(DOMAIN + canonical(dict(version=VERSION, context=context.document(), receipt=unsigned)))
    receipt = {**unsigned, "signature": base64.b64encode(signature).decode()}
    output = dict(version=VERSION, context=context.document(), receipt=receipt)
    write_new(cfg.root / "public" / (job_id + ".json"), output)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "initialize", "issue", "work", "collect"])
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--config-sha256", required=True)
    p.add_argument("--job")
    p.add_argument("--base")
    p.add_argument("--head")
    a = p.parse_args()
    cfg = Config.load(a.config, a.config_sha256)
    if a.action == "prepare":
        packet = prepare_packet(git=cfg.git, mirror=cfg.mirror, repository=cfg.repository, base=a.base or "", head=a.head or "")
        print(canonical(dict(packet_digest=digest(packet), packet_bytes=len(packet), tree=json.loads(packet)["tree"])).decode())
    elif a.action == "initialize":
        print(canonical(initialize(cfg)).decode())
    elif a.action == "issue":
        issue(cfg, a.job or "")
    elif a.action == "work":
        from .native_acceptance_capture import work
        work(cfg, a.job or "")
    else:
        print(canonical(collect(cfg, a.job or "")).decode())


if __name__ == "__main__":
    main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:40 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]
