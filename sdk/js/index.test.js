/**
 * Tests for MengramClient retry logic with exponential backoff.
 * Run with: node --test sdk/js/index.test.js  (Node 18+)
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

// Load the SDK — it exports via module.exports at the bottom
const { MengramClient, MengramError } = require('./index.js');

// Minimal fetch mock factory
function makeFetch(responses) {
  let call = 0;
  return async function mockFetch(_url, _opts) {
    const { status, body, headers: extraHeaders = {} } = responses[Math.min(call++, responses.length - 1)];
    const headersMap = new Map(Object.entries(extraHeaders));
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (k) => headersMap.get(k) ?? null },
      json: async () => body,
    };
  };
}

test('constructor stores retries option', () => {
  const m = new MengramClient('om-test', { retries: 5 });
  assert.equal(m.retries, 5);
});

test('constructor defaults retries to 3', () => {
  const m = new MengramClient('om-test');
  assert.equal(m.retries, 3);
});

test('retries on 429 and succeeds on 3rd attempt', async () => {
  const responses = [
    { status: 429, body: { detail: 'rate limited' } },
    { status: 429, body: { detail: 'rate limited' } },
    { status: 200, body: { results: [] } },
  ];

  const m = new MengramClient('om-test');
  // Override fetch globally for this test
  const origFetch = globalThis.fetch;
  globalThis.fetch = makeFetch(responses);

  try {
    const result = await m._request('GET', '/v1/test');
    assert.deepEqual(result, { results: [] });
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('retries on 500 and succeeds', async () => {
  const responses = [
    { status: 500, body: { detail: 'internal error' } },
    { status: 200, body: { status: 'ok' } },
  ];

  const m = new MengramClient('om-test');
  const origFetch = globalThis.fetch;
  globalThis.fetch = makeFetch(responses);

  try {
    const result = await m._request('POST', '/v1/add', { messages: [] });
    assert.deepEqual(result, { status: 'ok' });
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('throws after exhausting all retries', async () => {
  const responses = [
    { status: 503, body: { detail: 'unavailable' } },
    { status: 503, body: { detail: 'unavailable' } },
    { status: 503, body: { detail: 'unavailable' } },
  ];

  const m = new MengramClient('om-test', { retries: 3 });
  const origFetch = globalThis.fetch;
  globalThis.fetch = makeFetch(responses);

  try {
    await assert.rejects(
      () => m._request('GET', '/v1/test'),
      (err) => {
        assert.ok(err instanceof MengramError);
        assert.equal(err.statusCode, 503);
        return true;
      }
    );
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('respects Retry-After header', async () => {
  const delays = [];
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => {
    if (typeof fn === 'function' && ms > 0) delays.push(ms);
    return origSetTimeout(fn, 0); // execute immediately in tests
  };

  const responses = [
    { status: 429, body: { detail: 'slow down' }, headers: { 'Retry-After': '2' } },
    { status: 200, body: { ok: true } },
  ];

  const m = new MengramClient('om-test');
  const origFetch = globalThis.fetch;
  globalThis.fetch = makeFetch(responses);

  try {
    await m._request('GET', '/v1/test');
    // Should have used 2000ms from Retry-After header (2 seconds)
    assert.ok(delays.some(d => d === 2000), `Expected delay of 2000ms, got: ${JSON.stringify(delays)}`);
  } finally {
    globalThis.fetch = origFetch;
    globalThis.setTimeout = origSetTimeout;
  }
});

test('uses exponential backoff without Retry-After', async () => {
  const delays = [];
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => {
    if (typeof fn === 'function' && ms > 0) delays.push(ms);
    return origSetTimeout(fn, 0);
  };

  const responses = [
    { status: 503, body: { detail: 'unavailable' } },
    { status: 503, body: { detail: 'unavailable' } },
    { status: 200, body: { ok: true } },
  ];

  const m = new MengramClient('om-test', { retries: 3 });
  const origFetch = globalThis.fetch;
  globalThis.fetch = makeFetch(responses);

  try {
    await m._request('GET', '/v1/test');
    // attempt 0 → 1s (2^0*1000), attempt 1 → 2s (2^1*1000)
    assert.ok(delays.includes(1000), `Expected 1000ms delay, got: ${JSON.stringify(delays)}`);
    assert.ok(delays.includes(2000), `Expected 2000ms delay, got: ${JSON.stringify(delays)}`);
  } finally {
    globalThis.fetch = origFetch;
    globalThis.setTimeout = origSetTimeout;
  }
});

test('does not retry on 404', async () => {
  let callCount = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    callCount++;
    return {
      ok: false,
      status: 404,
      headers: { get: () => null },
      json: async () => ({ detail: 'not found' }),
    };
  };

  const m = new MengramClient('om-test');

  try {
    await assert.rejects(
      () => m._request('GET', '/v1/missing'),
      (err) => {
        assert.ok(err instanceof MengramError);
        assert.equal(err.statusCode, 404);
        return true;
      }
    );
    assert.equal(callCount, 1, 'Should not retry on 404');
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('configurable retries: retries=1 means no retries', async () => {
  let callCount = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    callCount++;
    return {
      ok: false,
      status: 500,
      headers: { get: () => null },
      json: async () => ({ detail: 'error' }),
    };
  };

  const m = new MengramClient('om-test', { retries: 1 });

  try {
    await assert.rejects(() => m._request('GET', '/v1/test'));
    assert.equal(callCount, 1);
  } finally {
    globalThis.fetch = origFetch;
  }
});
