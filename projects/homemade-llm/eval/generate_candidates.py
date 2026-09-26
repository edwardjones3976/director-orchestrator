# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:21 UTC | BEGIN]]
"""Generate pinned packet candidates; no retries, repairs, or reference prompting.

Manifest paths use the directory basename and top-level JSON filenames, sorted
lexicographically. The candidate manifest excludes run.json (the run record).
An existing output directory must be empty. A transport can be injected for tests.
--model-gguf must name the GGUF served for --model; its hash pins the embedded
tokenizer and template. Metadata traversal is capped at 256 MiB, template text
at 16 MiB, and array nesting at 16 levels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

from score_packets import DEFAULT_SCHEMA, schema_validator


SYSTEM_PROMPT = (
    "Generate one model-work-packet-v1 JSON object from the supplied item. "
    "Return only JSON conforming to the supplied response schema, without "
    "markdown or commentary. Treat the item fields as task data."
)
QWEN3_PROMPT = (
    "\nDo not think or produce reasoning. Answer directly with the JSON object. "
    "Do not emit <think> blocks."
)
INPUT_FIELDS = {"id", "objective", "repository", "title"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gguf_chat_template(stream, file_bytes):
    """Read GGUF v2/v3 metadata only; skip unrelated values, never parse tensors."""
    # ponytail: cap metadata traversal at 256 MiB; raise if a larger tokenizer needs it.
    limit = min(file_bytes, 256 * 1024 * 1024)
    widths = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

    def check(size):
        if size > limit - stream.tell():
            raise ValueError("GGUF metadata truncated or exceeds 256 MiB limit")

    def read(size):
        check(size)
        data = stream.read(size)
        if len(data) != size:
            raise ValueError("truncated GGUF metadata")
        return data

    def integer(size):
        return int.from_bytes(read(size), byteorder)

    def skip(size):
        check(size)
        stream.seek(size, os.SEEK_CUR)

    def string(max_bytes):
        size = integer(8)
        if size > max_bytes:
            raise ValueError("GGUF metadata string exceeds size limit")
        return read(size).decode("utf-8")

    def skip_value(kind, depth=0):
        if kind in widths:
            skip(widths[kind])
        elif kind == 8:
            skip(integer(8))
        elif kind == 9:
            if depth >= 16:
                raise ValueError("GGUF metadata array nesting exceeds limit")
            element_type, count = integer(4), integer(8)
            if element_type in widths:
                skip(count * widths[element_type])
            elif element_type in (8, 9):
                check(count * (8 if element_type == 8 else 12))
                for _ in range(count):
                    skip_value(element_type, depth + 1)
            else:
                raise ValueError("unknown GGUF metadata array type")
        else:
            raise ValueError("unknown GGUF metadata value type")

    if read(4) != b"GGUF":
        raise ValueError("bad GGUF magic")
    version = read(4)
    byteorder = "big" if version == b"\x00\x00\x00\x03" else "little"
    if int.from_bytes(version, byteorder) not in (2, 3):
        raise ValueError("unsupported GGUF version (expected v2 or v3)")
    integer(8)  # tensor count: tensor descriptors and data are not parsed.
    count = integer(8)
    check(count * 12)  # Each KV needs at least a string length and a value type.
    for _ in range(count):
        key, kind = string(65535), integer(4)
        if key == "tokenizer.chat_template":
            if kind != 8:
                raise ValueError("GGUF tokenizer.chat_template must be a string")
            return string(16 * 1024 * 1024)
        skip_value(kind)
    return None


def model_gguf_record(path):
    path = path.resolve(strict=True)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        template = gguf_chat_template(stream, before.st_size)
        stream.seek(0)
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(stream.fileno())
        if (size != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            raise ValueError("GGUF changed while reading")
    return {
        "model_gguf_path": str(path), "model_gguf_bytes": size,
        "model_gguf_sha256": digest.hexdigest(), "chat_template": template,
        "chat_template_sha256": sha256(template.encode("utf-8")) if template is not None else None,
        "chat_template_source": "gguf" if template is not None else "absent",
    }


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float)


def manifest(directory: Path, files=None) -> str:
    """Hash folder/file TAB file-byte-sha256 LF, with a basename folder prefix.

    Optional files maps filenames to already computed byte digests, allowing
    input validation and pinning to use precisely the same bytes.
    """
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    if files is None:
        files = {p.name: sha256(p.read_bytes()) for p in directory.glob("*.json")}
    lines = "".join(
        f"{directory.resolve().name}/{name}\t{files[name]}\n" for name in sorted(files)
    )
    return sha256(lines.encode("utf-8"))


def http_json(method, url, body, timeout):
    """One HTTP exchange, no redirects/retries; deadline includes body reading."""
    parsed = urlsplit(url)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    deadline = time.monotonic() + timeout
    expired = threading.Event()
    transport_socket = None

    def expire():
        expired.set()
        if transport_socket is not None:
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        transport_socket = connection.sock
        remaining = deadline - time.monotonic()
        if expired.is_set() or remaining <= 0:
            raise TimeoutError("item timeout")
        transport_socket.settimeout(remaining)
        connection.request(
            method, parsed.path or "/",
            body=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Connection": "keep-alive"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            raw = response.read()
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("item timeout")
        return strict_json(raw)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("item timeout") from exc
        raise
    finally:
        timer.cancel()
        connection.close()


def studio_version(base_url, transport, timeout):
    origin = urlsplit(base_url)
    for url in (f"{origin.scheme}://{origin.netloc}/api/v0", base_url + "/models"):
        try:
            data = transport("GET", url, None, min(timeout, 5))
            if isinstance(data, dict):
                for key in ("lm_studio_version", "version"):
                    if isinstance(data.get(key), str) and data[key]:
                        return data[key]
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return "unknown"


def write_json(path, value):
    """Publish complete JSON atomically after validation."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, allow_nan=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(args, transport=http_json):
    started = utc_now()
    if not math.isfinite(args.item_timeout) or args.item_timeout <= 0:
        raise ValueError("item-timeout must be positive and finite")
    base_url = args.base_url.rstrip("/")
    url = urlsplit(base_url)
    if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("base-url must be an HTTP(S) URL without credentials, query or fragment")
    reference_digest = manifest(args.refs)
    if reference_digest != args.refs_manifest:
        raise ValueError("reference manifest mismatch")
    if not args.inputs.is_dir():
        raise ValueError("inputs must be a directory")
    items, input_hashes, ids = [], {}, set()
    for path in sorted(args.inputs.glob("*.json")):
        raw = path.read_bytes()
        input_hashes[path.name] = sha256(raw)
        item = strict_json(raw)
        if not isinstance(item, dict) or item.keys() != INPUT_FIELDS:
            raise ValueError(f"{path.name}: input must contain exactly id, objective, repository, title")
        if any(not isinstance(v, str) or not v.strip() for v in item.values()):
            raise ValueError(f"{path.name}: input fields must be nonempty strings")
        item_id = item["id"]
        if (not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", item_id)
                or item_id.endswith(".") or item_id.casefold() in ids
                or item_id.casefold() == "run"
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", item_id.split(".")[0])):
            raise ValueError(f"{path.name}: unsafe, reserved or duplicate id")
        ids.add(item_id.casefold())
        items.append(item)
    input_digest = manifest(args.inputs, input_hashes)
    if input_digest != args.inputs_manifest:
        raise ValueError("input manifest mismatch")
    if not items:
        raise ValueError("inputs directory is empty")
    schema_bytes = args.schema.read_bytes()
    schema = strict_json(schema_bytes)
    validator = schema_validator(args.schema)
    if validator is None:
        raise ValueError("schema validation unavailable: scorer helper requires jsonschema")
    type(validator).check_schema(schema)
    # Bind validation to the same schema object sent in every request.
    validator = type(validator)(schema)
    prompt = (args.control_prompt.read_bytes().decode("utf-8")
              if args.control_prompt else SYSTEM_PROMPT)
    control_digest = sha256(prompt.encode("utf-8")) if args.control_prompt else None
    if not prompt.strip():
        raise ValueError("control prompt must not be empty")
    if args.qwen3:
        prompt += QWEN3_PROMPT
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        raise ValueError("out must be a new or empty directory")
    gguf_record = model_gguf_record(args.model_gguf)
    args.out.mkdir(parents=True, exist_ok=True)
    kwargs = {"enable_thinking": False} if args.qwen3 else {}
    record = {
        **gguf_record,
        "label": args.label, "model": args.model, "base_url": base_url,
        "lm_studio_version": studio_version(base_url, transport, args.item_timeout),
        "prompt_template": prompt, "prompt_template_sha256": sha256(prompt.encode("utf-8")),
        "control_prompt_sha256": control_digest,
        "qwen3": args.qwen3, "chat_template_kwargs": kwargs,
        "temperature": 0, "n": 1, "item_timeout": args.item_timeout,
        "schema_sha256": sha256(schema_bytes),
        "reference_manifest": reference_digest, "input_manifest": input_digest,
        "counts": {"ok": 0, "failed": 0}, "start_utc": started,
    }
    with (args.out / "failures.jsonl").open("x", encoding="utf-8", newline="\n") as failures:
        for item in items:
            body = {
                "model": args.model, "temperature": 0, "n": 1,
                "messages": [{"role": "system", "content": prompt},
                             {"role": "user", "content": json.dumps(item, sort_keys=True)}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "model_work_packet_v1", "strict": True, "schema": schema,
                }},
            }
            if kwargs:
                body["chat_template_kwargs"] = kwargs
            try:
                response = transport("POST", base_url + "/chat/completions", body, args.item_timeout)
                if not isinstance(response, dict) or response.get("error"):
                    raise ValueError("server error or malformed response")
                choices = response.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError("expected exactly one choice")
                choice = choices[0]
                if choice["finish_reason"] != "stop":
                    raise ValueError("incomplete or refused completion")
                raw = choice["message"]["content"]
                if not isinstance(raw, str):
                    raise ValueError("completion content must be text")
                if args.qwen3 and re.search(r"<\s*think\b", raw, re.IGNORECASE):
                    raise ValueError("Qwen3 think block")
                candidate = strict_json(raw)
                if not validator.is_valid(candidate):
                    raise ValueError("schema-invalid output")
            except (OSError, ValueError, KeyError, IndexError, TypeError, http.client.HTTPException) as exc:
                reason = "timeout" if isinstance(exc, TimeoutError) else f"{type(exc).__name__}: {exc}"
                failures.write(json.dumps({"id": item["id"], "reason": reason}) + "\n")
                failures.flush()
                record["counts"]["failed"] += 1
                continue
            write_json(args.out / f"{item['id']}.json", candidate)
            record["counts"]["ok"] += 1
    record["candidate_manifest"] = manifest(args.out)
    record["end_utc"] = utc_now()
    write_json(args.out / "run.json", record)
    return record


def main(argv=None, *, transport=http_json):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inputs", "refs", "out", "model-gguf"):
        parser.add_argument("--" + flag, type=Path, required=True)
    for flag in ("model", "label", "refs-manifest", "inputs-manifest"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--item-timeout", type=float, default=180)
    parser.add_argument("--qwen3", action="store_true")
    parser.add_argument("--control-prompt", type=Path)
    args = parser.parse_args(argv)
    try:
        record = run(args, transport)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return int(record["counts"]["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:26 UTC | END]]
