# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | BEGIN]]
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 13:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | BEGIN]]
"""Dedicated provider canaries: durable admission and captured execution evidence.

This schema never contains production tasks or writer leases. Schema creation is
an explicit operator action; constructing a store or verifying evidence does no DDL.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import secrets
import time
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .packet_contracts import canonical_json_bytes, validate_packet
from .provider_activation import ProviderActivationBinding

SCHEMA = "director_provider_validation_v1"
CHECKPOINT_NAMESPACE = "director-provider-validation/v1"
MAX_CAPTURE_BYTES = 1_048_576
# Explicit bounded cleanup allowance for the best-effort ambiguity marker in
# PostgresValidationStore.fail. The allowance is anchored once per cleanup and
# then only decreases: it never resets, restarts, or extends the authoritative
# operation deadline, funds no second dispatch, erases no retained usage, and
# treats no rollback as undoing provider work. It only bounds an otherwise
# indefinite wait on a row the timed-out primary operation, a concurrent
# settlement, or maintenance may still be holding.
CLEANUP_ALLOWANCE_SECONDS = 2.0


class ValidationError(RuntimeError):
    """Validation cannot safely run or authenticate its evidence."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def binding_digest(binding: ProviderActivationBinding) -> str:
    return digest(canonical_json_bytes(asdict(binding)))


