import assert from "node:assert/strict";
import { test } from "node:test";
import { StateRecordChronology } from "/opt/rea-lab/node_modules/rea-agents/dist/domain/analysisView/StateRecordChronology.js";
import { CapturedWriteCompletion } from "/opt/rea-lab/node_modules/rea-agents/dist/domain/analysisView/environmentStateWriteCompletion.js";
import { addOffsetDateTimeMilliseconds, readRecordedStart } from "/opt/rea-lab/node_modules/rea-agents/dist/domain/offsetDateTime.js";

function record(ordinal, timestamp, duration) {
    const instant = readRecordedStart(timestamp)?.instant;
    return { ordinal, timing: {
        start: instant === undefined ? undefined : { instant, ms: Date.parse(timestamp) },
        end: instant === undefined || duration === undefined ? undefined : addOffsetDateTimeMilliseconds(instant, duration),
        endMs: duration === undefined ? undefined : Date.parse(timestamp) + duration,
    } };
}

function overlaps(write, read) {
    const chronology = new StateRecordChronology([write, read], "recorded-start-time");
    const completion = new CapturedWriteCompletion(() => true);
    completion.write("/api/folders/3", chronology.endPosition(write), chronology.startPosition(write), write.ordinal);
    return { completed: chronology.completedBefore(write, read), overlaps: completion.runningAt(
        "/api/folders/3", chronology.startPosition(read), chronology.endPosition(read),
    ) };
}

test("a completed write and rapid read within one millisecond are disjoint", () => {
    assert.deepEqual(overlaps(record(0, "2026-11-15T00:00:00.123100Z", 0.05),
        record(1, "2026-11-15T00:00:00.123200Z", 0.02)), { completed: true, overlaps: false });
});

test("real submillisecond overlap remains unconfirmed", () => {
    assert.deepEqual(overlaps(record(0, "2026-11-15T00:00:00.123100Z", 0.2),
        record(1, "2026-11-15T00:00:00.123200Z", 0.02)), { completed: false, overlaps: true });
});

test("unknown completion remains unconfirmed", () => {
    assert.deepEqual(overlaps(record(0, "2026-11-15T00:00:00.123100Z"),
        record(1, "2026-11-15T00:00:00.123200Z", 0.02)), { completed: false, overlaps: true });
});

test("touching boundaries and equivalent offset spellings share a coordinate", () => {
    assert.deepEqual(overlaps(record(0, "2026-11-15T01:00:00.999900+01:00", 0.1),
        record(1, "2026-11-15T00:00:01.000000Z", 0.02)), { completed: true, overlaps: false });
});

test("arbitrary fractional precision survives far below floating epoch resolution", () => {
    assert.deepEqual(overlaps(record(0, "2026-11-15T00:00:00.123456789123456789Z", 0),
        record(1, "2026-11-15T00:00:00.123456789123456790Z", 0)), { completed: true, overlaps: false });
});

test("same instant remains equal despite trailing zeroes", () => {
    const write = record(0, "2026-11-15T00:00:00.5Z", 0);
    const read = record(1, "2026-11-15T00:00:00.500000Z", 0);
    const chronology = new StateRecordChronology([write, read], "recorded-start-time");
    assert.equal(chronology.endPosition(write), chronology.startPosition(read));
});

test("record-order fallback keeps its existing explicit ordinal policy", () => {
    const records = [record(8, "invalid"), record(3, "invalid")];
    const chronology = new StateRecordChronology(records, "record-order");
    assert.equal(chronology.startPosition(records[0]), 0);
    assert.equal(chronology.endPosition(records[1]), 1);
    assert.equal(chronology.completedBefore(records[0], records[1]), true);
});
