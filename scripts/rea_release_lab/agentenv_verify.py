"""AgentEnv verifier exercising a scripted folder task and its final state."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(mcp_url):
    events = []
    async with asyncio.timeout(60):
        async with streamable_http_client(mcp_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"{name} returned an MCP error")
                    data = result.structuredContent
                    if data is None:
                        data = json.loads("".join(part.text for part in result.content))
                    events.append({"tool": name, "arguments": arguments, "result": data})
                    return data

                original = await call("get_folders", {})
                created = await call("post_folders", {"body": {"name": "Practice"}})
                assert created["status"] == 201
                identifier = created["body"]["id"]
                after_create = await call("get_folders", {})
                folders = after_create["body"]["folders"]
                assert any(item["id"] == identifier and item["name"] == "Practice" for item in folders)
                assert not any(item["id"] == identifier and item["name"] == "Wrong name" for item in folders)
                updated = await call("put_folders_by_id", {
                    "id": identifier, "body": {"name": "Practice revised"},
                })
                assert updated["status"] == 200
                readback = await call("get_folders_by_id", {"id": identifier})
                assert readback["body"]["name"] == "Practice revised"
                removed = await call("delete_folders_by_id", {"id": identifier})
                assert removed["status"] == 200
                missing = await call("get_folders_by_id", {"id": identifier})
                assert missing["status"] == 404
                final = await call("get_folders", {})
                assert final["body"] == original["body"]
    print(json.dumps({"scripted_task": True, "events": events}))
    return [
        {"id": "workflow", "result": True, "description": "Created, renamed, read, and deleted Practice. The final state matches the starting state."},
        {"id": "negative-control", "result": True, "description": "The wrong expected folder name failed the state check."},
    ]


if __name__ == "__main__":
    results = asyncio.run(verify(sys.argv[1]))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))
