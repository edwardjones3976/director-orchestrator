"""Offline mocked tests for predict_dev (no torch, no network, no real model)."""
import hashlib
import json
import sys
import types
import unittest
from unittest.mock import patch

import predict_dev
from predict_dev import DevPredictor, UnsupportedShape, run_predictions

SYNTHETIC_SOURCE = "Synthetic assignment: itemise the cited requirements."
INPUT = {
    "objective": "obj", "repository": "repo", "title": "ttl",
    "context": [{
        "kind": "assignment_packet", "source_ref": "synthetic://dev-fixture#L1",
        "text": SYNTHETIC_SOURCE,
        "source_sha256": hashlib.sha256(SYNTHETIC_SOURCE.encode("utf-8")).hexdigest(),
    }],
}
VALID = {"acceptance_checks": ["A"], "constraints": ["B"], "evidence_requirements": ["C"], "non_goals": ["D"]}
VALID_TEXT = json.dumps(VALID)
ROSTER = ["g1"]
PACKETS = {"g1": ["r1", "r2"]}
BASE = 1000

GOOD_BASE_REVISION = "a" * 40
GOOD_IDENTITY_BASE = {
    "base_revision": GOOD_BASE_REVISION,
    "weights_manifest_sha256": "b" * 64,
    "template_sha256": predict_dev._sha256_hex(predict_dev._default_template_source()),
}


def good_identity(source_label="base"):
    identity = dict(GOOD_IDENTITY_BASE)
    if source_label == "checkpoint":
        identity["adapter_sha256"] = "c" * 64
    return identity


class RawOutput:
    """Wraps a literal value so FakeModel.generate returns it unchanged (for
    crafting malformed generate() outputs without the normal id-construction)."""

    def __init__(self, value):
        self.value = value


class FakeTokenizer:
    def __init__(self, encoded=None):
        self.template_calls = []
        self.decode_calls = []
        self.encoded = [[10, 11, 12]] if encoded is None else encoded

    def apply_chat_template(self, messages, **kwargs):
        self.template_calls.append(dict(kwargs, messages=messages))
        return self.encoded

    def decode(self, ids, **kwargs):
        self.decode_calls.append((list(ids), kwargs))
        return "".join(chr(i - BASE) for i in ids)


class FakeModel:
    def __init__(self, outputs, config=None, sequences_mode=False):
        self.outputs = list(outputs)
        self.generate_calls = []
        self.config = types.SimpleNamespace(_commit_hash=GOOD_BASE_REVISION) if config is None else config
        self.sequences_mode = sequences_mode
        self.device = "cpu"

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, RawOutput):
            return out.value
        prompt_row = list(kwargs["input_ids"][0])
        full = prompt_row + [BASE + ord(c) for c in out]
        return types.SimpleNamespace(sequences=[full]) if self.sequences_mode else [full]



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


def predictor(model, tok, source_label="base", max_new_tokens=8, factory=stub_factory, identity=None):
    p = DevPredictor(model, tok, max_new_tokens=max_new_tokens, source_label=source_label,
                     identity=identity if identity is not None else good_identity(source_label),
                     constraint_factory=factory)
    p._seed = lambda: None  # never import transformers in tests
    return p


def make_factory(callback):
    seen = []

    def factory(tokenizer, schema):
        seen.append((tokenizer, schema))
        return callback

    return factory, seen


class OfflinePredictorTestCase(unittest.TestCase):
    """Scope a dependency stub to each mocked test; preserve production preflight."""

    def setUp(self):
        fake_transformers = types.ModuleType("transformers")
        fake_transformers.set_seed = lambda seed: None
        stub = patch.dict(sys.modules, {"transformers": fake_transformers})
        stub.start()
        self.addCleanup(stub.stop)


