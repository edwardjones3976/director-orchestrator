# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:39 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:22 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:53 UTC | BEGIN]]
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
import hmac
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
from dataclasses import asdict, dataclass, field, replace
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
_CHANNEL = re.compile(r"[CG][A-Z0-9]{8,20}")
_TASK = re.compile(r"DIR-LG-[A-F0-9]{1,32}")
_TS = re.compile(r"\d{10,}\.\d{1,6}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
DRILL_KINDS = ("refusal-4xx", "reserved-5xx", "transport-failure", "duplicate-input", "ambiguous-post")
# Owner decision 2026-09-23: every runtime lane actually used must be its real product.
# The product transport IDs (primary_transports / primary_parallel) that run each OpenAI product.
_OPENAI_TRANSPORTS = {"responses-api": "openai-responses", "native-cli": "openai-codex",
                      "authenticated-windows-host-bridge": "openai-codex"}
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
    # Owner ruling 2026-09-24 ("Offline = pipeline proof"): 10 proves the offline pipeline end to end; it does
    # not verify answers, which is check 20's job.
    Check("10", "Ten actual offline Slack runs qualify", "runtime", "Run ten consecutive actual-owner Slack trials with a committed structural rubric (pipeline proof, not answer correctness), time and zero-call evidence."),
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
    runtime_tree: str = ""  # the image's tree label; "" when absent or malformed
    collected_at: datetime | None = None  # the collection time: the upper bound of every evidence moment

    @property
    def binding(self) -> dict[str, str]:
        return {"runtime_sha": self.runtime_sha, "image_id": self.image_id}


def _is_current(since: datetime, until: datetime | None, *moments: datetime | None) -> bool:
    """The one freshness rule every collector applies before grading anything, positive or negative.

    Evidence counts only when each of its moments (a Slack post, a task's creation, an execution's
    start) lies in [since, until]: at or after the latest creation of the components that handled it,
    and no later than the collection time. No producer records the executing image, so older evidence
    belongs to a predecessor, and a moment after the collection had not happened when it was read:
    either grades nothing here, and its outcome is UNKNOWN, never PASS and never FAIL.
    """
    return until is not None and all(moment is not None and since <= moment <= until for moment in moments)


def _same(a: Any, b: Any) -> bool:
    """Constant-time equality for secrets and digests; anything but two strings never matches."""
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


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
    tree_label = labels.get("org.director.candidate.tree", "")
    runtime_tree = tree_label if isinstance(tree_label, str) and re.fullmatch(r"[a-f0-9]{40}", tree_label) else ""
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
                       f"http://127.0.0.1:{int(port)}", health, env, runtime_tree, at)
    binding = runtime.binding
    result = []
    # The persisted owner row alone is not ownership: this process must hold the live writer lease.
    owner = _all_fields(primary, {"new_intake_owner": "langgraph", "legacy_new_intake_disabled": True,
                                  "writer_lease_ready": True})
    generation = primary.get("intake_fencing_generation")
    if owner is not None:
        owner = owner and type(generation) is int and generation > 0
    result.append(_observation("05", owner, "Observed database-derived intake owner and fencing health.",
                               health_url, {key: primary.get(key) for key in
                               ("new_intake_owner", "legacy_new_intake_disabled", "intake_fencing_generation",
                                "writer_lease_ready")}, at, **binding))
    active = _all_fields(primary, {"primary_runtime_active": True, "effect_executor_ready": True,
                                   "reconciliation_worker_ready": True})
    if active is not None:
        # A paid runtime is active only with its provider lanes live-activated too.
        active = (active and health.get("paid_runs_enabled") is True
                  and (primary.get("provider_activation") or {}).get("combined_live_ready") is True)
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
_ACTIVE_INTAKE = """BEGIN READ ONLY; SET LOCAL search_path TO public;
SELECT json_build_object('active',w.active,'active_version',w."activeVersionId",'stored_version',h."versionId",
'http_node_count',(SELECT count(*) FROM json_array_elements(h.nodes::json) n WHERE n->>'type'='n8n-nodes-base.httpRequest'),
'static_request',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Create Director Task' AND n->>'type'='n8n-nodes-base.httpRequest' AND n->'parameters'->>'url' LIKE 'http%' AND n->'parameters'->>'url' NOT LIKE '%{{%'),
'supported_request',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Create Director Task' AND n->>'type'='n8n-nodes-base.httpRequest' AND n->'parameters'->>'method'='POST' AND n->'parameters'->>'url'='http://orchestrator:8080/v1/intake'),
'supported_webhook',EXISTS(SELECT 1 FROM json_array_elements(h.nodes::json) n WHERE n->>'name'='Slack Gateway Webhook' AND n->>'type'='n8n-nodes-base.webhook' AND n->'parameters'->>'httpMethod'='POST' AND n->'parameters'->>'path'='director-intake'),
'direct_connection',h.connections::json->'Slack Gateway Webhook'->'main'->0->0->>'node'='Create Director Task',
'nodes',h.nodes::json,'connections',h.connections::json,'settings',w.settings::json,'pin_data',w."pinData"::json)
FROM workflow_entity w LEFT JOIN workflow_history h ON h."workflowId"=w.id AND h."versionId"=w."activeVersionId"
WHERE w.id='directorIntake'; COMMIT;"""
# Newest saved, successful, scheduled Director Status run that posted, within the
# 72-hour retention window; the Slack node's response is read from its data.
_STATUS_EXECUTION = """BEGIN READ ONLY; SET LOCAL search_path TO public;
SELECT json_build_object('workflows',(SELECT count(*) FROM workflow_entity WHERE name='Director Status'),
 'latest',(SELECT row_to_json(y) FROM (SELECT e.id,e.status,e.finished,e."startedAt",e."stoppedAt"
   FROM workflow_entity w JOIN execution_entity e ON e."workflowId"=w.id
   WHERE w.name='Director Status' AND e.mode='trigger' AND e."startedAt" > now()-interval '72 hours'
   ORDER BY e."startedAt" DESC, e.id DESC LIMIT 1) y),
 'execution',(SELECT row_to_json(x) FROM (SELECT e.id,e.status,e.finished,e.mode,e."startedAt",e."stoppedAt",d.data
   ,w.active AS workflow_active,w."activeVersionId" AS active_version,
     to_jsonb(e)->>'workflowVersionId' AS execution_version,w."updatedAt" AS workflow_updated_at,
     (SELECT h."createdAt" FROM workflow_history h WHERE h."workflowId"=w.id AND h."versionId"=w."activeVersionId")
       AS active_version_at,
     (SELECT h.nodes::json FROM workflow_history h WHERE h."workflowId"=w.id AND h."versionId"=w."activeVersionId")
       AS active_nodes,
     (SELECT h.connections::json FROM workflow_history h
        WHERE h."workflowId"=w.id AND h."versionId"=w."activeVersionId") AS active_connections,
     w.settings::json AS workflow_settings, w."pinData"::json AS pin_data
   FROM workflow_entity w JOIN execution_entity e ON e."workflowId"=w.id JOIN execution_data d ON d."executionId"=e.id
   WHERE w.name='Director Status' AND e.status='success' AND e.finished AND e.mode='trigger'
     AND e."stoppedAt" > now()-interval '72 hours' AND length(d.data)<=600000 AND d.data LIKE '%"Post Status"%'
   ORDER BY e."stoppedAt" DESC LIMIT 1) x)); COMMIT;"""


_REPO_ROOT = Path(__file__).resolve().parents[2]
# The in-process renderer checks 08 and 20 trust: they must be the running candidate's own files.
_RENDERER_FILES = ("src/director_orchestrator/primary_slack_delivery.py", "src/director_orchestrator/contribution_tags.py",
                   "src/director_orchestrator/models.py")


def _candidate_blob(sha: str, path: str) -> bytes | None:
    """A file exactly as the candidate commit holds it (read-only git show), or None when unavailable."""
    try:
        return _run_bytes(["git", "-C", str(_REPO_ROOT), "show", f"{_sha(sha)}:{path}"])
    except _SOURCE_ERRORS:
        return None


def _candidate_renderer(sha: str) -> bool:
    """The scorecard's own renderer files are byte-for-byte the candidate's (line endings aside)."""
    for path in _RENDERER_FILES:
        blob = _candidate_blob(sha, path)
        try:
            local = (_REPO_ROOT / path).read_bytes()
        except OSError:
            return False
        if blob is None or blob.replace(b"\r\n", b"\n") != local.replace(b"\r\n", b"\n"):
            return False
    return True


# Node fields n8n or the operator sets at deploy time and that never change behaviour. Everything else
# (type, typeVersion, parameters, disabled, retry and error settings, credential types, the connection
# graph and the node set) must equal the repository workflow.
_WORKFLOW_DEPLOY_FIELDS = frozenset({"id", "position", "webhookId", "credentials"})


