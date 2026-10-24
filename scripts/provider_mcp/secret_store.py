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


def _dpapi(protect: bool, data: bytes) -> bytes:
    """Call crypt32 directly so DPAPI needs no third-party package."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output = Blob()
    description = ctypes.c_wchar_p("Z.ai MCP credential") if protect else None
    # CRYPTPROTECT_UI_FORBIDDEN: fail instead of prompting.
    if not function(ctypes.byref(source), description, None, None, None, 0x1, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def import_key(raw: bytes) -> None:
    if os.name != "nt":
        raise ValueError("Windows DPAPI is required")
    key = raw.rstrip(b"\r\n")
    if not valid_key(key):
        raise ValueError("Invalid key format")
    path = store_path()
    if path.exists():
        raise ValueError("A Z.ai credential is already stored; explicit rotation is required")
    ciphertext = _dpapi(True, key)
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
    try:
        raw = _dpapi(False, path.read_bytes())
    except OSError:
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
