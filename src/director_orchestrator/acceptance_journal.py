# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
"""Pre-migration v2 activation journal. Owner-protected configuration is authority."""
from __future__ import annotations
import base64
from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .provider_acceptance_v2 import (IssuerAuthority, ReviewContext, canonical,
    verify_provider_acceptance, _closed, _pairs)
from .primary_activation_evidence import ActivationIdentity, ActivationEvidenceError

POLICY_VERSION = "director-acceptance-registry/v1"
REQUIRED_ACTIVATION_CHECKS = frozenset({"Contribution attribution", "Python 3.12 (ubuntu-latest)",
    "Python 3.12 (windows-latest)", "Compose and container build", "Secret hygiene", "Cross-process recovery probe"})


def _activation_errors(method):
    """Translate expected input/storage failures after transaction unwinding."""
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            raise ActivationEvidenceError("v2 acceptance evidence unavailable or invalid") from exc
    return guarded


class AcceptanceJournal:
    @_activation_errors
    def __init__(self, path: str, registry_path: str, registry_sha256: str, *, read_only: bool = False):
        self.read_only = read_only
        self.path = Path(path)
        self.registry_path = Path(registry_path)
        self.pin = registry_sha256
        if not self.path.is_absolute() or not self.registry_path.is_absolute():
            raise ActivationEvidenceError("v2 journal and registry paths must be absolute")
        self.policy()  # Never create storage without authenticated configuration.
        if read_only:
            # mode=ro refuses absent storage and SQLite enforces every write ban.
            with self.connect() as db:
                db.execute("SELECT revision FROM activation_state WHERE id=1")
            return
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS activation_state (
                id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, document BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS acceptance_challenge (
                challenge TEXT PRIMARY KEY, context BLOB NOT NULL, registry BLOB NOT NULL,
                accepted_at INTEGER, package BLOB, package_digest TEXT, effective_authorities BLOB,
                CHECK ((accepted_at IS NULL AND package IS NULL AND package_digest IS NULL)
                    OR (accepted_at IS NOT NULL AND package IS NOT NULL AND package_digest IS NOT NULL)));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10) if self.read_only else sqlite3.connect(self.path, timeout=10)
        try:
            if not self.read_only:
                db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @_activation_errors
    def policy(self):
        raw = self.registry_path.read_bytes()
        if len(raw) > 131072 or hashlib.sha256(raw).hexdigest() != self.pin:
            raise ActivationEvidenceError("owner-pinned v2 registry mismatch")
        doc = json.loads(raw, object_pairs_hook=_pairs)
        _closed(doc, {"version", "activation", "repository", "pull_request_number", "packet_digest",
                      "material_author_providers", "authorities", "revoked_deployments", "ci_oidc_policy"})
        if doc["version"] != POLICY_VERSION or not isinstance(doc["revoked_deployments"], list):
            raise ActivationEvidenceError("invalid v2 registry policy")
        authorities = self.authorities(doc)
        if not REQUIRED_ACTIVATION_CHECKS <= authorities["ci"].required_ci_checks:
            raise ActivationEvidenceError("production acceptance requires all six baseline CI checks")
        if doc["ci_oidc_policy"] is not None:
            from .ci_acceptance_issuer import GitHubOIDCPolicy
            oidc = GitHubOIDCPolicy.from_document(doc["ci_oidc_policy"])
            template = doc["authorities"]["ci"]
            if (template["provider"] != "github-actions" or template["principal"] != oidc.principal or
                template["deployment"] != oidc.deployment or template["ci_workflow_ref"] != oidc.workflow_ref or
                template["ci_workflow_commit"] != oidc.workflow_sha or
                set(template["required_ci_checks"]) != oidc.required_ci_checks):
                raise ActivationEvidenceError("CI template and OIDC authority disagree")
        self.context(doc, "0"*64, 1, 2)
        return doc

    @staticmethod
    def authorities(doc):
        _closed(doc["authorities"], {"security", "independent_review", "ci"})
        result = {}
        for role, fields in doc["authorities"].items():
            _closed(fields, {"role", "provider", "principal", "deployment", "public_key",
                            "required_ci_checks", "ci_workflow_ref", "ci_workflow_commit"})
            values = dict(fields)
            values["public_key"] = base64.b64decode(values["public_key"], validate=True)
            values["required_ci_checks"] = frozenset(values["required_ci_checks"])
            result[role] = IssuerAuthority(**values)
            if result[role].role != role or result[role].deployment in doc["revoked_deployments"]:
                raise ActivationEvidenceError("issuer revoked or wrong registry role")
        return result

    @staticmethod
    def context(doc, challenge, issued_at, expires_at):
        return ReviewContext(ActivationIdentity(**doc["activation"]), doc["repository"],
            doc["pull_request_number"], doc["packet_digest"], challenge, issued_at, expires_at,
            frozenset(doc["material_author_providers"]))

    @_activation_errors
    def load(self):
        self.policy()
        with self.connect() as db:
            row = db.execute("SELECT revision,document FROM activation_state WHERE id=1").fetchone()
        return (None, None) if row is None else (row[0], json.loads(row[1], object_pairs_hook=_pairs))

    @staticmethod
    def _write(db, revision, value):
        if revision is None:
            try:
                db.execute("INSERT INTO activation_state VALUES (1,1,?)", (canonical(value),))
            except sqlite3.IntegrityError as exc:
                raise ActivationEvidenceError("activation state changed concurrently") from exc
        elif db.execute("UPDATE activation_state SET revision=revision+1,document=? WHERE id=1 AND revision=?",
                        (canonical(value), revision)).rowcount != 1:
            raise ActivationEvidenceError("activation state changed concurrently")

    def write(self, revision, value):
        policy = self.policy()
        if value.get("identity") != policy["activation"]:
            raise ActivationEvidenceError("activation identity differs from owner registry")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._write(db, revision, value)

    def challenge(self, identity, now=None):
        now = int(time.time()) if now is None else now
        policy = self.policy()
        if asdict(identity) != policy["activation"]:
            raise ActivationEvidenceError("challenge identity differs from owner registry")
        context = self.context(policy, secrets.token_hex(32), now, now+3600)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM activation_state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {}
            if [s["stage"] for s in state.get("stages", [])] != ["PREFLIGHT"]:
                raise ActivationEvidenceError("challenge requires exact preflight state")
            db.execute("INSERT INTO acceptance_challenge(challenge,context,registry) VALUES (?,?,?)",
                       (context.challenge, canonical(context.document()), canonical(policy)))
        return context.document()

    @_activation_errors
    def accept(self, revision, state, raw, now=None):
        now = int(time.time()) if now is None else now
        if len(raw) > 131072:
            raise ActivationEvidenceError("v2 package too large")
        source = json.loads(raw, object_pairs_hook=_pairs)
        policy = self.policy()
        package = source
        if policy["ci_oidc_policy"] is not None:
            _closed(source, {"version", "package", "ci_identity"})
            if source["version"] != "director-provider-acceptance-envelope/v2":
                raise ActivationEvidenceError("dynamic CI requires authenticated envelope")
            package = source["package"]
        challenge = package.get("context", {}).get("challenge")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT context,registry,accepted_at FROM acceptance_challenge WHERE challenge=?", (challenge,)).fetchone()
            if row is None or row[2] is not None:
                raise ActivationEvidenceError("unknown or replayed acceptance challenge")
            context_doc, snapshot = json.loads(row[0]), json.loads(row[1])
            if snapshot != policy:
                raise ActivationEvidenceError("registry changed since challenge")
            context = self.context(snapshot, challenge, context_doc["issued_at"], context_doc["expires_at"])
            if state["identity"] != snapshot["activation"] or [s["stage"] for s in state["stages"]] != ["PREFLIGHT"]:
                raise ActivationEvidenceError("acceptance requires exact preflight identity")
            authorities = self.authorities(policy)
            if policy["ci_oidc_policy"] is not None:
                from .ci_acceptance_issuer import GitHubOIDCPolicy, resolve_ci_identity
                authorities["ci"], ci_session = resolve_ci_identity(source["ci_identity"], expected=context,
                    policy=GitHubOIDCPolicy.from_document(policy["ci_oidc_policy"]), now=now)
                if package["receipts"]["ci"]["session_id"] != ci_session:
                    raise ActivationEvidenceError("CI receipt session differs from authenticated issuer run")
            if any(a.deployment in policy["revoked_deployments"] for a in authorities.values()):
                raise ActivationEvidenceError("effective issuer is currently revoked")
            verify_provider_acceptance(canonical(package), expected=context, authorities=authorities, now=now)
            encoded = canonical(source)
            digest = hashlib.sha256(encoded).hexdigest()
            authority_document = {}
            for role, authority in authorities.items():
                fields = asdict(authority)
                fields["public_key"] = base64.b64encode(fields["public_key"]).decode()
                fields["required_ci_checks"] = sorted(fields["required_ci_checks"])
                authority_document[role] = fields
            facts = dict(challenge=challenge, package_digest=digest)
            state["stages"].append(dict(stage="ACCEPTED", facts=facts,
                evidence_digest=hashlib.sha256(canonical(facts)).hexdigest(), recorded_at=now,
                source_artifact=source, source_artifact_digest=digest))
            db.execute("UPDATE acceptance_challenge SET accepted_at=?,package=?,package_digest=?,effective_authorities=? WHERE challenge=? AND accepted_at IS NULL",
                       (now, encoded, digest, canonical(authority_document), challenge))
            self._write(db, revision, state)  # Failure rolls back consumption and acceptance together.
        return state

    @_activation_errors
    def verify_record(self, identity, item):
        policy = self.policy()
        challenge = item["facts"].get("challenge")
        with self.connect() as db:
            row = db.execute("SELECT context,registry,accepted_at,package,package_digest,effective_authorities FROM acceptance_challenge WHERE challenge=?", (challenge,)).fetchone()
        if row is None or row[2] is None:
            raise ActivationEvidenceError("durable accepted challenge missing")
        context_doc, snapshot = json.loads(row[0]), json.loads(row[1])
        # Current owner policy can revoke or replace an issuer; replacement is fail-closed.
        for field in snapshot:
            if field != "revoked_deployments" and snapshot[field] != policy[field]:
                raise ActivationEvidenceError("accepted registry no longer authorized")
        self.authorities(policy)
        context = self.context(snapshot, challenge, context_doc["issued_at"], context_doc["expires_at"])
        # Provider key snapshots must still match owner-pinned keys. Dynamic CI
        # is reauthenticated below against GitHub, never trusted because DB says so.
        effective = dict(snapshot, authorities=json.loads(row[5]), revoked_deployments=policy["revoked_deployments"])
        for role in ("security", "independent_review", "ci"):
            if role != "ci" or snapshot["ci_oidc_policy"] is None:
                if effective["authorities"][role] != snapshot["authorities"][role]:
                    raise ActivationEvidenceError("recorded issuer authority was substituted")
        if snapshot["ci_oidc_policy"] is not None:
            ci = effective["authorities"]["ci"]
            pinned = snapshot["ci_oidc_policy"]
            if (ci["role"] != "ci" or ci["provider"] != "github-actions" or
                ci["principal"] != pinned["principal"] or ci["deployment"] != pinned["deployment"] or
                ci["ci_workflow_ref"] != pinned["workflow_ref"] or ci["ci_workflow_commit"] != pinned["workflow_sha"] or
                set(ci["required_ci_checks"]) != set(pinned["required_ci_checks"])):
                raise ActivationEvidenceError("recorded CI authority was substituted")
        authorities = self.authorities(effective)
        source = json.loads(row[3])
        package = source["package"] if snapshot["ci_oidc_policy"] is not None else source
        if snapshot["ci_oidc_policy"] is not None:
            from .ci_acceptance_issuer import GitHubOIDCPolicy, resolve_ci_identity
            authenticated_ci, ci_session = resolve_ci_identity(source["ci_identity"], expected=context,
                policy=GitHubOIDCPolicy.from_document(policy["ci_oidc_policy"]), now=row[2])
            if package["receipts"]["ci"]["session_id"] != ci_session:
                raise ActivationEvidenceError("CI receipt session differs from authenticated issuer run")
            if authenticated_ci.deployment in policy["revoked_deployments"]:
                raise ActivationEvidenceError("effective CI issuer is currently revoked")
            if authenticated_ci != authorities["ci"]:
                raise ActivationEvidenceError("CI public key archive does not authenticate")
        verify_provider_acceptance(canonical(package), expected=context, authorities=authorities, now=row[2])
        encoded = canonical(source)
        digest = hashlib.sha256(encoded).hexdigest()
        if (asdict(identity) != snapshot["activation"] or item.get("source_artifact_digest") != digest
                or row[4] != digest or item["facts"] != dict(challenge=challenge, package_digest=digest)
                or canonical(item.get("source_artifact")) != encoded or item["recorded_at"] != row[2]):
            raise ActivationEvidenceError("accepted package or projection was tampered")


