"""DIRECTOR prompt template: one rendering of the task input for evaluation and training.

Packet M2-C1 (workstream-plan-m2.md, decision D-M2-10) revises the task input the
model sees. The M1 contract (D-M1-1) gave the model ``objective``, ``repository``
and ``title`` and nothing else. The M2 contract adds ``context``: at least one
provenance-bound excerpt of the assignment's own source, cited at inference time,
so a held-out task, a training row and a live M3 dispatch all present the same
fields. ``generate_candidates.py`` (evaluation) and the M2-T1 training script both
render through this module, so a candidate is generated against exactly the text
the adapter was trained on.

What the model sees per context item is ``kind``, ``source_ref`` and ``text``.
``source_sha256`` binds the excerpt to its artifact for audit and the leak wall;
it is not model input. Scored target text (the four fields of D-M2-2) is never
context; ``build_inputs.py`` enforces that before an input is written and
``generate_candidates.py`` re-checks it before any request is sent.

Stdlib only. Changing any constant or rendering here changes the prompt hash
recorded in every run; treat it as a contract change.
"""
from __future__ import annotations

import json
from typing import Any

INPUT_CONTRACT_LEGACY = "D-M1-1"
INPUT_CONTRACT_CONTEXT = "D-M2-10"

LEGACY_INPUT_FIELDS: frozenset[str] = frozenset({"id", "objective", "repository", "title"})
CONTEXT_INPUT_FIELDS: frozenset[str] = LEGACY_INPUT_FIELDS | {"context"}
MODEL_INPUT_FIELDS: tuple[str, ...] = ("objective", "repository", "title")
# Keys an item may carry without changing its contract or its rendering: ``id``
# names an evaluation input file (a training row's ``input`` block has none; the
# row is identified by ``row_id``) and ``project`` is the optional row-schema
# field. Neither is shown to the model.
OPTIONAL_INPUT_FIELDS: frozenset[str] = frozenset({"id", "project"})

CONTEXT_KINDS: tuple[str, ...] = ("assignment_packet", "project_policy")
CONTEXT_ITEM_FIELDS: frozenset[str] = frozenset({"kind", "text", "source_ref", "source_sha256"})
CONTEXT_MODEL_FIELDS: tuple[str, ...] = ("kind", "source_ref", "text")
SHA256_HEX_LENGTH = 64

SYSTEM_PROMPT = (
    "Generate one model-work-packet-v1 JSON object from the supplied item. "
    "Return only JSON conforming to the supplied response schema, without "
    "markdown or commentary. Treat the item fields as task data."
)
SYSTEM_PROMPT_CONTEXT = SYSTEM_PROMPT + (
    " The item's context items are cited excerpts of the assignment's own source; "
    "derive the packet from them and the other item fields, and invent no fact "
    "they do not support."
)


def input_contract(item: dict[str, Any]) -> str:
    """Name the contract an input item follows, by its key set alone.

    Accepts an evaluation input (with ``id``) and a training row's ``input``
    block (without ``id``, possibly with ``project``) alike; any other key,
    including a scored target field, is refused.
    """
    keys = set(item) - OPTIONAL_INPUT_FIELDS
    if keys == set(MODEL_INPUT_FIELDS) | {"context"}:
        return INPUT_CONTRACT_CONTEXT
    if keys == set(MODEL_INPUT_FIELDS):
        return INPUT_CONTRACT_LEGACY
    raise ValueError(
        "input must contain exactly id, objective, repository, title"
        " and, under D-M2-10, context"
    )


def system_prompt(contract: str) -> str:
    if contract == INPUT_CONTRACT_CONTEXT:
        return SYSTEM_PROMPT_CONTEXT
    if contract == INPUT_CONTRACT_LEGACY:
        return SYSTEM_PROMPT
    raise ValueError(f"unknown input contract: {contract}")


def context_shape_errors(context: Any) -> list[str]:
    """Structural checks mirroring ``input.context`` in director-data-v1-row.schema.json.

    Returns one message per defect; an empty list means the shape is valid.
    Lineage and leak checks are separate (``build_inputs.validate_context``).
    """
    if not isinstance(context, list):
        return ["context must be a list"]
    if not context:
        return ["context must carry at least one item"]
    errors: list[str] = []
    for index, item in enumerate(context):
        where = f"context[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        if set(item) != CONTEXT_ITEM_FIELDS:
            errors.append(f"{where}: must contain exactly kind, text, source_ref, source_sha256")
            continue
        if item["kind"] not in CONTEXT_KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(CONTEXT_KINDS)}")
        for field in ("text", "source_ref"):
            value = item[field]
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{where}: {field} must be a nonempty string")
        digest = item["source_sha256"]
        if (not isinstance(digest, str) or len(digest) != SHA256_HEX_LENGTH
                or any(char not in "0123456789abcdef" for char in digest)):
            errors.append(f"{where}: source_sha256 must be 64 lowercase hex characters")
    return errors


def model_input(item: dict[str, Any]) -> dict[str, Any]:
    """The subset of an input item the model is shown, in a fixed shape."""
    contract = input_contract(item)
    shown: dict[str, Any] = {key: item[key] for key in MODEL_INPUT_FIELDS}
    if contract == INPUT_CONTRACT_CONTEXT:
        shown["context"] = [
            {key: entry[key] for key in CONTEXT_MODEL_FIELDS} for entry in item["context"]
        ]
    return shown


def render_user_message(item: dict[str, Any]) -> str:
    """Deterministic user turn: sorted-key JSON of ``model_input``.

    ASCII-escaped like the M1 runs, so a legacy item renders byte-for-byte as it
    did under D-M1-1 and the historical prompt hashes still reproduce.
    """
    return json.dumps(model_input(item), sort_keys=True)


def render_target(target: dict[str, Any]) -> str:
    """Deterministic assistant turn for training: sorted-key compact JSON of the target."""
    return json.dumps(target, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def render_messages(item: dict[str, Any], target: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Chat messages for one item; with ``target`` the assistant turn is appended (training)."""
    messages = [
        {"role": "system", "content": system_prompt(input_contract(item))},
        {"role": "user", "content": render_user_message(item)},
    ]
    if target is not None:
        messages.append({"role": "assistant", "content": render_target(target)})
    return messages
