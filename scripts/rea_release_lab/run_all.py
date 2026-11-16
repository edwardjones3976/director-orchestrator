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


def cleanup_owned_container(container_name, expected_labels, timeout=20):
    """Verify container ownership via labels and stop only the verified owned container."""
    try:
        inspect_result = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return "inspect-failed", f"docker inspect failed: {exc}"

    if inspect_result.returncode != 0:
        detail = (inspect_result.stderr or inspect_result.stdout or "").strip()
        if "no such" in detail.lower():
            return "already-removed", detail
        return "inspect-failed", detail or f"docker inspect exited {inspect_result.returncode}"

    try:
        info = json.loads(inspect_result.stdout)
        if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict):
            return "inspect-failed", "Unexpected docker inspect payload"
        entry = info[0]
        actual_name = str(entry.get("Name", "")).lstrip("/")
        labels = (entry.get("Config") or {}).get("Labels") or {}
        if not isinstance(labels, dict):
            labels = {}
        container_id = entry.get("Id") or container_name
    except (ValueError, TypeError) as exc:
        return "inspect-failed", f"Invalid docker inspect JSON: {exc}"

    if actual_name != container_name or any(labels.get(k) != v for k, v in expected_labels.items()):
        return (
            "refused-foreign-owner",
            f"Container {container_name} ({container_id}) labels {labels} do not match expected {expected_labels}",
        )

    try:
        stop_result = subprocess.run(
            ["docker", "stop", "--time", "5", str(container_id)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return "stop-failed", f"docker stop failed: {exc}"

    if stop_result.returncode != 0:
        detail = (stop_result.stderr or stop_result.stdout or "").strip()
        if "no such" in detail.lower():
            return "already-removed", detail
        return "stop-failed", detail or f"docker stop exited {stop_result.returncode}"

    return "stopped", str(container_id)


def execute(
    output, receipts, command, name, timeout=900,
    container_name=None, container_labels=None, cleanup_timeout=20,
):
    started = time.perf_counter()
    log = output / (name + ".log")
    errors = output / (name + ".stderr")
    receipts_path = output / "run-receipts.json"

    def write_receipt(receipt):
        receipts.append(receipt)
        receipts_path.write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")

    try:
        with log.open("wb") as stream, errors.open("wb") as diagnostics:
            process = subprocess.run(
                command, stdout=stream, stderr=diagnostics,
                cwd=output, timeout=timeout,
            )
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        status = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "cancelled"
        cleanup_status = "not-applicable"
        cleanup_detail = None
        if container_name is not None:
            cleanup_status, cleanup_detail = cleanup_owned_container(
                container_name, container_labels or {}, timeout=cleanup_timeout,
            )
        receipt = {
            "step": name, "command": command, "exit_code": None,
            "status": status,
            "elapsed_seconds": time.perf_counter() - started,
            "log": log.name, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "stderr": errors.name,
            "stderr_sha256": hashlib.sha256(errors.read_bytes()).hexdigest(),
            "container_name": container_name,
            "container_labels": container_labels,
            "cleanup_status": cleanup_status,
            "cleanup_detail": cleanup_detail,
        }
        write_receipt(receipt)
        if container_name is not None and cleanup_status not in ("stopped", "already-removed"):
            raise RuntimeError(
                f"{name} {status}; container cleanup {cleanup_status}: {cleanup_detail}"
            ) from exc
        raise

    receipt = {
        "step": name, "command": command, "exit_code": process.returncode,
        "status": "passed" if process.returncode == 0 else "failed",
        "elapsed_seconds": time.perf_counter() - started,
        "log": log.name, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "stderr": errors.name,
        "stderr_sha256": hashlib.sha256(errors.read_bytes()).hexdigest(),
        "container_name": container_name,
        "container_labels": container_labels,
        "cleanup_status": "not-needed" if container_name is not None else "not-applicable",
    }
    write_receipt(receipt)
    if process.returncode:
        raise RuntimeError(f"{name} failed. See {log}")
    print(name, "PASS", round(receipt["elapsed_seconds"], 3), flush=True)


def docker_run_command(image, source, output, step_name, run_id, container_args):
    container_name = f"doa-rea-lab-{run_id}-{step_name}"
    labels = {
        "doa.rea-lab.run": run_id,
        "doa.rea-lab.step": step_name,
    }
    user_args = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    command = [
        "docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
        *user_args,
        "--name", container_name,
        "--label", f"doa.rea-lab.run={run_id}",
        "--label", f"doa.rea-lab.step={step_name}",
        "--mount", f"type=bind,source={source},target=/lab,readonly",
        "--mount", f"type=bind,source={output},target=/evidence",
        image,
        *container_args,
    ]
    return command, container_name, labels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--agent-env", default="agent-env")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parent
    run_id = hashlib.sha256(str(output).encode("utf-8")).hexdigest()[:16]
    receipts = []

    def run_step(command, name, timeout=900):
        execute(output, receipts, command, name, timeout=timeout)

    def run_docker(step_name, container_args, timeout=900):
        command, container_name, labels = docker_run_command(
            image, source, output, step_name, run_id, container_args,
        )
        execute(
            output, receipts, command, step_name, timeout=timeout,
            container_name=container_name, container_labels=labels,
        )

    image = "doa-rea-release-lab:20261115-rapid-mcp"
    run_step(["docker", "build", "-t", image, "-f", str(source / "Dockerfile"), str(source)], "image-build")
    run_docker("unit-tests", ["python3", "-m", "unittest", "discover", "-s", "/lab", "-p", "test_*.py"])
    run_docker("precision-tests", ["node", "--test", "/lab/test_precision.mjs"])
    run_docker("fixture-build", ["python3", "/lab/benchmark.py", "/evidence/fixtures"])
    run_docker("benchmark", ["python3", "/lab/benchmark.py", "/evidence", "--run"])
    run_docker("http-capture", ["python3", "/lab/agentenv_fixture.py", "/evidence/captures"])
    run_docker("rapid-requests", ["python3", "/lab/rapid_checks.py", "/evidence/rapid"])
    run_step([sys.executable, str(source / "mcp_stdio_check.py"), image, str(output)], "native-mcp", timeout=180)
    run_docker("agentenv-export", [
        "rea", "environment-build", "/evidence/captures/training.har",
        "/evidence/agentenv-bundle", "--name", "release-folders", "--json", "--full-output",
    ])
    run_docker("probe-evidence", [
        "rea", "inspect-web-network-capture", "/evidence/captures/probe.har",
        "har", "--json", "--full-output",
    ])
    probe_text = (output / "probe-evidence.log").read_text(encoding="utf-8")
    probe = json.loads(probe_text)
    (output / "probe-evidence.json").write_text(json.dumps(probe["data"]), encoding="utf-8")
    run_docker("agentenv-fidelity", [
        "rea", "environment-compare",
        "/evidence/agentenv-bundle/envs/release-folders/manifest.json",
        "--evidence", "/evidence/agentenv-bundle/envs/release-folders/evidence.json",
        "--probe", "/evidence/probe-evidence.json", "--json", "--full-output",
    ])
    fidelity = json.loads((output / "agentenv-fidelity.log").read_text(encoding="utf-8"))
    if fidelity["data"]["summary"]["agreement"] != 1:
        raise RuntimeError("The AgentEnv export disagreed with the held-out recording")
    bundle = output / "framework-bundle"
    shutil.copytree(output / "agentenv-bundle", bundle)
    run_step([sys.executable, str(source / "prepare_agentenv.py"), str(bundle)], "prepare-framework")
    run_step([args.agent_env, "run", str(bundle), "--task", "smoke"], "framework-smoke", timeout=1200)
    run_step([args.agent_env, "run", str(bundle), "--task", "workflow"], "framework-workflow", timeout=600)
    print(output, flush=True)


if __name__ == "__main__":
    main()
