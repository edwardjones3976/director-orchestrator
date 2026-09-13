# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:33 UTC | BEGIN]]
"""One-shot publication of existing native captures; no execution or signing."""
from dataclasses import asdict
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import time
from types import SimpleNamespace

from .approvals import owner_identity_matches
from .packet_contracts import canonical_json_bytes
from .primary_activation_commands import (
    ProviderActivationAdvanceRequest, verify_provider_activation_advance_proof,
)
from .primary_activation_evidence import ActivationEvidenceError
from .primary_provider_activation import PrimaryProviderActivationService
from .provider_acceptance_v2 import _pairs
from .provider_activation import (
    ACTIVATION_STAGES, ProviderActivationError, ProviderActivationEvidence, ProviderActivationStage, ProviderActivationSurface,
)
from .provider_validation import PostgresValidationStore

VERSION = "director-staged-provider-evidence/v1"
MAX_BYTES = 131072
MAX_REQUESTS = 160
MAX_OBSERVATION_BYTES = 8192
MAX_SECONDS = 120


def evidence_id(identity, generation, provider, surface, evidence_digest, *, stage=ProviderActivationStage.LIVE_OK):
    """An externally signed v1 request authenticates this generation binding.

    The existing proof signs evidence_id in its body, including the provider in
    its endpoint path. An editable outer file generation is never authority.
    """
    payload = dict(version=VERSION, activation=asdict(identity), generation=generation,
        provider=provider, surface=surface.value, stage=stage.value, evidence_digest=evidence_digest)
    return "staged-v1:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def require_runtime_off(settings):
    if (any((settings.allow_paid_runs, settings.langgraph_primary_enabled,
            settings.langgraph_primary_new_intake_enabled,
            settings.langgraph_primary_live_provider_dispatch_enabled,
            settings.langgraph_primary_offline_simulation_enabled))
        or not settings.langgraph_parallel_kill_switch
        or os.environ.get("DIRECTOR_ASGI_APP") != "director_orchestrator.primary_staging:app"):
        raise ActivationEvidenceError("publication requires the evidence-only runtime with execution disabled")


