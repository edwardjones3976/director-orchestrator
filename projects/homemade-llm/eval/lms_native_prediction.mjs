// Native SDK engineering only. Generator integration and live execution remain held.
import assert from 'node:assert/strict';

export function validateNativeRequest(request) {
  assert.equal(request?.protocol, 'lms-native-prediction-v1');
  const endpoint = new URL(request.baseUrl);
  assert.equal(endpoint.protocol, 'ws:');
  assert.equal(endpoint.hostname, '127.0.0.1');
  assert.ok(endpoint.port && !endpoint.username && !endpoint.password);
  assert.equal(endpoint.pathname, '/');
  assert.equal(endpoint.search + endpoint.hash, '');
  assert.match(request.alias, /^m2-[A-Za-z0-9._-]{1,120}$/);
  assert.ok(Array.isArray(request.messages) && request.messages.length > 0);
  for (const message of request.messages) {
    assert.deepEqual(Object.keys(message).sort(), ['content', 'role']);
    assert.ok(['system', 'user', 'assistant'].includes(message.role));
    assert.equal(typeof message.content, 'string');
  }
  assert.equal(request.schema?.type, 'object');
  assert.deepEqual([...request.schema.required].sort(),
    ['acceptance_checks', 'constraints', 'evidence_requirements', 'non_goals']);
  assert.equal(request.schema.additionalProperties, false);
  assert.equal(request.temperature, 0);
  assert.equal(request.enableThinking, false);
  assert.ok(Number.isInteger(request.maxOutput) && request.maxOutput > 0 && request.maxOutput <= 2048);
  assert.ok(Number.isInteger(request.deadlineMs) && request.deadlineMs > 0 && request.deadlineMs <= 180000);
  return request;
}

if (process.argv[2] === '--self-test') {
  const request = {protocol: 'lms-native-prediction-v1', baseUrl: 'ws://127.0.0.1:1234',
    alias: 'm2-offline-fixture', messages: [{role: 'user', content: 'text\nΩ'}],
    schema: {type: 'object', required: ['acceptance_checks', 'constraints', 'evidence_requirements', 'non_goals'],
      additionalProperties: false}, temperature: 0, enableThinking: false, maxOutput: 2048, deadlineMs: 100};
  assert.equal(validateNativeRequest(request), request);
  assert.throws(() => validateNativeRequest({...request, baseUrl: 'ws://example.com:1234'}));
  assert.throws(() => validateNativeRequest({...request, schema: {type: 'object', required: ['constraints']}}));
  console.log('PASS: native request boundary; no SDK import, client, model or inference');
}
