"""Regression tests for digest-bound REA patching and marker validation."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apply_rea_precision


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _build_disposable_tree(root):
    (root / "package.json").write_text(
        json.dumps({"name": "rea-agents", "version": "6.9.0"}) + "\n",
        encoding="utf-8",
    )
    bundle_template = (
        "//#region dist/domain/analysisView/StateRecordChronology.js\n"
        "export class StateRecordChronology {}\n"
        "//#endregion\n"
        "//#region dist/domain/analysisView/environmentStateWrites.js\n"
        "\t\tclockStart = (records) => {\n"
        "\t\t\tif (records.some((record) => record.timing.endMs === undefined)) return null;\n"
        "\t\t\tlet latest;\n"
        "\t\t\tfor (const record of records) {\n"
        "\t\t\t\tif (record.timing.endMs === undefined || Math.abs(record.timing.endMs) > LATEST_END_MS) continue;\n"
        "\t\t\t\tconst end = record.timing.end;\n"
        "\t\t\t\tif (end === undefined) return null;\n"
        "\t\t\t\tif (latest === undefined || compareOffsetDateTimeInstants(end, latest) > 0) latest = end;\n"
        "\t\t\t}\n"
        "\t\t};\n"
        "//#endregion\n"
        "//#region dist/domain/analysisView/EnvironmentStateAccumulator.js\n"
        "// caf\u00e9 \u2014 bundled\n"
        "const a = 128 * 8 + 128 * 8 + x.startMs(y) + x.endMs(z);\n"
        "const b = records.some((record) => record.timing.endMs === undefined);\n"
        "//#endregion\n"
    )
    files = {
        "dist/domain/analysisView/StateRecordChronology.js": "// before chronology\nexport class StateRecordChronology {}\n",
        "dist/domain/analysisView/EnvironmentStateAccumulator.js": (
            "// caf\u00e9 \u2014 module\n"
            "const a = ENTRY_BYTES * 8 + ENTRY_BYTES * 8 + r.startMs(a) + r.endMs(b);\n"
            "const b = records.some((record) => record.timing.endMs === undefined);\n"
        ),
        "dist/domain/analysisView/environmentStateQueryCalls.js": "x.startMs(a); y.endMs(b);\n",
        "dist/domain/analysisView/environmentStateWriteLinks.js": "x.startMs(a); y.endMs(b);\n",
        "dist/domain/analysisView/environmentStateRules.js": "x.startMs(a); y.endMs(b);\n",
        "dist/domain/analysisView/EnvironmentStateWriteSimulation.js": "x.startMs(a); y.endMs(b);\n",
        "dist/domain/analysisView/environmentStateWrites.js": (
            "const LATEST_END_MS = 8_640_000_000_000_000 - 1000;\n"
            "const clockStart = (records) => {\n"
            "    // The clock is global: any unknown completion may extend beyond every known end.\n"
            "    if (records.some((record) => record.timing.endMs === undefined))\n"
            "        return null;\n"
            "    // A record timed past the range of a date carries no usable time, so the clock starts from the others.\n"
            "    let latest;\n"
            "    for (const record of records) {\n"
            "        if (record.timing.endMs === undefined ||\n"
            "            Math.abs(record.timing.endMs) > LATEST_END_MS)\n"
            "            continue;\n"
            "        const end = record.timing.end;\n"
            "        if (end === undefined)\n"
            "            return null;\n"
            "        if (latest === undefined || compareOffsetDateTimeInstants(end, latest) > 0)\n"
            "            latest = end;\n"
            "    }\n"
            "    return latest === undefined\n"
            "        ? null\n"
            "        : new Date((latest.epochSecond + (latest.fraction === \"\" ? 0 : 1)) * 1000).toISOString();\n"
            "};\n"
        ),
        "dist/cli-runtime/analysisView-CcU5S_9m.js": bundle_template,
        "dist/mcp/toolOutputSchemaGroups-B6TXFAYq.js": bundle_template,
    }
    digests = {}
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = content.encode("utf-8")
        target.write_bytes(raw)
        digests[rel] = _sha256_bytes(raw)
    return digests


def _snapshot_tree(root, names):
    snap = {name: (root / name).read_bytes() for name in names if (root / name).exists()}
    marker = root / "rea-lab-precision.json"
    if marker.exists():
        snap["rea-lab-precision.json"] = marker.read_bytes()
    return snap


class ApplyReaPrecisionTest(unittest.TestCase):
    def test_real_expected_refuses_mismatched_and_partial_trees_before_writing(self):
        self.assertEqual(len(apply_rea_precision.EXPECTED), 9)
        self.assertIn("dist/domain/analysisView/environmentStateWrites.js", apply_rea_precision.EXPECTED)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_disposable_tree(root)
            before = _snapshot_tree(root, apply_rea_precision.EXPECTED)
            with self.assertRaises(ValueError):
                apply_rea_precision.apply(root)
            self.assertEqual(_snapshot_tree(root, apply_rea_precision.EXPECTED), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

            (root / "dist/domain/analysisView/StateRecordChronology.js").unlink()
            with self.assertRaises(ValueError):
                apply_rea_precision.apply(root)
            self.assertFalse((root / "rea-lab-precision.json").exists())

    def test_two_run_byte_identical_idempotence_and_consumer_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            self.assertEqual(len(mock_expected), 9)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                first = apply_rea_precision.apply(root)
                snap_after_first = _snapshot_tree(root, mock_expected)
                second = apply_rea_precision.apply(root)
                snap_after_second = _snapshot_tree(root, mock_expected)

            self.assertEqual(first, second)
            self.assertEqual(snap_after_first, snap_after_second)
            self.assertEqual(set(first["files"].keys()), set(mock_expected.keys()))

            acc_text = (root / "dist/domain/analysisView/EnvironmentStateAccumulator.js").read_bytes().decode("utf-8")
            self.assertIn("caf\u00e9 \u2014 module", acc_text)
            self.assertIn("records.some((record) => record.timing.end === undefined)", acc_text)
            self.assertNotIn("timing.endMs === undefined", acc_text)

            writes_text = (root / "dist/domain/analysisView/environmentStateWrites.js").read_bytes().decode("utf-8")
            self.assertIn("records.some((record) => record.timing.end === undefined)", writes_text)
            self.assertNotIn("records.some((record) => record.timing.endMs === undefined)", writes_text)
            self.assertIn(
                "    for (const record of records) {\n"
                "        const end = record.timing.end;\n"
                "        if (end === undefined)\n"
                "            return null;\n"
                "        if (record.timing.endMs === undefined ||\n"
                "            Math.abs(record.timing.endMs) > LATEST_END_MS)\n"
                "            continue;\n"
                "        if (latest === undefined || compareOffsetDateTimeInstants(end, latest) > 0)\n"
                "            latest = end;\n"
                "    }",
                writes_text,
            )

            for bundle in ("dist/cli-runtime/analysisView-CcU5S_9m.js", "dist/mcp/toolOutputSchemaGroups-B6TXFAYq.js"):
                text = (root / bundle).read_bytes().decode("utf-8")
                self.assertIn("caf\u00e9 \u2014 bundled", text)
                self.assertEqual(text.count("records.some((record) => record.timing.end === undefined)"), 2)
                self.assertNotIn("records.some((record) => record.timing.endMs === undefined)", text)
                self.assertIn(
                    "\t\t\tfor (const record of records) {\n"
                    "\t\t\t\tconst end = record.timing.end;\n"
                    "\t\t\t\tif (end === undefined) return null;\n"
                    "\t\t\t\tif (record.timing.endMs === undefined || Math.abs(record.timing.endMs) > LATEST_END_MS) continue;",
                    text,
                )

    def test_original_digest_and_partial_tree_refusal_preserve_preflight_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            target = root / "dist/domain/analysisView/environmentStateRules.js"
            target.write_bytes(b"tampered before patch\n")
            before = _snapshot_tree(root, mock_expected)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)
            self.assertEqual(_snapshot_tree(root, mock_expected), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            (root / "dist/domain/analysisView/environmentStateWriteLinks.js").unlink()
            before = _snapshot_tree(root, mock_expected)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)
            self.assertEqual(_snapshot_tree(root, mock_expected), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            (root / "dist/domain/analysisView/environmentStateWrites.js").unlink()
            before = _snapshot_tree(root, mock_expected)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)
            self.assertEqual(_snapshot_tree(root, mock_expected), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

    def test_unbundled_writes_mismatch_refuses_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            writes_path = root / "dist/domain/analysisView/environmentStateWrites.js"
            altered = (
                writes_path.read_text(encoding="utf-8")
                + "const extra = records.some((record) => record.timing.endMs === undefined);\n"
            ).encode("utf-8")
            writes_path.write_bytes(altered)
            mock_expected["dist/domain/analysisView/environmentStateWrites.js"] = _sha256_bytes(altered)
            before = _snapshot_tree(root, mock_expected)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)
            self.assertEqual(_snapshot_tree(root, mock_expected), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

    def test_unbundled_writes_clock_start_loop_mismatch_refuses_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            writes_path = root / "dist/domain/analysisView/environmentStateWrites.js"
            original_text = writes_path.read_text(encoding="utf-8")
            self.assertEqual(original_text.count(apply_rea_precision.OLD_END_CHECK), 1)
            altered_text = original_text.replace(
                "Math.abs(record.timing.endMs) > LATEST_END_MS",
                "Math.abs(record.timing.endMs) >= LATEST_END_MS",
                1,
            )
            self.assertNotEqual(altered_text, original_text)
            self.assertEqual(altered_text.count(apply_rea_precision.OLD_END_CHECK), 1)
            altered = altered_text.encode("utf-8")
            writes_path.write_bytes(altered)
            mock_expected["dist/domain/analysisView/environmentStateWrites.js"] = _sha256_bytes(altered)
            before = _snapshot_tree(root, mock_expected)
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                with self.assertRaises(ValueError) as ctx:
                    apply_rea_precision.apply(root)
            self.assertIn("Unbundled clockStart completion check changed", str(ctx.exception))
            self.assertEqual(_snapshot_tree(root, mock_expected), before)
            self.assertFalse((root / "rea-lab-precision.json").exists())

    def test_malformed_truncated_and_non_utf8_marker_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            marker = root / "rea-lab-precision.json"
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                apply_rea_precision.apply(root)
                for bad_bytes in (
                    b'{"schema": "rea-lab-precision/v1"',
                    b"\xff\xfe{",
                    b"[]\n",
                ):
                    marker.write_bytes(bad_bytes)
                    with self.assertRaises(ValueError):
                        apply_rea_precision.apply(root)

    def test_forged_and_inconsistent_markers_and_reentry_drift_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_expected = _build_disposable_tree(root)
            marker = root / "rea-lab-precision.json"
            with patch.dict(apply_rea_precision.EXPECTED, mock_expected, clear=True):
                valid = apply_rea_precision.apply(root)

                def check_bad_marker(mutator):
                    candidate = json.loads(json.dumps(valid))
                    mutator(candidate)
                    marker.write_bytes((json.dumps(candidate) + "\n").encode("utf-8"))
                    with self.assertRaises(ValueError):
                        apply_rea_precision.apply(root)

                check_bad_marker(lambda c: c.__setitem__("schema", "rea-lab-precision/v2"))
                check_bad_marker(lambda c: c.__setitem__("upstream_version", "6.8.0"))
                check_bad_marker(lambda c: c.__setitem__("extra", True))
                check_bad_marker(lambda c: c["files"].pop("dist/domain/analysisView/environmentStateRules.js"))
                check_bad_marker(lambda c: c["files"].pop("dist/domain/analysisView/environmentStateWrites.js"))
                check_bad_marker(lambda c: c["files"].__setitem__("dist/extra.js", {"before_sha256": "0" * 64, "after_sha256": "1" * 64}))
                check_bad_marker(lambda c: c["files"]["dist/domain/analysisView/environmentStateRules.js"].__setitem__("before_sha256", "0" * 64))
                check_bad_marker(
                    lambda c: c["files"]["dist/domain/analysisView/environmentStateRules.js"].__setitem__(
                        "after_sha256",
                        c["files"]["dist/domain/analysisView/environmentStateRules.js"]["before_sha256"],
                    )
                )
                check_bad_marker(lambda c: c["files"]["dist/domain/analysisView/environmentStateRules.js"].__setitem__("after_sha256", "not-a-digest"))

                marker.write_bytes((json.dumps(valid) + "\n").encode("utf-8"))
                (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.8.0"}) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)
                (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.9.0"}) + "\n", encoding="utf-8")

                (root / "dist/domain/analysisView/environmentStateRules.js").write_bytes(b"drifted\n")
                with self.assertRaises(ValueError):
                    apply_rea_precision.apply(root)


if __name__ == "__main__":
    unittest.main()
