"""Validate DIRECTOR data v1 rows before they are trained on (packet M2-S1, D-M2-1 to D-M2-5).

Usage:
    python projects/homemade-llm/eval/validate_rows.py \\
        --rows rows.jsonl --set-a path/to/set-a/refs --set-b path/to/set-b/refs \\
        --out path/to/validated [--set-a-manifest HEX --set-b-manifest HEX] \\
        [--threshold 0.5 --quarantine-band 0.2]

Every row is checked, in this order, and a row that fails any check is never
written:

1. Row shape: valid JSON object validating against
   ``schemas/director-data-v1-row.schema.json`` (excluded terms tiers and
   providers are unrepresentable there, so a planted excluded-tier row fails
   here); ``row_id`` equal to the first 16 hex characters of the SHA-256 of the
   canonical JSON of ``{"input": ..., "target": ...}``; no duplicate ``row_id``;
   ``terms_verified_at`` present on every provider row.
2. Manifest binding: ``heldout_check.set_a_manifest`` and ``set_b_manifest``
   equal the D-M1-0 manifests computed from the read-only reference folders
   (and the ``--set-*-manifest`` pins when given).
3. Leak wall (D-M2-3), against both held-out sets:
   - lineage: ``receipt_ref``, ``source_group`` and every ``input.context``
     ``source_ref`` must not resolve to a held-out identity (a reference id, or
     a reference's recorded source file or title);
   - title and objective: the normalized ``input.title`` or ``input.objective``
     must not equal a held-out item's title or objective;
   - near-duplicate: the highest word-shingle containment of any input or
     scored target field against the corresponding held-out field decides:
     at or above ``threshold`` the row is refused; inside
     ``[threshold - quarantine_band, threshold)`` the row is written to
     ``quarantine.jsonl`` with split ``quarantine`` and the nearest item named,
     never to the accepted output; below the band it is clear. Any field text
     that carries a whole scored-field string or an eight-word shingle of a
     reference is refused outright, as ``build_inputs.py`` already does for
     inputs.
4. Split: every ``source_group`` sits on one side of train and dev; the report
   carries the unique group count per split. A row the producer already marked
   ``quarantine`` stays in the quarantine file until a non-author reviewer
   decides (D-M2-3).

Outputs, in an empty ``--out`` directory: ``accepted.jsonl``, ``quarantine.jsonl``
and ``report.json`` (counts, per-row rejection reasons naming the check and the
nearest held-out item id, never held-out text). Exit status 0 when no row was
rejected, 1 otherwise. Nothing is sent anywhere; held-out text never leaves the
report as anything but an item id and a field name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_inputs  # noqa: E402

ROW_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "director-data-v1-row.schema.json"
SCORED_FIELDS = build_inputs.SCORED_FIELDS
PROVIDER_SOURCES = frozenset({"anthropic_api", "openai", "gemini_api", "cursor"})
SHINGLE_WORDS = 5
DEFAULT_THRESHOLD = 0.5
DEFAULT_QUARANTINE_BAND = 0.2


def canonical_row_id(input_block: Any, target_block: Any) -> str:
    """First 16 hex characters of SHA-256 over canonical JSON of input plus target."""
    canonical = json.dumps({"input": input_block, "target": target_block},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _field_text(value: Any) -> str:
    return " ".join(build_inputs._flatten_strings(value))


def _shingles(text: str, size: int = SHINGLE_WORDS) -> set[str]:
    words = build_inputs._words(text)
    if len(words) < size:
        return set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def shingle_containment(candidate: str, reference: str) -> float:
    """Share of the candidate's word shingles that also occur in the reference.

    Fields shorter than ``SHINGLE_WORDS`` words carry no shingles and score 0
    here; the whole-string leak check covers them.
    """
    ours = _shingles(candidate)
    if not ours:
        return 0.0
    theirs = _shingles(reference)
    if not theirs:
        return 0.0
    return len(ours & theirs) / len(ours)


class HeldOutSet:
    """Read-only view of one held-out reference folder and its D-M1-0 manifest."""

    def __init__(self, name: str, refs_dir: Path):
        self.name = name
        self.refs_dir = refs_dir.resolve()
        if not self.refs_dir.is_dir():
            raise ValueError(f"set {name}: refs is not a directory: {self.refs_dir}")
        self.ids = build_inputs._reference_ids(self.refs_dir)
        if not self.ids:
            raise ValueError(f"set {name}: no reference packets under {self.refs_dir}")
        self.references: dict[str, dict[str, Any]] = {}
        self.titles: dict[str, str] = {}
        for item_id in self.ids:
            reference = build_inputs._load_json(self.refs_dir / f"{item_id}.json")
            if not isinstance(reference, dict):
                raise ValueError(f"set {name}: reference {item_id} is not a JSON object")
            self.references[item_id] = reference
            title = build_inputs._resolve_title(item_id, self.refs_dir, name, {})
            if title:
                self.titles[item_id] = title
        self.manifest = build_inputs.manifest(self.refs_dir)
        self.lineage_keys = build_inputs.lineage_keys(self.refs_dir, self.ids)
        self.normalized_titles = {
            build_inputs._normalize_text(title): item_id for item_id, title in self.titles.items()
        }
        self.normalized_objectives = {
            build_inputs._normalize_text(str(ref.get("objective", ""))): item_id
            for item_id, ref in self.references.items() if ref.get("objective")
        }
        self.normalized_titles.pop("", None)
        self.normalized_objectives.pop("", None)

    def reference_field_text(self, item_id: str, field: str) -> str:
        if field == "title":
            return self.titles.get(item_id, "")
        return _field_text(self.references[item_id].get(field))


def _row_fields(row: dict[str, Any]) -> dict[str, str]:
    """Texts compared for near-duplicates, keyed by the held-out field they map to."""
    fields = {
        "objective": str(row["input"].get("objective", "")),
        "title": str(row["input"].get("title", "")),
    }
    for field in SCORED_FIELDS:
        fields[field] = _field_text(row["target"].get(field))
    return fields


def _context_texts(row: dict[str, Any]) -> list[str]:
    return [str(item.get("text", "")) for item in row["input"].get("context", []) if isinstance(item, dict)]


def leak_wall(row: dict[str, Any], sets: list[HeldOutSet]) -> tuple[list[str], float, str | None]:
    """D-M2-3 checks 1 to 3. Returns (refusal reasons, max overlap, nearest item)."""
    reasons: list[str] = []
    # Check 1: lineage by identity.
    pointers = {"receipt_ref": row.get("receipt_ref", ""), "source_group": row.get("source_group", "")}
    for index, item in enumerate(row["input"].get("context", [])):
        if isinstance(item, dict):
            pointers[f"input.context[{index}].source_ref"] = str(item.get("source_ref", ""))
    for label, pointer in pointers.items():
        if not pointer:
            continue
        forms = build_inputs._source_ref_forms(str(pointer))
        for held in sets:
            if forms & held.lineage_keys:
                reasons.append(f"lineage: {label} resolves to a held-out item in set {held.name}")
                break
    # Check 3: title and objective match.
    title = build_inputs._normalize_text(str(row["input"].get("title", "")))
    objective = build_inputs._normalize_text(str(row["input"].get("objective", "")))
    for held in sets:
        if title and title in held.normalized_titles:
            reasons.append(f"title_objective_match: title equals set {held.name} item {held.normalized_titles[title]}")
        if objective and objective in held.normalized_objectives:
            reasons.append(
                f"title_objective_match: objective equals set {held.name} item {held.normalized_objectives[objective]}")
    # Whole-string and eight-word-shingle leak of any scored reference text into any row text.
    texts = list(_row_fields(row).values()) + _context_texts(row)
    for held in sets:
        for item_id in held.ids:
            reference = held.references[item_id]
            for text in texts:
                if build_inputs._text_leak_match(reference, text) is not None:
                    reasons.append(f"scored_text_leak: set {held.name} item {item_id}")
                    break
            else:
                continue
            break
    # Check 2: near-duplicate containment per corresponding field.
    max_overlap, nearest = 0.0, None
    fields = _row_fields(row)
    for held in sets:
        for item_id in held.ids:
            for field, text in fields.items():
                overlap = shingle_containment(text, held.reference_field_text(item_id, field))
                if overlap > max_overlap:
                    max_overlap, nearest = overlap, f"{held.name}/{item_id}/{field}"
    return reasons, max_overlap, nearest


def _canonical_reasons(errors: list[str]) -> list[str]:
    return sorted(set(errors))


def validate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate DIRECTOR data v1 rows (M2-S1).")
    parser.add_argument("--rows", type=Path, required=True, help="JSONL of candidate rows.")
    parser.add_argument("--set-a", type=Path, required=True, dest="set_a")
    parser.add_argument("--set-b", type=Path, required=True, dest="set_b")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--set-a-manifest", default=None, dest="set_a_manifest")
    parser.add_argument("--set-b-manifest", default=None, dest="set_b_manifest")
    parser.add_argument("--schema", type=Path, default=ROW_SCHEMA)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--quarantine-band", type=float, default=DEFAULT_QUARANTINE_BAND, dest="quarantine_band")
    args = parser.parse_args(argv)

    def fail(message: str) -> int:
        print(message, file=sys.stderr)
        return 2

    if not (0.0 < args.threshold <= 1.0) or not (0.0 <= args.quarantine_band < args.threshold):
        return fail("threshold must be in (0, 1] and quarantine-band in [0, threshold)")
    out_dir = args.out.resolve()
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        return fail(f"refusing non-empty --out: {out_dir}")
    if not args.rows.is_file():
        return fail(f"rows is not a file: {args.rows}")
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return fail("schema validation unavailable: jsonschema is required")
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    try:
        sets = [HeldOutSet("a", args.set_a), HeldOutSet("b", args.set_b)]
    except ValueError as exc:
        return fail(str(exc))
    for held, pinned in ((sets[0], args.set_a_manifest), (sets[1], args.set_b_manifest)):
        if pinned is not None and pinned != held.manifest:
            return fail(f"set {held.name} manifest mismatch: computed {held.manifest}")
    manifests = {held.name: held.manifest for held in sets}

    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    group_sides: dict[str, set[str]] = {}
    group_lines: dict[str, list[int]] = {}

    with args.rows.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                rejected.append({"line": line_number, "row_id": None, "reasons": [f"json: {exc.msg}"]})
                continue
            if not isinstance(row, dict):
                rejected.append({"line": line_number, "row_id": None, "reasons": ["json: row is not an object"]})
                continue
            row_id = row.get("row_id") if isinstance(row.get("row_id"), str) else None
            reasons = [f"schema: {'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
                       for error in validator.iter_errors(row)]
            if reasons:
                rejected.append({"line": line_number, "row_id": row_id, "reasons": _canonical_reasons(reasons)})
                continue
            expected_id = canonical_row_id(row["input"], row["target"])
            if row_id != expected_id:
                reasons.append("row_id: does not equal the canonical digest of input plus target")
            if row_id in seen_ids:
                reasons.append(f"row_id: duplicate of line {seen_ids[row_id]}")
            else:
                seen_ids[row_id] = line_number
            if row["source_provider"] in PROVIDER_SOURCES and not row.get("terms_verified_at"):
                reasons.append("terms_verified_at: required on a provider row")
            check = row["heldout_check"]
            for held in sets:
                if check.get(f"set_{held.name}_manifest") != held.manifest:
                    reasons.append(f"heldout_check: set_{held.name}_manifest does not match the reference folder")
            near = check["near_duplicate"]
            if near["threshold"] != args.threshold or near["quarantine_band"] != args.quarantine_band:
                reasons.append("heldout_check: near_duplicate threshold or quarantine_band differ from this run")
            leak_reasons, max_overlap, nearest = leak_wall(row, sets)
            reasons.extend(leak_reasons)
            if max_overlap >= args.threshold:
                reasons.append(f"near_duplicate: overlap {max_overlap:.3f} at or above threshold, nearest {nearest}")
            if reasons:
                rejected.append({"line": line_number, "row_id": row_id, "reasons": _canonical_reasons(reasons)})
                continue
            in_band = max_overlap >= args.threshold - args.quarantine_band
            declared_status = near["status"]
            if in_band or declared_status == "quarantined":
                row["split"] = "quarantine"
                near["status"] = "quarantined"
                near["max_overlap"] = max(max_overlap, float(near.get("max_overlap", 0.0)))
                if nearest is not None or "nearest_item" not in near:
                    near["nearest_item"] = nearest or near.get("nearest_item") or "unknown"
                check["overlap"] = "quarantined"
                if validator.is_valid(row):
                    quarantined.append(row)
                else:
                    rejected.append({"line": line_number, "row_id": row_id,
                                     "reasons": ["quarantine: rewritten row failed schema validation"]})
                continue
            if declared_status == "clear" and float(near.get("max_overlap", 0.0)) < max_overlap:
                near["max_overlap"] = max_overlap
                if nearest is not None:
                    near["nearest_item"] = nearest
            group = row["source_group"]
            group_sides.setdefault(group, set()).add(row["split"])
            group_lines.setdefault(group, []).append(line_number)
            accepted.append(row)

    # Split check: every source_group on exactly one side.
    straddling = sorted(group for group, sides in group_sides.items() if len(sides) > 1)
    if straddling:
        kept: list[dict[str, Any]] = []
        for row in accepted:
            if row["source_group"] in straddling:
                rejected.append({"line": None, "row_id": row["row_id"],
                                 "reasons": [f"split: source_group {row['source_group']} appears on both sides"]})
            else:
                kept.append(row)
        accepted = kept
    groups_per_split = {
        split: len({row["source_group"] for row in accepted if row["split"] == split}) for split in ("train", "dev")
    }
    rows_per_split = {split: sum(1 for row in accepted if row["split"] == split) for split in ("train", "dev")}

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("accepted.jsonl", accepted), ("quarantine.jsonl", quarantined)):
        with (out_dir / name).open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    report = {
        "rows_path": str(args.rows.resolve()),
        "schema_sha256": hashlib.sha256(args.schema.read_bytes()).hexdigest(),
        "set_a_manifest": manifests["a"],
        "set_b_manifest": manifests["b"],
        "threshold": args.threshold,
        "quarantine_band": args.quarantine_band,
        "shingle_words": SHINGLE_WORDS,
        "n_accepted": len(accepted),
        "n_quarantined": len(quarantined),
        "n_rejected": len(rejected),
        "rows_per_split": rows_per_split,
        "unique_source_groups_per_split": groups_per_split,
        "straddling_source_groups": straddling,
        "rejected": rejected,
        "utc_time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    (out_dir / "report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for entry in rejected:
        where = f"line {entry['line']}" if entry["line"] is not None else f"row {entry['row_id']}"
        print(f"rejected {where}: {'; '.join(entry['reasons'])}", file=sys.stderr)
    return 1 if rejected else 0


def main() -> int:
    return validate()


if __name__ == "__main__":
    raise SystemExit(main())
