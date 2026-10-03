"""Free item-level scorer. Aggregate counts only; never emit item text or ids."""

import argparse
import difflib
import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

FIELDS = ("non_goals", "constraints", "acceptance_checks", "evidence_requirements")
PER_FIELD_KEYS = (
    "reference_items",
    "candidate_items",
    "matched",
    "misrouted",
    "omitted",
    "correct",
    "wrong_field",
    "duplicate",
    "spurious",
    "near_right_field",
    "precision",
    "recall",
)


class UsageError(Exception):
    pass


def norm(s):
    s = unicodedata.normalize("NFKC", s).casefold()
    out = []
    prev_sep = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_sep = False
        else:
            if out and not prev_sep:
                out.append(" ")
                prev_sep = True
    return "".join(out).strip()


def _ratio(n, d):
    if d == 0:
        return None
    return n / d


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _empty_field_counts():
    return {
        "reference_items": 0,
        "candidate_items": 0,
        "matched": 0,
        "misrouted": 0,
        "omitted": 0,
        "correct": 0,
        "wrong_field": 0,
        "duplicate": 0,
        "spurious": 0,
        "near_right_field": 0,
        "precision": None,
        "recall": None,
    }


def _reject_dup_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate property")
        out[k] = v
    return out


def _load_json_data(path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_dup_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _collapse_ws(text):
    return " ".join(text.split())


def _parse_reference_fields(obj):
    if not isinstance(obj, dict):
        return None
    parsed = {}
    for field in FIELDS:
        if field not in obj:
            return None
        value = obj[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return None
        parsed[field] = value
    return parsed


def _parse_candidate_fields(obj):
    if not isinstance(obj, dict):
        return None
    parsed = {}
    for field in FIELDS:
        if field not in obj:
            parsed[field] = []
            continue
        value = obj[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return None
        parsed[field] = value
    return parsed


def _load_excluded(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        raise UsageError from None
    if data is None:
        raise UsageError
    rows = data
    if isinstance(data, dict) and "excluded" in data:
        rows = data["excluded"]
    if not isinstance(rows, list):
        raise UsageError
    excluded = set()
    for row in rows:
        if isinstance(row, dict) and "id" in row:
            excluded.add(str(row["id"]))
        else:
            raise UsageError
    return excluded


def _context_for_scoring(path):
    if not path.is_file():
        return False, ""
    data = _load_json_data(path)
    if not isinstance(data, dict):
        return False, ""
    if "context" not in data:
        return False, ""
    blocks = data["context"]
    if not isinstance(blocks, list):
        return False, ""
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            return False, ""
        if "text" not in block:
            return False, ""
        text = block["text"]
        if not isinstance(text, str):
            return False, ""
        parts.append(text)
    return True, [_collapse_ws(t) for t in parts]


def score_packet(ref_fields, cand_fields, context_blocks, context_available=True):
    ref_n = {f: [norm(x) for x in ref_fields[f]] for f in FIELDS}
    cand_n = {f: [norm(x) for x in cand_fields[f]] for f in FIELDS}
    ref_status = {f: [None] * len(ref_n[f]) for f in FIELDS}
    cand_status = {f: [None] * len(cand_n[f]) for f in FIELDS}
    has_empty = any(not x for f in FIELDS for x in ref_n[f]) or any(
        not x for f in FIELDS for x in cand_n[f]
    )

    for field in FIELDS:
        claimed = [False] * len(ref_n[field])
        for i, item in enumerate(cand_n[field]):
            if not item:
                continue
            for j, ref_item in enumerate(ref_n[field]):
                if not ref_item:
                    continue
                if not claimed[j] and item == ref_item:
                    claimed[j] = True
                    cand_status[field][i] = "correct"
                    ref_status[field][j] = "matched"
                    break

    for field in FIELDS:
        for i, item in enumerate(cand_n[field]):
            if cand_status[field][i] is not None:
                continue
            if not item:
                continue
            if any(cand_n[field][k] == item for k in range(i)):
                cand_status[field][i] = "duplicate"

    for field in FIELDS:
        for i, item in enumerate(cand_n[field]):
            if cand_status[field][i] is not None:
                continue
            if not item:
                continue
            taken = False
            for other in FIELDS:
                if other == field:
                    continue
                for j, ref_item in enumerate(ref_n[other]):
                    if not ref_item:
                        continue
                    if ref_status[other][j] is None and ref_item == item:
                        ref_status[other][j] = "misrouted"
                        cand_status[field][i] = "wrong_field"
                        taken = True
                        break
                if taken:
                    break

    for field in FIELDS:
        for i in range(len(cand_n[field])):
            if cand_status[field][i] is None:
                cand_status[field][i] = "spurious"
        for j in range(len(ref_n[field])):
            if ref_status[field][j] is None:
                ref_status[field][j] = "omitted"

    per_field = {}
    spurious_in_context = 0
    spurious_not_in_context = 0
    spurious_context_unknown = 0
    for field in FIELDS:
        counts = _empty_field_counts()
        counts["reference_items"] = len(ref_n[field])
        counts["candidate_items"] = len(cand_n[field])
        for status in ref_status[field]:
            counts[status] += 1
        for status in cand_status[field]:
            counts[status] += 1
        spurious_items = [
            cand_n[field][i]
            for i, status in enumerate(cand_status[field])
            if status == "spurious"
        ]
        nonempty_spurious = [extra for extra in spurious_items if extra]
        near = 0
        for j, ref_item in enumerate(ref_n[field]):
            if ref_status[field][j] != "omitted":
                continue
            if not ref_item:
                continue
            if any(
                difflib.SequenceMatcher(None, ref_item, extra, autojunk=False).ratio()
                >= 0.90
                for extra in nonempty_spurious
            ):
                near += 1
        counts["near_right_field"] = near
        per_field[field] = counts
        if context_blocks is not None:
            if context_available:
                for i, status in enumerate(cand_status[field]):
                    if status != "spurious":
                        continue
                    collapsed = _collapse_ws(cand_fields[field][i])
                    if not collapsed:
                        spurious_not_in_context += 1
                        continue
                    if any(
                        collapsed in block
                        for block in context_blocks
                        if block
                    ):
                        spurious_in_context += 1
                    else:
                        spurious_not_in_context += 1
            else:
                spurious_context_unknown += len(spurious_items)

    form_fields = {}
    for field in FIELDS:
        for item in cand_n[field]:
            bucket = form_fields.get(item)
            if bucket is None:
                bucket = set()
                form_fields[item] = bucket
            bucket.add(field)
    repeated = 0
    for field in FIELDS:
        for item in cand_n[field]:
            if len(form_fields[item]) >= 2:
                repeated += 1

    omitted = sum(per_field[f]["omitted"] for f in FIELDS)
    misrouted = sum(per_field[f]["misrouted"] for f in FIELDS)
    wrong_field = sum(per_field[f]["wrong_field"] for f in FIELDS)
    duplicate = sum(per_field[f]["duplicate"] for f in FIELDS)
    spurious = sum(per_field[f]["spurious"] for f in FIELDS)
    matched = sum(per_field[f]["matched"] for f in FIELDS)
    ref_items = sum(per_field[f]["reference_items"] for f in FIELDS)
    cand_items = sum(per_field[f]["candidate_items"] for f in FIELDS)
    return {
        "per_field": per_field,
        "reference_items": ref_items,
        "candidate_items": cand_items,
        "matched": matched,
        "correct": sum(per_field[f]["correct"] for f in FIELDS),
        "spurious_in_context": spurious_in_context,
        "spurious_not_in_context": spurious_not_in_context,
        "spurious_context_unknown": spurious_context_unknown,
        "repeated_across_fields": repeated,
        "zero_error": omitted == 0
        and misrouted == 0
        and wrong_field == 0
        and duplicate == 0
        and spurious == 0
        and not has_empty,
        "zero_omission": omitted == 0 and misrouted == 0 and not has_empty,
        "packet_recall": _ratio(matched, ref_items) if ref_items else None,
    }


def _atomic_write(path, text):
    directory = path.parent
    if str(directory) == "":
        directory = Path(".")
    fd, tmp_name = tempfile.mkstemp(prefix=".item_score_", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.link(tmp_name, str(path))
        os.unlink(tmp_name)
        tmp_name = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refs", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--exclude", default=None)
    parser.add_argument("--inputs", default=None)

    def _fail(_message=None):
        raise UsageError

    parser.error = _fail
    try:
        return parser.parse_args(argv)
    except SystemExit:
        raise UsageError from None


def build_report(totals, field_totals, packet_recalls, use_inputs):
    unreadable = totals["packets_unreadable_reference"] > 0
    per_field = {}
    for field in FIELDS:
        counts = field_totals[field]
        counts["precision"] = _ratio(counts["correct"], counts["candidate_items"])
        if unreadable:
            counts["recall"] = None
        else:
            counts["recall"] = _ratio(counts["matched"], counts["reference_items"])
        per_field[field] = {key: counts[key] for key in PER_FIELD_KEYS}
    if packet_recalls:
        packet_recall = {
            "median": _median(packet_recalls),
            "min": min(packet_recalls),
            "max": max(packet_recalls),
        }
    else:
        packet_recall = None
    if (
        totals["packets_missing_candidate"] == 0
        and totals["packets_malformed_candidate"] == 0
        and totals["packets_unreadable_reference"] == 0
        and totals["packets_context_unavailable"] == 0
    ):
        status = "complete"
    else:
        status = "incomplete"
    if unreadable:
        overall_recall = None
    else:
        overall_recall = _ratio(totals["matched"], totals["reference_items"])
    report = {
        "packets_scored": totals["packets_scored"],
        "packets_missing_candidate": totals["packets_missing_candidate"],
        "packets_malformed_candidate": totals["packets_malformed_candidate"],
        "packets_unreadable_reference": totals["packets_unreadable_reference"],
        "status": status,
        "packets_roster": totals["packets_roster"],
        "reference_items": totals["reference_items"],
        "reference_items_scored": totals["reference_items_scored"],
        "candidate_items": totals["candidate_items"],
        "per_field": per_field,
        "repeated_across_fields": totals["repeated_across_fields"],
        "zero_error_packets": totals["zero_error_packets"],
        "zero_omission_packets": totals["zero_omission_packets"],
        "packet_recall": packet_recall,
        "overall_recall": overall_recall,
        "overall_recall_valid_candidates_only": _ratio(
            totals["matched"], totals["reference_items_scored"]
        ),
        "overall_precision": _ratio(totals["correct"], totals["candidate_items"]),
    }
    if use_inputs:
        report["spurious_in_context"] = totals["spurious_in_context"]
        report["spurious_not_in_context"] = totals["spurious_not_in_context"]
        report["packets_context_unavailable"] = totals["packets_context_unavailable"]
        report["spurious_context_unknown"] = totals["spurious_context_unknown"]
        ordered = {}
        for key, value in report.items():
            ordered[key] = value
            if key == "per_field":
                ordered["spurious_in_context"] = report["spurious_in_context"]
                ordered["spurious_not_in_context"] = report["spurious_not_in_context"]
                ordered["packets_context_unavailable"] = report[
                    "packets_context_unavailable"
                ]
                ordered["spurious_context_unknown"] = report["spurious_context_unknown"]
        return ordered
    return report


def run(argv):
    args = _parse_args(argv)
    refs_dir = Path(args.refs)
    cands_dir = Path(args.candidates)
    out_path = Path(args.out)
    if not refs_dir.is_dir() or not cands_dir.is_dir():
        raise UsageError
    if args.inputs is not None and not Path(args.inputs).is_dir():
        raise UsageError
    if args.exclude is not None and not Path(args.exclude).is_file():
        raise UsageError
    if out_path.exists():
        raise UsageError
    parent = out_path.parent
    if str(parent) == "":
        parent = Path(".")
    if not parent.exists() or not parent.is_dir():
        raise UsageError

    excluded = set()
    if args.exclude is not None:
        excluded = _load_excluded(Path(args.exclude))

    ids = sorted(
        path.stem
        for path in refs_dir.glob("*.json")
        if path.is_file() and path.stem not in excluded
    )

    totals = {
        "packets_scored": 0,
        "packets_missing_candidate": 0,
        "packets_malformed_candidate": 0,
        "packets_unreadable_reference": 0,
        "packets_roster": 0,
        "reference_items": 0,
        "reference_items_scored": 0,
        "candidate_items": 0,
        "matched": 0,
        "correct": 0,
        "repeated_across_fields": 0,
        "zero_error_packets": 0,
        "zero_omission_packets": 0,
        "spurious_in_context": 0,
        "spurious_not_in_context": 0,
        "packets_context_unavailable": 0,
        "spurious_context_unknown": 0,
    }
    field_totals = {field: _empty_field_counts() for field in FIELDS}
    packet_recalls = []
    use_inputs = args.inputs is not None
    inputs_dir = Path(args.inputs) if use_inputs else None
    totals["packets_roster"] = len(ids)

    for item_id in ids:
        ref_obj = _load_json_data(refs_dir / f"{item_id}.json")
        ref_fields = (
            _parse_reference_fields(ref_obj) if ref_obj is not None else None
        )
        if ref_fields is None:
            totals["packets_unreadable_reference"] += 1
            continue
        cand_path = cands_dir / f"{item_id}.json"
        if not cand_path.is_file():
            totals["packets_missing_candidate"] += 1
            result = score_packet(ref_fields, {f: [] for f in FIELDS}, None)
            totals["reference_items"] += result["reference_items"]
            for field in FIELDS:
                dest = field_totals[field]
                src = result["per_field"][field]
                dest["reference_items"] += src["reference_items"]
                dest["omitted"] += src["omitted"]
                dest["misrouted"] += src["misrouted"]
                dest["matched"] += src["matched"]
            if result["reference_items"]:
                packet_recalls.append(0.0)
            continue
        cand_obj = _load_json_data(cand_path)
        cand_fields = (
            _parse_candidate_fields(cand_obj) if cand_obj is not None else None
        )
        if cand_fields is None:
            totals["packets_malformed_candidate"] += 1
            result = score_packet(ref_fields, {f: [] for f in FIELDS}, None)
            totals["reference_items"] += result["reference_items"]
            for field in FIELDS:
                dest = field_totals[field]
                src = result["per_field"][field]
                dest["reference_items"] += src["reference_items"]
                dest["omitted"] += src["omitted"]
                dest["misrouted"] += src["misrouted"]
                dest["matched"] += src["matched"]
            if result["reference_items"]:
                packet_recalls.append(0.0)
            continue
        ctx = None
        ctx_available = True
        if use_inputs:
            ctx_path = inputs_dir / f"{item_id}.json"
            ctx_available, ctx_blocks = _context_for_scoring(ctx_path)
            ctx = ctx_blocks
            if not ctx_available:
                totals["packets_context_unavailable"] += 1
        result = score_packet(
            ref_fields,
            cand_fields,
            ctx,
            context_available=ctx_available if use_inputs else True,
        )
        totals["packets_scored"] += 1
        totals["reference_items"] += result["reference_items"]
        totals["reference_items_scored"] += result["reference_items"]
        totals["candidate_items"] += result["candidate_items"]
        totals["matched"] += result["matched"]
        totals["correct"] += result["correct"]
        totals["repeated_across_fields"] += result["repeated_across_fields"]
        totals["zero_error_packets"] += int(result["zero_error"])
        totals["zero_omission_packets"] += int(result["zero_omission"])
        totals["spurious_in_context"] += result["spurious_in_context"]
        totals["spurious_not_in_context"] += result["spurious_not_in_context"]
        totals["spurious_context_unknown"] += result["spurious_context_unknown"]
        if result["packet_recall"] is not None:
            packet_recalls.append(result["packet_recall"])
        for field in FIELDS:
            dest = field_totals[field]
            src = result["per_field"][field]
            for key in dest:
                if key in ("precision", "recall"):
                    continue
                dest[key] += src[key]

    report = build_report(totals, field_totals, packet_recalls, use_inputs)
    payload = json.dumps(report, indent=2) + "\n"
    try:
        _atomic_write(out_path, payload)
    except OSError:
        raise UsageError from None
    sys.stdout.write(payload)
    if totals["packets_scored"] == 0:
        return 2
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        return run(argv)
    except UsageError:
        sys.stderr.write("usage error\n")
        return 1
    except Exception:
        sys.stderr.write("usage error\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