class ValidationRequest(BaseModel):
    """Only a configured generation and lane may be selected by the caller."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    generation: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class ValidationCapture:
    generation: str
    provider: str
    candidate: str
    tree: str
    binding_digest: str
    configuration_digest: str
    reservation: str
    nonce: str
    fence: int
    checkpoint_namespace: str
    input_digest: str
    output_digest: str
    result_digest: str
    native_session: str
    expires_at: int
    signature: str

    def payload(self) -> dict:
        value = asdict(self)
        value.pop("signature")
        return value


class PostgresValidationStore:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("validation DSN is required")
        self.dsn = dsn

    def connect(self, connect_timeout: int = 5):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=connect_timeout)

    def _connect_bounded(self, remaining_seconds=None):
        """Open one store connection bounded by the shared whole-operation deadline."""
        if remaining_seconds is None:
            return self.connect()
        remaining = remaining_seconds()
        if remaining <= 0:
            raise ValidationError("validation deadline exhausted before connecting")
        return self.connect(connect_timeout=max(1, min(5, int(remaining))))

    @staticmethod
    def _bind_deadline(db, remaining_seconds) -> None:
        """Scope this transaction's statement and lock waits to the shared deadline.

        The transaction-local timeouts also bound commit-time deferred-trigger
        lock waits, so a blocked COMMIT aborts and rolls back instead of
        persisting settlement after expiry. Such a rollback never reverses an
        external provider effect; real results, usage and charges remain
        reconcilable facts.
        """
        remaining = remaining_seconds()
        milliseconds = str(max(1, int(remaining * 1000)))
        db.execute("SELECT set_config('statement_timeout',%s,true), set_config('lock_timeout',%s,true)",
                   (milliseconds, milliseconds))

    @staticmethod
    def _refresh_deadline(db, remaining_seconds) -> None:
        """Re-derive statement/lock timeouts from the time actually left.

        A timeout bound once at method entry goes stale across successive
        statements, and the transaction-local setting also governs commit-time
        deferred-trigger lock waits. Refreshing before later statements and
        again immediately before the implicit COMMIT bounds every wait by
        remaining time instead of an earlier, larger figure. Source-level
        correction for the cumulative per-statement/COMMIT staleness
        suspicion (F7); no runtime reproduction is claimed.
        """
        if remaining_seconds is not None:
            remaining_seconds()
            PostgresValidationStore._bind_deadline(db, remaining_seconds)

    def _cleanup_remaining(self, remaining_seconds):
        """Return one anchored, strictly decreasing cleanup budget source.

        The allowance is fixed on the first reading: the documented fixed
        grace, tightened to the shared operation deadline's remaining time
        when that source is still live. Every later reading counts down the
        same anchored deadline, so per-statement refreshes inside the cleanup
        -- including the refresh immediately before the implicit COMMIT -- can
        never restart or extend the allowance after the original deadline has
        raised or expired. A spent or failing budget source anchors the plain
        grace; it never unbounds the marker.
        """
        anchored: dict = {}

        def remaining() -> float:
            if "deadline" not in anchored:
                budget = CLEANUP_ALLOWANCE_SECONDS
                if remaining_seconds is not None:
                    try:
                        left = float(remaining_seconds())
                    except Exception:
                        # A spent budget source must not unbound the marker;
                        # the fixed grace then applies and any marker failure
                        # stays best-effort for the caller.
                        left = 0.0
                    if left > 0:
                        budget = min(budget, left)
                anchored["deadline"] = time.monotonic() + max(budget, 0.0)
            left = anchored["deadline"] - time.monotonic()
            if left <= 0:
                raise ValidationError("validation cleanup allowance is exhausted")
            return left

        return remaining

    def bootstrap(self) -> None:
        """Explicit migration of the fixed validation schema only."""
        with self.connect() as db:
            db.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            db.execute(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.generations (
                generation text PRIMARY KEY, candidate text NOT NULL, tree text NOT NULL,
                owner_digest text NOT NULL, expires_at bigint NOT NULL,
                budget_units bigint NOT NULL CHECK(budget_units > 0),
                reserved_units bigint NOT NULL DEFAULT 0 CHECK(reserved_units >= 0),
                revoked boolean NOT NULL DEFAULT false, configuration jsonb NOT NULL)""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.attempts (
                reservation text PRIMARY KEY, generation text NOT NULL REFERENCES {SCHEMA}.generations,
                provider text NOT NULL, nonce text NOT NULL UNIQUE, fence bigint NOT NULL,
                state text NOT NULL CHECK(state IN ('admitted','running','succeeded','ambiguous')),
                input bytea NOT NULL, input_digest text NOT NULL,
                output bytea, capture jsonb, capture_digest text UNIQUE,
                UNIQUE(generation,provider))""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.checkpoints (
                reservation text NOT NULL REFERENCES {SCHEMA}.attempts,
                namespace text NOT NULL CHECK(namespace = '{CHECKPOINT_NAMESPACE}'),
                sequence integer NOT NULL, state text NOT NULL,
                PRIMARY KEY(reservation,sequence))""")

    def create_generation(self, *, generation: str, candidate: str, tree: str,
                          owner_token: str, expires_at: int, budget_units: int,
                          lanes: Mapping[str, dict]) -> None:
        import re
        if (not re.fullmatch(r"[0-9a-f]{64}", generation)
                or not re.fullmatch(r"[0-9a-f]{40}", candidate)
                or not re.fullmatch(r"[0-9a-f]{40}", tree)
                or len(owner_token) < 32 or type(budget_units) is not int or budget_units < 1
                or not 0 < expires_at - int(time.time()) <= 86400 or not lanes):
            raise ValidationError("invalid validation generation")
        for lane, spec in lanes.items():
            if set(spec) != {"binding", "configuration_digest", "cost_units", "work_packet"}:
                raise ValidationError("closed lane configuration required")
            binding = ProviderActivationBinding(**spec["binding"])
            if binding.provider_id != lane or type(spec["cost_units"]) is not int or spec["cost_units"] < 1:
                raise ValidationError("lane or authoritative cost mismatch")
            if not re.fullmatch(r"[0-9a-f]{64}", spec["configuration_digest"]):
                raise ValidationError("invalid production configuration digest")
            work = validate_packet("work", spec["work_packet"])
            if (work["starting_ref"] != candidate or work["allowed_external_effects"]
                    or work["allowed_tools"] or work["limits"]["max_hops"] != 1
                    or work["limits"]["max_revisions"] != 0
                    or not work["task_id"].startswith("VALIDATION-")
                    or not work["assignment_id"].startswith("VALIDATION-")):
                raise ValidationError("canary must be fixed, packet-only, and effect-free")
        with self.connect() as db:
            db.execute(f"INSERT INTO {SCHEMA}.generations "
                       "(generation,candidate,tree,owner_digest,expires_at,budget_units,configuration) "
                       "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                       (generation,candidate,tree,digest(owner_token.encode()),expires_at,
                        budget_units,json.dumps(dict(lanes))))

    def admit(self, request: ValidationRequest, owner_token: str, *, preserve_canary_identity: bool = False,
              remaining_seconds=None) -> dict:
        """One transaction locks eligibility, budget and replay admission."""
        with self._connect_bounded(remaining_seconds) as db:
            if remaining_seconds is not None:
                self._bind_deadline(db, remaining_seconds)
            row = db.execute(f"SELECT candidate,tree,owner_digest,expires_at,budget_units,"
                             f"reserved_units,revoked,configuration FROM {SCHEMA}.generations "
                             "WHERE generation=%s FOR UPDATE", (request.generation,)).fetchone()
            if (row is None or not hmac.compare_digest(row[2],digest(owner_token.encode()))
                    or row[6] or row[3] <= int(time.time()) or request.provider not in row[7]):
                raise ValidationError("validation generation unauthorized or expired")
            spec = row[7][request.provider]
            if db.execute(f"SELECT 1 FROM {SCHEMA}.attempts WHERE generation=%s AND provider=%s",
                          (request.generation,request.provider)).fetchone():
                raise ValidationError("attempt already admitted; reconcile without redispatch")
            if row[5] + spec["cost_units"] > row[4]:
                raise ValidationError("validation budget exhausted")
            self._refresh_deadline(db, remaining_seconds)
            reservation, nonce = secrets.token_hex(32), secrets.token_hex(32)
            work = dict(spec["work_packet"])
            # Unique canary identity prevents accidental reuse of a production task.
            preserve_canary = preserve_canary_identity if request.provider == "openai" else (request.provider == "antigravity")
            if not preserve_canary:
                work.update(task_id="VALIDATION-"+reservation, assignment_id="VALIDATION-"+reservation,
                            idempotency_key="VALIDATION-"+reservation)
            raw = canonical_json_bytes(work)
            db.execute(f"UPDATE {SCHEMA}.generations SET reserved_units=reserved_units+%s WHERE generation=%s",
                       (spec["cost_units"],request.generation))
            db.execute(f"INSERT INTO {SCHEMA}.attempts "
                       "(reservation,generation,provider,nonce,fence,state,input,input_digest) "
                       "VALUES (%s,%s,%s,%s,1,'admitted',%s,%s)",
                       (reservation,request.generation,request.provider,nonce,raw,digest(raw)))
            db.execute(f"INSERT INTO {SCHEMA}.checkpoints VALUES (%s,%s,1,'admitted')",
                       (reservation,CHECKPOINT_NAMESPACE))
            self._refresh_deadline(db, remaining_seconds)
            return dict(reservation=reservation,nonce=nonce,fence=1,generation=request.generation,
                        provider=request.provider,candidate=row[0],tree=row[1],expires_at=row[3],
                        binding=spec["binding"],configuration_digest=spec["configuration_digest"],
                        packet=raw,owner_digest=row[2])

    def claim_execution(self, admission: dict, *, remaining_seconds=None) -> None:
        with self._connect_bounded(remaining_seconds) as db:
            if remaining_seconds is not None:
                self._bind_deadline(db, remaining_seconds)
            row = db.execute(f"SELECT a.state,a.nonce,a.fence,a.input_digest,g.revoked,g.expires_at "
                             f"FROM {SCHEMA}.attempts a JOIN {SCHEMA}.generations g USING(generation) "
                             "WHERE a.reservation=%s AND a.generation=%s AND a.provider=%s FOR UPDATE",
                             (admission["reservation"],admission["generation"],admission["provider"]))
            if (row is None or row[0] != "admitted" or row[1] != admission["nonce"]
                    or row[2] != admission["fence"] or row[3] != digest(admission["packet"])
                    or row[4] or row[5] <= int(time.time())):
                raise ValidationError("validation fence or attempt is not eligible")
            self._refresh_deadline(db, remaining_seconds)
            db.execute(f"UPDATE {SCHEMA}.attempts SET state='running' WHERE reservation=%s", (admission["reservation"],))
            db.execute(f"INSERT INTO {SCHEMA}.checkpoints VALUES (%s,%s,2,'running')",
                       (admission["reservation"],CHECKPOINT_NAMESPACE))
            self._refresh_deadline(db, remaining_seconds)

    def verify_fence(self, request, *, remaining_seconds=None) -> None:
        """Called by the same native executor directly before I/O."""
        reservation = request.task_id.removeprefix("VALIDATION-")
        with self._connect_bounded(remaining_seconds) as db:
            if remaining_seconds is not None:
                self._bind_deadline(db, remaining_seconds)
            row = db.execute(f"SELECT a.state,a.fence,a.nonce,a.input_digest,g.owner_digest,"
                             f"g.expires_at,g.revoked,a.provider FROM {SCHEMA}.attempts a "
                             f"JOIN {SCHEMA}.generations g USING(generation) WHERE reservation=%s",
                             (reservation,)).fetchone()
            if (not request.task_id.startswith("VALIDATION-") or row is None or row[0] != "running"
                    or row[1] != request.writer_generation or row[2] != request.nonce
                    or row[3] != request.packet_digest or "sha256:"+row[4] != request.writer_owner_token_digest
                    or row[5] <= int(time.time()) or row[6] or row[7] != request.provider
                    or request.assignment_id != request.task_id):
                raise ValidationError("dedicated validation writer fence rejected")

    def fail(self, reservation: str, *, remaining_seconds=None) -> None:
        """Best-effort durable ambiguity marker under one anchored allowance.

        The cleanup deadline is anchored on the first reading: it starts at
        the fixed grace, tightened to the shared operation deadline's
        remaining time while that budget is still live. Later readings in the
        same cleanup count down that anchored deadline, so a refresh can
        never restart or extend the allowance after the primary deadline has
        raised or expired. This writes no second dispatch, resets no
        operation budget, erases no retained usage, and treats no rollback as
        undoing provider work. If the bounded marker cannot be written, the
        caller's original refusal keeps propagating and the attempt stays
        exactly as the aborted primary operation left it: admitted or
        running, still ineligible for redispatch, still reconcilable.
        """
        bounded = self._cleanup_remaining(remaining_seconds)
        with self._connect_bounded(bounded) as db:
            self._bind_deadline(db, bounded)
            db.execute(f"UPDATE {SCHEMA}.attempts SET state='ambiguous' WHERE reservation=%s AND state IN ('admitted','running')",
                       (reservation,))
            self._refresh_deadline(db, bounded)

    def settle(self, admission: dict, output: bytes, capture: ValidationCapture, *, remaining_seconds=None) -> str:
        # The deadline is entered before capture serialization: an exhausted
        # budget refuses as a deadline, so a malformed capture can never
        # masquerade as deadline evidence and expiry can never surface as a
        # serialization TypeError.
        with self._connect_bounded(remaining_seconds) as db:
            if remaining_seconds is not None:
                self._bind_deadline(db, remaining_seconds)
            encoded = canonical_json_bytes(asdict(capture))
            evidence_digest = digest(encoded)
            row = db.execute(f"SELECT a.state,g.revoked,g.expires_at FROM {SCHEMA}.attempts a "
                             f"JOIN {SCHEMA}.generations g USING(generation) WHERE reservation=%s FOR UPDATE",
                             (admission["reservation"],)).fetchone()
            if row is None or row[0] != "running" or row[1] or row[2] <= int(time.time()):
                raise ValidationError("completion revoked or expired")
            self._refresh_deadline(db, remaining_seconds)
            db.execute(f"UPDATE {SCHEMA}.attempts SET state='succeeded',output=%s,capture=%s::jsonb,"
                       "capture_digest=%s WHERE reservation=%s", (output,encoded.decode(),evidence_digest,admission["reservation"]))
            db.execute(f"INSERT INTO {SCHEMA}.checkpoints VALUES (%s,%s,3,'succeeded')",
                       (admission["reservation"],CHECKPOINT_NAMESPACE))
            self._refresh_deadline(db, remaining_seconds)
        return evidence_digest

    def load_capture(self, evidence_digest: str):
        with self.connect() as db:
            return db.execute(f"SELECT a.capture,a.input,a.output,g.revoked,g.expires_at,a.state "
                              f"FROM {SCHEMA}.attempts a JOIN {SCHEMA}.generations g USING(generation) "
                              "WHERE a.capture_digest=%s", (evidence_digest,)).fetchone()


