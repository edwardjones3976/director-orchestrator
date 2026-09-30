# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | BEGIN]]
"""M2-G1 exemplar harness: one bounded, admitted local dispatch per scenario.

A dry run checks source chronology and the held-out folders, then writes
nothing and makes no inference call or model probe. GitHub chronology still
uses its existing read-only network lookup. A live CLI run uses only an admitted,
artifact-bound local model; cloud training-target dispatch remains held by the
M2 terms gate. Rows publish only after the leak wall, schema and scrub pass.

    python projects/homemade-llm/eval/generate_rows.py --dry-run \\
        --scenarios scenarios.jsonl --provider local_model \\
        --set-a set-a/refs --set-b set-b/refs --out rows.jsonl \\
        --source-root path/to/dataset --terms-verified-at 2026-09-28T20:00:00Z

Each scenario must pass ``build_inputs.validate_context`` (byte/path guard) and
GitHub issue/linked-PR chronology before a dry run or provider call.
Other source types fail closed until they have an independent chronology route.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
import secrets
from typing import Any, Callable
from urllib.parse import urlsplit

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
import build_inputs  # noqa: E402
import generate_candidates as local_transport  # noqa: E402
import prompt_template  # noqa: E402
import validate_rows  # noqa: E402

MAX_ROWS_PER_CALL = 8
MAX_OUTPUT_TOKENS = 2048
MAX_TIMEOUT_SECONDS = 300
MAX_PACKET_BYTES = 24000
SCHEMA_VERSION = "homemade-llm-director-row/v1"
TARGET_FIELDS = ("acceptance_checks", "constraints", "evidence_requirements", "non_goals")
COMPETING_PROVIDERS = {
    "anthropic_api": "anthropic",
    "openai": "codex",
    "cursor": "cursor",
}
# Owner-approved local routes; hashes bind the verified conversion and license bytes.
LOCAL_MODELS = {
    "Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18": (
        "af16f12d22ebc467e758e0928f9e32f22943b4d211e2cc950cf90b789e7ec023",
        "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e",
    ),
    "microsoft/phi-4@2db69c1c3e91a05d2c64a3185acfbaf36f744e25": (
        "000c3c3524b52ab2f6b770d333c3938b7f87b672bb4a5a614e06487e4774fbed",
        "c49419617a6070bcb197cfe272f7007fdec3e790dbb529cb995473bd69c0bd51",
    ),
}
SECRET_MARKERS = ("_KEY=", "_TOKEN=", "_SECRET=", "DSN=", "PASSWORD=", "xoxb-", "xapp-")
UTC_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")
RECEIPT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]*(/[A-Za-z0-9._@+-]+)*$")
PURPOSE_PATTERN = re.compile(
    r"^(?P<project>[a-z0-9][a-z0-9-]*) (?P<milestone>M\d+|-) "
    r"(?P<packet>[A-Za-z0-9][A-Za-z0-9._-]*): \S.*$"
)
RECEIPT_LINE = re.compile(
    r"^(?:anthropic|codex exit \d+;|cursor exit \d+;).*receipt:?\s+(\S+)\s*$"
)
RECEIPT_MODEL_TAG = re.compile(
    r"^\[\[(?:[^|\]]+) \| (?P<model>[^|\]]+?) \| [0-9]{4}-[0-9]{2}-[0-9]{2} \| "
    r"[0-9]{2}:[0-9]{2} UTC \| BEGIN\]\]$"
)
DISPATCH_PY = Path.home() / ".claude" / "skills" / "provider-dispatch" / "dispatch.py"
Dispatch = Callable[[str, str, str], "DispatchResult"]
Scrub = Callable[[Path], str | None]

GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_CHRONOLOGY_QUERY = (
    "query($owner:String!,$repo:String!,$issue:Int!){"
    "repository(owner:$owner,name:$repo){"
    "issue(number:$issue){title createdAt lastEditedAt body userContentEdits(first:1){totalCount}"
    "timelineItems(first:100,itemTypes:[CROSS_REFERENCED_EVENT,RENAMED_TITLE_EVENT]){pageInfo{hasNextPage}"
    "nodes{__typename ... on CrossReferencedEvent{source{__typename ... on PullRequest{"
    "number body createdAt lastEditedAt repository{nameWithOwner} userContentEdits(first:1){totalCount}"
    "closingIssuesReferences(first:100){pageInfo{hasNextPage} nodes{number repository{nameWithOwner}}}"
    "commits(first:100){pageInfo{hasNextPage}"
    "nodes{commit{authoredDate committedDate}}}}}}}}}}}"
)


class DispatchResult:
    """One completed provider call. ``model`` is the name the receipt records."""

    def __init__(self, text: str, receipt_path: Path, model: str) -> None:
        self.text = text
        self.receipt_path = receipt_path
        self.model = model


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def infer_source_root(set_a: Path, set_b: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
        if not root.is_dir():
            fail(f"source-root is not a directory: {root}")
        return root
    if set_a.name != "refs" or set_b.name != "refs":
        fail("set-a and set-b must be refs directories, or pass --source-root")
    if not (set_a.parent.name.startswith("set-") and set_b.parent.name.startswith("set-")):
        fail("could not infer --source-root from set paths; pass it explicitly")
    if set_a.parent.parent != set_b.parent.parent:
        fail("set-a and set-b do not share a dataset root; pass --source-root")
    return set_a.parent.parent.resolve()


def _load_references(refs_dir: Path) -> dict[str, dict[str, Any]]:
    references: dict[str, dict[str, Any]] = {}
    for item_id in build_inputs._reference_ids(refs_dir):
        payload = json.loads((refs_dir / f"{item_id}.json").read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            references[item_id] = payload
    return references


def _held_out_guard(set_a: Path, set_b: Path) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, set[str]]]:
    references = _load_references(set_a)
    second = _load_references(set_b)
    if overlap := references.keys() & second.keys():
        fail(f"duplicate held-out reference IDs across sets: {', '.join(sorted(overlap))}")
    references.update(second)
    lineage: set[str] = set()
    digests: dict[str, set[str]] = {}
    for refs_dir in (set_a, set_b):
        item_ids = build_inputs._reference_ids(refs_dir)
        lineage |= build_inputs.lineage_keys(refs_dir, item_ids)
        for digest, owners in build_inputs.source_digests(refs_dir, item_ids).items():
            digests.setdefault(digest, set()).update(owners)
    return references, lineage, digests


def validate_scenario_source_provenance(
    scenario: dict[str, Any],
    *,
    source_root: Path,
    set_a: Path,
    set_b: Path,
    label: str,
) -> None:
    references, lineage_keys, held_out_digests = _held_out_guard(set_a, set_b)
    if build_inputs._source_ref_forms(str(scenario["source_group"])) & lineage_keys:
        fail(f"{label}: source_group identifies a held-out task")
    problems = build_inputs.validate_context(
        scenario["input"]["context"],
        references,
        lineage_keys,
        set_a,
        str(scenario["source_group"]),
        held_out_digests,
        source_root=source_root,
        allow_held_out_source=False,
    )
    if problems:
        fail(f"{label}: {problems[0]}")


def github_chronology(repository: str, issue: int) -> dict[str, Any]:
    owner, repo = repository.split("/", 1)
    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={GITHUB_CHRONOLOGY_QUERY}",
             "-F", f"owner={owner}", "-F", f"repo={repo}",
             "-F", f"issue={issue}"],
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
        )
        record = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        fail("GitHub chronology lookup failed")
    if (result.returncode != 0 or not isinstance(record, dict) or record.get("errors")
            or not isinstance(record.get("data"), dict)):
        fail("GitHub chronology lookup failed")
    return record["data"].get("repository") or {}


def validate_chronology(scenario: dict[str, Any], source_root: Path, label: str) -> None:
    block = scenario.get("source_provenance")
    repository = scenario["input"]["repository"]
    if (not isinstance(block, dict) or not isinstance(repository, str)
            or GITHUB_REPOSITORY.fullmatch(repository) is None
            or type(block.get("github_issue")) is not int
            or type(block.get("first_work_pr")) is not int
            or min(block["github_issue"], block["first_work_pr"]) < 1):
        fail(f"{label}: an unverified source needs github_issue and first_work_pr")
    canonical_group = f"github/{repository.casefold()}/issues/{block['github_issue']}"
    if scenario["source_group"] != canonical_group:
        fail(f"{label}: source_group must equal verified issue group {canonical_group}")
    source_items = [item for item in scenario["input"]["context"]
                    if item["kind"] == "assignment_packet"]
    if not source_items or len(source_items) != len(scenario["input"]["context"]):
        fail(f"{label}: this chronology route supports assignment issue context only")
    record = github_chronology(repository, block["github_issue"])
    issue = record.get("issue") if isinstance(record, dict) else None
    if not isinstance(issue, dict):
        fail(f"{label}: GitHub assignment issue is missing")

    def utc(value: Any) -> datetime:
        if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
            fail(f"{label}: GitHub chronology timestamp is missing")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail(f"{label}: GitHub chronology timestamp is invalid")

    created = utc(issue.get("createdAt"))
    if (issue.get("lastEditedAt") is not None
            or (issue.get("userContentEdits") or {}).get("totalCount") != 0):
        fail(f"{label}: GitHub assignment issue was edited")
    body = issue.get("body")
    if not isinstance(body, str) or not isinstance(issue.get("title"), str):
        fail(f"{label}: GitHub issue has no verifiable title/body")
    if scenario["input"]["title"] != issue["title"]:
        fail(f"{label}: input title differs from the assignment issue title")
    objective = scenario["input"].get("objective")
    if (not isinstance(objective, str) or not objective.strip()
            or " ".join(objective.split()) not in " ".join(body.split())):
        fail(f"{label}: input objective is not an excerpt of the assignment")
    timeline = issue.get("timelineItems") or {}
    if (timeline.get("pageInfo") or {}).get("hasNextPage") is not False:
        fail(f"{label}: GitHub cross-reference history is incomplete")
    linked: list[tuple[datetime, int]] = []
    for event in timeline.get("nodes") or []:
        if (event or {}).get("__typename") == "RenamedTitleEvent":
            fail(f"{label}: GitHub assignment issue title was renamed")
        pr = (event or {}).get("source") or {}
        if pr.get("__typename") != "PullRequest":
            continue
        source_repo = (pr.get("repository") or {}).get("nameWithOwner")
        if not isinstance(source_repo, str):
            fail(f"{label}: linked PR repository is unverified")
        if source_repo.casefold() != repository.casefold():
            fail(f"{label}: foreign PR cross-reference needs independent source admission")
        closing = pr.get("closingIssuesReferences") or {}
        if (closing.get("pageInfo") or {}).get("hasNextPage") is not False:
            fail(f"{label}: linked PR closing-issue history is incomplete")
        issue_ref = (rf"(?:(?:{re.escape(repository)})?#|GH-|"
                     rf"https?://github\.com/{re.escape(repository)}/issues/){block['github_issue']}\b")
        closing_claim = r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\b\s*:?\s*"
        claimed = (re.search(rf"(?im)^Issue:\s*{issue_ref}", pr.get("body") or "") is not None
                   or re.search(closing_claim + issue_ref,
                                pr.get("body") or "", re.I) is not None
                   or any(node.get("number") == block["github_issue"]
                          and (node.get("repository") or {}).get("nameWithOwner", "").casefold() == repository.casefold()
                          for node in closing.get("nodes") or []))
        if not claimed:
            # ponytail: unknown PR references stop; richer admission needs independent evidence.
            fail(f"{label}: unbound same-repository PR cross-reference")
        commits = pr.get("commits") or {}
        if (commits.get("pageInfo") or {}).get("hasNextPage") is not False or not commits.get("nodes"):
            fail(f"{label}: linked PR commit history is incomplete")
        # Dates are editable Git metadata; independent source admission is still required.
        dates = [utc((node.get("commit") or {}).get(field))
                 for node in commits["nodes"] for field in ("authoredDate", "committedDate")]
        linked.append((min(utc(pr.get("createdAt")), *dates), pr["number"]))
    if (not linked or block["first_work_pr"] not in [number for _, number in linked]
            or created >= min(time for time, _ in linked)
            or block["first_work_pr"] != min(linked)[1]):
        fail(f"{label}: first_work_pr is not the earliest linked GitHub work")
    for item in source_items:
        source = (source_root / item["source_ref"]).resolve()
        if source.read_bytes() != body.encode("utf-8"):
            fail(f"{label}: captured source bytes differ from GitHub issue body")


def validate_scenarios_for_dispatch(
    scenarios: list[dict[str, Any]],
    *,
    source_root: Path,
    set_a: Path,
    set_b: Path,
    sets: list[validate_rows.HeldOutSet],
) -> None:
    group_splits: dict[str, str] = {}
    for index, scenario in enumerate(scenarios, start=1):
        label = f"scenario {index} ({scenario.get('source_group', '?')})"
        validate_scenario_source_provenance(
            scenario, source_root=source_root, set_a=set_a, set_b=set_b, label=label,
        )
        reasons, overlap, nearest = validate_rows.leak_wall(
            {"input": scenario["input"], "target": {}, "source_group": scenario["source_group"]}, sets,
        )
        if reasons or overlap >= validate_rows.DEFAULT_THRESHOLD - validate_rows.DEFAULT_QUARANTINE_BAND:
            fail(f"{label}: held-out input leak before dispatch: {reasons or nearest}")
        validate_chronology(scenario, source_root, label)
        group, split = scenario["source_group"], scenario["split"]
        if group in group_splits and group_splits[group] != split:
            fail(f"{label}: one verified issue group cannot straddle train and dev")
        group_splits[group] = split


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def refuse_secrets(value: Any, label: str) -> None:
    for text in _strings(value):
        if any(marker in text for marker in SECRET_MARKERS):
            fail(f"{label} contains a secret marker")


def load_scenarios(path: Path, max_rows: int) -> list[dict[str, Any]]:
    if not path.is_file():
        fail(f"scenarios is not a file: {path}")
    if max_rows < 1 or max_rows > MAX_ROWS_PER_CALL:
        fail(f"max-rows must be from 1 to {MAX_ROWS_PER_CALL}")
    scenarios: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            scenario = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"scenarios line {line_number}: {exc.msg}")
        if not isinstance(scenario, dict):
            fail(f"scenarios line {line_number}: not an object")
        if scenario.get("split") not in ("train", "dev"):
            fail(f"scenarios line {line_number}: split must be train or dev")
        group = scenario.get("source_group")
        if not isinstance(group, str) or not group.strip() or any(char.isspace() for char in group):
            fail(f"scenarios line {line_number}: source_group must be one nonempty token")
        item = scenario.get("input")
        if not isinstance(item, dict):
            fail(f"scenarios line {line_number}: input must be an object")
        try:
            contract = prompt_template.input_contract(item)
        except ValueError as exc:
            fail(f"scenarios line {line_number}: {exc}")
        if contract != prompt_template.INPUT_CONTRACT_CONTEXT:
            fail(f"scenarios line {line_number}: an exemplar input must carry context")
        shape = prompt_template.context_shape_errors(item.get("context"))
        if shape:
            fail(f"scenarios line {line_number}: {shape[0]}")
        refuse_secrets(scenario, f"scenarios line {line_number}")
        scenarios.append(scenario)
    if not scenarios:
        fail("scenarios file has no rows")
    if len(scenarios) > max_rows:
        fail(f"scenarios has {len(scenarios)} rows; this call allows {max_rows}")
    return scenarios


def exemplar_packet(item: dict[str, Any]) -> str:
    messages = prompt_template.render_messages(item)
    packet = (
        messages[0]["content"] + "\n\n" + messages[1]["content"]
        + "\n\nReturn only a JSON object with the keys non_goals, constraints, "
        "acceptance_checks, and evidence_requirements. No markdown."
    )
    if len(packet.encode("utf-8")) > MAX_PACKET_BYTES:
        fail(f"exemplar packet exceeds {MAX_PACKET_BYTES} bytes")
    return packet


def parse_target(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[-1].strip() == "```":
            lines = lines[1:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        fail(f"provider answer is not JSON: {exc.msg}")
    if not isinstance(value, dict) or set(value) != set(TARGET_FIELDS):
        fail("provider answer must be exactly the four target fields")
    refuse_secrets(value, "provider answer")
    return value


def receipt_ref(path: Path) -> str:
    parts = path.parts
    if "receipts" not in parts:
        fail(f"receipt path has no receipts segment: {path.name}")
    relative = "/".join(parts[parts.index("receipts"):])
    if RECEIPT_REF_PATTERN.fullmatch(relative) is None:
        fail("receipt path is not a relative receipt reference")
    return relative


def model_from_receipt(path: Path) -> str:
    """Return the model the receipt itself records. A missing label fails closed."""
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fail("receipt does not record a model")
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not model.strip():
            fail("receipt does not record a model")
        return model.strip()
    if path.suffix == ".md":
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            fail("receipt does not record a model")
        for line in lines:
            match = RECEIPT_MODEL_TAG.fullmatch(line.strip())
            if match is None:
                continue
            model = match.group("model").strip()
            if model and not any(marker in model for marker in SECRET_MARKERS):
                return model
            break
    fail("receipt does not record a model")


def purpose_for(source_group: str) -> str:
    purpose = f"homemade-llm M2 M2-G1: exemplar {source_group}"
    if PURPOSE_PATTERN.fullmatch(purpose) is None:
        fail(f"purpose is not a paid-call label: {source_group}")
    return purpose


def live_dispatch(provider: str, packet: str, purpose: str, *, timeout: int, max_output: int, worktree: Path | None) -> DispatchResult:
    command_name = COMPETING_PROVIDERS[provider]
    if not DISPATCH_PY.is_file():
        fail(f"dispatch.py is not at {DISPATCH_PY}")
    command = [
        sys.executable, str(DISPATCH_PY), command_name,
        "--packet", "-", "--purpose", purpose, "--timeout", str(timeout),
    ]
    if command_name == "anthropic":
        command += ["--max-output", str(max_output)]
    else:
        if worktree is None:
            fail(f"{provider} requires --worktree")
        command += ["--worktree", str(worktree)]
    # The packet is passed as a file, not stdin, because anthropic and cursor read --packet.
    with tempfile.TemporaryDirectory(prefix="m2-g1-") as directory:
        packet_path = Path(directory) / "packet.txt"
        packet_path.write_text(packet, encoding="utf-8", newline="\n")
        command[command.index("-")] = str(packet_path)
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout + 15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            fail(f"dispatch failed: {type(exc).__name__}")
    if completed.returncode != 0:
        fail(f"dispatch exited {completed.returncode}")
    receipt_path: Path | None = None
    for line in completed.stdout.splitlines():
        match = RECEIPT_LINE.fullmatch(line.strip())
        if match:
            receipt_path = Path(match.group(1))
    if receipt_path is None or not receipt_path.is_file():
        fail("dispatch returned no receipt")
    answer = completed.stdout
    marker = "--- "
    if marker in answer:
        answer = answer.split("--- ", 1)[-1]
        answer = answer.split("\n", 1)[1] if "\n" in answer else ""
    else:
        # Anthropic prints the answer, then one status line.
        lines = [line for line in completed.stdout.splitlines() if not RECEIPT_LINE.fullmatch(line.strip())]
        answer = "\n".join(lines)
    return DispatchResult(answer, receipt_path, model_from_receipt(receipt_path))


def _schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(validate_rows.ROW_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def prepare_local_model(*, model: str, model_gguf: Path, conversion_receipt: Path,
                        license_file: Path, base_url: str, lms_models_dir: Path,
                        timeout: int) -> dict[str, Any]:
    """Verify the admitted model artifacts before probing the local loaded alias."""
    if (not isinstance(model, str) or not model.strip()
            or any(not isinstance(path, Path) for path in
                   (model_gguf, conversion_receipt, license_file, lms_models_dir))):
        fail("local_model requires model, model-gguf, conversion-receipt and license-file")
    try:
        url = urlsplit(base_url)
        allowed_url = (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                       and url.port == 1234 and url.path.rstrip("/") == "/v1"
                       and not (url.username or url.password or url.query or url.fragment))
    except ValueError:
        allowed_url = False
    if not allowed_url:
        fail("local_model requires the loopback LM Studio endpoint on port 1234")
    try:
        if conversion_receipt.stat().st_size > 1024 * 1024 or license_file.stat().st_size > 1024 * 1024:
            fail("local conversion or license evidence exceeds 1 MiB")
        conversion_bytes = conversion_receipt.read_bytes()
        conversion = local_transport.strict_json(conversion_bytes)
        if (not isinstance(conversion, dict) or not isinstance(conversion.get("gguf"), list)
                or any(not isinstance(item, dict) for item in conversion["gguf"])):
            fail("local conversion receipt is malformed")
        identity = f"{conversion['repo']}@{conversion['revision']}"
        if conversion.get("status") != "complete" or identity not in LOCAL_MODELS:
            fail("conversion receipt does not identify an approved complete local model")
        gguf_hash, license_hash = LOCAL_MODELS[identity]
        gguf = local_transport.model_gguf_record(model_gguf)
        matches = [item for item in conversion["gguf"]
                   if item.get("sha256") == gguf_hash and item.get("quant") == "Q4_K_M"
                   and item.get("bytes") == gguf["model_gguf_bytes"]
                   and Path(item["path"]).resolve() == model_gguf.resolve()]
        if gguf["model_gguf_sha256"] != gguf_hash or len(matches) != 1:
            fail("GGUF bytes do not match the pinned conversion receipt")
        if local_transport.sha256(license_file.read_bytes()) != license_hash:
            fail("license bytes do not match the admitted model license")
        args = argparse.Namespace(base_url=base_url, model=model,
                                  lms_models_dir=lms_models_dir, item_timeout=timeout)
        binding = local_transport.loaded_model_binding(args, gguf)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail(f"local model verification failed: {type(exc).__name__}: {exc}")
    return {"model": identity, "alias": model, "base_url": base_url.rstrip("/"),
            "conversion_receipt": str(conversion_receipt.resolve()),
            "conversion_receipt_sha256": local_transport.sha256(conversion_bytes),
            "license_file": str(license_file.resolve()), "license_sha256": license_hash,
            "loaded_model_binding": binding, **gguf}


def local_dispatch(scenario: dict[str, Any], purpose: str, prepared: dict[str, Any], *,
                   receipts_dir: Path, verified_at: str, timeout: int,
                   max_output: int) -> DispatchResult:
    """One local request; persist success or failure without overwriting a receipt."""
    schema = json.loads(validate_rows.ROW_SCHEMA.read_text(encoding="utf-8"))["properties"]["target"]
    generated_schema, rewrites = local_transport.generation_schema(schema)
    messages = prompt_template.render_messages(scenario["input"])
    qwen = prepared["model"].startswith("Qwen/")
    if qwen:
        messages[0]["content"] += local_transport.QWEN3_PROMPT
    body = {"model": prepared["alias"], "temperature": 0, "n": 1,
            "max_tokens": max_output, "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "director_target_v1", "strict": True, "schema": generated_schema}}}
    if qwen:
        body.update(chat_template_kwargs={"enable_thinking": False}, reasoning_effort="none")
    record = {**prepared, "provider": "local_model", "purpose": purpose,
              "terms_verified_at": verified_at, "input": scenario["input"],
              "source_group": scenario["source_group"],
              "source_provenance": scenario["source_provenance"],
              "prompt_sha256": local_transport.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")),
              "schema_sha256": local_transport.sha256(json.dumps(schema, sort_keys=True).encode("utf-8")),
              "request_sha256": local_transport.sha256(json.dumps(body, sort_keys=True).encode("utf-8")),
              "schema_rewrites": rewrites, "temperature": 0, "api_cost_usd": 0,
              "local_compute": "not metered as API spend", "start_utc": local_transport.utc_now(),
              "status": "failed", "usage": None, "response_id": None, "response_sha256": None}
    receipts_dir.mkdir(parents=True, exist_ok=True)
    stamp = record["start_utc"].replace("-", "").replace(":", "").replace(".", "")
    path = receipts_dir / f"local-{stamp}-{secrets.token_hex(8)}.json"
    # Reserve the receipt before contact; a collision fails before any inference.
    try:
        handle = path.open("x", encoding="utf-8", newline="\n")
    except OSError:
        fail("could not reserve local receipt; no inference was sent")
    with handle:
        try:
            response = local_transport.http_json("POST", prepared["base_url"] + "/chat/completions", body, timeout)
            if not isinstance(response, dict) or response.get("error"):
                raise ValueError("server error or malformed response")
            record["response_sha256"] = local_transport.sha256(json.dumps(response, sort_keys=True).encode("utf-8"))
            record["response_model"] = response.get("model")
            if record["response_model"] not in {prepared["alias"], prepared["loaded_model_binding"]["model_key"]}:
                raise ValueError("response model differs from the verified loaded alias and catalog model")
            record["response_id"] = response.get("id")
            record["usage"] = response.get("usage")
            choices = response.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or choices[0]["finish_reason"] != "stop":
                raise ValueError("expected one complete, non-truncated choice")
            message = choices[0]["message"]
            usage = response.get("usage") or {}
            if not isinstance(message, dict) or not isinstance(usage, dict):
                raise ValueError("malformed message or usage")
            details = usage.get("completion_tokens_details") or {}
            if (not isinstance(details, dict) or details.get("reasoning_tokens", 0) != 0
                    or message.get("reasoning") or message.get("reasoning_content")):
                raise ValueError("response contains reasoning")
            text = message["content"]
            if not isinstance(text, str) or re.search(r"<\s*think\b", text, re.I):
                raise ValueError("completion content is not clean text")
            target = local_transport.strict_json(text)
            if not jsonschema.Draft202012Validator(schema).is_valid(target):
                raise ValueError("local response fails the four-field target schema")
            refuse_secrets(target, "local answer")
            record.update(status="completed", target=target)
        except (OSError, ValueError, KeyError, IndexError, TypeError,
                local_transport.http.client.HTTPException) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            fail("local inference failed; inspect its receipt before any retry")
        finally:
            record["end_utc"] = local_transport.utc_now()
            json.dump(record, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
    return DispatchResult(text, path, prepared["model"])


def local_receipt_hash(result: DispatchResult, scenario: dict[str, Any], target: dict[str, Any],
                       verified_at: str) -> str:
    """A local row must bind the completed receipt, not merely a model alias."""
    try:
        raw = result.receipt_path.read_bytes()
        record = local_transport.strict_json(raw)
        gguf_hash, license_hash = LOCAL_MODELS[result.model]
        binding = record["loaded_model_binding"]
        if (record["status"] != "completed" or record["provider"] != "local_model"
                or record["model"] != result.model or record["input"] != scenario["input"]
                or record["source_group"] != scenario["source_group"] or record["target"] != target
                or record["source_provenance"] != scenario["source_provenance"]
                or record["purpose"] != purpose_for(scenario["source_group"])
                or record["terms_verified_at"] != verified_at or record["api_cost_usd"] != 0
                or record["model_gguf_sha256"] != gguf_hash or record["license_sha256"] != license_hash
                or binding["sha256"] != gguf_hash or binding["identifier"] != record["alias"]
                or record["response_model"] not in {record["alias"], binding["model_key"]}
                or not binding["model_key"] or record["model_gguf_bytes"] <= 0
                or not record["conversion_receipt"] or not record["license_file"]
                or not record["model_gguf_path"]):
            fail("local receipt does not bind this row and admitted model")
        for key in ("conversion_receipt_sha256", "prompt_sha256", "schema_sha256", "request_sha256", "response_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", record[key]) is None:
                fail("local receipt is missing a required artifact hash")
        for key in ("start_utc", "end_utc"):
            if UTC_PATTERN.fullmatch(record[key]) is None:
                fail("local receipt is missing an explicit UTC time")
        messages = prompt_template.render_messages(scenario["input"])
        if result.model.startswith("Qwen/"):
            messages[0]["content"] += local_transport.QWEN3_PROMPT
        schema = _schema_validator().schema["properties"]["target"]
        if (record["prompt_sha256"] != local_transport.sha256(json.dumps(messages, sort_keys=True).encode("utf-8"))
                or record["schema_sha256"] != local_transport.sha256(json.dumps(schema, sort_keys=True).encode("utf-8"))
                or record["conversion_receipt_sha256"] != local_transport.sha256(Path(record["conversion_receipt"]).read_bytes())
                or license_hash != local_transport.sha256(Path(record["license_file"]).read_bytes())):
            fail("local receipt artifact or prompt bytes changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail(f"local receipt verification failed: {type(exc).__name__}")
    return local_transport.sha256(raw)


def build_row(
    scenario: dict[str, Any],
    target: dict[str, Any],
    *,
    provider: str,
    model: str,
    receipt: str,
    verified_at: str,
    generated_at: str,
    sets: list[validate_rows.HeldOutSet],
) -> dict[str, Any]:
    reasons, overlap, nearest = validate_rows.leak_wall(
        {"input": scenario["input"], "target": target, "receipt_ref": receipt,
         "source_group": scenario["source_group"]},
        sets,
    )
    if reasons or overlap >= validate_rows.DEFAULT_THRESHOLD - validate_rows.DEFAULT_QUARANTINE_BAND:
        where = f" nearest {nearest}" if nearest else ""
        fail(f"leak wall refused {scenario['source_group']}{where}")
    row = {
        "schema_version": SCHEMA_VERSION,
        "split": scenario["split"],
        "source_kind": "provider_exemplar",
        "source_provider": provider,
        "source_model": model,
        "terms_tier": "open_weight_license" if provider == "local_model" else "competing_only_clause",
        "terms_verified_at": verified_at,
        "generated_at": generated_at,
        "receipt_ref": receipt,
        "source_group": scenario["source_group"],
        "input": scenario["input"],
        "target": target,
        "scrub": {"gitleaks_clean": True, "redact_pass": True, "scrubbed_at": generated_at},
        "heldout_check": {
            "set_a_manifest": sets[0].manifest,
            "set_b_manifest": sets[1].manifest,
            "source_lineage": "clear",
            "near_duplicate": {
                "status": "clear",
                "max_overlap": round(overlap, 6),
                "threshold": validate_rows.DEFAULT_THRESHOLD,
                "quarantine_band": validate_rows.DEFAULT_QUARANTINE_BAND,
            },
            "title_objective_match": False,
            "overlap": "none",
        },
    }
    row["row_id"] = validate_rows.canonical_row_id(row["input"], row["target"])
    return row


def live_scrub(directory: Path) -> str | None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "scan-dataset.ps1"
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), "-Path", str(directory)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
    )
    if completed.returncode != 0:
        fail(f"dataset scan failed with exit {completed.returncode}")
    return None


def generate(
    scenarios: list[dict[str, Any]],
    *,
    provider: str,
    set_a: Path,
    set_b: Path,
    out: Path,
    terms_verified_at: str,
    generated_at: str,
    dry_run: bool,
    dispatch: Dispatch | None = None,
    scrub: Scrub | None = None,
    source_root: Path | None = None,
    timeout: int = 120,
    max_output: int = 1024,
    worktree: Path | None = None,
    local_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if provider not in COMPETING_PROVIDERS and provider != "local_model":
        fail(f"provider is not a competing-only exemplar source: {provider}")
    if UTC_PATTERN.fullmatch(terms_verified_at) is None or UTC_PATTERN.fullmatch(generated_at) is None:
        fail("terms-verified-at and generated-at must be explicit UTC timestamps")
    if not (1 <= max_output <= MAX_OUTPUT_TOKENS) or not (1 <= timeout <= MAX_TIMEOUT_SECONDS):
        fail("max-output and timeout are outside the per-call bound")
    try:
        sets = [validate_rows.HeldOutSet("a", set_a), validate_rows.HeldOutSet("b", set_b)]
    except ValueError as exc:
        fail(str(exc))
    if out.exists():
        fail(f"refusing existing --out: {out}")
    root = infer_source_root(set_a, set_b, source_root)
    validate_scenarios_for_dispatch(scenarios, source_root=root, set_a=set_a, set_b=set_b, sets=sets)
    for scenario in scenarios:
        exemplar_packet(scenario["input"])  # Preserve the same packet-size bound on every route.
    if dry_run:
        return {"dry_run": True, "rows": 0, "receipts": 0}
    if provider != "local_model" and dispatch is None:
        fail("cloud exemplar generation is held by the M2 training-data terms gate")
    if provider == "local_model" and dispatch is None:
        if not isinstance(local_options, dict):
            fail("local_model requires pinned model, conversion, license and receipt paths")
        options = dict(local_options)
        receipts_dir = options.pop("receipts_dir", None)
        if not isinstance(receipts_dir, Path) or "receipts" not in receipts_dir.parts:
            fail("local receipts directory must contain a receipts path segment")
        if set(options) != {"model", "model_gguf", "conversion_receipt", "license_file", "base_url", "lms_models_dir"}:
            fail("local_model requires all pinned model options")
        prepared = prepare_local_model(timeout=timeout, **options)
    caller = dispatch or (lambda name, packet, purpose: live_dispatch(
        name, packet, purpose, timeout=timeout, max_output=max_output, worktree=worktree))
    checker = scrub or live_scrub
    validator = _schema_validator()
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = (local_dispatch(scenario, purpose_for(scenario["source_group"]), prepared,
                                receipts_dir=receipts_dir, verified_at=terms_verified_at,
                                timeout=timeout, max_output=max_output)
                  if provider == "local_model" and dispatch is None
                  else caller(provider, exemplar_packet(scenario["input"]), purpose_for(scenario["source_group"])))
        if not isinstance(result, DispatchResult):
            fail("dispatch returned no result")
        if not result.model.strip():
            fail("receipt does not record a model")
        target = parse_target(result.text)
        row = build_row(
            scenario, target, provider=provider, model=result.model,
            receipt=receipt_ref(result.receipt_path), verified_at=terms_verified_at,
            generated_at=generated_at, sets=sets,
        )
        if provider == "local_model":
            row["receipt_sha256"] = local_receipt_hash(result, scenario, target, terms_verified_at)
        errors = list(validator.iter_errors(row))
        if errors:
            fail(f"row failed the schema: {errors[0].message}")
        rows.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m2-g1-rows-") as directory:
        folder = Path(directory)
        draft = folder / "rows.jsonl"
        draft.write_text(
            "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8", newline="\n",
        )
        report_ref = checker(folder)
        if report_ref is not None:
            if RECEIPT_REF_PATTERN.fullmatch(report_ref) is None:
                fail("scrub report path is not a relative reference")
            for row in rows:
                row["scrub"]["scan_report_ref"] = report_ref
                errors = list(validator.iter_errors(row))
                if errors:
                    fail(f"row failed the schema: {errors[0].message}")
            draft.write_text(
                "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8", newline="\n",
            )
        try:
            os.replace(draft, out)
        except OSError as exc:
            fail(f"could not publish rows: {exc.strerror}")
    return {"dry_run": False, "rows": len(rows), "receipts": len(rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate DIRECTOR exemplar rows (M2-G1).")
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--set-a", type=Path, required=True, dest="set_a")
    parser.add_argument("--set-b", type=Path, required=True, dest="set_b")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--terms-verified-at", required=True, dest="terms_verified_at")
    parser.add_argument("--generated-at", default=None, dest="generated_at")
    parser.add_argument("--max-rows", type=int, default=1)
    parser.add_argument("--max-output", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--worktree", type=Path, default=None)
    parser.add_argument("--source-root", type=Path, default=None, dest="source_root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--model-gguf", type=Path)
    parser.add_argument("--conversion-receipt", type=Path)
    parser.add_argument("--license-file", type=Path)
    parser.add_argument("--receipts-dir", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--lms-models-dir", type=Path, default=Path.home() / ".lmstudio" / "models")
    args = parser.parse_args(argv)
    generated_at = args.generated_at or args.terms_verified_at
    try:
        scenarios = load_scenarios(args.scenarios, args.max_rows)
        report = generate(
            scenarios, provider=args.provider, set_a=args.set_a, set_b=args.set_b, out=args.out,
            terms_verified_at=args.terms_verified_at, generated_at=generated_at, dry_run=args.dry_run,
            source_root=args.source_root,
            timeout=args.timeout, max_output=args.max_output, worktree=args.worktree,
            local_options={key: getattr(args, key) for key in (
                "model", "model_gguf", "conversion_receipt", "license_file", "receipts_dir",
                "base_url", "lms_models_dir")} if args.provider == "local_model" else None,
        )
    except SystemExit as exc:
        if exc.code not in (0, None):
            return int(exc.code) if isinstance(exc.code, int) else 2
        raise
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | END]]
