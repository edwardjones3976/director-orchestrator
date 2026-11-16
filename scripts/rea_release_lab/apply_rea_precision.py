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
    "dist/cli-runtime/analysisView-CcU5S_9m.js": "551d507df0faaa45efa9409c4fdc7a757568e8e96189283ec0be8cb4edfd2352",
    "dist/mcp/toolOutputSchemaGroups-B6TXFAYq.js": "6f63edcc9e8b9a5f92a7bdfb97fcd928a6cac55215eea84622993c2e1e7b1770",
}

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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(root):
    marker = root / "rea-lab-precision.json"
    if marker.exists():
        receipt = json.loads(marker.read_text())
        for name, item in receipt["files"].items():
            if digest(root / name) != item["after_sha256"]:
                raise ValueError("Previously patched REA file changed: " + name)
        return receipt
    if json.loads((root / "package.json").read_text())["version"] != "6.9.0":
        raise ValueError("This correction is qualified against REA 6.9.0")
    # Validate every input before changing any package byte.
    for name, expected in EXPECTED.items():
        if digest(root / name) != expected:
            raise ValueError("REA input digest differs: " + name)
    contents = {}
    for name in EXPECTED:
        text = (root / name).read_text()
        bundled = name.startswith(("dist/cli-runtime/", "dist/mcp/"))
        if name.endswith("/StateRecordChronology.js"):
            text = 'import { compareOffsetDateTimeInstants } from "../offsetDateTime.js";\n' + CHRONOLOGY
        elif bundled:
            start = text.index("//#region dist/domain/analysisView/StateRecordChronology.js")
            end = text.index("\n//#endregion", start)
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
        elif bundled:
            start = text.index("//#region dist/domain/analysisView/EnvironmentStateAccumulator.js")
            end = text.index("\n//#endregion", start)
            section = text[start:end]
            if section.count("128 * 8 +") != 2:
                raise ValueError("Bundled exact-coordinate reservation sites changed")
            text = text[:start] + section.replace("128 * 8 +", "128 * 14 +") + text[end:]
        contents[name] = text.encode()
    receipt = {"schema": "rea-lab-precision/v1", "upstream_version": "6.9.0", "files": {}}
    for name, data in contents.items():
        (root / name).write_bytes(data)
        receipt["files"][name] = {"before_sha256": EXPECTED[name], "after_sha256": hashlib.sha256(data).hexdigest()}
    marker.write_bytes((json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


if __name__ == "__main__":
    print(json.dumps(apply(Path(sys.argv[1]).resolve()), indent=2))