class CaptureVerifier:
    def __init__(self, store: PostgresValidationStore, *, capture_key: str,
                 candidate: str, generation: str, team_route=None, openai_responses_route=None):
        if len(capture_key) < 32:
            raise ValueError("dedicated bridge capture key is required")
        self.store,self.key,self.candidate,self.generation=store,capture_key,candidate,generation
        self.team_route=team_route
        self.openai_responses_route=openai_responses_route

    def verify(self, binding: ProviderActivationBinding, evidence_digest: str, *, expires_at=None) -> bool:
        try:
            row = self.store.load_capture(evidence_digest)
            if row is None or row[3] or row[4] <= int(time.time()) or row[5] != "succeeded":
                return False
            capture = ValidationCapture(**row[0])
            if (capture.candidate != self.candidate or capture.generation != self.generation
                    or capture.provider != binding.provider_id or capture.binding_digest != binding_digest(binding)
                    or capture.checkpoint_namespace != CHECKPOINT_NAMESPACE
                    or capture.expires_at <= int(time.time()) or not capture.native_session
                    or digest(bytes(row[1])) != capture.input_digest or digest(bytes(row[2])) != capture.output_digest
                    or digest(canonical_json_bytes(asdict(capture))) != evidence_digest
                    or (expires_at is not None and expires_at.timestamp() > capture.expires_at)):
                return False
            if capture.provider == "antigravity" and json.loads(bytes(row[2])).get("attempt_digest") != "sha256:"+capture.reservation:
                return False
            result = normalize_capture(capture.provider,json.loads(bytes(row[1])),bytes(row[2]),
                                       team_route=self.team_route, openai_responses_route=self.openai_responses_route)
            if digest(canonical_json_bytes(result)) != capture.result_digest or result["run_reference"] != capture.native_session:
                return False
            expected = hmac.new(self.key.encode(),canonical_json_bytes(capture.payload()),hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected,capture.signature)
        except (ValueError,TypeError,KeyError,ValidationError):
            return False


