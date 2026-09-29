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
        --terms-verified-at 2026-09-28T20:00:00Z
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
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
DISPATCH_PY = Path.home() / ".claude" / "skills" / "provider-dispatch" / "dispatch.py"
Dispatch = Callable[[str, str, str], "DispatchResult"]
Scrub = Callable[[Path], str | None]


class DispatchResult:
    """One completed provider call. ``model`` is the name the receipt records."""

    def __init__(self, text: str, receipt_path: Path, model: str) -> None:
        self.text = text
        self.receipt_path = receipt_path
        self.model = model


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


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
    if path.suffix != ".json":
        fail("receipt does not record a model")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("receipt does not record a model")
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str) or not model.strip():
        fail("receipt does not record a model")
    return model


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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    generated_at = args.generated_at or args.terms_verified_at
    try:
        scenarios = load_scenarios(args.scenarios, args.max_rows)
        report = generate(
            scenarios, provider=args.provider, set_a=args.set_a, set_b=args.set_b, out=args.out,
            terms_verified_at=args.terms_verified_at, generated_at=generated_at, dry_run=args.dry_run,
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
