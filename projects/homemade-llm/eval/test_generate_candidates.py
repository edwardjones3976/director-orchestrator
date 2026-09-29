# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:21 UTC | BEGIN]]
"""Fixture-only tests: python -m unittest discover -s projects/homemade-llm/eval -p test_generate_candidates.py -v"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import http.client
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_candidates as gen
from test_score_packets import _minimal_ref


def gguf_fixture(template=None):
    def string(value):
        raw = value.encode("utf-8")
        return struct.pack("<Q", len(raw)) + raw

    pairs = [
        ("general.architecture", 8, string("fixture")),
        ("general.alignment", 4, struct.pack("<I", 32)),
        ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 2) + string("a") + string("b")),
        ("tokenizer.ggml.scores", 9, struct.pack("<IQff", 6, 2, 0.0, 1.0)),
    ]
    if template is not None:
        pairs.append(("tokenizer.chat_template", 8, string(template)))
    return struct.pack("<4sIQQ", b"GGUF", 3, 0, len(pairs)) + b"".join(
        string(key) + struct.pack("<I", kind) + value for key, kind, value in pairs
    )


class GenerateCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.refs, self.inputs, self.out = (self.root / name for name in ("refs", "inputs", "out"))
        self.refs.mkdir()
        self.inputs.mkdir()
        self.item = {"id": "p1", "objective": "Do the thing.", "repository": "org/repo", "title": "Fixture"}
        (self.refs / "p1.json").write_text(json.dumps(_minimal_ref()), encoding="utf-8")
        (self.inputs / "p1.json").write_text(json.dumps(self.item), encoding="utf-8")
        self.template = "{{ bos_token }}\r\n{{ messages[0]['content'] }}\n\u00e9"
        self.gguf = self.root / "fixture.gguf"
        self.gguf.write_bytes(gguf_fixture(self.template))
        self.args = argparse.Namespace(
            refs=self.refs, inputs=self.inputs, out=self.out, schema=gen.DEFAULT_SCHEMA,
            refs_manifest=gen.manifest(self.refs), inputs_manifest=gen.manifest(self.inputs),
            model="fixture-model", label="fixture-base", base_url="http://127.0.0.1:1234/v1",
            model_gguf=self.gguf, lms_models_dir=self.root,
            qwen3=False, control_prompt=None, item_timeout=180, max_tokens=4096,
        )
        self.loaded = [{"identifier": "fixture-model", "path": "fixture.gguf"}]
        self.catalog = [{"modelKey": "fixture-key", "path": "fixture.gguf"}]
        probe = mock.patch.object(gen.subprocess, "run", side_effect=lambda command, **kw:
                                  argparse.Namespace(stdout=json.dumps(self.loaded if command[1] == "ps" else self.catalog)))
        probe.start()
        self.addCleanup(probe.stop)
        self.response = self.completion(json.dumps(_minimal_ref()))
        self.transport = mock.Mock(side_effect=self.respond)
        # Every test injects a transport or connection; accidental sockets fail.
        no_network = mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        no_network.start()
        self.addCleanup(no_network.stop)

    @staticmethod
    def completion(content):
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

    def respond(self, method, url, body, timeout):
        if method == "GET":
            return {"data": []}
        return self.response

    def posts(self):
        return [call for call in self.transport.call_args_list if call.args[0] == "POST"]

    def assert_failed(self, reason):
        record = gen.run(self.args, self.transport)
        self.assertEqual(record["counts"], {"ok": 0, "failed": 1})
        self.assertFalse((self.out / "p1.json").exists())
        failures = (self.out / "failures.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(failures), 1)
        failure = json.loads(failures[0])
        self.assertEqual(failure["id"], "p1")
        self.assertIn(reason, failure["reason"])
        self.assertEqual(len(self.posts()), 1)

    def test_scored_and_unknown_fields_rejected_before_any_request(self):
        for field in ("non_goals", "constraints", "acceptance_checks", "evidence_requirements", "extra"):
            with self.subTest(field=field):
                item = dict(self.item, **{field: ["must never be sent"]})
                (self.inputs / "z.json").write_text(json.dumps(item), encoding="utf-8")
                self.args.inputs_manifest = gen.manifest(self.inputs)
                with self.assertRaisesRegex(ValueError, "exactly"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_input_without_id_is_rejected_before_any_request(self):
        # The shared template tolerates a training row's id-less input block; an
        # evaluation input file still needs its id to name the candidate.
        (self.inputs / "p1.json").write_text(
            json.dumps({k: v for k, v in self.item.items() if k != "id"}), encoding="utf-8")
        self.args.inputs_manifest = gen.manifest(self.inputs)
        with self.assertRaisesRegex(ValueError, "must carry id"):
            gen.run(self.args, self.transport)
        self.transport.assert_not_called()

    def test_both_manifest_mismatches_stop_before_any_request(self):
        for name in ("refs_manifest", "inputs_manifest"):
            with self.subTest(name=name), mock.patch.object(self.args, name, "0" * 64):
                with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_manifest_formula_sorted_top_level_raw_bytes(self):
        folder = self.root / "manifest-fixture"
        folder.mkdir()
        files = {"z.json": b'{"z": 1}\r\n', "A.json": b'{}\n'}
        for name, content in files.items():
            (folder / name).write_bytes(content)
        (folder / "ignore.txt").write_text("ignore")
        (folder / "nested").mkdir()
        (folder / "nested" / "ignore.json").write_text("{}")
        lines = "".join(f"manifest-fixture/{name}\t{hashlib.sha256(files[name]).hexdigest()}\n"
                        for name in sorted(files))
        self.assertEqual(gen.manifest(folder), hashlib.sha256(lines.encode()).hexdigest())

    def test_valid_response_and_complete_run_record(self):
        record = gen.run(self.args, self.transport)
        self.assertEqual(json.loads((self.out / "p1.json").read_text()), _minimal_ref())
        self.assertEqual((self.out / "failures.jsonl").read_text(), "")
        self.assertEqual(json.loads((self.out / "run.json").read_text()), record)
        required = {"label", "model", "base_url", "lm_studio_version", "prompt_template",
                    "prompt_template_sha256", "control_prompt_sha256", "qwen3", "chat_template_kwargs",
                    "temperature", "schema_sha256", "reference_manifest", "input_manifest",
                    "candidate_manifest", "counts", "start_utc", "end_utc"}
        self.assertTrue(required <= record.keys())
        self.assertEqual(record["label"], "fixture-base")
        self.assertEqual(record["model"], "fixture-model")
        self.assertEqual(record["loaded_model_binding"]["sha256"], record["model_gguf_sha256"])
        self.assertEqual(record["base_url"], self.args.base_url)
        self.assertEqual(record["lm_studio_version"], "unknown")
        self.assertEqual(record["schema_sha256"], gen.sha256(self.args.schema.read_bytes()))
        self.assertEqual(record["decision"], "O-044")
        self.assertEqual(record["max_tokens"], 4096)
        self.assertNotIn("reasoning_effort", record)
        self.assertEqual(record["reference_manifest"], self.args.refs_manifest)
        self.assertEqual(record["input_manifest"], self.args.inputs_manifest)
        candidate_hash = gen.sha256((self.out / "p1.json").read_bytes())
        expected = hashlib.sha256(f"out/p1.json\t{candidate_hash}\n".encode()).hexdigest()
        self.assertEqual(record["candidate_manifest"], expected)
        self.assertEqual(record["counts"], {"ok": 1, "failed": 0})
        self.assertEqual(record["chat_template_kwargs"], {})
        self.assertFalse(record["qwen3"])
        self.assertEqual(record["temperature"], 0)
        self.assertIsNone(record["control_prompt_sha256"])
        self.assertLessEqual(datetime.fromisoformat(record["start_utc"]), datetime.fromisoformat(record["end_utc"]))
        self.assertEqual(len(self.posts()), 1)
        body = self.posts()[0].args[2]
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["max_tokens"], 4096)
        self.assert_generation_schema(body, record)
        user_message = json.loads(body["messages"][1]["content"])
        self.assertIsInstance(user_message, dict)
        self.assertEqual(set(user_message), {"objective", "repository", "title"})
        self.assertEqual(user_message, {key: self.item[key] for key in ("objective", "repository", "title")})
        self.assertEqual(body["messages"][0]["content"], gen.SYSTEM_PROMPT)
        self.assertEqual(body["messages"][0]["content"], record["prompt_template"])
        self.assertEqual(record["prompt_template_sha256"], gen.sha256(record["prompt_template"].encode()))
        self.assertNotIn("chat_template_kwargs", body)

    def test_loaded_alias_catalog_and_bytes_must_match_before_generation(self):
        other = self.root / "other.gguf"
        other.write_bytes(gguf_fixture("different template"))
        cases = [([], self.catalog), (self.loaded * 2, self.catalog),
                 (self.loaded, []), (self.loaded, self.catalog * 2),
                 ([{"identifier": "fixture-model", "path": "other.gguf"}],
                  [{"modelKey": "other-key", "path": "other.gguf"}])]
        for loaded, catalog in cases:
            with self.subTest(loaded=loaded, catalog=catalog):
                self.loaded, self.catalog = loaded, catalog
                with self.assertRaisesRegex(ValueError, "model binding"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()
                self.assertFalse(self.out.exists())

    def test_binding_probe_failure_and_remote_endpoint_send_no_requests(self):
        with mock.patch.object(gen.subprocess, "run", side_effect=gen.subprocess.TimeoutExpired("lms", 1)):
            with self.assertRaisesRegex(ValueError, "probe failed"):
                gen.run(self.args, self.transport)
        self.args.base_url = "http://remote.invalid:1234/v1"
        with self.assertRaisesRegex(ValueError, "local LM Studio endpoint"):
            gen.run(self.args, self.transport)
        self.transport.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_gguf_template_and_hashes_are_recorded(self):
        gen.run(self.args, self.transport)
        record = json.loads((self.out / "run.json").read_text())
        raw = self.gguf.read_bytes()
        self.assertEqual(record["model_gguf_path"], str(self.gguf.resolve()))
        self.assertEqual(record["model_gguf_bytes"], len(raw))
        self.assertEqual(record["model_gguf_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["chat_template"], self.template)
        self.assertEqual(record["chat_template_sha256"], hashlib.sha256(self.template.encode()).hexdigest())
        self.assertEqual(record["chat_template_source"], "gguf")

    def test_absent_gguf_template_records_null(self):
        self.gguf.write_bytes(gguf_fixture())
        gen.run(self.args, self.transport)
        record = json.loads((self.out / "run.json").read_text())
        self.assertIsNone(record["chat_template"])
        self.assertIsNone(record["chat_template_sha256"])
        self.assertEqual(record["chat_template_source"], "absent")
        self.assertEqual(len(self.posts()), 1)

    def test_missing_or_bad_magic_gguf_stops_before_any_request(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.args.model_gguf = self.root / "missing.gguf" if missing else self.gguf
                if not missing:
                    self.gguf.write_bytes(b"NOPE" + b"\0" * 20)
                with self.assertRaises((OSError, ValueError)):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()
                self.assertFalse(self.out.exists())

    def test_metadata_reader_stops_at_metadata_boundary(self):
        for template in (None, self.template, ""):
            with self.subTest(template=template):
                metadata = gguf_fixture(template)
                stream = io.BytesIO(metadata + b"tokenizer.chat_template tensor sentinel")
                self.assertEqual(gen.gguf_chat_template(stream, len(stream.getvalue())), template)
                self.assertEqual(stream.tell(), len(metadata))

    def test_truncated_and_oversized_metadata_stops_before_any_request(self):
        for raw in (gguf_fixture(self.template)[:-1],
                    struct.pack("<4sIQQQ", b"GGUF", 3, 0, 1, 2**63)):
            with self.subTest(raw=raw):
                self.gguf.write_bytes(raw)
                with self.assertRaises(ValueError):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_gguf_hash_uses_bounded_reads(self):
        raw = gguf_fixture(self.template) + b"\0" * (2 * 1024 * 1024)
        self.gguf.write_bytes(raw)
        with self.gguf.open("rb") as stream:
            guarded = mock.Mock(wraps=stream)
            def read(size=-1):
                self.assertGreaterEqual(size, 0)
                self.assertLessEqual(size, 1024 * 1024)
                return stream.read(size)
            guarded.read.side_effect = read
            with mock.patch.object(Path, "open") as opened:
                opened.return_value.__enter__.return_value = guarded
                record = gen.model_gguf_record(self.gguf)
        self.assertEqual(record["model_gguf_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["model_gguf_bytes"], len(raw))
        self.assertGreaterEqual(guarded.read.call_args_list.count(mock.call(1024 * 1024)), 3)

    def test_qwen3_request_contract(self):
        self.args.qwen3 = True
        self.args.max_tokens = 2048
        record = gen.run(self.args, self.transport)
        method, url, body, timeout = self.posts()[0].args
        self.assertEqual((method, url, timeout), ("POST", self.args.base_url + "/chat/completions", 180))
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_tokens"], 2048)
        self.assertEqual(record["reasoning_effort"], "none")
        self.assertEqual(record["max_tokens"], 2048)
        expected_schema = self.assert_generation_schema(body, record)
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "model_work_packet_v1", "strict": True,
            "schema": expected_schema,
        }})
        self.assertNotIn("$defs", body["response_format"]["json_schema"]["schema"])
        self.assertIn("Do not think", body["messages"][0]["content"])
        self.assertTrue(record["qwen3"])
        self.assertEqual(record["chat_template_kwargs"], {"enable_thinking": False})

    def assert_generation_schema(self, body, record):
        expected = json.loads(self.args.schema.read_bytes())
        original_pattern = expected["properties"]["repository"]["pattern"]
        escaped_pattern = r"^[^\/]+\/[^\/]+$"
        expected["properties"]["repository"]["pattern"] = escaped_pattern
        self.assertEqual(body["response_format"]["json_schema"]["schema"], expected)
        self.assertEqual(record["schema_rewrites"], [{
            "path": "/properties/repository/pattern", "from": original_pattern, "to": escaped_pattern,
        }])
        self.assertEqual(record["generation_schema_sha256"], gen.sha256(json.dumps(
            expected, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")))
        self.assertEqual(json.loads((self.out / "run.json").read_text()), record)
        return expected

    def test_generation_schema_preserves_escapes_and_nested_patterns(self):
        original = {"description": "a/b", "allOf": [
            {"pattern": r"^a/b$"}, {"pattern": r"^a\/b$"}, {"pattern": r"^a\\/b$"},
        ]}
        before = json.dumps(original)
        generated, rewrites = gen.generation_schema(original)
        self.assertEqual(generated, {"description": "a/b", "allOf": [
            {"pattern": r"^a\/b$"}, {"pattern": r"^a\/b$"}, {"pattern": r"^a\\\/b$"},
        ]})
        self.assertEqual([entry["path"] for entry in rewrites], ["/allOf/0/pattern", "/allOf/2/pattern"])
        self.assertEqual(json.dumps(original), before)
        self.assertEqual(gen.generation_schema(generated), (generated, []))

    def test_original_schema_patterns_still_validate_candidates(self):
        original = json.loads(self.args.schema.read_bytes())
        validator_type = type(gen.schema_validator(self.args.schema))
        is_valid = validator_type.is_valid

        def validate(validator, candidate):
            self.assertEqual(validator.schema, original)
            return is_valid(validator, candidate)

        packet = dict(_minimal_ref(), repository="org/repo/extra")
        self.response = self.completion(json.dumps(packet))
        with mock.patch.object(validator_type, "is_valid", autospec=True, side_effect=validate) as checked:
            self.assert_failed("schema-invalid")
        checked.assert_called_once()

    def test_reasoning_fields_fail_without_retry(self):
        self.args.qwen3 = True
        for field in ("reasoning_content", "reasoning"):
            with self.subTest(field=field):
                self.args.out = self.root / field
                self.out = self.args.out
                self.transport.reset_mock()
                self.response = self.completion(json.dumps(_minimal_ref()))
                self.response["choices"][0]["message"][field] = "hidden thought"
                self.assert_failed("contains reasoning")

    def test_reasoning_tokens_fail_without_retry(self):
        self.args.qwen3 = True
        self.response["usage"] = {"completion_tokens_details": {"reasoning_tokens": 1}}
        self.assert_failed("reasoning tokens")

    def test_empty_reasoning_and_zero_reasoning_tokens_succeed(self):
        self.args.qwen3 = True
        self.response["choices"][0]["message"].update(reasoning_content="", reasoning=None)
        self.response["usage"] = {"completion_tokens_details": {"reasoning_tokens": 0}}
        self.assertEqual(gen.run(self.args, self.transport)["counts"], {"ok": 1, "failed": 0})

    def test_nonpositive_token_cap_sends_no_requests(self):
        for cap in (0, -1):
            with self.subTest(cap=cap):
                self.args.max_tokens = cap
                with self.assertRaisesRegex(ValueError, "max-tokens must be positive"):
                    gen.run(self.args, self.transport)
        self.transport.assert_not_called()

    def test_invalid_json_is_failure(self):
        self.response = self.completion('{"objective":')
        self.assert_failed("JSONDecodeError")

    def test_schema_invalid_output_is_failure(self):
        self.response = self.completion('{"objective": "incomplete packet"}')
        self.assert_failed("schema-invalid")

    def test_think_block_is_failure_even_inside_valid_json(self):
        self.args.qwen3 = True
        packet = _minimal_ref(objective="<think>hidden thought</think>")
        self.response = self.completion(json.dumps(packet))
        self.assert_failed("think block")

    def test_timeout_is_failure_without_retry(self):
        self.transport.side_effect = TimeoutError("fixture timeout")
        self.assert_failed("timeout")
        self.assertEqual(self.posts()[0].args[3], 180)

    def test_http_error_is_failure_without_retry(self):
        self.transport.side_effect = ValueError("HTTP 503")
        self.assert_failed("HTTP 503")

    def test_truncated_but_parseable_completion_is_failure(self):
        self.response["choices"][0]["finish_reason"] = "length"
        self.assert_failed("max_tokens reached")

    def test_server_error_envelope_is_failure(self):
        self.response = {"error": {"message": "fixture error"}}
        self.assert_failed("server error")

    def test_control_prompt_is_used_verbatim_and_hashed(self):
        self.args.control_prompt = self.root / "control.txt"
        prompt = b"Fixed synthetic few-shot example.\r\nInput: toy. Output: {}.\r\n"
        self.args.control_prompt.write_bytes(prompt)
        record = gen.run(self.args, self.transport)
        self.assertEqual(self.posts()[0].args[2]["messages"][0]["content"], prompt.decode())
        self.assertEqual(record["control_prompt_sha256"], hashlib.sha256(prompt).hexdigest())
        self.assertEqual(record["prompt_template_sha256"], record["control_prompt_sha256"])

    def test_version_falls_back_to_models_endpoint(self):
        def respond(method, url, body, timeout):
            if url.endswith("/api/v0"):
                raise ValueError("HTTP 404")
            return {"version": "fixture-version"} if method == "GET" else self.response
        self.transport.side_effect = respond
        record = gen.run(self.args, self.transport)
        self.assertEqual(record["lm_studio_version"], "fixture-version")
        self.assertEqual(len(self.posts()), 1)

    def test_missing_validator_fails_closed(self):
        with mock.patch.object(gen, "schema_validator", return_value=None):
            with self.assertRaisesRegex(ValueError, "validation unavailable"):
                gen.run(self.args, self.transport)
        self.transport.assert_not_called()

    def test_existing_output_rejected_without_overwriting(self):
        self.out.mkdir()
        stale = self.out / "p1.json"
        stale.write_text("stale")
        with self.assertRaisesRegex(ValueError, "empty directory"):
            gen.run(self.args, self.transport)
        self.assertEqual(stale.read_text(), "stale")
        self.transport.assert_not_called()

    def test_unsafe_duplicate_and_reserved_ids_rejected(self):
        for item_id in ("../escape", "run", "CON", "p1"):
            with self.subTest(item_id=item_id):
                item = dict(self.item, id=item_id)
                (self.inputs / "z.json").write_text(json.dumps(item))
                self.args.inputs_manifest = gen.manifest(self.inputs)
                with self.assertRaisesRegex(ValueError, "id"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(self):
        for raw in ('{"id": "one", "id": "two"}', '{"value": NaN}', '{"value": Infinity}', '{"value": 1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gen.strict_json(raw)

    def test_cli_with_injected_transport(self):
        args = ["--inputs", str(self.inputs), "--refs", str(self.refs), "--out", str(self.out),
                "--inputs-manifest", self.args.inputs_manifest, "--refs-manifest", self.args.refs_manifest,
                "--schema", str(self.args.schema), "--model", "fixture-model", "--label", "fixture-base"]
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            gen.main(args, transport=self.transport)
        self.assertEqual(error.exception.code, 2)
        self.transport.assert_not_called()
        args.extend(["--model-gguf", str(self.gguf), "--lms-models-dir", str(self.root)])
        self.assertEqual(gen.main(args, transport=self.transport), 0)
        self.assertEqual(self.posts()[0].args[2]["max_tokens"], 4096)
        args.extend(["--qwen3", "--max-tokens", "1024", "--out", str(self.root / "custom-out")])
        self.assertEqual(gen.main(args, transport=self.transport), 0)
        self.assertEqual(self.posts()[-1].args[2]["max_tokens"], 1024)
        self.assertEqual(self.posts()[-1].args[2]["reasoning_effort"], "none")

    def test_http_transport_uses_single_connection_and_rejects_redirect(self):
        with mock.patch.object(http.client, "HTTPConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"data": []}'
            self.assertEqual(gen.http_json("GET", "http://fixture.invalid/v1/models", None, 1), {"data": []})
            connection.request.assert_called_once()
            self.assertEqual(connection.request.call_args.args[:2], ("GET", "/v1/models"))
            connection.close.assert_called_once()
            connection.request.reset_mock()
            response.status = 302
            with self.assertRaisesRegex(ValueError, "HTTP 302"):
                gen.http_json("POST", "http://fixture.invalid/v1/chat/completions", {}, 1)
            connection.request.assert_called_once()


class GenerateCandidatesContextTests(GenerateCandidatesTests):
    """Packet M2-C1: the D-M2-10 input contract, re-checked before any request."""

    SOURCE_TEXT = "The owner wants the mirror log kept nightly.\n"
    SHA = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()

    def context_item(self, **overrides):
        item = {"kind": "assignment_packet", "text": "The owner wants the mirror log kept nightly.",
                "source_ref": "packets/p1-source.md", "source_sha256": self.SHA}
        item.update(overrides)
        return item

    def write_input(self, name, item):
        (self.inputs / f"{name}.json").write_text(json.dumps(item), encoding="utf-8")
        self.args.inputs_manifest = gen.manifest(self.inputs)

    def setUp(self):
        super().setUp()
        (self.root / "packets").mkdir()
        (self.root / "packets" / "p1-source.md").write_bytes(self.SOURCE_TEXT.encode("utf-8"))
        (self.refs / "metadata").mkdir()
        (self.refs / "metadata" / "p1.meta.json").write_text(
            json.dumps({"source_file": "packets/p1-source.md", "source_sha256": self.SHA}),
            encoding="utf-8")
        self.item = dict(self.item, context=[self.context_item()])
        self.write_input("p1", self.item)

    def test_valid_response_and_complete_run_record(self):
        # The parent's record test fixes the D-M1-1 user-message keys; every
        # other inherited test re-runs unchanged against context inputs.
        self.test_context_is_rendered_through_the_shared_template()

    def test_context_is_rendered_through_the_shared_template(self):
        record = gen.run(self.args, self.transport)
        self.assertEqual(record["counts"], {"ok": 1, "failed": 0})
        self.assertEqual(record["input_contract"], "D-M2-10")
        body = self.posts()[0].args[2]
        self.assertEqual(body["messages"][0]["content"], gen.SYSTEM_PROMPT_CONTEXT)
        self.assertEqual(record["prompt_template"], gen.SYSTEM_PROMPT_CONTEXT)
        self.assertEqual(body["messages"][1]["content"], gen.prompt_template.render_user_message(self.item))
        user_message = json.loads(body["messages"][1]["content"])
        self.assertEqual(set(user_message), {"objective", "repository", "title", "context"})
        self.assertEqual(user_message["context"], [{
            "kind": "assignment_packet", "source_ref": "packets/p1-source.md",
            "text": "The owner wants the mirror log kept nightly.",
        }])
        self.assertNotIn(self.SHA, body["messages"][1]["content"])

    def test_legacy_inputs_still_record_the_m1_contract(self):
        self.write_input("p1", {k: v for k, v in self.item.items() if k != "context"})
        record = gen.run(self.args, self.transport)
        self.assertEqual(record["input_contract"], "D-M1-1")
        self.assertEqual(self.posts()[0].args[2]["messages"][0]["content"], gen.SYSTEM_PROMPT)

    def test_context_leaking_a_scored_field_stops_before_any_request(self):
        reference = _minimal_ref()
        leaks = {
            "whole short string": reference["non_goals"][0] if len(reference["non_goals"][0].split()) >= 4
            else "skip unrelated work now please",
            "eight word shingle": "one two three four five six seven eight nine",
        }
        (self.refs / "p1.json").write_text(json.dumps(dict(
            reference, constraints=["one two three four five six seven eight nine ten"],
            non_goals=["skip unrelated work now please"],
        )), encoding="utf-8")
        self.args.refs_manifest = gen.manifest(self.refs)
        for name, leaked in leaks.items():
            with self.subTest(name=name):
                self.write_input("p1", dict(self.item, context=[self.context_item(text=f"Note: {leaked}.")]))
                with self.assertRaisesRegex(ValueError, "overlaps scored field of p1"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_context_citing_a_held_out_item_stops_before_any_request(self):
        (self.refs / "metadata" / "p1.meta.json").write_text(
            json.dumps({"source_file": "packets/p1-source.md", "source_title": "Frozen Source Title",
                        "source_sha256": self.SHA}),
            encoding="utf-8")
        for source_ref in ("p1", "heldout/set-a/p1.json", "Frozen Source Title"):
            with self.subTest(source_ref=source_ref):
                self.write_input("p1", dict(self.item, context=[self.context_item(source_ref=source_ref)]))
                with self.assertRaisesRegex(ValueError, "resolves to a held-out item"):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_forged_or_changed_source_stops_before_any_request(self):
        cases = {
            "nonexistent": (self.context_item(source_ref="packets/missing.md"),
                            "does not resolve to a source file"),
            "wrong hash": (self.context_item(source_sha256="0" * 64),
                           "does not match source bytes"),
            "invented text": (self.context_item(text="Not present in the frozen assignment."),
                              "not an excerpt of source_ref"),
        }
        for name, (entry, expected) in cases.items():
            with self.subTest(name=name):
                self.write_input("p1", dict(self.item, context=[entry]))
                with self.assertRaisesRegex(ValueError, expected):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()

    def test_reconstructed_pr_has_no_verified_task_time_source(self):
        (self.refs / "metadata" / "p1.meta.json").write_text(
            json.dumps({"source_pr": 103, "source_title": "Fixture PR"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no verified assignment-time source"):
            gen.run(self.args, self.transport)
        self.transport.assert_not_called()

    def test_malformed_context_and_mixed_contracts_stop_before_any_request(self):
        cases = {
            "empty context": (dict(self.item, context=[]), "at least one"),
            "bad kind": (dict(self.item, context=[self.context_item(kind="reference")]), "kind must be"),
            "scored field inside item": (dict(self.item, context=[dict(self.context_item(), constraints=["x"])]),
                                         "exactly kind"),
            "context plus scored field": (dict(self.item, constraints=["x"]), "exactly"),
        }
        for name, (item, fragment) in cases.items():
            with self.subTest(name=name):
                self.write_input("p1", item)
                with self.assertRaisesRegex(ValueError, fragment):
                    gen.run(self.args, self.transport)
                self.transport.assert_not_called()
        self.write_input("p1", self.item)
        self.write_input("z", {k: v for k, v in self.item.items() if k != "context"} | {"id": "z"})
        with self.assertRaisesRegex(ValueError, "mix the D-M1-1 and D-M2-10"):
            gen.run(self.args, self.transport)
        self.transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:26 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:32 UTC | END]]
