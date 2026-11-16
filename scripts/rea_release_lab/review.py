"""Compare release artifacts with diffoscope and optional REA evidence."""

import argparse
import hashlib
import html
import json
import subprocess
import time
from pathlib import Path


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


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Release input contains a symbolic link")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(path)
    return files


def diffoscope_paths(document, roots):
    """Read changed file leaves, excluding diffoscope's directory metadata."""
    paths = set()

    def visit(item, context=None):
        current = context
        for source in (item.get("source1", ""), item.get("source2", "")):
            for root in roots:
                prefix = str(root).rstrip("/") + "/"
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


def review(left, right, left_tree, right_tree, output, enhanced=False):
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    processes = [run([
        "diffoscope", "--exclude-directory-metadata=yes", "--json", "-",
        str(left_tree), str(right_tree),
    ], output, "diffoscope", accepted=(0, 1))]
    diff_path = output / "diffoscope.json"
    document = json.loads(diff_path.read_text()) if diff_path.stat().st_size else {}
    changed = diffoscope_paths(document, (left_tree, right_tree))
    result = {
        "schema": "rea-release-review/v1",
        "pipeline": "diffoscope+rea" if enhanced else "diffoscope",
        "left_sha256": digest(left),
        "right_sha256": digest(right),
        "changed_paths": changed,
        "same_artifact": digest(left) == digest(right),
        "processes": processes,
        "findings": [],
        "model_calls": 0,
        "incremental_model_cost_usd": 0,
        "infrastructure_cost_usd": None,
        "limitations": [
            "Deterministic static pipeline. This run does not measure model reasoning accuracy.",
            "Static relationships do not establish runtime behavior.",
        ],
    }
    if enhanced and not result["same_artifact"]:
        evidence = {}
        for name, artifact in (("left", left), ("right", right)):
            processes.append(run([
                "rea", "analyze-javascript-application", str(artifact),
                "--json", "--full-output",
            ], output, name + "-analysis"))
            envelope = json.loads((output / (name + "-analysis.json")).read_text())
            if not envelope.get("ok"):
                raise RuntimeError("REA did not return successful analysis evidence")
            evidence[name] = envelope["data"]
            observed = evidence[name]["normalized_result"]["root_artifact_sha256"]
            if observed != digest(artifact):
                raise ValueError("REA evidence belongs to different artifact bytes")
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
    result["elapsed_seconds"] = time.perf_counter() - started
    (output / "review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    rows = "".join(
        "<li>" + html.escape(path) + "</li>" for path in result["changed_paths"]
    )
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Release artifact review</title><h1>Release artifact review</h1>'
        "<p>" + html.escape(result["pipeline"]) + "</p><ul>" + rows + "</ul>"
        '<p><a href="review.json">Findings and evidence references</a></p></html>\n',
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("left", "right", "left_tree", "right_tree", "output"):
        parser.add_argument(argument, type=Path)
    parser.add_argument("--rea", action="store_true")
    args = parser.parse_args()
    result = review(
        args.left.resolve(), args.right.resolve(), args.left_tree.resolve(),
        args.right_tree.resolve(), args.output.resolve(), args.rea,
    )
    print(json.dumps({
        "pipeline": result["pipeline"], "changes": result["changed_paths"],
        "elapsed_seconds": result["elapsed_seconds"],
    }))
