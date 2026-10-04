"""Offline mocked tests for predict_dev (no torch, no network, no real model)."""
import json
import sys
import unittest

import predict_dev
from predict_dev import DevPredictor, run_predictions

INPUT = {"objective": "obj", "repository": "repo", "title": "ttl"}
VALID = {"acceptance_checks": ["A"], "constraints": ["B"], "evidence_requirements": ["C"], "non_goals": ["D"]}
VALID_TEXT = json.dumps(VALID)
ROSTER = ["g1"]
PACKETS = {"g1": ["r1", "r2"]}
BASE = 1000


class FakeTokenizer:
    def __init__(self):
        self.template_calls = []
        self.decode_calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.template_calls.append(dict(kwargs, messages=messages))
        return [10, 11, 12]

    def decode(self, ids, **kwargs):
        self.decode_calls.append(kwargs)
        return "".join(chr(i - BASE) for i in ids)


class FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.generate_calls = []

    def generate(self, input_ids, **kwargs):
        self.generate_calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return list(input_ids) + [BASE + ord(c) for c in out]


class PoisonedRow(dict):
    """A dev row whose target raises on any read (input-only enforcement)."""

    def __getitem__(self, key):
        if key == "target":
            raise AssertionError("row target must never be read")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "target":
            raise AssertionError("row target must never be read")
        return super().get(key, default)


def row(rid, group="g1"):
    return {"row_id": rid, "source_group": group, "input": dict(INPUT), "target": dict(VALID)}


def stub_factory(tokenizer, schema):
    return lambda batch_id, input_ids: 0


def predictor(model, tok, source_label="base", max_new_tokens=8, factory=stub_factory):
    p = DevPredictor(model, tok, max_new_tokens=max_new_tokens, source_label=source_label,
                     constraint_factory=factory)
    p._seed = lambda: None  # never import transformers in tests
    return p


def make_factory(callback):
    seen = []

    def factory(tokenizer, schema):
        seen.append((tokenizer, schema))
        return callback

    return factory, seen