def capture_verifier_from_environment(*, candidate: str = "", tree: str = "", require_enabled: bool = True):
    """No DDL or dispatch. Staged readers may opt out of the execution switch.

    Disabling that switch check never supplies missing configuration or bypasses
    the candidate/tree pins and authenticated capture checks.
    """
    import os
    if require_enabled and os.environ.get("DIRECTOR_PROVIDER_VALIDATION_ENABLED") != "true":
        return None
    if not candidate or candidate != os.environ["DIRECTOR_PROVIDER_VALIDATION_CANDIDATE"]:
        raise ValidationError("validation candidate differs from runtime pin")
    if not tree or tree != os.environ["DIRECTOR_PROVIDER_VALIDATION_TREE"]:
        raise ValidationError("validation tree differs from runtime pin")
    from .provider_validation_team import team_route_from_environment
    from .openai_responses_validation import responses_route_from_environment
    return CaptureVerifier(PostgresValidationStore(os.environ["DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN"]),
        capture_key=os.environ["DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY"],
        candidate=os.environ["DIRECTOR_PROVIDER_VALIDATION_CANDIDATE"],
        generation=os.environ["DIRECTOR_PROVIDER_VALIDATION_GENERATION"],
        team_route=team_route_from_environment(),
        openai_responses_route=responses_route_from_environment())


