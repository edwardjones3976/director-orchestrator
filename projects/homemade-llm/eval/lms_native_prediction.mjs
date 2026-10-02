// [[OpenAI Codex | GPT-6 | 2026-10-01 | 21:58 UTC | BEGIN]]
// Held library seam: generator integration and live qualification require separate review.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { performance } from 'node:perf_hooks';

export const SDK_PIN = Object.freeze({version: '2.0.0',
  entrySha256: '18daf943812755c3c5f9c2ad61c20887fbd0a07f4ddb7f129f05a57da0ba3a2a',
  packageSha256: '82d692923dbd073fe4b164958dbabe8249f7c4d320ce75e05c3b346491ddf92b'});
const UNKNOWN = 'UNKNOWN';
const hash = value => createHash('sha256').update(value).digest('hex');
const noopLogger = Object.freeze({info() {}, error() {}, warn() {}, debug() {}});

// JSON-only copies refuse lossy serialization; raw KV values are not interpreted or normalized.
function snapshot(value, depth = 0, seen = new Set()) {
  assert.ok(depth <= 64, 'JSON depth');
  if (value === null || ['string', 'boolean'].includes(typeof value)) return value;
  if (typeof value === 'number') { assert.ok(Number.isFinite(value)); return value; }
  assert.equal(typeof value, 'object', 'JSON value');
  assert.ok(!seen.has(value), 'JSON cycle');
  assert.ok(Array.isArray(value) || Object.getPrototypeOf(value) === Object.prototype
    || Object.getPrototypeOf(value) === null, 'JSON object');
  seen.add(value);
  const copy = Array.isArray(value) ? [] : {};
  for (const key of Object.keys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    assert.ok(descriptor && 'value' in descriptor, 'JSON accessor');
    Object.defineProperty(copy, key, {value: snapshot(descriptor.value, depth + 1, seen),
      enumerable: true, writable: true, configurable: true});
  }
  if (Array.isArray(value)) assert.equal(Object.keys(value).length, value.length, 'sparse array');
  seen.delete(value);
  return Object.freeze(copy);
}

export function validateNativeRequest(input) {
  const request = snapshot(input);
  assert.equal(request.protocol, 'lms-native-prediction-v1');
  const endpoint = new URL(request.baseUrl);
  assert.equal(endpoint.protocol, 'ws:');
  assert.equal(endpoint.hostname, '127.0.0.1');
  assert.ok(endpoint.port && !endpoint.username && !endpoint.password);
  assert.equal(endpoint.pathname, '/');
  assert.equal(endpoint.search + endpoint.hash, '');
  assert.match(request.alias, /^m2-[A-Za-z0-9._-]{1,120}$/);
  assert.equal(request.messages.length, 2);
  assert.deepEqual(request.messages.map(message => message.role), ['system', 'user']);
  for (const message of request.messages) {
    assert.deepEqual(Object.keys(message).sort(), ['content', 'role']);
    assert.equal(typeof message.content, 'string');
    assert.ok(message.content.length > 0);
  }
  // Current ordinary schema has no regex rewrites. Bind the entire authoritative schema.
  const schema = JSON.parse(readFileSync(new URL('../schemas/director-data-v1-row.schema.json',
    import.meta.url), 'utf8')).properties.target;
  assert.deepEqual(request.schema, schema);
  assert.equal(request.temperature, 0);
  assert.equal(request.enableThinking, false);
  assert.ok(Number.isInteger(request.maxOutput) && request.maxOutput > 0 && request.maxOutput <= 2048);
  assert.ok(Number.isInteger(request.deadlineMs) && request.deadlineMs > 0 && request.deadlineMs <= 300000);
  for (const key of ['modelKey', 'path']) {
    assert.equal(typeof request.expectedModel[key], 'string');
    assert.ok(request.expectedModel[key].length > 0);
  }
  assert.ok(Number.isSafeInteger(request.expectedModel.sizeBytes) && request.expectedModel.sizeBytes > 0);
  assert.match(request.expectedModel.ggufSha256, /^[0-9a-f]{64}$/);
  assert.deepEqual(request.expectedServer, {version: '0.4.25', build: 1});
  const sdkUrl = new URL(request.sdkEntryUrl);
  assert.equal(sdkUrl.protocol, 'file:');
  assert.equal(sdkUrl.hostname, '', 'SDK entry must be a local file, not a network share');
  return request;
}

