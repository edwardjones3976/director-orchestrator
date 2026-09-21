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
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | BEGIN]]
"""Protected Linux native broker with deny-all native tools and isolated checks.

This is an executable worker, not host enrollment. A separately accepted signed
qualification for this boot, runtime and two unprivileged identities is required.
The native account receives an explicit source prompt; its tools remain denied.
Only the supervisor writes proposed bytes and runs the exact granted argv checks
as a different identity which cannot read the native account or broker keys.
"""
from __future__ import annotations

import base64
import ctypes
from datetime import datetime, timezone
import hashlib
import math
import hmac
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
from contextlib import contextmanager

from .antigravity_team import TeamAuthorizationError, TeamGrant
from .antigravity_team_artifacts import decode_returned_files
from .antigravity_team_transport import parse_native_stream
from .entitlement_contracts import canonical_json_bytes, document_digest

DENIED = {"unassigned_source", "git_history", "coordinator_credentials", "broker_keys",
          "launcher_mutation", "production_network"}
DENY_TOOLS = {"read_file(*)", "write_file(*)", "read_url(*)", "execute_url(*)",
              "command(*)", "unsandboxed(*)", "mcp(*)"}
MAX_FRAME = 32 * 1024 * 1024


def _shared_deadline(own, carried):
    """The earlier of this worker's own limit and the caller's carried bound.

    A worker keeps its grant timer when it is called standalone, and narrows
    it - never widens or restarts it - when the primary lane carries its own
    remaining budget.
    """

    if own is None:
        return carried
    if carried is None:
        return own
    return min(own, carried)


def _remaining_seconds(deadline, what):
    """Exactly what is left, never rounded up into a new allowance.

    `max(1, int(remaining))` would hand a process a fresh second after the
    budget had effectively run out, so a non-positive remainder is refused
    here instead. The value returned is fractional on purpose.
    """

    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TeamAuthorizationError(what + " deadline exhausted")
    return remaining


def _require_shared_deadline(deadline, what):
    """Refuse a bound that is already spent before starting this stage."""

    if deadline is not None and deadline - time.monotonic() <= 0:
        raise TeamAuthorizationError(what + " deadline expired before it started")
    return deadline


# How a primary bound crosses hosts. Not as a wall-clock instant, which only
# means something if the two hosts' clocks agree and needs a stated premise
# saying by how much; and not as a duration re-anchored on arrival, which
# hands the transit back. Instead the remote server takes an instant on its
# own monotonic clock and then sends an authenticated anchor frame; the
# director samples what is left of its fixed bound only after that frame has
# arrived, and sends that. The server's instant therefore precedes the
# director's sample in real time, so instant plus remaining can only be
# early - by at least the transit in each direction and any pause on either
# side - never late. No clock is compared with another, no skew is assumed,
# and nothing is renewed on receipt: the exchange frame names the same anchor
# and carries no time value at all.
PRIMARY_BOUND_MODE = "director-team-remote-anchor/v1"


def _grant_timeout_ceiling():
    """The task grant's own `timeout_seconds` ceiling, read from the model.

    No director can hold a remote bound longer than its grant, so no
    authenticated frame can legitimately carry more remaining than this.
    It is read from `TeamGrant` rather than restated here, so the two cannot
    drift apart; a model that no longer declares it fails this import closed.
    """

    declared = [getattr(item, "le", None) for item in TeamGrant.model_fields["timeout_seconds"].metadata]
    declared = [value for value in declared if value is not None]
    if len(declared) != 1:
        raise TeamAuthorizationError("task grant timeout ceiling is not declared")
    return float(declared[0])


MAX_REMAINING_SECONDS = _grant_timeout_ceiling()


