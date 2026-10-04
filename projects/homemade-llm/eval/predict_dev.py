"""Input-only constrained dev predictor (WO-R5 slice 2).

Renders each dev row's input through the trainer's shared renderer (the scored
target is never read), generates under an lmformatenforcer schema constraint,
and parses strictly: anything not exactly the four string-list fields is
reported schema-invalid, never repaired or fabricated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

TARGET_FIELDS = ("acceptance_checks", "constraints", "evidence_requirements", "non_goals")
DEV_SEED = 1234
SOURCE_LABELS = ("base", "three_shot", "checkpoint")
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {f: {"type": "array", "items": {"type": "string"}} for f in TARGET_FIELDS},
    "required": list(TARGET_FIELDS),
    "additionalProperties": False,
}

try:
    from prompt_template import render_messages
except ImportError:  # resolved from this file: ../train holds the shared renderer
    def render_messages(item: Dict[str, Any], target: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        train_dir = Path(__file__).resolve().parent.parent / "train"
        if str(train_dir) not in sys.path:
            sys.path.insert(0, str(train_dir))
        from prompt_template import render_messages as _render
        return _render(item, target)


def _no_duplicate_keys(pairs: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key in model output: {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite constant in model output: {name}")


def parse_output(text: str) -> Optional[Dict[str, List[str]]]:
    """Strict parse: exactly the four fields, each a list of str, else None."""
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or set(value) != set(TARGET_FIELDS):
        return None
    if any(not isinstance(value[f], list) or not all(isinstance(x, str) for x in value[f])
           for f in TARGET_FIELDS):
        return None
    return {f: list(value[f]) for f in TARGET_FIELDS}


def _default_constraint_factory(tokenizer: Any, schema: Dict[str, Any]) -> Any:
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn
    return build_transformers_prefix_allowed_tokens_fn(tokenizer, JsonSchemaParser(schema))


def _default_seed(seed: int) -> None:
    import transformers
    transformers.set_seed(seed)


class DevPredictor:
    def __init__(self, model: Any, tokenizer: Any, *, max_new_tokens: int, source_label: str,
                 constraint_factory: Optional[Callable[[Any, Dict[str, Any]], Any]] = None) -> None:
        if not isinstance(max_new_tokens, int) or isinstance(max_new_tokens, bool) or max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be a positive int (bool excluded)")
        if not isinstance(source_label, str) or source_label not in SOURCE_LABELS:
            raise ValueError(f"source_label must be one of {SOURCE_LABELS}")
        self.model = model
        self.tokenizer = tokenizer
        self.max_new_tokens = max_new_tokens
        self.source_label = source_label
        self.constraint_factory = constraint_factory or _default_constraint_factory
        self.constraint_fn: Any = None

    def _seed(self) -> None:
        _default_seed(DEV_SEED)

    def _to_inputs(self, input_ids: Any) -> Any:
        return input_ids

    def _get_constraint_fn(self) -> Any:
        if self.constraint_fn is None:
            self.constraint_fn = self.constraint_factory(self.tokenizer, OUTPUT_SCHEMA)
        return self.constraint_fn

    def _generate_text(self, row: Dict[str, Any]) -> str:
        messages = render_messages(row["input"])
        prompt_ids = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            enable_thinking=False, return_tensors="pt")
        input_ids = self._to_inputs(prompt_ids)
        self._seed()
        output_ids = self.model.generate(
            input_ids, max_new_tokens=self.max_new_tokens, do_sample=False,
            prefix_allowed_tokens_fn=self._get_constraint_fn())
        new_ids = list(output_ids)[len(list(prompt_ids)):]
        return self.tokenizer.decode(new_ids, skip_special_tokens=True)

    def predict(self, dev_rows: Any, frozen_roster: Any, expected_packets: Any) -> Dict[str, Any]:
        err = self._validate(dev_rows, frozen_roster, expected_packets)
        if err:
            raise ValueError(err)
        # Built once before the loop so a default-factory ImportError
        # propagates (fail closed) instead of becoming a per-row failure.
        self._get_constraint_fn()
        rows = list(dev_rows)
        predictions: Dict[str, Dict[str, List[str]]] = {}
        invalid: List[str] = []
        for row in rows:
            try:
                parsed = parse_output(self._generate_text(row))
            except Exception:
                parsed = None
            if parsed is None:
                invalid.append(row["row_id"])
            else:
                predictions[row["row_id"]] = parsed
        return {
            "predictions": predictions,
            "schema_invalid_row_ids": sorted(invalid),
            "binding": {
                "source_label": self.source_label,
                "seed": DEV_SEED,
                "max_new_tokens": self.max_new_tokens,
                "thinking": False,
                "roster": list(frozen_roster),
                "row_ids": sorted(r["row_id"] for r in rows),
            },
            "complete": len(predictions) == len(rows),
        }

    @staticmethod
    def _validate(dev_rows: Any, frozen_roster: Any, expected_packets: Any) -> Optional[str]:
        if not isinstance(dev_rows, list) or not dev_rows:
            return "dev_rows must be a non-empty list"
        seen: set = set()
        for row in dev_rows:
            if not isinstance(row, dict):
                return "each dev row must be a dict"
            for key in ("row_id", "source_group"):
                v = row.get(key)
                if not isinstance(v, str) or not v:
                    return f"dev row {key} must be a non-empty str"
            if row["row_id"] in seen:
                return f"duplicate row id: {row['row_id']!r}"
            seen.add(row["row_id"])
        if (not isinstance(frozen_roster, list) or not frozen_roster
                or len(set(frozen_roster)) != len(frozen_roster)
                or any(not isinstance(g, str) or not g for g in frozen_roster)):
            return "frozen_roster must be a non-empty duplicate-free list of non-empty str"
        if not isinstance(expected_packets, dict) or set(expected_packets) != set(frozen_roster):
            return "expected_packets must bind exactly the roster groups"
        for group in frozen_roster:
            ids = expected_packets[group]
            if (not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
                    or any(not isinstance(i, str) or not i for i in ids)):
                return f"expected_packets[{group!r}] must be a non-empty duplicate-free list of ids"
        for row in dev_rows:
            if row["source_group"] not in frozen_roster:
                return f"row {row['row_id']!r} has source_group outside the roster"
            ids = [r["row_id"] for r in dev_rows if r["source_group"] == row["source_group"]]
            want = expected_packets[row["source_group"]]
            if len(ids) != len(want) or set(ids) != set(want):
                return f"rows for group {row['source_group']!r} do not match expected_packets"
        return None


def run_predictions(dev_rows: List[Dict[str, Any]], model: Any, tokenizer: Any, *,
                    frozen_roster: Sequence[str], expected_packets: Dict[str, Sequence[str]],
                    max_new_tokens: int, source_label: str,
                    constraint_factory: Optional[Callable[[Any, Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    predictor = DevPredictor(model, tokenizer, max_new_tokens=max_new_tokens,
                             source_label=source_label, constraint_factory=constraint_factory)
    return predictor.predict(dev_rows, frozen_roster, expected_packets)
