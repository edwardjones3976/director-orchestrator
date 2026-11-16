"""Apply the digest-bound REA 6.9.0 interval correction inside a lab image."""

import hashlib
import json
import sys
from pathlib import Path


EXPECTED = {
    "dist/domain/analysisView/StateRecordChronology.js": "1b794d292b059c9a1dda9ca60678d28d9f459371bc545991c2dd965bfd479e6f",
    "dist/domain/analysisView/EnvironmentStateAccumulator.js": "c51819ddc3339e37ee768b87c43058474f1d1a82504a7293dd78b03e6091e70c",
    "dist/domain/analysisView/environmentStateQueryCalls.js": "8042b79599d0fd3ce368ff39922c773adc0533de033b8880d0b1a3c0e98097eb",
    "dist/domain/analysisView/environmentStateWriteLinks.js": "47ba000354e17f7605a508d1b5a5ae33d38d941659fe0fd8d2929832d54a12c7",
    "dist/domain/analysisView/environmentStateRules.js": "6366b6bf3c5fabae3f8c6bbbfeb3ca75fed922c9a1cfa28e1037e82459183cc3",
    "dist/domain/analysisView/EnvironmentStateWriteSimulation.js": "f812817363a259a75d97aead5222a45d8a427968758caf9225c2b3b8463093f3",
    "dist/domain/analysisView/environmentStateWrites.js": "146e958c702532a61231756e9785b4bfe054c39dbf891b19f8f4de6af20a917f",
    "dist/cli-runtime/analysisView-CcU5S_9m.js": "551d507df0faaa45efa9409c4fdc7a757568e8e96189283ec0be8cb4edfd2352",
    "dist/mcp/toolOutputSchemaGroups-B6TXFAYq.js": "6f63edcc9e8b9a5f92a7bdfb97fcd928a6cac55215eea84622993c2e1e7b1770",
}

MARKER_SCHEMA = "rea-lab-precision/v1"
UPSTREAM_VERSION = "6.9.0"

CHRONOLOGY = '''/** Exact interval coordinates preserve every source fraction and numeric interval indexes. */
export class StateRecordChronology {
    timeline;
    positions;
    #coordinates = new WeakMap();
    constructor(records, timeline) {
        this.timeline = timeline;
        this.positions = new Map(records.map((record, index) => [record.ordinal, index]));
        if (timeline === "record-order")
            return;
        const endpoints = records.flatMap((record) => [record.timing.start?.instant, record.timing.end])
            .filter((instant) => instant !== undefined)
            .sort(compareOffsetDateTimeInstants);
        let coordinate = 0;
        let previous;
        for (const instant of endpoints) {
            if (previous !== undefined && compareOffsetDateTimeInstants(previous, instant) !== 0)
                coordinate++;
            this.#coordinates.set(instant, coordinate);
            previous = instant;
        }
    }
    startPosition(record) {
        return this.timeline === "record-order"
            ? this.positions.get(record.ordinal)
            : this.#coordinates.get(record.timing.start?.instant);
    }
    endPosition(record) {
        return this.timeline === "record-order"
            ? this.positions.get(record.ordinal)
            : this.#coordinates.get(record.timing.end);
    }
    completedBefore(left, right) {
        const end = this.endPosition(left);
        const start = this.startPosition(right);
        return end !== undefined && start !== undefined && end <= start;
    }
}
'''

OLD_END_CHECK = "records.some((record) => record.timing.endMs === undefined)"
NEW_END_CHECK = "records.some((record) => record.timing.end === undefined)"

