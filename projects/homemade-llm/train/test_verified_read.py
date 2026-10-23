"""WO-G34 deterministic read/consumer tests; real tensor checks run in a clean process."""
import hashlib
import copy
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_train_adapter as fixtures

trainer = fixtures.train_adapter


def entry(name, data):
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def tokenizer_fixture():
    # Captured tokenizer.json components, with a small offline BPE vocabulary.
    bytelevel = {"type": "ByteLevel", "add_prefix_space": False, "trim_offsets": False, "use_regex": False}
    return {"version": "1.0", "truncation": None, "padding": None, "normalizer": {"type": "NFC"},
            "added_tokens": [{"id": 5, "content": "<|endoftext|>", "single_word": False,
                              "lstrip": False, "rstrip": False, "normalized": False, "special": True}],
            "decoder": dict(bytelevel), "post_processor": dict(bytelevel),
            "pre_tokenizer": {"type": "Sequence", "pretokenizers": [
                {"type": "Split", "pattern": {"Regex": r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"},
                 "behavior": "Isolated", "invert": False}, dict(bytelevel)]},
            "model": {"type": "BPE", "dropout": None, "unk_token": None,
                      "continuing_subword_prefix": "", "end_of_word_suffix": "", "fuse_unk": False,
                      "byte_fallback": False, "ignore_merges": False,
                      "vocab": {"H": 0, "e": 1, "l": 2, "o": 3, "!": 4, "<|endoftext|>": 5}, "merges": []}}


def transformed_tokenizer(expected, changes=("decoder", "pre_tokenizer", "post_processor")):
    loaded = copy.deepcopy(expected)
    if "decoder" in changes:
        loaded["decoder"].update(add_prefix_space=True, trim_offsets=True, use_regex=True)
    if "pre_tokenizer" in changes:
        loaded["pre_tokenizer"]["pretokenizers"][1]["trim_offsets"] = True
    if "post_processor" in changes:
        loaded["post_processor"] = {
            "type": "TemplateProcessing", "single": [{"Sequence": {"id": "A", "type_id": 0}}],
            "pair": [{"Sequence": {"id": "A", "type_id": 0}}, {"Sequence": {"id": "B", "type_id": 1}}],
            "special_tokens": {}}
    return loaded


class TokenizerCompatibilityTests(unittest.TestCase):
    def test_identity_and_each_captured_transformation(self):
        expected = tokenizer_fixture()
        before = copy.deepcopy(expected)
        fields = ("decoder", "pre_tokenizer", "post_processor")
        for count in range(4):
            for changes in itertools.combinations(fields, count):
                with self.subTest(changes=changes):
                    loaded = transformed_tokenizer(expected, changes)
                    untouched = copy.deepcopy(loaded)
                    self.assertTrue(trainer.tokenizer_json_matches(loaded, expected))
                    self.assertEqual(loaded, untouched)
        self.assertEqual(expected, before)
        self.assertTrue(trainer.tokenizer_json_matches(dict(reversed(list(expected.items()))), expected))
        self.assertFalse(trainer.tokenizer_json_matches(expected, transformed_tokenizer(expected)))

    def test_protected_fields_types_and_template_tampering_refuse(self):
        changes = [
            (("model", "vocab", "H"), 99), (("model", "vocab", "H"), False),
            (("model", "vocab", "H"), 0.0), (("added_tokens",), []), (("normalizer",), None),
            (("model", "merges"), [["H", "e"]]), (("padding",), {}), (("truncation",), {}),
            (("pre_tokenizer", "pretokenizers", 0, "pattern", "Regex"), "changed"),
            (("pre_tokenizer", "pretokenizers", 0, "invert"), True),
            (("pre_tokenizer", "pretokenizers", 0, "behavior"), "Removed"),
            (("pre_tokenizer", "pretokenizers", 1, "add_prefix_space"), True),
            (("pre_tokenizer", "pretokenizers", 1, "use_regex"), True),
            (("post_processor", "type"), "Other"),
            (("post_processor", "single", 0, "Sequence", "type_id"), False),
            (("post_processor", "pair", 1, "Sequence", "type_id"), 0),
            (("post_processor", "pair", 1, "Sequence", "type_id"), 1.0),
            (("post_processor", "single", 0), {"SpecialToken": {"id": "A", "type_id": 0}}),
            (("post_processor", "special_tokens"), []),
            (("post_processor", "special_tokens"), {"extra": {}}),
        ]
        expected = tokenizer_fixture()
        for path, value in changes:
            with self.subTest(path=path, value=value):
                loaded = transformed_tokenizer(expected)
                target = loaded
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assertFalse(trainer.tokenizer_json_matches(loaded, expected))
        for path in (("pre_tokenizer", "pretokenizers"), ("post_processor", "pair")):
            loaded = transformed_tokenizer(expected)
            loaded[path[0]][path[1]].reverse()
            self.assertFalse(trainer.tokenizer_json_matches(loaded, expected))
            loaded[path[0]][path[1]].pop()
            self.assertFalse(trainer.tokenizer_json_matches(loaded, expected))

    def test_missing_extra_keys_and_other_decoder_flags_refuse(self):
        expected = tokenizer_fixture()
        for path in ((), ("decoder",), ("pre_tokenizer",),
                     ("pre_tokenizer", "pretokenizers", 1), ("post_processor",), ("model",)):
            for extra in (False, True):
                with self.subTest(path=path, extra=extra):
                    loaded = transformed_tokenizer(expected)
                    target = loaded
                    for key in path:
                        target = target[key]
                    if extra:
                        target["unexpected"] = None
                    else:
                        target.pop(next(iter(target)))
                    self.assertFalse(trainer.tokenizer_json_matches(loaded, expected))
        for flags in itertools.product((False, True), repeat=3):
            loaded = copy.deepcopy(expected)
            loaded["decoder"].update(zip(("add_prefix_space", "trim_offsets", "use_regex"), flags))
            self.assertEqual(trainer.tokenizer_json_matches(loaded, expected), len(set(flags)) == 1)
        loaded["decoder"]["trim_offsets"] = 1
        self.assertFalse(trainer.tokenizer_json_matches(loaded, expected))

    def test_real_tokenizer_behavior_and_vocab_mismatch(self):
        try:
            from tokenizers import Tokenizer
        except ImportError:
            self.skipTest("real tokenizers dependency unavailable")
        from tokenizers.pre_tokenizers import ByteLevel
        expected = tokenizer_fixture()
        expected["model"]["vocab"] = {token: index for index, token in enumerate(sorted(ByteLevel.alphabet()))}
        expected["model"]["vocab"]["<|endoftext|>"] = 256
        expected["added_tokens"][0]["id"] = 256
        reference = Tokenizer.from_str(json.dumps(expected))
        for changes in ((), ("decoder",), ("pre_tokenizer",), ("post_processor",),
                        ("decoder", "pre_tokenizer", "post_processor")):
            with self.subTest(changes=changes):
                loaded = Tokenizer.from_str(json.dumps(transformed_tokenizer(expected, changes)))
                trainer.verify_tokenizer_behavior(loaded, reference)
        vocab = expected["model"]["vocab"]
        vocab["H"], vocab["e"] = vocab["e"], vocab["H"]
        changed_reference = Tokenizer.from_str(json.dumps(expected))
        with self.assertRaisesRegex(SystemExit, "loaded tokenizer behavior differs from verified tokenizer bytes"):
            trainer.verify_tokenizer_behavior(loaded, changed_reference)

    def test_behavior_checks_every_field_pair_decode_and_exceptions(self):
        encoding = types.SimpleNamespace(ids=[0], tokens=["H"], offsets=[(0, 1)], type_ids=[0], attention_mask=[1])
        expected = mock.Mock()
        expected.encode.return_value = encoding
        expected.decode.return_value = "H"
        actual = mock.Mock()
        actual.encode.return_value = encoding
        actual.decode.return_value = "H"
        trainer.verify_tokenizer_behavior(actual, expected)
        self.assertEqual(actual.encode.call_count, 34)
        self.assertEqual(actual.decode.call_count, 68)
        pairs = [call for call in actual.encode.call_args_list if len(call.args) == 2]
        self.assertEqual(len(pairs), 4)
        for field in vars(encoding):
            with self.subTest(field=field):
                changed = copy.copy(encoding)
                setattr(changed, field, [])
                actual.encode.return_value = changed
                with self.assertRaisesRegex(SystemExit, "loaded tokenizer behavior differs"):
                    trainer.verify_tokenizer_behavior(actual, expected)
        actual.encode.return_value = encoding
        for skip in (False, True):
            is_pair = False
            def encode(*args, **kwargs):
                nonlocal is_pair
                is_pair = len(args) == 2
                return encoding
            actual.encode.side_effect = encode
            actual.decode.side_effect = lambda ids, skip_special_tokens: "bad" if is_pair and skip_special_tokens == skip else "H"
            with self.subTest(skip=skip), self.assertRaisesRegex(SystemExit, "loaded tokenizer behavior differs"):
                trainer.verify_tokenizer_behavior(actual, expected)
        actual.encode.side_effect = RuntimeError("private exception detail")
        with self.assertRaisesRegex(SystemExit, "^loaded tokenizer behavior differs from verified tokenizer bytes$"):
            trainer.verify_tokenizer_behavior(actual, expected)

    @unittest.skipUnless(hasattr(os, "memfd_create"), "verified loader requires Linux memfd")
    def test_loader_structural_refusal_persists_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            config = b'{"quantization_config":{"load_in_4bit":true}}'
            expected = tokenizer_fixture()
            data = json.dumps(expected).encode()
            evidence = trainer.VerifiedSnapshot(path / "receipt.json", [entry("config.json", config), entry("tokenizer.json", data)])
            evidence.update(trusted_root_sha256="a" * 64)
            evidence.payloads.update({"config.json": config, "tokenizer.json": data})
            loaded = transformed_tokenizer(expected)
            loaded["model"]["vocab"]["H"] = 99
            tokenizer = mock.Mock()
            tokenizer.backend_tokenizer.to_str.return_value = json.dumps(loaded)
            tokenizers = types.ModuleType("tokenizers")
            tokenizers.Tokenizer = mock.Mock()
            tokenizers.Tokenizer.from_str.return_value.to_str.return_value = json.dumps(expected)
            with mock.patch.dict(sys.modules, {"tokenizers": tokenizers}), \
                 mock.patch.object(trainer, "torch", mock.Mock()), \
                 mock.patch.object(trainer, "reference_state_dict", return_value={}), \
                 mock.patch.object(trainer, "sealed_snapshot") as sealed, \
                 mock.patch.object(trainer, "FastLanguageModel") as model, \
                 mock.patch.object(trainer, "verify_tokenizer_behavior") as behavior, \
                 mock.patch.object(trainer, "verify_loaded_model") as weights:
                sealed.return_value.__enter__.return_value = path
                model.from_pretrained.return_value = (mock.Mock(), tokenizer)
                with self.assertRaisesRegex(SystemExit, "loaded tokenizer differs from verified tokenizer bytes"):
                    trainer.load_verified_model(path, evidence, max_seq_length=1536)
                behavior.assert_not_called()
                weights.assert_not_called()
            self.assertEqual(json.loads(evidence.receipt_path.read_bytes())["status"], "refused")
            self.assertEqual(evidence.payloads["tokenizer.json"], data)


class VerifiedReadTests(unittest.TestCase):
    def test_real_file_rewrite_between_reads_refuses(self):
        for replacement in (b"abce", b"longer input"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, "model.safetensors")
                path.write_bytes(b"abcd")
                evidence = trainer.VerifiedSnapshot(None, [])
                real_read = trainer._read_file_once

                def read(p, size, attempt):
                    if evidence.receipt["attempts"]:
                        previous = p.stat()
                        p.write_bytes(replacement)
                        os.utime(p, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
                    return real_read(p, size, attempt)

                with mock.patch.object(trainer, "_read_file_once", side_effect=read):
                    with self.assertRaisesRegex(SystemExit, "changed between reads|size mismatch"):
                        trainer.verified_file_bytes(path, entry(path.name, b"abcd"), evidence)
                self.assertEqual(len(evidence.receipt["attempts"]), 2)
                self.assertEqual(evidence.receipt["status"], "refused")

    def test_trust_chain_consecutive_reads_and_exhaustion(self):
        for target in ("root", "list"):
            for corrupt, accepted in (((False, False), True), ((True, False, False), True),
                                     ((False, True, False), False), ((True, True, True), False)):
                with self.subTest(target=target, corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                    root, listing, pin = fixtures.TrainAdapterTests()._trusted_chain(Path(tmp))
                    selected = root if target == "root" else listing
                    original = trainer._read_file_once
                    count = 0
                    evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [])

                    def read(path, size, attempt):
                        nonlocal count
                        data = original(path, size, attempt)
                        if path == selected:
                            damaged = corrupt[count]
                            count += 1
                            if damaged:
                                data = bytes([data[0] ^ 8]) + data[1:]
                        return data

                    with mock.patch.object(trainer, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                         mock.patch.object(trainer, "_read_file_once", side_effect=read):
                        if accepted:
                            files, binding = trainer.load_authenticated_digests(root, listing,
                                trainer.WINNER_BASE, trainer.WINNER_REVISION, evidence=evidence)
                            self.assertEqual(files[0]["path"], "model.safetensors")
                            self.assertEqual(binding["trusted_root_sha256"], pin)
                            self.assertEqual(evidence.trust_payloads["root"], root.read_bytes())
                            self.assertEqual(evidence.trust_payloads["expected_digests"], listing.read_bytes())
                        else:
                            with self.assertRaisesRegex(SystemExit, "digest mismatch.*exhausted reads.*read 3"):
                                trainer.load_authenticated_digests(root, listing,
                                    trainer.WINNER_BASE, trainer.WINNER_REVISION, evidence=evidence)
                            self.assertEqual(evidence.receipt["status"], "refused")
                    self.assertEqual(count, len(corrupt))
                    for attempt in evidence.receipt["attempts"]:
                        self.assertIsNone(attempt["expected_size"])
                        self.assertEqual(attempt["size_authority"], "observed only")
                        self.assertEqual(attempt["size"], attempt["bytes_read"])

    def test_trust_chain_parses_retained_bytes_after_path_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, listing, pin = fixtures.TrainAdapterTests()._trusted_chain(Path(tmp))
            expected_root, expected_list = root.read_bytes(), listing.read_bytes()
            evidence = trainer.VerifiedSnapshot(None, [])
            real_verify = trainer.verified_file_bytes

            def verify(path, *args, **kwargs):
                accepted = real_verify(path, *args, **kwargs)
                path.write_bytes(b"replacement must not be parsed")
                return accepted

            with mock.patch.object(trainer, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(trainer, "verified_file_bytes", side_effect=verify), \
                 mock.patch.object(Path, "read_bytes", side_effect=AssertionError("path reopened")):
                files, _ = trainer.load_authenticated_digests(root, listing,
                    trainer.WINNER_BASE, trainer.WINNER_REVISION, evidence=evidence)
            self.assertEqual(files[0]["path"], "model.safetensors")
            self.assertEqual(evidence.trust_payloads,
                {"root": expected_root, "expected_digests": expected_list})

    def test_unparented_list_refuses_before_snapshot_resolution(self):
        with mock.patch.object(trainer, "local_snapshot_dir") as resolve:
            with self.assertRaisesRegex(SystemExit, "no authenticated parent"):
                trainer.digest_verification_before_load(trainer.SMOKE_BASE, trainer.SMOKE_REVISION,
                    Path("untrusted.json"))
            resolve.assert_not_called()

    def test_full_precision_winner_and_fallback_refuse_before_model_effects(self):
        for base in (trainer.WINNER_BASE, trainer.FALLBACK_BASE):
            with self.subTest(base=base), tempfile.TemporaryDirectory() as tmp:
                data = b'{"model_type":"qwen3"}'
                evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [entry("config.json", data)])
                evidence.update(trusted_root_sha256="a" * 64, base=base)
                evidence.payloads["config.json"] = data
                with mock.patch.object(trainer, "FastLanguageModel") as model, \
                     mock.patch.object(trainer, "reference_state_dict") as decode, \
                     mock.patch.object(trainer.torch, "cuda", create=True) as cuda:
                    with self.assertRaisesRegex(SystemExit, "full-precision.*WO-G32-LDR-FP1"):
                        trainer.load_verified_model(Path(tmp), evidence, max_seq_length=4096)
                    self.assertEqual(model.mock_calls, [])
                    decode.assert_not_called()
                    self.assertEqual(cuda.mock_calls, [])
                self.assertEqual(evidence.receipt["status"], "refused")

    def test_consecutive_rule_and_three_read_limit(self):
        good, bad = b"verified", b"verifimd"  # one XOR-0x08 byte
        for sequence, accepted in (([good, good], True), ([bad, good, good], True),
                                   ([good, bad, good], False), ([bad] * 3, False)):
            with self.subTest(sequence=sequence), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, "model.safetensors")
                path.write_bytes(good)
                evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [entry(path.name, good)])
                real_read = trainer._read_file_once
                calls = []

                def read(p, size, attempt):
                    actual = real_read(p, size, attempt)
                    self.assertEqual(actual, good)  # each attempt really reads the file
                    calls.append(p)
                    return sequence[len(calls) - 1]

                with mock.patch.object(trainer, "_read_file_once", side_effect=read):
                    if accepted:
                        self.assertEqual(trainer.verified_file_bytes(path, entry(path.name, good), evidence), good)
                    else:
                        with self.assertRaises(SystemExit):
                            trainer.verified_file_bytes(path, entry(path.name, good), evidence)
                self.assertEqual(len(calls), len(sequence))
                attempts = json.loads(evidence.receipt_path.read_bytes())["attempts"]
                self.assertEqual(len(attempts), len(sequence))
                for number, attempt in enumerate(attempts, 1):
                    self.assertEqual(attempt["attempt"], number)
                    self.assertEqual(attempt["digest"], hashlib.sha256(sequence[number - 1]).hexdigest())
                    self.assertEqual(attempt["bytes_read"], len(good))
                    self.assertIsNotNone(attempt["method"])
                    self.assertGreaterEqual(attempt["seconds"], 0)

    def test_missing_wrong_size_short_io_and_timeout_refuse_immediately(self):
        for kind in ("missing", "size", "short", "io", "timeout", "changed"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, "model.safetensors")
                if kind != "missing":
                    path.write_bytes(b"abcd")
                spec = entry(path.name, b"abcd")
                if kind == "size":
                    spec["size"] = 5
                evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [spec])
                original = trainer._read_file_once

                def read(p, size, attempt):
                    if kind == "io":
                        raise OSError("synthetic read failure")
                    if kind == "timeout":
                        raise TimeoutError("synthetic deadline")
                    data = original(p, size, attempt)
                    if kind == "changed" and evidence.receipt["attempts"]:
                        attempt["identity"][-1] += 1
                    return data[:-1] if kind == "short" else data

                with mock.patch.object(trainer, "_read_file_once", side_effect=read):
                    with self.assertRaises(SystemExit):
                        trainer.verified_file_bytes(path, spec, evidence)
                receipt = json.loads(evidence.receipt_path.read_bytes())
                self.assertEqual(receipt["status"], "refused")
                self.assertEqual(len(receipt["attempts"]), 2 if kind == "changed" else 1)
                self.assertTrue(receipt["attempts"][-1]["error"])

    def test_receipt_failure_blocks_even_matching_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "w")
            path.write_bytes(b"data")
            spec = entry("w", b"data")
            evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [spec])
            with mock.patch.object(evidence, "persist", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    trainer.verified_file_bytes(path, spec, evidence)
            self.assertEqual(len(evidence.receipt["attempts"]), 1)

    def test_retained_corruption_refuses_before_decoder(self):
        spec = entry("model.safetensors", b"data")
        evidence = trainer.VerifiedSnapshot(None, [spec])
        evidence.payloads[spec["path"]] = b"datA"
        with self.assertRaisesRegex(SystemExit, "retained buffer corruption"):
            evidence.checked_bytes(spec["path"])

    def test_loader_requires_authenticated_receipt(self):
        with mock.patch.object(trainer, "FastLanguageModel") as flm:
            for supplied in (None, {}, trainer.VerifiedSnapshot(None, [])):
                with self.subTest(supplied=supplied), self.assertRaises(SystemExit):
                    trainer.load_verified_model(Path("unused"), supplied, max_seq_length=2048)
            flm.from_pretrained.assert_not_called()

    @unittest.skipUnless(hasattr(os, "memfd_create"), "sealed snapshot requires Linux memfd")
    def test_path_substitution_and_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "snapshot"
            source.mkdir()
            data = b"original retained weights"
            (source / "model.safetensors").write_bytes(data)
            verified = trainer.verify_expected_digests(source, [entry("model.safetensors", data)],
                receipt_path=root / "receipt.json")
            (source / "model.safetensors").write_bytes(b"substituted after authentication")
            with trainer.sealed_snapshot(verified) as sealed:
                self.assertEqual((sealed / "model.safetensors").read_bytes(), data)
                with self.assertRaises(OSError):
                    (sealed / "model.safetensors").write_bytes(b"tamper")
            verified.payloads["model.safetensors"] = b"broken"
            with self.assertRaisesRegex(SystemExit, "retained buffer corruption"):
                with trainer.sealed_snapshot(verified):
                    self.fail("corrupt bytes reached the loader")

    @unittest.skipUnless(hasattr(os, "O_DIRECT"), "O_DIRECT fallback requires Linux")
    def test_direct_unsupported_uses_fadvise(self):
        import errno
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "data")
            path.write_bytes(b"abcd")
            real_open, real_advise = os.open, os.posix_fadvise
            def open_file(p, flags):
                if flags & os.O_DIRECT:
                    raise OSError(errno.EINVAL, "unsupported direct I/O")
                return real_open(p, flags)
            attempt = {"bytes_read": 0}
            with mock.patch.object(os, "open", side_effect=open_file), \
                 mock.patch.object(os, "posix_fadvise", wraps=real_advise) as advise:
                self.assertEqual(trainer._read_file_once(path, 4, attempt), b"abcd")
            advise.assert_called_once()
            self.assertEqual(attempt["method"], "posix_fadvise(DONTNEED)")

    def test_all_consumers_refuse_unverified_loading(self):
        for mode in ("train", "context", "probe"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp, "report.json")
                snapshot = Path(tmp, trainer.SMOKE_REVISION)
                with mock.patch.object(trainer, "digest_verification_before_load", return_value=None), \
                     mock.patch.object(trainer, "local_snapshot_dir", return_value=snapshot), \
                     mock.patch.object(trainer, "FastLanguageModel") as flm, \
                     mock.patch.object(trainer, "set_seed"), \
                     mock.patch.object(trainer.torch, "cuda", mock.MagicMock(), create=True):
                    with self.assertRaisesRegex(SystemExit, "retained bytes"):
                        if mode == "train":
                            trainer.train([fixtures.row()], [], trainer.SMOKE_BASE, trainer.SMOKE_REVISION,
                                          20, report, winner=False)
                        elif mode == "probe":
                            trainer.run_probe([fixtures.row()], trainer.SMOKE_BASE, trainer.SMOKE_REVISION,
                                              report, probe_rows_total=None, probe_seq_len=2048)
                        else:
                            inputs = Path(tmp, "inputs.jsonl")
                            inputs.write_text(json.dumps(fixtures.row()["input"]) + "\n")
                            with fixtures.admitted_probe_template():
                                trainer.run_context_probe(inputs, trainer.SMOKE_BASE, trainer.SMOKE_REVISION,
                                                          report, context_limit=4096)
                    flm.from_pretrained.assert_not_called()
                    flm.get_peft_model.assert_not_called()

    def test_launcher_missing_digest_refusal(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell required for actual launcher refusal statements")
        source = Path(trainer.__file__).parents[1] / "scripts" / "train-adapter.ps1"
        text = source.read_text()
        start = text.index("# Every consumer, including smoke")
        end = text.index("if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {", start)
        fragment = text[start:end]
        command = "$ErrorActionPreference='Stop'; $ExpectedDigests=''; try {\n" + fragment + "\nexit 9 } catch { Write-Output $_; exit 0 }"
        result = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unverified smoke loading is disabled", result.stdout)


class PaddingAllowanceTests(unittest.TestCase):
    class Tensor:
        """Small bf16 byte fixture exercising the production byte comparator."""
        def __init__(self, rows, *, storage=None, offset=0):
            self.rows = copy.deepcopy(rows)
            self.shape = (len(rows), len(rows[0]))
            self.dtype = "bfloat16"
            self.device = types.SimpleNamespace(type="cpu")
            self.storage = storage if storage is not None else object()
            self.offset = offset

        def data_ptr(self):
            return id(self.storage) + self.offset

        def untyped_storage(self):
            return types.SimpleNamespace(data_ptr=lambda: id(self.storage))

        def is_floating_point(self):
            return True

        def to(self, *, dtype):
            result = self.clone()
            result.dtype = dtype
            return result

        def clone(self):
            result = PaddingAllowanceTests.Tensor(self.rows)
            result.dtype = self.dtype
            return result

        def __getitem__(self, index):
            return types.SimpleNamespace(zero_=lambda: self.rows[index].__setitem__(slice(None), [0] * self.shape[1]))

        def detach(self):
            return self

        contiguous = detach
        cpu = detach
        numpy = detach

        def reshape(self, *_):
            return self

        def view(self, *_):
            return self

        def tobytes(self):
            return b"".join(value.to_bytes(2, "little") for row in self.rows for value in row)

    def setUp(self):
        self.key = "backbone.input.weight"  # No hard-coded model.embed_tokens name.
        self.config = {"tie_word_embeddings": False}
        self.docs = {
            "config.json": self.config,
            "tokenizer_config.json": {"pad_token": "<pad>", "eos_token": "<eos>"},
            "generation_config.json": {"pad_token_id": 1, "eos_token_id": 2},
            "tokenizer.json": {"added_tokens": [{"id": 1, "content": "<pad>"}, {"id": 2, "content": "<eos>"}]},
        }
        self.reference = {self.key: self.Tensor([[1, 2], [3, 4], [5, 6]]),
                          "lm_head.weight": self.Tensor([[7, 8], [9, 10], [11, 12]]),
                          "linear.weight": self.Tensor([[13, 14]]),
                          "linear.weight.quant_state.bitsandbytes__nf4": self.Tensor([[15]])}
        self.actual = {key: value.clone() for key, value in self.reference.items()}
        class Embedding:
            pass
        embedding = Embedding()
        embedding.weight = self.actual[self.key]
        embedding.padding_idx = 1
        self.embedding = embedding
        class Linear4bit:
            pass
        linear = Linear4bit()
        linear.weight = self.actual["linear.weight"]
        linear.weight.quant_state = linear.quant_state = object()
        self.model = types.SimpleNamespace(
            get_input_embeddings=lambda: embedding,
            lm_head=types.SimpleNamespace(weight=self.actual["lm_head.weight"]),
            named_modules=lambda: [("backbone.input", embedding), ("linear", linear)],
            state_dict=lambda: dict(self.actual))
        patches = [
            mock.patch.object(trainer, "torch", types.SimpleNamespace(
                nn=types.SimpleNamespace(Embedding=Embedding), uint8="uint8", device=lambda value: value)),
            mock.patch.dict(sys.modules, {
                "bitsandbytes.nn": types.SimpleNamespace(Linear4bit=Linear4bit),
                "bitsandbytes.functional": types.SimpleNamespace(
                    QuantState=types.SimpleNamespace(from_dict=lambda *args, **kwargs: object()))}),
            mock.patch.object(trainer, "compare_quant_state"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def check(self, *, verified=True):
        payloads = {name: json.dumps(doc).encode() for name, doc in self.docs.items()}
        evidence = trainer.VerifiedSnapshot(None, [entry(name, data) for name, data in payloads.items()])
        evidence.payloads = payloads
        return trainer.verify_loaded_model(self.model, self.reference, dtype="bfloat16",
                                          config=self.config, verified=evidence if verified else None)

    def zero_pad(self):
        self.actual[self.key].rows[1] = [0, 0]

    def test_exact_identity_nonzero_padding_and_no_metadata(self):
        for verified in (True, False):
            result = self.check(verified=verified)
            self.assertNotIn("input_embedding_pad_row_allowance", result)
            self.assertNotIn("allowance", result["comparison"])

    def test_zero_pad_receipt_and_no_mutation(self):
        self.zero_pad()
        before = {key: value.tobytes() for key, value in self.actual.items()}
        originals = {key: value.tobytes() for key, value in self.reference.items()}
        result = self.check()
        allowance = result["input_embedding_pad_row_allowance"]
        self.assertEqual((allowance["tensor"], allowance["token_id"], allowance["token"]),
                         (self.key, 1, "<pad>"))
        self.assertEqual(allowance["configuration_sha256"],
                         {name: hashlib.sha256(json.dumps(doc).encode()).hexdigest() for name, doc in self.docs.items()})
        self.assertEqual(allowance["source"]["sha256"], "e09924b71078efabcd5173002620baa72cf9a09f1d974af4bad5516f5508e225")
        self.assertEqual(allowance["source"]["lines"], "3004-3025")
        self.assertIn("1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739", allowance["pinned_image"])
        self.assertIn("input-embedding pad row zeroed by loader (allowance)", result["comparison"])
        self.assertEqual(before, {key: value.tobytes() for key, value in self.actual.items()})
        self.assertEqual(originals, {key: value.tobytes() for key, value in self.reference.items()})
        with tempfile.TemporaryDirectory() as tmp:
            evidence = trainer.VerifiedSnapshot(Path(tmp, "receipt.json"), [])
            evidence.receipt.update(status="verified", post_load=result)
            evidence.persist()
            self.assertEqual(json.loads(evidence.receipt_path.read_bytes())["post_load"], result)

    def test_corrupt_rows_and_negative_zero_refuse(self):
        for rows in ([[0, 0], [3, 4], [5, 6]], [[1, 2], [0, 1], [5, 6]],
                     [[1, 2], [0, 0x8000], [5, 6]], [[0, 0], [0, 0], [5, 6]]):
            with self.subTest(rows=rows):
                self.actual[self.key].rows = rows
                with self.assertRaisesRegex(SystemExit, "payload differs"):
                    self.check()

    def test_other_tensor_and_shape_dtype_checks_remain_exact(self):
        self.zero_pad()
        head = self.actual["lm_head.weight"]
        head.rows[1] = [0, 0]
        with self.assertRaisesRegex(SystemExit, "payload differs: lm_head.weight"):
            self.check()
        head.rows[1] = [9, 10]
        tensor = self.actual[self.key]
        for field, value in (("shape", (3, 1)), ("dtype", "float16")):
            with self.subTest(field=field), mock.patch.object(tensor, field, value):
                with self.assertRaisesRegex(SystemExit, "shape/dtype differs"):
                    self.check()

    def test_allowance_preserves_existing_dtype_conversion(self):
        self.zero_pad()
        self.reference[self.key].dtype = "float32"
        result = self.check()
        self.assertEqual(result["casts"], [{"name": self.key, "from": "float32", "to": "bfloat16"}])
        self.assertIn("input_embedding_pad_row_allowance", result)
        self.assertEqual(self.reference[self.key].dtype, "float32")

    def test_each_eligibility_condition_refuses_and_exact_still_passes(self):
        cases = ("padding_none", "padding_wrong", "padding_bool", "same_eos", "declared_tie",
                 "shared_storage", "shared_offset", "not_embedding", "wrong_name", "wrong_tensor", "no_metadata")
        for case in cases:
            with self.subTest(case=case):
                self.doCleanups()
                self.setUp()
                if case.startswith("padding_"):
                    self.embedding.padding_idx = {"padding_none": None, "padding_wrong": 0, "padding_bool": True}[case]
                elif case == "same_eos":
                    self.docs["generation_config.json"]["eos_token_id"] = 1
                    self.docs["tokenizer_config.json"]["eos_token"] = "<pad>"
                elif case == "declared_tie":
                    self.config["tie_word_embeddings"] = True
                elif case.startswith("shared_"):
                    self.model.lm_head.weight.storage = self.embedding.weight.storage
                    self.model.lm_head.weight.offset = 2 if case == "shared_offset" else 0
                elif case == "not_embedding":
                    self.model.get_input_embeddings = lambda: types.SimpleNamespace(weight=self.actual[self.key], padding_idx=1)
                elif case == "wrong_name":
                    self.key = "other.weight"
                    self.actual[self.key] = self.actual.pop("backbone.input.weight")
                    self.reference[self.key] = self.reference.pop("backbone.input.weight")
                elif case == "wrong_tensor":
                    self.embedding.weight = self.embedding.weight.clone()
                self.assertNotIn("input_embedding_pad_row_allowance", self.check(verified=case != "no_metadata"))
                self.zero_pad()
                with self.assertRaisesRegex(SystemExit, "payload differs"):
                    self.check(verified=case != "no_metadata")

    def test_invalid_metadata_refuses(self):
        baseline = copy.deepcopy(self.docs)
        mutations = [(file, None, None) for file in baseline]
        mutations += [("generation_config.json", key, value)
                      for key in ("pad_token_id", "eos_token_id") for value in (None, True, 1.0, "1", -1, 3, [2])]
        mutations += [("tokenizer_config.json", key, value)
                      for key in ("pad_token", "eos_token") for value in (None, "wrong", {"content": "<pad>"})]
        mutations += [("tokenizer.json", "added_tokens", value) for value in (
            [], None, [{"id": 1, "content": "wrong"}],
            [{"id": True, "content": "<pad>"}, {"id": 2, "content": "<eos>"}],
            baseline["tokenizer.json"]["added_tokens"] + [{"id": 1, "content": "duplicate"}])]
        for file, key, value in mutations:
            with self.subTest(file=file, key=key, value=value):
                self.docs = copy.deepcopy(baseline)
                if key is None:
                    del self.docs[file]
                else:
                    self.docs[file][key] = value
                self.zero_pad()
                with self.assertRaisesRegex(SystemExit, "payload differs"):
                    self.check()


# Import real torch/bnb before the offline fixture module in a clean process.
# CPU mocks cannot qualify the GPU destination path.
TENSOR_CHECK = r'''
import sys
try:
    import torch
    import bitsandbytes as bnb
    from safetensors.torch import save
except ImportError as exc:
    print("real tensor dependencies unavailable:", exc)
    sys.exit(77)
if sys.argv[1] == "cuda" and not torch.cuda.is_available():
    print("CUDA unavailable; real destination verification not exercised")
    sys.exit(77)
import test_train_adapter as fixtures
import train_adapter as t
from unittest import mock
from pathlib import Path
import tempfile, hashlib, json
device = torch.device(sys.argv[1])
nested = bnb.functional.QuantState(absmax=torch.tensor([1.0]), code=torch.linspace(-1, 1, 256),
    blocksize=256, dtype=torch.float32)
state = bnb.functional.QuantState(absmax=torch.tensor([128], dtype=torch.uint8), shape=torch.Size([2, 4]),
    code=torch.linspace(-1, 1, 16), blocksize=64, quant_type="nf4", dtype=torch.float16,
    offset=torch.tensor(0.5), state2=nested)
layer = bnb.nn.Linear4bit(4, 2, bias=False, quant_type="nf4")
layer.weight = bnb.nn.Params4bit(torch.tensor([[1], [23], [45], [67]], dtype=torch.uint8),
    requires_grad=False, quant_state=state, bnb_quantized=True)
layer.quant_state = state
model = torch.nn.Sequential(layer)
reference = {k: v.clone() for k, v in model.state_dict().items()}
model.to(device)
def check():
    return t.verify_loaded_model(model, reference, dtype=torch.float16, config={})
check()
def refuses(action, undo):
    action()
    try:
        check()
    except SystemExit:
        pass
    else:
        raise AssertionError("corruption passed destination verification")
    finally:
        undo()
    check()
with torch.no_grad():
    original = layer.weight.data.clone()
    refuses(lambda: layer.weight.data.fill_(99), lambda: layer.weight.data.copy_(original))
    original_absmax = layer.weight.quant_state.absmax.clone()
    refuses(lambda: layer.weight.quant_state.absmax.fill_(2),
            lambda: layer.weight.quant_state.absmax.copy_(original_absmax))
    original_state = layer.weight.quant_state
    refuses(lambda: setattr(layer.weight, "quant_state", None),
            lambda: setattr(layer.weight, "quant_state", original_state))
    old_blocksize = original_state.blocksize
    refuses(lambda: setattr(original_state, "blocksize", 128),
            lambda: setattr(original_state, "blocksize", old_blocksize))
    original_nested = original_state.state2.code.clone()
    refuses(lambda: original_state.state2.code.fill_(0),
            lambda: original_state.state2.code.copy_(original_nested))
    original_weight = reference["0.weight"]
    refuses(lambda: reference.__setitem__("0.weight", original_weight.reshape(-1)),
            lambda: reference.__setitem__("0.weight", original_weight))
    refuses(lambda: reference.__setitem__("0.weight", original_weight.to(torch.int16)),
            lambda: reference.__setitem__("0.weight", original_weight))
    refuses(lambda: reference.__setitem__("unexpected.weight", original_weight),
            lambda: reference.pop("unexpected.weight"))
# Exact scalar comparison and corruption, not just matrix payloads.
t.compare_tensor("scalar", torch.tensor(1., device=device), torch.tensor(1.))
if hasattr(__import__("os"), "memfd_create"):
    with tempfile.TemporaryDirectory() as tmp:
        payload = save(reference)
        specs = [{"path": "model.safetensors", "size": len(payload),
                  "sha256": hashlib.sha256(payload).hexdigest()}]
        verified = t.VerifiedSnapshot(Path(tmp, "receipt.json"), specs)
        verified.payloads["model.safetensors"] = payload
        decoded = t.reference_state_dict(verified)
        for key in reference:
            t.compare_tensor(key, decoded[key], reference[key])
        # A buffer corrupted after acceptance cannot reach safetensors.load.
        verified.payloads["model.safetensors"] = payload[:-1] + bytes([payload[-1] ^ 8])
        try:
            t.reference_state_dict(verified)
        except SystemExit:
            pass
        else:
            raise AssertionError("corrupt retained bytes decoded")
print("real", device, "tensor and quant-state checks passed")
'''


class RealTensorTests(unittest.TestCase):
    def run_check(self, device):
        result = subprocess.run([sys.executable, "-c", TENSOR_CHECK, device],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=120)
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cpu_payload_and_quant_state_corruption(self):
        self.run_check("cpu")

    def test_real_cuda_destination_payload_and_quant_state_corruption(self):
        self.run_check("cuda")


if __name__ == "__main__":
    unittest.main()
