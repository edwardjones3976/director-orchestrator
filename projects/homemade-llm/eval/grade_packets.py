"""Build grader packets and merge grader replies (M1). Standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

FIELDS = ("non_goals", "constraints", "acceptance_checks", "evidence_requirements")
RUBRIC = (
    "Accept a field only if a Director could dispatch it unedited. Wording may differ; content may not: "
    "every requirement, prohibition, deadline, artifact binding and evidence obligation in the reference "
    "must be present, and nothing unsupported may be added."
)
RUBRIC_SHA = hashlib.sha256(RUBRIC.encode("utf-8")).hexdigest()
BATCH = 16
LABEL_RE = re.compile(r"^[a-z0-9]{2,16}$")
SET_RE = re.compile(r"^[a-z0-9]{1,8}$")
BANNED_SUBSTR = (
    "a3",
    "oracle",
    "refs",
    "copy",
    "ceiling",
    "regrade",
    "baseline",
    "control",
    "arm",
)
_FENCE = "`" * 3
ROW_KEY_SETS = (frozenset({"id", "field", "accepted"}), frozenset({"id", "field", "accepted", "grader"}))
SUMMARY_KEYS = (
    "expected_keys",
    "distinct_keys",
    "missing",
    "unknown",
    "outside_roster",
    "bad_field",
    "non_bool",
    "malformed",
    "duplicate",
    "conflicting",
    "foreign_label",
    "missing_reply_files",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print("usage error", file=sys.stderr)
        sys.exit(1)


def load_excluded(path: Path | None) -> set[str]:
    if not path:
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("excluded", [])
    out: set[str] = set()
    if not isinstance(rows, list):
        return out
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]:
            out.add(r["id"])
    return out


def load_only(path: Path | None) -> set[str] | None:
    if not path:
        return None
    text = path.read_text(encoding="utf-8")
    return {ln.strip() for ln in text.splitlines() if ln.strip()}


def label_ok(label: str) -> bool:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        return False
    low = label.lower()
    return not any(b in low for b in BANNED_SUBSTR)


def set_ok(name: str) -> bool:
    if not isinstance(name, str) or not SET_RE.fullmatch(name):
        return False
    low = name.lower()
    return not any(b in low for b in BANNED_SUBSTR)


def _parse_json_data(text: str) -> object:
    return json.loads(text, object_pairs_hook=_reject_dup_keys)


def _load_refs(refs_dir: Path) -> dict[str, object]:
    refs: dict[str, object] = {}
    for p in sorted(refs_dir.glob("*.json")):
        refs[p.stem] = _parse_json_data(p.read_text(encoding="utf-8"))
    return refs


def _candidate_field(cand: object, field: str) -> str:
    if isinstance(cand, dict):
        return json.dumps(cand.get(field), ensure_ascii=False)
    return "<missing or invalid candidate>"


def _read_candidate(cand_dir: Path, item_id: str) -> object | None:
    path = cand_dir / f"{item_id}.json"
    if not path.exists():
        return None
    try:
        return _parse_json_data(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _packet_lines(
    label: str,
    set_name: str,
    batch_index: int,
    n_batches: int,
    batch: list[str],
    refs: dict[str, object],
    cand_dir: Path,
) -> list[str]:
    lines = [
        f"GRADING PACKET homemade-llm M1 S1 | run {label} | set {set_name} | batch {batch_index} of {n_batches}",
        "You are one of two independent graders. Judge each candidate field against its reference field only. Do not rewrite anything; do not consult any other source; do not grade the objective.",
        f"RUBRIC (sha256 {RUBRIC_SHA}): {RUBRIC}",
        "OUTPUT: for every item and each of the four fields, exactly one JSON line: "
        '{"id": "<id>", "field": "<field>", "grader": "__GRADER__", "accepted": true|false}. '
        f"{len(batch) * 4} lines total, nothing else before or after them. A field missing from the candidate is accepted:false.",
        "",
    ]
    for item_id in batch:
        ref = refs[item_id]
        ref_obj = ref if isinstance(ref, dict) else {}
        cand = _read_candidate(cand_dir, item_id)
        lines.append(f"=== ITEM {item_id} ===")
        for field in FIELDS:
            lines.append(f"--- field {field}")
            lines.append("REFERENCE: " + json.dumps(ref_obj.get(field), ensure_ascii=False))
            lines.append("CANDIDATE: " + _candidate_field(cand, field))
        lines.append("")
    return lines


def cmd_build(a: argparse.Namespace) -> int:
    if not label_ok(a.label):
        print("invalid label", file=sys.stderr)
        return 1
    if not set_ok(a.set):
        print("invalid set", file=sys.stderr)
        return 1
    size = a.batch if isinstance(a.batch, int) and a.batch > 0 else BATCH
    refs_dir = Path(a.refs)
    cand_dir = Path(a.candidates)
    out = Path(a.out)
    if not refs_dir.is_dir() or not cand_dir.is_dir():
        print("usage error", file=sys.stderr)
        return 1
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print("output exists", file=sys.stderr)
        return 1
    try:
        refs = _load_refs(refs_dir)
        excluded = load_excluded(Path(a.exclude) if a.exclude else None)
        only = load_only(Path(a.only) if a.only else None)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        print("usage error", file=sys.stderr)
        return 1
    ids = [i for i in sorted(refs) if i not in excluded]
    if only is not None:
        ids = [i for i in ids if i in only]
    parent = out.parent if str(out.parent) else Path(".")
    try:
        parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".gpk-", dir=str(parent)))
    except OSError:
        print("usage error", file=sys.stderr)
        return 1
    replaced = False
    try:
        n_batches = (len(ids) + size - 1) // size if ids else 0
        manifest_packets: list[dict] = []
        for b, start in enumerate(range(0, len(ids), size), 1):
            batch = ids[start:start + size]
            lines = _packet_lines(a.label, a.set, b, n_batches, batch, refs, cand_dir)
            name = f"packet-{a.label}-set-{a.set}-b{b:02d}.md"
            dest = tmp / name
            data = "\n".join(lines).encode("utf-8")
            dest.write_bytes(data)
            manifest_packets.append(
                {"packet": name, "items": batch, "sha256": hashlib.sha256(data).hexdigest()}
            )
        man_obj = {
            "label": a.label,
            "set": a.set,
            "rubric_sha256": RUBRIC_SHA,
            "fields": FIELDS,
            "n_items": len(ids),
            "excluded": sorted(excluded),
            "packets": manifest_packets,
        }
        (tmp / f"manifest-{a.label}-set-{a.set}.json").write_bytes(
            json.dumps(man_obj, indent=1).encode("utf-8")
        )
        if out.exists():
            if not out.is_dir() or any(out.iterdir()):
                print("output exists", file=sys.stderr)
                return 1
            out.rmdir()
        try:
            os.rename(str(tmp), str(out))
        except FileExistsError:
            print("output exists", file=sys.stderr)
            return 1
        except PermissionError:
            if out.exists():
                print("output exists", file=sys.stderr)
            else:
                print("usage error", file=sys.stderr)
            return 1
        except OSError:
            print("usage error", file=sys.stderr)
            return 1
        replaced = True
    except OSError:
        print("usage error", file=sys.stderr)
        return 1
    finally:
        if not replaced and tmp.exists():
            try:
                import shutil

                shutil.rmtree(tmp, ignore_errors=True)
            except OSError:
                pass
    if not replaced:
        return 1
    print(f"{len(ids)} items {len(manifest_packets)} packets")
    return 0


def _empty_summary() -> dict[str, int]:
    return {k: 0 for k in SUMMARY_KEYS}


def _reject_dup_keys(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate property")
        out[k] = v
    return out


def _is_scalar(v: object) -> bool:
    return v is None or isinstance(v, (str, bool, int, float))


def _reply_content_lines(text: str) -> tuple[list[str], int]:
    lines = text.splitlines()
    nonempty = [(i, ln) for i, ln in enumerate(lines) if ln.strip()]
    extra_malformed = 0
    skip: set[int] = set()
    if nonempty:
        first_i, first_ln = nonempty[0]
        last_i, last_ln = nonempty[-1]
        first_s = first_ln.strip()
        last_s = last_ln.strip()
        open_fence = first_s == _FENCE or first_s.lower() == _FENCE + "json"
        close_fence = last_s == _FENCE
        if open_fence and close_fence and first_i != last_i:
            skip.add(first_i)
            skip.add(last_i)
        elif open_fence or close_fence:
            extra_malformed = 1
            if open_fence:
                skip.add(first_i)
            if close_fence:
                skip.add(last_i)
    content = [ln for i, ln in enumerate(lines) if i not in skip and ln.strip()]
    return content, extra_malformed


def _print_summary(summary: dict[str, int]) -> None:
    print(json.dumps({k: int(summary[k]) for k in SUMMARY_KEYS}))


def _publish_no_clobber(tmp_name: str, dest: str) -> None:
    os.link(tmp_name, dest)


# Grader identity is the caller-asserted --grader argument; independence must come from the route and receipt, not from row labels.
def cmd_merge(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists():
        print("output exists", file=sys.stderr)
        return 1
    man_path = Path(a.manifest)
    replies_dir = Path(a.replies)
    if not man_path.is_file() or not replies_dir.is_dir():
        print("usage error", file=sys.stderr)
        return 1
    grader = a.grader
    if not isinstance(grader, str) or not grader:
        print("usage error", file=sys.stderr)
        return 1
    allow = set(a.allow_label or [])
    allow.add("__GRADER__")
    allow.add(grader)
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("usage error", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict) or not isinstance(manifest.get("packets"), list):
        print("usage error", file=sys.stderr)
        return 1
    packets = manifest["packets"]
    all_ids: set[str] = set()
    roster: dict[str, str] = {}
    expected_pairs: list[tuple[str, set[str]]] = []
    for pkt in packets:
        if not isinstance(pkt, dict) or not isinstance(pkt.get("packet"), str) or not isinstance(pkt.get("items"), list):
            print("usage error", file=sys.stderr)
            return 1
        items = [i for i in pkt["items"] if isinstance(i, str) and i]
        name = pkt["packet"]
        for i in items:
            all_ids.add(i)
            roster[i] = name
        expected_pairs.append((name, set(items)))
    summary = _empty_summary()
    summary["expected_keys"] = sum(len(items) * len(FIELDS) for _, items in expected_pairs)
    distinct: set[tuple[str, str]] = set()
    valid: dict[tuple[str, str], bool] = {}
    seen_acc: dict[tuple[str, str], object] = {}
    violations = False

    for pkt_name, packet_ids in expected_pairs:
        reply_path = replies_dir / (Path(pkt_name).stem + ".txt")
        if not reply_path.is_file():
            summary["missing_reply_files"] += 1
            violations = True
            summary["missing"] += len(packet_ids) * len(FIELDS)
            continue
        try:
            text = reply_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            summary["missing_reply_files"] += 1
            violations = True
            summary["missing"] += len(packet_ids) * len(FIELDS)
            continue
        covered: set[tuple[str, str]] = set()
        content_lines, fence_malformed = _reply_content_lines(text)
        if fence_malformed:
            summary["malformed"] += fence_malformed
            violations = True
        for line in content_lines:
            try:
                obj = json.loads(line, object_pairs_hook=_reject_dup_keys)
            except (json.JSONDecodeError, ValueError, TypeError):
                summary["malformed"] += 1
                violations = True
                continue
            if not isinstance(obj, dict) or frozenset(obj.keys()) not in ROW_KEY_SETS:
                summary["malformed"] += 1
                violations = True
                continue
            if any(not _is_scalar(v) for v in obj.values()):
                summary["malformed"] += 1
                violations = True
                continue
            iid = obj.get("id")
            field = obj.get("field")
            acc = obj.get("accepted")
            if not isinstance(iid, str) or not iid or not isinstance(field, str):
                summary["malformed"] += 1
                violations = True
                continue
            key = (iid, field)
            distinct.add(key)
            if key in seen_acc:
                prev = seen_acc[key]
                if isinstance(prev, bool) and isinstance(acc, bool) and prev != acc:
                    summary["conflicting"] += 1
                else:
                    summary["duplicate"] += 1
                violations = True
            else:
                seen_acc[key] = acc
            if field not in FIELDS:
                summary["bad_field"] += 1
                violations = True
            if not isinstance(acc, bool):
                summary["non_bool"] += 1
                violations = True
            grader_ok = True
            if "grader" in obj:
                gval = obj["grader"]
                if not isinstance(gval, str) or gval not in allow:
                    summary["foreign_label"] += 1
                    grader_ok = False
                    violations = True
            if iid not in all_ids:
                summary["unknown"] += 1
                violations = True
            elif iid not in packet_ids:
                summary["outside_roster"] += 1
                violations = True
            row_ok = (
                field in FIELDS
                and isinstance(acc, bool)
                and grader_ok
                and iid in packet_ids
            )
            if row_ok:
                covered.add(key)
                valid[key] = acc
        expected = {(i, f) for i in packet_ids for f in FIELDS}
        miss = sum(1 for k in expected if k not in covered)
        if miss:
            summary["missing"] += miss
            violations = True

    summary["distinct_keys"] = len(distinct)
    if summary["unknown"] or summary["outside_roster"] or summary["bad_field"] or summary["non_bool"]:
        violations = True
    if summary["malformed"] or summary["duplicate"] or summary["conflicting"]:
        violations = True
    if summary["foreign_label"] or summary["missing_reply_files"] or summary["missing"]:
        violations = True

    if violations:
        _print_summary(summary)
        return 2

    rows = [
        (iid, field, valid[(iid, field)])
        for iid, field in sorted(valid.keys())
    ]
    parent = out.parent if str(out.parent) else Path(".")
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".merge-", suffix=".tmp", dir=str(parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            for iid, field, acc in rows:
                fh.write(
                    json.dumps(
                        {"id": iid, "field": field, "grader": grader, "accepted": acc},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            _publish_no_clobber(tmp_name, str(out))
        except FileExistsError:
            print("output exists", file=sys.stderr)
            return 1
        except OSError:
            print("usage error", file=sys.stderr)
            return 1
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        tmp_name = None
    except OSError:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            tmp_name = None
        print("usage error", file=sys.stderr)
        return 1
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    _print_summary(summary)
    return 0


def make_parser() -> _Parser:
    ap = _Parser(add_help=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--refs", required=True)
    b.add_argument("--candidates", required=True)
    b.add_argument("--exclude")
    b.add_argument("--label", required=True)
    b.add_argument("--set", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--batch", type=int, default=BATCH)
    b.add_argument("--only")
    m = sub.add_parser("merge")
    m.add_argument("--manifest", required=True)
    m.add_argument("--replies", required=True)
    m.add_argument("--grader", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--allow-label", action="append", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = make_parser()
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "merge":
        return cmd_merge(a)
    print("usage error", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
