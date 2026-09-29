# [[Cursor | Auto | 2026-09-29 | 06:54 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 05:32 UTC | BEGIN]]
"""M2-G1 exemplar harness: one bounded competing-only dispatch per scenario.

A dry run checks the scenarios and the held-out folders, then writes nothing
and calls no provider. A live run calls ``dispatch.py`` once per scenario,
reads the model name from that call's receipt, and writes schema-valid rows
only when every scenario clears the leak wall and the scrub check. Any error
stops the run before ``--out`` is created.

    python projects/homemade-llm/eval/generate_rows.py --dry-run \\
        --scenarios scenarios.jsonl --provider anthropic_api \\
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
from typing import Any, Callable

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
import build_inputs  # noqa: E402
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
    "nodes{commit{committedDate}}}}}}}}}}}"
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
    references.update(_load_references(set_b))
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
            continue
        closing = pr.get("closingIssuesReferences") or {}
        if (closing.get("pageInfo") or {}).get("hasNextPage") is not False:
            fail(f"{label}: linked PR closing-issue history is incomplete")
        issue_ref = rf"(?:{re.escape(repository)})?#{block['github_issue']}\b"
        claimed = (re.search(rf"(?im)^Issue:\s*{issue_ref}", pr.get("body") or "") is not None
                   or re.search(rf"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s*:?\s*{issue_ref}",
                                pr.get("body") or "", re.I) is not None
                   or any(node.get("number") == block["github_issue"]
                          and (node.get("repository") or {}).get("nameWithOwner", "").casefold() == repository.casefold()
                          for node in closing.get("nodes") or []))
        if not claimed:
            if (pr.get("lastEditedAt") is not None
                    or (pr.get("userContentEdits") or {}).get("totalCount") != 0):
                fail(f"{label}: edited linked PR may hide an earlier work claim")
            continue  # A discussion cross-reference is not a work claim.
        commits = pr.get("commits") or {}
        if (commits.get("pageInfo") or {}).get("hasNextPage") is not False or not commits.get("nodes"):
            fail(f"{label}: linked PR commit history is incomplete")
        dates = [utc((node.get("commit") or {}).get("committedDate"))
                 for node in commits["nodes"]]
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
) -> None:
    for index, scenario in enumerate(scenarios, start=1):
        label = f"scenario {index} ({scenario.get('source_group', '?')})"
        validate_scenario_source_provenance(
            scenario, source_root=source_root, set_a=set_a, set_b=set_b, label=label,
        )
        validate_chronology(scenario, source_root, label)


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
        "terms_tier": "competing_only_clause",
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
) -> dict[str, Any]:
    if provider not in COMPETING_PROVIDERS:
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
    validate_scenarios_for_dispatch(scenarios, source_root=root, set_a=set_a, set_b=set_b)
    if dry_run:
        return {"dry_run": True, "rows": 0, "receipts": 0}
    caller = dispatch or (lambda name, packet, purpose: live_dispatch(
        name, packet, purpose, timeout=timeout, max_output=max_output, worktree=worktree))
    checker = scrub or live_scrub
    validator = _schema_validator()
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = caller(provider, exemplar_packet(scenario["input"]), purpose_for(scenario["source_group"]))
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
    args = parser.parse_args(argv)
    generated_at = args.generated_at or args.terms_verified_at
    try:
        scenarios = load_scenarios(args.scenarios, args.max_rows)
        report = generate(
            scenarios, provider=args.provider, set_a=args.set_a, set_b=args.set_b, out=args.out,
            terms_verified_at=args.terms_verified_at, generated_at=generated_at, dry_run=args.dry_run,
            source_root=args.source_root,
            timeout=args.timeout, max_output=args.max_output, worktree=args.worktree,
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
