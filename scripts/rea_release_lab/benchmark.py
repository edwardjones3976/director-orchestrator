"""Build owned Electron fixtures and benchmark release review."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import statistics
import time
from pathlib import Path

from review import review


def build_fixtures(output):
    output.mkdir(parents=True, exist_ok=False)
    versions = {
        "baseline": ("preload-a.js", "Build overview", "1.2.0"),
        "candidate": ("preload-b.js", "Release overview", "1.3.0"),
    }
    records = {}
    for name, (preload, label, dependency) in versions.items():
        files = {
            "package.json": json.dumps({
                "name": "owned-electron-release-fixture",
                "version": "1.0.0",
                "main": "main.js",
                "dependencies": {"fixture-format": "^1.2.0"},
            }) + "\n",
            "main.js": (
                'const { app, BrowserWindow } = require("electron");\n'
                'const path = require("node:path");\n'
                'app.whenReady().then(() => {\n'
                '  const window = new BrowserWindow({ webPreferences: {\n'
                f'    preload: path.join(__dirname, "{preload}")\n'
                '  } });\n'
                '  window.loadFile("index.html");\n'
                '});\n'
            ),
            "index.html": (
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<title>Release fixture</title><h1 id="title"></h1>'
                '<script src="renderer.js"></script></html>\n'
            ),
            "renderer.js": f'document.querySelector("#title").textContent = "{label}";\n',
            "preload-a.js": (
                'const { contextBridge } = require("electron");\n'
                'contextBridge.exposeInMainWorld("fixture", { revision: () => "a" });\n'
            ),
            "preload-b.js": (
                'const { contextBridge } = require("electron");\n'
                'contextBridge.exposeInMainWorld("fixture", { revision: () => "b" });\n'
            ),
            "node_modules/fixture-format/package.json": json.dumps({
                "name": "fixture-format", "version": dependency, "main": "index.js",
            }) + "\n",
            "node_modules/fixture-format/index.js": 'module.exports = value => String(value);\n',
            "unchanged.json": '{"theme":"light"}\n',
        }
        artifact = output / name / "app.asar"
        artifact.parent.mkdir()
        with tempfile.TemporaryDirectory(prefix="owned-electron-") as source:
            for relative, body in sorted(files.items()):
                path = Path(source) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body.encode("utf-8"))
            subprocess.run(["asar", "pack", source, str(artifact)], check=True, timeout=30)
        records[name] = {
            "file": artifact.relative_to(output).as_posix(),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    control = output / "control" / "app.asar"
    control.parent.mkdir()
    shutil.copyfile(output / records["baseline"]["file"], control)
    records["control"] = {
        "file": control.relative_to(output).as_posix(),
        "sha256": hashlib.sha256(control.read_bytes()).hexdigest(),
    }
    if records["control"]["sha256"] != records["baseline"]["sha256"]:
        raise RuntimeError("The unchanged control did not match the baseline")
    (output / "fixture.json").write_text(json.dumps({
        "artifacts": records,
        "expected_changes": [
            {
                "path": "main.js",
                "label": "Electron preload selection",
                "before": "preload-a.js",
                "after": "preload-b.js",
            },
            {
                "path": "renderer.js",
                "label": "Renderer heading",
                "before": "Build overview",
                "after": "Release overview",
            },
            {
                "path": "node_modules/fixture-format/package.json",
                "label": "Bundled fixture dependency",
                "before": "1.2.0",
                "after": "1.3.0",
            },
        ],
        "unchanged_path": "unchanged.json",
    }, indent=2) + "\n", encoding="utf-8")


def benchmark(output, repetitions):
    fixture = json.loads((output / "fixtures" / "fixture.json").read_text())
    trees = output / "extracted"
    trees.mkdir(exist_ok=False)
    preprocessing = time.perf_counter()
    for name, artifact in fixture["artifacts"].items():
        subprocess.run([
            "asar", "extract", str(output / "fixtures" / artifact["file"]),
            str(trees / name),
        ], check=True, timeout=30)
    preprocessing = time.perf_counter() - preprocessing
    expected = {item["path"] for item in fixture["expected_changes"]}
    measurements = []
    for repetition in range(repetitions):
        for pair in ("candidate", "control"):
            arms = (False, True) if repetition % 2 == 0 else (True, False)
            for enhanced in arms:
                arm = "rea" if enhanced else "diffoscope"
                directory = output / "runs" / f"{pair}-{arm}-{repetition + 1}"
                result = review(
                    output / "fixtures/baseline/app.asar",
                    output / "fixtures" / fixture["artifacts"][pair]["file"],
                    trees / "baseline", trees / pair, directory, enhanced,
                )
                truth = expected if pair == "candidate" else set()
                predicted = set(result["changed_paths"])
                measurements.append({
                    "pipeline": result["pipeline"],
                    "pair": pair,
                    "repetition": repetition + 1,
                    "true_positives": len(predicted & truth),
                    "false_positives": len(predicted - truth),
                    "false_negatives": len(truth - predicted),
                    "elapsed_seconds": result["elapsed_seconds"],
                    "review": directory.relative_to(output).as_posix() + "/review.json",
                })
    summary = {
        "schema": "rea-release-benchmark/v1",
        "shared_extraction_seconds": preprocessing,
        "measurements": measurements,
        "medians": [
            {
                "pipeline": pipeline,
                "pair": pair,
                "seconds": statistics.median(
                    item["elapsed_seconds"] for item in measurements
                    if item["pipeline"] == pipeline and item["pair"] == pair
                ),
            }
            for pair in ("candidate", "control")
            for pipeline in ("diffoscope", "diffoscope+rea")
        ],
        "cost": {
            "incremental_model_calls": 0,
            "incremental_model_cost_usd": 0,
            "infrastructure_cost_usd": None,
            "codex_authoring_cost_usd": None,
            "note": "Tool timings exclude installation, shared extraction, and Codex authoring.",
        },
        "passed": all(
            item["false_positives"] == 0 and item["false_negatives"] == 0
            for item in measurements
        ),
    }
    (output / "benchmark.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    if not summary["passed"]:
        raise SystemExit("Benchmark contains missed changes or false positives")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    if args.run:
        benchmark(args.output.resolve(), args.repetitions)
    else:
        build_fixtures(args.output.resolve())
