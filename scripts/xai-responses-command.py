# [[Codex Director | GPT-6 Astra | 2026-09-23 | 02:04 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 01:43 UTC | BEGIN]]
"""One receipt-backed xAI Responses request for the existing host command seam."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

API = "https://api.x.ai/v1/"
MODEL = "grok-4.6"
OUTPUT_CAP = 8192  # Responses includes reasoning tokens in this cap.
SYSTEM = "You are Grok, assisting the Director. Follow the supplied task authority and result contract. No tools are available."


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def request_body(prompt):
    return {"model": MODEL, "input": [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt}], "max_output_tokens": OUTPUT_CAP,
            "tools": [], "store": False, "service_tier": "default", "truncation": "disabled"}


def post(endpoint, body, key):
    request = Request(API + endpoint, data=canonical(body), method="POST",
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    limit = 64 * 1024 * 1024 if endpoint == "tokenize-text" else 1024 * 1024
    with urlopen(request, timeout=600) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Provider response exceeds capture limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Provider response is not an object")
    return value


def attempt_limits(environment):
    try:
        budget = Decimal(environment["DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD"])
        tokens = environment["DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT"]
        if (not budget.is_finite() or not 0 < budget <= Decimal("0.55")
                or budget * 1000000 != (budget * 1000000).to_integral_value()
                or not tokens.isascii() or not tokens.isdigit() or not 0 < int(tokens) <= 125000):
            raise ValueError("Invalid admitted provider allowance")
    except (KeyError, InvalidOperation, TypeError, AttributeError) as exc:
        raise ValueError("Authoritative admitted provider allowance is required") from exc
    return {"budget_microusd": int(budget * 1000000), "total_token_limit": int(tokens)}


def token_bound(tokenized, *, budget_microusd, total_token_limit):
    if (type(budget_microusd) is not int or not 0 < budget_microusd <= 550000
            or type(total_token_limit) is not int or not 0 < total_token_limit <= 125000):
        raise ValueError("Invalid admitted provider allowance")
    tokens = tokenized.get("token_ids")
    if not isinstance(tokens, list) or not tokens or any(
        not isinstance(token, dict) or type(token.get("token_id")) is not int
        or token["token_id"] < 0 for token in tokens
    ):
        raise ValueError("Complete provider tokenizer evidence is required")
    # Tokenize the entire serialized request, including roles and parameters;
    # retain another 1024 tokens for server framing. No hidden CLI prompt exists.
    inputs = len(tokens) + 1024
    cost = inputs * 2 + OUTPUT_CAP * 6  # short-context standard tariff, micro-USD
    if inputs + OUTPUT_CAP > total_token_limit or cost > budget_microusd:
        raise ValueError("Provider token or monetary allowance exceeded before inference")
    return {"input_token_upper_bound": inputs, "output_token_cap": OUTPUT_CAP,
            "total_token_upper_bound": inputs + OUTPUT_CAP, "reserved_upper_microusd": cost}


def execute(prompt, receipt_dir, key, send=post, *, environment=None):
    limits = attempt_limits(os.environ if environment is None else environment)
    body = request_body(prompt)
    raw = canonical(body)
    if not prompt or len(raw) > 1048576:
        raise ValueError("Missing or oversized native prompt")
    fingerprint = hashlib.sha256(raw).hexdigest()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    stem = receipt_dir / fingerprint
    record = {"request_sha256": fingerprint, "model": MODEL,
              "started_at": datetime.now(timezone.utc).isoformat(), "inference_started": False,
              "automatic_retries": 0, "admitted_limits": limits}
    # Exclusive creation is the replay guard. An uncertain prior call is retained.
    with stem.with_suffix(".intent.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    stem.with_suffix(".request.json").write_bytes(raw)
    try:
        tokenized = send("tokenize-text", {"text": raw.decode("utf-8"), "model": MODEL}, key)
        record.update(token_bound(tokenized, **limits))
        record["tokenizer_response_sha256"] = hashlib.sha256(canonical(tokenized)).hexdigest()
        record["inference_started"] = True
        stem.with_suffix(".preflight.json").write_bytes(canonical(record))
        result = send("responses", body, key)
        stem.with_suffix(".response.json").write_bytes(canonical(result))
        record.update(response_id=result.get("id"), response_status=result.get("status"),
                      usage=result.get("usage"), response_sha256=hashlib.sha256(canonical(result)).hexdigest())
        if result.get("max_output_tokens") != OUTPUT_CAP:
            raise ValueError("Provider did not echo the bound output allowance")
        return result  # Preserve the genuine API envelope, including incomplete results.
    except Exception as exc:
        record["error_type"] = type(exc).__name__  # Never expose credentials in exception text.
        raise
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        stem.with_suffix(".receipt.json").write_bytes(canonical(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        key = os.environ["XAI_API_KEY"]
        if not key or any(character.isspace() for character in key):
            raise ValueError("Missing provider credential")
        result = execute(args.prompt_file.read_text(encoding="utf-8"), args.receipt_dir, key)
        sys.stdout.buffer.write(canonical(result))
    except Exception as exc:
        print("xAI Responses command refused or ambiguous: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 01:43 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 02:04 UTC | END]]
