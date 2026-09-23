# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:26 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | BEGIN]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]]
"""A read-only, evidence-derived product scorecard; never an activation gate.

Saved reports and arbitrary JSON claims cannot become observations. Live probes
are bounded and fixed in this module. This is not a sandbox against Python code
running in the same process, nor an attestation of an uncompromised local host.
"""
from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

import httpx

from .state_witness import StateWitnessLedger, WitnessStatus, gh_checks_green

REPOSITORY = "edwardjones3976/director-orchestrator"
MAX_BYTES = 2 * 1024 * 1024
FRESH_FOR = timedelta(minutes=5)
_COLLECTED = object()
# Exact selectors. There is no default deployment and no committed channel.
_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
_CHANNEL = re.compile(r"C[A-Z0-9]{8,20}")
_TASK = re.compile(r"DIR-LG-[A-F0-9]{1,32}")
_TS = re.compile(r"\d{10,}\.\d{1,6}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
DRILL_KINDS = ("refusal-4xx", "reserved-5xx", "transport-failure", "duplicate-input", "ambiguous-post")
# Owner decision 2026-09-23: every runtime lane actually used must be its real product.
REAL_PRODUCTS = {"openai": {"openai-responses", "openai-codex"},
                 "grok": {"xai-grok-responses", "xai-grok-cli"},
                 "claude": {"anthropic-claude"}}
# Fixed, owner-installed verification key with no CLI override, the same
# stance as _native_security_index: a caller cannot nominate its own signer.
CI_REPLICA_KEY = Path.home() / ".director-scorecard" / "ci-replica-signer.ed25519.pub"
# Any of these from a source becomes UNKNOWN. Exception text is never reported:
# third-party errors can contain credentials or response bodies. ArithmeticError
# covers malformed decimal timestamps; ImportError an unavailable reader module.
_SOURCE_ERRORS = (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError,
                  ArithmeticError, ImportError, subprocess.SubprocessError, sqlite3.Error, httpx.HTTPError)
_SLACK_UNAVAILABLE = ("--channel not supplied or differs from the deployment's configured channel; "
                      "no other channel was read.")
_SLACK_SOURCE = "Slack conversations.replies and the deployment's authenticated Director readback"


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    scope: str
    action: str


CHECKS = (
    Check("01", "Correct project route and Slack channel", "runtime", "Verify the selected deployment's loopback Director and n8n ports, the --channel project channel and the correlated intake route together."),
    Check("02", "One active Slack gateway for this app", "runtime", "Count every Docker gateway sharing the app token and inspect known host gateway launch signatures."),
    Check("03", "Active intake handles rejection and ambiguity correctly", "runtime", "Execute the deployed 4xx, 5xx-after-reservation and transport-failure cases."),
    Check("04", "Status workflow actually delivers to Slack", "runtime", "Read back a fresh scheduled status message and correlate its workflow execution."),
    Check("05", "Primary owns intake with fencing", "runtime", "Observe LangGraph ownership, disabled legacy intake and a positive fencing generation."),
    Check("06", "Checkpoint survives restart", "runtime", "Resume the same bounded task after restart, preserving checkpoint and unique task identity."),
    Check("07", "Primary reconciliation is clear", "runtime", "Run the in-image read-only consistency command; it must report zero unresolved, ambiguous or conflicting records for the running generation."),
    Check("08", "Owner approval is authenticated and scoped", "runtime", "Verify a real signed owner decision against its delivered prompt and approved work packet."),
    Check("09", "Final reply is durable and unambiguous", "runtime", "Read back exactly one terminal reply; prove duplicate input and ambiguous-post recovery."),
    Check("10", "Ten actual offline Slack runs qualify", "runtime", "Run ten consecutive actual-owner Slack trials with independent correctness, time and zero-call evidence."),
    Check("11", "Every runtime lane used is its real product", "runtime", "Attest every lane the paid run used as its real product (OpenAI/Codex Responses, xAI Grok, Claude) from verified validation-store captures; no substitution (owner decision 2026-09-23)."),
    Check("12", "Native activation evidence is fresh", "runtime", "Verify current native captures, binding digests, identities and expiry through the activation verifier."),
    Check("13", "Rolling twenty admissions recompute reported resource bounds", "coordination", "Use the authoritative append-only assignment ledger, including failed and ambiguous attempts. The ledger does not authenticate operator-reported usage or attestations; it only recomputes reported bound violations from journal rows."),
    Check("14", "Attempts, time and output are bounded", "coordination", "Use the workload-ledger window: twenty post-cutover admissions whose bounds are all set and whose USD hard cap is verified."),
    Check("15", "Daily monetary cap and actual usage are enforced", "runtime", "Verify enrolled daily budget reservations and correlated actual charges; unknown cost is not zero."),
    Check("16", "Exact candidate CI really executed and passed", "candidate", "Run the six required jobs at this exact head on hosted or registered self-hosted runners with executed steps; if none executed, supply an owner-signed local-replica receipt (owner decision 2026-09-23)."),
    Check("17", "Independent exact-candidate ACCEPT exists", "candidate", "Verify one non-author review against this exact head and original product identity."),
    Check("18", "Exact-candidate Security scan is complete", "candidate", "Verify the sealed scan's manifest, coverage and findings disposition for this exact candidate."),
    Check("19", "Paid primary runtime and effect executor are active", "runtime", "Observe paid execution, primary runtime, effect executor and reconciliation worker active together."),
    Check("20", "One real paid Slack result qualifies", "runtime", "Verify an actual-owner result with independent correctness, wall time under 60 seconds and actual cost below USD 1."),
)


@dataclass(frozen=True)
class Observation:
    check_id: str
    passed: bool | None
    explanation: str
    source: str
    observed_at: datetime
    expires_at: datetime
    candidate_sha: str | None = None
    runtime_sha: str | None = None
    image_id: str | None = None
    digest: str = ""
    facts: Any = field(default_factory=dict)
    _origin: object = field(default=None, repr=False, compare=False)


def _observation(check_id: str, passed: bool | None, explanation: str,
                 source: str, facts: Any, at: datetime, **binding: Any) -> Observation:
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return Observation(check_id, passed, explanation, source, at, at + FRESH_FOR,
                       digest=digest, facts=facts, _origin=_COLLECTED, **binding)


def _sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("candidate SHA must be a full lowercase 40-hex commit")
    return value


def _when(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks a timezone")
    return parsed


def _run_bytes(arguments: list[str], timeout: int = 15) -> bytes:
    # No shell, secret-bearing stderr, retries or caller-provided commands.
    result = subprocess.run(arguments, capture_output=True, timeout=timeout, check=False)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError("read-only command failed or exceeded its output bound")
    return result.stdout


def _run_json(arguments: list[str], timeout: int = 15) -> Any:
    return json.loads(_run_bytes(arguments, timeout))


def _health(url: str, environment: str) -> dict[str, Any]:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with build_opener(NoRedirect).open(url, timeout=5) as response:
        if response.geturl() != url:
            raise ValueError("health endpoint redirected")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("health response exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("environment") != environment:
        raise ValueError("endpoint is not the selected deployment's Director")
    return value


def _all_fields(data: dict[str, Any], expected: dict[str, Any]) -> bool | None:
    if any(key not in data for key in expected):
        return None
    # Strict equality avoids bool(unknown string) and bool == 1 surprises.
    return all(type(data[key]) is type(wanted) and data[key] == wanted
               for key, wanted in expected.items())


@dataclass(frozen=True)
class _Runtime:
    project: str
    container_id: str
    image_id: str
    runtime_sha: str
    created_at: datetime
    started_at: datetime
    environment: str
    health_url: str
    api_base: str
    health: dict = field(repr=False)
    env: dict = field(repr=False)  # holds secrets: memory only, never in facts

    @property
    def binding(self) -> dict[str, str]:
        return {"runtime_sha": self.runtime_sha, "image_id": self.image_id}


def _labels(container: dict[str, Any] | None) -> dict[str, str]:
    return ((container or {}).get("Config") or {}).get("Labels") or {}


def _compose_ids(project: str, service: str) -> list[str]:
    """Running containers of one Compose service, discovered by labels only."""
    ids = _run_bytes(["docker", "ps", "-q", "--no-trunc",
                      "--filter", f"label=com.docker.compose.project={project}",
                      "--filter", f"label=com.docker.compose.service={service}"]).decode().split()
    if len(ids) > 16 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ValueError("Compose service inventory is malformed")
    return ids


def _runtime(at: datetime, project: str) -> tuple[list[Observation], _Runtime]:
    """Observe only the selected deployment's Director; never a default name or port."""
    ids = _compose_ids(project, "orchestrator")
    if len(ids) != 1:
        raise ValueError("the selected deployment has no single running Director")
    service = _run_json(["docker", "inspect", ids[0]])[0]
    labels = _labels(service)
    if (service.get("Id") != ids[0] or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != "orchestrator"
            or service.get("State", {}).get("Running") is not True):
        raise ValueError("inspected container is not the selected deployment's running Director")
    runtime_sha = _sha(labels.get("org.director.candidate.commit", ""))
    image_id = service.get("Image", "")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("runtime image identity unavailable")
    published = (service.get("NetworkSettings", {}).get("Ports") or {}).get("8080/tcp") or []
    port = str(published[0].get("HostPort", "")) if len(published) == 1 else ""
    if (len(published) != 1 or published[0].get("HostIp") != "127.0.0.1"
            or not re.fullmatch(r"[0-9]{4,5}", port) or not 1024 <= int(port) <= 65535):
        raise ValueError("Director is not published on exactly one loopback port")
    env = _environment(service)
    environment = env.get("APP_ENV", "").strip() or "local"
    health_url = f"http://127.0.0.1:{int(port)}/health"
    health = _health(health_url, environment)
    primary = health.get("langgraph_primary", {})
    if not isinstance(primary, dict):
        raise ValueError("primary health is malformed")
    runtime = _Runtime(project, ids[0], image_id, runtime_sha, _when(service["Created"]),
                       _when(service["State"]["StartedAt"]), environment, health_url,
                       f"http://127.0.0.1:{int(port)}", health, env)
    binding = runtime.binding
    result = []
    owner = _all_fields(primary, {"new_intake_owner": "langgraph", "legacy_new_intake_disabled": True})
    generation = primary.get("intake_fencing_generation")
    if owner is not None:
        owner = owner and type(generation) is int and generation > 0
    result.append(_observation("05", owner, "Observed database-derived intake owner and fencing health.",
                               health_url, {key: primary.get(key) for key in
                               ("new_intake_owner", "legacy_new_intake_disabled", "intake_fencing_generation")}, at, **binding))
    active = _all_fields(primary, {"primary_runtime_active": True, "effect_executor_ready": True,
                                   "reconciliation_worker_ready": True})
    if active is not None:
        active = active and health.get("paid_runs_enabled") is True
    result.append(_observation("19", active, "Observed paid switch and operational primary components together.",
                               health_url, {"paid": health.get("paid_runs_enabled"), "active": active}, at, **binding))
    return result, runtime


_HOST_GATEWAYS = r"""$ErrorActionPreference='Stop';
$p=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(pythonw?|node|pwsh|powershell)([0-9.]*)?\.exe$' });
$missing=@($p | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }).Count;
$matches=@($p | Where-Object { $_.CommandLine -match '(?i)(director_orchestrator\.slack_bot|[/\\]slack_bot\.py|slack[-_]socket[-_]gateway|slack[-_]gateway\.ps1)([\s"'']|$)' });
@{inspected=$p.Count;unreadable=$missing;gateway_process_count=$matches.Count} | ConvertTo-Json -Compress
"""
_UPSTREAM_HEALTH = """fetch('http://orchestrator:8080/health',{signal:AbortSignal.timeout(5000),redirect:'error'}).then(async r=>{if(!r.ok)throw Error('health'); const b=await r.text();if(b.length>1048576)throw Error('bound'); const h=JSON.parse(b);console.log(JSON.stringify({environment:h.environment}));}).catch(()=>{process.exitCode=1;});"""
_ACTIVE_INTAKE = """BEGIN READ ONLY;
SELECT json_build_object('active',w.active,'active_version',w."activeVersionId",'stored_version',h."versionId",
'http_node_count',(SELECT count(*) FROM json_array_elements(h.nodes::json) n WHERE n->>'type'='n8n-nodes-base.httpRequest'),
'static_request',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Create Director Task' AND n->>'type'='n8n-nodes-base.httpRequest' AND n->'parameters'->>'url' LIKE 'http%' AND n->'parameters'->>'url' NOT LIKE '%{{%'),
'supported_request',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Create Director Task' AND n->>'type'='n8n-nodes-base.httpRequest' AND n->'parameters'->>'method'='POST' AND n->'parameters'->>'url'='http://orchestrator:8080/v1/intake'),
'supported_webhook',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Slack Gateway Webhook' AND n->>'type'='n8n-nodes-base.webhook' AND n->'parameters'->>'httpMethod'='POST' AND n->'parameters'->>'path'='director-intake'),
'direct_connection',h.connections::json->'Slack Gateway Webhook'->'main'->0->0->>'node'='Create Director Task')
FROM workflow_entity w LEFT JOIN workflow_history h ON h."workflowId"=w.id AND h."versionId"=w."activeVersionId"
WHERE w.id='directorIntake'; COMMIT;"""
# Newest saved, successful, scheduled Director Status run that posted, within the
# 72-hour retention window; the Slack node's response is read from its data.
_STATUS_EXECUTION = """BEGIN READ ONLY;
SELECT json_build_object('workflows',(SELECT count(*) FROM workflow_entity WHERE name='Director Status'),
 'execution',(SELECT row_to_json(x) FROM (SELECT e.id,e.status,e.finished,e.mode,e."startedAt",e."stoppedAt",d.data
   FROM workflow_entity w JOIN execution_entity e ON e."workflowId"=w.id JOIN execution_data d ON d."executionId"=e.id
   WHERE w.name='Director Status' AND e.status='success' AND e.finished AND e.mode='trigger'
     AND e."stoppedAt" > now()-interval '72 hours' AND length(d.data)<=600000 AND d.data LIKE '%"Post Status"%'
   ORDER BY e."stoppedAt" DESC LIMIT 1) x)); COMMIT;"""


def _environment(container: dict[str, Any]) -> dict[str, str]:
    return dict(value.split("=", 1) for value in container.get("Config", {}).get("Env", []) if "=" in value)


def _loopback(container: dict[str, Any], port: int) -> bool:
    rows = (container.get("NetworkSettings", {}).get("Ports") or {}).get(f"{port}/tcp") or []
    return len(rows) == 1 and rows[0].get("HostIp") == "127.0.0.1"


def _container_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("expected gateway must be an exact Docker container name")
    return value


def _gateway_launch(container: dict[str, Any]) -> bool:
    command = (container.get("Config", {}).get("Entrypoint") or []) + (container.get("Config", {}).get("Cmd") or [])
    return ("director_orchestrator.slack_bot" in command
            or any(str(arg).replace("\\", "/").endswith("/slack_bot.py") for arg in command))


def _topology_observations(at: datetime, runtime: _Runtime, expected_gateway: str,
                           channel: str | None) -> list[Observation]:
    """Inspect every running container; the gateway is the caller's exact name, never a guess."""
    binding = runtime.binding
    ids = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if not ids or len(ids) > 128 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ValueError("running container inventory unavailable or exceeds 128")
    containers = _run_json(["docker", "inspect", *ids])
    after = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if set(ids) != set(after) or {row.get("Id") for row in containers} != set(ids):
        raise ValueError("container inventory changed during observation")
    by_name = {row.get("Name", "").lstrip("/"): row for row in containers}
    director = next((row for row in containers if row.get("Id") == runtime.container_id), None)
    if director is None:
        raise ValueError("Director left the running inventory during topology collection")
    if director.get("Image") != runtime.image_id:
        raise ValueError("Director image changed during topology collection")

    def service(name: str) -> list[dict[str, Any]]:
        return [row for row in containers if _labels(row).get("com.docker.compose.project") == runtime.project
                and _labels(row).get("com.docker.compose.service") == name]

    n8n_rows, db_rows = service("n8n"), service("n8n-db")
    n8n = n8n_rows[0] if len(n8n_rows) == 1 else None
    n8n_db = db_rows[0] if len(db_rows) == 1 else None
    gateway = by_name.get(expected_gateway)
    if gateway is None or gateway.get("State", {}).get("Running") is not True or not _gateway_launch(gateway):
        facts = {"expected_gateway": expected_gateway, "running": bool(gateway) and gateway.get("State", {}).get("Running") is True,
                 "gateway_launch": bool(gateway) and _gateway_launch(gateway)}
        return [_observation(key, None, "The expected gateway is not a running gateway container; no other container was substituted.",
                             "Docker running inventory", facts, at, **binding) for key in ("01", "02")]
    gateway_image = gateway.get("Image", "")
    if not isinstance(gateway_image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", gateway_image):
        raise ValueError("expected gateway image identity unavailable")
    gateway_identity = {"expected_gateway": expected_gateway, "gateway_container_id": gateway["Id"],
                        "gateway_image_id": gateway_image}
    gateway_env = _environment(gateway)
    gateway_project = _labels(gateway).get("com.docker.compose.project")
    in_deployment = None if gateway_project is None else gateway_project == runtime.project
    workflow = None
    upstream = {}
    if n8n is not None and n8n_db is not None:
        try:
            workflow = _run_json(["docker", "exec", n8n_db["Id"], "psql", "-U", "n8n", "-d", "n8n",
                                  "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", _ACTIVE_INTAKE])
            if (isinstance(workflow, dict) and workflow.get("supported_request") is True
                    and workflow.get("active_version") and workflow.get("active_version") == workflow.get("stored_version")):
                upstream = _run_json(["docker", "exec", n8n["Id"], "node", "-e", _UPSTREAM_HEALTH])
        except _SOURCE_ERRORS:
            workflow = None
    channel_ok = channel is not None and gateway_env.get("SLACK_DIRECTOR_CHANNEL_ID") == channel
    relay = urlsplit(gateway_env.get("N8N_DIRECTOR_WEBHOOK_URL", ""))
    n8n_name = (n8n or {}).get("Name", "").lstrip("/")
    route_ok = (relay.scheme == "http" and relay.hostname in {"n8n", *([n8n_name] if n8n_name else [])}
                and relay.port == 5678 and relay.path == "/webhook/director-intake"
                and not relay.username and not relay.password and not relay.query and not relay.fragment)
    facts = {**gateway_identity, "director_port_loopback": _loopback(director, 8080),
             "n8n_port_loopback": _loopback(n8n, 5678) if n8n is not None else None,
             "n8n_services_found": [len(n8n_rows), len(db_rows)], "gateway_in_deployment": in_deployment,
             "gateway_channel_matches": channel_ok, "gateway_n8n_route_matches": route_ok,
             "n8n_upstream_environment": upstream.get("environment"),
             "active_intake": workflow,
             "containers_running": all(row is not None and row.get("State", {}).get("Running") is True
                                       for row in (director, gateway, n8n))}
    route_pass = _all_fields(facts, {"director_port_loopback": True, "n8n_port_loopback": True, "gateway_channel_matches": True,
                                   "gateway_n8n_route_matches": True, "containers_running": True})
    workflow_ok = _all_fields(workflow or {}, {"active": True, "http_node_count": 1, "supported_request": True,
                                             "supported_webhook": True, "direct_connection": True})
    if workflow and workflow.get("supported_request") is False and workflow.get("static_request") is not True:
        workflow_ok = None  # An unsupported expression is not a proven wrong destination.
    if route_pass is True:
        route_pass = workflow_ok
        if route_pass is True:
            route_pass = (bool(workflow.get("active_version")) and workflow["active_version"] == workflow.get("stored_version")
                          and upstream.get("environment") == runtime.environment)
    explanation = ("Observed the selected deployment's loopback Director and n8n ports, the gateway's --channel and relay, "
                   "and the actual published intake version's supported forwarding URL, then checked that upstream's "
                   "environment. Missing/unsupported workflow evidence cannot pass. Full task delivery is scored separately.")
    if in_deployment is False:
        route_pass, explanation = False, "The expected gateway belongs to another Compose project."
    elif channel is None:
        route_pass, explanation = None, "--channel was not supplied; the project channel was not compared."
    elif n8n is None or n8n_db is None:
        route_pass, explanation = None, "The selected deployment does not run exactly one n8n and one n8n-db service."
    elif route_pass is not False and in_deployment is None:
        route_pass, explanation = None, "The expected gateway carries no Compose labels; deployment membership is unproven."
    result = [_observation("01", route_pass, explanation,
                           "Docker topology and n8n upstream GET /health", facts, at, **binding)]
    app_token = gateway_env.get("SLACK_APP_TOKEN", "")
    if not app_token:
        return result + [_observation("02", None, "Canonical gateway app identity unavailable.", "Docker gateway", gateway_identity, at, **binding)]
    fingerprint = hashlib.sha256(app_token.encode()).digest()
    matches, unknown = [], 0
    for row in containers:
        token = _environment(row).get("SLACK_APP_TOKEN", "")
        if not token or hashlib.sha256(token.encode()).digest() != fingerprint:
            continue
        command = (row.get("Config", {}).get("Entrypoint") or []) + (row.get("Config", {}).get("Cmd") or [])
        if _gateway_launch(row):
            matches.append(row.get("Id"))
        elif "director_orchestrator.main:app" not in command:
            unknown += 1
    host = None
    try:
        host = _run_json(["powershell", "-NoProfile", "-NonInteractive", "-Command", _HOST_GATEWAYS])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    passed = False if len(matches) != 1 else None
    if len(matches) == 1 and unknown == 0 and isinstance(host, dict):
        if type(host.get("unreadable")) is int and type(host.get("gateway_process_count")) is int and host["unreadable"] == 0 and host["gateway_process_count"] == 0:
            passed = True
    inventory = {**gateway_identity, "running_containers_inspected": len(containers),
                 "matching_docker_gateways": len(matches), "unclassified_same_app_containers": unknown, "host_inventory": host}
    result.append(_observation("02", passed, "Compared app-token fingerprints in memory across all running containers and inspected known Windows gateway launch signatures. Unreadable/unclassified or host gateway processes require reconciliation; custom or remote launches are not attested.",
                               "Docker running inventory and Windows Win32_Process", inventory, at, **binding))
    return result


def _trial_module():
    """A fresh private instance of the offline collector for one collection."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "collect-offline-slack-trials.py"
    spec = importlib.util.spec_from_file_location("director_scorecard_trials", path)
    if spec is None or spec.loader is None:
        raise OSError("offline trial collector unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SlackThreads:
    """Bounded GET-only Slack and Director reads for the selected deployment.

    The collector module is a private instance whose API allowlist names only
    this deployment. Credentials come from the Director container's environment
    and stay in memory; the expected channel comes only from --channel.
    """

    def __init__(self, runtime: _Runtime, channel: str | None, *, client: httpx.Client | None = None):
        if channel is None or runtime.env.get("SLACK_DIRECTOR_CHANNEL_ID") != channel:
            raise ValueError("channel differs from deployment")
        self.m = _trial_module()
        self.m.API = runtime.api_base
        self.env = {"SLACK_DIRECTOR_CHANNEL_ID": channel,
                    **{key: runtime.env[key] for key in ("SLACK_BOT_TOKEN", "ORCHESTRATOR_SHARED_SECRET", "SLACK_OWNER_USER_ID")}}
        self.client = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
        try:
            self.reads = self.m.Collector(self.client, None, self.env)
            auth, _ = self.reads.get(self.m.SLACK + "auth.test")
            if (auth.get("ok") is not True or not re.fullmatch(r"[UW][A-Z0-9]+", str(auth.get("user_id", "")))
                    or not re.fullmatch(r"B[A-Z0-9]+", str(auth.get("bot_id", "")))):
                raise ValueError("native Slack identity unavailable")
        except Exception:
            self.client.close()
            raise
        self.bot, self.owner, self.api, self.channel = auth["user_id"], self.env["SLACK_OWNER_USER_ID"], runtime.api_base, channel

    def replies(self, thread: str):
        return self.reads.replies(thread)

    def task(self, task_id: str):
        return self.reads.get(self.api + "/v1/tasks/" + task_id, params=dict(self.m.READBACK_QUERY))

    def deliveries(self, task_id: str):
        return self.reads.get(self.api + "/v1/primary/tasks/" + task_id + "/deliveries")[0]

    def _get(self, url: str, params: dict[str, str], token: str) -> tuple[int, bytes]:
        if time.monotonic() >= self.reads.deadline:
            raise ValueError("collection deadline reached")
        with self.client.stream("GET", url, params=params, headers={"Authorization": "Bearer " + token}) as response:
            body = bytearray()
            for chunk in response.iter_bytes(8192):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("response exceeds its bound")
            return response.status_code, bytes(body)

    def history(self, oldest: str, latest: str) -> list[dict[str, Any]]:
        """Top-level --channel messages between two timestamps, inclusive; at most five pages."""
        messages, cursor = [], None
        for _ in range(5):
            params = {"channel": self.channel, "oldest": oldest, "latest": latest, "inclusive": "true", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            status, body = self._get(self.m.SLACK + "conversations.history", params, self.env["SLACK_BOT_TOKEN"])
            page = json.loads(body) if status == 200 else None
            if not isinstance(page, dict) or page.get("ok") is not True or not isinstance(page.get("messages"), list):
                raise ValueError("channel history unavailable")
            messages.extend(page["messages"])
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not page.get("has_more") and not cursor:
                return messages
        raise ValueError("channel history exceeds its bound")

    def binding(self, thread: str) -> str | None:
        """200 -> task ID, 404 -> None, 409 -> "AMBIGUOUS"; anything else is unavailable."""
        status, body = self._get(self.api + "/v1/slack-thread-bindings",
                                 {"channel_id": self.channel, "thread_ts": thread}, self.env["ORCHESTRATOR_SHARED_SECRET"])
        if status == 404:
            return None
        if status == 409:
            return "AMBIGUOUS"
        if status != 200:
            raise ValueError("thread binding unavailable")
        value = json.loads(body)
        task_id = value.get("task_id") if isinstance(value, dict) else None
        if (not isinstance(task_id, str) or not _TASK.fullmatch(task_id)
                or value.get("channel_id") != self.channel or value.get("thread_ts") != thread):
            raise ValueError("thread binding does not echo this thread")
        return task_id

    def close(self) -> None:
        self.client.close()


# The offline collector's native receipt wording (REQUEST RECEIVED, legacy ACCEPTED).
_RECEIPT = re.compile(r"(?<![A-Z0-9-])(DIR-LG-[A-F0-9]+)\s+—\s+(?:ACCEPTED|REQUEST RECEIVED)\b")


def _thread_counts(t: _SlackThreads, thread: str, messages: list[dict[str, Any]]) -> tuple[int, list[str], int]:
    owner_inputs = sum(1 for m in messages if m.get("user") == t.owner and not m.get("bot_id") and not m.get("subtype")
                       and (m.get("ts") == thread or m.get("thread_ts") == thread))
    bot = [html.unescape(str(m.get("text", ""))) for m in messages
           if m.get("user") == t.bot and m.get("thread_ts") == thread]
    receipts = [match.group(1) for text in bot for match in _RECEIPT.finditer(text)]
    blocked = sum(1 for text in bot if "DIRECTOR — BLOCKED" in text)
    return owner_inputs, receipts, blocked


def _task_facts(t: _SlackThreads, thread: str, task_id: str, replies=None) -> dict[str, Any]:
    """One thread snapshot plus the task's typed readback, summarized by the offline collector."""
    messages, complete, _ = replies or t.replies(thread)
    task, headers = t.task(task_id)
    deliveries = t.deliveries(task_id)
    row = t.m.summarize(thread, task_id, task, deliveries, messages, complete, t.owner, t.bot, headers, t.channel)
    readback, latest, state, _ = t.m._readback(task_id, task)
    collected = state == "collected"
    return {"task_id": task_id, "row": row, "task": task if isinstance(task, dict) else {}, "messages": messages,
            "complete": complete, "readback": readback if collected else None, "latest": latest if collected else None}


_FAIL_COUNTS = ("unresolved_reconciliation", "ambiguous_assignments", "tasks_requiring_reconciliation",
                "terminal_tasks_open_assignments", "terminal_tasks_open_effects",
                "projection_batch_digest_conflicts", "projections_ahead_of_fence")
_INFLIGHT_COUNTS = ("open_effect_reservations", "active_assignments", "live_writer_claims",
                    "batches_pending_projection_binding")
_CONSISTENCY_SOURCE = "docker exec <Director> python -m director_orchestrator.primary_generation consistency (READ ONLY)"
_CAPTURES_SOURCE = "docker exec <Director> python -m director_orchestrator.primary_generation activation-captures"


def _reconciliation_observation(at: datetime, runtime: _Runtime, consistency: dict | None) -> Observation:
    primary = runtime.health.get("langgraph_primary", {})
    binding = runtime.binding
    blocked = (any(type(primary.get(key)) is int and primary[key] > 0
                   for key in ("ambiguous_effect_count", "historical_parallel_plan_hold_count"))
               or isinstance(primary.get("reconciliation_hold_reason"), str)
               and bool(primary["reconciliation_hold_reason"].strip()))
    health_facts = {key: primary.get(key) for key in
                    ("ambiguous_effect_count", "historical_parallel_plan_hold_count", "reconciliation_hold_reason")}
    if blocked:
        return _observation("07", False, "Health reports a historical hold, hold reason or ambiguous primary effect.",
                            runtime.health_url, health_facts, at, **binding)
    # Zero health counters are never consistency proof: release 879 reported
    # exactly these while authoritative task/batch plan digests conflicted.
    if consistency is None:
        return _observation("07", None, "The in-image read-only consistency command was unavailable; health hold counters "
                            "cannot prove reconciliation. Worker readiness is scored in 19.",
                            runtime.health_url, health_facts, at, **binding)
    counts = consistency.get("counts")
    keys = _FAIL_COUNTS + _INFLIGHT_COUNTS
    if not isinstance(counts, dict) or any(not _counter(counts.get(key)) for key in keys):
        return _observation("07", None, "The consistency command returned malformed counts.",
                            _CONSISTENCY_SOURCE, health_facts, at, **binding)
    generation = consistency.get("generation") if isinstance(consistency.get("generation"), dict) else {}
    fence = consistency.get("intake_fencing_generation")
    facts = {"counts": {key: counts[key] for key in keys}, "manifest_sha256": generation.get("manifest_sha256"),
             "intake_fencing_generation": fence, "captured_at": consistency.get("captured_at")}
    failing = [key for key in _FAIL_COUNTS if counts[key] > 0]
    if failing:
        return _observation("07", False, "Authoritative records are unresolved, ambiguous or conflicting: " + ", ".join(failing) + ".",
                            _CONSISTENCY_SOURCE, facts, at, **binding)
    pin = generation.get("manifest_sha256")
    if (not isinstance(pin, str) or not _HEX64.fullmatch(pin)
            or pin != runtime.env.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256")
            or type(fence) is not int or fence != primary.get("intake_fencing_generation")):
        return _observation("07", None, "The consistency snapshot is not bound to the running generation pin and intake fence.",
                            _CONSISTENCY_SOURCE, facts, at, **binding)
    busy = [key for key in _INFLIGHT_COUNTS if counts[key] > 0]
    if busy:
        return _observation("07", None, "Work is in flight (" + ", ".join(busy) + "); observe again when quiescent.",
                            _CONSISTENCY_SOURCE, facts, at, **binding)
    return _observation("07", True, "The in-image read-only consistency snapshot reports zero unresolved, ambiguous or "
                        "conflicting records for the running generation and fence.", _CONSISTENCY_SOURCE, facts, at, **binding)


def _capture_verdict(at: datetime, runtime: _Runtime, captures: dict) -> tuple[bool | None, str, dict]:
    pin = runtime.env.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256")
    if not pin or captures.get("generation_manifest_sha256") != pin:
        return None, "The captures are not bound to the running generation pin.", {}
    raw = runtime.env.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON")
    if raw is None:
        return None, "The running provider bindings are unavailable.", {}
    if captures.get("provider_bindings_sha256") != hashlib.sha256(raw.encode()).hexdigest():
        return False, "The captures are bound to other provider bindings than the running ones.", {}
    lanes = captures.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        return None, "No activation lanes were reported.", {}
    facts, stale = {}, []
    for lane, value in sorted(lanes.items()):
        surfaces = value["surfaces"]
        ok = isinstance(value.get("binding_digest"), str) and bool(_HEX64.fullmatch(value["binding_digest"]))
        lane_facts = {"binding_digest": value.get("binding_digest")}
        for name in ("app", "runtime_adapter"):
            surface = surfaces.get(name)
            if surface is None:
                ok, lane_facts[name] = False, None
                continue
            expires = _when(surface["expires_at"])
            lane_facts[name] = {"stage": surface.get("stage"), "expires_at": expires.isoformat(),
                                "capture_verified": surface.get("capture_verified")}
            if surface.get("stage") != "LIVE_OK" or surface.get("capture_verified") is not True or expires < at + FRESH_FOR:
                ok = False
        facts[lane] = lane_facts
        if not ok:
            stale.append(lane)
    if stale:
        return False, "Missing, unverified, non-LIVE_OK or expiring captures for: " + ", ".join(stale) + ".", facts
    return True, ("Every lane's app and runtime-adapter captures are LIVE_OK, natively verified, unexpired and bound "
                  "to the manifest's provider_bindings_sha256."), facts


def _capture_observations(at: datetime, runtime: _Runtime, captures: dict | None,
                          paid: dict | None) -> list[Observation]:
    binding = runtime.binding
    activation = runtime.health.get("langgraph_primary", {}).get("provider_activation", {})
    if isinstance(activation, dict) and activation.get("combined_live_ready") is False:
        obs12 = _observation("12", False, "The running activation verifier reports combined_live_ready=false.",
                             runtime.health_url, {"combined_live_ready": False}, at, **binding)
    elif captures is None:
        # A readiness flag never exposes capture identity or expiry.
        obs12 = _observation("12", None, "The in-image activation-captures command was unavailable; a readiness flag "
                             "alone never attests captures.", runtime.health_url, {}, at, **binding)
    else:
        try:
            passed, explanation, facts = _capture_verdict(at, runtime, captures)
        except _SOURCE_ERRORS:
            passed, explanation, facts = None, "The activation captures were malformed.", {}
        obs12 = _observation("12", passed, explanation, _CAPTURES_SOURCE, facts, at, **binding)
    latest = (paid or {}).get("latest")
    if not latest or captures is None:
        obs11 = _observation("11", None, "Requires the paid thread's collected readback with provider attempts and the "
                             "in-image activation captures.", _CAPTURES_SOURCE, {}, at, **binding)
    else:
        obs11 = _product_observation(at, runtime, captures, paid)
    return [obs12, obs11]


def _product_observation(at: datetime, runtime: _Runtime, captures: dict, paid: dict) -> Observation:
    """11: the captures that attest each lane are the ones that gated the paid run's attempts."""
    binding = runtime.binding
    attempts = paid["readback"]["provider_attempts"]
    used = sorted({str(event["provider_id"]) for event in paid["latest"].values()})
    lanes = captures.get("lanes") if isinstance(captures.get("lanes"), dict) else {}
    products, transports, unbound = {}, {}, []
    for lane in used:
        value = lanes.get(lane) if isinstance(lanes.get(lane), dict) else {}
        products[lane] = value.get("product")
        times = [_when(event["recorded_at"]) for event in attempts if event["provider_id"] == lane]
        transports[lane] = sorted({str(event["transport_id"]) for event in attempts if event["provider_id"] == lane})
        surface = (value.get("surfaces") or {}).get("runtime_adapter")
        # A capture minted after the attempt, or expired before it ended, did not gate it.
        if (not isinstance(surface, dict) or not times or not surface.get("observed_at")
                or not _when(surface["observed_at"]) <= min(times) <= max(times) < _when(surface["expires_at"])):
            unbound.append(lane)
    facts = {"lanes_used": used, "products": products, "transports": transports}
    raw = runtime.env.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON")
    if (runtime.created_at > _when(paid["readback"]["task_created_at"]) or raw is None
            or captures.get("generation_manifest_sha256") != runtime.env.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256")
            or captures.get("provider_bindings_sha256") != hashlib.sha256(raw.encode()).hexdigest()):
        return _observation("11", None, "The paid task predates the running container, or the captures are not bound to "
                            "its generation and provider bindings; they cannot attest that run.",
                            _CAPTURES_SOURCE, facts, at, **binding)

    def attested(lane: str) -> bool:
        if lane not in REAL_PRODUCTS or products[lane] not in REAL_PRODUCTS[lane]:
            return False
        # The OpenAI lane has two real products; the capture must be the one the attempts' transport used.
        return lane != "openai" or {"openai-responses" if transport == "responses-api" else "openai-codex"
                                    for transport in transports[lane]} == {products[lane]}

    substituted = [lane for lane in used if not attested(lane)]
    if substituted:
        return _observation("11", False, "Lanes used without an attested real product: " + ", ".join(substituted) + ".",
                            _CAPTURES_SOURCE, facts, at, **binding)
    if unbound:
        return _observation("11", None, "No capture observed before and unexpired through the paid attempts of: "
                            + ", ".join(unbound) + ".", _CAPTURES_SOURCE, facts, at, **binding)
    return _observation("11", True, "Every lane the paid run used is attested as its real product by a verified "
                        "validation-store capture that was current when its attempts ran, under the running "
                        "generation and provider bindings.", _CAPTURES_SOURCE, facts, at, **binding)


def _acceptance_observation(at: datetime, candidate_sha: str, consistency: dict | None) -> Observation:
    source = _CONSISTENCY_SOURCE + " generation.adoption"
    generation = (consistency or {}).get("generation")
    generation = generation if isinstance(generation, dict) else {}
    adoption = generation.get("adoption")
    if (consistency is None or generation.get("acceptance_mode") != "owner-local-evidence-v1"
            or not isinstance(adoption, dict)):
        return _observation("17", None, "No adopted owner-local acceptance artifact was read for the running generation.",
                            source, {}, at, candidate_sha=candidate_sha)
    facts = {key: adoption.get(key) for key in ("repository", "pull_request_number", "pull_request_author",
             "independent_reviewer", "security_reviewer", "adoption_digest", "reviewed_source_tree")}
    reviewed = adoption.get("reviewed_source_commit")
    bound = reviewed if isinstance(reviewed, str) and re.fullmatch(r"[a-f0-9]{40}", reviewed) else None
    actors = [adoption.get(key) for key in ("pull_request_author", "independent_reviewer", "security_reviewer")]
    if all(isinstance(actor, str) and actor.strip() for actor in actors) and actors[0].casefold() in (
            actors[1].casefold(), actors[2].casefold()):
        return _observation("17", False, "The adopted acceptance names the pull request author as a reviewer.",
                            source, facts, at, candidate_sha=bound)
    if bound is None or not all(isinstance(actor, str) and actor.strip() for actor in actors):
        return _observation("17", None, "The adopted acceptance does not name a full reviewed source commit.",
                            source, facts, at, candidate_sha=candidate_sha)
    return _observation("17", True, "The running generation's pinned owner-local adoption accepts this reviewed source "
                        "commit with independent and security reviewers distinct from the author.",
                        source, facts, at, candidate_sha=bound)


def _flatted(text: str) -> Any:
    """Decode n8n's flatted execution data: strings inside containers are table indices."""
    table = json.loads(text)
    if not isinstance(table, list) or not table:
        raise ValueError("execution data is not a flatted table")
    memo: dict[int, Any] = {}

    def resolve(index: int) -> Any:
        if not 0 <= index < len(table):
            raise ValueError("flatted reference out of range")
        if index in memo:
            return memo[index]
        value = table[index]
        if isinstance(value, dict):
            out: Any = {}
            memo[index] = out
            for key, member in value.items():
                out[key] = resolve(int(member)) if isinstance(member, str) else member
            return out
        if isinstance(value, list):
            out = []
            memo[index] = out
            out.extend(resolve(int(member)) if isinstance(member, str) else member for member in value)
            return out
        return value

    try:
        return resolve(0)
    except RecursionError:
        raise ValueError("execution data nests beyond its bound") from None


def _status_observation(at: datetime, runtime: _Runtime, t: _SlackThreads) -> Observation:
    binding = runtime.binding
    source = "n8n Director Status saved execution (READ ONLY) and Slack conversations.replies"
    databases = _compose_ids(runtime.project, "n8n-db")
    if len(databases) != 1:
        return _observation("04", None, "The selected deployment does not run exactly one n8n-db service.",
                            source, {}, at, **binding)
    found = _run_json(["docker", "exec", databases[0], "psql", "-U", "n8n", "-d", "n8n",
                       "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", _STATUS_EXECUTION])
    if not isinstance(found, dict) or found.get("workflows") != 1 or not isinstance(found.get("execution"), dict):
        return _observation("04", None, "No single Director Status workflow has a saved successful posting execution "
                            "in the last 72 hours.", source, {}, at, **binding)
    execution = found["execution"]
    out = _flatted(execution["data"])["resultData"]["runData"]["Post Status"][-1]["data"]["main"][0][0]["json"]
    ts = out.get("ts") or (out.get("message") or {}).get("ts")
    started, stopped = _when(execution["startedAt"]), _when(execution["stoppedAt"])
    well_formed = isinstance(ts, str) and bool(_TS.fullmatch(ts))
    facts = {"execution_id": execution.get("id"), "stopped_at": stopped.isoformat(), "message_ts": ts if well_formed else None}
    if (out.get("ok") is not True or not well_formed or out.get("channel") != t.channel
            or not Decimal(str(started.timestamp())) <= Decimal(ts) <= Decimal(str(stopped.timestamp())) + 1):
        return _observation("04", None, "The saved execution's Slack response is not a correlated post to the project channel.",
                            source, facts, at, **binding)
    messages, complete, _ = t.replies(ts)
    posted = [m for m in messages if m.get("ts") == ts]
    status = [m for m in posted if m.get("bot_id") and html.unescape(str(m.get("text", ""))).startswith("*STATUS —")]
    if complete and len(posted) == 1 and len(status) == 1:
        return _observation("04", True, "A saved successful scheduled execution's Slack post was read back as the bot's "
                            "STATUS message in the project channel.", source, facts, at, **binding)
    if complete and posted and not status:
        return _observation("04", False, "The message at the execution's posted timestamp is not the bot's STATUS message.",
                            source, facts, at, **binding)
    return _observation("04", None, "The status message readback was incomplete.", source, facts, at, **binding)


def _drill_verdict(t: _SlackThreads, kind: str, thread: str) -> tuple[bool | None, dict[str, Any]]:
    messages, complete, _ = t.replies(thread)
    owner_inputs, receipts, blocked = _thread_counts(t, thread, messages)
    bound = t.binding(thread)
    status = 404 if bound is None else 409 if bound == "AMBIGUOUS" else 200
    facts: dict[str, Any] = {"owner_inputs": owner_inputs, "blocked": blocked, "receipts": len(receipts),
                             "binding_status": status, "thread_tasks": None}
    snapshot = _task_facts(t, thread, bound, (messages, complete, None)) if status == 200 and kind != "refusal-4xx" else None
    readback = (snapshot or {}).get("readback")
    tasks = readback["task_scope_counters"]["thread_tasks"] if readback else None
    replies = snapshot["row"]["terminal_reply_count"] if snapshot else None
    facts.update(thread_tasks=tasks, terminal_reply_count=replies)
    verdict: bool | None = None
    if kind == "refusal-4xx":
        if status != 404 or receipts:
            verdict = False
        elif complete and owner_inputs == 1 and blocked >= 1:
            verdict = True
    elif kind == "reserved-5xx":
        if status == 409 or (tasks is not None and tasks > 1):
            verdict = False
        elif status == 200 and complete and owner_inputs == 1 and blocked >= 1 and tasks == 1:
            verdict = True
    elif kind == "transport-failure":
        # No status reached the gateway: the task may or may not exist, but never twice.
        # A receipt means the relay answered 202, so the failure was not exercised.
        if status == 409 or (tasks is not None and tasks > 1):
            verdict = False
        elif (complete and owner_inputs == 1 and blocked >= 1 and not receipts
              and (status == 404 or (status == 200 and tasks == 1))):
            verdict = True
    elif kind == "duplicate-input":
        if status == 409 or len(set(receipts)) > 1 or (tasks or 0) > 1 or (replies or 0) > 1:
            verdict = False
        elif (len(receipts) >= 2 and status == 200 and complete and owner_inputs == 1
              and set(receipts) == {bound} and tasks == 1 and replies == 1):
            verdict = True
    else:
        terminal = [r for r in readback["slack_deliveries"] if r.get("notification_key") == "terminal"] if readback else []
        row = terminal[0] if len(terminal) == 1 else {}
        facts.update(terminal_rows=len(terminal), terminal_state=row.get("state"))
        if (replies or 0) > 1 or (row.get("state") == "AMBIGUOUS" and row.get("readback_attempts") == 2):
            verdict = False
        elif (status == 200 and row.get("state") == "DELIVERED" and row.get("acknowledged_at") is None
              and row.get("delivered_at") and row.get("slack_ts") and replies == 1
              and snapshot["row"].get("final_receipt_matches_native_message") is True):
            verdict = True
    facts["verdict"] = verdict
    return verdict, facts


def _drill_observations(at: datetime, runtime: _Runtime, t: _SlackThreads,
                        drills: dict[str, str]) -> list[Observation]:
    verdicts: dict[str, bool | None] = {}
    facts: dict[str, Any] = {}
    for kind, thread in drills.items():
        try:
            verdicts[kind], facts[kind] = _drill_verdict(t, kind, thread)
        except _SOURCE_ERRORS:
            verdicts[kind], facts[kind] = None, {"verdict": None, "source_unavailable": True}
    result = []
    for check, kinds, proven in (
            ("03", ("refusal-4xx", "reserved-5xx", "transport-failure"),
             "The 4xx refusal created no task or receipt, the 5xx after reservation kept exactly one task, and the "
             "relay transport failure was reported BLOCKED with no receipt and at most one task."),
            ("09", ("duplicate-input", "ambiguous-post"),
             "Duplicate input kept one task and one final reply; the ambiguous post was recovered by readback "
             "to exactly one delivered final reply.")):
        values = [verdicts.get(kind) for kind in kinds]
        missing = [kind for kind in kinds if kind not in drills]
        if any(value is False for value in values):
            passed, explanation = False, "A drill thread shows a refused, duplicated or ambiguous outcome: " + ", ".join(
                kind for kind in kinds if verdicts.get(kind) is False) + "."
        elif all(value is True for value in values):
            passed, explanation = True, proven
        else:
            passed = None
            explanation = ("Missing --drill-thread selections: " + ", ".join(missing) + "." if missing else
                           "A drill was not exercised or its evidence was incomplete or unavailable.")
        result.append(_observation(check, passed, explanation, _SLACK_SOURCE,
                                   {kind: facts.get(kind) for kind in kinds}, at, **runtime.binding))
    return result


def _approval_observations(at: datetime, runtime: _Runtime, t: _SlackThreads, task_id: str,
                           consistency: dict | None) -> list[Observation]:
    binding = runtime.binding
    task, _ = t.task(task_id)
    thread = ((task or {}).get("intake") or {}).get("thread_ts")
    if not isinstance(thread, str) or not _TS.fullmatch(thread):
        raise ValueError("approval task has no Slack thread")
    snapshot = _task_facts(t, thread, task_id)
    row, readback = snapshot["row"], snapshot["readback"]
    if readback is None:
        reason = "The approval task's typed readback was not collected complete."
        return [_observation(key, None, reason, _SLACK_SOURCE, {}, at, **binding) for key in ("06", "08")]
    counters = readback["task_scope_counters"]
    ordinals: dict[str, set[int]] = {}
    for event in readback["provider_attempts"]:
        ordinals.setdefault(event["assignment_id"], set()).add(event["attempt_ordinal"])
    redispatched = sorted(key for key, values in ordinals.items() if len(values) > 1)
    replies = row.get("terminal_reply_count")
    terminal = [r for r in readback["slack_deliveries"] if r.get("notification_key") == "terminal"]
    facts06 = {"thread_tasks": counters["thread_tasks"], "redispatched_assignments": len(redispatched),
               "terminal_reply_count": replies, "terminal_classification": readback["terminal_classification"],
               "process_started_at": runtime.started_at.isoformat(), "task_created_at": readback["task_created_at"],
               "terminal_delivery_created_at": terminal[0]["created_at"] if len(terminal) == 1 else None}
    if counters["thread_tasks"] > 1 or redispatched or (replies or 0) > 1:
        obs06 = _observation("06", False, "The approval task was duplicated, redispatched or replied to more than once.",
                             _SLACK_SOURCE, facts06, at, **binding)
    else:
        spans = (len(terminal) == 1
                 and _when(readback["task_created_at"]) < runtime.started_at < _when(terminal[0]["created_at"]))
        settled = all(a.get("status") in ("SUCCEEDED", "FAILED") for a in readback["assignments"])
        resumed = (readback["terminal_classification"] is not None and counters["thread_tasks"] == 1 and spans and settled
                   and counters["started_attempts"] == counters["attempt_ordinals"]
                   and row["qualification_evidence"].get("effect_state") != "AMBIGUOUS_DELIVERY_EFFECT"
                   and replies == 1 and row.get("final_receipt_matches_native_message") is True)
        obs06 = _observation("06", True if resumed else None,
                             "One task spanned the current Director process start and finished with one dispatch per "
                             "assignment and one final reply." if resumed else
                             "The task did not demonstrably span the current process start, or it is unsettled.",
                             _SLACK_SOURCE, facts06, at, **binding)
    approvals = [r for r in readback["slack_deliveries"]
                 if t.m.notification_key_family(r.get("notification_key")) == "approval"]
    messages, complete = snapshot["messages"], snapshot["complete"]
    entry = ((consistency or {}).get("tasks") or {}).get(task_id)
    receipts = entry.get("approval_receipts") if isinstance(entry, dict) else None
    receipts = receipts if isinstance(receipts, list) else []
    failures, gaps = [], 0
    for delivery in approvals:
        slack_ts = delivery.get("slack_ts")
        decided = _when(delivery["owner_decision_at"]) if delivery.get("owner_decision_at") else None
        prompt = [m for m in messages if slack_ts and m.get("ts") == slack_ts]
        if delivery.get("owner_decision") == "approve" and delivery.get("state") != "DELIVERED":
            failures.append("decision recorded on an undelivered prompt")
        if decided is not None and slack_ts and Decimal(str(decided.timestamp())) < Decimal(slack_ts):
            failures.append("decision precedes its prompt")
        if complete and prompt and any(m.get("user") != t.bot for m in prompt):
            failures.append("prompt message is from another user")
        native = [m for m in prompt if m.get("client_msg_id") == delivery.get("client_msg_id") and m.get("user") == t.bot]
        command = hashlib.sha256(str(delivery.get("owner_command_id")).encode()).hexdigest()
        # The control plane's own approval_resume receipt, re-read, not the delivery row's copy of it.
        receipted = decided is not None and any(
            isinstance(receipt, dict) and receipt.get("command_id_sha256") == command
            and isinstance(receipt.get("recorded_at"), str) and _when(receipt["recorded_at"]) == decided
            for receipt in receipts)
        if not (delivery.get("state") == "DELIVERED" and slack_ts and delivery.get("owner_decision") == "approve"
                and delivery.get("owner_command_id") and decided is not None
                and Decimal(slack_ts) <= Decimal(str(decided.timestamp()))
                and str(delivery.get("notification_key", "")).endswith(":" + str(delivery.get("projection_digest")))
                and len(native) == 1 and receipted):
            gaps += 1
    facts08 = {"approval_rows": len(approvals), "task_record_bound": row.get("task_record_bound"),
               "task_api_gateway_attested": row.get("task_api_gateway_attested"),
               "failures": sorted(set(failures)), "unproven_rows": gaps}
    if failures:
        passed08, explanation08 = False, "An owner decision is not bound to its delivered bot prompt: " + "; ".join(sorted(set(failures))) + "."
    elif (row.get("task_record_bound") is True and row.get("task_api_gateway_attested") is True and approvals and not gaps):
        passed08, explanation08 = True, ("Every approval prompt was delivered by the bot and read back, then approved later by "
                                         "an approval_resume command receipt re-read from the control plane whose command "
                                         "and time match the digest-bound decision. The gateway authenticates the owner.")
    else:
        passed08, explanation08 = None, "No complete, digest-bound owner approval was observed for this task."
    return [obs06, _observation("08", passed08, explanation08, _SLACK_SOURCE, facts08, at, **binding)]


def _run_record(runtime: _Runtime, snapshot: dict[str, Any], correctness: bool, label: str) -> dict[str, Any]:
    """A RUN_RECORD_FIELDS row built only from task-level evidence, never the mode switch."""
    row, readback = snapshot["row"], snapshot["readback"]
    counters = readback["task_scope_counters"] if readback else None
    finals = row.get("terminal_messages") or []
    offline = (row.get("all_recorded_outcomes_are_offline_doubles") is True
               and row.get("zero_calls_in_exposed_task_projection") is True and counters is not None
               and counters["attempt_ordinals"] == 0 and len(finals) == 1 and finals[0].get("simulation_label_present") is True)
    states = {str(receipt.get("state")) for receipt in row.get("delivery_receipts") or []}
    if (row.get("final_receipt_matches_native_message") is True and row.get("delivery_receipt_list_complete") is True
            and states <= {"DELIVERED", "SUPERSEDED", "SUPPRESSED"}):
        delivery_ambiguous: bool | None = False
    else:
        delivery_ambiguous = True if "AMBIGUOUS" in states else None
    # Only a container that already existed when the task was created can be
    # the image that executed it; no producer records the executing image.
    bound = readback is not None and runtime.created_at <= _when(readback["task_created_at"])
    return {"run_id": row.get("task_id"),
            "mode": "offline" if offline else ("paid" if counters is not None and counters["attempt_ordinals"] >= 1 else None),
            "actual_owner_event": (row.get("owner_account_input_matched") is True and row.get("task_api_gateway_attested") is True
                                   and row.get("task_record_bound") is True),
            "correctness_verified": correctness,
            "task_count": counters["thread_tasks"] if counters else None,
            "terminal_reply_count": row.get("terminal_reply_count"),
            "wall_seconds": row.get("wall_seconds"), "owner_pause_seconds": row.get("owner_pause_seconds"),
            "machine_seconds": row.get("machine_seconds"),
            "ambiguous_effects": (1 if row["qualification_evidence"].get("effect_state") == "AMBIGUOUS_DELIVERY_EFFECT" else 0)
            if readback else None,
            "delivery_ambiguous": delivery_ambiguous,
            "operational_provider_attempts": counters["attempt_ordinals"] if counters else None,
            "actual_cost_usd": 0 if offline else None, "cost_basis": "simulation_zero_calls" if offline else None,
            "runtime_sha": runtime.runtime_sha if bound else None, "image_id": runtime.image_id if bound else None,
            "simulation_label": label if offline else None}


_RECORD_ORDER = ("run_id", "mode", "actual_owner_event", "correctness_verified", "task_count", "terminal_reply_count",
                 "wall_seconds", "owner_pause_seconds", "machine_seconds", "ambiguous_effects", "delivery_ambiguous",
                 "operational_provider_attempts", "actual_cost_usd", "cost_basis", "runtime_sha", "image_id")


def _run_records_observation(check: str, mode: str, at: datetime, runtime: _Runtime,
                             records: list[dict[str, Any]], source: str) -> Observation:
    facts = {"records": [{key: record[key] for key in ("run_id", "mode", "correctness_verified", "terminal_reply_count",
                                                       "wall_seconds", "operational_provider_attempts", "actual_cost_usd",
                                                       "runtime_sha", "image_id")} for record in records]}
    wrong = [record["run_id"] for record in records if record["mode"] not in (None, mode)]
    if wrong:
        return _observation(check, False, f"Task evidence shows these are not {mode} runs: " + ", ".join(map(str, wrong)) + ".",
                            source, facts, at, **runtime.binding)
    order = _RECORD_ORDER + (("simulation_label",) if mode == "offline" else ())
    missing = next(((record["run_id"], key) for record in records for key in order if record[key] is None), None)
    if missing:
        return _observation(check, None, f"Run {missing[0]} lacks measured {missing[1]}; unknown is not zero or compliant.",
                            source, facts, at, **runtime.binding)
    passed, reason = evaluate_run_records(records, mode=mode, runtime_sha=runtime.runtime_sha, image_id=runtime.image_id)
    return _observation(check, passed, reason, source, facts, at, **runtime.binding)


def _trial_observation(at: datetime, runtime: _Runtime, t: _SlackThreads, trials: list[str] | None,
                       rubric_path: Path | None) -> Observation:
    if not trials or len(set(trials)) != 10 or len(trials) != 10 or rubric_path is None:
        return _observation("10", None, "Ten distinct --trial threads and their --rubric are required.",
                            _SLACK_SOURCE, {}, at, **runtime.binding)
    rubric, digest = t.m.load_rubric(Path(rubric_path))
    records = []
    for thread in trials:
        replies = t.replies(thread)
        task_id = t.m.task_from_accepted(thread, replies[0], replies[1], t.bot)
        snapshot = _task_facts(t, thread, task_id, replies)
        row = snapshot["row"]
        markers = re.findall(r"\brubric-sha256:([a-f0-9]{64})\b", str((snapshot["task"].get("intake") or {}).get("text", "")))
        row["input_rubric_sha256"] = markers[0] if row["task_record_bound"] and len(markers) == 1 else None
        records.append(_run_record(runtime, snapshot, t.m.evaluate_rubric(row, rubric, digest)["passed"], t.m.LABEL))
    observed = _run_records_observation("10", "offline", at, runtime, records, _SLACK_SOURCE)
    if observed.passed is not True:
        return observed
    # Consecutive: the ten threads are the only top-level owner posts between the first and the last.
    # Only what the gateway itself ignores is excluded, so any post it could forward counts.
    ordered = sorted(trials, key=Decimal)
    posts = {str(m.get("ts")) for m in t.history(ordered[0], ordered[-1])
             if m.get("user") == t.owner and not m.get("bot_id")
             and m.get("subtype") not in ("bot_message", "message_changed", "message_deleted")}
    if posts != set(trials):
        return _observation("10", None, f"The ten trials are not consecutive: {len(posts - set(trials))} other top-level "
                            "owner post(s) fall between them, or a trial is not a top-level owner post.",
                            _SLACK_SOURCE + " and conversations.history", observed.facts, at, **runtime.binding)
    return _observation("10", True, observed.explanation + " The ten are the only top-level owner posts in the "
                        "channel between the first and the last, so they are consecutive.",
                        _SLACK_SOURCE + " and conversations.history", observed.facts, at, **runtime.binding)


def _paid_facts(t: _SlackThreads, thread: str) -> dict[str, Any]:
    replies = t.replies(thread)
    task_id = t.m.task_from_accepted(thread, replies[0], replies[1], t.bot)
    return _task_facts(t, thread, task_id, replies)


def _load_paid_rubric(path: Path) -> tuple[dict[str, Any], str]:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("paid rubric exceeds its bound")
    value = json.loads(raw)
    substrings = value.get("expected_answer_substrings") if isinstance(value, dict) else None
    if (not isinstance(value, dict)
            or set(value) != {"schema", "rubric_id", "declared_at", "expected_answer_substrings"}
            or value["schema"] != "director-paid-answer-rubric/v1"
            or not isinstance(value["rubric_id"], str) or not re.fullmatch(r"[a-z0-9-]{1,80}", value["rubric_id"])
            or not isinstance(substrings, list) or not 1 <= len(substrings) <= 5
            or any(not isinstance(item, str) or not item or len(item) > 200 for item in substrings)):
        raise ValueError("unsupported paid answer rubric")
    _when(value["declared_at"])
    return value, hashlib.sha256(raw).hexdigest()


def _paid_correct(paid: dict[str, Any], rubric_path: Path) -> bool:
    rubric, digest = _load_paid_rubric(rubric_path)
    row = paid["row"]
    markers = re.findall(r"\brubric-sha256:([a-f0-9]{64})\b", str((paid["task"].get("intake") or {}).get("text", "")))
    finals = row.get("terminal_messages") or []
    if not (row.get("task_record_bound") is True and markers == [digest] and row.get("input_message_ts")
            and _when(rubric["declared_at"]).timestamp() <= float(row["input_message_ts"])
            and row.get("task_status") == "DONE" and len(finals) == 1):
        return False
    text = next((html.unescape(str(m.get("text", ""))) for m in paid["messages"] if m.get("ts") == finals[0]["ts"]), None)
    return text is not None and all(item in text for item in rubric["expected_answer_substrings"])


def _settled_cost(consistency: dict | None, paid: dict[str, Any]) -> tuple[float | None, str | None]:
    """Actual charges only from settled usage rows correlated to request and response IDs."""
    entry = ((consistency or {}).get("tasks") or {}).get(paid["task_id"])
    attempts = {event["attempt_digest"] for event in (paid.get("latest") or {}).values()}
    if not isinstance(entry, dict) or entry.get("usage_tables_present") is not True:
        return None, None
    spend = entry.get("spend") or []
    if not attempts or {row.get("attempt_digest") for row in spend} != attempts:
        return None, None
    if any(not _counter(row.get("actual_microusd")) or row.get("request_correlated") is not True for row in spend):
        return None, None
    return sum(row["actual_microusd"] for row in spend) / 1_000_000, "correlated_actual_charges"


def _paid_observation(at: datetime, runtime: _Runtime, paid: dict[str, Any] | None, rubric_path: Path | None,
                      consistency: dict | None, label: str) -> Observation:
    if paid is None or rubric_path is None:
        return _observation("20", None, "A --paid-thread and its --paid-rubric are required.", _SLACK_SOURCE, {}, at,
                            **runtime.binding)
    cost, basis = _settled_cost(consistency, paid)
    record = _run_record(runtime, paid, _paid_correct(paid, rubric_path), label)
    if record["mode"] == "paid":
        record.update(actual_cost_usd=cost, cost_basis=basis)
    return _run_records_observation("20", "paid", at, runtime, [record], _SLACK_SOURCE + " and settled meter rows")


def _monetary_observation(at: datetime, runtime: _Runtime, paid: dict[str, Any] | None,
                          consistency: dict | None, cost: float | None) -> Observation:
    over: list[str] = []
    unpriced: list[str] = []
    accounting = (runtime.health.get("usage_monitor") or {}).get("daily_accounting")
    monitored = isinstance(accounting, dict) and bool(accounting)
    if monitored:
        for provider, value in sorted(accounting.items()):
            budget = value.get("daily_budget_usd")
            if type(budget) in (int, float):
                used = max(value.get("reported_cost_usd") or 0, value.get("reserved_cost_usd") or 0)
                requests = value.get("request_count")
                if used > budget:
                    over.append("usage monitor " + str(provider))
                elif (value.get("cost_visibility_complete") is not True and value.get("reserved_cost_usd") is None
                      and not (type(requests) is int and requests == 0)):
                    # reported_cost_usd sums known costs only; unknown cost is not zero.
                    unpriced.append(str(provider))
    budgets = None
    if paid is not None and consistency is not None:
        entry = (consistency.get("tasks") or {}).get(paid["task_id"])
        budgets = entry.get("budgets") if isinstance(entry, dict) else None
    if isinstance(budgets, list):
        for row in budgets:
            if not _counter(row["day_held_microusd"]) or not _counter(row["daily_limit_microusd"]):
                raise ValueError("malformed enrolled budget row")
            if row["day_held_microusd"] > row["daily_limit_microusd"]:
                over.append("enrolled daily budget")
    facts = {"usage_monitor_providers": sorted(accounting) if monitored else None, "over_cap": over,
             "unknown_cost_providers": unpriced,
             "enrolled_budget_rows": len(budgets) if isinstance(budgets, list) else None, "settled_actual_cost_usd": cost}
    source = "Director /health usage_monitor and " + _CONSISTENCY_SOURCE
    if over:
        return _observation("15", False, "A daily monetary cap is exceeded: " + ", ".join(over) + ".", source, facts, at, **runtime.binding)
    if unpriced:
        return _observation("15", None, "Usage-monitor cost is incomplete and unreserved for attempted providers: "
                            + ", ".join(unpriced) + "; unknown cost is not zero.", source, facts, at, **runtime.binding)
    if monitored and isinstance(budgets, list) and budgets and cost is not None:
        return _observation("15", True, "Enrolled signed daily caps hold, usage-monitor accounting is within budget and the "
                            "paid task's actual charges are settled request-correlated meter rows.", source, facts, at, **runtime.binding)
    return _observation("15", None, "Requires the paid task's enrolled budgets, usage-monitor accounting and settled "
                        "request-correlated meter rows; unknown cost is not zero.", source, facts, at, **runtime.binding)


def _deployment_observations(at: datetime, candidate_sha: str, project: str, *, channel: str | None,
                             gateway: str | None, topology: bool, drills: dict[str, str], approval_task: str | None,
                             trials: list[str] | None, rubric_path: Path | None, paid_thread: str | None,
                             paid_rubric_path: Path | None) -> list[Observation]:
    try:
        runtime_rows, runtime = _runtime(at, project)
    except _SOURCE_ERRORS:
        return [_observation("19", None, "Director identity/health could not be verified; no retry was made.",
                             "Docker Compose labels and Director /health", {}, at)]
    binding = runtime.binding
    found: dict[str, Observation] = {item.check_id: item for item in runtime_rows}

    def put(items: list[Observation]) -> None:
        for item in items:
            found[item.check_id] = item

    def unknown(keys: tuple[str, ...], reason: str, source: str) -> None:
        put([_observation(key, None, reason, source, {}, at, **binding) for key in keys])

    if topology:
        try:
            if gateway is None:
                unknown(("01", "02"), "No expected gateway identity was supplied (--gateway); topology was not inspected.",
                        "Docker / Windows process inventory")
            else:
                put(_topology_observations(at, runtime, gateway, channel))
        except _SOURCE_ERRORS:
            unknown(("01", "02"), "Deployment topology could not be verified; no retry was made.", "Docker / Windows process inventory")
    t = None
    if channel is not None:
        try:
            t = _SlackThreads(runtime, channel)
        except _SOURCE_ERRORS:
            t = None
    paid = None
    label = None
    try:
        if t is None:
            unknown(("03", "04", "06", "08", "09", "10", "11", "20"), _SLACK_UNAVAILABLE, _SLACK_SOURCE)
        else:
            label = t.m.LABEL
            try:
                put(_drill_observations(at, runtime, t, drills))
            except _SOURCE_ERRORS:
                unknown(("03", "09"), "Drill threads could not be read; no retry was made.", _SLACK_SOURCE)
            try:
                put([_trial_observation(at, runtime, t, trials, rubric_path)])
            except _SOURCE_ERRORS:
                unknown(("10",), "The offline trial threads could not be read; no retry was made.", _SLACK_SOURCE)
            try:
                put([_status_observation(at, runtime, t)])
            except _SOURCE_ERRORS:
                unknown(("04",), "The status execution or its Slack readback could not be read; no retry was made.",
                        "n8n Director Status saved execution")
            if paid_thread is not None:
                try:
                    paid = _paid_facts(t, paid_thread)
                except _SOURCE_ERRORS:
                    paid = None
        task_ids = list(dict.fromkeys(x for x in ((paid or {}).get("task_id"), approval_task)
                                      if isinstance(x, str) and _TASK.fullmatch(x)))[:12]

        def in_image(command: str, *extra: str) -> dict | None:
            try:
                value = _run_json(["docker", "exec", runtime.container_id, "python", "-m",
                                   "director_orchestrator.primary_generation", command, *extra], timeout=45)
            except _SOURCE_ERRORS:
                return None
            return value if isinstance(value, dict) else None

        consistency = in_image("consistency", *[part for task_id in task_ids for part in ("--task", task_id)])
        captures = in_image("activation-captures")
        if t is not None:
            try:
                if approval_task is None:
                    unknown(("06", "08"), "No --approval-task was supplied.", _SLACK_SOURCE)
                else:
                    put(_approval_observations(at, runtime, t, approval_task, consistency))
            except _SOURCE_ERRORS:
                unknown(("06", "08"), "The approval task could not be read; no retry was made.", _SLACK_SOURCE)
        try:
            put([_reconciliation_observation(at, runtime, consistency)])
        except _SOURCE_ERRORS:
            unknown(("07",), "The consistency snapshot could not be evaluated.", _CONSISTENCY_SOURCE)
        try:
            put(_capture_observations(at, runtime, captures, paid))
        except _SOURCE_ERRORS:
            unknown(("11", "12"), "The activation captures could not be evaluated.", _CAPTURES_SOURCE)
        try:
            put([_acceptance_observation(at, candidate_sha, consistency)])
        except _SOURCE_ERRORS:
            put([_observation("17", None, "The adopted acceptance could not be evaluated.", _CONSISTENCY_SOURCE, {}, at,
                              candidate_sha=candidate_sha)])
        try:
            put([_paid_observation(at, runtime, paid, paid_rubric_path, consistency, label or "")])
        except _SOURCE_ERRORS:
            unknown(("20",), "The paid run or its settlement could not be evaluated; no retry was made.", _SLACK_SOURCE)
        try:
            cost = _settled_cost(consistency, paid)[0] if paid is not None else None
            put([_monetary_observation(at, runtime, paid, consistency, cost)])
        except _SOURCE_ERRORS:
            unknown(("15",), "The monetary sources could not be evaluated; no retry was made.", _CONSISTENCY_SOURCE)
        if t is None:
            unknown(("11", "20"), _SLACK_UNAVAILABLE, _SLACK_SOURCE)
        elif paid_thread is not None and paid is None:
            unknown(("20",), "The --paid-thread could not be read or bound to exactly one task; no retry was made.",
                    _SLACK_SOURCE)
    finally:
        if t is not None:
            t.close()
    return list(found.values())


def _native_security_index() -> Path:
    # Native installed-plugin default, not an imported report path or CLI trust override.
    # Custom CODEX_SECURITY_STATE_DIR installations remain UNKNOWN until supported.
    return Path.home() / ".codex/state/plugins/codex-security/workbench.sqlite3"


def _read_bounded_file(root: Path, relative: str) -> bytes:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve().parent == path.resolve():
        raise ValueError("nonlocal artifact path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact escaped native scan directory")
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise ValueError("artifact has a link or junction")
        cursor = cursor.parent
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("artifact exceeds read bound")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("artifact grew beyond read bound")
    return raw


def _security_observation(scan_id: str, candidate_sha: str, at: datetime) -> Observation:
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", scan_id):
        raise ValueError("native Security scan ID must be a UUID")
    index = _native_security_index()
    if not index.is_file() or index.is_symlink():
        raise ValueError("native Security index unavailable")
    with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,status,scan_dir,target_path,target_revision,mode,diff_base_revision,"
                         "diff_head_revision,seal_manifest_digest,completed_at FROM scans WHERE id=?", (scan_id,)).fetchone()
        if row is None or row["status"] != "complete" or row["mode"] != "diff" or row["diff_head_revision"] != candidate_sha:
            raise ValueError("native index has no complete scan for this candidate")
        indexed = dict(row)
    root = Path(indexed["scan_dir"])
    if not root.is_absolute() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("native scan directory is not canonical")
    manifest_raw = _read_bounded_file(root, "scan-manifest.json")
    if "sha256:" + hashlib.sha256(manifest_raw).hexdigest() != indexed["seal_manifest_digest"]:
        raise ValueError("manifest does not match the native indexed seal")
    manifest = json.loads(manifest_raw)
    scan = manifest["scan"]
    target = scan["target"]
    base = _sha(indexed["diff_base_revision"])
    if (manifest.get("documentType") != "codex-security.scan-manifest" or scan.get("id") != scan_id
            or scan.get("status") != "completed" or target.get("kind") != "git_diff"
            or target.get("headRevision") != candidate_sha or target.get("baseRevision") != base
            or scan.get("completedAt") != indexed["completed_at"] or not scan.get("sealedAt")):
        raise ValueError("manifest binding disagrees with native index")
    sealed = datetime.fromisoformat(scan["sealedAt"].replace("Z", "+00:00"))
    if sealed.tzinfo is None or sealed > at:
        raise ValueError("native seal is future dated")
    artifacts = scan.get("artifacts", [])
    preserved = scan.get("preservedSources", {})
    if len(artifacts) > 20 or len(preserved) > 100:
        raise ValueError("native artifact inventory exceeds bound")
    hashes = {entry["path"]: entry["sha256"] for entry in artifacts}
    if len(hashes) != len(artifacts) or not {"coverage.json", "findings.json"} <= hashes.keys():
        raise ValueError("native canonical artifacts missing or duplicated")
    contents = {}
    for relative, digest in [*hashes.items(), *preserved.items()]:
        raw = _read_bounded_file(root, relative)
        # Native canonical artifacts use byte hashes. workbench_saved_results
        # _encoded/_digest uses normalized JSON for preserved checkpoints.
        hashed = (json.dumps(json.loads(raw), ensure_ascii=True, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode() if relative in preserved else raw)
        if hashlib.sha256(hashed).hexdigest() != digest:
            raise ValueError("native artifact digest mismatch")
        if relative in {"coverage.json", "findings.json"}:
            contents[relative] = json.loads(raw)
    coverage, findings = contents["coverage.json"], contents["findings.json"]
    if coverage.get("scanId") != scan_id or findings.get("scanId") != scan_id:
        raise ValueError("canonical evidence belongs to another scan")
    changed = set(_run_bytes(["git", "-C", indexed["target_path"], "diff", "--name-only", "-z", base, candidate_sha, "--"]).decode().rstrip("\0").split("\0"))
    surfaces = coverage.get("surfaces", [])
    covered = {item.get("label") for item in surfaces if item.get("disposition") in {"no_issue_found", "rejected", "validated"}}
    passed = (coverage.get("completeness") == "complete" and not coverage.get("deferred")
              and not coverage.get("excludePaths") and not coverage.get("explicitExclusions")
              and bool(changed) and "" not in changed and changed <= covered and findings.get("findings") == [])
    facts = {"scan_id": scan_id, "native_manifest_digest": indexed["seal_manifest_digest"],
             "coverage_completeness": coverage.get("completeness"), "surface_count": len(surfaces),
             "changed_paths": len(changed), "changed_paths_covered": changed <= covered,
             "findings_count": len(findings.get("findings", [])), "sealed_at": scan["sealedAt"],
             "limitations": scan.get("scope", {}).get("limitations", [])}
    return _observation("18", passed, "Verified the native index's immutable seal, all declared hashes, exact head/base and changed-path coverage. Complete with no canonical findings is required; operational limitations remain in the source manifest.",
                        str(root / "scan-manifest.json"), facts, at, candidate_sha=candidate_sha)


CI_JOBS = ("Contribution attribution", "Python 3.12 (ubuntu-latest)",
           "Python 3.12 (windows-latest)", "Compose and container build",
           "Secret hygiene", "Cross-process recovery probe")
# The only workflow whose jobs can fill CI_JOBS. Fixed here, never taken from the
# candidate: ci.yml itself routes to hosted or registered self-hosted runners.
CI_WORKFLOW = ".github/workflows/ci.yml"
_REPLICA_LANES = ("ubuntu-replica", "windows-postgres-lane")
_REPLICA_LANE_KEYS = {"lane", "head_tests_run", "base_tests_run", "head_failures", "base_failures", "command_sha256"}


def _ci_observation(candidate_sha: str, at: datetime, replica_receipt: Path | None = None) -> Observation:
    paths: dict[Any, Any] = {}

    def executed(args: list[str]) -> Any:
        # Zero-step jobs are billing stubs and are dropped, so an older executed
        # ci.yml job of the same name (hosted or self-hosted) can stand. A job of
        # any other workflow is renamed by its workflow path: it can still fail
        # 16, but it can never stand in for a required ci.yml job.
        value = _run_json(["gh", *args])
        if "/jobs?" not in args[-1]:
            paths.update((run["id"], run.get("path")) for run in value["workflow_runs"])
            return value
        path = paths.get(int(args[-1].split("/runs/")[1].split("/")[0]))
        jobs = [job if path == CI_WORKFLOW else {**job, "name": f"{path}: {job.get('name')}"}
                for job in value["jobs"] if job.get("steps")]
        return {**value, "jobs": jobs}

    # Reuse the project's oracle rather than invent a second CI truth predicate.
    with tempfile.TemporaryDirectory(prefix="director-scorecard-") as folder:
        ledger = StateWitnessLedger(Path(folder) / "witness.sqlite3")
        row = gh_checks_green(ledger, REPOSITORY, candidate_sha, agent="completion-scorecard",
                              required=CI_JOBS, gh_json=executed)
    source = f"https://github.com/{REPOSITORY}/commit/{candidate_sha}/checks"
    jobs = row.observed.get("jobs") if isinstance(row.observed, dict) else None
    if row.status == WitnessStatus.MATCH:
        return _observation("16", True, "Existing Witness Ledger CI oracle: every required ci.yml job executed steps at this "
                            "exact head, on hosted or registered self-hosted runners, and concluded success.",
                            source, row.observed, at, candidate_sha=candidate_sha)
    if not isinstance(jobs, dict):
        return _observation("16", None, "GitHub CI could not be verified; no retry was made.", source, {}, at,
                            candidate_sha=candidate_sha)
    if any(job.get("conclusion") != "success" for job in jobs.values()):
        return _observation("16", False, "An executed job at this exact head did not conclude success.",
                            source, row.observed, at, candidate_sha=candidate_sha)
    if not set(CI_JOBS) & set(jobs) and replica_receipt is not None:
        # Owner amendment: the receipt stands in only when no required job executed.
        return _ci_replica_observation(replica_receipt, candidate_sha, at)
    return _observation("16", False, "Required jobs did not execute at this exact head; billing stubs and missing jobs do not pass.",
                        source, row.observed, at, candidate_sha=candidate_sha)


def _ci_replica_document(document: Any) -> dict[str, Any]:
    if (not isinstance(document, dict)
            or set(document) != {"schema", "repository", "candidate_sha", "base_sha", "produced_at", "lanes"}
            or document["schema"] != "director-ci-local-replica/v1" or document["repository"] != REPOSITORY):
        raise ValueError("unsupported local-replica receipt")
    _sha(document["candidate_sha"])
    _sha(document["base_sha"])
    lanes = document["lanes"]
    if (not isinstance(lanes, list) or len(lanes) != 2 or any(not isinstance(lane, dict) or set(lane) != _REPLICA_LANE_KEYS
                                                              for lane in lanes)
            or sorted(str(lane["lane"]) for lane in lanes) != list(_REPLICA_LANES)):
        raise ValueError("the receipt must carry exactly the two replica lanes")
    for lane in lanes:
        if any(type(lane[key]) is not int or lane[key] <= 0 for key in ("head_tests_run", "base_tests_run")):
            raise ValueError("replica lanes must have run tests")
        for key in ("head_failures", "base_failures"):
            values = lane[key]
            if (not isinstance(values, list) or len(values) > 10000 or len(set(map(str, values))) != len(values)
                    or any(not isinstance(item, str) or not item or len(item) > 512 for item in values)):
                raise ValueError("malformed replica failure set")
        if not isinstance(lane["command_sha256"], str) or not _HEX64.fullmatch(lane["command_sha256"]):
            raise ValueError("malformed replica command digest")
    return document


def _ci_replica_observation(path: Path, candidate_sha: str, at: datetime) -> Observation:
    """Owner-signed local replica: no new failure against the base on both lanes."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if CI_REPLICA_KEY.is_symlink() or not CI_REPLICA_KEY.is_file():
        raise ValueError("owner replica signer key unavailable")
    key_hex = CI_REPLICA_KEY.read_text(encoding="ascii").strip()
    if not _HEX64.fullmatch(key_hex):
        raise ValueError("owner replica signer key is malformed")
    source = Path(path)
    if source.is_symlink():
        raise ValueError("replica receipt must not be a link")
    with source.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("replica receipt exceeds its bound")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"document", "signature"}:
        raise ValueError("unsupported replica receipt envelope")
    signature = envelope["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ValueError("malformed replica signature")
    signed = json.dumps(envelope["document"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                        allow_nan=False).encode()
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(bytes.fromhex(signature), signed)
    except InvalidSignature:
        raise ValueError("replica receipt signature invalid") from None
    document = _ci_replica_document(envelope["document"])
    if _when(document["produced_at"]) > at:
        raise ValueError("replica receipt is future dated")
    lanes = {lane["lane"]: lane for lane in document["lanes"]}
    new = {name: sorted(set(lane["head_failures"]) - set(lane["base_failures"])) for name, lane in lanes.items()}
    base, candidate = document["base_sha"], document["candidate_sha"]
    # The diff means something only against a main commit the candidate descends
    # from, and only when the head lane ran at least the base lane's tests.
    base_on_main = (_compare_status(base, candidate) == "ahead"
                    and _compare_status(base, "main") in ("ahead", "identical"))
    facts = {"receipt_sha256": hashlib.sha256(raw).hexdigest(),
             "signer_key_sha256": hashlib.sha256(bytes.fromhex(key_hex)).hexdigest(), "base_sha": base,
             "base_is_main_ancestor_of_candidate": base_on_main,
             "lanes": {name: {"head_tests_run": lanes[name]["head_tests_run"], "base_tests_run": lanes[name]["base_tests_run"],
                              "new_failure_count": len(new[name])} for name in sorted(lanes)}}
    if not base_on_main:
        return _observation("16", None, "The replica's base_sha is not a main commit that the candidate descends from.",
                            str(source.resolve()), facts, at, candidate_sha=candidate)
    short = [name for name in sorted(lanes) if lanes[name]["head_tests_run"] < lanes[name]["base_tests_run"]]
    if short:
        return _observation("16", None, "The replica's head lane ran fewer tests than its base on: " + ", ".join(short) + ".",
                            str(source.resolve()), facts, at, candidate_sha=candidate)
    regressed = [name for name in sorted(new) if new[name]]
    return _observation("16", not regressed,
                        ("The owner-signed local replica shows new failures against the base on: " + ", ".join(regressed) + "."
                         if regressed else "No required job executed at this exact head; the owner-signed Windows Postgres "
                         "and Ubuntu replica receipt, diffed against a main ancestor, shows no failure beyond the base's "
                         "failure set."),
                        str(source.resolve()), facts, at, candidate_sha=candidate)


def _compare_status(base: str, head: str) -> str:
    """GitHub's ahead/behind/identical/diverged status of head against base (GET only)."""
    value = _run_json(["gh", "api", f"repos/{REPOSITORY}/compare/{base}...{head}", "--jq", "{status: .status}"])
    return value["status"] if isinstance(value, dict) and isinstance(value.get("status"), str) else ""


def _workload_observation(path: Path, at: datetime) -> list[Observation]:
    from .engineering_workload_ledger import EngineeringWorkloadLedger

    status = EngineeringWorkloadLedger(path).status()
    # Imported historical attestations are explicitly unauthenticated by this
    # tool. A complete window of actual prospective admissions needs no invented
    # historical filler. This checks journal consistency, not authority to spend.
    passed = None
    explanation = "A complete observed window of twenty prospective admissions is not yet available."
    rows = status.get("records", [])
    complete = False
    consistent = _all_fields(status.get("journal_consistency", {}),
                             {"sqlite_integrity_ok": True, "policy_digest_matches": True})
    if consistent is True and status.get("rolling_window_complete") is True and len(rows) == 20:
        cutover = datetime.fromisoformat(status["cutover_at"].replace("Z", "+00:00"))
        times = [datetime.fromisoformat(row["admitted_at"].replace("Z", "+00:00")) for row in rows]
        if cutover.tzinfo is not None and all(value.tzinfo is not None and cutover < value <= at for value in times):
            complete = True
            # The ledger already exposes an authoritative tri-state resource-bound
            # result. Unknown or incomplete reported usage is never treated as
            # compliant; only a known violation fails, and only fully known,
            # in-bound usage passes.
            bounds_ok = status.get("current_window_checks", {}).get("reported_resource_bounds_ok")
            if type(bounds_ok) is bool:
                passed = bounds_ok
                explanation = ("Recomputed the journal's twenty post-cutover admissions; reported resource usage is known and within bounds."
                               if passed else
                               "Recomputed the journal's twenty post-cutover admissions; a known reported resource-bound violation exists.")
                explanation += " This does not authenticate external attestations or grant dispatch authority."
            else:
                explanation = "Reported resource usage is unknown or incomplete for one or more of the twenty post-cutover admissions; unknown usage is not treated as compliant."
        else:
            explanation = "This window includes imported, future or unbound history; the tool does not authenticate its attestations."
    facts = {key: status.get(key) for key in ("history_mode", "cutover_at", "rolling_window_complete",
             "real_assignments_in_window", "utilization", "holds", "journal_consistency", "current_window_checks")}
    source = str(path.resolve())

    def bounded(bounds: Any) -> bool:
        return (isinstance(bounds, dict)
                and all(type(bounds.get(key)) is int and bounds[key] > 0
                        for key in ("timeout_seconds", "call_limit", "turn_limit", "output_bytes_limit"))
                and type(bounds.get("usd_limit")) in (int, float) and bounds["usd_limit"] > 0
                and bounds.get("hard_usd_cap_verified") is True)

    uncapped = sum(1 for row in rows if not isinstance(row.get("bounds"), dict) or row["bounds"].get("usd_limit") is None
                   or row["bounds"].get("hard_usd_cap_verified") is not True)
    passed14, explanation14 = None, "A complete window of twenty post-cutover admissions is not yet available."
    if complete and uncapped:
        passed14, explanation14 = False, "An admission in the window lacks a USD limit or a verified hard USD cap."
    elif complete and all(bounded(row.get("bounds")) for row in rows):
        passed14, explanation14 = True, ("Every one of the twenty post-cutover admissions declares positive timeout, call, "
                                         "turn and output bounds and a verified hard USD cap.")
    elif complete:
        explanation14 = "An admission's declared bounds are malformed."
    facts14 = {"admissions": len(rows), "window_complete": complete, "uncapped_admissions": uncapped}
    return [_observation("13", passed, explanation, source, facts, at),
            _observation("14", passed14, explanation14, source, facts14, at)]


def collect(candidate_sha: str, *, deployment: str | None = None, channel: str | None = None,
            now: datetime | None = None, github: bool = False, ledger_path: Path | None = None,
            gateway: str | None = None, topology: bool = True, security_scan_id: str | None = None,
            drill_threads: dict[str, str] | None = None, approval_task: str | None = None,
            trials: list[str] | None = None, rubric_path: Path | None = None, paid_thread: str | None = None,
            paid_rubric_path: Path | None = None, ci_replica_receipt: Path | None = None) -> list[Observation]:
    """One bounded collection of the explicitly selected deployment and sources.

    Without ``deployment`` no container, port or Director endpoint is probed;
    the expected Slack channel comes only from ``channel``. Topology (01, 02)
    is inspected only for an explicitly named running gateway.
    """
    _sha(candidate_sha)
    if gateway is not None:
        _container_name(gateway)
    if deployment is not None and (not isinstance(deployment, str) or not _PROJECT.fullmatch(deployment)):
        raise ValueError("--deployment must be an exact Compose project name")
    if channel is not None and (not isinstance(channel, str) or not _CHANNEL.fullmatch(channel)):
        raise ValueError("--channel must be an exact Slack channel ID")
    drills = dict(drill_threads or {})
    if (any(kind not in DRILL_KINDS for kind in drills) or len(set(drills.values())) != len(drills)
            or any(not isinstance(value, str) or not _TS.fullmatch(value) for value in drills.values())):
        raise ValueError("--drill-thread takes distinct KIND=THREAD_TS values of known drill kinds")
    trial_list = list(trials) if trials else None
    if trial_list is not None and (len(trial_list) > 10 or len(set(trial_list)) != len(trial_list)
                                   or any(not isinstance(value, str) or not _TS.fullmatch(value) for value in trial_list)):
        raise ValueError("--trial takes one to ten distinct THREAD_TS values")
    if approval_task is not None and (not isinstance(approval_task, str) or not _TASK.fullmatch(approval_task)):
        raise ValueError("--approval-task must be an exact DIR-LG task ID")
    if paid_thread is not None and (not isinstance(paid_thread, str) or not _TS.fullmatch(paid_thread)):
        raise ValueError("--paid-thread must be a THREAD_TS")
    if ci_replica_receipt is not None and not github:
        raise ValueError("--ci-replica-receipt requires --github")
    if rubric_path is not None and trial_list is None:
        raise ValueError("--rubric requires --trial")
    if paid_rubric_path is not None and paid_thread is None:
        raise ValueError("--paid-rubric requires --paid-thread")
    at = now or datetime.now(timezone.utc)
    result: list[Observation] = []
    if deployment is None:
        result.append(_observation("19", None, "No --deployment was selected; no runtime was inspected.",
                                   "Docker Compose labels", {}, at))
    else:
        result.extend(_deployment_observations(
            at, candidate_sha, deployment, channel=channel, gateway=gateway, topology=topology, drills=drills,
            approval_task=approval_task, trials=trial_list, rubric_path=rubric_path, paid_thread=paid_thread,
            paid_rubric_path=paid_rubric_path))
    if github:
        try:
            result.append(_ci_observation(candidate_sha, at, ci_replica_receipt))
        except _SOURCE_ERRORS:
            result.append(_observation("16", None, "GitHub CI or the local-replica receipt could not be verified; no retry was made.",
                                       "GitHub Actions", {}, at, candidate_sha=candidate_sha))
    if ledger_path is not None:
        try:
            result.extend(_workload_observation(ledger_path, at))
        except _SOURCE_ERRORS:
            result.extend(_observation(key, None, "The assignment journal could not be verified; no retry was made.",
                                       str(ledger_path.resolve()), {}, at) for key in ("13", "14"))
    if security_scan_id is not None:
        try:
            result.append(_security_observation(security_scan_id, candidate_sha, at))
        except _SOURCE_ERRORS:
            result.append(_observation("18", None, "The native Security index, seal, hashes or exact-candidate binding could not be verified.",
                                       "Codex Security native index", {}, at, candidate_sha=candidate_sha))
    return result


RUN_RECORD_FIELDS = frozenset({
    "run_id", "mode", "actual_owner_event", "correctness_verified", "task_count",
    "terminal_reply_count", "wall_seconds", "owner_pause_seconds", "machine_seconds",
    "ambiguous_effects", "delivery_ambiguous", "operational_provider_attempts", "actual_cost_usd",
    "runtime_sha", "image_id", "cost_basis",
})
OFFLINE_RUN_RECORD_FIELDS = RUN_RECORD_FIELDS | {"simulation_label"}


def evaluate_run_record(row: dict[str, Any], *, mode: str,
                        runtime_sha: str, image_id: str) -> tuple[bool | None, str]:
    """Validate one measured row without inventing a ten-run sequence."""
    _sha(runtime_sha)
    if mode not in {"offline", "paid"} or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Invalid run mode or immutable image")
    if not RUN_RECORD_FIELDS <= row.keys() or row["actual_cost_usd"] is None:
        return None, "Missing measured evidence or actual cost; unknown is not zero."
    if not isinstance(row["run_id"], str) or not row["run_id"]:
        return False, "Run IDs must be distinct."
    try:
        values = [row[key] for key in ("wall_seconds", "owner_pause_seconds", "machine_seconds", "actual_cost_usd")]
        if any(type(value) not in {int, float} for value in values):
            return None, "Measurements must be finite numeric values."
        wall, pause, machine, cost = values
        if not (0 <= pause <= wall < 60 and 0 <= machine < 60 and abs(wall - pause - machine) < 0.001 and 0 <= cost < 1):
            return False, "Actual wall time, pause accounting or actual cost misses the standard."
    except (ValueError, TypeError):
        return None, "Invalid measurements."
    expected = {"mode": mode, "actual_owner_event": True, "correctness_verified": True,
                "task_count": 1, "terminal_reply_count": 1, "ambiguous_effects": 0,
                "delivery_ambiguous": False, "runtime_sha": runtime_sha, "image_id": image_id,
                "cost_basis": "simulation_zero_calls" if mode == "offline" else "correlated_actual_charges"}
    if _all_fields(row, expected) is not True:
        return False, "Wrong origin/mode, incorrect result, duplicate or ambiguous delivery."
    attempts = row["operational_provider_attempts"]
    if type(attempts) is not int or attempts < 0:
        return None, "Actual provider attempt count is unavailable."
    if mode == "offline" and (attempts != 0 or cost != 0 or row.get("simulation_label") != "SIMULATION — NO PROVIDER INTERACTION"):
        return False, "Offline qualification requires zero operational calls and an explicit simulation label."
    if mode == "paid" and attempts < 1:
        return False, "A simulation cannot qualify real paid execution."
    return True, "Measured run facts meet the standard; source authentication remains a separate collector requirement."


def evaluate_run_records(records: list[dict[str, Any]], *, mode: str,
                         runtime_sha: str, image_id: str) -> tuple[bool | None, str]:
    """Validate facts AFTER a future authoritative run collector verifies receipts.

    This semantic validator alone cannot create a scored observation. The CLI
    does not accept a JSON run log as proof that its Slack events/costs are real.
    """
    _sha(runtime_sha)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("runtime image ID must be an immutable sha256")
    required_count = 10 if mode == "offline" else 1
    if mode not in {"offline", "paid"}:
        raise ValueError("mode must be offline or paid")
    if len(records) != required_count:
        return None, f"Exactly {required_count} independently verified runs required."
    seen: set[str] = set()
    for row in records:
        if not RUN_RECORD_FIELDS <= row.keys() or row["actual_cost_usd"] is None:
            return None, "Missing measured evidence or actual cost; unknown is not zero."
        run_id = row["run_id"]
        if not isinstance(run_id, str) or not run_id or run_id in seen:
            return False, "Run IDs must be distinct."
        seen.add(run_id)
        verdict = evaluate_run_record(row, mode=mode, runtime_sha=runtime_sha, image_id=image_id)
        if verdict[0] is not True:
            return verdict
    return True, "Measured run facts meet the standard; source authentication remains a separate collector requirement."


# Section 4/5 qualification vocabulary. It is deliberately distinct from the
# check 10/20 PASS criterion above: a canary whose provider returned no cost is
# recorded as QUALIFIED_COST_UNKNOWN, which never becomes a measured-cost PASS.
QUALIFICATION_VERDICTS = ("QUALIFIED_COST_MEASURED", "QUALIFIED_COST_UNKNOWN", "NOT_QUALIFIED", "UNKNOWN")
QUALIFICATION_RECORD_FIELDS = frozenset({
    "run_id", "source_event_id_sha256", "task_id", "assignment_ids", "attempt_ids", "effect_ids",
    "provider_route", "runtime_sha", "image_id", "configuration_digest", "duplicate_external_effects",
    "effect_state", "delivery_state", "readback_verified", "status_receipt_counts", "actual_cost_usd",
    "policy_violations",
})
_STATUS_RECEIPT_KEYS = ("tasks_created", "assignments_created", "provider_attempts_created", "deduplicated_receipts")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_ASSERTED = "asserted links only; authenticated collector correlation is a separate requirement"


def _counter(value: Any) -> bool:
    """Exact non-negative integer; booleans and floats are not counters."""
    return type(value) is int and value >= 0


def _unique_identities(values: Any) -> bool:
    return (isinstance(values, list) and bool(values)
            and all(isinstance(item, str) and _IDENTITY.fullmatch(item) for item in values)
            and len(set(values)) == len(values))


def evaluate_qualification_record(row: dict[str, Any], *, runtime_sha: str, image_id: str,
                                  configuration_digest: str) -> tuple[str, str]:
    """Classify one canary's *asserted* lineage row; never a scorecard PASS.

    The row is untrusted caller input: this validates that the asserted links
    are complete, well-formed, unique and consistent with the exact revision.
    It cannot verify that any link is real; authenticated collector correlation
    remains a separate requirement and no verdict claims it. Missing links stay
    UNKNOWN. A finite measured cost is required only for
    QUALIFIED_COST_MEASURED; explicit UNKNOWN cost yields the distinct
    QUALIFIED_COST_UNKNOWN verdict so the stricter evaluate_run_record
    criterion is preserved rather than silently satisfied.
    """
    from .allocation_profiles import LANES
    _sha(runtime_sha)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) or not re.fullmatch(r"[0-9a-f]{64}", configuration_digest):
        raise ValueError("runtime image ID and configuration digest must be immutable digests")
    if not isinstance(row, dict) or not QUALIFICATION_RECORD_FIELDS <= row.keys():
        return "UNKNOWN", "Asserted evidence fields are missing; nothing is inferred."
    if not isinstance(row["run_id"], str) or not _IDENTITY.fullmatch(row["run_id"]):
        return "UNKNOWN", "Run identity is missing or malformed."
    if row["runtime_sha"] != runtime_sha or row["image_id"] != image_id or row["configuration_digest"] != configuration_digest:
        return "NOT_QUALIFIED", "Evidence is bound to another revision, image or configuration."
    if not isinstance(row["source_event_id_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["source_event_id_sha256"]):
        return "UNKNOWN", "Source event digest is missing or malformed."
    if not isinstance(row["task_id"], str) or not _IDENTITY.fullmatch(row["task_id"]):
        return "UNKNOWN", "Task identity is missing or malformed."
    if not isinstance(row["provider_route"], str) or row["provider_route"] not in LANES:
        return "UNKNOWN", "Provider route is not a canonical allocation lane."
    for key in ("assignment_ids", "attempt_ids", "effect_ids"):
        if not _unique_identities(row[key]):
            return "UNKNOWN", f"Lineage link {key} is incomplete, malformed or duplicated; no receipt is manufactured."
    if row["effect_state"] == "ZERO_PROVIDER_EFFECT":
        return "NOT_QUALIFIED", "A zero-provider-effect simulation cannot qualify a live provider round trip."
    if row["effect_state"] != "PROVIDER_EFFECT_RECORDED":
        return ("NOT_QUALIFIED" if row["effect_state"] == "AMBIGUOUS_DELIVERY_EFFECT" else "UNKNOWN",
                "Effect state is ambiguous or unknown; an ambiguous paid effect is never repeated.")
    if not _counter(row["duplicate_external_effects"]) or not _counter(row["policy_violations"]):
        return "UNKNOWN", "Duplicate-effect and policy-violation counters must be exact non-negative integers."
    if row["duplicate_external_effects"] != 0 or row["policy_violations"] != 0:
        return "NOT_QUALIFIED", "Duplicate external effects or policy violations are not zero."
    if row["delivery_state"] != "DELIVERED" or row["readback_verified"] is not True:
        return "NOT_QUALIFIED", "Slack delivery lacks verified readback; an acknowledgement alone is insufficient."
    counts = row["status_receipt_counts"]
    if not isinstance(counts, dict) or any(counts.get(key) is None for key in _STATUS_RECEIPT_KEYS):
        return "UNKNOWN", "Status-receipt counts depend on the accepted Issue #230 interface."
    if not all(_counter(counts[key]) for key in _STATUS_RECEIPT_KEYS):
        return "UNKNOWN", "Status-receipt counters must be exact non-negative integers."
    if tuple(counts[key] for key in _STATUS_RECEIPT_KEYS) != (0, 0, 0, 1):
        return "NOT_QUALIFIED", "A status mention must create zero tasks/assignments/attempts and one deduplicated receipt."
    cost = row["actual_cost_usd"]
    if cost is None:
        return "QUALIFIED_COST_UNKNOWN", f"Asserted lineage is complete ({_ASSERTED}); actual cost is explicitly UNKNOWN, not zero and not a measured-cost pass."
    if type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0:
        return "UNKNOWN", "Reported cost is not a finite non-negative number."
    return "QUALIFIED_COST_MEASURED", f"Asserted lineage is complete ({_ASSERTED}) with a finite provider-reported actual cost."


def _file_references(folder: Path | None) -> list[dict[str, str]]:
    if folder is None:
        return []
    if not folder.is_dir():
        raise ValueError("evidence directory does not exist")
    # Explicit directory only, no recursion, no hidden files or credential stores.
    result = []
    for path in sorted(folder.iterdir())[:100]:
        if path.is_symlink() or path.name.startswith(".") or path.suffix.lower() not in {".json", ".md"}:
            continue
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        result.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "status": "UNVERIFIED_REFERENCE"})
    return result


def score(candidate_sha: str, observations: list[Observation] | None = None, *,
          now: datetime | None = None, runtime_sha: str | None = None,
          image_id: str | None = None, evidence_dir: Path | None = None) -> dict[str, Any]:
    """Calculate a diagnostic score. No caller-supplied PASS string is accepted."""
    _sha(candidate_sha)
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise ValueError("score time must be timezone-aware")
    rows = []
    for check in CHECKS:
        row = {**asdict(check), "status": "UNKNOWN", "explanation": "No authoritative collector proof is available.", "evidence": []}
        matches = [item for item in observations or [] if isinstance(item, Observation)
                   and item.check_id == check.id and item._origin is _COLLECTED]
        if matches:
            item = max(matches, key=lambda value: value.observed_at.isoformat())
            error = None
            if item.observed_at.tzinfo is None or item.expires_at.tzinfo is None:
                error = "Evidence timestamp is not timezone-aware."
            elif item.observed_at > at or item.expires_at <= at or item.expires_at - item.observed_at > FRESH_FOR:
                error = "Evidence is future-dated, stale or has an invalid expiry."
            elif check.scope == "candidate" and item.candidate_sha != candidate_sha:
                error = "Evidence belongs to another candidate."
            elif check.scope == "runtime" and (not item.runtime_sha or not item.image_id or
                    (runtime_sha and item.runtime_sha != runtime_sha) or (image_id and item.image_id != image_id)):
                error = "Evidence is missing or mismatches the runtime image binding."
            row["status"] = "UNKNOWN" if error or item.passed is None else "PASS" if item.passed is True else "FAIL"
            row["explanation"] = error or item.explanation
            row["evidence"] = [{"source": item.source, "sha256": item.digest,
                                "facts": item.facts,
                                "observed_at": item.observed_at.isoformat(), "expires_at": item.expires_at.isoformat(),
                                "candidate_sha": item.candidate_sha, "runtime_sha": item.runtime_sha, "image_id": item.image_id}]
        rows.append(row)
    passed = sum(row["status"] == "PASS" for row in rows)
    return {"schema": "director-completion-scorecard/v1", "generated_at": at.isoformat(),
            "candidate_sha": candidate_sha, "runtime_sha": runtime_sha, "image_id": image_id,
            "passed": passed, "total": 20, "percent": passed * 5, "all_checks_verified": passed == 20,
            "activation_authorized": False, "readiness": "NOT_AUTHORIZED_BY_SCORECARD",
            "limitations": ["This report cannot activate, authorize spending or replace existing policy gates.",
                            "Local collectors trust the named host and service; hashes prove bytes, not authorship.",
                            "Saved JSON/Markdown receipts are references only; they never earn points.",
                            "Collectors cover all twenty checks when their sources and selections are supplied; a missing selection or unavailable source stays UNKNOWN, and health counters alone can only fail 07 and 12.",
                            "Runtime and candidate versions are separate. A candidate review never transfers to another image."],
            "checks": rows, "reference_files": _file_references(evidence_dir)}


def render_markdown(report: dict[str, Any]) -> str:
    def escape(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
    lines = ["# Director completion scorecard", "", f"**{report['passed']}/20 verified ({report['percent']}%)**",
             "", f"Observed: {report['generated_at']}", f"Candidate: `{report['candidate_sha']}`", "",
             "This is a progress display. It grants no activation, spending or merge authority.", "",
             "| # | Required outcome | State | Evidence or next action |", "|---|---|---|---|"]
    for row in report["checks"]:
        detail = row["explanation"] + (" Next: " + row["action"] if row["status"] != "PASS" else "")
        lines.append(f"| {row['id']} | {escape(row['title'])} | **{row['status']}** | {escape(detail)} |")
    lines += ["", "## Evidence", ""]
    for row in report["checks"]:
        for item in row["evidence"]:
            source = item["source"]
            link = f"[source](<{source}>)" if source.startswith(("http://", "https://")) or Path(source).is_absolute() else escape(source)
            lines.append(f"- Check {row['id']}: {link}; observed {item['observed_at']}; expires {item['expires_at']}; SHA256 `{item['sha256']}`; runtime `{item['runtime_sha']}`; image `{item['image_id']}`.")
            if row["id"] == "02":
                lines.append(f"  Recorded inventory: `{escape(json.dumps(item['facts'], sort_keys=True))}`")
    lines += ["", "## Unverified local references", "", "These files do not contribute points. Their hashes identify bytes, not verified claims.", ""]
    for item in report["reference_files"]:
        lines.append(f"- [{escape(Path(item['path']).name)}](<{item['path']}>) — SHA256 `{item['sha256']}`")
    lines += ["", "## Limits", "", *("- " + item for item in report["limitations"]), ""]
    return "\n".join(lines)
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:26 UTC | END]]
