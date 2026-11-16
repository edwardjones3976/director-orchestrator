"""Compare release artifacts with diffoscope and optional REA evidence."""

import argparse
import hashlib
import html
import json
import os
import subprocess
import time
from pathlib import Path, PureWindowsPath


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, directory, name, accepted=(0,), timeout=120):
    started = time.perf_counter()
    stdout = directory / (name + ".json")
    stderr = directory / (name + ".stderr")
    with stdout.open("wb") as output, stderr.open("wb") as errors:
        process = subprocess.run(command, stdout=output, stderr=errors, timeout=timeout)
    record = {
        "command": command,
        "exit_code": process.returncode,
        "elapsed_seconds": time.perf_counter() - started,
        "stdout": stdout.name,
        "stdout_sha256": digest(stdout),
        "stderr": stderr.name,
        "stderr_sha256": digest(stderr),
    }
    (directory / (name + ".receipt.json")).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8",
    )
    if process.returncode not in accepted:
        raise RuntimeError(f"{command[0]} exited {process.returncode}. See {stderr}")
    return record


def _is_link_or_junction(path):
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def _validate_inputs(left, right, left_tree, right_tree, output):
    for label, artifact in (("left", left), ("right", right)):
        if _is_link_or_junction(artifact):
            raise ValueError(f"{label} artifact must not be a symbolic link or junction: {artifact}")
        if not artifact.is_file():
            raise ValueError(f"{label} artifact file does not exist: {artifact}")

    resolved_trees = []
    for label, tree in (("left_tree", left_tree), ("right_tree", right_tree)):
        if _is_link_or_junction(tree):
            raise ValueError(f"{label} directory must not be a symbolic link or junction: {tree}")
        if not tree.is_dir():
            raise ValueError(f"{label} directory does not exist: {tree}")
        for dirpath, dirnames, filenames in os.walk(tree, followlinks=False):
            current_dir = Path(dirpath)
            for name in dirnames + filenames:
                member = current_dir / name
                if _is_link_or_junction(member):
                    raise ValueError(f"Release input contains a symbolic link or junction: {member}")
        resolved_trees.append(tree.resolve())

    if _is_link_or_junction(output) or output.exists():
        raise ValueError(f"Output path already exists: {output}")

    resolved_output = output.resolve()
    for tree_resolved in resolved_trees:
        if resolved_output == tree_resolved or resolved_output.is_relative_to(tree_resolved):
            raise ValueError(f"Output directory must not be inside an input release tree: {output}")


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if _is_link_or_junction(path):
            raise ValueError("Release input contains a symbolic link or junction")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(path)
    return files


def _validate_relative_path(raw_path, label="source path"):
    """Validate a relative POSIX path against traversal, absolute, and Windows-drive forms."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{label} must be a non-empty relative path")
    if "\x00" in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise ValueError(f"Unsafe or non-relative {label}: {raw_path!r}")
    if PureWindowsPath(raw_path).is_absolute() or (len(raw_path) >= 2 and raw_path[1] == ":"):
        raise ValueError(f"Absolute {label} is not allowed: {raw_path!r}")
    parts = raw_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Invalid path segment in {label}: {raw_path!r}")
    return raw_path


def _is_file_list_item(item):
    return item.get("source1") == "file list" and item.get("source2") == "file list"


def _collect_file_list_paths(item, parent_rel, roots, paths):
    """Translate diffoscope file-list additions/removals into regular-file leaves."""
    if parent_rel is None:
        raw_parent = item.get("parent")
        if raw_parent:
            parent_rel = _validate_relative_path(raw_parent.rstrip("/"), label="diffoscope parent path")
        else:
            parent_rel = ""
    else:
        parent_rel = parent_rel.rstrip("/")
        if parent_rel:
            _validate_relative_path(parent_rel, label="diffoscope parent path")

    parent_parts = parent_rel.split("/") if parent_rel else []
    for line in item.get("unified_diff", "").splitlines():
        if not line or line.startswith(("@@ ", "--- ", "+++ ", "\\")):
            continue
        if line.startswith("+"):
            side_idx = 1
            entry = line[1:]
        elif line.startswith("-"):
            side_idx = 0
            entry = line[1:]
        else:
            continue

        if entry.endswith("/") and len(entry) > 1:
            entry = entry[:-1]
        _validate_relative_path(entry, label="diffoscope file-list entry")

        if side_idx >= len(roots):
            continue
        side_root = Path(roots[side_idx])
        if _is_link_or_junction(side_root):
            raise ValueError(f"Release root must not be a symbolic link or junction: {side_root}")
        if not side_root.exists():
            continue

        resolved_root = side_root.resolve()
        curr = side_root
        for part in parent_parts + entry.split("/"):
            curr = curr / part
            if _is_link_or_junction(curr):
                raise ValueError(f"Refusing symbolic link or junction in diffoscope path: {curr}")

        if not curr.exists():
            continue
        if not curr.resolve().is_relative_to(resolved_root):
            raise ValueError(f"Refusing path outside trusted root: {curr}")

        if curr.is_dir():
            for dirpath, dirnames, filenames in os.walk(curr, followlinks=False):
                current_dir = Path(dirpath)
                for name in dirnames + filenames:
                    member = current_dir / name
                    if _is_link_or_junction(member):
                        raise ValueError(f"Refusing symbolic link or junction in diffoscope path: {member}")
                for name in filenames:
                    member = current_dir / name
                    if member.is_file():
                        paths.add(member.relative_to(side_root).as_posix())
        elif curr.is_file():
            paths.add(curr.relative_to(side_root).as_posix())


def diffoscope_paths(document, roots):
    """Read changed file leaves, excluding diffoscope's directory metadata."""
    paths = set()

    def visit(item, context=None):
        if isinstance(item, list):
            for entry in item:
                visit(entry, context)
            return
        if not isinstance(item, dict):
            return
        if _is_file_list_item(item):
            _collect_file_list_paths(item, context, roots, paths)
            for child in item.get("details", []):
                visit(child, context)
            return

        current = context
        for source in (item.get("source1", ""), item.get("source2", "")):
            for root in roots:
                root_posix = root.as_posix() if hasattr(root, "as_posix") else str(root)
                prefix = root_posix.rstrip("/") + "/"
                if source.startswith(prefix):
                    current = source[len(prefix):]
        if item.get("unified_diff"):
            if current and not current.endswith("/"):
                paths.add(current)
        for child in item.get("details", []):
            visit(child, current)

    visit(document)
    return sorted(paths)