export async function loadPinnedSdk(entryUrl) {
  const entry = fileURLToPath(entryUrl);
  assert.equal(entry.split(/[\\/]/).slice(-2).join('/'), 'dist/index.mjs');
  const [bytes, packageBytes] = await Promise.all([
    readFile(entry), readFile(join(dirname(dirname(entry)), 'package.json'))]);
  assert.equal(hash(bytes), SDK_PIN.entrySha256, 'SDK entry drift');
  assert.equal(hash(packageBytes), SDK_PIN.packageSha256, 'SDK package drift');
  const metadata = JSON.parse(packageBytes.toString('utf8'));
  assert.equal(metadata.name, '@lmstudio/sdk');
  assert.equal(metadata.version, SDK_PIN.version);
  return {sdk: await import(pathToFileURL(entry).href), ...SDK_PIN,
    provenance: 'PINNED_PUBLIC_PACKAGE', dependenciesVerified: UNKNOWN};
}

function assertIdentity(info, request, reference) {
  assert.ok(info && typeof info.instanceReference === 'string' && info.instanceReference.length > 0,
    'missing instance reference');
  for (const key of ['modelKey', 'path', 'sizeBytes']) assert.equal(info[key], request.expectedModel[key], key);
  assert.equal(info.identifier, request.alias, 'owned alias');
  if (reference !== undefined) assert.equal(info.instanceReference, reference, 'instance changed');
}

function rawConfig(value) {
  if (value === undefined || value === null) return {state: UNKNOWN, value: null};
  const copy = snapshot(value);
  assert.ok(Array.isArray(copy.fields), 'raw KV fields missing');
  for (const field of copy.fields) {
    assert.equal(typeof field.key, 'string');
    assert.ok(Object.hasOwn(field, 'value'), 'raw KV value missing');
  }
  return {state: 'RETURNED_RAW_UNINTERPRETED', value: copy};
}

