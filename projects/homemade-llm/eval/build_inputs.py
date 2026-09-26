# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:51 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | BEGIN]]
"""Build model task inputs from reference work packets (M1 eval).

``manifest()`` matches plan D-M1-0; ``generate_candidates.py`` is not on this branch yet,
so the helper lives here rather than being imported.

Usage:
    python projects/homemade-llm/eval/build_inputs.py \\
        --set a --refs path/to/refs --out path/to/inputs [--titles titles.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCORED_FIELDS: tuple[str, ...] = (
    "non_goals",
    "constraints",
    "acceptance_checks",
    "evidence_requirements",
)


def manifest(directory: Path) -> str:
    """SHA-256 manifest for top-level ``*.json`` files in ``directory`` (D-M1-0)."""
    directory = directory.resolve()
    base = directory.name
    lines: list[str] = []
    for name in sorted(p.name for p in directory.iterdir() if p.is_file() and p.suffix == ".json"):
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{base}/{name}\t{digest}\n")
    return "".join(lines)


def _normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    parts: list[str] = []
    for char in folded:
        if char.isalnum():
            parts.append(char)
        elif parts and parts[-1] != " ":
            parts.append(" ")
    return "".join(parts).strip()


def _words(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    return normalized.split()


def _eight_word_shingles(text: str) -> list[str]:
    words = _words(text)
    if len(words) < 8:
        return []
    return [" ".join(words[i : i + 8]) for i in range(len(words) - 7)]


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_flatten_strings(item))
        return collected
    if isinstance(value, dict):
        collected = []
        for item in value.values():
            collected.extend(_flatten_strings(item))
        return collected
    return [] if value is None else [str(value)]


def _scored_field_strings(reference: dict[str, Any]) -> list[str]:
    strings: list[str] = []
    for field in SCORED_FIELDS:
        if field in reference:
            strings.extend(_flatten_strings(reference[field]))
    return strings


def _leak_match(reference: dict[str, Any], objective: str, repository: str, title: str) -> str | None:
    combined = _normalize_text(f"{objective} {repository} {title}")
    if not combined:
        return None
    for scored in _scored_field_strings(reference):
        normalized_scored = _normalize_text(scored)
        if not normalized_scored:
            continue
        scored_words = normalized_scored.split()
        if len(scored_words) >= 4 and normalized_scored in combined:
            return normalized_scored
        for shingle in _eight_word_shingles(scored):
            if shingle in combined:
                return shingle
    return None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference_ids(refs_dir: Path) -> list[str]:
    return sorted(p.stem for p in refs_dir.glob("*.json") if p.is_file())


def _title_from_heading(source_path: Path) -> str | None:
    if not source_path.is_file():
        return None
    for line in source_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title if title else None
    return None


def _resolve_title(
    item_id: str,
    refs_dir: Path,
    set_name: str,
    titles_override: dict[str, str],
) -> str | None:
    if item_id in titles_override:
        title = titles_override[item_id]
        return title.strip() if isinstance(title, str) and title.strip() else None

    meta_path = refs_dir / "metadata" / f"{item_id}.meta.json"
    if not meta_path.is_file():
        return None
    meta = _load_json(meta_path)
    if set_name == "b":
        raw = meta.get("source_title")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    source_file = meta.get("source_file")
    if not isinstance(source_file, str) or not source_file.strip():
        return None
    source_root = refs_dir.parent.resolve()
    source_path = (source_root / source_file).resolve()
    if Path(source_file).is_absolute() or not source_path.is_relative_to(source_root):
        raise ValueError(f"reference {item_id}: source_file escapes refs parent")
    return _title_from_heading(source_path)


def _format_task_input(item_id: str, objective: str, repository: str, title: str) -> str:
    payload = {
        "id": item_id,
        "objective": objective,
        "repository": repository,
        "title": title,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _fail(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def build(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build model task inputs from reference packets.")
    parser.add_argument("--set", choices=("a", "b"), required=True, dest="set_name")
    parser.add_argument("--refs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--titles", type=Path, default=None, help="JSON object mapping id to title.")
    parser.add_argument(
        "--allow-leak",
        action="append",
        default=[],
        dest="allow_leak",
        metavar="ID",
        help="Record accepted leakage for this id (repeatable); owner policy for the M1 primary score is to use --exclude instead.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="ID=REASON",
        help="Drop this reference from inputs and record its non-empty reason (repeatable).",
    )
    args = parser.parse_args(argv)

    refs_dir = args.refs.resolve()
    out_dir = args.out.resolve()
    run_path = out_dir.parent / f"{out_dir.name}.build.json"

    if out_dir.exists() and any(out_dir.iterdir()):
        return _fail(f"refusing non-empty --out: {out_dir}")
    if run_path.exists():
        return _fail(f"refusing existing run record: {run_path}")

    if not refs_dir.is_dir():
        return _fail(f"refs is not a directory: {refs_dir}")

    titles_override: dict[str, str] = {}
    if args.titles is not None:
        raw = _load_json(args.titles.resolve())
        if not isinstance(raw, dict):
            return _fail("--titles must be a JSON object mapping id to title")
        titles_override = {str(k): str(v) for k, v in raw.items()}

    allow_leak_ids = sorted(set(args.allow_leak))
    override_ids = sorted(titles_override.keys())

    item_ids = _reference_ids(refs_dir)
    if not item_ids:
        return _fail(f"no reference packets under {refs_dir}")

    exclusions: dict[str, str] = {}
    for entry in args.exclude:
        item_id, separator, reason = entry.partition("=")
        if not separator or not item_id or not reason.strip():
            return _fail("--exclude requires ID=REASON with a non-empty reason")
        if item_id not in item_ids:
            return _fail(f"unknown --exclude id: {item_id}")
        if item_id in exclusions:
            return _fail(f"duplicate --exclude id: {item_id}")
        exclusions[item_id] = reason
    if len(exclusions) == len(item_ids):
        return _fail("cannot exclude every reference: at least one input is required")

    missing_titles: list[str] = []
    leak_errors: list[tuple[str, str]] = []
    built: dict[str, str] = {}

    for item_id in item_ids:
        if item_id in exclusions:
            continue
        ref_path = refs_dir / f"{item_id}.json"
        reference = _load_json(ref_path)
        objective = reference.get("objective")
        repository = reference.get("repository")
        if not isinstance(objective, str) or not objective:
            return _fail(f"reference {item_id} missing non-empty objective")
        if not isinstance(repository, str) or not repository:
            return _fail(f"reference {item_id} missing non-empty repository")

        try:
            title = _resolve_title(item_id, refs_dir, args.set_name, titles_override)
        except ValueError as exc:
            return _fail(str(exc))
        if not title:
            missing_titles.append(item_id)
            continue

        leak = _leak_match(reference, objective, repository, title)
        if leak is not None and item_id not in allow_leak_ids:
            leak_errors.append((item_id, leak))
            continue

        built[item_id] = _format_task_input(item_id, objective, repository, title)

    if missing_titles:
        return _fail("missing or empty title: " + ", ".join(missing_titles))

    if leak_errors:
        for item_id, shingle in leak_errors:
            print(f"leak {item_id}: {shingle}", file=sys.stderr)
        return 1

    reference_manifest = manifest(refs_dir)

    temp_dir = out_dir.parent / f"{out_dir.name}.tmp-build"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    published = False
    run_temp: Path | None = None
    try:
        for item_id in sorted(built.keys()):
            (temp_dir / f"{item_id}.json").write_text(built[item_id], encoding="utf-8")

        if out_dir.exists():
            out_dir.rmdir()
        temp_dir.rename(out_dir)
        published = True

        run_record = {
            "set": args.set_name,
            "refs": str(refs_dir),
            "out": str(out_dir),
            "reference_manifest": reference_manifest,
            "input_manifest": manifest(out_dir),
            "item_count": len(built),
            "excluded": [{"id": item_id, "reason": exclusions[item_id]} for item_id in sorted(exclusions)],
            "n_references": len(item_ids),
            "n_excluded": len(exclusions),
            "n_inputs": len(built),
            "override_ids": override_ids,
            "allow_leak_ids": allow_leak_ids,
            "utc_time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        with tempfile.NamedTemporaryFile(dir=out_dir.parent, prefix=f"{run_path.name}.", delete=False) as handle:
            run_temp = Path(handle.name)
        run_temp.write_text(json.dumps(run_record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(run_temp, run_path)
    except Exception:
        if published:
            shutil.rmtree(out_dir)
        if run_temp is not None:
            run_temp.unlink(missing_ok=True)
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return 0


def main() -> int:
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:16 UTC | END]]
