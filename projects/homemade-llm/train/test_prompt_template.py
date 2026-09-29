"""Fixture-only tests: python -m unittest discover -s projects/homemade-llm/train -p test_prompt_template.py -v"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_template as pt  # noqa: E402

ROW_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "director-data-v1-row.schema.json"
SHA = "0123456789abcdef" * 4


def context_item(**overrides):
    item = {"kind": "assignment_packet", "text": "Excerpt of the source packet.",
            "source_ref": "packets/source.md", "source_sha256": SHA}
    item.update(overrides)
    return item


def legacy_item():
    return {"id": "p1", "objective": "Do the thing.", "repository": "org/repo", "title": "Fixture é"}


def context_item_input():
    return dict(legacy_item(), context=[context_item()])


class PromptTemplateTests(unittest.TestCase):
    def test_constants_match_row_schema_context_definition(self):
        schema = json.loads(ROW_SCHEMA.read_text(encoding="utf-8"))
        context = schema["properties"]["input"]["properties"]["context"]
        self.assertEqual(context["minItems"], 1)
        self.assertEqual(tuple(context["items"]["properties"]["kind"]["enum"]), pt.CONTEXT_KINDS)
        self.assertEqual(set(context["items"]["required"]), pt.CONTEXT_ITEM_FIELDS)
        self.assertEqual(set(context["items"]["properties"]), pt.CONTEXT_ITEM_FIELDS)
        self.assertFalse(context["items"]["additionalProperties"])
        self.assertEqual(set(schema["properties"]["input"]["required"]), {*pt.MODEL_INPUT_FIELDS, "context"})

    def test_input_contract_by_key_set(self):
        self.assertEqual(pt.input_contract(legacy_item()), pt.INPUT_CONTRACT_LEGACY)
        self.assertEqual(pt.input_contract(context_item_input()), pt.INPUT_CONTRACT_CONTEXT)
        for bad in (dict(legacy_item(), extra=1), {k: v for k, v in legacy_item().items() if k != "title"},
                    dict(legacy_item(), non_goals=["x"]), dict(context_item_input(), constraints=["x"]), {}):
            with self.subTest(bad=sorted(bad)), self.assertRaisesRegex(ValueError, "exactly"):
                pt.input_contract(bad)

    def test_training_row_input_block_renders_without_id_and_ignores_project(self):
        # A director-data-v1 row's ``input`` has no ``id`` and may carry ``project``;
        # the trainer feeds it straight in and gets the same text an evaluation
        # input with those fields would produce.
        row_input = {k: v for k, v in context_item_input().items() if k != "id"}
        row_input["project"] = "homemade-llm"
        self.assertEqual(pt.input_contract(row_input), pt.INPUT_CONTRACT_CONTEXT)
        self.assertEqual(pt.render_user_message(row_input), pt.render_user_message(context_item_input()))
        self.assertNotIn("homemade-llm", pt.render_user_message(row_input))
        legacy_row_input = {k: v for k, v in legacy_item().items() if k != "id"}
        self.assertEqual(pt.input_contract(legacy_row_input), pt.INPUT_CONTRACT_LEGACY)
        self.assertEqual(pt.render_user_message(legacy_row_input), pt.render_user_message(legacy_item()))
        self.assertEqual([m["role"] for m in pt.render_messages(row_input, {"non_goals": []})],
                         ["system", "user", "assistant"])

    def test_legacy_user_message_is_byte_identical_to_the_m1_formula(self):
        item = legacy_item()
        expected = json.dumps({key: item[key] for key in ("objective", "repository", "title")}, sort_keys=True)
        self.assertEqual(pt.render_user_message(item), expected)
        self.assertNotIn("é", expected)  # ASCII-escaped exactly as M1 sent it
        self.assertEqual(pt.system_prompt(pt.INPUT_CONTRACT_LEGACY), pt.SYSTEM_PROMPT)

    def test_context_user_message_cites_ref_and_text_but_never_the_hash(self):
        rendered = json.loads(pt.render_user_message(context_item_input()))
        self.assertEqual(set(rendered), {"objective", "repository", "title", "context"})
        self.assertEqual(rendered["context"], [
            {"kind": "assignment_packet", "source_ref": "packets/source.md", "text": "Excerpt of the source packet."}
        ])
        self.assertNotIn(SHA, pt.render_user_message(context_item_input()))
        self.assertNotIn("id", rendered)
        self.assertTrue(pt.SYSTEM_PROMPT_CONTEXT.startswith(pt.SYSTEM_PROMPT))
        self.assertEqual(pt.system_prompt(pt.INPUT_CONTRACT_CONTEXT), pt.SYSTEM_PROMPT_CONTEXT)
        with self.assertRaises(ValueError):
            pt.system_prompt("D-M0-0")

    def test_render_messages_for_evaluation_and_training(self):
        item = context_item_input()
        self.assertEqual([m["role"] for m in pt.render_messages(item)], ["system", "user"])
        target = {"non_goals": ["b"], "constraints": [], "acceptance_checks": ["a"], "evidence_requirements": ["e"]}
        messages = pt.render_messages(item, target)
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant"])
        self.assertEqual(messages[0]["content"], pt.SYSTEM_PROMPT_CONTEXT)
        self.assertEqual(messages[1]["content"], pt.render_user_message(item))
        self.assertEqual(json.loads(messages[2]["content"]), target)
        self.assertEqual(messages[2]["content"], json.dumps(target, sort_keys=True, separators=(",", ":")))

    def test_context_shape_errors(self):
        self.assertEqual(pt.context_shape_errors([context_item()]), [])
        cases = {
            "not a list": ({}, "must be a list"),
            "empty": ([], "at least one"),
            "not an object": (["x"], "must be an object"),
            "extra key": ([dict(context_item(), extra=1)], "exactly"),
            "missing key": ([{k: v for k, v in context_item().items() if k != "text"}], "exactly"),
            "bad kind": ([context_item(kind="held_out_reference")], "kind must be"),
            "blank text": ([context_item(text="  ")], "text must be"),
            "blank ref": ([context_item(source_ref="")], "source_ref must be"),
            "short sha": ([context_item(source_sha256="abc")], "64 lowercase hex"),
            "upper sha": ([context_item(source_sha256=SHA.upper())], "64 lowercase hex"),
        }
        for name, (context, fragment) in cases.items():
            with self.subTest(name=name):
                errors = pt.context_shape_errors(context)
                self.assertTrue(errors, name)
                self.assertIn(fragment, errors[0])


if __name__ == "__main__":
    unittest.main()