// Injection exists for offline assertions. A mock outcome cannot qualify a live route.
export async function predictNative(input, {dryRun = false, sdkLoader = loadPinnedSdk, absoluteDeadline = Infinity, ownedInstanceReference} = {}) {
  const started = performance.now();
  const request = validateNativeRequest(input);
  const evidence = {protocol: request.protocol, status: 'DRY_RUN',
    requestSha256: hash(JSON.stringify(request)), messagesSha256: hash(JSON.stringify(request.messages)),
    schemaSha256: hash(JSON.stringify(request.schema)), messageCount: request.messages.length,
    inferenceCalls: 0, sdkProvenance: UNKNOWN, server: UNKNOWN,
    requestedPredictionConfig: {temperature: request.temperature, maxTokens: request.maxOutput,
      enableThinking: request.enableThinking, contextOverflowPolicy: 'stopAtLimit',
      structured: {type: 'json', jsonSchema: request.schema}},
    predictionConfig: {state: UNKNOWN, value: null}, loadConfig: {state: UNKNOWN, value: null},
    templateSnapshot: {state: UNKNOWN, finalWireBinding: UNKNOWN},
    effectiveSampler: UNKNOWN, generationAdmission: false,
    cleanup: {cancellationRequested: false, clientDisposal: UNKNOWN, backendStopped: UNKNOWN,
      parentProcessContained: UNKNOWN, ownedAliasUnloaded: UNKNOWN}};
  if (dryRun) return snapshot(evidence);
  const end = Math.min(started + request.deadlineMs, absoluteDeadline);
  const controller = new AbortController();
  let client, prediction, stage = 'sdk_setup', timedOut = false, terminal = false;
  let rejectExpiry;
  const expiry = new Promise((_, reject) => { rejectExpiry = reject; });
  // Observe expiry even when a synchronous failure happens before the first race.
  expiry.catch(() => {});
  const expire = () => {
    timedOut = true;
    if (prediction && !terminal) evidence.cleanup.cancellationRequested = true;
    controller.abort(); // SDK emits cancel; this is not a backend acknowledgment.
    rejectExpiry(new Error('absolute_deadline'));
  };
  const timer = setTimeout(expire, Math.max(0, end - performance.now()));
  async function bounded(name, operation) {
    stage = name;
    if (timedOut || performance.now() >= end) { expire(); throw new Error('absolute_deadline'); }
    // Invoke once, observe late rejection, and never progress after a timeout wins.
    const pending = Promise.resolve().then(() => {
      if (timedOut || performance.now() >= end) { expire(); throw new Error('absolute_deadline'); }
      return operation();
    });
    return Promise.race([pending, expiry]);
  }
  try {
    const loaded = await bounded('sdk_setup', () => sdkLoader(request.sdkEntryUrl));
    for (const key of ['version', 'entrySha256', 'packageSha256']) assert.equal(loaded[key], SDK_PIN[key]);
    evidence.sdkProvenance = loaded.provenance ?? UNKNOWN;
    const {sdk} = loaded;
    assert.equal(typeof sdk.Chat?.from, 'function');
    assert.equal(typeof sdk.LMStudioClient, 'function');
    client = await bounded('client_setup', () => new sdk.LMStudioClient({baseUrl: request.baseUrl,
      logger: noopLogger, verboseErrorMessages: false}));
    assert.equal(typeof client[Symbol.asyncDispose], 'function', 'public disposal missing');
    evidence.server = snapshot(await bounded('server_identity', () => client.system.getLMStudioVersion()));
    assert.deepEqual(evidence.server, request.expectedServer, 'server version drift');
    const aliasHandle = await bounded('alias_handle', () => client.llm.createDynamicHandle({identifier: request.alias}));
    const initial = await bounded('alias_identity', () => aliasHandle.getModelInfo());
    assertIdentity(initial, request, ownedInstanceReference);
    evidence.initialModel = snapshot(initial);
    assert.equal(typeof client.llm.createDynamicHandleFromInstanceReference, 'function', 'public pinned handle missing');
    const pinned = await bounded('pinned_handle', () =>
      client.llm.createDynamicHandleFromInstanceReference(initial.instanceReference));
    assertIdentity(await bounded('pinned_identity', () => pinned.getModelInfo()), request, initial.instanceReference);
    const history = sdk.Chat.from(request.messages);
    assert.deepEqual(history.getMessagesArray().map(message => ({role: message.getRole(), content: message.getText()})),
      request.messages, 'SDK chat conversion');
    const template = await bounded('template_snapshot', () => pinned.applyPromptTemplate(history));
    assert.equal(typeof template, 'string');
    evidence.templateSnapshot = {state: 'SNAPSHOT_ONLY', sha256: hash(template),
      bytes: Buffer.byteLength(template), finalWireBinding: UNKNOWN};
    assertIdentity(await bounded('alias_before_prediction', () => aliasHandle.getModelInfo()), request, initial.instanceReference);
    assertIdentity(await bounded('pinned_before_prediction', () => pinned.getModelInfo()), request, initial.instanceReference);
    const result = await bounded('prediction', () => {
      evidence.inferenceCalls++;
      prediction = pinned.respond(history, {temperature: request.temperature, maxTokens: request.maxOutput,
        enableThinking: request.enableThinking, contextOverflowPolicy: 'stopAtLimit', structured: {type: 'json', jsonSchema: request.schema},
        signal: controller.signal});
      assert.equal(typeof prediction.cancel, 'function', 'public cancellation missing');
      return prediction;
    });
    terminal = true;
    // Retain terminal metadata before refusal, including partial/missing raw config evidence.
    evidence.predictionConfig = rawConfig(result.predictionConfig);
    evidence.loadConfig = rawConfig(result.loadConfig);
    evidence.returnedModel = result.modelInfo === undefined ? UNKNOWN : snapshot(result.modelInfo);
    evidence.stats = result.stats === undefined ? UNKNOWN : snapshot(result.stats);
    evidence.roundIndex = result.roundIndex === undefined ? UNKNOWN : result.roundIndex;
    evidence.reasoningContent = result.reasoningContent === undefined ? UNKNOWN : result.reasoningContent;
    evidence.nonReasoningContent = result.nonReasoningContent === undefined ? UNKNOWN : result.nonReasoningContent;
    assertIdentity(result.modelInfo, request, initial.instanceReference);
    assert.equal(result.roundIndex, 0);
    assert.ok(['eosFound', 'stopStringFound'].includes(result.stats?.stopReason), 'incomplete prediction');
    assert.equal(typeof result.content, 'string');
    assert.equal(result.reasoningContent, '', 'reasoning present or unknown');
    assert.equal(result.nonReasoningContent, result.content, 'structural/reasoning content');
    assert.ok(!/<\s*think\b/i.test(result.content), 'reasoning tag');
    assert.equal(evidence.predictionConfig.state, 'RETURNED_RAW_UNINTERPRETED', 'prediction config unknown');
    assert.equal(evidence.loadConfig.state, 'RETURNED_RAW_UNINTERPRETED', 'load config unknown');
    assertIdentity(await bounded('alias_after_prediction', () => aliasHandle.getModelInfo()), request, initial.instanceReference);
    evidence.content = result.content;
    evidence.status = 'NATIVE_COMPLETED';
    evidence.outputValidation = 'PENDING_EXISTING_GENERATOR_SCHEMA_AND_SECRET_GUARDS';
  } catch {
    evidence.status = timedOut ? 'TIMEOUT' : 'REFUSED';
    evidence.failureStage = stage; // Do not retain SDK exception text or credential-bearing logs.
    if (prediction && !terminal && !controller.signal.aborted) {
      evidence.cleanup.cancellationRequested = true;
      controller.abort();
    }
  } finally {
    if (client && typeof client[Symbol.asyncDispose] === 'function') {
      // Close only this client's connections. No model unload/server/process operation occurs here.
      const disposal = Promise.resolve().then(() => client[Symbol.asyncDispose]());
      disposal.catch(() => {});
      if (performance.now() < end && !timedOut) {
        try {
          await Promise.race([disposal, expiry]);
          evidence.cleanup.clientDisposal = 'CLIENT_CONNECTION_DISPOSAL_RETURNED';
        } catch { evidence.cleanup.clientDisposal = UNKNOWN; }
      }
    }
    clearTimeout(timer);
  }
  // Deadline/disposal failure cannot become an accepted completion. Synchronous JS blocking
  // still requires an independently supervised parent process; this is not containment proof.
  if (timedOut || performance.now() >= end) {
    evidence.status = 'TIMEOUT';
    delete evidence.content;
  } else if (evidence.cleanup.clientDisposal === UNKNOWN && evidence.status === 'NATIVE_COMPLETED') {
    evidence.status = 'REFUSED';
    evidence.failureStage = 'client_disposal';
    delete evidence.content;
  }
  evidence.elapsedMs = performance.now() - started;
  return snapshot(evidence);
}
// [[OpenAI Codex | GPT-6 | 2026-10-01 | 21:58 UTC | END]]

