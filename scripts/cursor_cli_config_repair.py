"""Repair ~/.cursor/cli-config.json after MCP retirement (read-only Foreman register)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RETIRED_MCP_SERVERS = frozenset(
    {
        "windows-mcp",
        "Researchers",
        "foreman-powershell-readonly",
        "providers",
        "workers",
        "admin",
        "admin-claude",
        "admin-cursor",
        "admin-openai",
    }
)

FOREMAN_REGISTER_READ_TOOLS = (
    "Mcp(foreman-register:register_list)",
    "Mcp(foreman-register:register_show)",
    "Mcp(foreman-register:register_inbox)",
)

MCP_ALLOW_RE = re.compile(r"^Mcp\(([^:(]+)(?::[^)]+)?\)$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def parse_mcp_allow_entry(entry: str) -> str | None:
    if not isinstance(entry, str):
        return None
    match = MCP_ALLOW_RE.match(entry.strip())
    if not match:
        return None
    return match.group(1)


def is_retired_allow_entry(entry: str) -> bool:
    server = parse_mcp_allow_entry(entry)
    return server is not None and server in RETIRED_MCP_SERVERS


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def mcp_server_keys(mcp_path: Path) -> set[str]:
    data = load_json_object(mcp_path)
    servers = data.get("mcpServers")
    if servers is None:
        return set()
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be an object in {mcp_path}")
    return set(servers.keys())


def assert_retired_servers_absent(mcp_path: Path) -> None:
    present = sorted(mcp_server_keys(mcp_path) & RETIRED_MCP_SERVERS)
    if present:
        raise ValueError(
            "refusing to edit cli-config while retired MCP servers remain registered: "
            + ", ".join(present)
        )


def repair_permissions(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    permissions = config.get("permissions")
    if permissions is None:
        permissions = {}
        config["permissions"] = permissions
    if not isinstance(permissions, dict):
        raise ValueError("permissions must be an object")

    allow = permissions.get("allow")
    if allow is None:
        allow = []
    if not isinstance(allow, list):
        raise ValueError("permissions.allow must be an array")

    deny = permissions.get("deny")
    if deny is not None and not isinstance(deny, list):
        raise ValueError("permissions.deny must be an array when present")

    removed: list[str] = []
    kept: list[str] = []
    for entry in allow:
        if not isinstance(entry, str):
            raise ValueError(f"permissions.allow entry is not a string: {entry!r}")
        if is_retired_allow_entry(entry):
            removed.append(entry)
        else:
            kept.append(entry)

    for tool in FOREMAN_REGISTER_READ_TOOLS:
        if tool not in kept:
            kept.append(tool)

    permissions["allow"] = kept
    return config, removed


def validate_config(config: dict[str, Any]) -> None:
    permissions = config.get("permissions")
    if not isinstance(permissions, dict):
        raise ValueError("permissions object required")
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        raise ValueError("permissions.allow must be an array")
    for entry in allow:
        if not isinstance(entry, str):
            raise ValueError("allow entries must be strings")
    deny = permissions.get("deny")
    if deny is not None:
        if not isinstance(deny, list):
            raise ValueError("permissions.deny must be an array when present")
        for entry in deny:
            if not isinstance(entry, str):
                raise ValueError("deny entries must be strings")

    for entry in allow:
        if is_retired_allow_entry(entry):
            raise ValueError(f"retired allow entry remains: {entry}")
    for tool in FOREMAN_REGISTER_READ_TOOLS:
        if tool not in allow:
            raise ValueError(f"missing required allow entry: {tool}")
    for entry in allow:
        server = parse_mcp_allow_entry(entry)
        if server == "foreman-register" and entry not in FOREMAN_REGISTER_READ_TOOLS:
            raise ValueError(
                "foreman-register allow entries must be exact read tools only: "
                f"found {entry}"
            )


def repair_cli_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    repaired, removed = repair_permissions(dict(config))
    validate_config(repaired)
    return repaired, removed


def write_receipt(
    receipt_path: Path,
    *,
    before_sha256: str,
    after_sha256: str,
    removed: list[str],
    cli_config_path: Path,
    mcp_path: Path,
    dry_run: bool,
) -> None:
    receipt = {
        "request_id": "ADMIN-RESOURCES-120H-20261027",
        "repair": "cursor-cli-config-permissions",
        "dry_run": dry_run,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cli_config_path": str(cli_config_path),
        "mcp_json_path": str(mcp_path),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "removed_allow_entries": removed,
        "added_allow_entries": list(FOREMAN_REGISTER_READ_TOOLS),
        "configured_not_loaded_note": (
            "CLI loads permissions at session start; restart cursor-agent after apply."
        ),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli-config",
        type=Path,
        default=None,
        help="Path to cli-config.json (default: ~/.cursor/cli-config.json)",
    )
    parser.add_argument(
        "--mcp-json",
        type=Path,
        default=None,
        help="Path to mcp.json (default: ~/.cursor/mcp.json)",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Write JSON receipt to this path",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired config (default: dry-run to stdout)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory for before backup (default: sibling backups/ under receipt root)",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    cli_config_path = args.cli_config or (home / ".cursor" / "cli-config.json")
    mcp_path = args.mcp_json or (home / ".cursor" / "mcp.json")

    if not mcp_path.is_file():
        print(f"mcp.json not found: {mcp_path}", file=sys.stderr)
        return 2
    assert_retired_servers_absent(mcp_path)

    if not cli_config_path.is_file():
        print(f"cli-config.json not found: {cli_config_path}", file=sys.stderr)
        return 2

    before_bytes = cli_config_path.read_bytes()
    before_sha = sha256_bytes(before_bytes)
    before_obj = json.loads(before_bytes.decode("utf-8"))
    if not isinstance(before_obj, dict):
        print("cli-config root must be an object", file=sys.stderr)
        return 2

    after_obj, removed = repair_cli_config(before_obj)
    after_bytes = canonical_json(after_obj)
    after_sha = sha256_bytes(after_bytes)

    if args.receipt:
        write_receipt(
            args.receipt,
            before_sha256=before_sha,
            after_sha256=after_sha,
            removed=removed,
            cli_config_path=cli_config_path,
            mcp_path=mcp_path,
            dry_run=not args.apply,
        )

    print(f"before_sha256={before_sha}")
    print(f"after_sha256={after_sha}")
    if removed:
        print("removed_allow_entries:")
        for entry in removed:
            print(f"  - {entry}")
    else:
        print("removed_allow_entries: (none matched retired servers)")

    if not args.apply:
        print("\n--- proposed cli-config.json ---")
        sys.stdout.buffer.write(after_bytes)
        return 0

    backup_dir = args.backup_dir
    if backup_dir is None and args.receipt:
        backup_dir = args.receipt.parent / "backups"
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"cli-config.json.bak-{stamp}"
        backup_path.write_bytes(before_bytes)
        print(f"backup={backup_path}")

    cli_config_path.write_bytes(after_bytes)
    print(f"applied={cli_config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