def make_capture(admission: dict, output: bytes, *, capture_key: str, team_route=None, openai_responses_route=None) -> ValidationCapture:
    """Only the bridge calls this after configured native execution returns."""
    if len(capture_key) < 32 or not output or len(output) > MAX_CAPTURE_BYTES:
        raise ValidationError("missing bounded execution capture")
    work = validate_packet("work", json.loads(admission["packet"]))
    if admission["provider"] == "antigravity" and json.loads(output).get("attempt_digest") != "sha256:"+admission["reservation"]:
        raise ValidationError("team capture belongs to another reservation")
    result = normalize_capture(admission["provider"],work,output,team_route=team_route,openai_responses_route=openai_responses_route)
    from .packet_contracts import build_replay_run
    build_replay_run(work,result)
    if result["provider"] != admission["provider"] or result["status"] != "SUCCEEDED" or result["side_effects"]:
        raise ValidationError("native canary did not complete without effects")
    fields = dict(generation=admission["generation"],provider=admission["provider"],
                  candidate=admission["candidate"],tree=admission["tree"],
                  binding_digest=binding_digest(ProviderActivationBinding(**admission["binding"])),
                  configuration_digest=admission["configuration_digest"],reservation=admission["reservation"],
                  nonce=admission["nonce"],fence=admission["fence"],checkpoint_namespace=CHECKPOINT_NAMESPACE,
                  input_digest=digest(admission["packet"]),output_digest=digest(output),
                  result_digest=digest(canonical_json_bytes(result)),
                  native_session=result["run_reference"],expires_at=admission["expires_at"])
    signature=hmac.new(capture_key.encode(),canonical_json_bytes(fields),hashlib.sha256).hexdigest()
    return ValidationCapture(**fields,signature=signature)


