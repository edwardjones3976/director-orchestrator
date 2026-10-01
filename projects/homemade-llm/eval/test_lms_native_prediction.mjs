// [[OpenAI Codex | GPT-6 | 2026-10-01 | 21:58 UTC | BEGIN]]
// Offline checks only: real SDK Chat conversion, mocked clients/results, no inference.
import assert from 'node:assert/strict';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import { syncBuiltinESMExports } from 'node:module';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
import { predictNative, validateNativeRequest, loadPinnedSdk, SDK_PIN } from './lms_native_prediction.mjs';

let networkAttempts = 0;
const deny = () => { networkAttempts++; throw new Error('offline network denied'); };
net.connect = deny;
net.createConnection = deny;
net.Socket.prototype.connect = deny;
tls.connect = deny;
http.request = http.get = https.request = https.get = deny;
globalThis.fetch = deny;
syncBuiltinESMExports();
const sdkEntry = process.env.LMS_NATIVE_SDK_ENTRY;
// CI without the separately staged package still runs mocked tests and reports that gap.
const real = sdkEntry ? await loadPinnedSdk(pathToFileURL(sdkEntry).href) : null;
const Chat = real?.sdk.Chat ?? {from(messages) { return {getMessagesArray() {
  return messages.map(message => ({getRole: () => message.role, getText: () => message.content}));
}}; }};
const schema = JSON.parse(readFileSync(new URL('../schemas/director-data-v1-row.schema.json', import.meta.url),
  'utf8')).properties.target;
const request = () => ({protocol: 'lms-native-prediction-v1', baseUrl: 'ws://127.0.0.1:1234',
  sdkEntryUrl: sdkEntry ? pathToFileURL(sdkEntry).href : 'file:///offline/dist/index.mjs',
  alias: 'm2-offline-fixture', messages: [{role: 'system', content: 'system\nΩ /no_think'},
    {role: 'user', content: 'unchanged ordinary public input\r\n終'}],
  schema: structuredClone(schema), temperature: 0, enableThinking: false, maxOutput: 2048, deadlineMs: 1000,
  expectedModel: {modelKey: 'publisher/model-Q4_K_M', path: 'publisher/model.gguf',
    sizeBytes: 123456, ggufSha256: 'a'.repeat(64)}, expectedServer: {version: '0.4.25', build: 1}});
const info = () => ({identifier: 'm2-offline-fixture', instanceReference: 'owned-instance-1',
  modelKey: 'publisher/model-Q4_K_M', path: 'publisher/model.gguf', sizeBytes: 123456,
  ttlMs: null, lastUsedTime: null});
const result = () => ({content: '{}', nonReasoningContent: '{}', reasoningContent: '', roundIndex: 0,
  modelInfo: info(), stats: {stopReason: 'eosFound'},
  predictionConfig: {fields: [{key: 'future.unknown', value: {checked: false, value: 17}},
    {key: 'llm.prediction.temperature', value: 0}]},
  loadConfig: {fields: [{key: 'future.load', value: [false, null, 'opaque']} ]}});
const never = () => new Promise(() => {});
let checks = 0;
const names = [];
async function check(name, body) { await body(); checks++; names.push(name); }

function fixture(overrides = {}) {
  const calls = {loader: 0, client: 0, alias: 0, pinned: 0, aliasInfo: 0, pinnedInfo: 0,
    template: 0, respond: 0, abort: 0, dispose: 0, forbidden: 0};
  let options, history, response = result();
  const alias = {async getModelInfo() {
    calls.aliasInfo++;
    return overrides.aliasInfo ? overrides.aliasInfo(calls.aliasInfo) : info();
  }};
  const pinned = {async getModelInfo() {
    calls.pinnedInfo++;
    return overrides.pinnedInfo ? overrides.pinnedInfo(calls.pinnedInfo) : info();
  }, async applyPromptTemplate(chat) {
    calls.template++;
    assert.deepEqual(chat.getMessagesArray().map(message => ({role: message.getRole(), content: message.getText()})),
      request().messages);
    return overrides.template ? overrides.template() : 'offline template snapshot only';
  }, respond(chat, opts) {
    calls.respond++; options = opts; history = chat;
    assert.equal(opts.temperature, 0);
    assert.equal(opts.maxTokens, 2048);
    assert.equal(opts.enableThinking, false);
    assert.equal(opts.contextOverflowPolicy, 'stopAtLimit');
    assert.deepEqual(opts.structured, {type: 'json', jsonSchema: schema});
    opts.signal.addEventListener('abort', () => { calls.abort++; });
    const pending = overrides.respond ? overrides.respond() : Promise.resolve(response);
    pending.cancel = async () => { calls.abort++; };
    return pending;
  }};
  const forbidden = () => { calls.forbidden++; throw new Error('load/model/unload forbidden'); };
  class MockClient {
    constructor(opts) {
      calls.client++;
      assert.equal(opts.baseUrl, 'ws://127.0.0.1:1234');
      assert.equal(opts.verboseErrorMessages, false);
      for (const key of ['info', 'error', 'warn', 'debug']) assert.equal(typeof opts.logger[key], 'function');
      this.system = {getLMStudioVersion: async () => overrides.server ?? {version: '0.4.25', build: 1}};
      this.llm = {model: forbidden, load: forbidden, unload: forbidden,
        createDynamicHandle(specifier) {
          calls.alias++; assert.deepEqual(specifier, {identifier: 'm2-offline-fixture'}); return alias;
        }, createDynamicHandleFromInstanceReference(reference) {
          calls.pinned++; assert.equal(reference, 'owned-instance-1'); return pinned;
        }};
      if (overrides.noPinnedMethod) delete this.llm.createDynamicHandleFromInstanceReference;
    }
    async [Symbol.asyncDispose]() { calls.dispose++; if (overrides.dispose) return overrides.dispose(); }
  }
  const loader = async () => {
    calls.loader++;
    if (overrides.loader) return overrides.loader();
    return {sdk: {Chat, LMStudioClient: MockClient}, ...SDK_PIN, ...overrides.pin,
      provenance: 'OFFLINE_MOCK_CLIENT_REAL_CHAT_IF_AVAILABLE'};
  };
  return {calls, loader, setResult(value) {response = value;}, get options() {return options;},
    get history() {return history;}};
}
function unknownCleanup(outcome) {
  for (const key of ['backendStopped', 'parentProcessContained', 'ownedAliasUnloaded']) {
    assert.equal(outcome.cleanup[key], 'UNKNOWN');
  }
  assert.equal(outcome.generationAdmission, false);
}

