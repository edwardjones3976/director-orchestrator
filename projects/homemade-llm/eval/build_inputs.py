# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:51 UTC | BEGIN]]
# [[Cursor | composer-2.5 | 2026-09-26 | 21:45 UTC | BEGIN]]
"""Build model task inputs from reference work packets (M1 eval).

``manifest()`` returns the SHA-256 of the sorted manifest lines (plan D-M1-0).

Usage:
    python projects/homemade-llm/eval/build_inputs.py \\
        --set a --refs path/to/refs --out path/to/inputs [--titles titles.json] \\
        [--context path/to/context]

Input contracts. Without ``--context`` an input carries ``id``, ``objective``,
``repository`` and ``title`` (D-M1-1, the historical M1 runs). With ``--context``
(D-M2-10, packet M2-C1) every input also carries ``context``: the items read from
``<context>/<id>.context.json``, a JSON array of provenance-bound excerpts with
``kind``, ``text``, ``source_ref`` and ``source_sha256``, in the shape of
``input.context`` in ``schemas/director-data-v1-row.schema.json``. A context
item is refused, and no input is written, unless its citation resolves to
matching source bytes containing the excerpt. The item's own frozen assignment
source is allowed; another held-out item's source and scored target text are not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
import prompt_template  # noqa: E402

SCORED_FIELDS: tuple[str, ...] = (
    "non_goals",
    "constraints",
    "acceptance_checks",
    "evidence_requirements",
)

PULLS_SOURCE_REF = re.compile(r"^pulls/\d+(?:/|$)")


def manifest(directory: Path) -> str:
    """SHA-256 manifest for top-level ``*.json`` files in ``directory`` (D-M1-0)."""
    directory = directory.resolve()
    base = directory.name
    lines: list[str] = []
    for name in sorted(p.name for p in directory.iterdir() if p.is_file() and p.suffix == ".json"):
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{base}/{name}\t{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


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
    return _text_leak_match(reference, f"{objective} {repository} {title}")


def _text_leak_match(reference: dict[str, Any], text: str) -> str | None:
    """A scored-field string or eight-word shingle of ``reference`` found in ``text``, or None."""
    combined = _normalize_text(text)
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


def _format_task_input(
    item_id: str,
    objective: str,
    repository: str,
    title: str,
    context: list[dict[str, str]] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": item_id,
        "objective": objective,
        "repository": repository,
        "title": title,
    }
    if context is not None:
        payload["context"] = [
            {key: item[key] for key in sorted(prompt_template.CONTEXT_ITEM_FIELDS)} for item in context
        ]
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def lineage_keys(refs_dir: Path, item_ids: list[str]) -> set[str]:
    """Normalized identities a context source_ref must not resolve to (D-M2-3 check 1).

    Every reference id, plus each reference's recorded source file and source
    title from ``metadata/<id>.meta.json`` when present.
    """
    keys: set[str] = set()
    for item_id in item_ids:
        keys.add(_normalize_text(item_id))
        meta_path = refs_dir / "metadata" / f"{item_id}.meta.json"
        if not meta_path.is_file():
            continue
        meta = _load_json(meta_path)
        if not isinstance(meta, dict):
            continue
        for field in ("source_file", "source_title"):
            value = meta.get(field)
            if isinstance(value, str) and value.strip():
                keys.add(_normalize_text(value))
                keys.add(_normalize_text(Path(value).name))
                keys.add(_normalize_text(Path(value).stem))
    keys.discard("")
    return keys


def _source_ref_forms(source_ref: str) -> set[str]:
    """Normalized forms of a source_ref that lineage keys are compared against."""
    forms = {_normalize_text(source_ref)}
    tail = source_ref.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    forms.add(_normalize_text(tail))
    stem = tail.split(".", 1)[0]
    forms.add(_normalize_text(stem))
    forms.discard("")
    return forms


def _forbidden_assignment_source(source_ref: str, source_path: Path, source_root: Path) -> str | None:
    """Reason a source_ref may not be cited as task-time assignment context, or None."""
    normalized = source_ref.replace("\\", "/")
    if PULLS_SOURCE_REF.match(normalized):
        return "source_ref points at a completed pull request, not task-time assignment"
    if source_path == (source_root / "raw" / "pr-bodies.jsonl").resolve():
        return "PR body archive lacks assignment-time provenance"
    return None


def source_digests(refs_dir: Path, item_ids: list[str]) -> dict[str, set[str]]:
    """Map recorded held-out source hashes to the reference ids they belong to."""
    owners: dict[str, set[str]] = {}
    for item_id in item_ids:
        meta_path = refs_dir / "metadata" / f"{item_id}.meta.json"
        if meta_path.is_file():
            meta = _load_json(meta_path)
            if isinstance(meta, dict):
                digest = meta.get("source_sha256")
                if isinstance(digest, str):
                    owners.setdefault(digest, set()).add(item_id)
    return owners


def validate_context(
    context: Any,
    references: dict[str, dict[str, Any]],
    lineage_keys: set[str],
    refs_dir: Path,
    item_id: str,
    held_out_digests: dict[str, set[str]],
    *,
    source_root: Path | None = None,
) -> list[str]:
    """Every reason a context array may not be written, as messages; empty means accepted.

    Shape follows ``input.context`` of the row schema. Only the item's own
    frozen assignment source may be cited from held-out lineage. Every citation
    must resolve to bytes with the stated hash and contain its excerpt.
    """
    errors = list(prompt_template.context_shape_errors(context))
    if errors:
        return errors
    source_root = (source_root or refs_dir.parent).resolve()
    meta_path = refs_dir / "metadata" / f"{item_id}.meta.json"
    meta = _load_json(meta_path) if meta_path.is_file() else {}
    if not isinstance(meta, dict):
        meta = {}
    for index, item in enumerate(context):
        where = f"context[{index}]"
        source_ref = item["source_ref"]
        own_source = (item["kind"] == "assignment_packet"
                      and source_ref == meta.get("source_file"))
        for ref_id in sorted(references):
            if own_source and ref_id == item_id:
                continue
            leak = _text_leak_match(references[ref_id], item["text"])
            if leak is not None:
                errors.append(f"{where}: text overlaps scored field of {ref_id}: {leak}")
                break
        if _source_ref_forms(source_ref) & lineage_keys and not own_source:
            errors.append(f"{where}: source_ref resolves to a held-out item")
        relative = Path(source_ref)
        if (relative.is_absolute() or relative.drive or "\\" in source_ref
                or any(part in (".", "..") for part in relative.parts)):
            errors.append(f"{where}: source_ref must be a relative source path")
        else:
            source_path = (source_root / relative).resolve()
            if not source_path.is_relative_to(source_root) or not source_path.is_file():
                errors.append(f"{where}: source_ref does not resolve to a source file")
            elif source_path.is_symlink() and not source_path.resolve().is_relative_to(source_root):
                errors.append(f"{where}: source_ref symlink escapes the source root")
            else:
                forbidden = _forbidden_assignment_source(source_ref, source_path, source_root)
                if forbidden:
                    errors.append(f"{where}: {forbidden}")
                    continue
                raw = source_path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if digest != item["source_sha256"]:
                    errors.append(f"{where}: source_sha256 does not match source bytes")
                if own_source and digest != meta.get("source_sha256"):
                    errors.append(f"{where}: source_sha256 does not match reference metadata")
                if held_out_digests.get(digest) and not own_source:
                    errors.append(f"{where}: source bytes belong to a held-out item")
                try:
                    source_text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    errors.append(f"{where}: source file is not UTF-8 text")
                else:
                    if " ".join(item["text"].split()) not in " ".join(source_text.split()):
                        errors.append(f"{where}: text is not an excerpt of source_ref")
    if meta.get("source_pr") and not meta.get("source_file"):
        errors.append("context has no verified assignment-time source for this PR")
    return errors


def _load_context_file(context_dir: Path, item_id: str) -> Any:
    path = context_dir / f"{item_id}.context.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing context file: {path}")
    return _load_json(path)


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
        "--context",
        type=Path,
        default=None,
        help="Directory of <id>.context.json arrays (D-M2-10). Every built input must have one.",
    )
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
    context_dir = args.context.resolve() if args.context is not None else None
    if context_dir is not None and not context_dir.is_dir():
        return _fail(f"context is not a directory: {context_dir}")

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
    context_errors: list[tuple[str, str]] = []
    built: dict[str, str] = {}

    references: dict[str, dict[str, Any]] = {}
    for item_id in item_ids:
        reference = _load_json(refs_dir / f"{item_id}.json")
        if not isinstance(reference, dict):
            return _fail(f"reference {item_id} is not a JSON object")
        references[item_id] = reference
    held_out_keys = lineage_keys(refs_dir, item_ids) if context_dir is not None else set()
    held_out_digests = source_digests(refs_dir, item_ids) if context_dir is not None else {}

    for item_id in item_ids:
        if item_id in exclusions:
            continue
        reference = references[item_id]
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

        context: list[dict[str, str]] | None = None
        if context_dir is not None:
            try:
                context = _load_context_file(context_dir, item_id)
            except FileNotFoundError as exc:
                context_errors.append((item_id, str(exc)))
                continue
            problems = validate_context(context, references, held_out_keys, refs_dir,
                                        item_id, held_out_digests)
            if problems:
                context_errors.extend((item_id, problem) for problem in problems)
                continue

        built[item_id] = _format_task_input(item_id, objective, repository, title, context)

    if missing_titles:
        return _fail("missing or empty title: " + ", ".join(missing_titles))

    if leak_errors:
        for item_id, shingle in leak_errors:
            print(f"leak {item_id}: {shingle}", file=sys.stderr)
        return 1

    if context_errors:
        for item_id, problem in context_errors:
            print(f"context {item_id}: {problem}", file=sys.stderr)
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
            "input_contract": (
                prompt_template.INPUT_CONTRACT_CONTEXT if context_dir is not None
                else prompt_template.INPUT_CONTRACT_LEGACY
            ),
            "context_dir": str(context_dir) if context_dir is not None else None,
            "context_manifest": manifest(context_dir) if context_dir is not None else None,
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
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:31 UTC | END]]
