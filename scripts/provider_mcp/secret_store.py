"""Windows CurrentUser DPAPI storage for the Z.ai advisory MCP credential.

The import command reads the key from stdin, never from argv or MCP settings.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def store_path() -> Path:
    return Path.home() / ".codex" / "provider-mcp-secrets" / "zai.dpapi"


def valid_key(raw: bytes) -> bool:
    return 16 <= len(raw) <= 512 and all(33 <= byte <= 126 for byte in raw)


def import_key(raw: bytes) -> None:
    if os.name != "nt":
        raise ValueError("Windows DPAPI is required")
    key = raw.rstrip(b"\r\n")
    if not valid_key(key):
        raise ValueError("Invalid key format")
    path = store_path()
    if path.exists():
        raise ValueError("A Z.ai credential is already stored; explicit rotation is required")
    import win32crypt  # type: ignore[import-not-found]

    ciphertext = win32crypt.CryptProtectData(key, "Z.ai MCP credential", None, None, None, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(ciphertext)


def load_key() -> str:
    value = os.environ.get("ZAI_API_KEY", "")
    if value:
        if not valid_key(value.encode("utf-8")):
            raise ValueError("Invalid ZAI_API_KEY format")
        return value
    path = store_path()
    if not path.is_file():
        raise ValueError("Z.ai credential is not configured")
    if os.name != "nt":
        raise ValueError("Windows DPAPI is required")
    import win32crypt  # type: ignore[import-not-found]

    try:
        _, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)
    except Exception:
        raise ValueError("Stored Z.ai credential could not be decrypted by this Windows user") from None
    if not valid_key(raw):
        raise ValueError("Stored Z.ai credential has an invalid format")
    return raw.decode("ascii")


if __name__ == "__main__":
    if sys.argv[1:] != ["import-stdin"]:
        raise SystemExit("usage: python -m provider_mcp.secret_store import-stdin")
    try:
        import_key(sys.stdin.buffer.readline())
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print("Z.ai credential stored for the current Windows user")
