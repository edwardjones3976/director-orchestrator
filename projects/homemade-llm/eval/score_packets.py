# [[Cursor | Composer 2.5 | 2026-09-26 | 01:40 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 00:40 UTC | BEGIN]]
# [[Cursor | cursor-agent | 2026-09-26 | 00:35 UTC | BEGIN]]
"""Score model-work-packet candidates against reference packets (M1 eval).

Usage:
    python projects/homemade-llm/eval/score_packets.py \\
        --refs path/to/gold --candidates path/to/model_out --out path/to/report
    python projects/homemade-llm/eval/score_packets.py ... --grades graders.jsonl

Writes fields.csv, packets.csv, and summary.json under --out. Similarity is mechanical;
acceptance comes only from optional grader JSONL rows.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FIELDS: tuple[str, ...] = (
    "objective",
    "non_goals",
    "constraints",
    "acceptance_checks",
    "evidence_requirements",
)
CHANGE_BAR = 0.10

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA = _REPO_ROOT / "docs" / "schemas" / "model-work-packet-v1.schema.json"


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_field_value(value: Any) -> str:
    """Normalize a packet field for text comparison."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _normalize_line_endings(value).strip()
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _normalize_line_endings(str(value)).strip()


def field_similarity(reference: Any, candidate: Any) -> float:
    ref_n = normalize_field_value(reference)
    cand_n = normalize_field_value(candidate)
    if not ref_n and not cand_n:
        return 1.0
    return difflib.SequenceMatcher(None, ref_n, cand_n, autojunk=False).ratio()


def field_changed_fraction(reference: Any, candidate: Any) -> float:
    ref_n = normalize_field_value(reference)
    cand_n = normalize_field_value(candidate)
    if not ref_n and not cand_n:
        return 0.0
    matcher = difflib.SequenceMatcher(None, ref_n, cand_n, autojunk=False)
    edit_size = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            edit_size += max(i2 - i1, j2 - j1)
    return min(1.0, edit_size / max(len(ref_n), 1))


def weighted_changed_fraction(
    reference: dict[str, Any],
    candidate: dict[str, Any] | None,
    fields: Iterable[str],
) -> float:
    weights: list[float] = []
    changed_fractions: list[float] = []
    for name in fields:
        ref_val = reference.get(name)
        ref_n = normalize_field_value(ref_val)
        weight = float(len(ref_n)) if ref_n else 1.0
        weights.append(weight)
        if candidate is None:
            changed_fractions.append(1.0)
        else:
            changed_fractions.append(field_changed_fraction(ref_val, candidate.get(name)))
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0
    return sum(c * w for c, w in zip(changed_fractions, weights)) / total_w