def read_requests(stream, *, settings, identity, generation, now=None):
    """Bound size and shape; verify every signed command before opening a store."""
    now = now or datetime.now(timezone.utc)
    try:
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized input")
        document = json.loads(raw, object_pairs_hook=_pairs)
        if (not isinstance(document, dict) or set(document) != {"version", "generation", "requests"}
            or document["version"] != VERSION or document["generation"] != generation):
            raise ValueError("closed generation contract mismatch")
        items = document["requests"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_REQUESTS:
            raise ValueError("bounded requests required")
        bindings = settings.primary_provider_activation_bindings()
        result, revisions, nonces = [], {}, set()
        for item in items:
            if not isinstance(item, dict) or set(item) not in (
                {"provider_id", "request"}, {"provider_id", "request", "observation_base64"}):
                raise ValueError("closed request contract mismatch")
            provider = item["provider_id"]
            request = ProviderActivationAdvanceRequest.model_validate(item["request"])
            # v1 HTTP parsing permits coercion; this file never coerces a revision.
            if type(item["request"].get("expected_revision")) is not int:
                raise ValueError("revision must be an integer")
            if (provider not in bindings or provider not in settings.langgraph_primary_required_providers
                or request.binding_version != bindings[provider].binding_version
                or request.expected_revision >= len(ACTIVATION_STAGES)
                or request.stage is not ACTIVATION_STAGES[request.expected_revision]
                or not owner_identity_matches(request.actor_id, settings.slack_owner_user_id)
                or request.evidence_id != evidence_id(identity, generation, provider,
                    request.surface, request.evidence_digest, stage=request.stage)):
                raise ValueError("signed owner, binding, stage or generation differs")
            if request.stage is ProviderActivationStage.LIVE_OK:
                if "observation_base64" in item:
                    raise ValueError("LIVE_OK must use the durable native capture")
            else:
                observation = base64.b64decode(item["observation_base64"], validate=True)
                if (not 1 <= len(observation) <= MAX_OBSERVATION_BYTES
                    or hashlib.sha256(observation).hexdigest() != request.evidence_digest):
                    raise ValueError("signed observation bytes differ")
            authorization = verify_provider_activation_advance_proof(settings.slack_gateway_secret,
                settings.shared_secret, provider, request, now=int(now.timestamp()))
            key = provider, request.surface
            if (request.expected_revision <= revisions.get(key, -1)
                or authorization.nonce_digest in nonces):
                raise ValueError("duplicate nonce or reordered surface stages")
            revisions[key] = request.expected_revision
            nonces.add(authorization.nonce_digest)
            result.append((provider, request, authorization))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ActivationEvidenceError("staged provider evidence input is invalid or unauthorized") from None


class _ProjectedObservations:
    """Preflight signed commands on a snapshot, without appending real evidence."""
    def __init__(self, store, bindings):
        self.rows = {(binding, surface): list(store.load(binding, surface))
            for binding in bindings.values() for surface in ProviderActivationSurface}

    def load(self, binding, surface):
        return tuple(self.rows[binding, surface])

    def append_authorized(self, evidence, *, expected_revision, authorization):
        rows = self.rows[evidence.binding, evidence.surface]
        if len(rows) != expected_revision:
            return False
        rows.append(evidence)
        return True


class _BoundedCaptureReader(PostgresValidationStore):
    def connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=10000")


def publish_requests(store, verifier, *, settings, requests, now=None, deadline=None):
    """Reuse existing stage and nonce checks; no canary admission or dispatch."""
    clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    deadline = deadline if deadline is not None else time.monotonic() + MAX_SECONDS
    def within_deadline():
        if time.monotonic() >= deadline:
            raise ActivationEvidenceError("publication time bound exceeded; inspect durable evidence before recovery")
    def checked(function):
        def call(*args, **kwargs):
            within_deadline()
            result = function(*args, **kwargs)
            within_deadline()
            return result
        return call
    if verifier is None or not requests:
        raise ActivationEvidenceError("authenticated capture verification and requests are required")
    # Existing service readiness can issue several reads internally. Guard each
    # individual store/verifier boundary, not just the outer provider loop.
    store = SimpleNamespace(load=checked(store.load), append_authorized=checked(store.append_authorized))
    verifier = SimpleNamespace(verify=checked(verifier.verify))
    providers = sorted({provider for provider, _, _ in requests})
    configured = settings.primary_provider_activation_bindings()
    bindings = {provider: configured[provider] for provider in providers}
    service = PrimaryProviderActivationService(store=store, bindings=bindings,
        required_providers=settings.langgraph_primary_required_providers, clock=clock,
        capture_verifier=verifier)
    projected = _ProjectedObservations(store, bindings)
    preview = PrimaryProviderActivationService(store=projected, bindings=bindings,
        required_providers=settings.langgraph_primary_required_providers, clock=clock,
        capture_verifier=verifier)
    expected = []
    # Validate the entire supplied sequence before any real append. These are
    # externally signed observations, never stages inferred from a canary.
    for provider, request, authorization in requests:
        within_deadline()
        binding = bindings[provider]
        verify_provider_activation_advance_proof(settings.slack_gateway_secret,
            settings.shared_secret, provider, request, now=int(clock().timestamp()))
        records = projected.load(binding, request.surface)
        value = ProviderActivationEvidence(schema_version=request.evidence_schema_version,
            binding=binding, surface=request.surface, stage=request.stage, revision=request.expected_revision + 1,
            evidence_id=request.evidence_id, evidence_digest=request.evidence_digest,
            observed_at=request.observed_at, expires_at=request.expires_at)
        if request.observed_at > clock() or request.expires_at <= clock():
            raise ActivationEvidenceError("observation is future-dated or expired")
        if request.expected_revision < len(records):
            if records[request.expected_revision] != value:
                raise ActivationEvidenceError("historical observation differs from signed request")
        else:
            try:
                preview.record_authorized(provider_id=provider, authorization=authorization,
                    **request.model_dump(exclude={"actor_id", "gateway_proof"}))
            except (ValueError, ProviderActivationError):
                raise ActivationEvidenceError("signed observation sequence or native capture is invalid") from None
        expected.append(value)
    for provider in providers:
        within_deadline()
        if not preview.provider_ready(provider, live=True):
            raise ActivationEvidenceError("complete current observation chains and native captures are required on both surfaces")
    for (provider, request, authorization), value in zip(requests, expected):
        within_deadline()
        # Store/network delays must not extend the short signed-command lifetime.
        verify_provider_activation_advance_proof(settings.slack_gateway_secret,
            settings.shared_secret, provider, request, now=int(clock().timestamp()))
        records = store.load(bindings[provider], request.surface)
        if request.expected_revision < len(records):
            if records[request.expected_revision] != value or not store.append_authorized(value,
                expected_revision=request.expected_revision, authorization=authorization):
                raise ActivationEvidenceError("historical authorization is not an exact durable replay")
        else:
            service.record_authorized(provider_id=provider, authorization=authorization,
                **request.model_dump(exclude={"actor_id", "gateway_proof"}))
    for value in expected:
        within_deadline()
        records = store.load(value.binding, value.surface)
        if len(records) != 5 or records[value.revision - 1] != value:
            raise ActivationEvidenceError("published provider evidence readback differs")
    for provider in providers:
        within_deadline()
        if not service.provider_ready(provider, live=True):
            raise ActivationEvidenceError("published captures are no longer current on both surfaces")
    within_deadline()
    return dict(version=VERSION, published_providers=providers,
        surfaces_verified=True, selected_provider_live_ready=True)


def publish_file(path, *, settings, identity):
    """Preflight exact generation and restore before opening the write pool."""
    from .primary_generation import require_generation, require_validation_generation
    from .primary_activation_evidence import ActivationEvidenceStore
    from .provider_validation import capture_verifier_from_environment
    from .langgraph_postgres import open_provider_activation_store
    deadline = time.monotonic() + MAX_SECONDS
    require_runtime_off(settings)
    require_generation(settings, identity)
    ActivationEvidenceStore("/data/langgraph-primary-activation-evidence.json",
        read_only=True).require_staged(identity)
    require_validation_generation(identity)
    generation = os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"]
    if os.environ.get("DIRECTOR_PROVIDER_VALIDATION_GENERATION") != generation:
        raise ActivationEvidenceError("capture verifier generation differs from staged generation")
    with open(path, "rb") as stream:
        requests = read_requests(stream, settings=settings, identity=identity, generation=generation)
    verifier = capture_verifier_from_environment(candidate=identity.candidate_commit, tree=identity.candidate_tree)
    if verifier is None:
        raise ActivationEvidenceError("authenticated capture verification is unavailable")
    verifier.store = _BoundedCaptureReader(verifier.store.dsn)
    with open_provider_activation_store(settings.langgraph_primary_postgres_dsn,
        schema=identity.postgres_schema, accepted_storage_identity=identity.database_identity,
        accepted_instance_id=identity.database_instance_id,
        allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres,
        deadline=deadline) as store:
        result = publish_requests(store, verifier, settings=settings, requests=requests, deadline=deadline)
    return dict(result, generation=generation)
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:33 UTC | END]]
