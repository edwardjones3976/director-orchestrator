import assert from "node:assert/strict";
import { test } from "node:test";
import { EnvironmentStateAccumulator } from "/opt/rea-lab/node_modules/rea-agents/dist/domain/analysisView/EnvironmentStateAccumulator.js";
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

function harRecord(ordinal, method, path, startedDateTime, time, responseJson, requestJson) {
    const responseText = JSON.stringify(responseJson);
    const requestText = requestJson === undefined ? undefined : JSON.stringify(requestJson);
    const request = {
        method,
        url: "http://127.0.0.1:8080" + path,
        httpVersion: "HTTP/1.1",
        headers: requestText === undefined ? [] : [{ name: "Content-Type", value: "application/json" }],
        queryString: [],
        cookies: [],
        headersSize: -1,
        bodySize: requestText === undefined ? 0 : Buffer.byteLength(requestText, "utf8"),
    };
    if (requestText !== undefined) {
        request.postData = { mimeType: "application/json", text: requestText };
    }
    return {
        ordinal,
        location: { kind: "json-pointer", pointer: `/log/entries/${ordinal}` },
        reported: {
            startedDateTime,
            time,
            request,
            response: {
                status: 200,
                statusText: "OK",
                httpVersion: "HTTP/1.1",
                cookies: [],
                headers: [{ name: "Content-Type", value: "application/json" }],
                content: {
                    size: Buffer.byteLength(responseText, "utf8"),
                    mimeType: "application/json",
                    text: responseText,
                },
                redirectURL: "",
                headersSize: -1,
                bodySize: Buffer.byteLength(responseText, "utf8"),
            },
            cache: {},
            timings: { send: 0, wait: 0, receive: 0 },
        },
        binary_fields: [],
        numeric_literals: [],
        redactions: [],
        limitations: [],
    };
}

function runAccumulator(putDurationMs) {
    const accumulator = new EnvironmentStateAccumulator();
    const records = [
        harRecord(0, "GET", "/api/folders", "2026-11-15T00:00:00.100000000Z", 0.01, { folders: [{ id: "1", name: "Inbox" }] }),
        harRecord(1, "GET", "/api/folders/1", "2026-11-15T00:00:00.200000000Z", 0.01, { id: "1", name: "Inbox" }),
        harRecord(2, "PUT", "/api/folders/1", "2026-11-15T00:00:00.300000000Z", putDurationMs, { id: "1", name: "Inbox revised" }, { name: "Inbox revised" }),
        harRecord(3, "GET", "/api/folders/1", "2026-11-15T00:00:00.400000000Z", 0.01, { id: "1", name: "Inbox revised" }),
        harRecord(4, "GET", "/api/folders", "2026-11-15T00:00:00.500000000Z", 0.01, { folders: [{ id: "1", name: "Inbox revised" }] }),
    ];
    for (const item of records) {
        const added = accumulator.add(item);
        assert.equal(added.ok, true);
    }
    const state = accumulator.finishState();
    assert.equal(state.ok, true);
    return state.value;
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

test("missing start under recorded-start-time and unregistered records withhold completion", () => {
    const missingStart = record(0, "invalid");
    const valid = record(1, "2026-11-15T00:00:00.123200Z", 0.02);
    const unregistered = record(2, "2026-11-15T00:00:00.123300Z", 0.02);
    const chronology = new StateRecordChronology([missingStart, valid], "recorded-start-time");
    assert.equal(chronology.startPosition(missingStart), undefined);
    assert.equal(chronology.endPosition(missingStart), undefined);
    assert.equal(chronology.completedBefore(missingStart, valid), false);
    assert.equal(chronology.completedBefore(valid, missingStart), false);
    assert.equal(chronology.startPosition(unregistered), undefined);
    assert.equal(chronology.endPosition(unregistered), undefined);
    assert.equal(chronology.completedBefore(valid, unregistered), false);
    const completion = new CapturedWriteCompletion(() => true);
    completion.write("/api/folders/3", chronology.endPosition(valid), chronology.startPosition(valid), valid.ordinal);
    assert.equal(completion.runningAt("/api/folders/3", chronology.startPosition(unregistered), chronology.endPosition(unregistered)), true);
});

test("finite huge duration overflowing exact offset range emits diagnostic and withholds write", () => {
    const disjoint = runAccumulator(0.01);
    const disjointUpdate = disjoint.writes.find((plan) => plan.effect === "update");
    assert.ok(disjointUpdate);
    assert.equal(disjointUpdate.served, true);
    assert.equal(disjoint.write_clock, "2026-11-15T00:00:01.000Z");
    assert.equal(
        disjoint.limitations.includes("Some records carry no usable request duration; their completion and overlap remain unknown."),
        false,
    );

    const maxDateSkipped = runAccumulator(1e16);
    assert.equal(maxDateSkipped.timeline, "recorded-start-time");
    assert.equal(maxDateSkipped.write_clock, "2026-11-15T00:00:01.000Z");
    assert.equal(
        maxDateSkipped.limitations.includes("Some records carry no usable request duration; their completion and overlap remain unknown."),
        false,
    );

    const overflow = runAccumulator(1e20);
    assert.equal(overflow.timeline, "recorded-start-time");
    assert.equal(overflow.write_clock, null);
    assert.equal(
        overflow.limitations.includes("Some records carry no usable request duration; their completion and overlap remain unknown."),
        true,
    );
    const overflowUpdate = overflow.writes.find((plan) => plan.effect === "update");
    assert.ok(overflowUpdate);
    assert.equal(overflowUpdate.served, false);
});

test("tight memory limit refuses EnvironmentStateAccumulator admission cleanly", () => {
    const accumulator = new EnvironmentStateAccumulator(128);
    const added = accumulator.add(
        harRecord(0, "GET", "/api/folders", "2026-11-15T00:00:00.100000000Z", 0.01, { folders: [{ id: "1", name: "Inbox" }] }),
    );
    assert.equal(added.ok, false);
});
