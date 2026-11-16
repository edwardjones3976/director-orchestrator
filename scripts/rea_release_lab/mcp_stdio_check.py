"""Exercise rapid capture inference through a real disposable REA MCP server."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


UNUSABLE_DURATION_LIMITATION = (
    "Some records carry no usable request duration; their completion and overlap remain unknown."
)


def unpack(result):
    if result.isError:
        raise RuntimeError("REA MCP returned an error")
    value = result.structuredContent
    if value is None:
        value = json.loads("".join(part.text for part in result.content if part.type == "text"))
    if "ok" in value:
        if not value["ok"]:
            raise RuntimeError("REA MCP refused the request")
        return value["data"]
    return value


def _subprocess_error_detail(exc):
    detail = str(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (
            exc.stderr.decode("utf-8", errors="replace").strip()
            if isinstance(exc.stderr, (bytes, bytearray))
            else (exc.stderr or "").strip()
        )
        if stderr:
            return f"{detail}: {stderr}"
    return detail


def _cleanup_containers(label):
    errors = []
    try:
        found = subprocess.run(
            ["docker", "ps", "--filter", "label=" + label, "--format", "{{.ID}}"],
            capture_output=True, text=True, check=True, timeout=20,
        )
        identifiers = found.stdout.split()
    except (subprocess.SubprocessError, OSError) as exc:
        errors.append(f"docker ps failed: {_subprocess_error_detail(exc)}")
        identifiers = []

    for identifier in identifiers:
        try:
            subprocess.run(
                ["docker", "stop", "--time", "5", identifier],
                capture_output=True, text=True, check=True, timeout=20,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            errors.append(f"docker stop {identifier} failed: {_subprocess_error_detail(exc)}")

    try:
        remaining = subprocess.run(
            ["docker", "ps", "--filter", "label=" + label, "--format", "{{.ID}}"],
            capture_output=True, text=True, check=True, timeout=20,
        )
        if remaining.stdout.strip():
            errors.append("The task-owned MCP server did not stop: " + remaining.stdout.strip())
    except (subprocess.SubprocessError, OSError) as exc:
        errors.append(f"docker ps verification failed: {_subprocess_error_detail(exc)}")

    return errors


async def check(image, output):
    directory = output / "mcp"
    directory.mkdir(exist_ok=False)
    label = "doa.rea-lab.mcp=" + hashlib.sha256(str(output).encode("utf-8")).hexdigest()[:16]
    user_args = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    command = [
        "run", "-i", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
        *user_args, "--label", label,
        "--mount", f"type=bind,source={output / 'rapid'},target=/captures,readonly",
        image, "rea", "mcp",
    ]
    results = []
    primary_error = None
    try:
        async with asyncio.timeout(120):
            with (directory / "server.stderr").open("w", encoding="utf-8") as errors:
                async with stdio_client(StdioServerParameters(command="docker", args=command), errlog=errors) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        (directory / "initialize.json").write_text(
                            initialized.model_dump_json(indent=2) + "\n", encoding="utf-8",
                        )
                        for name in [
                            "live-1",
                            "synthetic-disjoint",
                            "synthetic-overlap",
                            "synthetic-unusable-duration",
                        ]:
                            captured = await session.call_tool("inspect_web_network_capture", {
                                "capture_path": f"/captures/{name}/training.har", "format": "har",
                            })
                            (directory / (name + "-capture.json")).write_text(
                                captured.model_dump_json(indent=2) + "\n", encoding="utf-8",
                            )
                            capture = unpack(captured)
                            if "operation" in capture and capture["operation"] != "inspect_web_network_capture":
                                raise AssertionError("Unexpected capture operation: " + str(capture["operation"]))
                            state_reply = await session.call_tool("inspect_analysis_view", {
                                "source": {"kind": "retained-evidence", "evidence_id": capture["evidence_id"]},
                                "view": {"kind": "environment-state", "template_rules": [
                                    "after-uid-literal", "opaque-token", "sibling-variance", "prefix-variance",
                                ]},
                            })
                            (directory / (name + "-state.json")).write_text(
                                state_reply.model_dump_json(indent=2) + "\n", encoding="utf-8",
                            )
                            state = unpack(state_reply)
                            normalized = state["normalized_result"]
                            if normalized["parent_evidence_id"] != capture["evidence_id"]:
                                raise AssertionError("MCP parent_evidence_id correlation mismatch: " + name)
                            if normalized["parent_operation"] != "inspect_web_network_capture":
                                raise AssertionError("MCP parent_operation mismatch: " + name)
                            if normalized["parent_digest"] != capture["evidence_id"][3:]:
                                raise AssertionError("MCP parent_digest correlation mismatch: " + name)
                            plans = normalized["writes"]
                            update = next(plan for plan in plans if plan["tool"] == "put_folders_by_id")
                            expected = name not in ("synthetic-overlap", "synthetic-unusable-duration")
                            if update["served"] != expected:
                                raise AssertionError("MCP interval disposition differs: " + name)
                            if name == "synthetic-unusable-duration":
                                if normalized["timeline"] != "recorded-start-time":
                                    raise AssertionError("Unexpected timeline for synthetic-unusable-duration")
                                if UNUSABLE_DURATION_LIMITATION not in normalized["limitations"]:
                                    raise AssertionError("Missing unusable-duration limitation notice")
                                if normalized["write_clock"] is not None:
                                    raise AssertionError("Expected null write_clock for unusable completion")
                                if update.get("reason") is None:
                                    raise AssertionError("Expected withholding reason for unusable completion")
                            results.append({
                                "case": name,
                                "parent_evidence_id": capture["evidence_id"],
                                "evidence_id": state["evidence_id"],
                                "update_served": update["served"],
                                "update_reason": update.get("reason"),
                                "passed": True,
                            })
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Only stop this check's labelled container after the stdio client has closed.
        cleanup_errors = _cleanup_containers(label)
        report_error = None
        try:
            (directory / "cleanup.json").write_text(
                json.dumps({
                    "label": label,
                    "status": "FAIL" if cleanup_errors else "PASS",
                    "errors": cleanup_errors,
                    "primary_error": f"{type(primary_error).__name__}: {primary_error}" if primary_error is not None else None,
                }, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            report_error = exc
        if cleanup_errors or report_error is not None:
            parts = []
            if cleanup_errors:
                parts.append("Container cleanup errors: " + "; ".join(cleanup_errors))
            if report_error is not None:
                parts.append(f"cleanup.json write failed: {report_error}")
            message = "; ".join(parts)
            print(message, file=sys.stderr)
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(message)
            elif report_error is not None:
                raise RuntimeError(message) from report_error
            else:
                raise RuntimeError(message)

    summary = {"schema": "rea-native-mcp-check/v1", "command": ["docker"] + command, "results": results, "container_cleanup": "PASS", "passed": True}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.image, args.output.resolve()))
