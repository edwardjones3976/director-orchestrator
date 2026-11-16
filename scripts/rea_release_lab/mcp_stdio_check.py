"""Exercise rapid capture inference through a real disposable REA MCP server."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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


async def check(image, output):
    directory = output / "mcp"
    directory.mkdir(exist_ok=False)
    label = "doa.rea-lab.mcp=" + hashlib.sha256(str(output).encode()).hexdigest()[:16]
    command = [
        "run", "-i", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}", "--label", label,
        "--mount", f"type=bind,source={output / 'rapid'},target=/captures,readonly",
        image, "rea", "mcp",
    ]
    results = []
    try:
        async with asyncio.timeout(120):
            with (directory / "server.stderr").open("w") as errors:
                async with stdio_client(StdioServerParameters(command="docker", args=command), errlog=errors) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        (directory / "initialize.json").write_text(initialized.model_dump_json(indent=2) + "\n")
                        for name in ["live-1", "synthetic-disjoint", "synthetic-overlap"]:
                            captured = await session.call_tool("inspect_web_network_capture", {
                                "capture_path": f"/captures/{name}/training.har", "format": "har",
                            })
                            (directory / (name + "-capture.json")).write_text(captured.model_dump_json(indent=2) + "\n")
                            capture = unpack(captured)
                            state_reply = await session.call_tool("inspect_analysis_view", {
                                "source": {"kind": "retained-evidence", "evidence_id": capture["evidence_id"]},
                                "view": {"kind": "environment-state", "template_rules": [
                                    "after-uid-literal", "opaque-token", "sibling-variance", "prefix-variance",
                                ]},
                            })
                            (directory / (name + "-state.json")).write_text(state_reply.model_dump_json(indent=2) + "\n")
                            state = unpack(state_reply)
                            plans = state["normalized_result"]["writes"]
                            update = next(plan for plan in plans if plan["tool"] == "put_folders_by_id")
                            expected = name != "synthetic-overlap"
                            if update["served"] != expected:
                                raise AssertionError("MCP interval disposition differs: " + name)
                            results.append({"case": name, "evidence_id": state["evidence_id"], "update_served": update["served"], "passed": True})
    finally:
        # Only stop this check's labelled container after the stdio client has closed.
        found = subprocess.run(["docker", "ps", "--filter", "label=" + label, "--format", "{{.ID}}"], capture_output=True, text=True, check=True, timeout=20)
        for identifier in found.stdout.split():
            subprocess.run(["docker", "stop", "--time", "5", identifier], check=True, timeout=20, stdout=subprocess.DEVNULL)
        remaining = subprocess.run(["docker", "ps", "--filter", "label=" + label, "--format", "{{.ID}}"], capture_output=True, text=True, check=True, timeout=20)
        if remaining.stdout.strip():
            raise RuntimeError("The task-owned MCP server did not stop")
    summary = {"schema": "rea-native-mcp-check/v1", "command": ["docker"] + command, "results": results, "container_cleanup": "PASS", "passed": True}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.image, args.output.resolve()))