def anchor_token(frame):
    """The token an authenticated anchor frame issued; nothing else is one.

    This is where the director refuses a remote server that does not speak
    the bound protocol - before preparation, and long before any source.
    """

    if (not isinstance(frame, dict) or set(frame) != {"mode", "anchor"}
            or frame["mode"] != PRIMARY_BOUND_MODE
            or not isinstance(frame["anchor"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", frame["anchor"])):
        raise TeamAuthorizationError("remote worker does not support the primary bound protocol")
    return frame["anchor"]


def anchored_primary_bound(request, *, anchored, issued):
    """The bound this attempt runs under, from the authenticated prepare frame.

    `anchored` is the instant this server took before sending its anchor and
    `issued` the token it sent with it. A frame that names that token was
    sent after the token arrived, so the remaining budget it carries was
    sampled after `anchored`: the deadline is `anchored + remaining`, never
    the receipt time plus remaining, and transit or a pause on either side is
    spent rather than returned.

    An explicit `None` is deliberate standalone use and keeps the grant timer
    alone. Anything else - the key absent, an older frame carrying
    `lifetime_seconds` or `primary_cutoff`, a bound naming some other anchor,
    a non-finite, non-positive or already spent value, or one above the task
    grant's own timeout ceiling - is refused rather than treated as unbounded.
    Nothing is clamped: a value this server cannot bind exactly is malformed
    input, and so is an anchor or result that is not a finite instant.
    """

    if (not isinstance(request, dict)
            or set(request) != {"binding", "grant_digest", "attempt_digest", "primary_bound"}):
        raise TeamAuthorizationError("unsupported worker preparation protocol")
    carried = request["primary_bound"]
    if carried is None:
        return None
    if (not isinstance(carried, dict) or set(carried) != {"mode", "anchor", "remaining_seconds"}
            or carried["mode"] != PRIMARY_BOUND_MODE or carried["anchor"] != issued):
        raise TeamAuthorizationError("primary bound is not anchored to this worker session")
    remaining = carried["remaining_seconds"]
    if (not isinstance(remaining, (int, float)) or isinstance(remaining, bool)
            or not math.isfinite(remaining) or remaining <= 0):
        raise TeamAuthorizationError("primary bound remaining budget is invalid")
    if remaining > MAX_REMAINING_SECONDS:
        raise TeamAuthorizationError("primary bound remaining budget exceeds the grant ceiling")
    if (not isinstance(anchored, (int, float)) or isinstance(anchored, bool)
            or not math.isfinite(anchored) or not math.isfinite(anchored + float(remaining))):
        raise TeamAuthorizationError("primary bound anchor is not a finite instant")
    return _require_shared_deadline(anchored + float(remaining), "primary bound")


def retained_primary_bound(request, bound, *, issued):
    """The exchange frame must name the bound preparation established.

    It carries no time value, so there is nothing here to renew: the bound is
    the one retained since preparation. A frame naming a different mode or
    anchor, claiming standalone use after a bound was prepared, or carrying a
    bound after standalone preparation, is refused before any of the exchange
    is used.
    """

    if (not isinstance(request, dict)
            or set(request) != {"grant", "work_packet", "source", "primary_bound"}):
        raise TeamAuthorizationError("unsupported worker exchange protocol")
    carried = request["primary_bound"]
    if bound is None:
        if carried is not None:
            raise TeamAuthorizationError("exchange carries a primary bound preparation did not have")
        return None
    if (not isinstance(carried, dict) or set(carried) != {"mode", "anchor"}
            or carried["mode"] != PRIMARY_BOUND_MODE or carried["anchor"] != issued):
        raise TeamAuthorizationError("exchange does not name the prepared primary bound")
    return _require_shared_deadline(bound, "remote exchange")


# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
def deny_all_settings_document():
    """The one accepted native settings document; nothing else is a policy."""
    return {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS)}}


def deny_all_hooks_document(hook_executable):
    return {"director-team-deny-all": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": hook_executable, "timeout": 5}]}]}}


def is_deny_all_settings(settings):
    """Exact closed shape: a subset match would admit surplus policy keys.

    Documented Antigravity settings also carry command-bearing keys such as
    statusLine, so anything beyond the deny-all permissions block is refused
    here rather than accepted as harmless surplus.
    """
    if not isinstance(settings, dict) or set(settings) != {"permissions"}:
        return False
    permissions = settings["permissions"]
    if not isinstance(permissions, dict) or set(permissions) != {"allow", "deny"}:
        return False
    deny = permissions["deny"]
    if permissions["allow"] != [] or not isinstance(deny, list) or not all(isinstance(t, str) for t in deny):
        return False
    return len(deny) == len(set(deny)) and set(deny) == DENY_TOOLS
# [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]


def sign(secret, evidence):
    return {"evidence": evidence, "signature": hmac.new(secret,
        canonical_json_bytes(evidence), hashlib.sha256).hexdigest()}


def authenticate(secret, value):
    if not isinstance(value, dict) or set(value) != {"evidence", "signature"}:
        raise TeamAuthorizationError("worker envelope invalid")
    expected = sign(secret, value["evidence"])["signature"]
    if not isinstance(value["signature"], str) or not hmac.compare_digest(expected, value["signature"]):
        raise TeamAuthorizationError("worker envelope authentication failed")
    return value["evidence"]


class FrameChannel:
    """Ordered mutually authenticated frames on a confidential pinned transport."""
    def __init__(self, reader, writer, secret, challenge, *, sender):
        if len(secret) < 32 or not re.fullmatch(r"[0-9a-f]{64}", challenge):
            raise TeamAuthorizationError("worker channel identity invalid")
        self.reader, self.writer, self.secret = reader, writer, secret
        self.challenge, self.sender = challenge, sender
        self.sent = self.received = 0

    def send(self, kind, payload):
        frame = sign(self.secret, {"protocol": "director-team-worker/v1", "sender": self.sender,
            "sequence": self.sent, "challenge": self.challenge, "kind": kind, "payload": payload})
        raw = canonical_json_bytes(frame)
        if len(raw) > MAX_FRAME:
            raise TeamAuthorizationError("worker frame exceeds limit")
        self.writer.write(raw + b"\n")
        self.writer.flush()
        self.sent += 1

    def receive(self, kind):
        raw = self.reader.readline(MAX_FRAME + 2)
        if not raw.endswith(b"\n") or len(raw) > MAX_FRAME + 1:
            raise TeamAuthorizationError("worker frame truncated or excessive")
        frame = authenticate(self.secret, json.loads(raw))
        if (set(frame) != {"protocol", "sender", "sequence", "challenge", "kind", "payload"}
            or frame["protocol"] != "director-team-worker/v1" or frame["sender"] == self.sender
            or frame["sender"] not in {"director", "worker"} or frame["sequence"] != self.received
            or frame["challenge"] != self.challenge or frame["kind"] != kind):
            raise TeamAuthorizationError("worker frame replay, order or identity mismatch")
        self.received += 1
        return frame["payload"]


def protected_bytes(path):
    path = Path(path)
    for parent in (path, *path.parents):
        metadata = parent.lstat()
        if parent.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise TeamAuthorizationError("broker runtime path is not root protected")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise TeamAuthorizationError("broker file alias")
    return path.read_bytes()


def protected_directory(path):
    path=Path(path)
    for item in (path,*path.parents):
        metadata=item.lstat()
        if item.is_symlink() or metadata.st_uid!=0 or metadata.st_mode&0o022 or not item.is_dir():
            raise TeamAuthorizationError("worker supervisor directory is not root protected")
    return path


def native_state_manifest(home):
    import stat
    result={}
    total=0
    for path in Path(home).rglob("*"):
        metadata=path.lstat()
        if path.is_symlink() or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise TeamAuthorizationError("native state contains an unqualified alias")
        if path.is_file():
            if metadata.st_nlink!=1:
                raise TeamAuthorizationError("native state contains a hardlink")
            total+=metadata.st_size
            if total>32*1024*1024 or len(result)>=10000:
                raise TeamAuthorizationError("native state exceeds the bounded capture")
            result[path.relative_to(home).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _limits(timeout):
    import resource
    # RLIMIT_CPU is whole seconds and is a coarse backstop for consumed CPU,
    # not the wall-clock bound: the caller's own timeout, which may be
    # fractional, is what actually ends the process on time.
    cpu_seconds = int(timeout) + 1
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0):
        raise RuntimeError("cannot enable no-new-privileges")


def require_cgroup_root(path):
    root = protected_directory(path)
    # Kernel-created control files distinguish a cgroup v2 hierarchy from an
    # ordinary directory. The mount itself must be cgroup2, not a fixture tree.
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    if not any(" - cgroup2 " in row and
               (str(root) == row.split()[4] or str(root).startswith(row.split()[4] + "/"))
               for row in mounts):
        raise TeamAuthorizationError("protected cgroup v2 mount required")
    for name in ("cgroup.procs", "cgroup.controllers", "cgroup.events"):
        protected_bytes(root / name)
    return root


def run_unprivileged(argv, *, uid, gid, cwd, home, timeout=None, cgroup_root,
                     input_bytes=b"", deadline=None):
    """Join a protected cgroup before dropping identity or executing any code.

    A setsid/double-fork descendant stays in this non-delegated kernel group.
    Always kill the entire group and verify it is empty, including on success.

    `deadline` is this host's own absolute bound for the whole call. It is
    checked after the cgroup and file setup, again immediately before the
    process is launched, and the wait is computed from what is left *then* -
    setup no longer happens outside the budget and then hands the child the
    original duration. A caller may still pass a plain `timeout` for
    standalone use; supplying both is what the primary lane does, and the
    earlier of the two wins.

    Two limits are deliberately not covered by this bound and are not claimed
    to be: `preexec_fn` runs between fork and exec and cannot be interrupted
    once `Popen` has been entered, and the cleanup below spends its own five
    second process wait plus a further five second cgroup drain.
    """
    if os.name != "posix" or sys.platform != "linux" or os.geteuid() != 0 or uid <= 0 or gid <= 0:
        raise TeamAuthorizationError("qualified Linux broker identity required")
    if deadline is None and timeout is None:
        raise TeamAuthorizationError("bounded native execution requires a deadline")
    if timeout is not None:
        deadline = _shared_deadline(time.monotonic() + timeout, deadline)
    _remaining_seconds(deadline, "native process setup")
    root = require_cgroup_root(cgroup_root)
    group = root / ("director-" + os.urandom(16).hex())
    group.mkdir(mode=0o755)
    if not (group / "cgroup.kill").is_file():
        group.rmdir()
        raise TeamAuthorizationError("kernel cgroup.kill support required")
    def enter():
        (group / "cgroup.procs").write_text(str(os.getpid()))
        _limits(_remaining_seconds(deadline, "native execution"))
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = None
        try:
            # After every setup step above, immediately before launching: work
            # that has already consumed the budget never starts a child.
            _remaining_seconds(deadline, "native execution")
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=stdout, stderr=stderr, umask=0o077,
                start_new_session=True, preexec_fn=enter)
            # Recomputed here, not the duration the caller held earlier.
            process.communicate(input=input_bytes,
                                timeout=_remaining_seconds(deadline, "native execution"))
        finally:
            (group / "cgroup.kill").write_text("1")
            if process is not None:
                process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while "populated 1" in (group / "cgroup.events").read_text():
                if time.monotonic() >= deadline:
                    raise TeamAuthorizationError("kernel execution group did not empty")
                time.sleep(.01)
            group.rmdir()
        stdout.seek(0)
        stderr.seek(0)
        return process.returncode, stdout.read(8 * 1024 * 1024 + 1), stderr.read(8 * 1024 * 1024 + 1)


class LinuxNativeTeamWorker:
    def __init__(self, config, *, owner_secret, issuer_secret, worker_secret):
        self.config = config
        self.owner_secret, self.issuer_secret, self.secret = owner_secret, issuer_secret, worker_secret
        if min(map(len, (owner_secret, issuer_secret, worker_secret))) < 32 or len({owner_secret, issuer_secret, worker_secret}) != 3:
            raise TeamAuthorizationError("distinct protected authorities required")
        self.prepared = self.prepared_deadline = None

    def qualify(self, grant):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise TeamAuthorizationError("native team worker requires the enrolled Linux broker")
        require_cgroup_root(self.config["cgroup_root"])
        qualification = authenticate(self.issuer_secret, json.loads(protected_bytes(self.config["qualification"])))
        now = datetime.now(timezone.utc)
        if (qualification.get("schema_version") != "director-team-linux-qualification/v1"
            or datetime.fromisoformat(qualification["expires_at"]) <= now
            or qualification["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            or qualification["network_namespace"] != os.readlink("/proc/self/ns/net")
            or set(qualification["denied_access"]) != DENIED
            or qualification["authentication_mode"] != "native-account"):
            raise TeamAuthorizationError("host qualification absent, stale or foreign")
        for key in ("installation_id", "executable_digest", "runtime_digest", "adapter_digest",
                    "binding_digest", "native_account_id", "native_project_id"):
            if qualification[key] != getattr(grant, key):
                raise TeamAuthorizationError("host qualification binding mismatch")
        native_uid, check_uid = self.config["native_uid"], self.config["check_uid"]
        if (not isinstance(native_uid, int) or not isinstance(check_uid, int)
            or min(native_uid, check_uid) <= 0 or native_uid == check_uid
            or qualification["native_uid"] != native_uid or qualification["check_uid"] != check_uid):
            raise TeamAuthorizationError("native and check identities must be separated")
        if document_digest({k: v for k, v in self.config.items() if k != "qualification"}) != qualification["worker_config_digest"]:
            raise TeamAuthorizationError("worker configuration changed after qualification")
        for key in ("claims_root","workspace_root","native_home"):
            protected_directory(self.config[key])
        for path, digest in qualification["protected_files"].items():
            if hashlib.sha256(protected_bytes(path)).hexdigest() != digest:
                raise TeamAuthorizationError("qualified runtime or policy changed")
        identity_path=self.config["native_identity_evidence_file"]
        if identity_path not in qualification["protected_files"]:
            raise TeamAuthorizationError("independent native identity readback is not qualified")
        identity=authenticate(self.issuer_secret,json.loads(protected_bytes(identity_path)))
        if (identity.get("schema_version")!="director-native-identity-readback/v1"
            or identity.get("source")!="native-authenticated-profile-and-project-readback"
            or identity.get("grant_digest")!=qualification["grant_digest"]
            or identity.get("observed_native_account_id")!=grant.native_account_id
            or identity.get("observed_native_project_id")!=grant.native_project_id
            or identity.get("runtime_digest")!=grant.runtime_digest
            or datetime.fromisoformat(identity["expires_at"])<=now):
            raise TeamAuthorizationError("native account/project readback is absent, expired or foreign")
        if hashlib.sha256(protected_bytes(self.config["executable"])).hexdigest() != grant.executable_digest:
            raise TeamAuthorizationError("native executable changed")
        settings_path = str(Path(self.config["native_home"]) / ".gemini/antigravity-cli/settings.json")
        if settings_path not in qualification["protected_files"]:
            raise TeamAuthorizationError("native permission policy is not qualified")
        # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
        if not is_deny_all_settings(json.loads(protected_bytes(settings_path))):
            raise TeamAuthorizationError("native tools must be denied by protected policy")
        # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]
        hooks_path=str(Path(self.config["native_home"])/".gemini/antigravity-cli/hooks.json")
        hook=self.config["deny_all_hook_executable"]
        if hooks_path not in qualification["protected_files"] or hook not in qualification["protected_files"]:
            raise TeamAuthorizationError("wildcard native tool guard is not qualified")
        expected_hook=deny_all_hooks_document(hook)
        if json.loads(protected_bytes(hooks_path))!=expected_hook:
            raise TeamAuthorizationError("all native tool categories require the protected wildcard guard")
        # Issuer evidence must cover real negative access tests for both exact
        # identities, native account mode, process termination and network policy.
        # A registry entry or configured flag cannot issue this qualification.
        return qualification

    def prepare(self, *, grant, grant_digest, attempt_digest, challenge, deadline=None):
        _require_shared_deadline(deadline, "worker preparation")
        grant.verify(self.owner_secret, datetime.now(timezone.utc))
        if grant.digest != grant_digest:
            raise TeamAuthorizationError("worker preparation grant mismatch")
        return self.prepare_binding(binding={key:getattr(grant,key) for key in (
            "installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","native_project_id","workspace_id")},
            grant_digest=grant_digest,attempt_digest=attempt_digest,challenge=challenge,
            deadline=deadline)

    def prepare_binding(self, *, binding, grant_digest, attempt_digest, challenge, deadline=None):
        """Qualify this host for one exact grant, inside the carried bound.

        Qualification, the native state scan and the durable claim below are
        real work that can outlast the budget, so the bound is re-checked
        between them and once more after the claim is durable: preparation
        that has already run out writes no claim, and one that runs out while
        writing it acknowledges nothing.
        """

        if (set(binding)!={"installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","native_project_id","workspace_id"}
            or not re.fullmatch(r"[0-9a-f]{64}",grant_digest)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}",attempt_digest)):
            raise TeamAuthorizationError("public worker binding contract mismatch")
        _remaining_seconds(deadline, "worker preparation")
        qualification = self.qualify(SimpleNamespace(**binding))
        if qualification.get("grant_digest")!=grant_digest:
            raise TeamAuthorizationError("host qualification is not scoped to this exact task grant")
        _remaining_seconds(deadline, "worker preparation")
        state=native_state_manifest(self.config["native_home"])
        if state!=qualification.get("native_state_manifest"):
            raise TeamAuthorizationError("native account state changed or contains prior task history")
        # A general host receipt is insufficient: cached native state must be
        # re-qualified for each task. Consume the exact task-scoped receipt
        # before acknowledging readiness to receive private source.
        # The last check before any durable side effect of preparation.
        _remaining_seconds(deadline, "worker preparation")
        qualification_claim=Path(self.config["claims_root"])/("qualification-"+document_digest(qualification))
        with qualification_claim.open("xb") as handle:
            handle.write(canonical_json_bytes({"grant_digest":grant_digest,"attempt_digest":attempt_digest,"challenge":challenge}))
            handle.flush()
            os.fsync(handle.fileno())
        # The claim is durable now whether or not the budget survived writing
        # it, and it is deliberately not removed: a claim that exists is a
        # receipt that was consumed, and removing it would make the same
        # receipt spendable again. Readiness, though, is only acknowledged
        # inside the bound - a preparation that ran out while persisting stays
        # unprepared, so a later exchange finds no preparation and the
        # consumed receipt can never be claimed a second time.
        _remaining_seconds(deadline, "worker preparation")
        self.prepared = (grant_digest, attempt_digest, challenge)
        # The bound this preparation was acknowledged under, bound to the
        # same attempt and challenge: the exchange can narrow it, never renew it.
        self.prepared_deadline = deadline
        self.qualification = qualification
        return sign(self.secret, dict(grant_digest=grant_digest, attempt_digest=attempt_digest,
            challenge=challenge, binding_digest=binding["binding_digest"], workspace_id=binding["workspace_id"],
            containment_evidence_digest=document_digest(qualification), expires_at=qualification["expires_at"],
            denied_access=sorted(DENIED), authentication_mode="native-account"))

    accepts_primary_deadline = True

    def exchange(self, *, grant, work_packet, source, challenge, before_execute,
                 before_local_command, deadline=None):
        self.qualify(grant)
        with self.account_lock():
            return self._exchange(grant=grant,work_packet=work_packet,source=source,challenge=challenge,
                before_execute=before_execute,before_local_command=before_local_command,
                deadline=deadline)

    @contextmanager
    def account_lock(self):
        import fcntl
        path=Path(self.config["claims_root"])/("native-"+str(self.config["native_uid"])+".lock")
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield

    def _exchange(self, *, grant, work_packet, source, challenge, before_execute,
                  before_local_command, deadline=None):
        if self.prepared is None or self.prepared != (grant.digest, self.prepared[1], challenge):
            raise TeamAuthorizationError("worker exchange has no preparation")
        attempt = self.prepared[1]
        # Never more than preparation was acknowledged under, whatever the
        # caller carries now.
        deadline = _shared_deadline(self.prepared_deadline, deadline)
        self.prepared = self.prepared_deadline = None
        self.qualify(grant)
        grant.verify(self.owner_secret, datetime.now(timezone.utc))
        if document_digest(work_packet) != grant.packet_digest or set(source) != {b.path for b in grant.source}:
            raise TeamAuthorizationError("worker source or packet mismatch")
        if sum(map(len, source.values())) > 16 * 1024 * 1024:
            raise TeamAuthorizationError("worker source exceeds bound")
        for blob in grant.source:
            if hashlib.sha256(source[blob.path]).hexdigest() != blob.sha256:
                raise TeamAuthorizationError("worker source bytes differ from grant")
        # Atomic create-once attempt survives crashes; no ambiguous work is retried.
        claim = Path(self.config["claims_root"]) / attempt.removeprefix("sha256:")
        with claim.open("xb") as handle:
            handle.write(canonical_json_bytes({"grant": grant.digest, "challenge": challenge}))
            handle.flush()
            os.fsync(handle.fileno())
        # The grant's own timer, narrowed by whatever the primary lane has
        # left. A caller that carries no bound is unchanged; a bound that has
        # already passed never starts native execution.
        deadline = _shared_deadline(time.monotonic() + grant.timeout_seconds, deadline)
        with tempfile.TemporaryDirectory(dir=self.config["workspace_root"], prefix="team-") as temporary:
            workspace = Path(temporary)
            os.chmod(workspace, 0o755)
            native_cwd = workspace / "native"
            native_cwd.mkdir(mode=0o555)
            checks_cwd = workspace / "checks"
            checks_cwd.mkdir(mode=0o700)
            os.chown(checks_cwd, self.config["check_uid"], self.config["check_gid"])
            prompt = {"instruction": "Return JSON with packet (the required result packet) and returned_files (path, sha256, content_base64). Use no tools. Proposed edits are applied and checked by the independent supervisor. Do not claim checks; leave packet.checks empty.",
                "work_packet": work_packet, "source": {p: base64.b64encode(b).decode() for p, b in source.items()},
                "writable_paths": grant.writable_paths}
            # Serialized before the boundary and before the bound is spent on
            # it: the helper below takes the absolute deadline, so nothing
            # between here and the launch is charged to a stale duration.
            prompt_bytes = canonical_json_bytes(
                {"event": "user", "message": {"content": canonical_json_bytes(prompt).decode()}}) + b"\n"
            before_execute()
            code, stream, diagnostics = run_unprivileged([self.config["executable"],
                "--input-format", "stream-json", "--output-format", "stream-json", "--model", grant.model,
                "--project",grant.native_project_id],
                uid=self.config["native_uid"], gid=self.config["native_gid"], cwd=native_cwd,
                home=self.config["native_home"], cgroup_root=self.config["cgroup_root"],
                deadline=deadline, input_bytes=prompt_bytes)
            if code or len(stream) > 8 * 1024 * 1024:
                raise TeamAuthorizationError("native execution failed or exceeded output limit")
            proposal, session, usage = parse_native_stream(stream, grant=grant, expected_cwd=str(native_cwd))
            for event in map(json.loads, stream.splitlines()):
                step = event.get("step_update", {})
                if step.get("step_type") == "tool" or step.get("tool_info"):
                    raise TeamAuthorizationError("native tools are not granted in this worker contract")
            if set(proposal) != {"packet", "returned_files"} or proposal["packet"].get("checks"):
                raise TeamAuthorizationError("native proposal contract mismatch")
            changes = decode_returned_files(grant, proposal["returned_files"])
            for path, data in {**source, **changes}.items():
                target = checks_cwd / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                os.chown(target, self.config["check_uid"], self.config["check_gid"])
            commands, checks = [], []
            for argv in grant.commands:
                _remaining_seconds(deadline, "worker total")
                self.qualify(grant)
                executable=self.config["command_binaries"].get(argv[0])
                if not executable or executable not in self.qualification["protected_files"]:
                    raise TeamAuthorizationError("local command executable is not qualified")
                before_local_command(argv)
                code, output, errors = run_unprivileged([executable,*argv[1:]], uid=self.config["check_uid"],
                    gid=self.config["check_gid"], cwd=checks_cwd, home=checks_cwd, cgroup_root=self.config["cgroup_root"],
                    deadline=deadline)
                commands.append(list(argv))
                checks.append({"command": json.dumps(list(argv)), "result": "output-sha256:" + hashlib.sha256(output + errors).hexdigest(), "exit_code": code})
            # Checks cannot smuggle modified source back. Capture the proposal's
            # exact bytes; check modifications are rejected, including extras.
            expected = {**source, **changes}
            entries=list(checks_cwd.rglob("*"))
            import stat
            if any(p.is_symlink() or not (stat.S_ISREG(p.lstat().st_mode) or stat.S_ISDIR(p.lstat().st_mode)) for p in entries):
                raise TeamAuthorizationError("check workspace contains an alias or special file")
            actual = {p.relative_to(checks_cwd).as_posix(): p for p in entries if not p.is_dir()}
            if set(actual) != set(expected) or any(p.is_symlink() or p.stat().st_nlink != 1 or p.read_bytes() != expected[name] for name,p in actual.items()):
                raise TeamAuthorizationError("checks changed the immutable candidate")
            self.qualify(grant)
            evidence = {key: getattr(grant, key) for key in ("installation_id", "executable_digest", "runtime_digest",
                "adapter_digest", "binding_digest", "native_account_id", "workspace_id")}
            evidence.update(grant_digest=grant.digest, attempt_digest=attempt, packet_digest=grant.packet_digest,
                completed_at=datetime.now(timezone.utc).isoformat(),
                native_state_manifest_after=native_state_manifest(self.config["native_home"]),
                native_project_id=grant.native_project_id,
                native_identity_observations={"requested_project_id":grant.native_project_id,
                    "observed_project_id":None,"observed_account_id":None},
                authentication_mode="native-account", cwd=str(native_cwd), challenge=challenge,
                source_manifest_digest=document_digest({"source": [b.model_dump() for b in grant.source]}),
                containment_evidence_digest=document_digest(self.qualification), native_stream=stream.decode(), exit_code=0,
                changed_files=[{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p,b in changes.items()],
                returned_files=proposal["returned_files"], executed_commands=commands, checks=checks)
            return sign(self.secret, evidence)


class PinnedSshTeamWorker:
    """One SSH process per funded attempt, pinned host keys, no recovery replay."""
    def __init__(self, *, ssh: Path, ssh_sha256: str, host: str, user: str,
                 identity_file: Path, known_hosts: Path, worker_command: str,
                 worker_secret: bytes, grant: TeamGrant):
        if (not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user)
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", worker_command)):
            raise TeamAuthorizationError("invalid pinned worker endpoint")
        self.ssh, self.ssh_sha256 = ssh.resolve(strict=True), ssh_sha256
        self.argv = [str(self.ssh), "-T", "-F", os.devnull, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "IdentitiesOnly=yes", "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no",
            "-o", "UserKnownHostsFile="+str(known_hosts.resolve(strict=True)),
            "-i", str(identity_file.resolve(strict=True)), user+"@"+host, worker_command]
        self.secret, self.grant = worker_secret, grant
        self.process = self.timer = self.anchor = None

    def close(self):
        if self.timer:
            self.timer.cancel()
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            self.process.stdin.close()
            self.process.stdout.close()

    accepts_primary_deadline = True

    def prepare(self, *, grant_digest, attempt_digest, challenge, deadline=None):
        if self.process is not None or grant_digest != self.grant.digest:
            raise TeamAuthorizationError("worker client is single-attempt")
        _require_shared_deadline(deadline, "worker preparation")
        if hashlib.sha256(self.ssh.read_bytes()).hexdigest() != self.ssh_sha256:
            raise TeamAuthorizationError("SSH executable changed")
        self.process = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={"SystemRoot": os.environ.get("SystemRoot", ""), "PATH": str(self.ssh.parent)})
        # One remote lifetime for the whole exchange, never one per stage: the
        # grant's own limit narrowed by what the primary lane has left.
        self.deadline = _shared_deadline(time.monotonic() + self.grant.timeout_seconds, deadline)
        self.timer = threading.Timer(max(0.001, self.deadline - time.monotonic()), self.process.kill)
        self.timer.daemon = True
        self.timer.start()
        self.channel = FrameChannel(self.process.stdout, self.process.stdin, self.secret, challenge, sender="director")
        try:
            self.process.stdin.write(challenge.encode()+b"\n")
            self.process.stdin.flush()
            # The remote server speaks first. Its authenticated anchor frame,
            # bound to this challenge, proves it enforces the bound protocol
            # before preparation - let alone source - is sent, and marks an
            # instant on its own clock that precedes the sample below. A
            # server that sends anything else is refused at the frame; one
            # that sends nothing is ended by the local timer, having received
            # nothing but the challenge.
            self.anchor=anchor_token(self.channel.receive("anchor"))
            # Sampled only now, after the anchor arrived, and sent as it is:
            # what is left of this side's one fixed bound. A pause before this
            # line shrinks it and nothing on the remote side can grow it.
            # Killing the local SSH process bounds this side only; this is
            # what bounds the worker's own execution there.
            remaining=self.deadline-time.monotonic()
            if remaining<=0:
                raise TeamAuthorizationError("remote preparation deadline expired before it started")
            binding={key:getattr(self.grant,key) for key in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","native_project_id","workspace_id")}
            self.channel.send("prepare", {"binding":binding,"grant_digest":grant_digest,
                "attempt_digest":attempt_digest,"primary_bound":{"mode":PRIMARY_BOUND_MODE,
                "anchor":self.anchor,"remaining_seconds":remaining}})
            return self.channel.receive("prepared")
        except BaseException:
            self.close()
            raise

    def exchange(self, *, grant, work_packet, source, challenge, before_execute,
                 before_local_command, deadline=None):
        try:
            if grant != self.grant or self.anchor is None or challenge != self.channel.challenge:
                raise TeamAuthorizationError("SSH exchange changed grant")
            # The remote exchange continues under the lifetime preparation
            # already started; it never begins a second full-grant timer.
            _require_shared_deadline(
                _shared_deadline(getattr(self, "deadline", None), deadline), "remote exchange")
            self.channel.send("exchange", {"grant":grant.model_dump(mode="json"),"work_packet": work_packet,
                "source": {p: base64.b64encode(b).decode() for p,b in source.items()},
                # The same anchor preparation was bound to, and no time value:
                # the receiver keeps what it retained, so nothing here can
                # hand back what preparation spent.
                "primary_bound":{"mode":PRIMARY_BOUND_MODE,"anchor":self.anchor}})
            self.channel.receive("before_execute")
            before_execute()
            self.channel.send("authorized", {"stage": "execution"})
            for command in grant.commands:
                actual = self.channel.receive("before_command")
                if actual != list(command):
                    raise TeamAuthorizationError("remote command differs from grant order")
                before_local_command(command)
                self.channel.send("authorized", {"stage": "command"})
            result = self.channel.receive("capture")
            self.process.stdin.close()
            if self.process.wait(timeout=5):
                raise TeamAuthorizationError("worker did not finish cleanly")
            return result
        finally:
            self.close()


def serve(config_path):
    config = json.loads(protected_bytes(config_path))
    secret_paths = config.pop("secret_files")
    secrets = {name: protected_bytes(path) for name,path in secret_paths.items()}
    worker = LinuxNativeTeamWorker(config, **secrets)
    challenge = sys.stdin.buffer.readline(66).decode().strip()
    channel = FrameChannel(sys.stdin.buffer, sys.stdout.buffer, worker.secret, challenge, sender="worker")
    # This host's own instant, taken before the first frame leaves. The
    # director samples its remaining budget only after this frame arrives, so
    # the bound derived below can only be early, never late.
    anchored = time.monotonic()
    issued = os.urandom(16).hex()
    channel.send("anchor", {"mode": PRIMARY_BOUND_MODE, "anchor": issued})
    request = channel.receive("prepare")
    remote_deadline = anchored_primary_bound(request, anchored=anchored, issued=issued)
    prepared = worker.prepare_binding(binding=request["binding"],grant_digest=request["grant_digest"],
        attempt_digest=request["attempt_digest"],challenge=challenge,deadline=remote_deadline)
    # Readiness is acknowledged inside the bound or not at all. The claim
    # preparation consumed stays consumed either way, and no source follows.
    _remaining_seconds(remote_deadline, "worker readiness")
    channel.send("prepared", prepared)
    request = channel.receive("exchange")
    # The bound retained since preparation; the frame may only name it.
    remote_deadline = retained_primary_bound(request, remote_deadline, issued=issued)
    grant = TeamGrant.model_validate_json(json.dumps(request["grant"]))
    def boundary(stage, argv=None):
        channel.send("before_execute" if stage == "execution" else "before_command", {} if argv is None else list(argv))
        if channel.receive("authorized") != {"stage": stage}:
            raise TeamAuthorizationError("director boundary authorization missing")
    capture = worker.exchange(grant=grant, work_packet=request["work_packet"],
        source={p: base64.b64decode(value, validate=True) for p,value in request["source"].items()},
        challenge=challenge, before_execute=lambda: boundary("execution"),
        before_local_command=lambda argv: boundary("command", argv),
        deadline=remote_deadline)
    channel.send("capture", capture)


if __name__ == "__main__":
    if sys.argv[1:]==["--deny-all-tools"]:
        print('{"decision":"deny"}')
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit("exact protected worker configuration path required")
    serve(sys.argv[1])
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | END]]
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
