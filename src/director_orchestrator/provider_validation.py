# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:12 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | BEGIN]]
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
import re
import secrets
import time
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .packet_contracts import canonical_json_bytes, validate_packet
from .provider_activation import ProviderActivationBinding

SCHEMA = "director_provider_validation_v1"
CHECKPOINT_NAMESPACE = "director-provider-validation/v1"
MAX_CAPTURE_BYTES = 1_048_576
# libpq treats a connect_timeout below two seconds as two seconds.
MIN_CONNECT_TIMEOUT_SECONDS = 2.0
# Explicit bounded cleanup allowance for the best-effort ambiguity marker in
# PostgresValidationStore.fail. It only bounds an otherwise indefinite wait on
# a row the timed-out primary operation, a concurrent settlement, or
# maintenance may still be holding; it funds no second dispatch, erases no
# retained usage, and treats no rollback as undoing provider work.
#
# Callers that share an absolute operation deadline (the Responses lane) must
# pass a `remaining_seconds` that already measures time left against that
# same deadline plus this fixed grace -- an absolute point in time, not a
# fresh window re-anchored at whatever moment cleanup happens to start. A
# primary operation that already consumed the whole packet budget must not
# then receive a full, fresh CLEANUP_ALLOWANCE_SECONDS on top of it; if the
# absolute deadline-plus-grace is already spent, cleanup is refused outright
# (the attempt stays exactly as the aborted primary operation left it --
# admitted or running -- which is still reconcilable). Callers with no shared
# deadline at all (the native lane) get a fresh CLEANUP_ALLOWANCE_SECONDS
# anchored at the first reading, since there is no absolute boundary to tie
# into.
CLEANUP_ALLOWANCE_SECONDS = 1.0
# Reserved out of CLEANUP_ALLOWANCE_SECONDS, never handed to the SQL-level
# statement/lock timeout itself. A lock_timeout computed to fill the ENTIRE
# remaining allowance leaves no room for connection handshake, the implicit
# ROLLBACK round trip Postgres requires after a canceled statement, or
# ordinary thread/OS scheduling jitter -- none of which lock_timeout bounds,
# since they happen before it starts or after it has already fired. Real
# PostgreSQL 17 evidence (Linux CI and local) observed wall-clock overshoot
# of the documented 2s-budget-plus-1s-grace boundary by 0.3-16ms when the
# full allowance was handed to lock_timeout with no margin reserved; this
# constant is sized well above that observed overshoot.
CLEANUP_OVERHEAD_MARGIN_SECONDS = 0.25


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
        """Open one store connection bounded by the shared whole-operation deadline.

        Returned as a guarded context manager, not the bare connection:
        psycopg.Connection.__exit__ commits on a clean exit but does not
        itself guard against that commit raising (only a rollback failure on
        the exception path is guarded), so a blocked implicit COMMIT that
        itself times out -- e.g. a deferred trigger contending on a lock a
        caller still holds -- propagates out of `__exit__` before it reaches
        its own `close()` call, leaking the connection until garbage
        collection warns about it. The outer `finally` here still closes it.
        """
        import contextlib

        @contextlib.contextmanager
        def guarded(connection):
            try:
                with connection as db:
                    yield db
            finally:
                if not connection.closed:
                    connection.close()

        if remaining_seconds is None:
            return guarded(self.connect())
        remaining = remaining_seconds()
        if remaining <= 0:
            raise ValidationError("validation deadline exhausted before connecting")
        # libpq rounds connect_timeout to whole seconds and treats anything
        # below MIN_CONNECT_TIMEOUT_SECONDS as that minimum, so the driver
        # can only honour a whole-second budget of at least two seconds. When
        # the remaining budget is smaller than what the driver would wait, the
        # acquisition is bounded by a real outer deadline here instead of the
        # driver's rounded-up figure; either way the shared deadline is re-read
        # after the connection returns so a slow connect that consumed the
        # budget fails here rather than at the first statement.
        driver_timeout = max(MIN_CONNECT_TIMEOUT_SECONDS, min(5.0, float(int(remaining))))
        if remaining >= driver_timeout:
            connection = self.connect(connect_timeout=int(driver_timeout))
        else:
            connection = self._connect_with_outer_deadline(int(driver_timeout), remaining)
        try:
            remaining_seconds()
        except ValidationError:
            connection.close()
            raise
        return guarded(connection)

    def _connect_with_outer_deadline(self, driver_timeout: int, remaining: float):
        """Bound a blocking connect by the caller's deadline, not the driver's.

        The connect runs on a worker thread and is awaited for exactly the
        remaining budget. On expiry the acquisition fails closed at once; the
        late connection, if the driver ever returns one, is closed on arrival
        so it never leaks into the pool of live handles.
        """
        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="validation-connect")
        future = executor.submit(self.connect, connect_timeout=driver_timeout)
        try:
            return future.result(timeout=max(0.0, remaining))
        except concurrent.futures.TimeoutError:
            def discard(done):
                if done.exception() is None:
                    try:
                        done.result().close()
                    except Exception:
                        pass
            future.add_done_callback(discard)
            raise ValidationError("validation deadline exhausted while connecting")
        finally:
            executor.shutdown(wait=False)

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
        """Return one strictly decreasing cleanup budget source.

        With no caller deadline (the native lane has none), the allowance is
        the fixed grace, anchored fresh at the first reading -- there is no
        absolute boundary to tie into, so a fresh window is the only option.

        With a caller-supplied `remaining_seconds` (the Responses lane), that
        callable is trusted to already measure time left against the SAME
        absolute deadline the primary operation used, extended by the fixed
        grace. A primary operation that already consumed the whole shared
        budget must not then receive a second, fresh CLEANUP_ALLOWANCE_SECONDS
        window measured from whatever moment cleanup happens to start: that
        would silently push completion past the documented
        operation-budget-plus-grace boundary. If the caller's
        deadline-plus-grace is already spent, cleanup is refused outright and
        the attempt stays exactly as the aborted primary operation left it
        (admitted or running), which is still reconcilable.

        Either way, CLEANUP_OVERHEAD_MARGIN_SECONDS is reserved out of
        whatever is left before it is handed to the SQL-level statement/lock
        timeout: that timeout only bounds Postgres's own wait, not the
        connection handshake before it or the implicit ROLLBACK round trip
        after a canceled statement, so a value computed to fill the entire
        remaining allowance leaves no margin and observed wall-clock
        completion lands at or past the boundary instead of strictly before
        it.
        """
        if remaining_seconds is not None:
            def bounded() -> float:
                left = remaining_seconds() - CLEANUP_OVERHEAD_MARGIN_SECONDS
                if left <= 0:
                    raise ValidationError("validation cleanup allowance is exhausted")
                return left
            return bounded
        anchored: dict = {}

        def remaining() -> float:
            if "deadline" not in anchored:
                anchored["deadline"] = time.monotonic() + CLEANUP_ALLOWANCE_SECONDS
            left = anchored["deadline"] - time.monotonic() - CLEANUP_OVERHEAD_MARGIN_SECONDS
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
                             (admission["reservation"],admission["generation"],admission["provider"])).fetchone()
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
        """Best-effort durable ambiguity marker under one bounded allowance.

        With no caller deadline (the native lane), the allowance is the fixed
        grace anchored fresh at the first reading. With a caller-supplied
        `remaining_seconds` (the Responses lane), it must already measure
        time left against the SAME absolute deadline the primary operation
        used, extended by the fixed grace -- never a fresh window re-anchored
        at whatever moment cleanup starts, which would let a primary timeout
        that already spent the whole shared budget push total elapsed time
        past the documented boundary. If that deadline-plus-grace is already
        spent, cleanup is refused outright. This writes no second dispatch,
        resets no operation budget, erases no retained usage, and treats no
        rollback as undoing provider work. If the bounded marker cannot be
        written (or cleanup is refused outright), the caller's original
        refusal keeps propagating and the attempt stays exactly as the
        aborted primary operation left it: admitted or running, still
        ineligible for redispatch, still reconcilable.
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
                 candidate: str, generation: str, tree: str, team_route=None, openai_responses_route=None):
        if len(capture_key) < 32:
            raise ValueError("dedicated bridge capture key is required")
        if not re.fullmatch(r"[0-9a-f]{40}", tree or ""):
            raise ValueError("expected runtime tree pin is required")
        self.store,self.key,self.candidate,self.generation=store,capture_key,candidate,generation
        self.tree=tree
        self.team_route=team_route
        self.openai_responses_route=openai_responses_route

    def verify(self, binding: ProviderActivationBinding, evidence_digest: str, *, expires_at=None) -> bool:
        try:
            row = self.store.load_capture(evidence_digest)
            if row is None or row[3] or row[4] <= int(time.time()) or row[5] != "succeeded":
                return False
            capture = ValidationCapture(**row[0])
            # The expected tree is compared, not just carried: a valid signed
            # capture minted for another runtime tree under the same candidate
            # and generation must never qualify this one.
            if (capture.candidate != self.candidate or capture.generation != self.generation
                    or capture.tree != self.tree
                    or capture.provider != binding.provider_id or capture.binding_digest != binding_digest(binding)
                    or capture.checkpoint_namespace != CHECKPOINT_NAMESPACE
                    or capture.expires_at <= int(time.time()) or not capture.native_session
                    or digest(bytes(row[1])) != capture.input_digest or digest(bytes(row[2])) != capture.output_digest
                    or digest(canonical_json_bytes(asdict(capture))) != evidence_digest
                    or (expires_at is not None and expires_at.timestamp() > capture.expires_at)):
                return False
            if capture.provider == "antigravity" and json.loads(bytes(row[2])).get("attempt_digest") != "sha256:"+capture.reservation:
                return False
            # A signed capture pins the lane configuration it was minted under.
            # Re-normalising the response cannot detect a rotated credential,
            # endpoint, meter enrollment, timeout ceiling or worker fingerprint,
            # so wherever this verifier holds a route that can recompute its
            # own identity, that route's CURRENT digest must still equal the
            # capture's. Native command lanes (and openai without a bound
            # Responses route) carry no recomputable identity here: their
            # configuration pin is enforced at execution time by the bridge
            # service (production_configuration_digest before and after the
            # run), which is the only place the executor and account files
            # exist. A later rotation of a native lane is therefore visible
            # only through re-execution, never through this read path.
            if capture.provider == "openai" and self.openai_responses_route is not None:
                if capture.configuration_digest != self.openai_responses_route.configuration_digest:
                    return False
            if capture.provider == "antigravity" and self.team_route is not None:
                if capture.configuration_digest != self.team_route.configuration_digest():
                    return False
            result = normalize_capture(capture.provider,json.loads(bytes(row[1])),bytes(row[2]),
                                       team_route=self.team_route, openai_responses_route=self.openai_responses_route)
            if digest(canonical_json_bytes(result)) != capture.result_digest or result["run_reference"] != capture.native_session:
                return False
            expected = hmac.new(self.key.encode(),canonical_json_bytes(capture.payload()),hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected,capture.signature)
        except (ValueError,TypeError,KeyError,ValidationError):
            return False


_GENERATION_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def resolved_provider_validation_generation() -> str:
    """Resolve the validation generation pin from protected generation selection.

    When DIRECTOR_PROVIDER_VALIDATION_GENERATION is absent, derive it from
    LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256. Explicit values must match.
    """
    import os

    manifest = os.environ.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256", "")
    explicit = os.environ.get("DIRECTOR_PROVIDER_VALIDATION_GENERATION", "")
    if explicit:
        if _GENERATION_SHA256.fullmatch(explicit) is None:
            raise ValidationError("validation generation pin is malformed")
        if manifest and explicit != manifest:
            raise ValidationError("validation generation differs from staged generation")
        return explicit
    if _GENERATION_SHA256.fullmatch(manifest) is None:
        raise ValidationError("validation generation is unavailable")
    return manifest


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
        generation=resolved_provider_validation_generation(),
        tree=os.environ["DIRECTOR_PROVIDER_VALIDATION_TREE"],
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
        obeys one absolute deadline-plus-grace: the ambiguity marker is bounded
        by the SAME shared deadline extended by the fixed grace, not a fresh
        grace window re-anchored at whenever cleanup starts, so a primary
        timeout that already spent the whole packet budget cannot push total
        elapsed time past the documented boundary; if that deadline-plus-grace
        is already spent, the marker write is refused outright.  A timeout or
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

        def cleanup_remaining_seconds() -> float:
            # Anchored to the SAME shared deadline (read fresh here, so it
            # reflects the packet-ceiling tightening below once that has
            # happened) plus the fixed grace, as an absolute point in time --
            # never a fresh window measured from whenever cleanup starts. A
            # primary operation that already spent the whole shared budget
            # gets only whatever of the fixed grace is left, not a full,
            # fresh CLEANUP_ALLOWANCE_SECONDS on top of it.
            rem = (deadline + CLEANUP_ALLOWANCE_SECONDS) - time.monotonic()
            if rem <= 0:
                raise ValidationError("Responses validation cleanup allowance is exhausted")
            return rem

        admission = self.store.admit(request, owner_token, preserve_canary_identity=True,
                                     remaining_seconds=remaining_seconds)
        raw_captures: list[bytes] = []
        try:
            if route.configuration_digest != admission["configuration_digest"]:
                raise ValidationError("Responses validation configuration digest drift")
            work = json.loads(admission["packet"])
            route.validate_canary(work, admission["packet"])

            # Tighten the shared deadline from the same operation start, never
            # from a post-admission reading; the earlier expiry wins. This must
            # happen before claim/fence/reservation, not only before settlement,
            # so every remaining store call -- including claim and the earlier
            # fence checks -- is bounded by the packet's own ceiling and not by
            # the much looser transport ceiling alone.
            packet_ceiling = int(work["limits"]["timeout_seconds"])
            if packet_ceiling < 1:
                raise ValidationError("Responses validation timeout is exhausted")
            deadline = min(deadline, started + packet_ceiling)
            remaining_seconds()

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
            # Best-effort durable reconciliation marker bounded by the same
            # absolute deadline plus its fixed grace (see
            # cleanup_remaining_seconds above); the original refusal must
            # keep propagating even if the marker itself cannot be written
            # or is refused outright because that deadline-plus-grace is
            # already spent.
            try:
                self.store.fail(admission["reservation"], remaining_seconds=cleanup_remaining_seconds)
            except Exception:
                pass
            raise


# ---------------------------------------------------------------------------
# Read-only all-lane qualification preflight
# ---------------------------------------------------------------------------
#
# The static lane inventory (eight canonical lanes plus three separately
# qualified contacts). Lane classes are descriptive only; every allocation
# weight is read from the owner-approved allocation_profiles authority rather
# than duplicated here. The inventory grants nothing: enrollment, live
# eligibility and paid readiness stay UNKNOWN because no authenticated
# collector exists yet. Research-only lanes keep an engineering target of zero.
_LANE_CLASSES = {
    "openai": "director_control_plane",
    "claude": "capability_routed_lane",
    "cursor": "capability_routed_lane",
    "grok": "capability_routed_lane",
    "antigravity": "capability_routed_lane",
    "perplexity": "evidence_only_research",
    "firecrawl": "public_search_transport",
    "github-copilot": "capability_routed_lane",
}


def _canonical_lanes():
    from .allocation_profiles import ALLOCATION_PROFILES, LANES
    if set(_LANE_CLASSES) != set(LANES):
        raise ValidationError("lane inventory drifted from the allocation authority")
    return tuple((lane, _LANE_CLASSES[lane],
                  {profile: int(targets[lane]) for profile, targets in ALLOCATION_PROFILES.items()})
                 for lane in LANES)


CANONICAL_LANES = _canonical_lanes()
ADDITIONAL_CONTACTS = (
    ("grok-bot", "Grok Bot", "Unverified as an enrolled transport; direct Grok evidence cannot qualify this contact."),
    ("z-ai", "Z.ai", "Separate tools-off Lite trial contract; not a canonical production lane."),
    ("vscode-coding-bot", "VS Code Coding Bot", "Editor host; the actual underlying provider/model identity must be preserved."),
)
RESEARCH_ONLY_LANES = frozenset({"perplexity", "firecrawl"})
PREFLIGHT_VERDICT = "UNKNOWN"


def preflight_fixture() -> dict:
    """One synthetic read-only fixture shared by every lane; never dispatched."""
    return {"schema_version": "director-preflight-fixture/v1", "task_id": "PREFLIGHT-synthetic",
            "assignment_id": "PREFLIGHT-synthetic", "objective": "Read-only lane preflight; no provider contact.",
            "allowed_tools": [], "allowed_external_effects": [], "starting_ref": "0" * 40,
            "limits": {"max_hops": 0, "max_revisions": 0, "timeout_seconds": 0, "budget_units": 0}}


def read_only_lane_preflight(lanes=None, *, fixture: dict | None = None,
                             evidence: Mapping[str, Mapping] | None = None) -> dict:
    """Evaluate every enrolled-or-listed lane against one synthetic fixture.

    Zero provider, work or spend calls: this function only projects the static
    inventory plus caller-supplied evidence rows, which are recorded verbatim
    as *asserted* (untrusted) evidence. No string in that evidence can change a
    lane's enrollment or live eligibility: both stay UNKNOWN until an
    authenticated collector exists, and missing evidence is never converted
    into a pass or a failure. Unknown lane names are reported, not admitted.
    """
    fixture = preflight_fixture() if fixture is None else dict(fixture)
    if fixture.get("allowed_tools") or fixture.get("allowed_external_effects") \
            or int(fixture.get("limits", {}).get("budget_units", 1)) != 0:
        raise ValidationError("preflight fixture must reserve no tools, effects or budget")
    fixture_digest = digest(canonical_json_bytes(fixture))
    known = {name: (lane_class, profiles) for name, lane_class, profiles in CANONICAL_LANES}
    requested = list(known) if lanes is None else list(dict.fromkeys(lanes))
    evidence = dict(evidence or {})
    rows = []
    for name in requested:
        if name not in known:
            rows.append({"lane": name, "lane_class": None, "allocation_profiles": None,
                         "enrollment_and_live_eligibility": PREFLIGHT_VERDICT, "research_only": None,
                         "asserted_evidence": None, "verdict": PREFLIGHT_VERDICT, "reason": "not_a_canonical_lane"})
            continue
        lane_class, profiles = known[name]
        row_evidence = evidence.get(name)
        asserted = dict(row_evidence) if isinstance(row_evidence, Mapping) else None
        rows.append({"lane": name, "lane_class": lane_class, "allocation_profiles": dict(profiles),
                     "enrollment_and_live_eligibility": PREFLIGHT_VERDICT,
                     "research_only": name in RESEARCH_ONLY_LANES,
                     "engineering_target_percent": profiles["engineering"],
                     "asserted_evidence": asserted,
                     "verdict": PREFLIGHT_VERDICT,
                     "reason": "no_authenticated_collector" if asserted is None
                     else "asserted_evidence_recorded_not_verified"})
    contacts = [{"contact": key, "name": label, "canonical_lane": None,
                 "separate_contact_qualification": PREFLIGHT_VERDICT, "general_enrollment": PREFLIGHT_VERDICT,
                 "note": note} for key, label, note in ADDITIONAL_CONTACTS]
    return {"schema": "director-lane-preflight/v1", "read_only": True, "fixture_sha256": fixture_digest,
            "provider_calls": 0, "work_reservations": 0, "spend_reservations": 0,
            "lanes": rows, "additional_contacts": contacts,
            "qualified_lane_count": 0, "unknown_lane_count": sum(row["verdict"] == PREFLIGHT_VERDICT for row in rows),
            "limitations": ["Static inventory plus caller-asserted evidence only; no provider, work or spend call was made.",
                            "Asserted evidence is untrusted and never establishes enrollment or eligibility; no authenticated collector exists yet.",
                            "UNKNOWN is never converted into pass or failure; a live canary needs separate owner authorization.",
                            "Research-only lanes keep their public-research restrictions; no trial route is promoted."]}
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
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:12 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | END]]