class TestPredictDev(unittest.TestCase):
    def test_generate_receives_constraint(self):
        tok, model = FakeTokenizer(), FakeModel([VALID_TEXT, VALID_TEXT])
        cb = lambda batch_id, input_ids: 0
        factory, seen = make_factory(cb)
        res = predictor(model, tok, factory=factory).predict([row("r1"), row("r2")], ROSTER, PACKETS)
        self.assertEqual(len(model.generate_calls), 2)
        for call in model.generate_calls:
            self.assertIs(call["prefix_allowed_tokens_fn"], cb)
            self.assertIs(call["do_sample"], False)
            self.assertEqual(call["max_new_tokens"], 8)
        self.assertIs(seen[0][0], tok)
        self.assertIs(seen[0][1], predict_dev.OUTPUT_SCHEMA)

    def test_template_kwargs(self):
        tok, model = FakeTokenizer(), FakeModel([VALID_TEXT])
        predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        kw = tok.template_calls[0]
        self.assertIs(kw["enable_thinking"], False)
        self.assertIs(kw["add_generation_prompt"], True)
        self.assertIs(kw["tokenize"], True)
        self.assertEqual(kw["return_tensors"], "pt")
        self.assertEqual(kw["messages"], predict_dev.render_messages(INPUT))

    def test_target_never_read(self):
        model = FakeModel([VALID_TEXT])
        poisoned = PoisonedRow(row_id="r1", source_group="g1", input=dict(INPUT))
        res = predictor(model, tok=FakeTokenizer()).predict([poisoned], ROSTER, {"g1": ["r1"]})
        self.assertEqual(res["predictions"]["r1"], VALID)
        self.assertTrue(res["complete"])

    def test_valid_output_keyed_by_row(self):
        tok, model = FakeTokenizer(), FakeModel([VALID_TEXT, VALID_TEXT])
        res = predictor(model, tok, source_label="checkpoint").predict(
            [row("r2"), row("r1")], ROSTER, PACKETS)
        self.assertEqual(sorted(res["predictions"]), ["r1", "r2"])
        self.assertEqual(res["predictions"]["r1"], VALID)
        self.assertEqual(res["schema_invalid_row_ids"], [])
        self.assertTrue(res["complete"])
        self.assertEqual(res["binding"], {
            "source_label": "checkpoint", "seed": predict_dev.DEV_SEED,
            "max_new_tokens": 8, "thinking": False,
            "roster": ["g1"], "row_ids": ["r1", "r2"]})

    def test_invalid_outputs_rejected(self):
        bad = {
            "invalid json": "{not json",
            "duplicate key": '{"acceptance_checks": ["A"], "acceptance_checks": ["A"], "constraints": [], "evidence_requirements": [], "non_goals": []}',
            "nan": '{"acceptance_checks": [NaN], "constraints": [], "evidence_requirements": [], "non_goals": []}',
            "missing field": '{"acceptance_checks": [], "constraints": [], "evidence_requirements": []}',
            "extra field": '{"acceptance_checks": [], "constraints": [], "evidence_requirements": [], "non_goals": [], "extra": []}',
            "wrong type": '{"acceptance_checks": "x", "constraints": [], "evidence_requirements": [], "non_goals": []}',
            "truncated": VALID_TEXT[:-5],
        }
        for name, text in bad.items():
            with self.subTest(name):
                model = FakeModel([text])
                res = predictor(model, FakeTokenizer()).predict([row("r1")], ROSTER, {"g1": ["r1"]})
                self.assertEqual(res["predictions"], {})
                self.assertEqual(res["schema_invalid_row_ids"], ["r1"])
                self.assertIs(res["complete"], False)

    def test_value_errors_before_generate(self):
        with self.assertRaises(ValueError):
            DevPredictor(FakeModel([]), FakeTokenizer(), max_new_tokens=8, source_label="bogus")
        with self.assertRaises(TypeError):  # proves max_new_tokens has no default
            DevPredictor(FakeModel([]), FakeTokenizer(), source_label="base")
        for mnt in (None, 0, -1, True):
            with self.subTest(max_new_tokens=mnt):
                with self.assertRaises(ValueError):
                    DevPredictor(FakeModel([]), FakeTokenizer(), max_new_tokens=mnt, source_label="base")
        rows2 = [row("r1"), row("r2")]
        cases = [
            ([], ROSTER, PACKETS, "empty rows"),
            ([row("r1"), row("r1")], ROSTER, PACKETS, "duplicate row id"),
            (rows2, ROSTER, None, "none manifest"),
            (rows2, ROSTER, {"g1": []}, "empty packet list"),
            (rows2, ROSTER, {}, "missing group key"),
            (rows2, ROSTER, {"g1": ["r1", "r9"]}, "manifest mismatch"),
            ([row("r1"), row("r2", group="gx")], ROSTER, PACKETS, "group outside roster"),
        ]
        for rows, roster, packets, name in cases:
            with self.subTest(name):
                model = FakeModel([])
                with self.assertRaises(ValueError):
                    predictor(model, FakeTokenizer()).predict(rows, roster, packets)
                self.assertEqual(model.generate_calls, [])

    def test_generate_exception_marks_row_invalid(self):
        model = FakeModel([RuntimeError("boom"), VALID_TEXT])
        res = predictor(model, FakeTokenizer()).predict([row("r1"), row("r2")], ROSTER, PACKETS)
        self.assertEqual(res["schema_invalid_row_ids"], ["r1"])
        self.assertEqual(sorted(res["predictions"]), ["r2"])
        self.assertIs(res["complete"], False)

    def test_missing_lmformatenforcer_raises_importerror(self):
        model = FakeModel([VALID_TEXT])
        p = predictor(model, FakeTokenizer(), factory=None)  # lazy default import
        saved = sys.modules.get("lmformatenforcer")
        sys.modules["lmformatenforcer"] = None
        try:
            with self.assertRaises(ImportError):
                p.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        finally:
            if saved is None:
                sys.modules.pop("lmformatenforcer", None)
            else:
                sys.modules["lmformatenforcer"] = saved
        self.assertEqual(model.generate_calls, [])

    def test_feeds_evaluate_primary_score(self):
        import dev_selection
        rows = [row("r1"), row("r2")]
        factory, _ = make_factory(lambda batch_id, input_ids: 0)
        saved_seed = predict_dev._default_seed
        predict_dev._default_seed = lambda seed: None
        try:
            res = run_predictions(rows, FakeModel([VALID_TEXT, VALID_TEXT]), FakeTokenizer(),
                                  frozen_roster=ROSTER, expected_packets=PACKETS,
                                  max_new_tokens=8, source_label="checkpoint",
                                  constraint_factory=factory)
        finally:
            predict_dev._default_seed = saved_seed
        score = dev_selection.evaluate_primary_score(
            rows, res["predictions"], ROSTER, res["schema_invalid_row_ids"], PACKETS)
        self.assertTrue(score["roster_bound"])
        self.assertIs(score["diagnostic_only"], False)
        self.assertIs(score["incomplete"], False)
        self.assertTrue(dev_selection.is_selectable(score))
        self.assertEqual(score["primary"], 1.0)


if __name__ == "__main__":
    unittest.main()
