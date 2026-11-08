"""Fixture-only tests: python -m unittest discover -s projects/homemade-llm/eval -p test_hermes_contract_probe.py -v"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("hermes_contract_probe", Path(__file__).with_name("hermes_contract_probe.py"))
hermes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hermes)


class HermesContractProbeTests(unittest.TestCase):
    def contract(self, output='<tool_call>{"name":"lookup","arguments":{"query":"status"}}</tool_call>'):
        return {
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"], "additionalProperties": False,
            }}}],
            "assistant_output": output,
            "tool_results": [{"call_index": 0, "name": "lookup", "effect_id": "effect-1"}],
            "final_effect_ids": ["effect-1"], "max_calls": 5,
        }

    def test_accepts_strict_bound_contract(self):
        self.assertEqual(hermes.probe(self.contract())["status"], "PASS")

    def test_rejects_non_json_unknown_and_missing_arguments(self):
        cases = {
            "STRICT_JSON": "<tool_call>{'name':'lookup','arguments':{}}</tool_call>",
            "UNKNOWN_TOOL": '<tool_call>{"name":"delete","arguments":{"query":"status"}}</tool_call>',
            "MISSING_ARGUMENT": '<tool_call>{"name":"lookup","arguments":{}}</tool_call>',
        }
        for expected, output in cases.items():
            with self.subTest(expected=expected):
                codes = {error["code"] for error in hermes.probe(self.contract(output))["errors"]}
                self.assertIn(expected, codes)

    def test_rejects_duplicate_keys_nonfinite_and_stale_evidence(self):
        for output in (
            '<tool_call>{"name":"lookup","name":"lookup","arguments":{"query":"status"}}</tool_call>',
            '<tool_call>{"name":"lookup","arguments":{"query":NaN}}</tool_call>',
            '<tool_call>{"name":"lookup","arguments":{"query":1e400}}</tool_call>',
        ):
            with self.subTest(output=output):
                self.assertIn("STRICT_JSON", {e["code"] for e in hermes.probe(self.contract(output))["errors"]})
        contract = self.contract()
        contract["final_effect_ids"] = ["stale"]
        self.assertIn("FINAL_EVIDENCE", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_malformed_tool_schemas_fail_without_raising(self):
        for field, value in (("properties", []), ("required", True), ("required", None)):
            with self.subTest(field=field, value=value):
                contract = self.contract()
                contract["tools"][0]["function"]["parameters"][field] = value
                self.assertIn("TOOL_DECLARATION", {e["code"] for e in hermes.probe(contract)["errors"]})

        for function in (None, [], "lookup"):
            with self.subTest(function=function):
                contract = self.contract()
                contract["tools"][0]["function"] = function
                self.assertIn("TOOL_DECLARATION", {e["code"] for e in hermes.probe(contract)["errors"]})

        for schema in (None, {"type": "not-a-json-type"}, {"enum": "not-a-list"}):
            with self.subTest(schema=schema):
                contract = self.contract()
                contract["tools"][0]["function"]["parameters"]["properties"]["query"] = schema
                self.assertIn("TOOL_DECLARATION", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_validates_nested_object_and_array_arguments(self):
        contract = self.contract(
            '<tool_call>{"name":"lookup","arguments":{"query":{"count":"wrong","extra":true}}}</tool_call>'
        )
        contract["tools"][0]["function"]["parameters"]["properties"]["query"] = {
            "type": "object", "properties": {"count": {"type": "integer"}},
            "required": ["count"], "additionalProperties": False,
        }
        codes = {e["code"] for e in hermes.probe(contract)["errors"]}
        self.assertTrue({"ARGUMENT_TYPE", "UNDECLARED_ARGUMENT"} <= codes)

        contract = self.contract('<tool_call>{"name":"lookup","arguments":{"query":[1,"wrong"]}}</tool_call>')
        contract["tools"][0]["function"]["parameters"]["properties"]["query"] = {
            "type": "array", "items": {"type": "integer"},
        }
        self.assertIn("ARGUMENT_TYPE", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_effects_require_nonempty_unique_final_evidence(self):
        for final_effects in (None, [], ["effect-1", "effect-1"]):
            with self.subTest(final_effects=final_effects):
                contract = self.contract()
                if final_effects is None:
                    contract.pop("final_effect_ids")
                else:
                    contract["final_effect_ids"] = final_effects
                self.assertIn("FINAL_EVIDENCE", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_result_binding_uses_original_call_index(self):
        contract = self.contract('<tool_call>{bad}</tool_call>\n<tool_call>{"name":"lookup","arguments":{"query":"status"}}</tool_call>')
        contract["tool_results"] = [{"call_index": 1, "name": "lookup", "effect_id": "effect-1"}]
        codes = {error["code"] for error in hermes.probe(contract)["errors"]}
        self.assertIn("STRICT_JSON", codes)
        self.assertNotIn("RESULT_BINDING", codes)

    def test_rejects_duplicate_result_binding_and_malformed_names_and_evidence(self):
        output = "\n".join([
            '<tool_call>{"name":"lookup","arguments":{"query":"one"}}</tool_call>',
            '<tool_call>{"name":"lookup","arguments":{"query":"two"}}</tool_call>',
        ])
        contract = self.contract(output)
        contract["tool_results"] = [
            {"call_index": 0, "name": "lookup", "effect_id": "effect-1"},
            {"call_index": 0, "name": "lookup", "effect_id": "effect-2"},
        ]
        contract["final_effect_ids"] = ["effect-1", "effect-2"]
        self.assertIn("RESULT_BINDING", {e["code"] for e in hermes.probe(contract)["errors"]})

        contract = self.contract('<tool_call>{"name":[],"arguments":{"query":"status"}}</tool_call>')
        self.assertIn("TOOL_CALL_SHAPE", {e["code"] for e in hermes.probe(contract)["errors"]})

        contract = self.contract()
        contract["final_effect_ids"] = [{}]
        self.assertIn("FINAL_EVIDENCE", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_cli_writes_hashed_receipt_and_preserves_existing_on_bad_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "contract.json", root / "receipt.json"
            source.write_text(json.dumps(self.contract()), encoding="utf-8")
            self.assertEqual(hermes.main(["--input", str(source), "--output", str(output)]), 0)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PASS")
            before = output.read_bytes()
            source.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                hermes.main(["--input", str(source), "--output", str(output)])
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