def load_json_dir(directory: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


def try_load_candidate(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Return (parsed dict or None, present_on_disk)."""
    if not path.is_file():
        return None, False
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except json.JSONDecodeError:
        return None, True


def schema_validator(schema_path: Path) -> Any | None:
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return None
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate_candidate(
    candidate: dict[str, Any] | None,
    validator: Any | None,
) -> bool | str:
    if validator is None:
        return "unchecked"
    if candidate is None:
        return False
    return validator.is_valid(candidate)


def load_grades(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def count_malformed_grade_rows(grades: list[dict[str, Any]]) -> int:
    """Count grade rows whose ``accepted`` value is not a JSON boolean."""
    return sum(1 for row in grades if "accepted" in row and not isinstance(row["accepted"], bool))


def packet_accepted_as_is(
    packet_id: str,
    fields: Iterable[str],
    grades: list[dict[str, Any]],
    *,
    candidate_ok: bool = True,
) -> bool:
    if not candidate_ok:
        return False
    field_set = set(fields)
    graded_by_grader: dict[str, dict[str, Any]] = {}
    for row in grades:
        if row.get("id") != packet_id:
            continue
        field = row.get("field")
        if field not in field_set:
            continue
        if row.get("accepted") is not True:
            return False
        grader = str(row["grader"])
        graded_by_grader.setdefault(grader, {})[field] = True
    complete_graders = sum(
        1 for field_map in graded_by_grader.values() if field_set <= field_map.keys()
    )
    return complete_graders >= 2


def score(
    references: dict[str, dict[str, Any]],
    candidates_dir: Path,
    out_dir: Path,
    fields: tuple[str, ...],
    grades_path: Path | None,
    schema_path: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    grades = load_grades(grades_path)
    validator = schema_validator(schema_path)
    has_grades = grades_path is not None

    field_rows: list[dict[str, Any]] = []
    packet_rows: list[dict[str, Any]] = []
    missing_or_invalid = 0
    per_field_sim: dict[str, list[float]] = {f: [] for f in fields}
    packet_candidate_ok: dict[str, bool] = {}

    for packet_id in sorted(references):
        ref = references[packet_id]
        cand_path = candidates_dir / f"{packet_id}.json"
        candidate, present = try_load_candidate(cand_path)
        invalid = present and candidate is None
        if not present or invalid:
            missing_or_invalid += 1
        candidate_ok = present and candidate is not None
        packet_candidate_ok[packet_id] = candidate_ok

        schema_flag = validate_candidate(candidate, validator)
        schema_cell: bool | str = schema_flag

        for field in fields:
            ref_field = ref.get(field)
            if candidate is None:
                sim = 0.0
                chg = field_changed_fraction(ref_field, None)
            else:
                cand_field = candidate.get(field)
                sim = field_similarity(ref_field, cand_field)
                chg = field_changed_fraction(ref_field, cand_field)
            per_field_sim[field].append(sim)
            field_rows.append(
                {
                    "id": packet_id,
                    "field": field,
                    "similarity": f"{sim:.6f}",
                    "changed_fraction": f"{chg:.6f}",
                    "candidate_present": str(present).lower(),
                    "schema_valid_candidate": (
                        "unchecked" if schema_cell == "unchecked" else str(bool(schema_cell)).lower()
                    ),
                }
            )

        pkt_changed = weighted_changed_fraction(ref, candidate, fields)
        row: dict[str, Any] = {
            "id": packet_id,
            "text_changed_fraction": f"{pkt_changed:.6f}",
            "schema_valid": (
                "unchecked" if schema_cell == "unchecked" else str(bool(schema_cell)).lower()
            ),
            "candidate_present": str(present).lower(),
        }
        if has_grades:
            row["accepted_as_is"] = str(
                packet_accepted_as_is(packet_id, fields, grades, candidate_ok=candidate_ok)
            ).lower()
        packet_rows.append(row)

    fields_path = out_dir / "fields.csv"
    with fields_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "id",
                "field",
                "similarity",
                "changed_fraction",
                "candidate_present",
                "schema_valid_candidate",
            ],
        )
        writer.writeheader()
        writer.writerows(field_rows)

    packets_path = out_dir / "packets.csv"
    packet_columns = ["id", "text_changed_fraction", "schema_valid", "candidate_present"]
    if has_grades:
        packet_columns.append("accepted_as_is")
    with packets_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=packet_columns)
        writer.writeheader()
        writer.writerows(packet_rows)

    n_packets = len(references)
    mean_similarity_by_field = {
        f: (sum(per_field_sim[f]) / len(per_field_sim[f]) if per_field_sim[f] else 0.0) for f in fields
    }

    accepted_ids: set[str] = set()
    if has_grades:
        for packet_id in references:
            if packet_accepted_as_is(
                packet_id,
                fields,
                grades,
                candidate_ok=packet_candidate_ok[packet_id],
            ):
                accepted_ids.add(packet_id)

    if has_grades:
        remaining = [r for r in packet_rows if r["id"] not in accepted_ids]
        pct_accepted = (len(accepted_ids) / n_packets * 100.0) if n_packets else 0.0
    else:
        remaining = packet_rows
        pct_accepted = None

    under_bar = 0
    for r in remaining:
        if float(r["text_changed_fraction"]) <= CHANGE_BAR:
            under_bar += 1
    pct_remaining_under_bar = (under_bar / len(remaining) * 100.0) if remaining else 0.0

    summary: dict[str, Any] = {
        "packet_count": n_packets,
        "missing_or_invalid_candidates": missing_or_invalid,
        "per_field_mean_similarity": {k: round(v, 6) for k, v in mean_similarity_by_field.items()},
        "pct_remaining_at_or_under_10pct_text_change": round(pct_remaining_under_bar, 6),
    }
    if has_grades:
        summary["pct_accepted_as_is"] = round(pct_accepted, 6)
        summary["accepted_as_is_count"] = len(accepted_ids)
        summary["remaining_packet_count"] = len(remaining)
        summary["malformed_grade_rows"] = count_malformed_grade_rows(grades)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def parse_fields(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_FIELDS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score packet candidates against references.")
    parser.add_argument("--refs", type=Path, required=True, help="Directory of gold <id>.json files")
    parser.add_argument("--candidates", type=Path, required=True, help="Directory of candidate <id>.json files")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for CSV and summary")
    parser.add_argument("--grades", type=Path, default=None, help="Optional grader verdicts JSONL")
    parser.add_argument("--fields", type=str, default=None, help="Comma-separated field names to score")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help="JSON Schema path for candidate validation",
    )
    args = parser.parse_args(argv)
    references = load_json_dir(args.refs)
    fields = parse_fields(args.fields)
    score(references, args.candidates, args.out, fields, args.grades, args.schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# [[Cursor | cursor-agent | 2026-09-26 | 00:35 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 00:40 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-26 | 01:40 UTC | END]]
