from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from director_orchestrator.packet_contracts import (
    MAX_DOCUMENT_BYTES,
    PacketContractError,
    build_replay_run,
    document_digest,
    load_json_document,
    load_replay_run,
    validate_packet,
    verify_replay_run,
    write_replay_run,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs" / "schemas"


class PacketContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")

    def test_accepted_examples_validate_and_build_a_verified_replay(self):
        validate_packet("work", self.work)
        validate_packet("result", self.result)

        replay = build_replay_run(self.work, self.result)
        verified = verify_replay_run(replay)

        self.assertEqual(verified.digest, replay["replay_run_digest"])
        self.assertEqual(verified.work_packet_digest, document_digest(self.work))
        self.assertEqual(verified.result_packet_digest, document_digest(self.result))

    def test_write_and_load_use_the_same_canonical_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            written = write_replay_run(path, self.work, self.result)
            loaded = load_replay_run(path, expected_digest=written.digest)

        self.assertEqual(written.digest, loaded.digest)
        self.assertEqual(written.document, loaded.document)

    def test_nested_packet_tampering_is_rejected(self):
        replay = build_replay_run(self.work, self.result)
        replay["result_packet"]["outcome"] = "tampered"

        with self.assertRaisesRegex(PacketContractError, "replay-run digest mismatch"):
            verify_replay_run(replay)

    def test_recomputed_self_digest_cannot_bypass_trusted_digest(self):
        replay = build_replay_run(self.work, self.result)
        trusted_digest = replay["replay_run_digest"]
        replay["result_packet"]["outcome"] = "tampered"
        replay["result_packet_digest"] = document_digest(replay["result_packet"])
        payload = {key: value for key, value in replay.items() if key != "replay_run_digest"}
        replay["replay_run_digest"] = document_digest(payload)

        with self.assertRaisesRegex(PacketContractError, "trusted reference"):
            verify_replay_run(replay, expected_digest=trusted_digest)

    def test_cross_linked_identifiers_must_match_even_with_valid_digests(self):
        result = copy.deepcopy(self.result)
        result["assignment_id"] = "DIR-OTHER-1"

        with self.assertRaisesRegex(PacketContractError, "assignment_id"):
            build_replay_run(self.work, result)

    def test_packet_unknown_fields_fail_closed(self):
        work = copy.deepcopy(self.work)
        work["unexpected"] = True

        with self.assertRaisesRegex(PacketContractError, "additionalProperties"):
            validate_packet("work", work)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
            with self.assertRaisesRegex(PacketContractError, "duplicate JSON"):
                load_json_document(path)

    def test_oversized_document_is_rejected_before_json_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b"{" + (b" " * MAX_DOCUMENT_BYTES) + b"}")
            with self.assertRaisesRegex(PacketContractError, "byte limit"):
                load_json_document(path)

    def test_oversized_in_memory_packet_is_rejected_before_schema_use(self):
        work = copy.deepcopy(self.work)
        work["objective"] = "x" * (MAX_DOCUMENT_BYTES + 1)

        with self.assertRaisesRegex(PacketContractError, "byte limit"):
            validate_packet("work", work)

    def test_replay_example_is_canonical_and_verified(self):
        example = load_json_document(SCHEMAS / "model-replay-run-v1.example.json")
        verified = verify_replay_run(example)

        self.assertEqual(verified.document, build_replay_run(self.work, self.result))

    def test_json_round_trip_does_not_change_digest(self):
        replay = build_replay_run(self.work, self.result)
        reparsed = json.loads(json.dumps(replay))
        self.assertEqual(document_digest(replay), document_digest(reparsed))


if __name__ == "__main__":
    unittest.main()
