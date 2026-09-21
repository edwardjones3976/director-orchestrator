# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
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
import json
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from .state_witness import StateWitnessLedger, WitnessStatus, gh_checks_green

REPOSITORY = "edwardjones3976/director-orchestrator"
CHANNEL = "C0BQ6GSCNLR"
HEALTH_URL = "http://127.0.0.1:8080/health"
MAX_BYTES = 2 * 1024 * 1024
FRESH_FOR = timedelta(minutes=5)
_COLLECTED = object()


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    scope: str
    action: str


CHECKS = (
    Check("01", "Correct project route and Slack channel", "runtime", "Verify Director 8080, n8n 5678 and the permitted project channel together."),
    Check("02", "One active Slack gateway for this app", "runtime", "Count every Docker gateway sharing the app token and inspect known host gateway launch signatures."),
    Check("03", "Active intake handles rejection and ambiguity correctly", "runtime", "Execute the deployed 4xx, 5xx-after-reservation and transport-failure cases."),
    Check("04", "Status workflow actually delivers to Slack", "runtime", "Read back a fresh scheduled status message and correlate its workflow execution."),
    Check("05", "Primary owns intake with fencing", "runtime", "Observe LangGraph ownership, disabled legacy intake and a positive fencing generation."),
    Check("06", "Checkpoint survives restart", "runtime", "Resume the same bounded task after restart, preserving checkpoint and unique task identity."),
    Check("07", "Primary reconciliation is clear", "runtime", "Collect fresh generation-bound consistency proof over durable projections, batches, assignments and unresolved effects from an authoritative collector. This collector does not exist yet."),
    Check("08", "Owner approval is authenticated and scoped", "runtime", "Verify a real signed owner decision against its delivered prompt and approved work packet."),
    Check("09", "Final reply is durable and unambiguous", "runtime", "Read back exactly one terminal reply; prove duplicate input and ambiguous-post recovery."),
    Check("10", "Ten actual offline Slack runs qualify", "runtime", "Run ten consecutive actual-owner Slack trials with independent correctness, time and zero-call evidence."),
    Check("11", "Real team products and transports are supported", "runtime", "Attest the actual Codex, Grok Bot and Gemini/Antigravity routes; do not substitute direct xAI or Cursor Grok."),
    Check("12", "Native activation evidence is fresh", "runtime", "Verify current native captures, binding digests, identities and expiry through the activation verifier."),
    Check("13", "Rolling twenty admissions recompute reported resource bounds", "coordination", "Use the authoritative append-only assignment ledger, including failed and ambiguous attempts. The ledger does not authenticate operator-reported usage or attestations; it only recomputes reported bound violations from journal rows."),
    Check("14", "Attempts, time and output are bounded", "runtime", "Verify enforced attempt, cancellation deadline and output/token limits on every selected route."),
    Check("15", "Daily monetary cap and actual usage are enforced", "runtime", "Verify enrolled daily budget reservations and correlated actual charges; unknown cost is not zero."),
    Check("16", "Exact candidate CI really executed and passed", "candidate", "Run all six required CI jobs on this candidate; billing stubs and missing jobs do not pass."),
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


def _run_bytes(arguments: list[str]) -> bytes:
    # No shell, secret-bearing stderr, retries or caller-provided commands.
    result = subprocess.run(arguments, capture_output=True, timeout=15, check=False)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError("read-only command failed or exceeded its output bound")
    return result.stdout


def _run_json(arguments: list[str]) -> Any:
    return json.loads(_run_bytes(arguments))


def _health() -> dict[str, Any]:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with build_opener(NoRedirect).open(HEALTH_URL, timeout=5) as response:
        if response.geturl() != HEALTH_URL:
            raise ValueError("health endpoint redirected")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("health response exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("environment") != "local":
        raise ValueError("endpoint is not the local Director")
    return value


def _all_fields(data: dict[str, Any], expected: dict[str, Any]) -> bool | None:
    if any(key not in data for key in expected):
        return None
    # Strict equality avoids bool(unknown string) and bool == 1 surprises.
    return all(type(data[key]) is type(wanted) and data[key] == wanted
               for key, wanted in expected.items())


def _runtime_observations(at: datetime) -> list[Observation]:
    """Observe only the named Director service; never probe sibling endpoints."""
    docker = _run_json(["docker", "inspect", "director-orchestrator-orchestrator-1"])
    service = docker[0]
    labels = service.get("Config", {}).get("Labels") or {}
    runtime_sha = _sha(labels.get("org.director.candidate.commit", ""))
    image_id = service.get("Image", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("runtime image identity unavailable")
    ports = service.get("NetworkSettings", {}).get("Ports", {})
    if not any(p.get("HostIp") == "127.0.0.1" and p.get("HostPort") == "8080"
               for p in ports.get("8080/tcp", []) or []):
        raise ValueError("inspected image is not bound to the Director endpoint")
    health = _health()
    primary = health.get("langgraph_primary", {})
    if not isinstance(primary, dict):
        raise ValueError("primary health is malformed")
    binding = {"runtime_sha": runtime_sha, "image_id": image_id}
    result = []
    owner = _all_fields(primary, {"new_intake_owner": "langgraph", "legacy_new_intake_disabled": True})
    generation = primary.get("intake_fencing_generation")
    if owner is not None:
        owner = owner and type(generation) is int and generation > 0
    result.append(_observation("05", owner, "Observed database-derived intake owner and fencing health.",
                               HEALTH_URL, {key: primary.get(key) for key in
                               ("new_intake_owner", "legacy_new_intake_disabled", "intake_fencing_generation")}, at, **binding))
    blocked = (any(type(primary.get(key)) is int and primary[key] > 0
                   for key in ("ambiguous_effect_count", "historical_parallel_plan_hold_count"))
               or isinstance(primary.get("reconciliation_hold_reason"), str)
               and bool(primary["reconciliation_hold_reason"].strip()))
    # Zero counters and a null hold reason are not consistency proof: release 879
    # reported exactly these while preserved authoritative task/batch plan digests
    # conflicted. Health can only report a blocker; clearance needs the missing
    # generation-bound authoritative consistency collector.
    result.append(_observation("07", False if blocked else None,
                               "Health reports a historical hold, hold reason or ambiguous primary effect." if blocked else
                               "Health hold counters cannot prove reconciliation. Missing collector: generation-bound authoritative consistency of durable projections, batches, assignments and unresolved effects. Worker readiness is scored in 19.",
                               HEALTH_URL, {key: primary.get(key) for key in
                               ("ambiguous_effect_count", "historical_parallel_plan_hold_count", "reconciliation_hold_reason")}, at, **binding))
    active = _all_fields(primary, {"primary_runtime_active": True, "effect_executor_ready": True,
                                   "reconciliation_worker_ready": True})
    if active is not None:
        active = active and health.get("paid_runs_enabled") is True
    result.append(_observation("19", active, "Observed paid switch and operational primary components together.",
                               HEALTH_URL, {"paid": health.get("paid_runs_enabled"), "active": active}, at, **binding))
    activation = primary.get("provider_activation", {})
    if isinstance(activation, dict) and activation.get("combined_live_ready") is False:
        result.append(_observation("12", False, "The running activation verifier reports combined_live_ready=false.",
                                   HEALTH_URL, {"combined_live_ready": False}, at, **binding))
    # A true readiness flag does not expose the capture identity and expiry needed
    # to independently verify check 12; never promote it to a capture attestation.
    return result


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


def _environment(container: dict[str, Any]) -> dict[str, str]:
    return dict(value.split("=", 1) for value in container.get("Config", {}).get("Env", []) if "=" in value)


def _port(container: dict[str, Any], port: int) -> bool:
    return any(row.get("HostIp") == "127.0.0.1" and row.get("HostPort") == str(port)
               for row in container.get("NetworkSettings", {}).get("Ports", {}).get(f"{port}/tcp", []) or [])


def _container_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("expected gateway must be an exact Docker container name")
    return value


def _gateway_launch(container: dict[str, Any]) -> bool:
    command = (container.get("Config", {}).get("Entrypoint") or []) + (container.get("Config", {}).get("Cmd") or [])
    return ("director_orchestrator.slack_bot" in command
            or any(str(arg).replace("\\", "/").endswith("/slack_bot.py") for arg in command))


def _topology_observations(at: datetime, binding: dict[str, str], expected_gateway: str) -> list[Observation]:
    """Inspect every running container; the gateway is the caller's exact name, never a guess."""
    ids = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if not ids or len(ids) > 128 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ValueError("running container inventory unavailable or exceeds 128")
    containers = _run_json(["docker", "inspect", *ids])
    after = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if set(ids) != set(after) or {row.get("Id") for row in containers} != set(ids):
        raise ValueError("container inventory changed during observation")
    by_name = {row.get("Name", "").lstrip("/"): row for row in containers}
    director = by_name["director-orchestrator-orchestrator-1"]
    n8n = by_name["director-orchestrator-n8n-1"]
    if director.get("Image") != binding["image_id"]:
        raise ValueError("Director image changed during topology collection")
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
    workflow = None
    upstream = {}
    try:
        workflow = _run_json(["docker", "exec", "director-orchestrator-n8n-db-1", "psql", "-U", "n8n", "-d", "n8n",
                              "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", _ACTIVE_INTAKE])
        if (isinstance(workflow, dict) and workflow.get("supported_request") is True
                and workflow.get("active_version") and workflow.get("active_version") == workflow.get("stored_version")):
            upstream = _run_json(["docker", "exec", "director-orchestrator-n8n-1", "node", "-e", _UPSTREAM_HEALTH])
    except (OSError, ValueError, subprocess.SubprocessError):
        workflow = None
    channel_ok = gateway_env.get("SLACK_DIRECTOR_CHANNEL_ID") == CHANNEL
    relay = urlsplit(gateway_env.get("N8N_DIRECTOR_WEBHOOK_URL", ""))
    route_ok = (relay.scheme == "http" and relay.hostname in {"n8n", "director-orchestrator-n8n-1"}
                and relay.port == 5678 and relay.path == "/webhook/director-intake"
                and not relay.username and not relay.password and not relay.query and not relay.fragment)
    facts = {**gateway_identity, "director_port_8080": _port(director, 8080), "n8n_port_5678": _port(n8n, 5678),
             "gateway_channel_matches": channel_ok, "gateway_n8n_route_matches": route_ok,
             "n8n_upstream_environment": upstream.get("environment"),
             "active_intake": workflow,
             "containers_running": all(row.get("State", {}).get("Running") is True for row in (director, gateway, n8n))}
    route_pass = _all_fields(facts, {"director_port_8080": True, "n8n_port_5678": True, "gateway_channel_matches": True,
                                   "gateway_n8n_route_matches": True, "containers_running": True})
    workflow_ok = _all_fields(workflow or {}, {"active": True, "http_node_count": 1, "supported_request": True,
                                             "supported_webhook": True, "direct_connection": True})
    if workflow and workflow.get("supported_request") is False and workflow.get("static_request") is not True:
        workflow_ok = None  # An unsupported expression is not a proven wrong destination.
    if route_pass is True:
        route_pass = workflow_ok
        if route_pass is True:
            route_pass = (bool(workflow.get("active_version")) and workflow["active_version"] == workflow.get("stored_version")
                          and upstream.get("environment") == "local")
    result = [_observation("01", route_pass, "Observed Director 8080, n8n 5678, gateway channel/relay and the actual published intake version's supported forwarding URL, then checked that upstream's environment. Missing/unsupported workflow evidence cannot pass. Full task delivery is scored separately.",
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


def _ci_observation(candidate_sha: str, at: datetime) -> Observation:
    # Reuse the project's oracle rather than invent a second CI truth predicate.
    with tempfile.TemporaryDirectory(prefix="director-scorecard-") as folder:
        ledger = StateWitnessLedger(Path(folder) / "witness.sqlite3")
        row = gh_checks_green(ledger, REPOSITORY, candidate_sha, agent="completion-scorecard",
                              required=CI_JOBS, gh_json=lambda args: _run_json(["gh", *args]))
    passed = {WitnessStatus.MATCH: True, WitnessStatus.MISMATCH: False}.get(row.status)
    return _observation("16", passed, "Existing Witness Ledger CI oracle: every required job must have executed steps and concluded success.",
                        f"https://github.com/{REPOSITORY}/commit/{candidate_sha}/checks", row.observed,
                        at, candidate_sha=candidate_sha)


def _workload_observation(path: Path, at: datetime) -> Observation:
    from .engineering_workload_ledger import EngineeringWorkloadLedger

    status = EngineeringWorkloadLedger(path).status()
    # Imported historical attestations are explicitly unauthenticated by this
    # tool. A complete window of actual prospective admissions needs no invented
    # historical filler. This checks journal consistency, not authority to spend.
    passed = None
    explanation = "A complete observed window of twenty prospective admissions is not yet available."
    rows = status.get("records", [])
    consistent = _all_fields(status.get("journal_consistency", {}),
                             {"sqlite_integrity_ok": True, "policy_digest_matches": True})
    if consistent is True and status.get("rolling_window_complete") is True and len(rows) == 20:
        cutover = datetime.fromisoformat(status["cutover_at"].replace("Z", "+00:00"))
        times = [datetime.fromisoformat(row["admitted_at"].replace("Z", "+00:00")) for row in rows]
        if cutover.tzinfo is not None and all(value.tzinfo is not None and cutover < value <= at for value in times):
            passed = not any(row.get("reported_bound_violations") for row in rows)
            explanation = "Recomputed the journal's twenty post-cutover admissions and resource-bound violations; this does not authenticate external attestations or grant dispatch authority."
        else:
            explanation = "This window includes imported, future or unbound history; the tool does not authenticate its attestations."
    facts = {key: status.get(key) for key in ("history_mode", "cutover_at", "rolling_window_complete",
             "real_assignments_in_window", "utilization", "holds", "journal_consistency", "current_window_checks")}
    return _observation("13", passed, explanation, str(path.resolve()), facts, at)


def collect(candidate_sha: str, *, now: datetime | None = None, github: bool = False,
            ledger_path: Path | None = None, gateway: str | None = None, topology: bool = True,
            security_scan_id: str | None = None) -> list[Observation]:
    """One bounded local collection and, only when selected, one GitHub collection.

    Topology (01, 02) is inspected only for an explicitly named running gateway.
    """
    _sha(candidate_sha)
    if gateway is not None:
        _container_name(gateway)
    at = now or datetime.now(timezone.utc)
    result: list[Observation] = []
    try:
        result.extend(_runtime_observations(at))
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        # No exception text: third-party errors can contain credentials or bodies.
        result.append(_observation("19", None, "Director identity/health could not be verified; no retry was made.",
                                   HEALTH_URL, {}, at))
    runtime = next((item for item in result if item.runtime_sha and item.image_id), None)
    if runtime and topology:
        binding = {"runtime_sha": runtime.runtime_sha, "image_id": runtime.image_id}
        reason = "No expected gateway identity was supplied (--gateway); topology was not inspected."
        try:
            if gateway is not None:
                result.extend(_topology_observations(at, binding, gateway))
                reason = ""
        except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
            reason = "Deployment topology could not be verified; no retry was made."
        if reason:
            result.extend(_observation(key, None, reason, "Docker / Windows process inventory", {}, at, **binding)
                          for key in ("01", "02"))
    if github:
        try:
            result.append(_ci_observation(candidate_sha, at))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            result.append(_observation("16", None, "GitHub CI could not be verified; no retry was made.",
                                       "GitHub Actions", {}, at, candidate_sha=candidate_sha))
    if ledger_path is not None:
        try:
            result.append(_workload_observation(ledger_path, at))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            result.append(_observation("13", None, "The assignment journal could not be verified; no retry was made.",
                                       str(ledger_path.resolve()), {}, at))
    if security_scan_id is not None:
        try:
            result.append(_security_observation(security_scan_id, candidate_sha, at))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
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
                            "Collectors currently cover checks 01, 02, 05, 13, 16, 18 and 19, plus negative evidence for 07 and 12; other checks, including authoritative reconciliation consistency for 07, require source-verifier integration.",
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
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