class TestPredictDev(OfflinePredictorTestCase):
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
        identity = good_identity("checkpoint")
        res = predictor(model, tok, source_label="checkpoint", identity=identity).predict(
            [row("r2"), row("r1")], ROSTER, PACKETS)
        self.assertEqual(sorted(res["predictions"]), ["r1", "r2"])
        self.assertEqual(res["predictions"]["r1"], VALID)
        self.assertEqual(res["schema_invalid_row_ids"], [])
        self.assertTrue(res["complete"])
        self.assertEqual(res["binding"], {
            "source_label": "checkpoint", "seed": predict_dev.DEV_SEED,
            "max_new_tokens": 8, "thinking": False,
            "roster": ["g1"], "row_ids": ["r1", "r2"],
            "identity": identity,
            "expected_packets_sha256": predict_dev._sha256_hex(predict_dev._canonical_json(PACKETS)),
            "input_sha256": {
                "r1": predict_dev._sha256_hex(predict_dev._canonical_json(INPUT)),
                "r2": predict_dev._sha256_hex(predict_dev._canonical_json(INPUT)),
            }})

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
            DevPredictor(FakeModel([]), FakeTokenizer(), max_new_tokens=8, source_label="bogus",
                         identity={})
        with self.assertRaises(TypeError):  # proves max_new_tokens has no default
            DevPredictor(FakeModel([]), FakeTokenizer(), source_label="base", identity=good_identity())
        for mnt in (None, 0, -1, True):
            with self.subTest(max_new_tokens=mnt):
                with self.assertRaises(ValueError):
                    DevPredictor(FakeModel([]), FakeTokenizer(), max_new_tokens=mnt, source_label="base",
                                 identity=good_identity())
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

    def test_infrastructure_failures_propagate(self):
        # generate raises
        model = FakeModel([RuntimeError("boom")])
        with self.assertRaises(RuntimeError) as cm:
            predictor(model, FakeTokenizer()).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(str(cm.exception), "boom")

        # tokenizer apply_chat_template raises
        class RaisingTokenizer(FakeTokenizer):
            def apply_chat_template(self, messages, **kwargs):
                raise ValueError("tok_boom")
        with self.assertRaises(ValueError) as cm:
            predictor(FakeModel([VALID_TEXT]), RaisingTokenizer()).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(str(cm.exception), "tok_boom")

        # tokenizer decode raises
        class RaisingDecodeTokenizer(FakeTokenizer):
            def decode(self, ids, **kwargs):
                raise TypeError("decode_boom")
        with self.assertRaises(TypeError) as cm:
            predictor(FakeModel([VALID_TEXT]), RaisingDecodeTokenizer()).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(str(cm.exception), "decode_boom")

        # seed hook raises ImportError
        p_seed = predictor(FakeModel([VALID_TEXT]), FakeTokenizer())
        def raising_seed():
            raise ImportError("seed_boom")
        p_seed._seed = raising_seed
        with self.assertRaises(ImportError) as cm:
            p_seed.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(str(cm.exception), "seed_boom")

    def test_missing_transformers_caught_by_preflight_with_zero_generate_calls(self):
        model = FakeModel([VALID_TEXT])
        # _seed mock isn't sufficient since preflight explicitly checks transformers
        p = predictor(model, FakeTokenizer(), factory=None)
        saved = sys.modules.get("transformers")
        sys.modules["transformers"] = None
        try:
            with self.assertRaises(predict_dev.UnsupportedRuntime) as cm:
                p.predict([row("r1")], ROSTER, {"g1": ["r1"]})
            self.assertIn("Missing dependency: transformers", str(cm.exception))
        finally:
            if saved is None:
                sys.modules.pop("transformers", None)
            else:
                sys.modules["transformers"] = saved
        self.assertEqual(model.generate_calls, [])

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
                                  identity=good_identity("checkpoint"),
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