def normalize_capture(provider: str, work: dict, raw: bytes, *, team_route=None, openai_responses_route=None) -> dict:
    """Require native terminal capture, never a caller-authored result packet."""
    if provider == "antigravity":
        if team_route is None:
            raise ValidationError("Antigravity team SSH capture verifier is unbound")
        return team_route.normalize(work,raw)
    if not raw or len(raw) > MAX_CAPTURE_BYTES:
        raise ValidationError("native capture size invalid")
    if provider == "openai":
        if openai_responses_route is not None:
            return openai_responses_route.normalize(work, raw)
        from .native_codex_transport import normalize_codex_jsonl
        return normalize_codex_jsonl(work,raw,requested_model="binding-pinned")
    from .primary_transports import normalize_native_provider_envelope, grok_terminal_success
    from .packet_contracts import _decode_json
    native = _decode_json(raw,source="native validation capture")
    if not isinstance(native,dict) or "schema_version" in native:
        raise ValidationError("raw native terminal envelope is required")
    # These are native CLI envelopes, not result-packet status assertions.
    # Three vendor dialects are recognised.  The third was established by an
    # authenticated grok canary on 2026-09-08: that CLI reports terminal success
    # as stopReason and names its session sessionId.  Only the exact value
    # "end_turn" is accepted, so a truncated ("max_tokens") or errored run still
    # fails; this recognises a dialect rather than relaxing the requirement.
    # The grok dialect is scoped to its own lane.  Left provider-agnostic, an
    # "end_turn" stop reason rehabilitates an already-failed envelope of another
    # dialect -- a claude-shaped result carrying subtype "error" and is_error
    # true would pass.  Only grok's envelope was observed, and a
    # provider payload must never select the dialect it is judged by.
    grok = provider == "grok"
    success = grok_terminal_success(native) if grok else (native.get("status") == "SUCCESS" or (
        native.get("type") == "result" and native.get("subtype") == "success"
        and native.get("is_error") is False))
    session_keys = ("session_id","conversation_id") + (("sessionId",) if grok else ())
    session = next((native[key] for key in session_keys
        if isinstance(native.get(key),str) and native[key]),None)
    if not success or session is None:
        raise ValidationError("native unique successful terminal/session is missing")
    if any(native.get(key) for key in ("tool_calls","tool_uses","side_effects")):
        raise ValidationError("unexpected native canary tool effects")
    native = dict(native)
    # Whichever dialect supplied it, the canonical key is promoted once here.
    native["session_id"] = session
    return validate_packet("result",normalize_native_provider_envelope(provider,work,native))



def pinned_path_manifest(value: str) -> dict:
    """Inventory a configured installed runtime tree without following aliases."""
    import stat
    from pathlib import Path
    root=Path(value)
    if not root.is_absolute() or not root.exists():
        raise ValidationError("absolute pinned runtime/account path unavailable")
    paths=[root]
    manifest={}
    total=0
    while paths:
        path=paths.pop()
        info=path.lstat()
        if path.is_symlink() or getattr(info,"st_file_attributes",0)&1024:
            raise ValidationError("pinned runtime contains an alias")
        if stat.S_ISDIR(info.st_mode):
            paths.extend(path.iterdir())
            if len(paths)+len(manifest)>50000:
                raise ValidationError("pinned runtime inventory exceeds file bound")
        elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
            total+=info.st_size
            if total>2*1024**3:
                raise ValidationError("pinned runtime inventory exceeds byte bound")
            with path.open("rb") as handle:
                hasher=hashlib.sha256()
                for chunk in iter(lambda:handle.read(1024*1024),b""):
                    hasher.update(chunk)
            manifest[str(path.resolve())]=hasher.hexdigest()
        else:
            raise ValidationError("pinned runtime contains a special file or hardlink")
    return manifest

def production_configuration_digest(executor, provider: str, *, account_files: tuple[str,...]) -> str:
    """Pin the real executable, argv files, workspace, adapter and auth material.

    Auth bytes are only locally hashed, never included in capture/source packets.
    Account-file paths are operator configuration, not caller inputs. Credential
    refresh changes this identity and requires fresh generation/binding evidence.
    """
    import os
    from pathlib import Path
    if provider not in executor.commands or not account_files:
        raise ValidationError("complete native account/runtime pin is required")
    argv=executor.commands[provider]
    files={}
    for value in (argv[0],*account_files):
        files.update(pinned_path_manifest(value))
    for value in argv[1:]:
        path=Path(value)
        if path.is_absolute() and path.is_file():
            if path.is_symlink():
                raise ValidationError("pinned argv file is a symlink")
            files[str(path.resolve())]=digest(path.read_bytes())
    adapter=Path(__file__).with_name("host_provider_bridge.py")
    files[str(adapter)]=digest(adapter.read_bytes())
    # Bind the native profile route without disclosing environment values.
    from .host_provider_bridge import _SAFE_OS_ENV, _LANE_AUTH_ENV
    environment={k:v for k,v in os.environ.items() if k.upper() in _SAFE_OS_ENV or k in _LANE_AUTH_ENV[provider]}
    return digest(canonical_json_bytes({"argv":list(argv),"files":files,
                  "workspace":str(executor.cwd),"environment_digest":digest(canonical_json_bytes(environment))}))


