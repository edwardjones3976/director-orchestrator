"""Run the release benchmark and REA/AgentEnv checks on a Linux Docker host."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--agent-env", default="agent-env")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parent
    receipts = []

    def execute(command, name, timeout=900):
        started = time.perf_counter()
        log = output / (name + ".log")
        errors = output / (name + ".stderr")
        with log.open("wb") as stream, errors.open("wb") as diagnostics:
            process = subprocess.run(
                command, stdout=stream, stderr=diagnostics,
                cwd=output, timeout=timeout,
            )
        receipt = {
            "step": name, "command": command, "exit_code": process.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "log": log.name, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "stderr": errors.name,
            "stderr_sha256": hashlib.sha256(errors.read_bytes()).hexdigest(),
        }
        receipts.append(receipt)
        (output / "run-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
        if process.returncode:
            raise RuntimeError(f"{name} failed. See {log}")
        print(name, "PASS", round(receipt["elapsed_seconds"], 3), flush=True)

    image = "doa-rea-release-lab:20261115-rapid-mcp"
    execute(["docker", "build", "-t", image, "-f", str(source / "Dockerfile"), str(source)], "image-build")
    docker = [
        "docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", f"type=bind,source={source},target=/lab,readonly",
        "--mount", f"type=bind,source={output},target=/evidence", image,
    ]
    execute(docker + ["python3", "-m", "unittest", "test_review"], "unit-tests")
    execute(docker + ["node", "--test", "/lab/test_precision.mjs"], "precision-tests")
    execute(docker + ["python3", "/lab/benchmark.py", "/evidence/fixtures"], "fixture-build")
    execute(docker + ["python3", "/lab/benchmark.py", "/evidence", "--run"], "benchmark")
    execute(docker + ["python3", "/lab/agentenv_fixture.py", "/evidence/captures"], "http-capture")
    execute(docker + ["python3", "/lab/rapid_checks.py", "/evidence/rapid"], "rapid-requests")
    execute([sys.executable, str(source / "mcp_stdio_check.py"), image, str(output)], "native-mcp", timeout=180)
    export_command = docker + [
        "rea", "environment-build", "/evidence/captures/training.har",
        "/evidence/agentenv-bundle", "--name", "release-folders", "--json", "--full-output",
    ]
    execute(export_command, "agentenv-export")
    execute(docker + [
        "rea", "inspect-web-network-capture", "/evidence/captures/probe.har",
        "har", "--json", "--full-output",
    ], "probe-evidence")
    probe_text = (output / "probe-evidence.log").read_text()
    probe = json.loads(probe_text)
    (output / "probe-evidence.json").write_text(json.dumps(probe["data"]))
    execute(docker + [
        "rea", "environment-compare",
        "/evidence/agentenv-bundle/envs/release-folders/manifest.json",
        "--evidence", "/evidence/agentenv-bundle/envs/release-folders/evidence.json",
        "--probe", "/evidence/probe-evidence.json", "--json", "--full-output",
    ], "agentenv-fidelity")
    fidelity = json.loads((output / "agentenv-fidelity.log").read_text())
    if fidelity["data"]["summary"]["agreement"] != 1:
        raise RuntimeError("The AgentEnv export disagreed with the held-out recording")
    bundle = output / "framework-bundle"
    shutil.copytree(output / "agentenv-bundle", bundle)
    execute([sys.executable, str(source / "prepare_agentenv.py"), str(bundle)], "prepare-framework")
    execute([args.agent_env, "run", str(bundle), "--task", "smoke"], "framework-smoke", timeout=1200)
    execute([args.agent_env, "run", str(bundle), "--task", "workflow"], "framework-workflow", timeout=600)
    print(output, flush=True)


if __name__ == "__main__":
    main()