await check('immutable ordinary contract and complete schema', () => {
  const input = request(); const copy = validateNativeRequest(input);
  input.messages[0].content = 'mutated'; input.schema.properties.non_goals.type = 'string';
  assert.equal(copy.messages[0].content, request().messages[0].content);
  assert.deepEqual(copy.schema, schema);
  assert.ok(Object.isFrozen(copy.messages[0]) && Object.isFrozen(copy.schema.properties));
  assert.throws(() => { copy.messages[0].content = 'mutated'; });
});
await check('dry run makes zero SDK calls', async () => {
  const f = fixture(); const outcome = await predictNative(request(), {dryRun: true, sdkLoader: f.loader});
  assert.equal(outcome.status, 'DRY_RUN'); assert.ok(Object.values(f.calls).every(n => n === 0));
  assert.equal(outcome.inferenceCalls, 0); assert.ok(!Object.hasOwn(outcome, 'content')); unknownCleanup(outcome);
});
await check('request refusals precede all SDK calls', async () => {
  const changes = [r => {r.baseUrl = 'ws://example.com:1234';}, r => {r.baseUrl = 'ws://u:p@127.0.0.1:1234';},
    r => {r.sdkEntryUrl = 'file://remote-host/share/dist/index.mjs';}, r => {r.messages.reverse();}, r => {r.messages[0].extra = 'discard forbidden';},
    r => {delete r.schema.properties.constraints;}, r => {delete r.schema.properties.acceptance_checks.minItems;},
    r => {r.schema.required.pop();}, r => {r.temperature = 0.1;}, r => {r.enableThinking = true;},
    r => {r.maxOutput = 2049;}, r => {r.deadlineMs = 300001;}, r => {r.expectedModel.ggufSha256 = 'bad';}];
  for (const change of changes) {
    const r = request(); change(r); const f = fixture();
    await assert.rejects(predictNative(r, {sdkLoader: f.loader}));
    assert.ok(Object.values(f.calls).every(n => n === 0));
  }
});
await check('raw unknown keys and unchecked values preserved; one pinned prediction', async () => {
  const f = fixture(); const raw = result(); f.setResult(raw);
  const outcome = await predictNative(request(), {sdkLoader: f.loader});
  assert.equal(outcome.status, 'NATIVE_COMPLETED'); assert.equal(f.calls.respond, 1);
  assert.equal(f.calls.alias, 1); assert.equal(f.calls.pinned, 1); assert.equal(f.calls.forbidden, 0);
  assert.deepEqual(outcome.predictionConfig.value, raw.predictionConfig);
  assert.deepEqual(outcome.loadConfig.value, raw.loadConfig);
  raw.predictionConfig.fields[0].value.checked = true;
  assert.equal(outcome.predictionConfig.value.fields[0].value.checked, false);
  assert.equal(outcome.templateSnapshot.finalWireBinding, 'UNKNOWN');
  assert.equal(outcome.effectiveSampler, 'UNKNOWN'); unknownCleanup(outcome);
});
await check('version and pinned method refusals', async () => {
  for (const override of [{pin: {version: '2.0.1'}}, {server: {version: '0.4.26', build: 1}},
    {noPinnedMethod: true}]) {
    const f = fixture(override); const out = await predictNative(request(), {sdkLoader: f.loader});
    assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 0); unknownCleanup(out);
  }
});
await check('missing/wrong initial identity and pre-prediction rebound refuse', async () => {
  for (const override of [{aliasInfo: () => undefined}, {aliasInfo: () => ({...info(), modelKey: 'other'})},
    {aliasInfo: () => ({...info(), instanceReference: ''})},
    {aliasInfo: n => ({...info(), instanceReference: n > 1 ? 'new-instance' : 'owned-instance-1'})},
    {pinnedInfo: () => ({...info(), instanceReference: 'other-instance'})}]) {
    const f = fixture(override); const out = await predictNative(request(), {sdkLoader: f.loader});
    assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 0); unknownCleanup(out);
  }
});
await check('returned instance mismatch and post-prediction alias rebound refuse', async () => {
  const f = fixture(); const raw = result(); raw.modelInfo.instanceReference = 'other-instance'; f.setResult(raw);
  const out = await predictNative(request(), {sdkLoader: f.loader});
  assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 1); assert.ok(!Object.hasOwn(out, 'content'));
  const g = fixture({aliasInfo: n => ({...info(), instanceReference: n >= 3 ? 'new-instance' : 'owned-instance-1'})});
  assert.equal((await predictNative(request(), {sdkLoader: g.loader})).status, 'REFUSED');
});
await check('all incomplete stop reasons reject; no replay', async () => {
  for (const reason of ['userStopped', 'modelUnloaded', 'failed', 'toolCalls', 'maxPredictedTokensReached',
    'contextLengthReached', 'unknown', undefined]) {
    const f = fixture(); const raw = result(); raw.stats.stopReason = reason;
    if (reason === undefined) delete raw.stats.stopReason;
    f.setResult(raw); const out = await predictNative(request(), {sdkLoader: f.loader});
    assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 1); assert.ok(!Object.hasOwn(out, 'content'));
  }
});
await check('reasoning and missing raw config explicitly refuse', async () => {
  for (const change of [raw => {raw.reasoningContent = 'reason';}, raw => {delete raw.reasoningContent;},
    raw => {raw.content = '<think>hidden'; raw.nonReasoningContent = raw.content;},
    raw => {raw.nonReasoningContent = 'different';}, raw => {delete raw.predictionConfig;},
    raw => {delete raw.loadConfig;}, raw => {raw.roundIndex = 1;}]) {
    const f = fixture(); const raw = result(); change(raw); f.setResult(raw);
    const out = await predictNative(request(), {sdkLoader: f.loader});
    assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 1); unknownCleanup(out);
    if (!raw.predictionConfig) assert.equal(out.predictionConfig.state, 'UNKNOWN');
    if (!raw.loadConfig) assert.equal(out.loadConfig.state, 'UNKNOWN');
  }
});
await check('setup, template, prediction, and disposal share absolute deadline', async () => {
  for (const [override, expectedCalls] of [[{loader: never}, 0], [{template: never}, 0],
    [{respond: never}, 1], [{dispose: never}, 1]]) {
    const f = fixture(override); const r = request(); r.deadlineMs = 60;
    const started = performance.now(); const out = await predictNative(r, {sdkLoader: f.loader});
    assert.equal(out.status, 'TIMEOUT'); assert.ok(performance.now() - started < 1000);
    assert.equal(f.calls.respond, expectedCalls); assert.ok(!Object.hasOwn(out, 'content')); unknownCleanup(out);
    if (override.respond) { assert.equal(f.calls.abort, 1); assert.equal(out.cleanup.cancellationRequested, true); }
  }
});
await check('late setup resolution cannot make client or prediction', async () => {
  let resolve; const f = fixture({loader: () => new Promise(r => {resolve = r;})});
  const r = request(); r.deadlineMs = 40;
  const out = await predictNative(r, {sdkLoader: f.loader}); assert.equal(out.status, 'TIMEOUT');
  resolve({sdk: {Chat, LMStudioClient: class {constructor() {f.calls.client++;}}}, ...SDK_PIN});
  await new Promise(r => setTimeout(r, 10)); assert.equal(f.calls.client, 0); assert.equal(f.calls.respond, 0);
});
await check('prediction errors retain unknown backend stop and no second call', async () => {
  const f = fixture({respond: () => Promise.reject(new Error('private exception must not be retained'))});
  const out = await predictNative(request(), {sdkLoader: f.loader});
  assert.equal(out.status, 'REFUSED'); assert.equal(f.calls.respond, 1); assert.equal(f.calls.abort, 1);
  assert.ok(!JSON.stringify(out).includes('private exception')); unknownCleanup(out);
});
assert.equal(networkAttempts, 0);
console.log(JSON.stringify({status: 'PASS', checks, names, networkAttempts,
  realSdkImportAndChat: real ? 'PASS_PINNED_2.0.0_NETWORK_DENIED' : 'NOT_RUN_PACKAGE_NOT_STAGED',
  clientsAndPredictionResults: 'OFFLINE_MOCKS_ONLY', liveInference: false, containment: 'UNKNOWN'}));
// [[OpenAI Codex | GPT-6 | 2026-10-01 | 21:58 UTC | END]]