class ValidationBridgeService:
    """Dedicated endpoint; the production executor configuration is reused exactly."""
    def __init__(self, *, store: PostgresValidationStore, production_executor,
                 capture_key: str, owner_token: str, account_files: Mapping[str,tuple[str,...]],
                 team_route=None, openai_responses_route=None):
        if len(capture_key)<32 or len(owner_token)<32 or hmac.compare_digest(capture_key,owner_token):
            raise ValueError("distinct validation owner and capture keys required")
        self.store,self.production,self.capture_key,self.owner_token=store,production_executor,capture_key,owner_token
        self.account_files=dict(account_files)
        self.team_route=team_route
        if openai_responses_route is not None:
            from .openai_responses_validation import OpenAIResponsesValidationRoute
            if not isinstance(openai_responses_route, OpenAIResponsesValidationRoute):
                raise ValueError("openai_responses_route must be OpenAIResponsesValidationRoute")
        self.openai_responses_route=openai_responses_route

    def run(self, request: ValidationRequest, owner_token: str) -> dict:
        if not hmac.compare_digest(owner_token,self.owner_token):
            raise ValidationError("validation owner authentication failed")
        if request.provider == "antigravity":
            if self.team_route is None:
                raise ValidationError("Antigravity team SSH validation route is unbound")
            return self.team_route.run(self,request,owner_token)
        if request.provider == "openai" and self.openai_responses_route is not None:
            return self._run_openai_responses(request, owner_token)
        admission=self.store.admit(request,owner_token)
        raw_captures=[]
        try:
            pin=production_configuration_digest(self.production,request.provider,
                                               account_files=self.account_files.get(request.provider,()))
            if pin!=admission["configuration_digest"]:
                raise ValidationError("production executable/account/workspace/adapter binding drift")
            self.store.claim_execution(admission)
            from .host_provider_bridge import BridgeRequest, WindowsHostProviderExecutor
            from types import SimpleNamespace
            executor=WindowsHostProviderExecutor(self.production.commands,cwd=self.production.cwd,
                timeout_seconds=self.production.timeout_seconds,
                writer_fence_verifier=SimpleNamespace(verify=self.store.verify_fence))
            bridge=BridgeRequest(provider=request.provider,task_id="VALIDATION-"+admission["reservation"],
                assignment_id="VALIDATION-"+admission["reservation"],
                idempotency_digest=digest(("VALIDATION-"+admission["reservation"]).encode()),
                writer_generation=admission["fence"],packet_digest=digest(admission["packet"]),
                writer_owner_token_digest="sha256:"+admission["owner_digest"],
                timestamp=int(time.time()),nonce=admission["nonce"],signature="",packet=admission["packet"])
            def observe(provider,raw,result):
                if provider!=request.provider or raw_captures:
                    raise ValidationError("duplicate or mismatched native capture")
                raw_captures.append(raw)
            executor.execute(bridge,execution_observer=observe)
            if len(raw_captures)!=1:
                raise ValidationError("native transport discarded its execution capture")
            if pin!=production_configuration_digest(self.production,request.provider,
                    account_files=self.account_files.get(request.provider,())):
                raise ValidationError("native configuration changed during execution")
            capture=make_capture(admission,raw_captures[0],capture_key=self.capture_key)
            evidence=self.store.settle(admission,raw_captures[0],capture)
            return {"evidence_digest":evidence,"evidence_id":admission["reservation"],
                    "expires_at":capture.expires_at,"surfaces":["app","runtime_adapter"]}
        except Exception:
            # Best-effort durable ambiguity marker under one anchored cleanup
            # allowance: the native lane supplies no shared deadline, so the
            # fixed anchored grace bounds the marker and no refresh can restart
            # it. The original refusal keeps propagating even if the marker
            # itself cannot be written.
            try:
                self.store.fail(admission["reservation"])
            except Exception:
                pass
            raise

    def _run_openai_responses(self, request: ValidationRequest, owner_token: str) -> dict:
        """Explicit Responses API validation; never falls back to native Codex.

        One whole-operation deadline, measured from a single start reading taken
        before admission, governs connection acquisition, admission, monetary
        reservation, claim, fence, network, capture, settlement and COMMIT alike.
        The canary packet's own ceiling tightens that same deadline from the shared
        start; a post-admission reading may never extend it.  Store calls receive
        the shared deadline so connection waits and transaction-local statement
        and lock timeouts -- refreshed before later statements and again
        immediately before the implicit COMMIT, including commit-time
        deferred-trigger lock waits -- are bounded, and a late COMMIT rolls back
        instead of persisting eligible settlement after expiry.  Failure cleanup
        obeys one anchored allowance: the ambiguity marker is bounded by the
        shared deadline's remaining time or, once that is spent, the fixed
        anchored grace, and a refresh can never restart it.  A timeout or
        rollback never pretends to reverse an external provider action: actual
        results, usage and charges remain reconcilable facts.
        """
        from .host_provider_bridge import BridgeRequest
        route = self.openai_responses_route
        transport_ceiling = int(getattr(route.transport, "maximum_timeout_seconds", 180))
        if transport_ceiling < 1:
            raise ValidationError("Responses validation timeout is exhausted")
        started = time.monotonic()
        deadline = started + transport_ceiling

        def remaining_seconds() -> float:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise ValidationError("Responses validation timeout is exhausted")
            return rem

        admission = self.store.admit(request, owner_token, preserve_canary_identity=True,
                                     remaining_seconds=remaining_seconds)
        raw_captures: list[bytes] = []
        try:
            if route.configuration_digest != admission["configuration_digest"]:
                raise ValidationError("Responses validation configuration digest drift")
            self.store.claim_execution(admission, remaining_seconds=remaining_seconds)
            bridge = BridgeRequest(
                provider="openai",
                task_id="VALIDATION-" + admission["reservation"],
                assignment_id="VALIDATION-" + admission["reservation"],
                idempotency_digest=digest(("VALIDATION-" + admission["reservation"]).encode()),
                writer_generation=admission["fence"],
                packet_digest=digest(admission["packet"]),
                writer_owner_token_digest="sha256:" + admission["owner_digest"],
                timestamp=int(time.time()),
                nonce=admission["nonce"],
                signature="",
                packet=admission["packet"],
            )
            self.store.verify_fence(bridge, remaining_seconds=remaining_seconds)
            work = json.loads(admission["packet"])
            route.validate_canary(work, admission["packet"])

            # Tighten the shared deadline from the same operation start, never
            # from a post-admission reading; the earlier expiry wins.
            packet_ceiling = int(work["limits"]["timeout_seconds"])
            if packet_ceiling < 1:
                raise ValidationError("Responses validation timeout is exhausted")
            deadline = min(deadline, started + packet_ceiling)
            remaining_seconds()
            route.reserve_monetary_admission(remaining_seconds=remaining_seconds)
            self.store.verify_fence(bridge, remaining_seconds=remaining_seconds)

            def observe(provider, raw, result):
                if provider != "openai" or raw_captures:
                    raise ValidationError("duplicate or mismatched native capture")
                if not isinstance(raw, (bytes, bytearray)) or not raw:
                    raise ValidationError("Responses validation capture bytes are required")
                raw_captures.append(bytes(raw))

            from types import SimpleNamespace
            context = SimpleNamespace(
                fencing_generation=admission["fence"],
                timeout_seconds=remaining_seconds(),
                deadline=deadline,
                total_token_limit=route.total_token_limit,
                max_hops=int(work["limits"]["max_hops"]),
                allowed_external_effects=tuple(work["allowed_external_effects"]),
                execution_observer=observe,
                task_id=work["task_id"],
                assignment_id=work["assignment_id"],
                idempotency_key=work["idempotency_key"],
                work_packet_digest="sha256:" + digest(admission["packet"]),
            )
            # Meter prepare/settle run inside the transport before LIVE_OK evidence
            # is minted. Missing provider account headers refuse here.
            route.transport.send(admission["packet"], context=context)
            if len(raw_captures) != 1:
                raise ValidationError("Responses transport discarded its execution capture")
            self.store.verify_fence(bridge, remaining_seconds=remaining_seconds)
            if route.configuration_digest != admission["configuration_digest"]:
                raise ValidationError("Responses validation configuration changed during execution")
            remaining_seconds()
            capture = make_capture(admission, raw_captures[0], capture_key=self.capture_key, openai_responses_route=route)
            evidence = self.store.settle(admission, raw_captures[0], capture,
                                         remaining_seconds=remaining_seconds)
            return {"evidence_digest": evidence, "evidence_id": admission["reservation"],
                    "expires_at": capture.expires_at, "surfaces": ["app", "runtime_adapter"],
                    "transport": "responses-api"}
        except Exception:
            # Best-effort durable reconciliation marker under the anchored
            # cleanup allowance; the original refusal must keep propagating
            # even if the marker itself cannot be written.
            try:
                self.store.fail(admission["reservation"], remaining_seconds=remaining_seconds)
            except Exception:
                pass
            raise
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:10 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 13:26 UTC | END]]
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]]
