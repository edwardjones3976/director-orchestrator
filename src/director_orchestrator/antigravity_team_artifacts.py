# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | BEGIN]]
"""Durable returned bytes and immutable Git candidates; never a checkout or merge."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import contextvars
import time
from datetime import datetime, timezone

from .antigravity_team import TeamAuthorizationError, safe_path
from .entitlement_contracts import canonical_json_bytes, document_digest


def decode_returned_files(grant, entries):
    if not isinstance(entries, list) or len(entries) > len(grant.writable_paths):
        raise TeamAuthorizationError("invalid returned-file inventory")
    files = {}
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "content_base64"}:
            raise TeamAuthorizationError("returned source contract mismatch")
        path = safe_path(entry["path"])
        if path not in grant.writable_paths or path.casefold() in {p.casefold() for p in files}:
            raise TeamAuthorizationError("returned path is duplicate or ungranted")
        try:
            data = base64.b64decode(entry["content_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise TeamAuthorizationError("invalid source encoding") from exc
        total += len(data)
        if total > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise TeamAuthorizationError("returned bytes differ from capture or exceed bound")
        files[path] = data
    if grant.operation == "review" and files:
        raise TeamAuthorizationError("review cannot return modified source")
    return files


class TeamArtifactStore:
    """Create-once content-addressed files in an operator-protected directory.

    Startup enrollment must protect this directory from the native worker. The
    store itself verifies exact bytes on every read and never overwrites a blob.
    """
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir() or root.is_symlink():
            raise TeamAuthorizationError("protected artifact directory required")

    def put(self, document):
        raw = canonical_json_bytes(document)
        if len(raw) > 32 * 1024 * 1024:
            raise TeamAuthorizationError("capture exceeds durable bound")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / (digest + ".json")
        try:
            with path.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if path.read_bytes() != raw:
                raise TeamAuthorizationError("durable capture collision")
        return digest

    def get(self, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise TeamAuthorizationError("invalid artifact digest")
        path = self.root / (digest + ".json")
        if path.is_symlink() or path.stat().st_nlink != 1:
            raise TeamAuthorizationError("artifact alias")
        raw = path.read_bytes()
        if len(raw) > 32 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
            raise TeamAuthorizationError("durable capture changed")
        return json.loads(raw)


class GitTeamCandidateIntegrator:
    """Construct immutable Git objects under the existing writer transaction.

    No working-tree files or branch refs are modified. Private retention refs
    prevent garbage collection. A failed database commit can leave an orphan
    anchor, which confers no authority without the transaction's durable row.
    """
    def __init__(self, *, authority, pool, repository: Path, git_executable: Path,
                 git_sha256: str, artifact_store: TeamArtifactStore, worker_verifier_secret: bytes):
        self.authority, self.pool = authority, pool
        self.repository = repository.resolve(strict=True)
        self.git = git_executable.resolve(strict=True)
        self.git_sha256 = git_sha256
        self.artifacts = artifact_store
        self.worker_secret=worker_verifier_secret
        self._deadline=contextvars.ContextVar("team_candidate_deadline",default=0.0)
        self._commit_date=contextvars.ContextVar("team_candidate_date",default="")

    def _git(self, args, *, data=None, index=None):
        if hashlib.sha256(self.git.read_bytes()).hexdigest() != self.git_sha256:
            raise TeamAuthorizationError("candidate Git executable changed")
        env = {"PATH": str(self.git.parent), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
               "GIT_AUTHOR_NAME": "Antigravity", "GIT_AUTHOR_EMAIL": "antigravity@invalid",
               "GIT_COMMITTER_NAME": "Director Candidate Recorder",
               "GIT_COMMITTER_EMAIL": "director@invalid"}
        if os.name == "nt":
            env["SystemRoot"] = os.environ["SystemRoot"]
        if index:
            env["GIT_INDEX_FILE"] = str(index)
        env["GIT_AUTHOR_DATE"]=env["GIT_COMMITTER_DATE"]=self._commit_date.get()
        remaining=self._deadline.get()-time.monotonic()
        if remaining<=0:
            raise TeamAuthorizationError("candidate integration deadline exhausted")
        result = subprocess.run([str(self.git), "--no-replace-objects", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.fsmonitor=false", "-C", str(self.repository), *args],
            input=data, capture_output=True, timeout=min(30,remaining), env=env, check=False)
        if result.returncode:
            raise TeamAuthorizationError("candidate Git operation failed")
        return result.stdout

    def integrate(self, *, grant, attempt_digest, capture_digest, query,
                  owner_token, fencing_generation, deadline=None, authority_recheck=None):
        """One immutable candidate for one authenticated capture, or nothing.

        `authority_recheck` is the primary lane's own durable recheck of the
        wave's cancellation, this assignment's binding and this attempt's
        latest event. When it is carried it is called as
        `authority_recheck(connection=..., stage="candidate")` on this
        transaction's connection, after the writer row below is held and
        before the first Git object is written. A standalone caller carries
        none and keeps the previous contract unchanged.
        """
        from .antigravity_team_worker import authenticate
        if not re.fullmatch(r"sha256:[0-9a-f]{64}",attempt_digest):
            raise TeamAuthorizationError("invalid candidate attempt identity")
        # The primary lane's own completion bound when one is carried; the
        # standalone sixty seconds only when it is not. Integration never
        # starts a fresh timer over a budget that is already running, and an
        # exhausted bound refuses before any Git or database work.
        if deadline is None:
            deadline = time.monotonic() + 60
        elif deadline - time.monotonic() <= 0:
            raise TeamAuthorizationError("candidate integration deadline exhausted")
        self._deadline.set(deadline)
        evidence = authenticate(self.worker_secret,self.artifacts.get(capture_digest))
        completed=datetime.fromisoformat(evidence["completed_at"])
        if completed.tzinfo is None or completed>datetime.now(timezone.utc):
            raise TeamAuthorizationError("capture completion timestamp invalid")
        self._commit_date.set(completed.isoformat())
        if evidence["grant_digest"] != grant.digest or evidence["attempt_digest"] != attempt_digest:
            raise TeamAuthorizationError("candidate capture binding mismatch")
        files = decode_returned_files(grant, evidence["returned_files"])
        schema = self.authority.schema
        from .langgraph_postgres import bounded_transaction
        with bounded_transaction(self.pool, deadline) as connection:
            writer = connection.execute(f"""SELECT task_id FROM {schema}.director_primary_task_writer
                WHERE task_id=%s AND engine_owner='langgraph' AND owner_token=%s
                AND fencing_generation=%s AND lease_expires_at>clock_timestamp() FOR UPDATE""",
                (grant.task_id, owner_token, fencing_generation)).fetchone()
            if writer is None:
                raise TeamAuthorizationError("candidate integration lacks current writer")
            if authority_recheck is not None:
                # Under primary authority, re-read here rather than trusting
                # the transport's read a moment ago: that ran in a separate
                # transaction, and a same-owner cancellation locks this very
                # writer row before it commits its kill switch. One that
                # completed before the lock above is seen by this read and
                # refused before any Git object, retention ref or row exists;
                # one still waiting behind that lock is serialized after this
                # candidate and never races it. The captured evidence already
                # stored by the transport is kept either way.
                authority_recheck(connection=connection, stage="candidate")
            self.authority.authorize(connection, grant_digest=grant.digest,
                query=query, stage="result", attempt_digest=attempt_digest)
            existing = connection.execute(f"SELECT candidate_sha,capture_digest FROM {schema}.director_primary_team_candidate WHERE attempt_digest=%s", (attempt_digest,)).fetchone()
            if existing:
                if existing["capture_digest"] != capture_digest:
                    raise TeamAuthorizationError("attempt returned different candidate")
                return existing["candidate_sha"]
            if self._git(["rev-parse", grant.commit + "^{tree}"]).decode().strip() != grant.tree:
                raise TeamAuthorizationError("candidate base tree mismatch")
            for blob in grant.source:
                raw = self._git(["show", grant.commit + ":" + blob.path])
                if hashlib.sha256(raw).hexdigest() != blob.sha256:
                    raise TeamAuthorizationError("candidate manifest differs from actual Git base")
            if grant.operation == "review":
                candidate = grant.commit
            else:
                with tempfile.TemporaryDirectory(prefix="director-team-index-") as temporary:
                    index = Path(temporary) / "index"
                    self._git(["read-tree", grant.commit], index=index)
                    for path, data in sorted(files.items()):
                        old = self._git(["ls-tree", grant.commit, "--", path]).decode()
                        mode = old.split(" ", 1)[0] if old else "100644"
                        if mode not in {"100644", "100755"}:
                            raise TeamAuthorizationError("candidate cannot replace a link or submodule")
                        blob_id = self._git(["hash-object", "-w", "--stdin"], data=data).decode().strip()
                        self._git(["update-index", "--add", "--cacheinfo", mode, blob_id, path], index=index)
                    tree = self._git(["write-tree"], index=index).decode().strip()
                    contribution_time=completed.astimezone(timezone.utc).strftime("%Y-%m-%d | %H:%M UTC")
                    message = ("[[Antigravity | " + grant.model + " | " + contribution_time + " | BEGIN]]\n"
                        "Bounded engineering candidate for " + grant.assignment_id + "\nCapture: " + capture_digest + "\n"
                        "[[Antigravity | " + grant.model + " | " + contribution_time + " | END]]\n\nContributed-by: Antigravity | " + grant.model + "\n")
                    candidate = self._git(["commit-tree", tree, "-p", grant.commit], data=message.encode()).decode().strip()
                    actual_paths = self._git(["diff-tree", "--no-commit-id", "--name-only", "-r", candidate]).decode().splitlines()
                    if not set(actual_paths).issubset(files):
                        raise TeamAuthorizationError("candidate readback contains ungranted changes")
                    for path, raw in files.items():
                        if self._git(["show", candidate + ":" + path]) != raw:
                            raise TeamAuthorizationError("candidate bytes failed independent readback")
                    # A private non-branch ref retains objects against Git GC.
                    # A transaction failure may leave this orphan anchor, but it
                    # confers no authority without the committed database row.
                    anchor="refs/director/team/"+attempt_digest.removeprefix("sha256:")
                    refs=self._git(["for-each-ref","--format=%(objectname)",anchor]).decode().splitlines()
                    if refs and refs!=[candidate]:
                        raise TeamAuthorizationError("candidate retention anchor conflicts")
                    if not refs:
                        self._git(["update-ref",anchor,candidate,"0"*40])
            live=connection.execute(f"SELECT 1 FROM {schema}.director_primary_task_writer WHERE task_id=%s AND lease_expires_at>clock_timestamp()",(grant.task_id,)).fetchone()
            if live is None:
                raise TeamAuthorizationError("writer lease expired during candidate construction")
            connection.execute(f"""INSERT INTO {schema}.director_primary_team_candidate
                (attempt_digest,grant_digest,capture_digest,candidate_sha) VALUES(%s,%s,%s,%s)""",
                (attempt_digest, grant.digest, capture_digest, candidate))
            return candidate
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]]