def _workflow_matches(sha: str, reference: str, nodes: Any, connections: Any,
                      bound: dict[str, tuple[tuple[str, ...], Any]] | None = None, *,
                      settings: Any, pin_data: Any) -> bool | None:
    """The active workflow version is exactly n8n/workflows/<reference>, node for node and edge for edge.

    Allowed to differ: node ids, canvas positions, webhookId, credential ids and names (the credential
    types must match), and the parameter values the collector binds separately (``bound``: node name ->
    (parameter path, expected value)), such as the project channel.
    """
    # The contract is the candidate commit's workflow file, never an unverified working tree.
    blob = _candidate_blob(sha, "n8n/workflows/" + reference)
    if blob is None:
        return None
    document = json.loads(blob.decode("utf-8"))
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        return False

    def normal(node: dict[str, Any]) -> dict[str, Any]:
        out = {key: value for key, value in node.items() if key not in _WORKFLOW_DEPLOY_FIELDS}
        out["disabled"] = node.get("disabled", False) is True  # n8n skips a disabled node
        out["credentials"] = sorted(node.get("credentials") or {}) if isinstance(node.get("credentials") or {}, dict) else None
        return out

    expected = {node["name"]: normal(json.loads(json.dumps(node))) for node in document["nodes"]}
    for name, (path, value) in (bound or {}).items():
        target = expected[name]["parameters"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    active = {node.get("name"): normal(node) for node in nodes}
    # Workflow-level settings (execution order, timeouts, save-data policy, error workflow, timezone) change
    # how n8n runs the graph, so they must equal the file too; pinned node data must be absent. Tags and
    # static data (trigger bookkeeping) change nothing at run time and are not compared.
    return (len(active) == len(nodes) and active == expected and connections == document["connections"]
            and settings == document["settings"] and pin_data in (None, {}))


def _environment(container: dict[str, Any]) -> dict[str, str]:
    return dict(value.split("=", 1) for value in container.get("Config", {}).get("Env", []) if "=" in value)


def _loopback(container: dict[str, Any], port: int) -> bool:
    rows = (container.get("NetworkSettings", {}).get("Ports") or {}).get(f"{port}/tcp") or []
    return len(rows) == 1 and rows[0].get("HostIp") == "127.0.0.1"


def _container_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("expected gateway must be an exact Docker container name")
    return value


# Every setting slack_bot.py reads that the Director must share (bearer, intake signing secret, Slack bot,
# owner and channel). test_gateway_identities_cover_every_gateway_secret derives the gateway's secret and
# identity reads from slack_bot.py and config.py, so a new one cannot be missed silently. SLACK_APP_TOKEN
# is gateway-only; its app identity is check 02's.
# The proof version the gateway's signed approval command carries (models.SlackGatewayProof).
_APPROVAL_PROOF_VERSION = "v1"
_GATEWAY_IDENTITIES = ("SLACK_BOT_TOKEN", "SLACK_OWNER_USER_ID", "SLACK_DIRECTOR_CHANNEL_ID", "ORCHESTRATOR_SHARED_SECRET",
                       "SLACK_GATEWAY_SECRET")
_GATEWAY_ONLY = ("SLACK_APP_TOKEN",)


def _gateway_credentials_match(gateway_env: dict[str, str], director_env: dict[str, str]) -> bool:
    """The gateway holds the Director's Slack bot, owner, channel and bearer (constant time, never reported)."""
    return all(_same(gateway_env.get(name), director_env.get(name)) for name in _GATEWAY_IDENTITIES)


def _slack_app_id(token: str) -> str | None:
    """The Slack app ID an app-level token names; never the token or any part of its secret."""
    match = re.fullmatch(r"xapp-\d+-(A[A-Z0-9]{6,20})-\d+-[A-Za-z0-9]+", token or "")
    return match.group(1) if match else None


def _gateway_launch(container: dict[str, Any]) -> bool:
    command = (container.get("Config", {}).get("Entrypoint") or []) + (container.get("Config", {}).get("Cmd") or [])
    return ("director_orchestrator.slack_bot" in command
            or any(str(arg).replace("\\", "/").endswith("/slack_bot.py") for arg in command))


def _networks(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    value = ((row or {}).get("NetworkSettings") or {}).get("Networks")
    return [a for a in value.values() if isinstance(a, dict) and a.get("NetworkID")] if isinstance(value, dict) else []


def _hop(source: dict[str, Any] | None, target: dict[str, Any] | None, host: str | None,
         containers: list[dict[str, Any]]) -> bool | None:
    """From docker inspect only: source and target share a network on which target, and no
    other container, answers to host. None when either shows no network attachments."""
    ours, theirs = _networks(source), _networks(target)
    if not ours or not theirs:
        return None
    # /etc/hosts wins over network DNS: an ExtraHosts entry for the name sends the hop elsewhere.
    config = (source or {}).get("HostConfig") or {}
    extra = config.get("ExtraHosts") or []
    if not isinstance(extra, list) or any(str(entry).split(":", 1)[0].split("=", 1)[0].strip() == host
                                          for entry in extra):
        return False
    # Host networking, replaced hosts or resolver files, and custom resolvers or search domains change
    # resolution in ways inspect cannot prove: unproven, not wrong.
    mounts = (source or {}).get("Mounts") or []
    if (config.get("NetworkMode") == "host" or config.get("Dns") or config.get("DnsSearch")
            or any(isinstance(m, dict) and m.get("Destination") in ("/etc/hosts", "/etc/resolv.conf") for m in mounts)):
        return None
    reachable = {attach["NetworkID"] for attach in ours}

    def answers(attach: dict[str, Any]) -> bool:
        return host in {*(attach.get("Aliases") or []), *(attach.get("DNSNames") or [])}

    if not any(attach["NetworkID"] in reachable and answers(attach) for attach in theirs):
        return False
    # The source resolves host on every network it is attached to: another owner of the
    # name on any of them makes resolution ambiguous, so the hop is not bound to target.
    return not any(row.get("Id") != (target or {}).get("Id") and other["NetworkID"] in reachable and answers(other)
                   for row in containers for other in _networks(row))


def _relay_route_ok(relay: Any, n8n: dict[str, Any] | None) -> bool:
    """The gateway's relay URL is n8n's intake webhook: plain http to n8n (or its container name) on 5678."""
    n8n_name = (n8n or {}).get("Name", "").lstrip("/")
    return (relay.scheme == "http" and relay.hostname in {"n8n", *([n8n_name] if n8n_name else [])}
            and relay.port == 5678 and relay.path == "/webhook/director-intake"
            and not relay.username and not relay.password and not relay.query and not relay.fragment)


def _intake_route_verified(runtime: _Runtime, gateway: str) -> bool | None:
    """Check 01's route predicates, re-evaluated now for the Slack-derived checks (03, 08, 09, 10, 20).

    The selected gateway's relay URL, its credentials, the gateway-to-n8n and n8n-to-Director hops, n8n's
    own execution store, and the active intake workflow version equal to the candidate's workflow file.
    Historical Slack evidence says nothing about an intake route that no longer holds.
    """
    ids = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if not ids or len(ids) > 128 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ValueError("running container inventory unavailable or exceeds 128")
    containers = _run_json(["docker", "inspect", *ids])

    def service(name: str) -> list[dict[str, Any]]:
        return [row for row in containers if _labels(row).get("com.docker.compose.project") == runtime.project
                and _labels(row).get("com.docker.compose.service") == name]

    gate = next((row for row in containers if row.get("Name", "").lstrip("/") == gateway), None)
    n8n, databases = service("n8n"), service("n8n-db")
    director = next((row for row in containers if row.get("Id") == runtime.container_id), None)
    if gate is None or director is None or len(n8n) != 1 or len(databases) != 1:
        return None
    env = _environment(gate)
    relay = urlsplit(env.get("N8N_DIRECTOR_WEBHOOK_URL", ""))
    if not _relay_route_ok(relay, n8n[0]) or not _gateway_credentials_match(env, runtime.env):
        return False
    hops = (_hop(gate, n8n[0], relay.hostname, containers), _hop(n8n[0], director, "orchestrator", containers),
            _n8n_store(n8n[0], databases[0], containers))
    if not all(hop is True for hop in hops):
        return False if False in hops else None
    workflow = _run_json(["docker", "exec", databases[0]["Id"], "psql", "-U", "n8n", "-d", "n8n",
                          "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", _ACTIVE_INTAKE])
    if (not isinstance(workflow, dict) or workflow.get("active") is not True or not workflow.get("active_version")
            or workflow.get("active_version") != workflow.get("stored_version")):
        return False
    return _workflow_matches(runtime.runtime_sha, "director-intake.json", workflow.get("nodes"),
                             workflow.get("connections"), settings=workflow.get("settings"),
                             pin_data=workflow.get("pin_data"))


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
    # Only n8n's own execution store says which intake workflow it runs.
    store = _n8n_store(n8n, n8n_db, containers) if n8n is not None else None
    if n8n is not None and n8n_db is not None and store is True:
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
    route_ok = _relay_route_ok(relay, n8n)
    facts = {**gateway_identity, "director_port_loopback": _loopback(director, 8080),
             "n8n_port_loopback": _loopback(n8n, 5678) if n8n is not None else None,
             "n8n_services_found": [len(n8n_rows), len(db_rows)], "gateway_in_deployment": in_deployment,
             "gateway_channel_matches": channel_ok, "gateway_n8n_route_matches": route_ok,
             # n8n forwards the gateway's Authorization unchanged: another bearer is refused by the Director.
             "gateway_credentials_match": _gateway_credentials_match(gateway_env, runtime.env),
             "gateway_n8n_network_shared": _hop(gateway, n8n, relay.hostname, containers),
             "n8n_director_network_shared": _hop(n8n, director, "orchestrator", containers),
             "n8n_upstream_environment": upstream.get("environment"), "n8n_store_bound": store,
             "active_intake": workflow,
             "containers_running": all(row is not None and row.get("State", {}).get("Running") is True
                                       for row in (director, gateway, n8n))}
    route_pass = _all_fields(facts, {"director_port_loopback": True, "n8n_port_loopback": True, "gateway_channel_matches": True,
                                   "gateway_n8n_route_matches": True, "containers_running": True,
                                   "gateway_n8n_network_shared": True, "n8n_director_network_shared": True,
                                   "gateway_credentials_match": True})
    hops = (facts["gateway_n8n_network_shared"], facts["n8n_director_network_shared"])
    if route_pass is False and None in hops and False not in hops and all(
            facts[key] is True for key in ("director_port_loopback", "n8n_port_loopback", "gateway_channel_matches",
                                           "gateway_n8n_route_matches", "containers_running")):
        route_pass = None  # Network attachment not shown by docker inspect: unproven, not wrong.
    if isinstance(workflow, dict):
        # Headers, signed body, timeout, redirect, response and wiring exactly as the repository's intake workflow.
        workflow["forwarding_contract"] = _workflow_matches(runtime.runtime_sha, "director-intake.json",
                                                            workflow.pop("nodes", None),
                                                            workflow.pop("connections", None),
                                                            settings=workflow.pop("settings", None),
                                                            pin_data=workflow.pop("pin_data", None))
    workflow_ok = _all_fields(workflow or {}, {"active": True, "http_node_count": 1, "supported_request": True,
                                             "supported_webhook": True, "direct_connection": True,
                                             "forwarding_contract": True})
    if workflow and workflow.get("supported_request") is False and workflow.get("static_request") is not True:
        workflow_ok = None  # An unsupported expression is not a proven wrong destination.
    if workflow and workflow.get("forwarding_contract") is None:
        workflow_ok = None  # The candidate's workflow file could not be read: unproven, not wrong.
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
    # A Slack app-level token carries its app's ID (xapp-<n>-<APP_ID>-...): that, not one credential
    # instance, is the gateway's identity, so a rotated or second token of the same app still matches.
    app = _slack_app_id(gateway_env.get("SLACK_APP_TOKEN", ""))
    if app is None:
        return result + [_observation("02", None, "Canonical gateway app identity unavailable.", "Docker gateway", gateway_identity, at, **binding)]
    matches, unknown = [], 0
    for row in containers:
        token = _environment(row).get("SLACK_APP_TOKEN", "")
        if not token:
            continue
        other = _slack_app_id(token)
        if other is None:
            unknown += 1  # an app token whose app cannot be read may be this app's
            continue
        if other != app:
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
    result.append(_observation("02", passed, "Compared the Slack app ID named by each running container's app token and inspected known Windows gateway launch signatures. Unreadable/unclassified or host gateway processes require reconciliation; custom or remote launches are not attested.",
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
_RECEIPT = re.compile(r"\*?(DIR-LG-[A-F0-9]+)\s+—\s+(?:ACCEPTED|REQUEST RECEIVED)\*?")
_BLOCKED = "*DIRECTOR — BLOCKED*"


def _thread_counts(t: _SlackThreads, thread: str,
                   messages: list[dict[str, Any]]) -> tuple[int, list[str], int, int]:
    owner_inputs = sum(1 for m in messages if m.get("user") == t.owner and not m.get("bot_id") and t.m.unedited(m)
                       and (m.get("ts") == thread or m.get("thread_ts") == thread))
    replies = [m for m in messages if m.get("user") == t.bot and m.get("thread_ts") == thread]
    # The bot never edits its replies: an edited or subtyped one no longer shows what was
    # posted, so its text is never parsed and the caller grades nothing in that thread. An edited
    # or subtyped owner post is a second, altered input the drill did not isolate: same rule.
    posts = [m for m in messages if m.get("user") == t.owner and not m.get("bot_id")
             and (m.get("ts") == thread or m.get("thread_ts") == thread)]
    altered = sum(1 for m in replies + posts if not t.m.unedited(m))
    bot = [html.unescape(str(m.get("text", ""))) for m in replies if t.m.unedited(m)]
    # At most one receipt per reply, and only as its header: a quoted or repeated receipt is not another.
    receipts = [match.group(1) for text in bot if (match := _RECEIPT.fullmatch(t.m.reply_header(text)))]
    blocked = sum(1 for text in bot if t.m.reply_header(text) == _BLOCKED)
    return owner_inputs, receipts, blocked, altered


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
                "succeeded_projections_with_unsucceeded_assignments", "projections_missing_batch",
                "batches_without_projection",
                "terminal_tasks_open_assignments", "terminal_tasks_open_effects", "terminal_batches_unbound",
                "projection_batch_digest_conflicts", "projections_ahead_of_fence")
_INFLIGHT_COUNTS = ("open_effect_reservations", "active_assignments", "live_writer_claims",
                    "batches_pending_projection_binding", "pending_owner_decisions")
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
    captured = consistency.get("captured_at")
    if (not isinstance(pin, str) or not _HEX64.fullmatch(pin)
            or not isinstance(captured, str) or _when(captured) > at
            or not _same(pin, runtime.env.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"))
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
    if not pin or not _same(captures.get("generation_manifest_sha256"), pin):
        return None, "The captures are not bound to the running generation pin.", {}
    raw = runtime.env.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON")
    if raw is None:
        return None, "The running provider bindings are unavailable.", {}
    if not _same(captures.get("provider_bindings_sha256"), hashlib.sha256(raw.encode()).hexdigest()):
        return False, "The captures are bound to other provider bindings than the running ones.", {}
    lanes = captures.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        return None, "No activation lanes were reported.", {}
    required = captures.get("required_providers")
    if not isinstance(required, list) or set(lanes) != set(required):
        return False, "The captured lanes are not exactly the generation's required providers.", {
            "required_providers": required if isinstance(required, list) else None, "captured_lanes": sorted(lanes)}
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
            # A capture not yet observed at collection time is future-dated, not current.
            observed = _when(surface["observed_at"]) if surface.get("observed_at") else None
            if (surface.get("stage") != "LIVE_OK" or surface.get("capture_verified") is not True or expires < at + FRESH_FOR
                    or observed is None or observed > at):
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
        # A capture that does not verify now, was minted after the attempt or expired before it ended
        # cannot attest the attempt: its missing product is unknown, not a substitution.
        # observed_at is caller-supplied: the row's persisted recorded_at must also precede the attempts.
        if (not isinstance(surface, dict) or surface.get("capture_verified") is not True or not times
                or not surface.get("observed_at") or not surface.get("recorded_at")
                or not _when(surface["observed_at"]) <= min(times) <= max(times) < _when(surface["expires_at"])
                or not _when(surface["recorded_at"]) <= min(times)
                or runtime.collected_at is None or max(times) > runtime.collected_at):
            unbound.append(lane)
    facts = {"lanes_used": used, "products": products, "transports": transports}
    raw = runtime.env.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON")
    if (runtime.created_at > _when(paid["readback"]["task_created_at"]) or raw is None
            or not _same(captures.get("generation_manifest_sha256"),
                         runtime.env.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"))
            or not _same(captures.get("provider_bindings_sha256"), hashlib.sha256(raw.encode()).hexdigest())):
        return _observation("11", None, "The paid task predates the running container, or the captures are not bound to "
                            "its generation and provider bindings; they cannot attest that run.",
                            _CAPTURES_SOURCE, facts, at, **binding)

    # The OpenAI lane has two real products; each attempt's transport must be a known one of them.
    # A capture that is not a real OpenAI product is a substitution whatever the transport.
    if ("openai" in used and "openai" not in unbound and products.get("openai") in REAL_PRODUCTS["openai"]
            and not set(transports.get("openai", [])) <= set(_OPENAI_TRANSPORTS)):
        unbound.append("openai")  # an unknown transport is unattested, never assumed to be Codex

    def attested(lane: str) -> bool:
        if lane not in REAL_PRODUCTS or products[lane] not in REAL_PRODUCTS[lane]:
            return False
        # The capture must be the product the attempts' transport used.
        return lane != "openai" or {_OPENAI_TRANSPORTS[transport] for transport in transports[lane]} == {products[lane]}

    substituted = [lane for lane in used if lane not in REAL_PRODUCTS or (lane not in unbound and not attested(lane))]
    if substituted:
        return _observation("11", False, "Lanes used without an attested real product: " + ", ".join(substituted) + ".",
                            _CAPTURES_SOURCE, facts, at, **binding)
    if unbound:
        return _observation("11", None, "No verified capture observed before and unexpired through the paid attempts of: "
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
    # Forks share commit SHAs: an adoption for another repository cannot accept this one's candidate.
    if adoption.get("repository") != REPOSITORY:
        return _observation("17", None, "The adopted acceptance is not for " + REPOSITORY + ".",
                            source, facts, at, candidate_sha=candidate_sha)
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


def _n8n_store(n8n: dict[str, Any] | None, database: dict[str, Any] | None,
               containers: list[dict[str, Any]]) -> bool | None:
    """From docker inspect only: n8n's own DB_* settings name the n8n/n8n PostgreSQL database on the
    queried container, which alone answers to that host on n8n's networks. No value is ever reported."""
    env = _environment(n8n or {})
    # Only the default public schema is supported; both n8n queries pin search_path to it.
    if (env.get("DB_POSTGRESDB_SCHEMA", "public") != "public"
            or env.get("DB_TYPE") != "postgresdb" or env.get("DB_POSTGRESDB_DATABASE") != "n8n"
            or env.get("DB_POSTGRESDB_USER") != "n8n" or env.get("DB_POSTGRESDB_PORT", "5432") != "5432"
            or not env.get("DB_POSTGRESDB_HOST") or database is None):
        return False
    # n8n must be able to open that store: the server's user, database and password are n8n's (the
    # password compared in memory, in constant time, never reported).
    served = _environment(database)
    if not (_same(served.get("POSTGRES_USER"), env["DB_POSTGRESDB_USER"])
            and _same(served.get("POSTGRES_DB", served.get("POSTGRES_USER")), env["DB_POSTGRESDB_DATABASE"])
            and _same(served.get("POSTGRES_PASSWORD"), env.get("DB_POSTGRESDB_PASSWORD"))):
        return False
    return _hop(n8n, database, env["DB_POSTGRESDB_HOST"], containers)


def _n8n_bindings(runtime: _Runtime) -> tuple[bool | None, bool | None, str | None, datetime | None]:
    """(n8n reaches the selected Director as orchestrator, n8n's store is the queried n8n-db, that db's ID,
    n8n's creation time)."""
    ids = _run_bytes(["docker", "ps", "-q", "--no-trunc"]).decode().split()
    if not ids or len(ids) > 128 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ValueError("running container inventory unavailable or exceeds 128")
    containers = _run_json(["docker", "inspect", *ids])

    def service(name: str) -> list[dict[str, Any]]:
        return [row for row in containers if _labels(row).get("com.docker.compose.project") == runtime.project
                and _labels(row).get("com.docker.compose.service") == name]

    n8n, databases = service("n8n"), service("n8n-db")
    director = next((row for row in containers if row.get("Id") == runtime.container_id), None)
    if len(n8n) != 1 or len(databases) != 1 or director is None:
        return None, None, None, None
    return (_hop(n8n[0], director, "orchestrator", containers), _n8n_store(n8n[0], databases[0], containers),
            databases[0].get("Id"), _when(n8n[0]["Created"]))


def _gateway_bound(runtime: _Runtime, gateway: str) -> _Runtime:
    """The runtime whose freshness bound is the latest creation of the Director, the selected gateway and
    the deployment's n8n.

    Slack intake passes through the gateway and n8n, so a drill, trial, approval or paid run handled by a
    predecessor of either cannot attest the one running now.
    """
    row = _run_json(["docker", "inspect", gateway])[0]
    if (row.get("Name", "").lstrip("/") != gateway or row.get("State", {}).get("Running") is not True
            or not _gateway_launch(row) or _labels(row).get("com.docker.compose.project") != runtime.project):
        raise ValueError("the selected gateway is not a running gateway of this deployment")
    # The readback uses the Director's Slack bot, owner, channel and bearer: the gateway that handled the
    # threads must hold the same ones (compared in constant time, never reported).
    if not _gateway_credentials_match(_environment(row), runtime.env):
        raise ValueError("the selected gateway does not hold the Director's Slack and bearer identities")
    relays = _compose_ids(runtime.project, "n8n")
    if len(relays) != 1:
        raise ValueError("the selected deployment does not run exactly one n8n")
    relay = _run_json(["docker", "inspect", relays[0]])[0]
    if relay.get("Id") != relays[0] or relay.get("State", {}).get("Running") is not True:
        raise ValueError("the deployment's n8n is not running")
    return replace(runtime, created_at=max(runtime.created_at, _when(row["Created"]), _when(relay["Created"])))


def _status_observation(at: datetime, runtime: _Runtime, t: _SlackThreads) -> Observation:
    binding = runtime.binding
    source = "n8n Director Status saved execution (READ ONLY) and Slack conversations.replies"
    hop, store, database, n8n_created = _n8n_bindings(runtime)
    if database is None:
        return _observation("04", None, "The selected deployment does not run exactly one n8n and one n8n-db service.",
                            source, {}, at, **binding)
    # The status workflow reads health from orchestrator: timing alone cannot say which Director that was.
    if hop is not True:
        return _observation("04", None, "The deployment's n8n is not shown to reach the selected Director, and only "
                            "it, as orchestrator; its status posts cannot attest this runtime.", source, {}, at, **binding)
    if store is not True:
        return _observation("04", None, "n8n's own database settings do not name the queried n8n-db as its only "
                            "execution store; its saved executions cannot be read as n8n's.", source, {}, at, **binding)
    found = _run_json(["docker", "exec", database, "psql", "-U", "n8n", "-d", "n8n",
                       "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", _STATUS_EXECUTION])
    if not isinstance(found, dict) or found.get("workflows") != 1 or not isinstance(found.get("execution"), dict):
        return _observation("04", None, "No single Director Status workflow has a saved successful posting execution "
                            "in the last 72 hours.", source, {}, at, **binding)
    execution = found["execution"]
    # The newest scheduled run, of any outcome, is graded first: a retained older success behind newer
    # failures is not operational delivery. (Runs post only on a readiness change, so the graded post
    # may be older than a newer successful run that had nothing to post.)
    latest = found.get("latest") if isinstance(found.get("latest"), dict) else {}
    if latest.get("status") in ("error", "crashed", "failed", "canceled"):
        return _observation("04", False, "The newest scheduled Director Status run did not succeed.",
                            source, {"latest_execution_id": latest.get("id")}, at, **binding)
    if (latest.get("status") != "success" or latest.get("finished") is not True
            or not isinstance(latest.get("startedAt"), str) or _when(latest["startedAt"]) < _when(execution["startedAt"])):
        return _observation("04", None, "The newest scheduled Director Status run is unfinished or not shown to "
                            "have succeeded.", source, {"latest_execution_id": latest.get("id")}, at, **binding)
    if execution.get("workflow_active") is not True:
        return _observation("04", None, "The Director Status workflow is not active now; a retained execution cannot "
                            "show that status delivery is operational.", source, {}, at, **binding)
    if _workflow_matches(runtime.runtime_sha, "director-status.json", execution.get("active_nodes"),
                         execution.get("active_connections"),
                             {"Post Status": (("channelId", "value"), t.channel)},
                         settings=execution.get("workflow_settings"), pin_data=execution.get("pin_data")) is not True:
        return _observation("04", None, "The active Director Status version is not exactly "
                            "n8n/workflows/director-status.json (every node, setting and edge), posting to the "
                            "project channel.",
                            source, {}, at, **binding)
    out = _flatted(execution["data"])["resultData"]["runData"]["Post Status"][-1]["data"]["main"][0][0]["json"]
    ts = out.get("ts") or (out.get("message") or {}).get("ts")
    started, stopped = _when(execution["startedAt"]), _when(execution["stoppedAt"])
    well_formed = isinstance(ts, str) and bool(_TS.fullmatch(ts))
    facts = {"execution_id": execution.get("id"), "stopped_at": stopped.isoformat(), "message_ts": ts if well_formed else None}
    # The execution must have run the version active now: by its recorded version where n8n
    # stores one, otherwise by starting after that version existed and the workflow last changed.
    version = execution.get("execution_version")
    if version is not None:
        current = isinstance(version, str) and bool(version) and version == execution.get("active_version")
    else:
        marks = (execution.get("active_version_at"), execution.get("workflow_updated_at"))
        current = all(isinstance(mark, str) for mark in marks) and started >= max(_when(mark) for mark in marks)
    if not current:
        return _observation("04", None, "The saved execution is not shown to have run the workflow's active version.",
                            source, facts, at, **binding)
    # No producer records the posting image: an execution that started before the
    # running container was created was posted by an earlier runtime.
    if not _is_current(max(runtime.created_at, n8n_created), runtime.collected_at, started, stopped):
        return _observation("04", None, "The newest saved status execution started before the running Director "
                            "container or the running n8n was created, so it cannot attest them.",
                            source, facts, at, **binding)
    if (out.get("ok") is not True or not well_formed or out.get("channel") != t.channel
            or not Decimal(str(started.timestamp())) <= Decimal(ts) <= Decimal(str(stopped.timestamp())) + 1):
        return _observation("04", None, "The saved execution's Slack response is not a correlated post to the project channel.",
                            source, facts, at, **binding)
    messages, complete, _ = t.replies(ts)
    posted = [m for m in messages if m.get("ts") == ts]
    if any(not t.m.unedited(m) for m in posted):
        return _observation("04", None, "The message at the execution's posted timestamp was edited or has a subtype, "
                            "so its text is not what the execution posted.", source, facts, at, **binding)
    # The read-back text is exactly what the execution's Slack response says it posted.
    sent = (out.get("message") or {}).get("text")
    status = [m for m in posted if m.get("bot_id") and m.get("user") == t.bot and isinstance(sent, str)
              and html.unescape(str(m.get("text", ""))) == html.unescape(sent)
              and html.unescape(sent).startswith("*STATUS —")]
    if complete and len(posted) == 1 and len(status) == 1:
        return _observation("04", True, "A saved successful scheduled execution's Slack post was read back as the bot's "
                            "STATUS message in the project channel.", source, facts, at, **binding)
    if complete and posted and not status:
        return _observation("04", False, "The message at the execution's posted timestamp is not the bot's STATUS message.",
                            source, facts, at, **binding)
    return _observation("04", None, "The status message readback was incomplete.", source, facts, at, **binding)


def _delivered_final(readback: dict[str, Any] | None, snapshot: dict[str, Any] | None) -> bool:
    """The one final-reply correlation every drill branch uses: a single terminal delivery row, DELIVERED
    and read back with its Slack ts, matching the native final message."""
    terminal = [r for r in (readback or {}).get("slack_deliveries") or [] if r.get("notification_key") == "terminal"]
    return (len(terminal) == 1 and terminal[0].get("state") == "DELIVERED" and bool(terminal[0].get("delivered_at"))
            and bool(terminal[0].get("slack_ts"))
            and ((snapshot or {}).get("row") or {}).get("final_receipt_matches_native_message") is True)


def _moments(snapshot: dict[str, Any] | None) -> list[datetime]:
    """Every Slack and readback moment a task verdict rests on: task creation and update, each delivery's
    creation, claim, acknowledgement, delivery and owner decision, each provider attempt, the owner input
    and the terminal replies. All of them must fall inside the freshness bound."""
    readback = (snapshot or {}).get("readback") or {}
    row = (snapshot or {}).get("row") or {}
    moments = [_when(readback[key]) for key in ("task_created_at", "task_updated_at") if readback.get(key)]
    for delivery in readback.get("slack_deliveries") or []:
        moments += [_when(delivery[key]) for key in ("created_at", "claimed_at", "acknowledged_at", "delivered_at",
                                                     "owner_decision_at") if delivery.get(key)]
    moments += [_when(event["recorded_at"]) for event in readback.get("provider_attempts") or []]
    stamps = [row.get("input_message_ts"), *(m.get("ts") for m in row.get("terminal_messages") or [])]
    moments += [datetime.fromtimestamp(float(Decimal(str(ts))), timezone.utc) for ts in stamps if ts]
    return moments


def _drill_verdict(t: _SlackThreads, kind: str, thread: str, since: datetime,
                   until: datetime | None = None) -> tuple[bool | None, dict[str, Any]]:
    messages, complete, _ = t.replies(thread)
    owner_inputs, receipts, blocked, altered = _thread_counts(t, thread, messages)
    if altered:
        return None, {"altered_bot_replies": altered, "verdict": None}
    bound = t.binding(thread)
    status = 404 if bound is None else 409 if bound == "AMBIGUOUS" else 200
    facts: dict[str, Any] = {"owner_inputs": owner_inputs, "blocked": blocked, "receipts": len(receipts),
                             "altered_bot_replies": altered,
                             "binding_status": status, "thread_tasks": None}
    snapshot = _task_facts(t, thread, bound, (messages, complete, None)) if status == 200 and kind != "refusal-4xx" else None
    readback = (snapshot or {}).get("readback")
    tasks = readback["task_scope_counters"]["thread_tasks"] if readback else None
    replies = snapshot["row"]["terminal_reply_count"] if snapshot else None
    facts.update(thread_tasks=tasks, terminal_reply_count=replies)
    # No producer records the executing image: a drill posted, or a task created,
    # before the running container was created cannot attest it.
    current = _is_current(since, until, datetime.fromtimestamp(float(Decimal(thread)), timezone.utc),
                          *_moments(snapshot))
    facts["after_container_created"] = current
    verdict: bool | None = None
    if not current:
        facts["verdict"] = None
        return None, facts
    if kind == "refusal-4xx":
        if status != 404 or receipts:
            verdict = False
        elif complete and owner_inputs == 1 and blocked == 1:
            verdict = True
    elif kind == "reserved-5xx":
        if status == 409 or (tasks is not None and tasks > 1):
            verdict = False
        # A receipt means the gateway answered 202, so the reserved 5xx was not exercised.
        elif status == 200 and complete and owner_inputs == 1 and blocked == 1 and tasks == 1 and not receipts:
            verdict = True
    elif kind == "transport-failure":
        # No status reached the gateway: the task may or may not exist, but never twice.
        # A receipt means the relay answered 202, so the failure was not exercised.
        if status == 409 or (tasks is not None and tasks > 1):
            verdict = False
        elif (complete and owner_inputs == 1 and blocked == 1 and not receipts
              and (status == 404 or (status == 200 and tasks == 1))):
            verdict = True
    elif kind == "duplicate-input":
        if status == 409 or len(set(receipts)) > 1 or (tasks or 0) > 1 or (replies or 0) > 1:
            verdict = False
        elif (len(receipts) >= 2 and status == 200 and complete and owner_inputs == 1
              and set(receipts) == {bound} and tasks == 1 and replies == 1 and _delivered_final(readback, snapshot)):
            verdict = True
    else:
        terminal = [r for r in readback["slack_deliveries"] if r.get("notification_key") == "terminal"] if readback else []
        row = terminal[0] if len(terminal) == 1 else {}
        facts.update(terminal_rows=len(terminal), terminal_state=row.get("state"))
        if ((replies or 0) > 1 or (tasks is not None and tasks > 1)
                or (row.get("state") == "AMBIGUOUS" and row.get("readback_attempts") == 2)):
            verdict = False
        elif (status == 200 and complete and owner_inputs == 1 and tasks == 1 and row.get("acknowledged_at") is None
              and replies == 1 and _delivered_final(readback, snapshot)):
            verdict = True
    facts["verdict"] = verdict
    return verdict, facts


def _drill_observations(at: datetime, runtime: _Runtime, t: _SlackThreads,
                        drills: dict[str, str]) -> list[Observation]:
    verdicts: dict[str, bool | None] = {}
    facts: dict[str, Any] = {}
    for kind, thread in drills.items():
        try:
            verdicts[kind], facts[kind] = _drill_verdict(t, kind, thread, runtime.created_at, runtime.collected_at)
        except _SOURCE_ERRORS:
            verdicts[kind], facts[kind] = None, {"verdict": None, "source_unavailable": True}
    result = []
    for check, kinds, proven in (
            ("03", ("refusal-4xx", "reserved-5xx", "transport-failure"),
             "The 4xx refusal created no task or receipt, the 5xx after reservation kept exactly one task, and the "
             "operator-labelled transport-failure thread was answered BLOCKED with no receipt and at most one task. "
             "That outcome matches a refusal or a reserved 5xx, so the relay transport failure itself is "
             "operator-attested, not observed."),
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
            stale = [kind for kind in kinds if (facts.get(kind) or {}).get("after_container_created") is False]
            altered = [kind for kind in kinds if (facts.get(kind) or {}).get("altered_bot_replies")]
            explanation = ("Missing --drill-thread selections: " + ", ".join(missing) + "." if missing else
                           "Drill threads hold edited or subtyped bot replies, so nothing in them is graded: "
                           + ", ".join(altered) + "." if altered else
                           "Drills that predate the running Director or gateway container cannot attest it: "
                           + ", ".join(stale) + "."
                           if stale else "A drill was not exercised or its evidence was incomplete or unavailable.")
        result.append(_observation(check, passed, explanation, _SLACK_SOURCE,
                                   {kind: facts.get(kind) for kind in kinds}, at, **runtime.binding))
    return result


def _render(sha: str, application: Any, projection: dict[str, Any], digest: str | None, created_at: datetime,
            channel: str, owner: str) -> dict[str, Any] | None:
    """The Director's own Slack rendering (primary_slack_delivery.render_notification) of a persisted
    application record (task record, execution mode, effect grant, ...), only when the in-process renderer is
    the candidate's own; otherwise None."""
    from types import SimpleNamespace
    from .primary_slack_delivery import render_notification

    if (not isinstance(application, dict) or not isinstance(application.get("task_record"), dict)
            or not _candidate_renderer(sha)):
        return None
    record = {key: value for key, value in application["task_record"].items() if key != "qualification_evidence"}
    row = {"projection": {**projection, "application": {**application, "task_record": record}},
           "projection_digest": digest, "created_at": created_at}
    try:
        return render_notification(row, SimpleNamespace(slack_channel_id=channel, slack_owner_user_id=owner))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _slack_blocks(value: Any) -> Any:
    """Blocks exactly as rendered, minus only what Slack adds itself: block_id, and emoji: true on plain_text.

    Every other field (text, fields, accessories, elements, confirm dialogs, URLs, values, styles) is
    compared, so nothing the owner could see or act on is projected away.
    """
    if isinstance(value, list):
        return [_slack_blocks(item) for item in value]
    if isinstance(value, dict):
        return {key: _slack_blocks(item) for key, item in value.items()
                if key != "block_id" and not (key == "emoji" and item is True and value.get("type") == "plain_text")}
    return value


def _prompt_scoped(message: dict[str, Any], task_id: str, delivery: dict[str, Any], application: Any,
                   sha: str, channel: str, owner: str) -> bool:
    """The delivered prompt is exactly the Director's canonical rendering of this request: the task record's
    complete proposed scope (plan outcome, every assignment, repository, limits and acceptance), the kind,
    and the approve button value, rendered by primary_slack_delivery.render_notification from the persisted
    record and the approval's kind, request and projection digests; compared on every visible block."""

    key, projection = str(delivery.get("notification_key", "")), str(delivery.get("projection_digest"))
    if not key.startswith("approval:") or not key.endswith(":" + projection):
        return False
    request = key[len("approval:"):-len(":" + projection)]
    blocks = message.get("blocks") if isinstance(message.get("blocks"), list) else []
    buttons = [item for block in blocks if isinstance(block, dict) and block.get("type") == "actions"
               for item in block.get("elements") or [] if isinstance(item, dict)
               and item.get("action_id") == "director_primary_approve"]
    if len(buttons) != 1:
        return False
    try:
        value = json.loads(buttons[0].get("value") or "")
    except ValueError:
        return False
    kind = value.get("kind") if isinstance(value, dict) else None
    if not isinstance(kind, str) or value != {"task_id": task_id, "projection_digest": projection, "kind": kind,
                                               "request_digest": request}:
        return False
    payload = _render(sha, application, {"pending_approval": {"kind": kind, "request_digest": request},
                                         "terminal_classification": None},
                      projection, _when(delivery["created_at"]), channel, owner)
    try:
        return payload is not None and _slack_blocks(blocks) == _slack_blocks(payload["blocks"])
    except ValueError:
        return False


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
    # Only a container set that already existed when the approval task was created can have handled it:
    # a predecessor's approval evidence grades nothing here, positive or negative.
    if not _is_current(runtime.created_at, runtime.collected_at, *_moments(snapshot)):
        reason = ("The approval task was created before the running Director container, gateway or n8n, so it "
                  "cannot attest this runtime.")
        return [_observation(key, None, reason, _SLACK_SOURCE, {"task_created_at": readback["task_created_at"]},
                             at, **binding) for key in ("06", "08")]
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
        # A task record that merely survived is not a resume: an approval prompt enqueued or a
        # provider attempt recorded must precede the process start, and the terminal delivery follow it.
        processed = (any(_when(r["created_at"]) < runtime.started_at for r in readback["slack_deliveries"]
                         if t.m.notification_key_family(r.get("notification_key")) == "approval")
                     or any(_when(e["recorded_at"]) < runtime.started_at for e in readback["provider_attempts"]))
        facts06["processed_before_start"] = processed
        # A terminal notification enqueued after the restart is not the graph resuming: a provider attempt
        # recorded, or the task projection updated, after the process start must show that it did.
        continued = (any(_when(e["recorded_at"]) > runtime.started_at for e in readback["provider_attempts"])
                     or _when(readback["task_updated_at"]) > runtime.started_at)
        facts06["continued_after_start"] = continued
        # Every moment the resume rests on happened before the collection.
        spans = (len(terminal) == 1 and processed and continued
                 and _is_current(runtime.created_at, runtime.collected_at, _when(terminal[0]["created_at"]),
                                 *(_when(e["recorded_at"]) for e in readback["provider_attempts"]))
                 and _when(readback["task_created_at"]) < runtime.started_at < _when(terminal[0]["created_at"]))
        settled = all(a.get("status") in ("SUCCEEDED", "FAILED") for a in readback["assignments"])
        resumed = (readback["terminal_classification"] is not None and counters["thread_tasks"] == 1 and spans and settled
                   and counters["started_attempts"] == counters["attempt_ordinals"]
                   and row["qualification_evidence"].get("effect_state") != "AMBIGUOUS_DELIVERY_EFFECT"
                   and replies == 1 and row.get("final_receipt_matches_native_message") is True)
        obs06 = _observation("06", True if resumed else None,
                             "One task was processed before the current Director process start and finished after it "
                             "with one dispatch per assignment and one final reply." if resumed else
                             "The task was not demonstrably processed before the current process start and finished "
                             "after it, or it is unsettled.",
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
        native = [m for m in prompt if m.get("client_msg_id") == delivery.get("client_msg_id") and m.get("user") == t.bot
                  and t.m.unedited(m)]
        # The prompt the owner saw must show this request's scope and carry its exact button value.
        scoped = len(native) == 1 and _prompt_scoped(
            native[0], task_id, delivery, entry.get("application") if isinstance(entry, dict) else None,
            runtime.runtime_sha, t.channel, t.owner)
        command = hashlib.sha256(str(delivery.get("owner_command_id")).encode()).hexdigest()
        # The control plane's own approval_resume receipt, re-read, not the delivery row's copy of it.
        # The receipt must join the command authorization the webhook-verification path admitted: same
        # command digest and writer fence, the gateway proof version, and the owner as actor.
        receipted = decided is not None and any(
            isinstance(receipt, dict) and _same(receipt.get("command_id_sha256"), command)
            and isinstance(receipt.get("recorded_at"), str) and _when(receipt["recorded_at"]) == decided
            and receipt.get("authorized") is True and receipt.get("authorized_command_digest") is True
            and receipt.get("authorized_fence") is True and receipt.get("proof_version") == _APPROVAL_PROOF_VERSION
            and _same(receipt.get("actor_id"), runtime.env.get("SLACK_OWNER_USER_ID"))
            # The authorization was claimed before the receipt, inside its signed-proof window, and before
            # the collection; the decision itself was not future-dated.
            and isinstance(receipt.get("authorization_claimed_at"), str)
            and isinstance(receipt.get("authorization_proof_expires_at"), str)
            and _when(receipt["authorization_claimed_at"]) <= _when(receipt["recorded_at"])
            and _when(receipt["authorization_claimed_at"]) < _when(receipt["authorization_proof_expires_at"])
            and runtime.collected_at is not None and decided <= runtime.collected_at
            for receipt in receipts)
        if not (delivery.get("state") == "DELIVERED" and slack_ts and delivery.get("owner_decision") == "approve"
                and delivery.get("owner_command_id") and decided is not None
                and Decimal(slack_ts) <= Decimal(str(decided.timestamp()))
                and str(delivery.get("notification_key", "")).endswith(":" + str(delivery.get("projection_digest")))
                and complete and len(native) == 1 and scoped and receipted):
            gaps += 1
    facts08 = {"approval_rows": len(approvals), "task_record_bound": row.get("task_record_bound"),
               "task_api_gateway_attested": row.get("task_api_gateway_attested"),
               "failures": sorted(set(failures)), "unproven_rows": gaps, "thread_complete": complete}
    if failures:
        passed08, explanation08 = False, "An owner decision is not bound to its delivered bot prompt: " + "; ".join(sorted(set(failures))) + "."
    elif (row.get("task_record_bound") is True and row.get("task_api_gateway_attested") is True and approvals and not gaps):
        passed08, explanation08 = True, ("Every approval prompt was delivered by the bot and read back, then approved later by "
                                         "an approval_resume command receipt re-read from the control plane whose command "
                                         "and time match the digest-bound decision. The gateway authenticates the owner.")
    else:
        passed08, explanation08 = None, "No complete, digest-bound owner approval was observed for this task."
    return [obs06, _observation("08", passed08, explanation08, _SLACK_SOURCE, facts08, at, **binding)]


def _ambiguous_effects(row: dict[str, Any], readback: dict[str, Any] | None, latest: Any) -> int | None:
    """1 for an ambiguous delivery effect; 0 only when every latest provider attempt is terminal (SUCCEEDED or
    FAILED) and every started external effect has a terminal disposition; otherwise unknown (None)."""
    if readback is None:
        return None
    if row["qualification_evidence"].get("effect_state") == "AMBIGUOUS_DELIVERY_EFFECT":
        return 1
    attempts = (latest or {}).values() if isinstance(latest, dict) else []
    settled = (all(event.get("status") in ("SUCCEEDED", "FAILED") for event in attempts)
               and all(effect.get("terminal_disposition") or not effect.get("execution_started_at")
                       for effect in readback.get("effects") or []))
    return 0 if settled else None


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
    bound = readback is not None and _is_current(runtime.created_at, runtime.collected_at, *_moments(snapshot))
    return {"run_id": row.get("task_id"),
            "mode": "offline" if offline else ("paid" if counters is not None and counters["attempt_ordinals"] >= 1 else None),
            "actual_owner_event": (row.get("owner_account_input_matched") is True and row.get("task_api_gateway_attested") is True
                                   and row.get("task_record_bound") is True),
            "correctness_verified": correctness,
            "task_count": counters["thread_tasks"] if counters else None,
            "terminal_reply_count": row.get("terminal_reply_count"),
            "wall_seconds": row.get("wall_seconds"), "owner_pause_seconds": row.get("owner_pause_seconds"),
            "machine_seconds": row.get("machine_seconds"),
            # Zero only once every attempt and every started effect is terminal; anything unsettled is unknown.
            "ambiguous_effects": _ambiguous_effects(row, readback, snapshot.get("latest")),
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
    # For offline runs the correctness field holds the structural (pipeline) rubric, never answer correctness.
    shown = {"correctness_verified": "structural_rubric_passed"} if mode == "offline" else {}
    facts = {"records": [{shown.get(key, key): record[key] for key in ("run_id", "mode", "correctness_verified",
                                                                       "terminal_reply_count", "wall_seconds",
                                                                       "operational_provider_attempts", "actual_cost_usd",
                                                                       "runtime_sha", "image_id")} for record in records]}
    # Freshness first: a run the running containers cannot have handled grades nothing, positive or negative.
    stale = [record["run_id"] for record in records if record["runtime_sha"] is None or record["image_id"] is None]
    if stale:
        return _observation(check, None, "Runs not bound to the running containers (no runtime_sha): "
                            + ", ".join(map(str, stale)) + "; they grade nothing here.", source, facts, at, **runtime.binding)
    wrong = [record["run_id"] for record in records if record["mode"] not in (None, mode)]
    if wrong:
        return _observation(check, False, f"Task evidence shows these are not {mode} runs: " + ", ".join(map(str, wrong)) + ".",
                            source, facts, at, **runtime.binding)
    order = _RECORD_ORDER + (("simulation_label",) if mode == "offline" else ())
    missing = next(((record["run_id"], key) for record in records for key in order if record[key] is None), None)
    if missing:
        return _observation(check, None, f"Run {missing[0]} lacks measured {shown.get(missing[1], missing[1])}; unknown is not zero or compliant.",
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
        # Owner ruling 2026-09-24: the committed structural rubric proves the pipeline, not the answer.
        records.append(_run_record(runtime, snapshot, t.m.evaluate_rubric(row, rubric, digest)["passed"], t.m.LABEL))
    observed = _run_records_observation("10", "offline", at, runtime, records, _SLACK_SOURCE)
    if observed.passed is not True:
        return observed
    # Consecutive: the ten threads are the only top-level owner posts between the first and the last.
    # Only what the gateway itself ignores is excluded, so any post it could forward counts.
    ordered = sorted(trials, key=Decimal)
    window = t.history(ordered[0], ordered[-1])
    posts = {str(m.get("ts")) for m in window
             if m.get("user") == t.owner and not m.get("bot_id")
             and m.get("subtype") not in ("bot_message", "message_changed", "message_deleted")}
    # A deleted post that had replies leaves a hidden tombstone, which may be a skipped trial.
    removed = sum(1 for m in window if m.get("subtype") == "tombstone" or m.get("hidden") is True)
    if posts != set(trials) or removed:
        return _observation("10", None, f"The ten trials are not consecutive: {len(posts - set(trials))} other top-level "
                            f"owner post(s) and {removed} deleted or hidden message(s) fall between them, or a trial "
                            "is not a top-level owner post.",
                            _SLACK_SOURCE + " and conversations.history", observed.facts, at, **runtime.binding)
    return _observation("10", True, observed.explanation + " The ten are the only top-level owner posts in the "
                        "channel between the first and the last, so they are consecutive.",
                        _SLACK_SOURCE + " and conversations.history", observed.facts, at, **runtime.binding)


def _paid_facts(t: _SlackThreads, thread: str, sha: str) -> dict[str, Any]:
    replies = t.replies(thread)
    task_id = t.m.task_from_accepted(thread, replies[0], replies[1], t.bot)
    return _with_rendering(t, _task_facts(t, thread, task_id, replies), sha)


def _with_rendering(t: _SlackThreads, facts: dict[str, Any], sha: str) -> dict[str, Any]:
    finals = {m.get("ts") for m in facts["row"].get("terminal_messages") or []}
    facts["finals_unedited"] = all(t.m.unedited(m) for m in facts["messages"] if m.get("ts") in finals)
    # The Director's own terminal rendering of this task (truncation included) and its fixed wrapper (the same
    # rendering with every provider result removed): only answer text that survived into it is gradable.
    readback = facts.get("readback") or {}
    task = facts.get("task") or {}
    terminal = [r for r in readback.get("slack_deliveries") or [] if r.get("notification_key") == "terminal"]
    rendered = wrapper = None
    if len(terminal) == 1:
        projection = {"terminal_classification": readback.get("terminal_classification"),
                      "reason_code": readback.get("terminal_reason_code")}
        created = _when(terminal[0]["created_at"])
        application = {"task_record": task, "execution_mode": readback.get("execution_mode")}
        rendered = _render(sha, application, projection, None, created, t.channel, t.owner)
        wrapper = _render(sha, {**application, "task_record": {**task, "assignment_outcomes": []}},
                          projection, None, created, t.channel, t.owner)
    facts["terminal_render"] = html.unescape(rendered["text"]) if rendered else None
    facts["terminal_wrapper"] = html.unescape(wrapper["text"]) if wrapper else None
    return facts


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


def _paid_correct(paid: dict[str, Any], rubric_path: Path) -> bool | None:
    rubric, digest = _load_paid_rubric(rubric_path)
    row = paid["row"]
    markers = re.findall(r"\brubric-sha256:([a-f0-9]{64})\b", str((paid["task"].get("intake") or {}).get("text", "")))
    finals = row.get("terminal_messages") or []
    if not (row.get("task_record_bound") is True and len(markers) == 1 and _same(markers[0], digest)
            and row.get("input_message_ts")
            and _when(rubric["declared_at"]).timestamp() <= float(row["input_message_ts"])
            and row.get("task_status") == "DONE" and len(finals) == 1):
        return False
    return _answer_verified(paid, rubric["expected_answer_substrings"])


def _answer_verified(facts: dict[str, Any], expected: list[str]) -> bool | None:
    """Answer-specific correctness (check 20): each expected substring is in the persisted provider
    results and in the delivered final reply, which must be the Director's own rendering of them (truncation
    included), and none is in the owner's question or the Director's fixed terminal wrapper."""
    finals = facts["row"].get("terminal_messages") or []
    final = [m for m in facts["messages"] if len(finals) == 1 and m.get("ts") == finals[0]["ts"]]
    # Slack keeps an edited message's ts but returns its new text: grade only the unedited reply.
    if len(final) != 1 or facts.get("finals_unedited") is not True:
        return False
    answer = "\n".join(str(item.get("result", "")) for item in facts["task"].get("assignment_outcomes") or []
                       if isinstance(item, dict))
    question = html.unescape(str((facts["task"].get("intake") or {}).get("text", "")))
    wrapper, rendered = facts.get("terminal_wrapper"), facts.get("terminal_render")
    visible = html.unescape(str(final[0].get("text", "")))
    # Unverifiable (renderer not the candidate's, or the native reply is not its rendering): unknown, not wrong.
    if not isinstance(wrapper, str) or not isinstance(rendered, str) or visible not in (
            rendered, rendered.replace("\n", " ")):
        return None
    if any(item in question or item in wrapper for item in expected):
        return False
    return bool(answer) and all(item in answer and item in visible for item in expected)


def _settled_cost(consistency: dict | None, paid: dict[str, Any]) -> tuple[float | None, str | None]:
    """Actual charges only from settled usage rows correlated to request and response IDs."""
    entry = ((consistency or {}).get("tasks") or {}).get(paid["task_id"])
    attempts = {event["attempt_digest"] for event in (paid.get("latest") or {}).values()}
    if not isinstance(entry, dict) or entry.get("usage_tables_present") is not True:
        return None, None
    spend = entry.get("spend") or []
    if not attempts or {row.get("attempt_digest") for row in spend} != attempts:
        return None, None
    if any(not _counter(row.get("actual_microusd")) or row.get("request_correlated") is not True
           or row.get("usage_verified") is not True for row in spend):
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
    monitor = runtime.health.get("usage_monitor") or {}
    accounting = monitor.get("daily_accounting")
    # A monitor counts only when enabled with the paid gate open and every configured provider current.
    ready = (monitor.get("enabled") is True and monitor.get("paid_run_gate_open") is True
             and monitor.get("gate_state") == "open" and monitor.get("unsupported_providers") == []
             and monitor.get("missing_or_stale_providers") == []
             and monitor.get("all_configured_have_recent_signal") is True)
    monitored = ready and isinstance(accounting, dict) and bool(accounting)
    uncapped: list[str] = []
    if monitored:
        for provider, value in sorted(accounting.items()):
            budget = value.get("daily_budget_usd")
            requests = value.get("request_count")
            attempted = ((type(requests) is int and requests > 0) or (value.get("reported_cost_usd") or 0) > 0
                         or (value.get("reserved_cost_usd") or 0) > 0)
            if attempted and type(budget) not in (int, float):
                uncapped.append(str(provider))  # an attempted provider with no numeric cap has no monetary control
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
    unsigned = False
    if isinstance(budgets, list):
        # A budget row counts only when the in-image report re-verified its owner signature
        # (ProviderBudget.verify) and it binds a provider the paid task was charged against.
        # Each charged attempt must join the exact signed document it reserved against: same grant, same provider.
        spend = ((consistency or {}).get("tasks") or {}).get((paid or {}).get("task_id"), {}).get("spend") or []
        unsigned = any(row.get("signature_verified") is not True for row in budgets) or not all(
            any(_same(budget.get("authority_digest"), charge.get("grant_digest"))
                and (budget.get("task_id"), budget.get("project_id"), budget.get("day"), budget.get("provider_id"))
                == (charge.get("task_id"), charge.get("project_id"), charge.get("budget_day"), charge.get("provider_id"))
                for budget in budgets) for charge in spend)
        budgets = [row for row in budgets if row.get("signature_verified") is True]
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
    facts.update(usage_monitor_ready=ready, uncapped_attempted_providers=uncapped)
    if not ready:
        return _observation("15", None, "The usage monitor is disabled, its paid gate is closed, or a configured "
                            "provider is unsupported, missing or stale.", source, facts, at, **runtime.binding)
    if uncapped:
        return _observation("15", None, "Attempted providers have no numeric daily budget: " + ", ".join(uncapped) + ".",
                            source, facts, at, **runtime.binding)
    if unsigned:
        return _observation("15", None, "A budget row is unsigned, fails the owner-signed budget contract, or a charged "
                            "attempt does not join the signed budget of its own grant and provider.",
                            source, facts, at, **runtime.binding)
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
    director_runtime, runtime = runtime, None
    if gateway is not None:
        try:
            runtime = _gateway_bound(director_runtime, gateway)
        except _SOURCE_ERRORS:
            runtime = None
    gateway_unbound = runtime is None
    runtime = runtime or director_runtime
    route_verified: bool | None = None
    if not gateway_unbound:
        try:
            route_verified = _intake_route_verified(director_runtime, gateway)
        except _SOURCE_ERRORS:
            route_verified = None
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
                put([_status_observation(at, director_runtime, t)])
            except _SOURCE_ERRORS:
                unknown(("04",), "The status execution or its Slack readback could not be read; no retry was made.",
                        "n8n Director Status saved execution")
            if paid_thread is not None:
                try:
                    paid = _paid_facts(t, paid_thread, runtime.runtime_sha)
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
        # The in-image commands verify the generation manifest; its commit, not the image label,
        # is what their evidence belongs to. Unverified or different: none of it attests this runtime.
        generation = (consistency or {}).get("generation")
        verified_commit = generation.get("candidate_commit") if isinstance(generation, dict) else None
        verified_tree = generation.get("candidate_tree") if isinstance(generation, dict) else None
        unbound = consistency is not None and not (
            _same(verified_commit, runtime.runtime_sha) and _same(verified_tree, runtime.runtime_tree)
            and _same(_commit_tree(runtime.runtime_sha), runtime.runtime_tree))
        if unbound or consistency is None:
            consistency = captures = None  # captures carry no commit: without the snapshot they are unbound
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
        if unbound:
            reason = ("The in-image generation commit or tree is unverified or differs from the runtime image labels "
                      "(or the labelled commit's tree), so its "
                      "consistency, acceptance, capture and settlement evidence cannot attest this runtime.")
            unknown(("07", "08", "11", "12", "15", "20"), reason, _CONSISTENCY_SOURCE)
            put([_observation("17", None, reason, _CONSISTENCY_SOURCE, {}, at, candidate_sha=candidate_sha)])
        if gateway_unbound and t is not None:
            unknown(("03", "08", "09", "10", "20"), "No running --gateway and single n8n of this deployment were "
                    "resolved, so Slack intake evidence cannot be bound to the relay that handled it.", _SLACK_SOURCE)
        elif route_verified is not True and t is not None:
            unknown(("03", "08", "09", "10", "20"), "The current intake route (gateway relay and credentials, both "
                    "hops, n8n's store and the candidate's active intake workflow) is not verified, so historical "
                    "Slack intake evidence cannot attest it.", _SLACK_SOURCE)
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
    if not _same("sha256:" + hashlib.sha256(manifest_raw).hexdigest(), indexed["seal_manifest_digest"]):
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
        if not _same(hashlib.sha256(hashed).hexdigest(), digest):
            raise ValueError("native artifact digest mismatch")
        if relative in {"coverage.json", "findings.json"}:
            contents[relative] = json.loads(raw)
    coverage, findings = contents["coverage.json"], contents["findings.json"]
    if coverage.get("scanId") != scan_id or findings.get("scanId") != scan_id:
        raise ValueError("canonical evidence belongs to another scan")
    # A convenient base (a descendant, or any commit other than the candidate's merge
    # base with main) can drop the candidate's own changes from the diff it covers.
    authoritative = _merge_base(candidate_sha)
    if base != authoritative:
        return _observation("18", None, "The sealed scan's base is not the candidate's merge base with main, so its "
                            "changed-path coverage may omit the candidate's changes.", str(root / "scan-manifest.json"),
                            {"scan_id": scan_id, "scan_base": base, "merge_base_with_main": authoritative},
                            at, candidate_sha=candidate_sha)
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
_RUN_PAGES = 10  # 1000 runs at one head; more is UNKNOWN, never a partial listing.
_REPLICA_LANE_KEYS = {"lane", "head_tests_run", "base_tests_run", "head_failures", "base_failures", "command_sha256"}


def _ci_observation(candidate_sha: str, at: datetime, replica_receipt: Path | None = None) -> Observation:
    paths: dict[Any, Any] = {}

    def executed(args: list[str]) -> Any:
        # Zero-step jobs are billing stubs and are dropped, so an older executed
        # ci.yml job of the same name (hosted or self-hosted) can stand. A job of
        # any other workflow is renamed by its workflow path: it can still fail
        # 16, but it can never stand in for a required ci.yml job.
        # "No required job executed" admits the replica receipt, so absence is concluded
        # only from a complete listing: every page of runs (bounded) and every job of a run.
        if "/jobs?" not in args[-1]:
            if "&per_page=20" not in args[-1]:
                raise ValueError("unexpected workflow-runs query")
            runs: list[dict[str, Any]] = []
            for page in range(1, _RUN_PAGES + 1):
                value = _run_json(["gh", *args[:-1], args[-1].replace("&per_page=20", f"&per_page=100&page={page}")])
                total, batch = value["total_count"], value["workflow_runs"]
                runs.extend(batch)
                if len(runs) >= total or not batch:
                    break
            if type(total) is not int or len(runs) != total or len({run["id"] for run in runs}) != total:
                raise ValueError("workflow runs at this head were not listed completely")
            paths.update((run["id"], run.get("path")) for run in runs)
            return {"total_count": total, "workflow_runs": runs}
        value = _run_json(["gh", *args])
        if value.get("total_count") != len(value["jobs"]):
            raise ValueError("a run's jobs were not listed completely")
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


def _merge_base(head: str) -> str | None:
    """GitHub's merge base of main and head (GET only)."""
    value = _run_json(["gh", "api", f"repos/{REPOSITORY}/compare/main...{head}", "--jq",
                       "{merge_base: .merge_base_commit.sha}"])
    sha = value.get("merge_base") if isinstance(value, dict) else None
    return sha if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha) else None


def _commit_tree(sha: str) -> str | None:
    """GitHub's tree for a commit (GET only); None when unreadable."""
    try:
        value = _run_json(["gh", "api", f"repos/{REPOSITORY}/git/commits/{sha}", "--jq", "{tree: .tree.sha}"])
    except _SOURCE_ERRORS:
        return None
    tree = value.get("tree") if isinstance(value, dict) else None
    return tree if isinstance(tree, str) and re.fullmatch(r"[a-f0-9]{40}", tree) else None


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
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:03 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:22 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:39 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | END]]