class TestBatchedGenerationPath(OfflinePredictorTestCase):
    def test_mapping_input_ids_and_attention_mask_forwarded(self):
        tok = FakeTokenizer(encoded={"input_ids": [[10, 11, 12]], "attention_mask": [[1, 1, 1]]})
        model = FakeModel([VALID_TEXT])
        res = predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(res["predictions"]["r1"], VALID)
        call = model.generate_calls[0]
        self.assertEqual(call["input_ids"], [[10, 11, 12]])
        self.assertEqual(call["attention_mask"], [[1, 1, 1]])

    def test_nested_list_input_accepted(self):
        tok = FakeTokenizer(encoded=[[10, 11, 12]])
        model = FakeModel([VALID_TEXT])
        res = predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(res["predictions"]["r1"], VALID)

    def test_flat_list_input_refused_with_zero_generate_and_decode(self):
        tok = FakeTokenizer(encoded=[10, 11, 12])
        model = FakeModel([VALID_TEXT])
        with self.assertRaises(UnsupportedShape):
            predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(model.generate_calls, [])
        self.assertEqual(tok.decode_calls, [])

    def test_device_resolution_and_movement(self):
        class FakeTensor:
            def __init__(self, value, device=None):
                self.value = value
                self.device = device
                self.to_calls = []
            def to(self, dev):
                self.to_calls.append(dev)
                return self
            def tolist(self):
                return self.value
            def __getitem__(self, index):
                return self.value[index]

        tok = FakeTokenizer(encoded={"input_ids": FakeTensor([[10, 11, 12]]), "attention_mask": FakeTensor([[1, 1, 1]])})
        
        # 1. model.device
        model1 = FakeModel([VALID_TEXT])
        model1.device = "cuda:0"
        p1 = predictor(model1, tok)
        p1.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(tok.encoded["input_ids"].to_calls, ["cuda:0"])
        self.assertEqual(tok.encoded["attention_mask"].to_calls, ["cuda:0"])

        # 2. get_input_embeddings().weight.device
        model2 = FakeModel([VALID_TEXT])
        del model2.device
        model2.get_input_embeddings = lambda: types.SimpleNamespace(weight=FakeTensor(None, device="cpu:0"))
        tok.encoded["input_ids"].to_calls.clear()
        tok.encoded["attention_mask"].to_calls.clear()
        p2 = predictor(model2, tok)
        p2.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(tok.encoded["input_ids"].to_calls, ["cpu:0"])

        # 3. Neither raises UnsupportedRuntime
        model3 = FakeModel([VALID_TEXT])
        del model3.device
        p3 = predictor(model3, tok)
        with self.assertRaises(predict_dev.UnsupportedRuntime) as cm3:
            p3.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertIn("Cannot determine model device", str(cm3.exception))

        # 4. Meta device raises
        model4 = FakeModel([VALID_TEXT])
        model4.device = "meta"
        p4 = predictor(model4, tok)
        with self.assertRaises(predict_dev.UnsupportedRuntime) as cm4:
            p4.predict([row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertIn("Model is on meta device", str(cm4.exception))

    def test_output_as_nested_list_and_as_sequences_object(self):
        for sequences_mode in (False, True):
            with self.subTest(sequences_mode=sequences_mode):
                tok, model = FakeTokenizer(), FakeModel([VALID_TEXT], sequences_mode=sequences_mode)
                res = predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
                self.assertEqual(res["predictions"]["r1"], VALID)

    def test_continuation_decoding_returns_only_new_tokens(self):
        tok, model = FakeTokenizer(), FakeModel([VALID_TEXT])
        predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        decoded_ids, _ = tok.decode_calls[0]
        self.assertEqual(decoded_ids, [BASE + ord(c) for c in VALID_TEXT])

    def test_unsupported_input_shapes_raise_before_any_generate_call(self):
        cases = {
            "batch size not 1": [[1, 2], [3, 4]],
            "flat list": [1, 2, 3],
            "non-integer ids": [[1, "x"]],
            "empty prompt": [[]],
        }
        for name, encoded in cases.items():
            with self.subTest(name):
                tok = FakeTokenizer(encoded=encoded)
                model = FakeModel([VALID_TEXT])
                with self.assertRaises(UnsupportedShape):
                    predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
                self.assertEqual(model.generate_calls, [])
                self.assertEqual(tok.decode_calls, [])

    def test_unsupported_output_shapes_raise_with_zero_decode_calls(self):
        cases = {
            "batch size not 1": [[10, 11, 12, 1], [10, 11, 12, 1]],
            "flat sequence": [10, 11, 12, 1],
            "non-integer ids": [[10, 11, 12, "x"]],
            "empty sequence": [[]],
        }
        for name, sequences in cases.items():
            with self.subTest(name):
                tok, model = FakeTokenizer(), FakeModel([RawOutput(sequences)])
                with self.assertRaises(UnsupportedShape):
                    predictor(model, tok).predict([row("r1")], ROSTER, {"g1": ["r1"]})
                self.assertEqual(len(model.generate_calls), 1)
                self.assertEqual(tok.decode_calls, [])

    def test_shape_defect_is_a_run_refusal_not_a_row_invalid(self):
        tok = FakeTokenizer()
        model = FakeModel([RawOutput([10, 11, 12, "bad"]), VALID_TEXT])
        with self.assertRaises(UnsupportedShape):
            predictor(model, tok).predict([row("r1"), row("r2")], ROSTER, PACKETS)
        self.assertEqual(len(model.generate_calls), 1)  # second row never attempted


class TestFullRosterValidation(OfflinePredictorTestCase):
    ROSTER2 = ["g1", "g2"]
    PACKETS2 = {"g1": ["r1", "r2"], "g2": ["r3", "r4"]}

    def _assert_refused_before_anything(self, dev_rows, roster, packets):
        model = FakeModel([])
        factory, seen = make_factory(lambda batch_id, input_ids: 0)
        with self.assertRaises(ValueError):
            predictor(model, FakeTokenizer(), factory=factory).predict(dev_rows, roster, packets)
        self.assertEqual(seen, [])
        self.assertEqual(model.generate_calls, [])

    def test_manifest_bound_group_with_no_rows_rejected(self):
        dev_rows = [row("r1", "g1"), row("r2", "g1")]  # g2 has zero rows
        self._assert_refused_before_anything(dev_rows, self.ROSTER2, self.PACKETS2)

    def test_extra_group_outside_roster_rejected(self):
        dev_rows = [row("r1", "g1"), row("r2", "g1"), row("r3", "g2"), row("r4", "g2"),
                    row("rX", "g3")]
        self._assert_refused_before_anything(dev_rows, self.ROSTER2, self.PACKETS2)

    def test_malformed_group_entry_rejected(self):
        dev_rows = [row("r1", "g1"), row("r2", "g1")]
        packets = {"g1": ["r1", "r2"], "g2": []}  # empty list is malformed
        self._assert_refused_before_anything(dev_rows, self.ROSTER2, packets)

    def test_short_group_rejected(self):
        dev_rows = [row("r1", "g1"), row("r2", "g1"), row("r3", "g2")]  # g2 missing r4
        self._assert_refused_before_anything(dev_rows, self.ROSTER2, self.PACKETS2)


class TestIdentityBindings(OfflinePredictorTestCase):
    def test_each_missing_identity_field_refuses_before_generate(self):
        base = GOOD_IDENTITY_BASE
        for missing in list(base):
            with self.subTest(missing=missing):
                bad = {k: v for k, v in base.items() if k != missing}
                model = FakeModel([VALID_TEXT])
                with self.assertRaises(ValueError):
                    DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                                 identity=bad, constraint_factory=stub_factory)
                self.assertEqual(model.generate_calls, [])

    def test_unknown_identity_field_refuses(self):
        bad = dict(GOOD_IDENTITY_BASE, extra_field="z" * 10)
        model = FakeModel([VALID_TEXT])
        with self.assertRaises(ValueError):
            DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=bad, constraint_factory=stub_factory)
        self.assertEqual(model.generate_calls, [])

    def test_non_hex_identity_value_refuses(self):
        for field in ("base_revision", "weights_manifest_sha256", "template_sha256", "adapter_sha256"):
            for bad_suffix in ("\n", " ", "A"):
                with self.subTest(field=field, suffix=repr(bad_suffix)):
                    bad = good_identity("checkpoint")
                    if field not in bad:
                        continue
                    # Replace last char with the bad suffix or append it
                    original = bad[field]
                    if bad_suffix == "A":
                        bad[field] = original[:-1] + "A"
                    else:
                        bad[field] = original + bad_suffix
                    model = FakeModel([VALID_TEXT])
                    with self.assertRaises(ValueError):
                        DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="checkpoint",
                                     identity=bad, constraint_factory=stub_factory)
                    self.assertEqual(model.generate_calls, [])

    def test_label_field_mismatch_refuses(self):
        # checkpoint requires adapter_sha256; the base identity lacks it.
        model = FakeModel([VALID_TEXT])
        with self.assertRaises(ValueError):
            DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="checkpoint",
                         identity=GOOD_IDENTITY_BASE, constraint_factory=stub_factory)
        # base forbids adapter_sha256; supplying it is an unknown field for base.
        bad = dict(GOOD_IDENTITY_BASE, adapter_sha256="c" * 64)
        with self.assertRaises(ValueError):
            DevPredictor(FakeModel([VALID_TEXT]), FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=bad, constraint_factory=stub_factory)
        # base forbids fewshot_sha256; supplying it is an unknown field for base.
        bad = dict(GOOD_IDENTITY_BASE, fewshot_sha256="d" * 64)
        with self.assertRaises(ValueError):
            DevPredictor(FakeModel([VALID_TEXT]), FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=bad, constraint_factory=stub_factory)
        
        # three_shot is unconditionally rejected
        with self.assertRaises(ValueError) as cm:
            DevPredictor(FakeModel([VALID_TEXT]), FakeTokenizer(), max_new_tokens=8, source_label="three_shot",
                         identity=dict(GOOD_IDENTITY_BASE, fewshot_sha256="d" * 64), constraint_factory=stub_factory)
        self.assertIn("three-shot generation is not available", str(cm.exception))

    def test_wrong_template_hash_refuses(self):
        bad = dict(GOOD_IDENTITY_BASE)
        bad["template_sha256"] = "0" * 64
        model = FakeModel([VALID_TEXT])
        with self.assertRaises(ValueError):
            DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=bad, constraint_factory=stub_factory)
        self.assertEqual(model.generate_calls, [])

    def test_independent_template_hash_accepted(self):
        import hashlib
        from pathlib import Path
        template_path = Path(__file__).resolve().parent.parent / "train" / "prompt_template.py"
        independent_digest = hashlib.sha256(template_path.read_bytes()).hexdigest()
        
        identity = dict(GOOD_IDENTITY_BASE)
        identity["template_sha256"] = independent_digest
        model = FakeModel([VALID_TEXT])
        # Should not raise ValueError
        p = DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=identity, constraint_factory=stub_factory)
        self.assertEqual(p.identity["template_sha256"], independent_digest)

    def test_mismatching_commit_hash_refuses(self):
        model = FakeModel([VALID_TEXT], config=types.SimpleNamespace(_commit_hash="f" * 40))
        with self.assertRaises(ValueError):
            DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=good_identity("base"), constraint_factory=stub_factory)
        self.assertEqual(model.generate_calls, [])

    def test_missing_commit_hash_attribute_refuses(self):
        model = FakeModel([VALID_TEXT], config=types.SimpleNamespace())  # no _commit_hash
        with self.assertRaises(ValueError):
            DevPredictor(model, FakeTokenizer(), max_new_tokens=8, source_label="base",
                         identity=good_identity("base"), constraint_factory=stub_factory)

    def test_good_identity_round_trips_into_binding(self):
        tok, model = FakeTokenizer(), FakeModel([VALID_TEXT])
        identity = good_identity("checkpoint")
        res = predictor(model, tok, source_label="checkpoint", identity=identity).predict(
            [row("r1")], ROSTER, {"g1": ["r1"]})
        self.assertEqual(res["binding"]["identity"], identity)
        self.assertEqual(set(res["binding"]["input_sha256"]), {"r1"})

    def test_different_input_sets_give_different_input_sha256(self):
        tok1, model1 = FakeTokenizer(), FakeModel([VALID_TEXT])
        res1 = predictor(model1, tok1).predict([row("r1")], ROSTER, {"g1": ["r1"]})
        tok2, model2 = FakeTokenizer(), FakeModel([VALID_TEXT])
        row2 = row("r1")
        row2["input"] = dict(INPUT, objective="different")
        res2 = predictor(model2, tok2).predict([row2], ROSTER, {"g1": ["r1"]})
        self.assertNotEqual(res1["binding"]["input_sha256"]["r1"], res2["binding"]["input_sha256"]["r1"])

    def test_template_hash_in_subprocess_without_train_in_sys_path(self):
        import subprocess
        import textwrap
        from pathlib import Path
        import hashlib
        
        template_path = Path(__file__).resolve().parent.parent / "train" / "prompt_template.py"
        predict_dev_path = Path(__file__).resolve().parent / "predict_dev.py"
        independent_digest = hashlib.sha256(template_path.read_bytes()).hexdigest()
        predict_dev_digest = hashlib.sha256(predict_dev_path.read_bytes()).hexdigest()
        
        script = textwrap.dedent(f'''
            import sys
            import json
            sys.path = [p for p in sys.path if "train" not in p]
            import predict_dev
            digest = predict_dev._sha256_hex(predict_dev._default_template_source())
            print(json.dumps({{"digest": digest}}))
        ''')
        
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True, cwd=str(Path(__file__).parent))
        out = json.loads(res.stdout)
        self.assertEqual(out["digest"], independent_digest)
        self.assertNotEqual(out["digest"], predict_dev_digest)


if __name__ == "__main__":
    unittest.main()