// One owned load/prediction/unload. Injection is exclusively an offline library test seam.
// Public unload is alias based: identity checks are observations, not an atomic guarantee.
export async function predictOwnedNative(input, {dryRun = false, sdkLoader = loadPinnedSdk} = {}) {
  const started = performance.now();
  const request = validateNativeRequest(input);
  if (dryRun) return predictNative(request, {dryRun: true, sdkLoader});
  const end = Math.min(started + request.deadlineMs,
    request.generationDeadlineUnixMs === undefined ? Infinity : started + Math.max(0, request.generationDeadlineUnixMs - Date.now()));
  const ac = new AbortController();
  let client, owned, loaded = false, stage = 'owned_setup';
  let outcome = {protocol: request.protocol, status: 'REFUSED', inferenceCalls: 0};
  const lifecycle = {loadCalls: 0, unloadCalls: 0, ownedInstance: UNKNOWN,
    ownedAliasUnloaded: UNKNOWN, backendStopped: UNKNOWN, clientDisposal: UNKNOWN,
    requestedLoadConfig: {contextLength: 8192, gpu: {ratio: 'max'}}};
  async function bounded(name, factory, deadline = end) {
    stage = name;
    const remaining = deadline - performance.now();
    if (remaining <= 0) throw new Error('deadline');
    let timer;
    try {
      return await Promise.race([Promise.resolve().then(factory), new Promise((_, reject) => {
        timer = setTimeout(() => {ac.abort(); reject(new Error('deadline'));}, remaining);
      })]);
    } finally { clearTimeout(timer); }
  }
  try {
    const pinnedSdk = await bounded('owned_setup', () => sdkLoader(request.sdkEntryUrl));
    assert.equal(pinnedSdk.version, SDK_PIN.version);
    assert.equal(pinnedSdk.entrySha256, SDK_PIN.entrySha256);
    assert.equal(pinnedSdk.packageSha256, SDK_PIN.packageSha256);
    client = new pinnedSdk.sdk.LMStudioClient({baseUrl: request.baseUrl, verboseErrorMessages: false,
      logger: {info() {}, warn() {}, error() {}, debug() {}}});
    assert.deepEqual(await bounded('owned_version', () => client.system.getLMStudioVersion()), request.expectedServer);
    assert.deepEqual(await bounded('owned_inventory', () => client.llm.listLoaded()), []);
    assert.deepEqual(await bounded('owned_embedding_inventory', () => client.embedding.listLoaded()), []);
    const catalog = await bounded('owned_catalog', () => client.system.listDownloadedModels('llm'));
    const matches = catalog.filter(info => info.modelKey === request.expectedModel.modelKey &&
      info.path === request.expectedModel.path && info.sizeBytes === request.expectedModel.sizeBytes);
    assert.equal(matches.length, 1, 'catalog artifact identity');
    assert.ok(matches[0].deviceIdentifier == null, 'local catalog only');
    const handle = await bounded('owned_load', () => {
      lifecycle.loadCalls++;
      return client.llm.load(request.expectedModel.modelKey, {identifier: request.alias,
        deviceIdentifier: null, config: lifecycle.requestedLoadConfig, signal: ac.signal, verbose: false});
    });
    loaded = true;
    owned = await bounded('owned_loaded_identity', () => handle.getModelInfo());
    assertIdentity(owned, request);
    assert.ok(owned.deviceIdentifier == null, 'local instance only');
    lifecycle.ownedInstance = snapshot(owned);
    outcome = JSON.parse(JSON.stringify(await predictNative(request, {sdkLoader: async () => pinnedSdk, absoluteDeadline: end, ownedInstanceReference: owned.instanceReference})));
    if (outcome.returnedModel !== undefined && outcome.returnedModel !== UNKNOWN)
      assertIdentity(outcome.returnedModel, request, owned.instanceReference);
  } catch {
    outcome.status = performance.now() >= end ? 'TIMEOUT' : 'REFUSED';
    outcome.failureStage = stage;
    ac.abort();
  } finally {
    // A separate bounded cleanup reserve. Timeout/late load cannot imply ownership.
    const cleanupEnd = performance.now() + 15000;
    try {
      if (client && owned) {
        const alias = client.llm.createDynamicHandle({identifier: request.alias});
        const current = await bounded('owned_cleanup_identity', () => alias.getModelInfo(), cleanupEnd);
        assertIdentity(current, request, owned.instanceReference);
        assert.ok(current.deviceIdentifier == null, 'local cleanup only');
        await bounded('owned_unload', () => {
          lifecycle.unloadCalls++;
          return client.llm.unload(request.alias, {deviceIdentifier: null});
        }, cleanupEnd);
        const remaining = await bounded('owned_absence', () => client.llm.listLoaded(), cleanupEnd);
        const infos = await bounded('owned_absence_identity', () => Promise.all(remaining.map(h => h.getModelInfo())), cleanupEnd);
        assert.ok(!infos.some(info => info.identifier === request.alias || info.instanceReference === owned.instanceReference));
        lifecycle.ownedAliasUnloaded = 'ABSENCE_OBSERVED';
      } else if (client && !loaded && lifecycle.loadCalls === 0) {
        lifecycle.ownedAliasUnloaded = 'NO_LOAD_ATTEMPTED';
      }
    } catch { lifecycle.ownedAliasUnloaded = UNKNOWN; }
    try {
      if (client && typeof client[Symbol.asyncDispose] === 'function') {
        await bounded('owned_disposal', () => client[Symbol.asyncDispose](), cleanupEnd);
        lifecycle.clientDisposal = 'CONNECTIONS_CLOSED';
      }
    } catch { lifecycle.clientDisposal = UNKNOWN; }
  }
  outcome.lifecycle = lifecycle;
  if (lifecycle.ownedAliasUnloaded !== 'ABSENCE_OBSERVED' || lifecycle.clientDisposal !== 'CONNECTIONS_CLOSED') {
    if (outcome.status === 'NATIVE_COMPLETED') outcome.status = 'CLEANUP_UNKNOWN';
    delete outcome.content;
  }
  return snapshot(outcome);
}
