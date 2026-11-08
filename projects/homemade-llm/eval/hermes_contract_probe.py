"""Validate strict Hermes-style tool calls without executing tools or exposing content."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path


CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict, "null": type(None),
}


def strict_json(raw: str) -> object:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    def constant(value):
        raise ValueError(f"non-finite number: {value}")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite number: {value}")
        return parsed

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def valid_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    expected = schema.get("type")
    if expected is not None and expected not in TYPE_MAP:
        return False
    if "enum" in schema and not isinstance(schema["enum"], list):
        return False
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not all(
        isinstance(name, str) and valid_schema(child) for name, child in properties.items()
    ):
        return False
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        return False
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        return False
    if "items" in schema and not valid_schema(schema["items"]):
        return False
    return True


def validate_value(value: object, schema: dict, field: str, errors: list[dict]) -> None:
    expected = schema.get("type")
    if expected:
        valid = isinstance(value, TYPE_MAP.get(expected, object))
        if expected in {"integer", "number"} and isinstance(value, bool):
            valid = False
        if not valid:
            errors.append({"code": "ARGUMENT_TYPE", "field": field, "expected": expected})
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"code": "ARGUMENT_ENUM", "field": field})
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append({"code": "MISSING_ARGUMENT", "field": f"{field}.{required}"})
        if schema.get("additionalProperties") is False:
            for child in value.keys() - properties.keys():
                errors.append({"code": "UNDECLARED_ARGUMENT", "field": f"{field}.{child}"})
        for child, child_value in value.items():
            if child in properties:
                validate_value(child_value, properties[child], f"{field}.{child}", errors)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate_value(item, schema["items"], f"{field}[{index}]", errors)


def probe(contract: dict) -> dict:
    errors: list[dict] = []
    declared: dict[str, dict] = {}
    tools = contract.get("tools", [])
    if not isinstance(tools, list):
        tools = []
        errors.append({"code": "TOOL_DECLARATION"})
    for item in tools:
        function = item.get("function") if isinstance(item, dict) and item.get("type") == "function" else None
        if not isinstance(function, dict):
            errors.append({"code": "TOOL_DECLARATION"})
            continue
        name = function.get("name")
        parameters = function.get("parameters", {})
        if (not isinstance(name, str) or not name or name in declared
                or not valid_schema(parameters) or parameters.get("type") not in (None, "object")):
            errors.append({"code": "TOOL_DECLARATION"})
            continue
        declared[name] = parameters

    output = contract.get("assistant_output")
    blocks = CALL.findall(output) if isinstance(output, str) else []
    residue = CALL.sub("", output).strip() if isinstance(output, str) else ""
    if not blocks or residue:
        errors.append({"code": "TOOL_CALL_ENVELOPE"})
    max_calls = contract.get("max_calls", 5)
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls < 0 or len(blocks) > max_calls:
        errors.append({"code": "CALL_LIMIT"})

    calls: dict[int, dict] = {}
    for index, block in enumerate(blocks):
        try:
            call = strict_json(block)
        except (json.JSONDecodeError, ValueError):
            errors.append({"code": "STRICT_JSON", "call_index": index})
            continue
        if (not isinstance(call, dict) or set(call) != {"name", "arguments"}
                or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict)):
            errors.append({"code": "TOOL_CALL_SHAPE", "call_index": index})
            continue
        name, arguments = call["name"], call["arguments"]
        schema = declared.get(name)
        if schema is None:
            errors.append({"code": "UNKNOWN_TOOL", "call_index": index})
            continue
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in arguments:
                errors.append({"code": "MISSING_ARGUMENT", "call_index": index, "field": required})
        if schema.get("additionalProperties") is False:
            for field in arguments.keys() - properties.keys():
                errors.append({"code": "UNDECLARED_ARGUMENT", "call_index": index, "field": field})
        for field, value in arguments.items():
            if field in properties:
                validate_value(value, properties[field], field, errors)
        calls[index] = {"name": name}

    results = contract.get("tool_results", [])
    if not isinstance(results, list):
        results = []
        errors.append({"code": "RESULT_COUNT"})
    effects: set[str] = set()
    bound_indices: set[int] = set()
    for result in results:
        if not isinstance(result, dict):
            errors.append({"code": "RESULT_BINDING"})
            continue
        index, name, effect_id = result.get("call_index"), result.get("name"), result.get("effect_id")
        if (not isinstance(index, int) or isinstance(index, bool) or index not in calls
                or index in bound_indices or calls[index]["name"] != name):
            errors.append({"code": "RESULT_BINDING"})
        else:
            bound_indices.add(index)
        if not isinstance(effect_id, str) or not effect_id or effect_id in effects:
            errors.append({"code": "EFFECT_IDENTITY"})
        else:
            effects.add(effect_id)
    if len(results) != len(blocks):
        errors.append({"code": "RESULT_COUNT"})

    final_effects = contract.get("final_effect_ids", [])
    valid_final_effects = isinstance(final_effects, list) and all(isinstance(effect, str) for effect in final_effects)
    if (not valid_final_effects or len(final_effects) != len(set(final_effects))
            or (effects and not final_effects)
            or any(effect not in effects for effect in final_effects)):
        errors.append({"code": "FINAL_EVIDENCE"})

    return {
        "schema": "director.hermes-contract-probe/v1",
        "status": "PASS" if not errors else "FAIL",
        "tool_count": len(declared), "call_count": len(blocks),
        "result_count": len(results), "effect_count": len(effects),
        "errors": errors, "raw_content_emitted": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = args.input.read_bytes()
    contract = strict_json(raw.decode("utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("contract must be a JSON object")
    result = probe(contract)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "errors": len(result["errors"]), "output": str(args.output)}))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