def observations(evidence, path):
    """Keep REA's provenance on each relevant static observation."""
    found = []
    for node in evidence["normalized_result"]["graph"]["nodes"]:
        for observation in node["observations"]:
            properties = observation["properties"]
            occurrence = properties.get("file_occurrence") or {}
            if properties.get("module_path") == path or occurrence.get("path") == path:
                found.append({
                    "node_id": node["node_id"],
                    "kind": node["kind"],
                    "label": observation["label"],
                    "properties": properties,
                    "evidence": observation["evidence"],
                })
    return found


def _normalize_source_paths(source_paths, enhanced):
    if source_paths is None:
        return []
    if isinstance(source_paths, (str, bytes)):
        raise ValueError("source_paths must be a sequence of relative paths")
    normalized = [_validate_relative_path(path, label="source path") for path in source_paths]
    if normalized and not enhanced:
        raise ValueError("--source-path requires --rea")
    return normalized


def _first_present(containers, key):
    for container in containers:
        if isinstance(container, dict) and key in container and container[key] is not None:
            return container[key]
    return None


def _extract_native_limits(side_evidence, normalized):
    """Collect deduplicated native limitation strings from data and normalized_result."""
    collected = []
    seen = set()
    found = False
    for container in (side_evidence, normalized):
        if isinstance(container, dict) and container.get("limitations") is not None:
            raw_limits = container["limitations"]
            if isinstance(raw_limits, list):
                found = True
                for item in raw_limits:
                    if isinstance(item, str):
                        if item not in seen:
                            seen.add(item)
                            collected.append(item)
                    elif item not in collected:
                        collected.append(item)
    return collected if found else None


def _extract_side_metadata(name, side_evidence):
    normalized = side_evidence.get("normalized_result") or {}
    if not isinstance(normalized, dict):
        normalized = {}
    graph = normalized.get("graph")
    native_coverage = graph.get("coverage") if isinstance(graph, dict) else None
    graph_coverage = (
        native_coverage
        if native_coverage is not None
        else _first_present((side_evidence, normalized), "graph_coverage")
    )
    native_limits = _extract_native_limits(side_evidence, normalized)
    limits = (
        native_limits
        if native_limits is not None
        else _first_present((side_evidence, normalized), "limits")
    )
    return {
        "evidence_id": side_evidence.get("evidence_id"),
        "analysis_file": f"{name}-analysis.json",
        "receipt_file": f"{name}-analysis.receipt.json",
        "root_artifact_sha256": normalized.get("root_artifact_sha256"),
        "source_selection": _first_present((normalized, side_evidence), "source_selection"),
        "graph_coverage": graph_coverage,
        "statistics": _first_present((normalized, side_evidence), "statistics"),
        "limits": limits,
    }


