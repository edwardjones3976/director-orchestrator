"""Fixture-only tests: python -m unittest discover -s projects/homemade-llm/eval -p test_hermes_contract_probe.py -v"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hermes_contract_probe as hermes


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
        ):
            with self.subTest(output=output):
                self.assertIn("STRICT_JSON", {e["code"] for e in hermes.probe(self.contract(output))["errors"]})
        contract = self.contract()
        contract["final_effect_ids"] = ["stale"]
        self.assertIn("FINAL_EVIDENCE", {e["code"] for e in hermes.probe(contract)["errors"]})

    def test_result_binding_uses_original_call_index(self):
        contract = self.contract('<tool_call>{bad}</tool_call>\n<tool_call>{"name":"lookup","arguments":{"query":"status"}}</tool_call>')
        contract["tool_results"] = [{"call_index": 1, "name": "lookup", "effect_id": "effect-1"}]
        codes = {error["code"] for error in hermes.probe(contract)["errors"]}
        self.assertIn("STRICT_JSON", codes)
        self.assertNotIn("RESULT_BINDING", codes)

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
