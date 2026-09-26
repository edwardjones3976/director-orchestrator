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
            model="fixture-model", label="fixture-base", base_url="http://fixture.invalid/v1",
            model_gguf=self.gguf,
            qwen3=False, control_prompt=None, item_timeout=180,
        )
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
        self.assertEqual(record["base_url"], self.args.base_url)
        self.assertEqual(record["lm_studio_version"], "unknown")
        self.assertEqual(record["schema_sha256"], gen.sha256(self.args.schema.read_bytes()))
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
        self.assertEqual(json.loads(body["messages"][1]["content"]), self.item)
        self.assertEqual(body["messages"][0]["content"], record["prompt_template"])
        self.assertEqual(record["prompt_template_sha256"], gen.sha256(record["prompt_template"].encode()))
        self.assertNotIn("chat_template_kwargs", body)

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
        record = gen.run(self.args, self.transport)
        method, url, body, timeout = self.posts()[0].args
        self.assertEqual((method, url, timeout), ("POST", self.args.base_url + "/chat/completions", 180))
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "model_work_packet_v1", "strict": True,
            "schema": json.loads(self.args.schema.read_bytes()),
        }})
        self.assertNotIn("$defs", body["response_format"]["json_schema"]["schema"])
        self.assertIn("Do not think", body["messages"][0]["content"])
        self.assertTrue(record["qwen3"])
        self.assertEqual(record["chat_template_kwargs"], {"enable_thinking": False})

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
        self.assert_failed("incomplete")

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
        args.extend(["--model-gguf", str(self.gguf)])
        self.assertEqual(gen.main(args, transport=self.transport), 0)

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


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:26 UTC | END]]
