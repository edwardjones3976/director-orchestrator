# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | BEGIN]]
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | BEGIN]]
"""Explicit OpenAI Responses validation/capture route for LIVE_OK evidence.

Native Codex host validation remains the default Windows bridge path and still
refuses before launch when signed total_token_limit cannot be enforced. This
module is the separate Responses API route that reuses OpenAIResponsesPacketTransport,
ValidationCapture/CaptureVerifier, and enrolled response metering.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .packet_contracts import canonical_json_bytes, validate_packet
from .provider_validation import ValidationError, digest, MAX_CAPTURE_BYTES


RESPONSES_VALIDATION_TRANSPORT = "responses-api"


class OpenAIResponsesValidationRoute:
    """Operator-injected Responses validation binding; never discovered from env."""

    def __init__(
        self,
        *,
        transport: object,
        total_token_limit: int,
        expected_model: str,
        meter_enrollment_digest: str,
        configuration_digest: str | None = None,
        expected_organization_id: str | None = None,
        expected_api_project_id: str | None = None,
        canary_task_id: str | None = None,
        canary_assignment_id: str | None = None,
        canary_attempt_digest: str | None = None,
        canary_work_packet_digest: str | None = None,
        budget_authority: object | None = None,
        budget_authority_digest: str | None = None,
        budget_limits: object | None = None,
    ) -> None:
        if type(total_token_limit) is not int or total_token_limit < 1:
            raise ValueError("Responses validation requires a positive signed total_token_limit")
        if not expected_model or not meter_enrollment_digest:
            raise ValueError("Responses validation route bindings are incomplete")
        meter = getattr(transport, "response_meter", None)
        if meter is None:
            raise ValueError("Responses validation requires an enrolled response meter")
        if getattr(transport, "model", None) != expected_model:
            raise ValueError("Responses validation model binding mismatch")

        # Automatically extract enrollment bindings if available on the meter
        if hasattr(meter, "enrollment"):
            e = meter.enrollment
            if expected_organization_id is None:
                expected_organization_id = getattr(e, "organization_id", None)
            if expected_api_project_id is None:
                expected_api_project_id = getattr(e, "api_project_id", None)
            if canary_task_id is None:
                canary_task_id = getattr(e, "task_id", None)
            if canary_assignment_id is None:
                canary_assignment_id = getattr(e, "assignment_id", None)
            if canary_attempt_digest is None:
                canary_attempt_digest = getattr(e, "attempt_digest", None)
            if canary_work_packet_digest is None:
                canary_work_packet_digest = getattr(e, "work_packet_digest", None)
            if budget_authority_digest is None:
                budget_authority_digest = getattr(e, "authority_digest", None)
            if budget_authority is None and hasattr(meter, "authority"):
                budget_authority = getattr(meter, "authority", None)
        elif hasattr(meter, "enrollments") and getattr(meter, "enrollments", None):
            enrollments = meter.enrollments
            e = None
            if canary_attempt_digest and canary_attempt_digest in enrollments:
                e = enrollments[canary_attempt_digest]
            elif len(enrollments) == 1:
                e = next(iter(enrollments.values()))
            if e is not None:
                if expected_organization_id is None:
                    expected_organization_id = getattr(e, "organization_id", None)
                if expected_api_project_id is None:
                    expected_api_project_id = getattr(e, "api_project_id", None)
                if canary_task_id is None:
                    canary_task_id = getattr(e, "task_id", None)
                if canary_assignment_id is None:
                    canary_assignment_id = getattr(e, "assignment_id", None)
                if canary_attempt_digest is None:
                    canary_attempt_digest = getattr(e, "attempt_digest", None)
                if canary_work_packet_digest is None:
                    canary_work_packet_digest = getattr(e, "work_packet_digest", None)
                if budget_authority_digest is None:
                    budget_authority_digest = getattr(e, "authority_digest", None)
            if budget_authority is None and hasattr(meter, "authority"):
                budget_authority = getattr(meter, "authority", None)

        self.transport = transport
        self.total_token_limit = total_token_limit
        self.expected_model = expected_model
        self.meter_enrollment_digest = meter_enrollment_digest
        self._pinned_configuration_digest = configuration_digest
        self.expected_organization_id = expected_organization_id
        self.expected_api_project_id = expected_api_project_id
        self.canary_task_id = canary_task_id
        self.canary_assignment_id = canary_assignment_id
        self.canary_attempt_digest = canary_attempt_digest
        self.canary_work_packet_digest = canary_work_packet_digest
        self.budget_authority = budget_authority
        self.budget_authority_digest = budget_authority_digest
        self.budget_limits = budget_limits

    def compute_configuration_digest(self) -> str:
        api_url = getattr(self.transport, "api_url", None)
        api_key = getattr(self.transport, "_api_key", None)
        model = getattr(self.transport, "model", None)
        if api_url is None or api_key is None or model is None:
            if self._pinned_configuration_digest is not None:
                return self._pinned_configuration_digest
            raise ValidationError("Responses validation transport configuration is incomplete")
        credential_digest = digest(api_key.encode())
        org_id = self.expected_organization_id or ""
        proj_id = self.expected_api_project_id or ""
        return responses_validation_configuration_digest(
            model=model,
            api_url=api_url,
            credential_digest=credential_digest,
            organization_id=org_id,
            api_project_id=proj_id,
            meter_enrollment_digest=self.meter_enrollment_digest,
            total_token_limit=self.total_token_limit,
        )

    @property
    def configuration_digest(self) -> str:
        computed = self.compute_configuration_digest()
        if self._pinned_configuration_digest is not None:
            if self._pinned_configuration_digest != computed:
                raise ValidationError("Responses validation configuration changed during execution")
            return self._pinned_configuration_digest
        return computed

    def validate_canary(self, work: Mapping, raw_packet: bytes) -> None:
        # Missing pins fail closed exactly like mismatched ones: a route that
        # cannot bind canary identity must never reach HTTP.
        if (self.canary_task_id is None or self.canary_assignment_id is None
                or self.canary_attempt_digest is None
                or self.canary_work_packet_digest is None):
            raise ValidationError("Responses validation canary identity is not fully pinned")
        if self.canary_task_id is not None and work.get("task_id") != self.canary_task_id:
            raise ValidationError("Responses validation canary task_id mismatch")
        if self.canary_assignment_id is not None and work.get("assignment_id") != self.canary_assignment_id:
            raise ValidationError("Responses validation canary assignment_id mismatch")
        if self.canary_attempt_digest is not None and work.get("idempotency_key") != self.canary_attempt_digest:
            raise ValidationError("Responses validation canary idempotency_key mismatch")
        if self.canary_work_packet_digest is not None:
            packet_digest = "sha256:" + digest(raw_packet)
            if packet_digest != self.canary_work_packet_digest:
                raise ValidationError("Responses validation canary work_packet_digest mismatch")
        if work.get("allowed_tools") or work.get("allowed_external_effects"):
            raise ValidationError("Responses validation canary cannot permit tools or effects")

    def reserve_monetary_admission(self, connection=None, *, remaining_seconds=None) -> None:
        if self.budget_authority is None:
            # Paid Responses validation without authenticated monetary funding
            # refuses before any HTTP call; it never proceeds unmetered.
            raise ValidationError("Responses validation lacks authenticated monetary funding")
        authority = self.budget_authority
        authority_digest = self.budget_authority_digest
        if not authority_digest:
            raise ValidationError("Responses validation budget authority digest is required")
        task_id = self.canary_task_id
        if not task_id:
            raise ValidationError("Responses validation canary task_id is required for budget reservation")
        attempt_digest = self.canary_attempt_digest
        if not attempt_digest:
            raise ValidationError("Responses validation canary attempt_digest is required for budget reservation")

        def check_remaining() -> float:
            if remaining_seconds is not None:
                rem = remaining_seconds()
                if rem <= 0:
                    raise ValidationError("Responses validation timeout exhausted during monetary reservation")
                return rem
            return 30.0

        def apply_timeouts(conn):
            rem = check_remaining()
            milliseconds = str(max(1, int(rem * 1000)))
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                (milliseconds, milliseconds),
            )

        def do_reserve(conn):
            apply_timeouts(conn)
            budget = authority.reserve(
                conn,
                authority_digest=authority_digest,
                provider="openai",
                task_id=task_id,
                attempt_digest=attempt_digest,
                limits=self.budget_limits,
            )
            check_remaining()
            if budget is not None and getattr(budget, "token_limit", None) is not None:
                if self.total_token_limit > budget.token_limit:
                    raise ValidationError(
                        f"Responses validation total_token_limit ({self.total_token_limit}) "
                        f"exceeds budget token limit ({budget.token_limit})"
                    )
            return budget

        if connection is not None:
            do_reserve(connection)
        else:
            pool = getattr(getattr(authority, "entitlements", None), "pool", None) or getattr(getattr(authority, "entitlements", None), "_pool", None)
            if pool is None:
                raise ValidationError("Responses validation budget authority pool unavailable")
            rem = check_remaining()
            with pool.connection(timeout=rem) as conn, conn.transaction():
                do_reserve(conn)
                check_remaining()

    def normalize(self, work: Mapping, raw: bytes) -> dict:
        self.validate_canary(work, canonical_json_bytes(work))
        return normalize_openai_responses_capture(
            work,
            raw,
            expected_model=self.expected_model,
            expected_organization_id=self.expected_organization_id,
            expected_api_project_id=self.expected_api_project_id,
            total_token_limit=self.total_token_limit,
        )


def responses_validation_configuration_digest(
    *,
    model: str,
    api_url: str,
    credential_digest: str,
    organization_id: str,
    api_project_id: str,
    meter_enrollment_digest: str,
    total_token_limit: int,
) -> str:
    """Pin Responses validation identity without inventing account proof.

    Organization and project ids here are the enrollment routing intention that
    settlement later matches against provider-returned headers. Missing provider
    headers still refuse qualification.
    """
    if (not model or not api_url or not credential_digest or not organization_id
            or not api_project_id or not meter_enrollment_digest
            or type(total_token_limit) is not int or total_token_limit < 1):
        raise ValidationError("Responses validation configuration pin is incomplete")
    return digest(canonical_json_bytes({
        "transport": RESPONSES_VALIDATION_TRANSPORT,
        "model": model,
        "api_url": api_url,
        "credential_digest": credential_digest,
        "organization_id": organization_id,
        "api_project_id": api_project_id,
        "meter_enrollment_digest": meter_enrollment_digest,
        "total_token_limit": total_token_limit,
    }))


def normalize_openai_responses_capture(
    work: Mapping,
    raw: bytes,
    *,
    expected_model: str,
    expected_organization_id: str | None = None,
    expected_api_project_id: str | None = None,
    total_token_limit: int | None = None,
) -> dict:
    """Qualify one Responses API terminal envelope; never accept arbitrary success JSON."""
    if not raw or len(raw) > MAX_CAPTURE_BYTES:
        raise ValidationError("native capture size invalid")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("OpenAI Responses capture is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("OpenAI Responses capture is malformed")

    # Support both retained capture bundle (response + response_headers) and standalone envelope
    if "response" in parsed and "response_headers" in parsed:
        native = parsed["response"]
        raw_headers = parsed["response_headers"]
    elif parsed.get("object") == "response":
        native = parsed
        raw_headers = {}
    else:
        raise ValidationError("OpenAI Responses capture object binding is required")

    if not isinstance(native, dict) or native.get("object") != "response":
        raise ValidationError("OpenAI Responses capture object binding is required")
    if (native.get("status") != "completed" or native.get("error") is not None
            or native.get("incomplete_details") is not None):
        raise ValidationError("OpenAI Responses capture is not a completed terminal result")
    if native.get("model") != expected_model:
        raise ValidationError("OpenAI Responses capture model differs from route binding")

    # Verify observed response headers against expected route binding
    headers = {str(k).lower(): v for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
    if expected_organization_id is not None:
        if headers.get("openai-organization") != expected_organization_id:
            raise ValidationError("OpenAI Responses capture organization header mismatch or missing")
    if expected_api_project_id is not None:
        if headers.get("openai-project") != expected_api_project_id:
            raise ValidationError("OpenAI Responses capture project header mismatch or missing")
    if (expected_organization_id is not None or expected_api_project_id is not None or "response_headers" in parsed):
        if not isinstance(headers.get("x-request-id"), str) or not headers["x-request-id"].strip():
            raise ValidationError("OpenAI Responses capture request ID header missing")

    run_reference = native.get("id")
    if not isinstance(run_reference, str) or not run_reference.strip():
        raise ValidationError("OpenAI Responses capture lacks authenticated response id")
    output = native.get("output")
    if not isinstance(output, list):
        raise ValidationError("OpenAI Responses output is malformed")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            raise ValidationError("OpenAI Responses output is malformed")
        if item.get("type") == "reasoning" and item.get("status") in {None, "completed"}:
            continue
        if (item.get("type") != "message" or item.get("role") != "assistant"
                or item.get("status") != "completed"
                or not isinstance(item.get("content"), list)):
            raise ValidationError("OpenAI Responses contains non-completed or unexpected output")
        for content in item["content"]:
            if (not isinstance(content, dict) or content.get("type") != "output_text"
                    or not isinstance(content.get("text"), str)):
                raise ValidationError("OpenAI Responses text is malformed or refused")
            chunks.append(content["text"])
    outcome = "".join(chunks)
    if not outcome.strip():
        raise ValidationError("OpenAI Responses lacks output text")
    usage = native.get("usage")
    if (not isinstance(usage, dict)
            or any(type(usage.get(key)) is not int or usage[key] < 0
                   for key in ("input_tokens", "output_tokens", "total_tokens"))
            or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]):
        raise ValidationError("OpenAI Responses usage is missing or inconsistent")
    if total_token_limit is not None and usage["total_tokens"] > total_token_limit:
        raise ValidationError("OpenAI Responses usage exceeds total_token_limit")

    from .primary_transports import _bound_research_result
    result = _bound_research_result(
        "openai", work, run_reference=run_reference, outcome=outcome,
    )
    result["usage"] = {
        "model": expected_model,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "cost_usd": None,
    }
    return validate_packet("result", result)


def responses_route_from_file(path: Path | str) -> OpenAIResponsesValidationRoute:
    """Load and validate an explicit Responses validation configuration file."""
    config_path = Path(path)
    if not config_path.is_absolute() or config_path.is_symlink() or not config_path.is_file():
        raise ValidationError("Responses validation config path must be an absolute regular file")
    raw = config_path.read_bytes()
    if not raw or len(raw) > 1_048_576:
        raise ValidationError("Responses validation config file size invalid")
    try:
        config = json.loads(raw)
    except Exception as exc:
        raise ValidationError("Responses validation config is not valid JSON") from exc
    if not isinstance(config, dict):
        raise ValidationError("Responses validation config must be a JSON object")

    required_keys = {"model", "api_url", "total_token_limit"}
    if not required_keys.issubset(config.keys()):
        raise ValidationError(f"Responses validation config missing required keys: {required_keys - set(config.keys())}")

    model = config["model"]
    api_url = config["api_url"]
    total_token_limit = config["total_token_limit"]
    if type(total_token_limit) is not int or total_token_limit < 1:
        raise ValidationError("Responses validation total_token_limit must be positive integer")

    if "api_key" in config:
        api_key = config["api_key"]
    elif "api_key_file" in config:
        key_path = Path(config["api_key_file"])
        if not key_path.is_absolute() or key_path.is_symlink() or not key_path.is_file():
            raise ValidationError("api_key_file must be an absolute regular file")
        api_key = key_path.read_text(encoding="utf-8").strip()
    else:
        raise ValidationError("Responses validation config requires api_key or api_key_file")
    if not api_key:
        raise ValidationError("Responses validation API key cannot be blank")

    meter_registry = None
    meter_enrollment_digest = config.get("meter_enrollment_digest", "")
    budget_authority = config.get("budget_authority")
    budget_limits = None
    if "budget_limits" in config:
        bl = config["budget_limits"]
        from .antigravity_team import ProviderAttemptBudget
        if isinstance(bl, ProviderAttemptBudget):
            budget_limits = bl
        elif isinstance(bl, dict):
            if "attempt_usd" in bl:
                budget_limits = ProviderAttemptBudget.from_usd(
                    project_id=bl["project_id"],
                    attempt_usd=bl["attempt_usd"],
                    task_usd=bl["task_usd"],
                    daily_usd=bl["daily_usd"],
                )
            else:
                budget_limits = ProviderAttemptBudget(
                    project_id=bl["project_id"],
                    attempt_microusd=bl["attempt_microusd"],
                    task_ceiling_microusd=bl["task_ceiling_microusd"],
                    daily_ceiling_microusd=bl["daily_ceiling_microusd"],
                )

    if "enrollment_file" in config:
        enroll_path = Path(config["enrollment_file"])
        if not enroll_path.is_absolute() or enroll_path.is_symlink() or not enroll_path.is_file():
            raise ValidationError("enrollment_file must be an absolute regular file")
        if "entitlements_postgres_dsn" in config:
            dsn = config["entitlements_postgres_dsn"]
            trusted_secret_file = config.get("trusted_verifier_secret_file")
            route_secret_file = config.get("route_verifier_secret_file")
            owner_secret_file = config.get("owner_authority_secret_file")
            if not (trusted_secret_file and route_secret_file and owner_secret_file):
                raise ValidationError("entitlements_postgres_dsn requires trusted, route, and owner secret files")
            schema = config.get("entitlements_schema", "director_entitlements_v1")
            primary_schema = config.get("primary_postgres_schema", "director_primary")

            from .entitlement_control_plane import (
                EntitlementControlPlaneStore,
                _bootstrap_schema,
                _load_pool_dependencies,
                _parse_connection_target,
                _read_secret_file,
                _require_schema,
                _storage_identity,
            )
            from psycopg.conninfo import conninfo_to_dict
            schema = _require_schema(schema)
            trusted_verifier_secret = _read_secret_file(trusted_secret_file)
            route_verifier_secret = _read_secret_file(route_secret_file)
            owner_authority_secret = _read_secret_file(owner_secret_file)
            if len({trusted_verifier_secret, route_verifier_secret, owner_authority_secret}) != 3:
                raise ValidationError("owner, install-verifier, and route-verifier secrets must be cryptographically separated")
            pool_type, dict_row = _load_pool_dependencies()
            parsed = _parse_connection_target(
                dsn,
                conninfo_to_dict=conninfo_to_dict,
                allow_insecure_local_transport=bool(config.get("allow_insecure_local_postgres", False)),
            )
            storage_identity = _storage_identity(parsed, schema=schema)
            pool = pool_type(
                conninfo=dsn,
                min_size=1,
                max_size=6,
                kwargs={"row_factory": dict_row, "autocommit": True},
                open=False,
            )
            pool.open(wait=True)
            try:
                with pool.connection() as connection, connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    _bootstrap_schema(connection, schema=schema, storage_identity=storage_identity, allow_create=False)
            except Exception:
                pool.close()
                raise

            store = EntitlementControlPlaneStore(
                pool,
                schema=schema,
                storage_identity=storage_identity,
                trusted_verifier_secret=trusted_verifier_secret,
                route_verifier_secret=route_verifier_secret,
                owner_authority_secret=owner_authority_secret,
            )
            from .antigravity_team import PrimaryProviderBudgetAuthority
            budget_authority = PrimaryProviderBudgetAuthority(store, primary_schema=primary_schema)
            from .response_meter import load_response_meter_registry
            meter_registry = load_response_meter_registry(enroll_path, authority=budget_authority, pool=pool)
            if not meter_enrollment_digest and meter_registry.enrollments:
                meter_enrollment_digest = digest(enroll_path.read_bytes())
        elif budget_authority is not None and "pool" in config:
            from .response_meter import load_response_meter_registry
            meter_registry = load_response_meter_registry(enroll_path, authority=budget_authority, pool=config["pool"])
            if not meter_enrollment_digest and meter_registry.enrollments:
                meter_enrollment_digest = digest(enroll_path.read_bytes())
        else:
            raise ValidationError("enrollment_file requires entitlements_postgres_dsn or explicit budget_authority and pool")

    from .primary_transports import OpenAIResponsesPacketTransport
    transport = OpenAIResponsesPacketTransport(
        api_url=api_url,
        api_key=api_key,
        model=model,
        response_meter=meter_registry,
        maximum_timeout_seconds=float(config.get("maximum_timeout_seconds", 180)),
    )

    return OpenAIResponsesValidationRoute(
        transport=transport,
        total_token_limit=total_token_limit,
        expected_model=model,
        meter_enrollment_digest=meter_enrollment_digest or digest(b"default-meter-enrollment"),
        expected_organization_id=config.get("organization_id"),
        expected_api_project_id=config.get("api_project_id"),
        canary_task_id=config.get("canary_task_id"),
        canary_assignment_id=config.get("canary_assignment_id"),
        canary_attempt_digest=config.get("canary_attempt_digest"),
        canary_work_packet_digest=config.get("canary_work_packet_digest"),
        budget_authority=budget_authority,
        budget_authority_digest=config.get("budget_authority_digest"),
        budget_limits=budget_limits,
    )


def responses_route_from_environment() -> OpenAIResponsesValidationRoute | None:
    """Resolve Responses validation route from environment, or None if unconfigured."""
    import os
    location = os.environ.get("DIRECTOR_PROVIDER_VALIDATION_OPENAI_RESPONSES_CONFIG")
    if not location:
        return None
    return responses_route_from_file(location)
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]]