def _semantic_coverage_status(requested_source_paths, sides):
    if requested_source_paths:
        return "partial"
    statuses = []
    for side in sides.values():
        coverage = side.get("graph_coverage")
        if isinstance(coverage, dict) and coverage.get("status"):
            statuses.append(coverage["status"])
        else:
            statuses.append("unknown")
    if "partial" in statuses:
        return "partial"
    if statuses and all(status == "complete" for status in statuses):
        return "complete"
    return "unknown"


def review(left, right, left_tree, right_tree, output, enhanced=False, source_paths=None):
    requested_source_paths = _normalize_source_paths(source_paths, enhanced)
    left = Path(left)
    right = Path(right)
    left_tree = Path(left_tree)
    right_tree = Path(right_tree)
    output = Path(output)
    _validate_inputs(left, right, left_tree, right_tree, output)

    left = left.resolve()
    right = right.resolve()
    left_tree = left_tree.resolve()
    right_tree = right_tree.resolve()
    output = output.resolve()

    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    processes = [run([
        "diffoscope", "--exclude-directory-metadata=yes", "--json", "-",
        str(left_tree), str(right_tree),
    ], output, "diffoscope", accepted=(0, 1))]
    diff_path = output / "diffoscope.json"
    document = json.loads(diff_path.read_text()) if diff_path.stat().st_size else {}
    changed = diffoscope_paths(document, (left_tree, right_tree))
    same_artifact = digest(left) == digest(right)
    analysis_scope = {
        "mode": "none" if not enhanced else "skipped_identical_artifact",
        "requested_source_paths": list(requested_source_paths),
        "reason": (
            "REA analysis not requested (--rea not set)."
            if not enhanced
            else "Left and right artifacts have identical SHA-256 digests; REA analysis skipped."
        ),
        "file_change_coverage": "all_files",
        "semantic_relationship_coverage": "none",
        "sides": {},
    }
    result = {
        "schema": "rea-release-review/v1",
        "pipeline": "diffoscope+rea" if enhanced else "diffoscope",
        "left_sha256": digest(left),
        "right_sha256": digest(right),
        "changed_paths": changed,
        "same_artifact": same_artifact,
        "requested_source_paths": list(requested_source_paths),
        "analysis_scope": analysis_scope,
        "processes": processes,
        "findings": [],
        "model_calls": 0,
        "incremental_model_cost_usd": 0,
        "infrastructure_cost_usd": None,
        "limitations": [
            "Deterministic static pipeline. This run does not measure model reasoning accuracy.",
            "Static relationships do not establish runtime behavior.",
            "Caller supplies independently verified extracted trees; this function does not authenticate arbitrary trees against ASAR contents.",
        ],
    }
    if enhanced and not result["same_artifact"]:
        evidence = {}
        sides = {}
        for name, artifact in (("left", left), ("right", right)):
            command = [
                "rea", "analyze-javascript-application", str(artifact),
                "--json", "--full-output",
            ]
            for source_path in requested_source_paths:
                command.extend(["--source-path", source_path])
            processes.append(run(command, output, name + "-analysis"))
            envelope = json.loads((output / (name + "-analysis.json")).read_text())
            if not envelope.get("ok"):
                raise RuntimeError("REA did not return successful analysis evidence")
            evidence[name] = envelope["data"]
            observed = evidence[name]["normalized_result"]["root_artifact_sha256"]
            if observed != digest(artifact):
                raise ValueError("REA evidence belongs to different artifact bytes")
            sides[name] = _extract_side_metadata(name, evidence[name])
        comparison_input = output / "comparison-input.json"
        comparison_input.write_text(json.dumps(evidence), encoding="utf-8")
        processes.append(run([
            "rea", "compare-application-versions", str(comparison_input),
            "--json", "--full-output",
        ], output, "rea-comparison"))
        files_before, files_after = inventory(left_tree), inventory(right_tree)
        for path in sorted(set(files_before) | set(files_after)):
            if files_before.get(path) != files_after.get(path):
                result["findings"].append({
                    "path": path,
                    "before_sha256": files_before.get(path),
                    "after_sha256": files_after.get(path),
                    "before_evidence_id": evidence["left"]["evidence_id"],
                    "after_evidence_id": evidence["right"]["evidence_id"],
                    "before_observations": observations(evidence["left"], path),
                    "after_observations": observations(evidence["right"], path),
                })
        result["changed_paths"] = [item["path"] for item in result["findings"]]
        semantic_coverage = _semantic_coverage_status(requested_source_paths, sides)
        result["analysis_scope"] = {
            "mode": "selected_sources" if requested_source_paths else "full_artifact",
            "requested_source_paths": list(requested_source_paths),
            "reason": None,
            "file_change_coverage": "all_files",
            "semantic_relationship_coverage": semantic_coverage,
            "comparison_file": "rea-comparison.json",
            "comparison_receipt_file": "rea-comparison.receipt.json",
            "sides": sides,
        }
        if requested_source_paths:
            result["limitations"].append(
                "Selected-source REA analysis (--source-path) covers only the requested source paths; "
                "semantic relationships outside selected files remain unknown, while file-change coverage still uses all inventoried files."
            )
        seen_limits = set(result["limitations"])
        missing_coverage_sides = []
        missing_limits_sides = []
        for side_name, side_meta in sides.items():
            coverage = side_meta.get("graph_coverage")
            if not isinstance(coverage, dict) or not coverage.get("status"):
                missing_coverage_sides.append(side_name)
            side_limits = side_meta.get("limits")
            if side_limits is None:
                missing_limits_sides.append(side_name)
            else:
                for limit_text in side_limits:
                    if isinstance(limit_text, str) and limit_text not in seen_limits:
                        seen_limits.add(limit_text)
                        result["limitations"].append(limit_text)
        if missing_coverage_sides:
            msg = (
                f"REA graph coverage unavailable for {', '.join(missing_coverage_sides)}; "
                "graph coverage status remains unknown."
            )
            if msg not in seen_limits:
                seen_limits.add(msg)
                result["limitations"].append(msg)
        if missing_limits_sides:
            msg = (
                f"REA limitations unavailable for {', '.join(missing_limits_sides)}; "
                "upstream analysis limitations remain unknown."
            )
            if msg not in seen_limits:
                seen_limits.add(msg)
                result["limitations"].append(msg)
    result["elapsed_seconds"] = time.perf_counter() - started
    (output / "review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    rows = "".join(
        "<li>" + html.escape(path) + "</li>" for path in result["changed_paths"]
    )
    evidence_links = ['<a href="review.json">Findings and evidence references</a>']
    if result["analysis_scope"]["sides"]:
        for side_name in ("left", "right"):
            analysis_file = result["analysis_scope"]["sides"][side_name]["analysis_file"]
            evidence_links.append(
                f'<a href="{html.escape(analysis_file)}">{html.escape(side_name)} analysis</a>'
            )
        comparison_file = result["analysis_scope"].get("comparison_file")
        if comparison_file:
            evidence_links.append(f'<a href="{html.escape(comparison_file)}">REA comparison</a>')
    scope_summary = (
        f"Scope: {result['analysis_scope']['mode']} | "
        f"File coverage: {result['analysis_scope']['file_change_coverage']} | "
        f"Semantic relationship coverage: {result['analysis_scope']['semantic_relationship_coverage']}"
    )
    requested_html = ""
    if requested_source_paths:
        requested_items = "".join(
            "<li>" + html.escape(path) + "</li>" for path in requested_source_paths
        )
        requested_html = "<p>Requested source paths:</p><ul>" + requested_items + "</ul>"
    limitation_items = "".join(
        "<li>" + html.escape(item) + "</li>" for item in result["limitations"]
    )
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Release artifact review</title><h1>Release artifact review</h1>'
        "<p>" + html.escape(result["pipeline"]) + " · " + html.escape(scope_summary) + "</p>"
        + requested_html
        + "<ul>" + rows + "</ul>"
        + "<h2>Limitations and unknowns</h2><ul>" + limitation_items + "</ul>"
        + "<p>" + " · ".join(evidence_links) + "</p></html>\n",
        encoding="utf-8",
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("left", "right", "left_tree", "right_tree", "output"):
        parser.add_argument(argument, type=Path)
    parser.add_argument("--rea", action="store_true")
    parser.add_argument(
        "--source-path",
        action="append",
        dest="source_paths",
        default=None,
        help="Exact inventoried relative file path to analyze with REA; repeat to select several.",
    )
    args = parser.parse_args(argv)
    result = review(
        args.left, args.right, args.left_tree,
        args.right_tree, args.output, args.rea,
        source_paths=args.source_paths,
    )
    print(json.dumps({
        "pipeline": result["pipeline"],
        "changes": result["changed_paths"],
        "requested_source_paths": result["requested_source_paths"],
        "semantic_relationship_coverage": result["analysis_scope"]["semantic_relationship_coverage"],
        "elapsed_seconds": result["elapsed_seconds"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
