"""Check rapid real captures and synthetic interval safety through the native REA CLI."""

import argparse
import copy
import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

from agentenv_fixture import capture


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, directory, label, expected_error=None):
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, timeout=90)
    output = directory / (label + ".json")
    errors = directory / (label + ".stderr")
    output.write_bytes(result.stdout)
    errors.write_bytes(result.stderr)
    receipt = {
        "command": command, "exit_code": result.returncode,
        "elapsed_seconds": time.perf_counter() - started,
        "stdout_sha256": digest(output), "stderr_sha256": digest(errors),
    }
    (directory / (label + ".receipt.json")).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8",
    )
    value = json.loads(result.stdout.decode("utf-8"))
    if expected_error is not None:
        if result.returncode == 0 or value.get("ok") or value.get("error", {}).get("code") != expected_error:
            raise AssertionError("Expected native input refusal was missing")
        return value["error"]
    if result.returncode:
        raise RuntimeError("Native rapid check failed: " + label)
    if not value.get("ok"):
        raise RuntimeError("REA refused rapid check: " + label)
    return value["data"]


def build(directory, expected_error=None):
    return run([
        "rea", "environment-build", str(directory / "training.har"),
        str(directory / "bundle"), "--name", "release-folders", "--json", "--full-output",
    ], directory, "export", expected_error)


def fidelity(directory):
    probe = run([
        "rea", "inspect-web-network-capture", str(directory / "probe.har"),
        "har", "--json", "--full-output",
    ], directory, "probe-envelope")
    (directory / "probe-evidence.json").write_text(json.dumps(probe), encoding="utf-8")
    result = run([
        "rea", "environment-compare", str(directory / "bundle/envs/release-folders/manifest.json"),
        "--evidence", str(directory / "bundle/envs/release-folders/evidence.json"),
        "--probe", str(directory / "probe-evidence.json"), "--json", "--full-output",
    ], directory, "fidelity")
    summary = result["summary"]
    if summary["equal"] != 21 or summary["steps"] != 21 or summary["unobserved"]:
        raise AssertionError("Rapid capture fidelity differs: " + str(summary))
    return summary


def timing(path):
    entries = json.loads(path.read_text(encoding="utf-8"))["log"]["entries"]
    starts = [datetime.fromisoformat(e["startedDateTime"].replace("Z", "+00:00")) for e in entries]
    milliseconds = [dt.replace(microsecond=(dt.microsecond // 1000) * 1000) for dt in starts]
    return {
        "sha256": digest(path), "requests": len(entries),
        "minimum_start_gap_microseconds": min((b - a).total_seconds() * 1_000_000 for a, b in zip(starts, starts[1:])),
        "same_millisecond_adjacent_pairs": sum(a == b for a, b in zip(milliseconds, milliseconds[1:])),
    }


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for repetition in range(1, 4):
        directory = output / ("live-" + str(repetition))
        directory.mkdir()
        capture(directory / "training.har", ("Alpha", "Beta"))
        capture(directory / "probe.har", ("Practice", "Second"))
        export = build(directory)
        if export["coverage"]["writes"]["withheld"]:
            raise AssertionError("A completed rapid write was withheld")
        results.append({
            "case": directory.name, "kind": "actual-unpaced-HTTP",
            "training": timing(directory / "training.har"), "probe": timing(directory / "probe.har"),
            "fidelity": fidelity(directory), "write_coverage": export["coverage"]["writes"],
        })
    if sum(item["training"]["same_millisecond_adjacent_pairs"] + item["probe"]["same_millisecond_adjacent_pairs"] for item in results) == 0:
        raise AssertionError("The actual HTTP captures did not exercise same-millisecond requests")
    synthetic_cases = [
        "synthetic-disjoint",
        "synthetic-overlap",
        "synthetic-unknown-duration",
        "synthetic-unusable-duration",
    ]
    for name in synthetic_cases:
        directory = output / name
        directory.mkdir()
        for capture_name in ["training.har", "probe.har"]:
            document = copy.deepcopy(json.loads((output / "live-1" / capture_name).read_text(encoding="utf-8")))
            for ordinal, entry in enumerate(document["log"]["entries"]):
                entry["startedDateTime"] = "2026-11-15T00:00:00." + str(100_000_000 + ordinal * 100_000).zfill(9) + "Z"
                entry["time"] = 0.01
                if entry["request"]["method"] == "PUT":
                    if name == "synthetic-overlap":
                        entry["time"] = 100
                    elif name == "synthetic-unknown-duration":
                        del entry["time"]
                    elif name == "synthetic-unusable-duration":
                        entry["time"] = 1e20
            (directory / capture_name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        if name == "synthetic-unknown-duration":
            error = build(directory, expected_error="invalid_request")
            if error["category"] != "invalid_input" or (directory / "bundle").exists():
                raise AssertionError("Malformed HAR produced an executable bundle")
            results.append({"case": name, "kind": "native-schema-regression", "error": error, "passed": True})
            continue
        coverage = build(directory)["coverage"]["writes"]
        result = {"case": name, "kind": "synthetic-interval-regression", "write_coverage": coverage}
        if name == "synthetic-disjoint":
            if coverage["withheld"]:
                raise AssertionError("Exact disjoint intervals were withheld")
            result["fidelity"] = fidelity(directory)
        else:
            if any(item["tool"] == "put_folders_by_id" for item in coverage["served"]):
                raise AssertionError("Unconfirmed update became executable")
            if not any(item["tool"] == "put_folders_by_id" for item in coverage["withheld"]):
                raise AssertionError("Expected unconfirmed update finding is missing")
        result["passed"] = True
        results.append(result)
    summary = {"schema": "rea-rapid-capture-checks/v1", "injected_pause_seconds": 0, "results": results, "passed": True}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output.resolve())