OLD_CLOCK_START = (
    "\t\t\tif (records.some((record) => record.timing.endMs === undefined)) return null;\n"
    "\t\t\tlet latest;\n"
    "\t\t\tfor (const record of records) {\n"
    "\t\t\t\tif (record.timing.endMs === undefined || Math.abs(record.timing.endMs) > LATEST_END_MS) continue;\n"
    "\t\t\t\tconst end = record.timing.end;\n"
    "\t\t\t\tif (end === undefined) return null;\n"
)
NEW_CLOCK_START = (
    "\t\t\tif (records.some((record) => record.timing.end === undefined)) return null;\n"
    "\t\t\tlet latest;\n"
    "\t\t\tfor (const record of records) {\n"
    "\t\t\t\tconst end = record.timing.end;\n"
    "\t\t\t\tif (end === undefined) return null;\n"
    "\t\t\t\tif (record.timing.endMs === undefined || Math.abs(record.timing.endMs) > LATEST_END_MS) continue;\n"
)
OLD_UNBUNDLED_CLOCK_START = (
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
)
NEW_UNBUNDLED_CLOCK_START = (
    "    if (records.some((record) => record.timing.end === undefined))\n"
    "        return null;\n"
    "    // A record timed past the range of a date carries no usable time, so the clock starts from the others.\n"
    "    let latest;\n"
    "    for (const record of records) {\n"
    "        const end = record.timing.end;\n"
    "        if (end === undefined)\n"
    "            return null;\n"
    "        if (record.timing.endMs === undefined ||\n"
    "            Math.abs(record.timing.endMs) > LATEST_END_MS)\n"
    "            continue;\n"
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_utf8(path):
    return path.read_bytes().decode("utf-8")


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _validate_package_version(root):
    package_path = root / "package.json"
    if not package_path.is_file():
        raise ValueError("Missing REA package.json")
    try:
        package = json.loads(_read_utf8(package_path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid REA package.json") from exc
    if not isinstance(package, dict) or package.get("version") != UPSTREAM_VERSION:
        raise ValueError("This correction is qualified against REA 6.9.0")


def _validate_marker(root, marker):
    if not marker.is_file():
        raise ValueError("Invalid REA precision marker")
    try:
        receipt = json.loads(_read_utf8(marker))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed REA precision marker") from exc
    if not isinstance(receipt, dict) or set(receipt.keys()) != {"schema", "upstream_version", "files"}:
        raise ValueError("Invalid REA precision marker structure")
    if receipt["schema"] != MARKER_SCHEMA:
        raise ValueError("Unsupported REA precision marker schema: " + str(receipt["schema"]))
    if receipt["upstream_version"] != UPSTREAM_VERSION:
        raise ValueError("Unexpected REA precision marker version: " + str(receipt["upstream_version"]))
    files = receipt["files"]
    if not isinstance(files, dict) or set(files.keys()) != set(EXPECTED.keys()):
        raise ValueError("REA precision marker file set mismatch")
    for name, expected_before in EXPECTED.items():
        item = files[name]
        if not isinstance(item, dict) or set(item.keys()) != {"before_sha256", "after_sha256"}:
            raise ValueError("Invalid REA precision marker entry: " + name)
        before_sha = item["before_sha256"]
        after_sha = item["after_sha256"]
        if not (_is_sha256(before_sha) and _is_sha256(after_sha)):
            raise ValueError("Invalid REA precision marker digest: " + name)
        if before_sha != expected_before:
            raise ValueError("REA precision marker before_sha256 mismatch: " + name)
        if after_sha == expected_before:
            raise ValueError("REA precision marker after_sha256 matches unpatched digest: " + name)
        target = root / name
        if not target.is_file():
            raise ValueError("Missing patched REA file: " + name)
        if digest(target) != after_sha:
            raise ValueError("Previously patched REA file changed: " + name)
    return receipt


def _region_bounds(text, region_path):
    header = "//#region " + region_path
    start = text.index(header)
    end = text.index("\n//#endregion", start)
    return start, end


def apply(root):
    marker = root / "rea-lab-precision.json"
    _validate_package_version(root)
    if marker.exists():
        return _validate_marker(root, marker)

    # Validate every input before changing any package byte.
    for name, expected in EXPECTED.items():
        target = root / name
        if not target.is_file():
            raise ValueError("Missing REA input file: " + name)
        if digest(target) != expected:
            raise ValueError("REA input digest differs: " + name)

    contents = {}
    for name in EXPECTED:
        text = _read_utf8(root / name)
        bundled = name.startswith(("dist/cli-runtime/", "dist/mcp/"))
        if name.endswith("/StateRecordChronology.js"):
            text = 'import { compareOffsetDateTimeInstants } from "../offsetDateTime.js";\n' + CHRONOLOGY
        elif bundled:
            start, end = _region_bounds(text, "dist/domain/analysisView/StateRecordChronology.js")
            declaration = CHRONOLOGY.replace("export class StateRecordChronology", "StateRecordChronology = class").rstrip() + ";"
            text = text[:start] + (
                "//#region dist/domain/analysisView/StateRecordChronology.js\n"
                "var StateRecordChronology;\n"
                "function init_StateRecordChronology() {\n"
                " return (init_StateRecordChronology = __esmMin((() => {\n"
                "  init_offsetDateTime();\n" + declaration + "\n })))();\n}\n"
            ) + text[end:]
        text = text.replace(".startMs(", ".startPosition(").replace(".endMs(", ".endPosition(")
        if name.endswith("/EnvironmentStateAccumulator.js"):
            if text.count("ENTRY_BYTES * 8 +") != 2:
                raise ValueError("Exact-coordinate memory reservation sites changed")
            text = text.replace("ENTRY_BYTES * 8 +", "ENTRY_BYTES * 14 +")
            if text.count(OLD_END_CHECK) != 1:
                raise ValueError("Exact-completion diagnostic check changed")
            text = text.replace(OLD_END_CHECK, NEW_END_CHECK)
        elif name.endswith("/environmentStateWrites.js"):
            if text.count(OLD_END_CHECK) != 1:
                raise ValueError("Unbundled clockStart missing-completion check changed")
            if text.count(OLD_UNBUNDLED_CLOCK_START) != 1:
                raise ValueError("Unbundled clockStart completion check changed")
            text = text.replace(OLD_UNBUNDLED_CLOCK_START, NEW_UNBUNDLED_CLOCK_START, 1)
        elif bundled:
            start, end = _region_bounds(text, "dist/domain/analysisView/EnvironmentStateAccumulator.js")
            section = text[start:end]
            if section.count("128 * 8 +") != 2:
                raise ValueError("Bundled exact-coordinate reservation sites changed")
            section = section.replace("128 * 8 +", "128 * 14 +")
            if section.count(OLD_END_CHECK) != 1:
                raise ValueError("Bundled exact-completion diagnostic check changed")
            section = section.replace(OLD_END_CHECK, NEW_END_CHECK)
            text = text[:start] + section + text[end:]

            w_start, w_end = _region_bounds(text, "dist/domain/analysisView/environmentStateWrites.js")
            w_section = text[w_start:w_end]
            if w_section.count(OLD_CLOCK_START) != 1:
                raise ValueError("Bundled clockStart completion check changed")
            w_section = w_section.replace(OLD_CLOCK_START, NEW_CLOCK_START)
            text = text[:w_start] + w_section + text[w_end:]

        encoded = text.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() == EXPECTED[name]:
            raise ValueError("Patch produced no change for REA file: " + name)
        contents[name] = encoded

    receipt = {"schema": MARKER_SCHEMA, "upstream_version": UPSTREAM_VERSION, "files": {}}
    for name, data in contents.items():
        (root / name).write_bytes(data)
        receipt["files"][name] = {"before_sha256": EXPECTED[name], "after_sha256": hashlib.sha256(data).hexdigest()}
    marker.write_bytes((json.dumps(receipt, indent=2) + "\n").encode("utf-8"))
    return receipt


if __name__ == "__main__":
    print(json.dumps(apply(Path(sys.argv[1]).resolve()), indent=2))