def journal_from_environment(*, read_only=False):
    mode = os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")
    if mode == "github-v1":
        return None
    if mode != "provider-v2":
        raise ActivationEvidenceError("unknown acceptance mode")
    return AcceptanceJournal(os.environ["LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH"],
        os.environ["LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH"],
        os.environ["LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256"], read_only=read_only)


def require_runtime_acceptance(settings):
    """Before DB access verify readiness once and return its immutable identity."""
    mode = settings.langgraph_primary_acceptance_mode
    if mode == "github-v1":
        return
    if mode != "provider-v2":
        raise ActivationEvidenceError("unknown acceptance mode")
    journal = AcceptanceJournal(settings.langgraph_primary_acceptance_journal_path,
        settings.langgraph_primary_acceptance_registry_path, settings.langgraph_primary_acceptance_registry_sha256,
        read_only=True)
    from .primary_activation_evidence import ActivationEvidenceStore
    store = ActivationEvidenceStore.__new__(ActivationEvidenceStore)
    store._journal = journal
    store._revision = None
    store._acceptance_secrets = None
    state = store.load()
    if state is None:
        raise ActivationEvidenceError("v2 runtime acceptance is missing")
    identity = ActivationIdentity(**state["identity"])
    stages = [item["stage"] for item in state["stages"]]
    expected = ["PREFLIGHT", "ACCEPTED", "MIGRATED", "BACKED_UP", "RESTORE_PROVED"]
    if stages not in (expected, expected+["ACTIVATED"]) or not isinstance(state.get("legacy_inventory"), dict):
        raise ActivationEvidenceError("v2 runtime prerequisites incomplete")
    if (identity.candidate_commit != settings.langgraph_primary_pinned_commit_sha or
        identity.candidate_tree != settings.langgraph_primary_pinned_tree_sha or
        identity.profile_id != settings.app_env or
        identity.postgres_schema != settings.langgraph_primary_postgres_schema or
        identity.checkpoint_namespace != settings.langgraph_primary_checkpoint_namespace or
        identity.compose_project != os.environ.get("DIRECTOR_COMPOSE_PROJECT_NAME", "")):
        raise ActivationEvidenceError("runtime identity differs from accepted candidate")
    from .primary_generation import require_generation
    require_generation(settings, identity)
    return identity


def bind_runtime_database(identity, handle):
    """Bind the newly opened DB to the already verified startup identity, without I/O."""
    if identity is not None and (identity.database_identity != handle.storage_identity or
                                 identity.database_instance_id != handle.database_instance_id):
        raise ActivationEvidenceError("runtime database differs from accepted instance")

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:24 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
